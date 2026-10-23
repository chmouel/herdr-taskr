"""Command line entry point for herdr-taskr."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .models import TaskItem, format_task_preview
from .picker import run_picker
from .providers import discover_tasks
from .runner import (
    cache_dir,
    dispatch_task,
    find_task_by_key_or_name,
    open_logs_popup,
    open_picker_popup,
    rerun_last_task,
    resolve_context,
    run_background_job,
    send_last_output_to_agent,
)
from .state import VALID_MODES, get_last_run, resolve_taskr_config


def self_bin_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "bin" / "herdr-taskr")


def _resolve_explicit_file(args: argparse.Namespace) -> Path | None:
    for attr in ("file", "makefile"):
        val = getattr(args, attr, None)
        if val:
            p = Path(val).expanduser().resolve()
            if p.is_file():
                return p
    for env_key in ("HERDR_TASKR_FILE", "HERDR_MAKE_MAKEFILE"):
        env_val = os.environ.get(env_key, "")
        if env_val:
            p = Path(env_val).expanduser().resolve()
            if p.is_file():
                return p
    return None


def cmd_open_picker(args: argparse.Namespace) -> int:
    ctx = resolve_context()
    if getattr(args, "cwd", None):
        ctx.cwd = args.cwd
    return open_picker_popup(
        ctx,
        remember_last=getattr(args, "remember_last", None),
        providers=getattr(args, "provider", None),
        default_mode=getattr(args, "default_mode", None),
        explicit_file=_resolve_explicit_file(args),
    )


def cmd_picker(args: argparse.Namespace) -> int:
    ctx = resolve_context()
    if getattr(args, "cwd", None):
        ctx.cwd = args.cwd
    explicit_file = _resolve_explicit_file(args)
    cfg = resolve_taskr_config(
        remember_last=getattr(args, "remember_last", None),
        providers=getattr(args, "provider", None),
        default_mode=getattr(args, "default_mode", None),
    )
    disc = discover_tasks(
        ctx.cwd,
        providers=cfg.providers,
        explicit_file=explicit_file,
        npm_client=cfg.npm_client,
        cargo_builtin_tasks=cfg.cargo_builtin_tasks,
    )
    if not disc.tasks:
        print(f"herdr-taskr: no project tasks found in {ctx.cwd}", file=sys.stderr)
        return 1

    last_key: str | None = None
    if cfg.remember_last:
        last = get_last_run(disc.project_dir)
        if last:
            last_key = last.task_key

    pick = run_picker(
        disc.tasks,
        disc.project_dir,
        self_bin_path(),
        last_key=last_key,
        default_mode=cfg.default_mode,
        explicit_file=explicit_file,
    )
    if pick.outcome == "cancelled":
        return 0
    if pick.outcome == "failed":
        print(f"herdr-taskr: {pick.error}", file=sys.stderr)
        return 1

    matched = find_task_by_key_or_name(disc.tasks, pick.task_key)
    if not matched:
        print(f"herdr-taskr: unknown task {pick.task_key}", file=sys.stderr)
        return 1

    return dispatch_task(matched, pick.mode, disc.project_dir, ctx, self_bin_path())


def cmd_rerun(args: argparse.Namespace) -> int:
    ctx = resolve_context()
    if getattr(args, "cwd", None):
        ctx.cwd = args.cwd
    explicit_file = _resolve_explicit_file(args)
    return rerun_last_task(
        ctx,
        self_bin_path(),
        explicit_file=explicit_file,
        mode_override=getattr(args, "mode", None),
    )


def cmd_preview(args: argparse.Namespace) -> int:
    start_dir = getattr(args, "cwd", None) or os.getcwd()
    explicit_file = _resolve_explicit_file(args)
    cfg = resolve_taskr_config(providers=getattr(args, "provider", None))
    disc = discover_tasks(
        start_dir,
        providers=cfg.providers,
        explicit_file=explicit_file,
        npm_client=cfg.npm_client,
        cargo_builtin_tasks=cfg.cargo_builtin_tasks,
    )
    found = find_task_by_key_or_name(disc.tasks, args.target)
    if not found:
        provider = "task"
        name = args.target
        if ":" in args.target:
            provider, name = args.target.split(":", 1)
        found = TaskItem(provider=provider, name=name, cwd=disc.project_dir)
    sys.stdout.write(format_task_preview(found))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    start_dir = getattr(args, "cwd", None) or os.getcwd()
    explicit_file = _resolve_explicit_file(args)
    cfg = resolve_taskr_config(providers=getattr(args, "provider", None))
    disc = discover_tasks(
        start_dir,
        providers=cfg.providers,
        explicit_file=explicit_file,
        npm_client=cfg.npm_client,
        cargo_builtin_tasks=cfg.cargo_builtin_tasks,
    )
    if not disc.tasks:
        print("herdr-taskr: no project tasks found", file=sys.stderr)
        return 1
    for t in disc.tasks:
        if t.description:
            print(f"[{t.badge}]\t{t.name}\t{t.description}")
        else:
            print(f"[{t.badge}]\t{t.name}")
    return 0


def cmd_run_bg(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir or args.cwd or os.getcwd()).expanduser().resolve()
    task_cwd = Path(args.cwd or project_dir).expanduser().resolve()
    return run_background_job(
        project_dir=project_dir,
        task_cwd=task_cwd,
        task_key=args.task_key,
        label=args.label or args.task_key,
        command=args.command,
        workspace_id=args.workspace or "",
        send_to_agent=bool(getattr(args, "send_to_agent", False)),
        agent_pane_id=getattr(args, "agent_pane", "") or "",
    )


def cmd_send_to_agent(args: argparse.Namespace) -> int:
    ctx = resolve_context()
    if getattr(args, "cwd", None):
        ctx.cwd = args.cwd
    explicit_file = _resolve_explicit_file(args)
    project_dir = explicit_file.parent if explicit_file else None
    return send_last_output_to_agent(
        ctx,
        project_dir=project_dir,
        interactive=bool(getattr(args, "interactive", False)),
    )


def cmd_open_logs(_args: argparse.Namespace) -> int:
    return open_logs_popup()


def cmd_logs_pane(_args: argparse.Namespace) -> int:
    last_log = cache_dir() / "last.log"
    if not last_log.exists():
        print("No background task logs found.")
        input("\nPress Enter to close. ")
        return 0
    pager = os.environ.get("PAGER") or "less"
    try:
        subprocess.run([pager, "-R", str(last_log)])
    except FileNotFoundError:
        sys.stdout.write(last_log.read_text(encoding="utf-8", errors="replace"))
        input("\nPress Enter to close. ")
    return 0


def _add_common_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-p",
        "--provider",
        action="append",
        help="filter or order task providers (just,make,task,npm,cargo)",
    )
    parser.add_argument("--file", help="explicit task file path")
    parser.add_argument("--makefile", help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="herdr-taskr",
        description="Interactive multi-runner project task launcher for Herdr.",
    )
    p.add_argument(
        "--remember-last",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="pre-select the last executed task for this project",
    )
    p.add_argument(
        "--default-mode",
        choices=VALID_MODES,
        default=None,
        help="override default execution mode for Enter",
    )
    _add_common_filter_args(p)
    sub = p.add_subparsers(dest="cmd", metavar="command")

    p_open = sub.add_parser("open-picker", help="open the popup task picker")
    p_open.add_argument("--cwd", help="override starting directory")
    p_open.add_argument(
        "--remember-last",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="pre-select the last executed task for this project",
    )
    p_open.add_argument(
        "--default-mode",
        choices=VALID_MODES,
        default=None,
        help="override default execution mode for Enter",
    )
    _add_common_filter_args(p_open)
    p_open.set_defaults(func=cmd_open_picker)

    p_picker = sub.add_parser("picker", help="run fzf inside the popup pane")
    p_picker.add_argument("--cwd", help="override starting directory")
    p_picker.add_argument(
        "--remember-last",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="pre-select the last executed task for this project",
    )
    p_picker.add_argument(
        "--default-mode",
        choices=VALID_MODES,
        default=None,
        help="override default execution mode for Enter",
    )
    _add_common_filter_args(p_picker)
    p_picker.set_defaults(func=cmd_picker)

    p_rerun = sub.add_parser("rerun", help="re-run the last executed task for this project")
    p_rerun.add_argument("--cwd", help="override starting directory")
    _add_common_filter_args(p_rerun)
    p_rerun.add_argument(
        "--mode",
        choices=VALID_MODES,
        help="override execution mode",
    )
    p_rerun.set_defaults(func=cmd_rerun)

    p_send = sub.add_parser(
        "send-to-agent",
        help="send the latest task output to an agent in the workspace",
    )
    p_send.add_argument("--cwd", help="override starting directory")
    _add_common_filter_args(p_send)
    p_send.add_argument(
        "--interactive",
        action="store_true",
        help="prompt with fzf when multiple agents exist",
    )
    p_send.set_defaults(func=cmd_send_to_agent)

    p_prev = sub.add_parser("preview", help="print ANSI preview for a task")
    p_prev.add_argument("--cwd", help="project directory")
    _add_common_filter_args(p_prev)
    p_prev.add_argument("target", help="task key (provider:name) or name")
    p_prev.set_defaults(func=cmd_preview)

    p_list = sub.add_parser("list", help="list discovered tasks in the project")
    p_list.add_argument("--cwd", help="directory to search from")
    _add_common_filter_args(p_list)
    p_list.set_defaults(func=cmd_list)

    p_bg = sub.add_parser("run-bg", help="run a task in the background and notify")
    p_bg.add_argument("--project-dir", default="", help="project root directory")
    p_bg.add_argument("--cwd", default="", help="working directory for the command")
    p_bg.add_argument("--task-key", required=True, help="task key (provider:name)")
    p_bg.add_argument("--label", default="", help="display label")
    p_bg.add_argument("--command", required=True, help="shell command to execute")
    p_bg.add_argument("--workspace", default="", help="Herdr workspace ID")
    p_bg.add_argument(
        "--send-to-agent",
        action="store_true",
        help="paste output into the workspace agent when finished",
    )
    p_bg.add_argument("--agent-pane", default="", help="specific agent pane ID")
    p_bg.set_defaults(func=cmd_run_bg)

    sub.add_parser("open-logs", help="open last background run log in a popup").set_defaults(
        func=cmd_open_logs
    )
    sub.add_parser("logs-pane", help="display last background run log").set_defaults(
        func=cmd_logs_pane
    )

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", cmd_open_picker)
    if not hasattr(args, "cwd"):
        args.cwd = None
    try:
        return func(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
