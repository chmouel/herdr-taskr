"""Taskfile (go-task) discovery and indentation-aware YAML parser."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from ..models import TaskItem, find_first_file

TASKFILE_NAMES = (
    "Taskfile.yml",
    "Taskfile.yaml",
    "taskfile.yml",
    "taskfile.yaml",
    "Taskfile.dist.yml",
    "Taskfile.dist.yaml",
)

TASK_KEY_RE = re.compile(r"^([A-Za-z0-9_.:/\-]+)\s*:\s*(.*)$")


def find_taskfile_in_dir(directory: Path) -> Path | None:
    return find_first_file(directory, TASKFILE_NAMES)


def _unquote_yaml_scalar(raw: str) -> str:
    val = raw.strip()
    if not val:
        return ""
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    if " #" in val:
        val = val.split(" #", 1)[0].rstrip()
    return val


def _indent_len(line: str) -> int:
    expanded = line.expandtabs(2)
    return len(expanded) - len(expanded.lstrip(" "))


def parse_taskfile(
    taskfile_path: str | Path,
    include_internal: bool = False,
) -> list[TaskItem]:
    """Parse tasks from a Taskfile.yml/.yaml without external YAML dependencies."""
    path = Path(taskfile_path).expanduser().resolve()
    if not path.is_file():
        return []

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    root_dir = path.parent
    rel_file = path.name

    in_tasks = False
    tasks_indent = 0
    task_key_indent: int | None = None

    current_name: str | None = None
    current_line = 1
    current_desc = ""
    current_deps: list[str] = []
    current_cmds: list[str] = []
    current_internal = False
    current_subkey: str | None = None
    subkey_indent = 0

    results: list[TaskItem] = []

    def flush_current() -> None:
        nonlocal \
            current_name, \
            current_desc, \
            current_deps, \
            current_cmds, \
            current_internal, \
            current_subkey
        if current_name and (not current_internal or include_internal):
            results.append(
                TaskItem(
                    provider="task",
                    provider_label="task",
                    name=current_name,
                    description=current_desc,
                    deps=", ".join(current_deps),
                    recipe=list(current_cmds),
                    command=f"task {shlex.quote(current_name)}",
                    argv=["task", current_name],
                    cwd=root_dir,
                    file=rel_file,
                    line=current_line,
                )
            )
        current_name = None
        current_desc = ""
        current_deps = []
        current_cmds = []
        current_internal = False
        current_subkey = None

    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = _indent_len(raw_line)

        if not in_tasks:
            if indent == 0 and re.match(r"^tasks\s*:\s*(#.*)?$", stripped):
                in_tasks = True
                tasks_indent = 0
                task_key_indent = None
            continue

        if indent <= tasks_indent:
            flush_current()
            in_tasks = False
            if indent == 0 and re.match(r"^tasks\s*:\s*(#.*)?$", stripped):
                in_tasks = True
            continue

        if task_key_indent is None or indent == task_key_indent:
            m = TASK_KEY_RE.match(stripped)
            if m:
                flush_current()
                task_key_indent = indent
                current_name = m.group(1).strip()
                current_line = idx
                rest = m.group(2).strip()
                if rest.startswith("#"):
                    current_desc = rest.lstrip("#").strip()
                elif rest and not rest.startswith(("{", "[")):
                    # Shorthand: `task-name: echo hello`
                    scalar = _unquote_yaml_scalar(rest)
                    if scalar:
                        current_cmds.append(scalar)
            continue

        if current_name is None or task_key_indent is None:
            continue

        # Inside a task definition
        if current_subkey and indent > subkey_indent:
            if stripped.startswith("-"):
                item = stripped[1:].strip()
                if item.startswith("cmd:"):
                    val = _unquote_yaml_scalar(item.split(":", 1)[1])
                    if val and current_subkey == "cmds":
                        current_cmds.append(val)
                elif item.startswith("task:"):
                    val = _unquote_yaml_scalar(item.split(":", 1)[1])
                    if val:
                        if current_subkey == "cmds":
                            current_cmds.append(f"task: {val}")
                        elif current_subkey == "deps":
                            current_deps.append(val)
                else:
                    val = _unquote_yaml_scalar(item)
                    if val:
                        if current_subkey == "cmds":
                            current_cmds.append(val)
                        elif current_subkey == "deps":
                            current_deps.append(val)
                continue
            elif current_subkey in ("desc", "summary") and not current_desc:
                current_desc = _unquote_yaml_scalar(stripped)
                continue

        # Property directly under current task
        prop_m = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", stripped)
        if not prop_m:
            continue

        prop_key = prop_m.group(1).strip()
        prop_val = prop_m.group(2).strip()
        current_subkey = None

        if prop_key in ("desc", "summary"):
            if prop_val and prop_val not in ("|", ">", "|-", ">-"):
                val = _unquote_yaml_scalar(prop_val)
                if prop_key == "desc" or not current_desc:
                    current_desc = val
            else:
                current_subkey = prop_key
                subkey_indent = indent
        elif prop_key == "internal":
            val = _unquote_yaml_scalar(prop_val).lower()
            current_internal = val in ("true", "yes", "1")
        elif prop_key in ("cmd", "cmds"):
            if prop_val.startswith("[") and prop_val.endswith("]"):
                inner = prop_val[1:-1]
                for part in inner.split(","):
                    cleaned = _unquote_yaml_scalar(part)
                    if cleaned:
                        current_cmds.append(cleaned)
            elif prop_val and prop_val not in ("|", ">", "|-", ">-"):
                cleaned = _unquote_yaml_scalar(prop_val)
                if cleaned:
                    current_cmds.append(cleaned)
            else:
                current_subkey = "cmds"
                subkey_indent = indent
        elif prop_key == "deps":
            if prop_val.startswith("[") and prop_val.endswith("]"):
                inner = prop_val[1:-1]
                for part in inner.split(","):
                    cleaned = _unquote_yaml_scalar(part)
                    if cleaned:
                        current_deps.append(cleaned)
            else:
                current_subkey = "deps"
                subkey_indent = indent

    flush_current()
    return results
