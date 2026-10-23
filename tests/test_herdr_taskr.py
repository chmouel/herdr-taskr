from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from herdr_taskr.agent import (
    bracketed_paste,
    check_agent_ready,
    format_task_output_for_agent,
    list_workspace_agents,
    send_text_to_agent,
)
from herdr_taskr.models import TaskItem, format_task_preview
from herdr_taskr.picker import (
    format_candidate_rows,
    map_expect_key,
    order_tasks_with_last,
    parse_fzf_output,
)
from herdr_taskr.providers import discover_tasks, find_provider_file
from herdr_taskr.providers.cargo import parse_cargo_toml
from herdr_taskr.providers.just import parse_justfile
from herdr_taskr.providers.make import make_command_for, parse_makefile
from herdr_taskr.providers.npm import detect_package_manager, parse_package_json
from herdr_taskr.providers.taskfile import parse_taskfile
from herdr_taskr.runner import (
    HerdrContext,
    choose_split_direction,
    dispatch_task,
    rerun_last_task,
    resolve_context,
    run_background_job,
)
from herdr_taskr.state import (
    get_last_run,
    record_last_run,
    resolve_default_mode,
    resolve_providers,
    resolve_taskr_config,
    should_remember_last,
)


class ProviderParserTests(unittest.TestCase):
    def test_find_makefile_walks_up_to_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            mf = root / "Makefile"
            mf.write_text("build:\n\tcargo build\n", encoding="utf-8")
            nested = root / "src" / "sub"
            nested.mkdir(parents=True)
            self.assertEqual(find_provider_file("make", nested), mf.resolve())

    def test_parse_makefile_targets_descriptions_and_recipes(self) -> None:
        sample = """\
.PHONY: build test lint .internal

# Leading doc comment
build: deps ## Build the release binary
\tcargo build --release

## Run the test suite
test: build
\tcargo nextest run \\
\t  --workspace

lint:
\tcargo clippy

%o: %c
\tcc -c $<

VAR := not_a_target
.internal:
\techo hidden
"""
        with tempfile.TemporaryDirectory() as tmp:
            mf = Path(tmp) / "Makefile"
            mf.write_text(sample, encoding="utf-8")
            tasks = parse_makefile(mf)

        by_name = {t.name: t for t in tasks}
        self.assertEqual(list(by_name.keys()), ["build", "test", "lint"])
        self.assertEqual(by_name["build"].provider, "make")
        self.assertEqual(by_name["build"].key, "make:build")
        self.assertEqual(by_name["build"].description, "Build the release binary")
        self.assertEqual(by_name["build"].deps, "deps")
        self.assertEqual(by_name["build"].recipe, ["cargo build --release"])
        self.assertEqual(by_name["test"].description, "Run the test suite")
        self.assertEqual(by_name["test"].deps, "build")

    def test_parse_makefile_follows_includes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inc = root / "ci.mk"
            inc.write_text("ci-check: ## Run CI checks\n\t./scripts/ci.sh\n", encoding="utf-8")
            mf = root / "Makefile"
            mf.write_text("include ci.mk\nall: ci-check ## Default target\n", encoding="utf-8")
            tasks = parse_makefile(mf)

        names = [t.name for t in tasks]
        self.assertEqual(names, ["ci-check", "all"])

    def test_parse_justfile_recipes_comments_attributes_and_private(self) -> None:
        sample = """\
set shell := ["bash", "-cu"]

# Build the project
build:
    cargo build

[doc('Run unit tests')]
test target="all": build
    cargo test {{target}}

[private]
secret:
    echo hidden

_helper:
    echo helper
"""
        with tempfile.TemporaryDirectory() as tmp:
            jf = Path(tmp) / "Justfile"
            jf.write_text(sample, encoding="utf-8")
            tasks = parse_justfile(jf)

        by_name = {t.name: t for t in tasks}
        self.assertEqual(list(by_name.keys()), ["build", "test"])
        self.assertEqual(by_name["build"].key, "just:build")
        self.assertEqual(by_name["build"].description, "Build the project")
        self.assertEqual(by_name["build"].command, "just build")
        self.assertEqual(by_name["test"].deps, "build")
        self.assertIn("Run unit tests", by_name["test"].description)
        self.assertIn('target="all"', by_name["test"].description)

    def test_parse_taskfile_yaml(self) -> None:
        sample = """\
version: '3'

tasks:
  build:
    desc: Build the binary
    cmds:
      - go build ./cmd/app

  test:
    summary: "Run unit tests"
    deps: [build]
    cmds:
      - cmd: go test ./...

  internal-helper:
    internal: true
    cmds:
      - echo secret
"""
        with tempfile.TemporaryDirectory() as tmp:
            tf = Path(tmp) / "Taskfile.yml"
            tf.write_text(sample, encoding="utf-8")
            tasks = parse_taskfile(tf)

        by_name = {t.name: t for t in tasks}
        self.assertEqual(list(by_name.keys()), ["build", "test"])
        self.assertEqual(by_name["build"].key, "task:build")
        self.assertEqual(by_name["build"].description, "Build the binary")
        self.assertEqual(by_name["build"].recipe, ["go build ./cmd/app"])
        self.assertEqual(by_name["test"].deps, "build")
        self.assertEqual(by_name["test"].command, "task test")

    def test_parse_package_json_and_detect_package_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
            pkg = root / "package.json"
            pkg.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "scripts": {
                            "dev": "vite",
                            "test:unit": "vitest run",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.assertEqual(detect_package_manager(root), "pnpm")
            tasks = parse_package_json(pkg)
            by_name = {t.name: t for t in tasks}
            self.assertEqual(list(by_name.keys()), ["dev", "test:unit"])
            self.assertEqual(by_name["dev"].provider, "npm")
            self.assertEqual(by_name["dev"].badge, "pnpm")
            self.assertEqual(by_name["dev"].command, "pnpm run dev")
            self.assertEqual(by_name["test:unit"].command, "pnpm run test:unit")

    def test_parse_cargo_toml_and_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
            (root / ".cargo").mkdir()
            (root / ".cargo" / "config.toml").write_text(
                '[alias]\nxtask = "run --package xtask --"\n',
                encoding="utf-8",
            )
            cargo_toml = root / "Cargo.toml"
            cargo_toml.write_text(
                """\
[package]
name = "demo-crate"
version = "0.1.0"

[[example]]
name = "demo"
""",
                encoding="utf-8",
            )
            tasks = parse_cargo_toml(cargo_toml)
            by_name = {t.name: t for t in tasks}
            self.assertIn("check", by_name)
            self.assertIn("test", by_name)
            self.assertIn("run", by_name)
            self.assertIn("example:demo", by_name)
            self.assertIn("xtask", by_name)
            self.assertEqual(by_name["example:demo"].command, "cargo run --example demo")
            self.assertEqual(by_name["xtask"].command, "cargo xtask")

    def test_unified_discover_tasks_respects_provider_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "Justfile").write_text("dev:\n    echo just-dev\n", encoding="utf-8")
            (root / "Makefile").write_text(
                "build: ## Make build\n\techo make-build\n", encoding="utf-8"
            )
            (root / "package.json").write_text(
                json.dumps({"scripts": {"lint": "eslint ."}}),
                encoding="utf-8",
            )

            disc = discover_tasks(root)
            self.assertEqual(
                [t.key for t in disc.tasks],
                ["just:dev", "make:build", "npm:lint"],
            )

            disc_filtered = discover_tasks(root, providers=["npm", "make"])
            self.assertEqual(
                [t.key for t in disc_filtered.tasks],
                ["npm:lint", "make:build"],
            )

    def test_format_task_preview_includes_command_recipe_and_deps(self) -> None:
        t = TaskItem(
            provider="make",
            name="build",
            description="Compile project",
            deps="fmt lint",
            recipe=["cargo build"],
            command="make build",
            line=10,
            file="Makefile",
        )
        preview = format_task_preview(t)
        self.assertIn("[make]", preview)
        self.assertIn("build", preview)
        self.assertIn("(Makefile:10)", preview)
        self.assertIn("make build", preview)
        self.assertIn("Compile project", preview)
        self.assertIn("fmt lint", preview)
        self.assertIn("cargo build", preview)


