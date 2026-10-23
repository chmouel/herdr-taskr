"""Justfile task discovery and parser."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from ..models import TaskItem, find_first_file

JUSTFILE_NAMES = ("Justfile", "justfile", ".justfile")

RECIPE_HEADER_RE = re.compile(r"^@?([A-Za-z_][A-Za-z0-9_-]*)(?:\s+([^:\n]+?))?\s*:(?![=])\s*(.*)$")
DOC_ATTR_RE = re.compile(r"""^\[doc\(\s*(['"])(.*?)\1\s*\)\]$""")
ATTR_RE = re.compile(r"^\[([A-Za-z0-9_,\s-]+)\]$")
RESERVED_KEYWORDS = {"alias", "export", "import", "mod", "set"}


def find_justfile_in_dir(directory: Path) -> Path | None:
    return find_first_file(directory, JUSTFILE_NAMES)


def just_command_for(justfile: Path, recipe_name: str) -> tuple[str, list[str]]:
    if justfile.name in ("Justfile", "justfile"):
        return f"just {shlex.quote(recipe_name)}", ["just", recipe_name]
    return (
        f"just --justfile {shlex.quote(justfile.name)} {shlex.quote(recipe_name)}",
        ["just", "--justfile", justfile.name, recipe_name],
    )


def parse_justfile(
    justfile_path: str | Path,
    include_private: bool = False,
) -> list[TaskItem]:
    """Parse recipes, comments, attributes, dependencies, and bodies from a Justfile."""
    path = Path(justfile_path).expanduser().resolve()
    if not path.is_file():
        return []

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    root_dir = path.parent
    rel_file = path.name
    tasks: dict[str, TaskItem] = {}

    pending_comments: list[str] = []
    pending_doc_attr: str | None = None
    pending_private = False
    current_recipe: str | None = None

    for idx, line in enumerate(lines, start=1):
        if line.startswith((" ", "\t")):
            if current_recipe and current_recipe in tasks:
                tasks[current_recipe].recipe.append(line.strip())
            continue

        stripped = line.strip()
        if not stripped:
            pending_comments.clear()
            pending_doc_attr = None
            pending_private = False
            current_recipe = None
            continue

        if stripped.startswith("#"):
            comment_text = stripped.lstrip("#").strip()
            if comment_text and not comment_text.startswith("!"):
                pending_comments.append(comment_text)
            continue

        doc_m = DOC_ATTR_RE.match(stripped)
        if doc_m:
            pending_doc_attr = doc_m.group(2).strip()
            continue

        attr_m = ATTR_RE.match(stripped)
        if attr_m:
            attrs = [a.strip() for a in attr_m.group(1).split(",")]
            if "private" in attrs:
                pending_private = True
            if "doc" in attrs:
                pending_doc_attr = ""
            continue

        # Skip variable assignments and top-level directives
        first_word = stripped.split()[0] if stripped.split() else ""
        if first_word in RESERVED_KEYWORDS or re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:=", stripped):
            pending_comments.clear()
            pending_doc_attr = None
            pending_private = False
            current_recipe = None
            continue

        m = RECIPE_HEADER_RE.match(line)
        if not m:
            pending_comments.clear()
            pending_doc_attr = None
            pending_private = False
            current_recipe = None
            continue

        name = m.group(1).strip()
        params = (m.group(2) or "").strip()
        rhs = m.group(3) or ""

        is_private = pending_private or name.startswith("_")
        doc = (
            pending_doc_attr if pending_doc_attr is not None else " ".join(pending_comments).strip()
        )
        pending_comments.clear()
        pending_doc_attr = None
        pending_private = False

        if is_private and not include_private:
            current_recipe = None
            continue

        inline_comment = ""
        if "#" in rhs:
            deps_part, inline_comment = rhs.split("#", 1)
            inline_comment = inline_comment.strip()
        else:
            deps_part = rhs
        deps_part = deps_part.strip()

        if inline_comment and not doc:
            doc = inline_comment
        if params:
            doc = f"({params}) {doc}".strip() if doc else f"({params})"

        cmd_str, argv = just_command_for(path, name)
        tasks[name] = TaskItem(
            provider="just",
            provider_label="just",
            name=name,
            description=doc,
            deps=deps_part,
            recipe=[],
            command=cmd_str,
            argv=argv,
            cwd=root_dir,
            file=rel_file,
            line=idx,
        )
        current_recipe = name

    return list(tasks.values())
