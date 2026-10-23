"""Core task model and ANSI preview formatter for herdr-taskr."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def find_first_file(directory: Path, names: tuple[str, ...]) -> Path | None:
    """Return the first of names present in directory, matching names exactly.

    Uses the directory listing so case-insensitive filesystems (macOS) do not
    report "makefile" when only "Makefile" exists.
    """
    try:
        entries = set(os.listdir(directory))
    except OSError:
        return None
    for name in names:
        if name in entries and (directory / name).is_file():
            return directory / name
    return None


PROVIDER_COLORS = {
    "just": "\033[38;5;2m",  # green
    "make": "\033[38;5;4m",  # blue
    "task": "\033[38;5;6m",  # cyan
    "npm": "\033[38;5;3m",  # yellow
    "pnpm": "\033[38;5;3m",  # yellow
    "yarn": "\033[38;5;6m",  # cyan
    "bun": "\033[38;5;5m",  # magenta
    "cargo": "\033[38;5;1m",  # red/rust
}


@dataclass
class TaskItem:
    provider: str
    name: str
    provider_label: str = ""
    description: str = ""
    deps: str = ""
    recipe: list[str] = field(default_factory=list)
    command: str = ""
    argv: list[str] = field(default_factory=list)
    cwd: Path = field(default_factory=Path.cwd)
    file: str = ""
    line: int = 1

    def __post_init__(self) -> None:
        if not self.provider_label:
            self.provider_label = self.provider
        if isinstance(self.cwd, str):
            self.cwd = Path(self.cwd)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.name}"

    @property
    def badge(self) -> str:
        return self.provider_label or self.provider

    @property
    def label(self) -> str:
        return f"{self.badge}:{self.name}"


def provider_color(badge: str) -> str:
    return PROVIDER_COLORS.get(badge.lower(), "\033[38;5;4m")


def format_task_preview(task: TaskItem) -> str:
    """Format an ANSI-colored preview of a TaskItem."""
    bold = "\033[1;38;5;15m"
    accent = "\033[38;5;4m"
    muted = "\033[38;5;8m"
    text = "\033[38;5;7m"
    pcolor = provider_color(task.badge)
    reset = "\033[0m"

    loc = f"{task.file}:{task.line}" if task.file else str(task.cwd)
    lines = [f"{pcolor}[{task.badge}]{reset} {bold}{task.name}{reset}  {muted}({loc}){reset}"]
    if task.description:
        lines.append(f"{accent}{task.description}{reset}")
    if task.command:
        lines.append(f"\n{muted}command:{reset} {bold}{task.command}{reset}")
    if task.deps:
        lines.append(f"{muted}depends on:{reset} {text}{task.deps}{reset}")

    if task.recipe:
        lines.append(f"\n{muted}recipe:{reset}")
        for cmd in task.recipe:
            lines.append(f"  {text}{cmd}{reset}")

    return "\n".join(lines) + "\n"