class PickerTests(unittest.TestCase):
    def test_format_candidate_rows_and_parse_fzf_output(self) -> None:
        tasks = [
            TaskItem(provider="just", name="build", description="Build binary"),
            TaskItem(provider="npm", provider_label="pnpm", name="test", description="Run tests"),
        ]
        rows = format_candidate_rows(tasks)
        self.assertTrue(any(r.endswith("\tjust:build") for r in rows))
        self.assertTrue(any("[pnpm]" in r and r.endswith("\tnpm:test") for r in rows))
        self.assertEqual(map_expect_key(""), "split-bg")
        self.assertEqual(map_expect_key("", default_mode="tab"), "tab")
        self.assertEqual(map_expect_key("alt-enter"), "split-focus")
        self.assertEqual(map_expect_key("ctrl-f"), "split-focus")
        self.assertEqual(map_expect_key("ctrl-t"), "tab")
        self.assertEqual(map_expect_key("ctrl-b"), "background")
        self.assertEqual(map_expect_key("ctrl-a"), "agent")

        res = parse_fzf_output("ctrl-t\n[pnpm] test   Run tests\tnpm:test\n")
        self.assertEqual(res.outcome, "picked")
        self.assertEqual(res.task_key, "npm:test")
        self.assertEqual(res.target, "test")
        self.assertEqual(res.mode, "tab")

    def test_remember_last_orders_task_first_with_badge(self) -> None:
        tasks = [
            TaskItem(provider="just", name="build", description="Build binary"),
            TaskItem(provider="make", name="test", description="Run tests"),
            TaskItem(provider="npm", name="lint", description="Run linter"),
        ]
        ordered = order_tasks_with_last(tasks, "npm:lint")
        self.assertEqual([t.key for t in ordered], ["npm:lint", "just:build", "make:test"])

        rows = format_candidate_rows(tasks, last_key="npm:lint")
        self.assertTrue(rows[0].endswith("\tnpm:lint"))
        self.assertIn("[last]", rows[0])
        self.assertNotIn("[last]", rows[1])


