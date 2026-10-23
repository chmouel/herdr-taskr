"""Unified multi-runner task discovery for herdr-taskr."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..models import TaskItem
from .cargo import find_cargo_toml_in_dir, parse_cargo_toml
from .just import JUSTFILE_NAMES, find_justfile_in_dir, parse_justfile
from .make import MAKEFILE_NAMES, find_makefile_in_dir, parse_makefile
from .npm import find_package_json_in_dir, parse_package_json
from .taskfile import TASKFILE_NAMES, find_taskfile_in_dir, parse_taskfile

DEFAULT_PROVIDERS: tuple[str, ...] = ("just", "make", "task", "npm", "cargo")

PROVIDER_ALIASES: dict[str, str] = {
    "just": "just",
    "justfile": "just",
    "make": "make",
    "makefile": "make",
    "gnumake": "make",
    "task": "task",
    "taskfile": "task",
    "go-task": "task",
    "npm": "npm",
    "pnpm": "npm",
    "yarn": "npm",
    "bun": "npm",
    "node": "npm",
    "package.json": "npm",
    "cargo": "cargo",
    "rust": "cargo",
}


@dataclass
class DiscoveryResult:
    project_dir: Path
    tasks: list[TaskItem]
    files: dict[str, Path]


def normalize_provider_name(name: str) -> str | None:
    cleaned = name.strip().lower()
    return PROVIDER_ALIASES.get(cleaned)


def normalize_provider_list(providers: Sequence[str] | None) -> list[str]:
    if not providers:
        return list(DEFAULT_PROVIDERS)
    result: list[str] = []
    for raw in providers:
        for part in str(raw).split(","):
            norm = normalize_provider_name(part)
            if norm and norm not in result:
                result.append(norm)
    return result or list(DEFAULT_PROVIDERS)


def git_toplevel(start_dir: Path) -> Path | None:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(start_dir), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        ).strip()
        return Path(out).resolve() if out else None
    except Exception:
        return None


def _walk_up_dirs(start_dir: str | Path) -> list[Path]:
    current = Path(start_dir).expanduser().resolve()
    if current.is_file():
        current = current.parent

    stop_at = git_toplevel(current)
    dirs: list[Path] = []
    visited: set[Path] = set()

    while current not in visited:
        visited.add(current)
        dirs.append(current)
        if stop_at and current == stop_at:
            break
        if current.parent == current:
            break
        current = current.parent
    return dirs


def find_provider_file(provider: str, start_dir: str | Path) -> Path | None:
    norm = normalize_provider_name(provider)
    if not norm:
        return None
    finder = {
        "just": find_justfile_in_dir,
        "make": find_makefile_in_dir,
        "task": find_taskfile_in_dir,
        "npm": find_package_json_in_dir,
        "cargo": find_cargo_toml_in_dir,
    }[norm]
    for d in _walk_up_dirs(start_dir):
        found = finder(d)
        if found:
            return found
    return None


def parse_file_for_provider(
    path: Path,
    provider: str | None = None,
    *,
    npm_client: str = "auto",
    cargo_builtin_tasks: bool = True,
) -> list[TaskItem]:
    resolved = path.expanduser().resolve()
    norm = normalize_provider_name(provider) if provider else None
    if not norm:
        fname = resolved.name
        if fname in JUSTFILE_NAMES or fname.lower().endswith(".just"):
            norm = "just"
        elif fname in MAKEFILE_NAMES or fname.endswith(".mk"):
            norm = "make"
        elif fname in TASKFILE_NAMES:
            norm = "task"
        elif fname == "package.json":
            norm = "npm"
        elif fname == "Cargo.toml":
            norm = "cargo"
        else:
            norm = "make"

    if norm == "just":
        return parse_justfile(resolved)
    if norm == "make":
        return parse_makefile(resolved)
    if norm == "task":
        return parse_taskfile(resolved)
    if norm == "npm":
        return parse_package_json(resolved, npm_client=npm_client)
    if norm == "cargo":
        return parse_cargo_toml(resolved, include_builtin=cargo_builtin_tasks)
    return []


def discover_tasks(
    start_dir: str | Path,
    providers: Sequence[str] | None = None,
    *,
    explicit_file: str | Path | None = None,
    npm_client: str = "auto",
    cargo_builtin_tasks: bool = True,
) -> DiscoveryResult:
    """Discover tasks across all enabled providers starting from start_dir."""
    start_path = Path(start_dir).expanduser().resolve()
    if start_path.is_file():
        start_path = start_path.parent

    if explicit_file:
        ef = Path(explicit_file).expanduser().resolve()
        provider_hint = providers[0] if providers and len(providers) == 1 else None
        tasks = parse_file_for_provider(
            ef,
            provider_hint,
            npm_client=npm_client,
            cargo_builtin_tasks=cargo_builtin_tasks,
        )
        p_key = (
            tasks[0].provider if tasks else (normalize_provider_name(provider_hint or "") or "make")
        )
        return DiscoveryResult(
            project_dir=ef.parent,
            tasks=tasks,
            files={p_key: ef} if ef.is_file() else {},
        )

    enabled = normalize_provider_list(providers)
    discovered_files: dict[str, Path] = {}
    all_tasks: list[TaskItem] = []

    for prov in enabled:
        f = find_provider_file(prov, start_path)
        if not f:
            continue
        discovered_files[prov] = f
        prov_tasks = parse_file_for_provider(
            f,
            prov,
            npm_client=npm_client,
            cargo_builtin_tasks=cargo_builtin_tasks,
        )
        all_tasks.extend(prov_tasks)

    if discovered_files:
        first_file = next(iter(discovered_files.values()))
        project_dir = first_file.parent
    else:
        project_dir = git_toplevel(start_path) or start_path

    return DiscoveryResult(
        project_dir=project_dir,
        tasks=all_tasks,
        files=discovered_files,
    )
