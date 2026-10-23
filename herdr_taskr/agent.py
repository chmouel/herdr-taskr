"""Agent discovery and bracketed-paste socket transport (modeled after herdr-reviewr)."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from dataclasses import dataclass

from .picker import FZF_CANCELLED, FZF_NO_MATCH, FZF_STYLE, MUTE, RESET, TARGET_TONE

PASTE_START = "\x1b[200~"
PASTE_END = "\x1b[201~"
MAX_REQUEST_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 180 * 1024
SOCKET_TIMEOUT_SECS = 7.0

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class AgentChoice:
    pane_id: str
    name: str
    status: str
    state_label: str
    tab_id: str = ""
    tab_label: str = ""
    focused: bool = False


def _herdr_bin() -> str:
    candidate = os.environ.get("HERDR_BIN_PATH", "")
    if candidate and os.access(candidate, os.X_OK):
        return candidate
    return "herdr"


def _herdr_json(args: list[str]) -> dict:
    proc = subprocess.run(
        [_herdr_bin(), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"herdr {' '.join(args)} failed: {msg}")
    return json.loads(proc.stdout)


def _tab_labels(workspace_id: str) -> dict[str, str]:
    if not workspace_id:
        return {}
    try:
        tabs = (
            _herdr_json(["tab", "list", "--workspace", workspace_id])
            .get("result", {})
            .get("tabs", [])
        )
        return {
            str(t["tab_id"]): str(t["label"])
            for t in tabs
            if isinstance(t, dict) and t.get("tab_id") and t.get("label")
        }
    except Exception:
        return {}


def _agent_row_name(entry: dict) -> str:
    for key in ("name", "display_agent", "agent", "pane_id"):
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "agent"


def _agent_row_state(entry: dict) -> str:
    status = str(entry.get("agent_status") or "").strip()
    labels = entry.get("state_labels")
    if isinstance(labels, dict):
        custom = labels.get(status)
        if isinstance(custom, str) and custom.strip():
            return custom.strip()
    return status or "idle"


def list_workspace_agents(
    workspace_id: str,
    exclude_pane_id: str = "",
    include_tab_labels: bool = False,
) -> list[AgentChoice]:
    """Return active agent panes in workspace_id (excluding exclude_pane_id)."""
    raw_agents = _herdr_json(["agent", "list"]).get("result", {}).get("agents", [])
    filtered: list[dict] = []
    for entry in raw_agents:
        if not isinstance(entry, dict):
            continue
        agent_kind = entry.get("agent")
        if not isinstance(agent_kind, str) or not agent_kind.strip():
            continue
        pane_id = str(entry.get("pane_id") or "")
        if not pane_id or pane_id == exclude_pane_id:
            continue
        if workspace_id and entry.get("workspace_id") != workspace_id:
            continue
        filtered.append(entry)

    tabs = _tab_labels(workspace_id) if (include_tab_labels and len(filtered) > 1) else {}
    return [
        AgentChoice(
            pane_id=str(e["pane_id"]),
            name=_agent_row_name(e),
            status=str(e.get("agent_status") or "").strip().lower(),
            state_label=_agent_row_state(e),
            tab_id=str(e.get("tab_id") or ""),
            tab_label=tabs.get(str(e.get("tab_id") or ""), ""),
            focused=bool(e.get("focused")),
        )
        for e in filtered
    ]


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def bracketed_paste(text: str) -> str:
    """Wrap text in bracketed-paste escape sequences, stripping any inner end markers."""
    clean = text.replace(PASTE_END, "")
    return f"{PASTE_START}{clean}{PASTE_END}"


def format_task_output_for_agent(
    raw_log: str,
    max_bytes: int = MAX_OUTPUT_BYTES,
) -> str:
    """Format a task run log into a clean block suitable for pasting into an agent."""
    cleaned = strip_ansi(raw_log).strip()
    if not cleaned:
        return "task output was empty."

    encoded = cleaned.encode("utf-8")
    if len(encoded) > max_bytes:
        lines = cleaned.splitlines()
        header = lines[0] if lines else ""
        tail_budget = max(1024, max_bytes - len(header.encode("utf-8")) - 128)
        tail_text = encoded[-tail_budget:].decode("utf-8", errors="ignore")
        first_nl = tail_text.find("\n")
        if first_nl != -1:
            tail_text = tail_text[first_nl + 1 :]
        omitted = len(encoded) - len(tail_text.encode("utf-8"))
        cleaned = f"{header}\n\n... ({omitted} bytes truncated) ...\n\n{tail_text}"

    return f"```\n{cleaned}\n```"


format_make_output_for_agent = format_task_output_for_agent


def check_agent_ready(pane_id: str) -> tuple[bool, str, str]:
    """Verify the target agent pane exists and is not blocked at a permission prompt."""
    try:
        raw_agents = _herdr_json(["agent", "list"]).get("result", {}).get("agents", [])
    except Exception as exc:
        return False, "agent", f"herdr agent list failed: {exc}"

    for entry in raw_agents:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("pane_id") or "") == pane_id and entry.get("agent"):
            name = _agent_row_name(entry)
            status = str(entry.get("agent_status") or "").strip().lower()
            if status == "blocked":
                return False, name, f"answer {name}'s prompt first"
            return True, name, ""

    return False, "the agent", "agent pane closed"


def _socket_send_text(socket_path: str, pane_id: str, pasted_text: str) -> tuple[bool, str]:
    payload = json.dumps(
        {
            "id": "herdr-taskr:send",
            "method": "pane.send_text",
            "params": {"pane_id": pane_id, "text": pasted_text},
        }
    )
    if len(payload.encode("utf-8")) > MAX_REQUEST_BYTES:
        return False, "output too large to send"

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(SOCKET_TIMEOUT_SECS)
            sock.connect(socket_path)
            sock.sendall((payload + "\n").encode("utf-8"))
            buf = bytearray()
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if b"\n" in chunk:
                    break
    except OSError:
        return False, "herdr socket did not answer"

    line = buf.decode("utf-8", errors="replace").splitlines()
    if not line:
        return False, "herdr closed connection without answering"
    try:
        reply = json.loads(line[0])
    except json.JSONDecodeError:
        return False, "unreadable reply from herdr"

    if "error" in reply and reply["error"]:
        err = reply["error"]
        code = err.get("code") if isinstance(err, dict) else ""
        if code == "pane_not_found":
            return False, "agent pane closed"
        return False, "herdr refused the send"
    if "result" in reply:
        return True, ""
    return False, "herdr refused the send"


def send_text_to_agent(
    pane_id: str,
    text: str,
    focus: bool = True,
) -> tuple[bool, str]:
    """Paste literal text into an agent pane's input (unsubmitted) and optionally focus it."""
    ok, agent_name, err = check_agent_ready(pane_id)
    if not ok:
        return False, err

    pasted = bracketed_paste(text)
    socket_path = os.environ.get("HERDR_SOCKET_PATH", "").strip()
    if socket_path:
        sent, send_err = _socket_send_text(socket_path, pane_id, pasted)
        if not sent:
            return False, send_err
    else:
        proc = subprocess.run(
            [_herdr_bin(), "pane", "send-text", pane_id, pasted],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return False, "herdr refused the send"

    if focus:
        subprocess.run(
            [_herdr_bin(), "agent", "focus", pane_id],
            capture_output=True,
            text=True,
        )
    return True, agent_name


def pick_agent_fzf(agents: list[AgentChoice]) -> AgentChoice | None:
    """Prompt the user with fzf to choose an agent when multiple exist in the workspace."""
    if not agents:
        return None
    if len(agents) == 1:
        return agents[0]

    width = min(max(len(a.name) for a in agents), 28)
    rows: list[str] = []
    for a in agents:
        padded = a.name.ljust(width)
        meta_parts = [a.state_label]
        if a.tab_label:
            meta_parts.append(f"tab:{a.tab_label}")
        meta_parts.append(a.pane_id)
        meta = "  ".join(meta_parts)
        shown = f"{TARGET_TONE}{padded}{RESET}  {MUTE}{meta}{RESET}"
        rows.append(f"{shown}\t{a.pane_id}")

    args = [
        "fzf",
        *FZF_STYLE,
        "--prompt",
        "Send to agent › ",
        "--delimiter",
        "\t",
        "--with-nth",
        "1",
    ]
    try:
        proc = subprocess.run(
            args,
            input="\n".join(rows),
            text=True,
            stdout=subprocess.PIPE,
        )
    except FileNotFoundError:
        return agents[0]

    if proc.returncode in (FZF_NO_MATCH, FZF_CANCELLED) or proc.returncode != 0:
        return None

    lines = proc.stdout.splitlines()
    if not lines:
        return None
    parts = lines[0].split("\t")
    chosen_pane = parts[1].strip() if len(parts) > 1 else parts[0].strip()
    return next((a for a in agents if a.pane_id == chosen_pane), None)


def select_best_nonblocked_agent(agents: list[AgentChoice]) -> AgentChoice | None:
    """Select the best agent without prompting (for non-interactive/background sends)."""
    ready = [a for a in agents if a.status != "blocked"]
    pool = ready if ready else agents
    if not pool:
        return None
    focused = next((a for a in pool if a.focused), None)
    return focused or pool[0]