class ConfigAndStateTests(unittest.TestCase):
    def test_should_remember_last_and_configurable_providers_and_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "cfg"
            cfg_dir.mkdir()
            self.assertFalse(should_remember_last(env={}, config_dir=cfg_dir))
            self.assertEqual(
                resolve_providers(env={}, config_dir=cfg_dir),
                ["just", "make", "task", "npm", "cargo"],
            )
            self.assertEqual(resolve_default_mode(env={}, config_dir=cfg_dir), "split-bg")

            (cfg_dir / "config.toml").write_text(
                """\
remember_last = true
default_mode = "tab"
npm_client = "bun"
cargo_builtin_tasks = false

[providers]
enabled = ["cargo", "just", "make"]

[providers.make]
enabled = false
""",
                encoding="utf-8",
            )
            cfg = resolve_taskr_config(env={}, config_dir=cfg_dir)
            self.assertTrue(cfg.remember_last)
            self.assertEqual(cfg.default_mode, "tab")
            self.assertEqual(cfg.providers, ["cargo", "just"])
            self.assertEqual(cfg.npm_client, "bun")
            self.assertFalse(cfg.cargo_builtin_tasks)

            # Env overrides config file
            self.assertFalse(
                should_remember_last(
                    env={"HERDR_TASKR_REMEMBER_LAST": "0"},
                    config_dir=cfg_dir,
                )
            )
            self.assertEqual(
                resolve_providers(
                    env={"HERDR_TASKR_PROVIDERS": "npm,task"},
                    config_dir=cfg_dir,
                ),
                ["npm", "task"],
            )
            # CLI overrides env
            self.assertTrue(
                should_remember_last(
                    cli_override=True,
                    env={"HERDR_TASKR_REMEMBER_LAST": "0"},
                    config_dir=cfg_dir,
                )
            )

    def test_record_and_get_last_run_and_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            mf = root / "Makefile"
            mf.write_text("build:\n\techo build\ntest:\n\techo test\n", encoding="utf-8")

            self.assertIsNone(get_last_run(root, state_dir=state_dir))
            record_last_run(
                root,
                "make:test",
                "tab",
                command="make test",
                state_dir=state_dir,
            )
            last = get_last_run(root, state_dir=state_dir)
            self.assertIsNotNone(last)
            assert last is not None
            self.assertEqual(last.task_key, "make:test")
            self.assertEqual(last.target, "test")
            self.assertEqual(last.mode, "tab")

            with (
                mock.patch.dict("os.environ", {"HERDR_PLUGIN_STATE_DIR": str(state_dir)}),
                mock.patch("herdr_taskr.runner.dispatch_task", return_value=0) as mock_dispatch,
            ):
                ctx = HerdrContext(pane_id="%1", workspace_id="%0", cwd=str(root))
                rc = rerun_last_task(ctx, "/bin/herdr-taskr", explicit_file=mf)
                self.assertEqual(rc, 0)
                mock_dispatch.assert_called_once()
                dispatched_task = mock_dispatch.call_args.args[0]
                self.assertEqual(dispatched_task.key, "make:test")
                self.assertEqual(mock_dispatch.call_args.args[1], "tab")


