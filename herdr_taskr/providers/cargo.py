"""Cargo.toml and .cargo/config.toml task discovery and parser."""

from __future__ import annotations

import shlex
import tomllib
from pathlib import Path

from ..models import TaskItem

CARGO_TOML_NAMES = ("Cargo.toml",)

BUILTIN_CARGO_TASKS = (
    ("check", "Check the current package and report errors", ["cargo", "check"]),
    ("test", "Execute all unit and integration tests", ["cargo", "test"]),
    ("build", "Compile the current package", ["cargo", "build"]),
    ("clippy", "Run Clippy lints on the current package", ["cargo", "clippy"]),
)


def find_cargo_toml_in_dir(directory: Path) -> Path | None:
    candidate = directory / "Cargo.toml"
    return candidate if candidate.is_file() else None


def _load_cargo_aliases(project_dir: Path) -> list[tuple[str, str, str]]:
    """Return (alias_name, description, config_rel_path) from .cargo/config.toml."""
    aliases: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    candidates = [
        project_dir / ".cargo" / "config.toml",
        project_dir / ".cargo" / "config",
    ]
    if project_dir.parent != project_dir:
        candidates.extend(
            [
                project_dir.parent / ".cargo" / "config.toml",
                project_dir.parent / ".cargo" / "config",
            ]
        )

    for cfg_path in candidates:
        if not cfg_path.is_file():
            continue
        try:
            data = tomllib.loads(cfg_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        alias_table = data.get("alias")
        if not isinstance(alias_table, dict):
            continue
        try:
            rel = str(cfg_path.relative_to(project_dir))
        except ValueError:
            rel = cfg_path.name
        for name, target in alias_table.items():
            if not isinstance(name, str) or name in seen:
                continue
            if isinstance(target, list):
                target_str = " ".join(str(x) for x in target)
            elif isinstance(target, str):
                target_str = target
            else:
                continue
            seen.add(name)
            aliases.append((name, f"cargo {target_str}", rel))
    return aliases


def parse_cargo_toml(
    cargo_toml_path: str | Path,
    include_builtin: bool = True,
) -> list[TaskItem]:
    """Discover Cargo tasks from Cargo.toml and .cargo/config.toml."""
    path = Path(cargo_toml_path).expanduser().resolve()
    if not path.is_file():
        return []

    try:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        data = tomllib.loads(raw_text)
    except Exception:
        return []

    if not isinstance(data, dict):
        return []

    root_dir = path.parent
    rel_file = path.name
    tasks: list[TaskItem] = []
    seen_names: set[str] = set()

    def add_task(
        name: str,
        description: str,
        argv: list[str],
        recipe: list[str] | None = None,
        file: str = rel_file,
    ) -> None:
        if name in seen_names:
            return
        seen_names.add(name)
        cmd_str = " ".join(shlex.quote(a) for a in argv)
        tasks.append(
            TaskItem(
                provider="cargo",
                provider_label="cargo",
                name=name,
                description=description,
                deps="",
                recipe=recipe or [cmd_str],
                command=cmd_str,
                argv=argv,
                cwd=root_dir,
                file=file,
                line=1,
            )
        )

    if include_builtin:
        for name, desc, argv in BUILTIN_CARGO_TASKS:
            add_task(name, desc, list(argv))

    pkg = data.get("package")
    bins = data.get("bin")
    has_default_main = (root_dir / "src" / "main.rs").is_file()

    if isinstance(bins, list) and bins:
        if len(bins) == 1 and isinstance(bins[0], dict) and bins[0].get("name"):
            bname = str(bins[0]["name"])
            add_task("run", f"Run binary `{bname}`", ["cargo", "run", "--bin", bname])
        else:
            for entry in bins:
                if isinstance(entry, dict) and entry.get("name"):
                    bname = str(entry["name"])
                    add_task(
                        f"run:{bname}",
                        f"Run binary `{bname}`",
                        ["cargo", "run", "--bin", bname],
                    )
    elif isinstance(pkg, dict) and has_default_main:
        pkg_name = str(pkg.get("name") or "main")
        add_task("run", f"Run binary `{pkg_name}`", ["cargo", "run"])

    examples = data.get("example")
    if isinstance(examples, list):
        for entry in examples:
            if isinstance(entry, dict) and entry.get("name"):
                ex_name = str(entry["name"])
                add_task(
                    f"example:{ex_name}",
                    f"Run example `{ex_name}`",
                    ["cargo", "run", "--example", ex_name],
                )

    benches = data.get("bench")
    if isinstance(benches, list):
        for entry in benches:
            if isinstance(entry, dict) and entry.get("name"):
                bench_name = str(entry["name"])
                add_task(
                    f"bench:{bench_name}",
                    f"Run benchmark `{bench_name}`",
                    ["cargo", "bench", "--bench", bench_name],
                )

    for alias_name, alias_cmd, alias_file in _load_cargo_aliases(root_dir):
        add_task(
            alias_name,
            f"Cargo alias: {alias_cmd}",
            ["cargo", alias_name],
            recipe=[alias_cmd],
            file=alias_file,
        )

    return tasks
