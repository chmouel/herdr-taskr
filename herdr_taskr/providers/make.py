"""Makefile task discovery and parser."""

from __future__ import annotations

import glob
import re
import shlex
from pathlib import Path

from ..models import TaskItem

MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")

SPECIAL_TARGETS = {
    ".PHONY",
    ".SUFFIXES",
    ".DEFAULT",
    ".PRECIOUS",
    ".INTERMEDIATE",
    ".SECONDARY",
    ".SECONDEXPANSION",
    ".DELETE_ON_ERROR",
    ".IGNORE",
    ".LOW_RESOLUTION_TIME",
    ".SILENT",
    ".EXPORT_ALL_VARIABLES",
    ".NOTPARALLEL",
    ".ONESHELL",
    ".POSIX",
    ".NOEXPORT",
    ".MAKE",
}

TARGET_LINE_RE = re.compile(r"^([A-Za-z0-9_./\-\s]+?)\s*:(?![=:])\s*(.*)$")
INCLUDE_RE = re.compile(r"^-?s?include\s+(.+)$")


def find_makefile_in_dir(directory: Path) -> Path | None:
    for name in MAKEFILE_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def make_command_for(makefile: Path, target: str) -> tuple[str, list[str]]:
    if makefile.name == "Makefile":
        return f"make {shlex.quote(target)}", ["make", target]
    return (
        f"make -f {shlex.quote(makefile.name)} {shlex.quote(target)}",
        ["make", "-f", makefile.name, target],
    )


def _join_continuations(raw_text: str) -> list[tuple[int, str]]:
    lines = raw_text.splitlines()
    joined: list[tuple[int, str]] = []
    i = 0
    n = len(lines)
    while i < n:
        start_line = i + 1
        buf = lines[i]
        if not buf.startswith("\t"):
            while buf.endswith("\\") and not buf.endswith("\\\\") and i + 1 < n:
                buf = buf[:-1].rstrip() + " " + lines[i + 1].lstrip()
                i += 1
        joined.append((start_line, buf))
        i += 1
    return joined


def _parse_single_file(
    path: Path,
    root_makefile: Path,
    targets: dict[str, TaskItem],
    seen_files: set[Path],
) -> None:
    resolved = path.resolve()
    if resolved in seen_files or not resolved.is_file():
        return
    seen_files.add(resolved)

    try:
        raw_text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    root_dir = root_makefile.parent
    try:
        rel_file = str(resolved.relative_to(root_dir))
    except ValueError:
        rel_file = resolved.name

    pending_doc: list[str] = []
    current_targets: list[str] = []

    for lineno, line in _join_continuations(raw_text):
        if line.startswith("\t"):
            if current_targets:
                cmd = line[1:]
                for tname in current_targets:
                    if tname in targets:
                        targets[tname].recipe.append(cmd)
            continue

        stripped = line.strip()
        if not stripped:
            pending_doc.clear()
            current_targets = []
            continue

        if stripped.startswith("##"):
            doc_text = stripped.lstrip("#").strip()
            if doc_text:
                pending_doc.append(doc_text)
            continue

        if stripped.startswith("#"):
            continue

        inc_match = INCLUDE_RE.match(stripped)
        if inc_match:
            pending_doc.clear()
            current_targets = []
            patterns = inc_match.group(1).split("#", 1)[0].split()
            for pat in patterns:
                if "$" in pat:
                    continue
                full_pat = str(resolved.parent / pat)
                for matched in sorted(glob.glob(full_pat)):
                    _parse_single_file(Path(matched), root_makefile, targets, seen_files)
            continue

        if re.match(r"^[A-Za-z0-9_./\-]+\s*(?:[:?!+]?=)", stripped):
            pending_doc.clear()
            current_targets = []
            continue

        m = TARGET_LINE_RE.match(line)
        if not m:
            pending_doc.clear()
            current_targets = []
            continue

        lhs, rhs = m.group(1).strip(), m.group(2)
        names = lhs.split()
        if not names or any(ch in lhs for ch in ("$", "%", "(", ")")):
            pending_doc.clear()
            current_targets = []
            continue

        inline_doc = ""
        if "##" in rhs:
            deps_part, inline_doc = rhs.split("##", 1)
            inline_doc = inline_doc.strip()
        else:
            deps_part = rhs.split("#", 1)[0]
        deps_part = deps_part.strip()

        doc = inline_doc or " ".join(pending_doc).strip()
        pending_doc.clear()

        if names[0] in SPECIAL_TARGETS or names[0].startswith("."):
            current_targets = []
            continue

        current_targets = []
        for name in names:
            if name in SPECIAL_TARGETS or name.startswith(".") or "%" in name or "$" in name:
                continue
            current_targets.append(name)
            existing = targets.get(name)
            cmd_str, argv = make_command_for(root_makefile, name)
            if existing is None:
                targets[name] = TaskItem(
                    provider="make",
                    provider_label="make",
                    name=name,
                    description=doc,
                    deps=deps_part,
                    recipe=[],
                    command=cmd_str,
                    argv=argv,
                    cwd=root_dir,
                    file=rel_file,
                    line=lineno,
                )
            else:
                if doc and not existing.description:
                    existing.description = doc
                if deps_part and not existing.deps:
                    existing.deps = deps_part
                elif deps_part and deps_part not in existing.deps.split():
                    existing.deps = f"{existing.deps} {deps_part}".strip()


def parse_makefile(makefile_path: str | Path) -> list[TaskItem]:
    """Parse targets, descriptions, dependencies, and recipes from a Makefile."""
    path = Path(makefile_path).expanduser().resolve()
    targets: dict[str, TaskItem] = {}
    seen_files: set[Path] = set()
    _parse_single_file(path, path, targets, seen_files)
    return list(targets.values())
