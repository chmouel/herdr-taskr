"""npm / pnpm / yarn / bun package.json script discovery and parser."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from ..models import TaskItem

PACKAGE_JSON_NAMES = ("package.json",)
VALID_NPM_CLIENTS = ("npm", "pnpm", "yarn", "bun")


def find_package_json_in_dir(directory: Path) -> Path | None:
    candidate = directory / "package.json"
    return candidate if candidate.is_file() else None


def detect_package_manager(
    pkg_dir: Path,
    package_data: dict | None = None,
    preferred_client: str = "auto",
) -> str:
    """Detect whether the project uses pnpm, yarn, bun, or npm."""
    pref = preferred_client.strip().lower()
    if pref in VALID_NPM_CLIENTS:
        return pref

    if isinstance(package_data, dict):
        pm_field = str(package_data.get("packageManager") or "").strip().lower()
        for client in VALID_NPM_CLIENTS:
            if pm_field == client or pm_field.startswith(f"{client}@"):
                return client

    search_dirs = [pkg_dir]
    if pkg_dir.parent != pkg_dir:
        search_dirs.append(pkg_dir.parent)

    for d in search_dirs:
        if (d / "pnpm-lock.yaml").is_file():
            return "pnpm"
        if (d / "bun.lockb").is_file() or (d / "bun.lock").is_file():
            return "bun"
        if (d / "yarn.lock").is_file():
            return "yarn"
        if (d / "package-lock.json").is_file() or (d / "npm-shrinkwrap.json").is_file():
            return "npm"

    return "npm"


def _script_line_numbers(raw_text: str) -> dict[str, int]:
    """Best-effort line number lookup for keys inside the `"scripts"` object."""
    lines = raw_text.splitlines()
    in_scripts = False
    brace_depth = 0
    result: dict[str, int] = {}

    for idx, line in enumerate(lines, start=1):
        if not in_scripts:
            if re.search(r'"scripts"\s*:\s*\{', line):
                in_scripts = True
                brace_depth = 1
            continue

        for ch in line:
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth <= 0:
                    return result

        if brace_depth == 1:
            m = re.match(r'^\s*"([^"]+)"\s*:', line)
            if m:
                result[m.group(1)] = idx
    return result


def parse_package_json(
    package_json_path: str | Path,
    npm_client: str = "auto",
) -> list[TaskItem]:
    """Parse scripts from package.json and build TaskItems for npm/pnpm/yarn/bun."""
    path = Path(package_json_path).expanduser().resolve()
    if not path.is_file():
        return []

    try:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw_text)
    except Exception:
        return []

    if not isinstance(data, dict):
        return []

    scripts = data.get("scripts")
    if not isinstance(scripts, dict) or not scripts:
        return []

    root_dir = path.parent
    pm = detect_package_manager(root_dir, data, preferred_client=npm_client)
    line_map = _script_line_numbers(raw_text)
    tasks: list[TaskItem] = []

    for name, script_body in scripts.items():
        if not isinstance(name, str) or not isinstance(script_body, str):
            continue
        name = name.strip()
        if not name:
            continue
        cmd_str = f"{pm} run {shlex.quote(name)}"
        argv = [pm, "run", name]
        tasks.append(
            TaskItem(
                provider="npm",
                provider_label=pm,
                name=name,
                description=script_body.strip(),
                deps="",
                recipe=[script_body.strip()],
                command=cmd_str,
                argv=argv,
                cwd=root_dir,
                file=path.name,
                line=line_map.get(name, 1),
            )
        )

    return tasks
