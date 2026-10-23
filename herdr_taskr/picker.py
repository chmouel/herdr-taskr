"""Interactive fzf picker for project tasks (Just, Make, Taskfile, npm, Cargo)."""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .models import TaskItem, provider_color

FZF_NO_MATCH = 1
FZF_CANCELLED = 130

FZF_STYLE = [
    "--layout",
    "reverse",
    "--info",
    "inline-right",
    "--scrollbar",
    "--pointer",
    "▌",
    "--highlight-line",
    "--ansi",
    "--padding",
    "1,2,1,2",
    "--color",
    "16,bg:-1,gutter:8,bg+:0,fg:7,fg+:15,hl:4,hl+:12,prompt:4,pointer:4,input-fg:15,info:8,separator:8,spinner:8,scrollbar:8,header:8,border:8",
]

MUTE = "\033[38;5;8m"
KEY_TONE = "\033[38;5;7m"
ACCENT_TONE = "\033[38;5;4m"
TARGET_TONE = "\033[1;38;5;15m"
RESET = "\033[0m"


@dataclass(frozen=True)
class PickResult:
    outcome: str  # "picked" | "cancelled" | "failed"
    task_key: str = ""
    mode: str = "split-bg"  # "split-bg" | "split-focus" | "tab" | "background" | "agent"
    error: str = ""

    @property
    def target(self) -> str:
        if ":" in self.task_key:
            return self.task_key.split(":", 1)[1]
        return self.task_key


def _matches_last(task: TaskItem, last_key: str | None) -> bool:
    if not last_key:
        return False
    if ":" in last_key:
        return task.key == last_key
    return task.name == last_key


def order_tasks_with_last(
    tasks: list[TaskItem],
    last_key: str | None = None,
) -> list[TaskItem]:
    if not last_key:
        return list(tasks)
    matched = [t for t in tasks if _matches_last(t, last_key)]
    if not matched:
        return list(tasks)
    first = matched[0]
    rest = [t for t in tasks if t is not first]
    return [first, *rest]


def format_candidate_rows(
    tasks: list[TaskItem],
    last_key: str | None = None,
) -> list[str]:
    if not tasks:
        return []
    ordered = order_tasks_with_last(tasks, last_key)
    badge_width = max(len(t.badge) + 2 for t in ordered)
    name_width = min(max(len(t.name) for t in ordered), 30)
    rows: list[str] = []
    marked_last = False
    for t in ordered:
        raw_badge = f"[{t.badge}]"
        badge_pad = " " * max(0, badge_width - len(raw_badge))
        pcolor = provider_color(t.badge)
        colored_badge = f"{pcolor}{raw_badge}{RESET}{badge_pad}"
        padded_name = t.name.ljust(name_width)

        is_last = not marked_last and _matches_last(t, last_key)
        if is_last:
            marked_last = True
        last_tag = f"  {ACCENT_TONE}[last]{RESET}" if is_last else ""

        if t.description:
            shown = (
                f"{colored_badge}  {TARGET_TONE}{padded_name}{RESET}  "
                f"{MUTE}{t.description}{RESET}{last_tag}"
            )
        else:
            shown = f"{colored_badge}  {TARGET_TONE}{t.name}{RESET}{last_tag}"
        rows.append(f"{shown}\t{t.key}")
    return rows


def map_expect_key(key: str, default_mode: str = "split-bg") -> str:
    key = key.strip().lower()
    if key in ("ctrl-f", "alt-enter"):
        return "split-focus"
    if key == "ctrl-t":
        return "tab"
    if key == "ctrl-b":
        return "background"
    if key == "ctrl-a":
        return "agent"
    return default_mode or "split-bg"


def build_header() -> str:
    pairs = [
        ("enter", "split"),
        ("ctrl-f", "split+focus"),
        ("ctrl-t", "new tab"),
        ("ctrl-b", "bg+toast"),
        ("ctrl-a", "to agent"),
        ("esc", "cancel"),
    ]
    return "   ".join(f"{KEY_TONE}{k}{RESET} {MUTE}{label}{RESET}" for k, label in pairs)


def parse_fzf_output(stdout: str, default_mode: str = "split-bg") -> PickResult:
    lines = stdout.splitlines()
    if not lines:
        return PickResult(outcome="cancelled")
    expect_key = lines[0] if len(lines) >= 2 else ""
    picked_line = lines[1] if len(lines) >= 2 else lines[0]
    if not picked_line.strip():
        return PickResult(outcome="cancelled")
    parts = picked_line.split("\t")
    task_key = parts[1].strip() if len(parts) > 1 else parts[0].strip()
    if not task_key:
        return PickResult(outcome="cancelled")
    return PickResult(
        outcome="picked",
        task_key=task_key,
        mode=map_expect_key(expect_key, default_mode=default_mode),
    )


def run_picker(
    tasks: list[TaskItem],
    project_dir: Path,
    self_bin: str,
    last_key: str | None = None,
    default_mode: str = "split-bg",
    explicit_file: Path | None = None,
) -> PickResult:
    rows = format_candidate_rows(tasks, last_key=last_key)
    if not rows:
        return PickResult(outcome="failed", error="No project tasks found")

    preview_parts = [
        shlex.quote(sys.executable),
        shlex.quote(self_bin),
        "preview",
        "--cwd",
        shlex.quote(str(project_dir)),
    ]
    if explicit_file:
        preview_parts.extend(["--file", shlex.quote(str(explicit_file))])
    preview_parts.append("{2}")
    preview_cmd = " ".join(preview_parts)

    project_name = project_dir.name or str(project_dir)
    args = [
        "fzf",
        *FZF_STYLE,
        "--prompt",
        f"taskr ({project_name}) › ",
        "--header",
        build_header(),
        "--header-first",
        "--delimiter",
        "\t",
        "--with-nth",
        "1",
        "--expect",
        "alt-enter,ctrl-f,ctrl-t,ctrl-b,ctrl-a",
        "--preview",
        preview_cmd,
        "--preview-window",
        "right,52%,border-left,wrap",
    ]

    try:
        proc = subprocess.run(
            args,
            input="\n".join(rows),
            text=True,
            stdout=subprocess.PIPE,
        )
    except FileNotFoundError:
        return PickResult(outcome="failed", error="fzf not found on PATH")

    if proc.returncode in (FZF_NO_MATCH, FZF_CANCELLED):
        return PickResult(outcome="cancelled")
    if proc.returncode != 0:
        return PickResult(outcome="failed", error=f"fzf exited with status {proc.returncode}")

    return parse_fzf_output(proc.stdout, default_mode=default_mode)
