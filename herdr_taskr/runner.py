"""Herdr CLI helpers and async task dispatchers for herdr-taskr."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .agent import (
    format_task_output_for_agent,
    list_workspace_agents,
    pick_agent_fzf,
    select_best_nonblocked_agent,
    send_text_to_agent,
)
from .models import TaskItem
from .providers import discover_tasks
from .state import (
    PLUGIN_ID,
    get_last_run,
    record_last_run,
    resolve_taskr_config,
)


@dataclass
class HerdrContext:
    pane_id: str = ""
    tab_id: str = ""
    workspace_id: str = ""
    cwd: str = ""


def herdr_bin() -> str:
    candidate = os.environ.get("HERDR_BIN_PATH", "")
    if candidate and os.access(candidate, os.X_OK):
        return candidate
    return "herdr"


def herdr_json(args: list[str]) -> dict:
    proc = subprocess.run(
        [herdr_bin(), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"herdr {' '.join(args)} failed: {msg}")
    return json.loads(proc.stdout)


def notify(title: str, body: str = "", sound: str = "none") -> None:
    cmd = [herdr_bin(), "notification", "show", title, "--sound", sound]
    if body:
        cmd.extend(["--body", body])
    subprocess.run(cmd, capture_output=True, text=True)


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = Path(base) / PLUGIN_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_context(env: dict[str, str] | None = None) -> HerdrContext:
    env = env if env is not None else os.environ
    pane_id = env.get("HERDR_TASKR_PANE") or env.get("HERDR_MAKE_PANE") or ""
    tab_id = env.get("HERDR_TASKR_TAB") or env.get("HERDR_MAKE_TAB") or ""
    workspace_id = env.get("HERDR_TASKR_WS") or env.get("HERDR_MAKE_WS") or ""
    cwd = env.get("HERDR_TASKR_CWD") or env.get("HERDR_MAKE_CWD") or ""

    raw_ctx = env.get("HERDR_PLUGIN_CONTEXT_JSON", "")
    if raw_ctx:
        try:
            ctx = json.loads(raw_ctx)
            pane_id = pane_id or ctx.get("focused_pane_id") or ""
            tab_id = tab_id or ctx.get("tab_id") or ""
            workspace_id = workspace_id or ctx.get("workspace_id") or ""
            cwd = (
                cwd
                or ctx.get("focused_pane_cwd")
                or ctx.get("workspace_cwd")
                or (ctx.get("worktree") or {}).get("checkout_path")
                or (ctx.get("worktree") or {}).get("repo_root")
                or ""
            )
        except json.JSONDecodeError:
            pass

    pane_id = pane_id or env.get("HERDR_ACTIVE_PANE_ID") or env.get("HERDR_PANE_ID") or ""
    tab_id = tab_id or env.get("HERDR_ACTIVE_TAB_ID") or env.get("HERDR_TAB_ID") or ""
    workspace_id = (
        workspace_id or env.get("HERDR_ACTIVE_WORKSPACE_ID") or env.get("HERDR_WORKSPACE_ID") or ""
    )
    cwd = cwd or env.get("HERDR_ACTIVE_PANE_CWD") or ""

    if not pane_id or not cwd or not workspace_id:
        try:
            panes = herdr_json(["pane", "list"]).get("result", {}).get("panes", [])
            focused = next((p for p in panes if p.get("focused")), None)
            if focused:
                pane_id = pane_id or focused.get("pane_id", "")
                tab_id = tab_id or focused.get("tab_id", "")
                workspace_id = workspace_id or focused.get("workspace_id", "")
                cwd = cwd or focused.get("cwd", "")
        except Exception:
            pass

    if not cwd:
        cwd = os.getcwd()

    return HerdrContext(
        pane_id=pane_id,
        tab_id=tab_id,
        workspace_id=workspace_id,
        cwd=cwd,
    )


def choose_split_direction(pane_id: str) -> str:
    """Split right on wide panes and down on narrower/taller panes."""
    if not pane_id:
        return "down"
    try:
        layout = (
            herdr_json(["pane", "layout", "--pane", pane_id]).get("result", {}).get("layout", {})
        )
        for p in layout.get("panes", []):
            if p.get("pane_id") == pane_id:
                rect = p.get("rect", {})
                width = rect.get("width", 0)
                height = rect.get("height", 0)
                if width and height and width >= height * 2.4:
                    return "right"
                break
    except Exception:
        pass
    return "down"


def open_picker_popup(
    ctx: HerdrContext,
    remember_last: bool | None = None,
    providers: Sequence[str] | str | None = None,
    default_mode: str | None = None,
    explicit_file: str | Path | None = None,
) -> int:
    cfg = resolve_taskr_config(
        remember_last=remember_last,
        providers=providers,
        default_mode=default_mode,
    )
    disc = discover_tasks(
        ctx.cwd,
        providers=cfg.providers,
        explicit_file=explicit_file,
        npm_client=cfg.npm_client,
        cargo_builtin_tasks=cfg.cargo_builtin_tasks,
    )
    if not disc.tasks:
        notify(PLUGIN_ID, f"No project tasks found in {ctx.cwd}", sound="none")
        return 0

    plugin_id = os.environ.get("HERDR_PLUGIN_ID") or PLUGIN_ID
    remember_flag = "1" if cfg.remember_last else "0"
    providers_csv = ",".join(cfg.providers)
    args = [
        "plugin",
        "pane",
        "open",
        "--plugin",
        plugin_id,
        "--entrypoint",
        "picker",
        "--placement",
        "popup",
        "--width",
        "104",
        "--height",
        "28",
        "--cwd",
        str(disc.project_dir),
        "--env",
        f"HERDR_TASKR_PANE={ctx.pane_id}",
        "--env",
        f"HERDR_TASKR_TAB={ctx.tab_id}",
        "--env",
        f"HERDR_TASKR_WS={ctx.workspace_id}",
        "--env",
        f"HERDR_TASKR_CWD={disc.project_dir}",
        "--env",
        f"HERDR_TASKR_REMEMBER_LAST={remember_flag}",
        "--env",
        f"HERDR_TASKR_PROVIDERS={providers_csv}",
        "--env",
        f"HERDR_TASKR_DEFAULT_MODE={cfg.default_mode}",
    ]
    if explicit_file:
        args.extend(["--env", f"HERDR_TASKR_FILE={Path(explicit_file).expanduser().resolve()}"])
    herdr_json(args)
    return 0


def _log_path_for(project_dir: Path, task_key: str) -> Path:
    project_name = project_dir.name or str(project_dir)
    safe_key = task_key.replace(":", "-").replace("/", "_")
    return cache_dir() / f"{project_name}-{safe_key}.log"


def dispatch_task(
    task: TaskItem,
    mode: str,
    project_dir: Path,
    ctx: HerdrContext,
    self_bin: str,
) -> int:
    task_cwd = str(task.cwd or project_dir)
    cmd_str = task.command
    label = task.label
    log_path = _log_path_for(project_dir, task.key)

    try:
        record_last_run(
            project_dir,
            task.key,
            mode,
            provider=task.provider,
            target=task.name,
            command=cmd_str,
            log_file=str(log_path),
        )
    except Exception:
        pass

    if mode in ("split-bg", "split-focus"):
        focus_flag = "--focus" if mode == "split-focus" else "--no-focus"
        direction = choose_split_direction(ctx.pane_id)
        split_args = [
            "pane",
            "split",
            "--direction",
            direction,
            "--cwd",
            task_cwd,
            focus_flag,
        ]
        if ctx.pane_id:
            split_args.extend(["--pane", ctx.pane_id])
        res = herdr_json(split_args)
        new_pane_id = res["result"]["pane"]["pane_id"]
        try:
            record_last_run(
                project_dir,
                task.key,
                mode,
                provider=task.provider,
                target=task.name,
                command=cmd_str,
                pane_id=new_pane_id,
                log_file=str(log_path),
            )
        except Exception:
            pass
        subprocess.run(
            [herdr_bin(), "pane", "rename", new_pane_id, label],
            capture_output=True,
            text=True,
        )
        herdr_json(["pane", "run", new_pane_id, cmd_str])
        return 0

    if mode == "tab":
        tab_args = [
            "tab",
            "create",
            "--cwd",
            task_cwd,
            "--label",
            label,
            "--no-focus",
        ]
        if ctx.workspace_id:
            tab_args.extend(["--workspace", ctx.workspace_id])
        res = herdr_json(tab_args)
        root_pane_id = res["result"]["root_pane"]["pane_id"]
        try:
            record_last_run(
                project_dir,
                task.key,
                mode,
                provider=task.provider,
                target=task.name,
                command=cmd_str,
                pane_id=root_pane_id,
                log_file=str(log_path),
            )
        except Exception:
            pass
        subprocess.run(
            [herdr_bin(), "pane", "rename", root_pane_id, label],
            capture_output=True,
            text=True,
        )
        herdr_json(["pane", "run", root_pane_id, cmd_str])
        return 0

    if mode in ("background", "agent"):
        agent_pane_id = ""
        if mode == "agent":
            try:
                agents = list_workspace_agents(
                    ctx.workspace_id,
                    exclude_pane_id=ctx.pane_id,
                    include_tab_labels=True,
                )
            except Exception as exc:
                notify(PLUGIN_ID, f"Failed to list agents: {exc}", sound="request")
                return 1
            if not agents:
                notify(PLUGIN_ID, "no agent in this workspace", sound="request")
                return 0
            chosen = pick_agent_fzf(agents)
            if not chosen:
                return 0
            agent_pane_id = chosen.pane_id

        bg_cmd = [
            sys.executable,
            self_bin,
            "run-bg",
            "--project-dir",
            str(project_dir),
            "--cwd",
            task_cwd,
            "--task-key",
            task.key,
            "--label",
            label,
            "--command",
            cmd_str,
        ]
        if ctx.workspace_id:
            bg_cmd.extend(["--workspace", ctx.workspace_id])
        if mode == "agent":
            bg_cmd.append("--send-to-agent")
            if agent_pane_id:
                bg_cmd.extend(["--agent-pane", agent_pane_id])
        subprocess.Popen(
            bg_cmd,
            cwd=task_cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return 0

    raise ValueError(f"Unknown execution mode: {mode}")


def _set_workspace_metadata(workspace_id: str, token_val: str | None) -> None:
    if not workspace_id:
        return
    args = [
        herdr_bin(),
        "workspace",
        "report-metadata",
        "--source",
        PLUGIN_ID,
    ]
    if token_val is not None:
        args.extend(["--token", f"taskr={token_val}", "--ttl-ms", "600000"])
    else:
        args.extend(["--clear-token", "taskr"])
    args.append(workspace_id)
    subprocess.run(args, capture_output=True, text=True)


def run_background_job(
    *,
    project_dir: Path,
    task_cwd: Path,
    task_key: str,
    label: str,
    command: str,
    workspace_id: str = "",
    send_to_agent: bool = False,
    agent_pane_id: str = "",
) -> int:
    project_name = project_dir.name or str(project_dir)
    cdir = cache_dir()
    log_file = _log_path_for(project_dir, task_key)
    last_link = cdir / "last.log"

    display_cmd = command or label
    _set_workspace_metadata(workspace_id, f"⚙ {label}")
    notify(display_cmd, f"Started in {project_name}", sound="none")

    argv = shlex.split(command) if command else ["make", task_key]
    start = time.monotonic()

    with open(log_file, "w", encoding="utf-8") as fh:
        fh.write(f"$ {command}  (in {task_cwd})\n\n")
        fh.flush()
        proc = subprocess.run(
            argv,
            cwd=str(task_cwd),
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            text=True,
        )
        fh.write(f"\n[exit code: {proc.returncode}]\n")

    try:
        if last_link.is_symlink() or last_link.exists():
            last_link.unlink()
        last_link.symlink_to(log_file)
    except OSError:
        pass

    elapsed = time.monotonic() - start
    _set_workspace_metadata(workspace_id, None)

    if send_to_agent:
        raw_log = log_file.read_text(encoding="utf-8", errors="replace")
        formatted = format_task_output_for_agent(raw_log)
        target_agent_pane = agent_pane_id
        if not target_agent_pane:
            try:
                chosen = select_best_nonblocked_agent(list_workspace_agents(workspace_id))
                if chosen:
                    target_agent_pane = chosen.pane_id
            except Exception:
                target_agent_pane = ""

        if not target_agent_pane:
            notify(
                display_cmd,
                "no agent in this workspace",
                sound="request",
            )
            return proc.returncode

        sent, msg = send_text_to_agent(target_agent_pane, formatted, focus=True)
        if sent:
            notify(
                f"sent {label} to {msg}",
                f"Exit {proc.returncode} ({elapsed:.1f}s)",
                sound="done",
            )
        else:
            notify(
                f"{label} send failed",
                msg,
                sound="request",
            )
        return proc.returncode

    if proc.returncode == 0:
        notify(
            f"{display_cmd} ✓",
            f"Finished in {project_name} ({elapsed:.1f}s)",
            sound="done",
        )
    else:
        tail_line = f"Exit code {proc.returncode} (see herdr-taskr logs)"
        try:
            lines = [
                ln.strip()
                for ln in log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.strip() and not ln.strip().startswith("[exit code:")
            ]
            if lines:
                tail_line = lines[-1][:120]
        except OSError:
            pass
        notify(
            f"{display_cmd} failed (exit {proc.returncode})",
            tail_line,
            sound="request",
        )
    return proc.returncode


def _read_pane_recent_text(pane_id: str) -> str | None:
    if not pane_id:
        return None
    proc = subprocess.run(
        [herdr_bin(), "pane", "read", "--source", "recent", "--format", "text", "--raw", pane_id],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return text if text else None


def resolve_latest_task_output(
    ctx: HerdrContext,
    project_dir: Path | None = None,
) -> str | None:
    scope = project_dir
    if scope is None:
        disc = discover_tasks(ctx.cwd)
        scope = disc.project_dir

    last = get_last_run(scope)
    if last:
        if last.pane_id:
            pane_text = _read_pane_recent_text(last.pane_id)
            if pane_text:
                return pane_text
        if last.log_file:
            lf = Path(last.log_file)
            if lf.is_file():
                return lf.read_text(encoding="utf-8", errors="replace")
        candidate = _log_path_for(Path(scope), last.task_key)
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")

    last_log = cache_dir() / "last.log"
    if last_log.is_file():
        return last_log.read_text(encoding="utf-8", errors="replace")
    return None


resolve_latest_make_output = resolve_latest_task_output


def send_last_output_to_agent(
    ctx: HerdrContext,
    project_dir: Path | None = None,
    interactive: bool = False,
) -> int:
    raw_output = resolve_latest_task_output(ctx, project_dir=project_dir)
    if not raw_output:
        notify(PLUGIN_ID, "No task output found to send", sound="none")
        return 0

    try:
        agents = list_workspace_agents(
            ctx.workspace_id,
            exclude_pane_id=ctx.pane_id,
            include_tab_labels=interactive,
        )
    except Exception as exc:
        notify(PLUGIN_ID, f"Failed to list agents: {exc}", sound="request")
        return 1

    if not agents:
        notify(PLUGIN_ID, "no agent in this workspace", sound="request")
        return 0

    if len(agents) > 1 and not interactive:
        plugin_id = os.environ.get("HERDR_PLUGIN_ID") or PLUGIN_ID
        args = [
            "plugin",
            "pane",
            "open",
            "--plugin",
            plugin_id,
            "--entrypoint",
            "send-agent",
            "--placement",
            "popup",
            "--width",
            "64",
            "--height",
            "16",
            "--cwd",
            ctx.cwd,
            "--env",
            f"HERDR_TASKR_PANE={ctx.pane_id}",
            "--env",
            f"HERDR_TASKR_TAB={ctx.tab_id}",
            "--env",
            f"HERDR_TASKR_WS={ctx.workspace_id}",
            "--env",
            f"HERDR_TASKR_CWD={ctx.cwd}",
        ]
        herdr_json(args)
        return 0

    chosen = pick_agent_fzf(agents) if interactive else agents[0]
    if not chosen:
        return 0

    formatted = format_task_output_for_agent(raw_output)
    sent, msg = send_text_to_agent(chosen.pane_id, formatted, focus=True)
    if sent:
        notify(f"sent task output to {msg}", sound="done")
        return 0
    notify(f"{PLUGIN_ID} send failed", msg, sound="request")
    return 1


def find_task_by_key_or_name(tasks: list[TaskItem], key_or_name: str) -> TaskItem | None:
    if ":" in key_or_name:
        matched = next((t for t in tasks if t.key == key_or_name), None)
        if matched:
            return matched
    return next((t for t in tasks if t.name == key_or_name), None)


def rerun_last_task(
    ctx: HerdrContext,
    self_bin: str,
    explicit_file: Path | None = None,
    mode_override: str | None = None,
) -> int:
    cfg = resolve_taskr_config()
    disc = discover_tasks(
        ctx.cwd,
        providers=cfg.providers,
        explicit_file=explicit_file,
        npm_client=cfg.npm_client,
        cargo_builtin_tasks=cfg.cargo_builtin_tasks,
    )
    if not disc.tasks:
        notify(PLUGIN_ID, f"No project tasks found in {ctx.cwd}", sound="none")
        return 0

    last = get_last_run(disc.project_dir)
    if not last:
        notify(
            PLUGIN_ID,
            f"No previous task recorded for {disc.project_dir.name}",
            sound="none",
        )
        return open_picker_popup(ctx, explicit_file=explicit_file)

    matched = find_task_by_key_or_name(disc.tasks, last.task_key)
    if not matched and last.command:
        matched = TaskItem(
            provider=last.provider or "task",
            name=last.target or last.task_key,
            command=last.command,
            argv=shlex.split(last.command),
            cwd=disc.project_dir,
        )
    if not matched:
        return open_picker_popup(ctx, explicit_file=explicit_file)

    return dispatch_task(
        matched,
        mode_override or last.mode,
        disc.project_dir,
        ctx,
        self_bin,
    )


def open_logs_popup() -> int:
    last_log = cache_dir() / "last.log"
    if not last_log.exists():
        notify(PLUGIN_ID, "No background task logs recorded yet", sound="none")
        return 0
    plugin_id = os.environ.get("HERDR_PLUGIN_ID") or PLUGIN_ID
    herdr_json(
        [
            "plugin",
            "pane",
            "open",
            "--plugin",
            plugin_id,
            "--entrypoint",
            "logs",
            "--placement",
            "popup",
            "--width",
            "80%",
            "--height",
            "70%",
        ]
    )
    return 0