class AgentSendTests(unittest.TestCase):
    def test_bracketed_paste_strips_inner_end_marker(self) -> None:
        pasted = bracketed_paste("hello \x1b[201~world")
        self.assertEqual(pasted, "\x1b[200~hello world\x1b[201~")

    def test_format_task_output_for_agent_strips_ansi_and_truncates(self) -> None:
        raw = "\x1b[31mError:\x1b[0m build failed\nline 2"
        formatted = format_task_output_for_agent(raw)
        self.assertEqual(formatted, "```\nError: build failed\nline 2\n```")

        huge = "$ just test\n" + ("x" * 2000 + "\n") * 20
        truncated = format_task_output_for_agent(huge, max_bytes=4096)
        self.assertIn("$ just test", truncated)
        self.assertIn("bytes truncated", truncated)

    @mock.patch("herdr_taskr.agent._herdr_json")
    def test_list_workspace_agents_and_readiness(self, mock_herdr: mock.Mock) -> None:
        mock_herdr.return_value = {
            "result": {
                "agents": [
                    {
                        "pane_id": "w1:p1",
                        "workspace_id": "w1",
                        "tab_id": "w1:t1",
                        "agent": "claude",
                        "name": "review-bot",
                        "agent_status": "idle",
                    },
                    {
                        "pane_id": "w1:p2",
                        "workspace_id": "w1",
                        "tab_id": "w1:t2",
                        "agent": "codex",
                        "agent_status": "blocked",
                    },
                    {
                        "pane_id": "w2:p1",
                        "workspace_id": "w2",
                        "tab_id": "w2:t1",
                        "agent": "claude",
                        "agent_status": "idle",
                    },
                ]
            }
        }
        agents = list_workspace_agents("w1", exclude_pane_id="w1:p9")
        self.assertEqual([a.pane_id for a in agents], ["w1:p1", "w1:p2"])
        self.assertEqual(agents[0].name, "review-bot")

        ok, name, err = check_agent_ready("w1:p1")
        self.assertTrue(ok)
        self.assertEqual(name, "review-bot")
        self.assertEqual(err, "")

        ok_blocked, name_blocked, err_blocked = check_agent_ready("w1:p2")
        self.assertFalse(ok_blocked)
        self.assertEqual(name_blocked, "codex")
        self.assertEqual(err_blocked, "answer codex's prompt first")

    @mock.patch("herdr_taskr.agent.subprocess.run")
    @mock.patch("herdr_taskr.agent.check_agent_ready", return_value=(True, "claude", ""))
    def test_send_text_to_agent_over_unix_socket(
        self,
        _mock_ready: mock.Mock,
        mock_run: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sock_path = str(Path(tmp) / "herdr.sock")
            received: list[str] = []
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(sock_path)
            srv.listen(1)

            def server() -> None:
                with srv:
                    conn, _ = srv.accept()
                    with conn:
                        data = b""
                        while b"\n" not in data:
                            chunk = conn.recv(4096)
                            if not chunk:
                                break
                            data += chunk
                        received.append(data.decode("utf-8"))
                        conn.sendall(b'{"id":"herdr-taskr:send","result":{"type":"ok"}}\n')

            t = threading.Thread(target=server, daemon=True)
            t.start()

            with mock.patch.dict("os.environ", {"HERDR_SOCKET_PATH": sock_path}):
                ok, agent_name = send_text_to_agent("w1:p1", "```\ntask output\n```", focus=True)

            t.join(timeout=2)
            self.assertTrue(ok)
            self.assertEqual(agent_name, "claude")
            self.assertEqual(len(received), 1)
            payload = json.loads(received[0])
            self.assertEqual(payload["method"], "pane.send_text")
            self.assertEqual(payload["params"]["pane_id"], "w1:p1")
            self.assertEqual(
                payload["params"]["text"],
                "\x1b[200~```\ntask output\n```\x1b[201~",
            )
            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args.args[0][-3:], ["agent", "focus", "w1:p1"])


class RunnerTests(unittest.TestCase):
    def test_resolve_context_from_plugin_context_json(self) -> None:
        payload = {
            "workspace_id": "%2",
            "tab_id": "%5",
            "focused_pane_id": "%9",
            "focused_pane_cwd": "/tmp/proj",
        }
        ctx = resolve_context({"HERDR_PLUGIN_CONTEXT_JSON": json.dumps(payload)})
        self.assertEqual(ctx.workspace_id, "%2")
        self.assertEqual(ctx.tab_id, "%5")
        self.assertEqual(ctx.pane_id, "%9")
        self.assertEqual(ctx.cwd, "/tmp/proj")

    @mock.patch("herdr_taskr.runner.herdr_json")
    def test_choose_split_direction_uses_aspect_ratio(self, mock_herdr: mock.Mock) -> None:
        mock_herdr.return_value = {
            "result": {
                "layout": {"panes": [{"pane_id": "%1", "rect": {"width": 160, "height": 40}}]}
            }
        }
        self.assertEqual(choose_split_direction("%1"), "right")

        mock_herdr.return_value = {
            "result": {
                "layout": {"panes": [{"pane_id": "%1", "rect": {"width": 80, "height": 50}}]}
            }
        }
        self.assertEqual(choose_split_direction("%1"), "down")

    @mock.patch("herdr_taskr.runner.subprocess.run")
    @mock.patch("herdr_taskr.runner.choose_split_direction", return_value="down")
    @mock.patch("herdr_taskr.runner.herdr_json")
    def test_dispatch_split_bg(
        self,
        mock_herdr: mock.Mock,
        _mock_dir: mock.Mock,
        _mock_run: mock.Mock,
    ) -> None:
        mock_herdr.side_effect = [
            {"result": {"pane": {"pane_id": "%42"}}},
            {"result": {}},
        ]
        ctx = HerdrContext(pane_id="%1", workspace_id="%0", cwd="/tmp/proj")
        task = TaskItem(
            provider="just",
            name="test",
            command="just test",
            argv=["just", "test"],
            cwd=Path("/tmp/proj"),
        )
        rc = dispatch_task(task, "split-bg", Path("/tmp/proj"), ctx, "/bin/herdr-taskr")
        self.assertEqual(rc, 0)
        self.assertEqual(
            mock_herdr.call_args_list[0].args[0],
            [
                "pane",
                "split",
                "--direction",
                "down",
                "--cwd",
                "/tmp/proj",
                "--no-focus",
                "--pane",
                "%1",
            ],
        )
        self.assertEqual(
            mock_herdr.call_args_list[1].args[0],
            ["pane", "run", "%42", "just test"],
        )

    @mock.patch("herdr_taskr.runner.subprocess.run")
    @mock.patch("herdr_taskr.runner.herdr_json")
    def test_dispatch_tab(self, mock_herdr: mock.Mock, _mock_run: mock.Mock) -> None:
        mock_herdr.side_effect = [
            {"result": {"root_pane": {"pane_id": "%77"}}},
            {"result": {}},
        ]
        ctx = HerdrContext(pane_id="%1", workspace_id="%0", cwd="/tmp/proj")
        task = TaskItem(
            provider="npm",
            provider_label="pnpm",
            name="build",
            command="pnpm run build",
            argv=["pnpm", "run", "build"],
            cwd=Path("/tmp/proj"),
        )
        rc = dispatch_task(task, "tab", Path("/tmp/proj"), ctx, "/bin/herdr-taskr")
        self.assertEqual(rc, 0)
        self.assertEqual(
            mock_herdr.call_args_list[0].args[0],
            [
                "tab",
                "create",
                "--cwd",
                "/tmp/proj",
                "--label",
                "pnpm:build",
                "--no-focus",
                "--workspace",
                "%0",
            ],
        )
        self.assertEqual(
            mock_herdr.call_args_list[1].args[0],
            ["pane", "run", "%77", "pnpm run build"],
        )

    def test_make_command_for_custom_filename(self) -> None:
        self.assertEqual(make_command_for(Path("/a/Makefile"), "build")[0], "make build")
        self.assertEqual(
            make_command_for(Path("/a/GNUmakefile"), "build")[0],
            "make -f GNUmakefile build",
        )

    @mock.patch("herdr_taskr.runner.notify")
    @mock.patch("herdr_taskr.runner._set_workspace_metadata")
    def test_run_background_job_logs_and_notifies(
        self,
        _mock_meta: mock.Mock,
        mock_notify: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": str(root / "cache")}):
                rc = run_background_job(
                    project_dir=root,
                    task_cwd=root,
                    task_key="make:hello",
                    label="make:hello",
                    command="echo hello-from-taskr",
                    workspace_id="%0",
                )
                self.assertEqual(rc, 0)
                last_log = root / "cache" / "herdr-taskr" / "last.log"
                self.assertTrue(last_log.exists())
                self.assertIn("hello-from-taskr", last_log.read_text(encoding="utf-8"))
                self.assertEqual(mock_notify.call_args_list[-1].kwargs.get("sound"), "done")


if __name__ == "__main__":
    unittest.main()
