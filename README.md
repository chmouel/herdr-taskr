<img src="assets/logo.svg" alt="herdr-taskr logo" width="120" align="right">

# herdr-taskr

A [herdr](https://herdr.dev) plugin that discovers tasks across your project (`Justfile`, `Makefile`, `Taskfile.yml`, `package.json`, and `Cargo.toml`), lets you filter and preview them in `fzf`, and runs the selected command asynchronously in a split pane, tab, background job, or workspace agent pane.

Popups in herdr capture keyboard input across the session until their process exits. `herdr-taskr` keeps the popup open only while you pick a task in `fzf`. As soon as you press Enter, the popup closes and the command runs in the background.

![herdr-taskr picker listing make targets with a preview of the selected task](assets/screenshot.png)

## Install

```sh
herdr plugin install chmouel/herdr-taskr
```

From a local checkout:

```sh
herdr plugin link ~/git/perso/herdr-taskr
```

The installer symlinks `herdr-taskr` into `~/.local/bin` when that directory exists. You can also run `sh install.sh` directly.

Bind the launcher and agent sender in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+t"
type = "plugin_action"
command = "herdr-taskr.launch"
description = "Run project task (taskr)"

[[keys.command]]
key = "prefix+ctrl+t"
type = "plugin_action"
command = "herdr-taskr.send-to-agent"
description = "Send last task output to agent"
```

## Picker keys

| Key | Action |
| --- | --- |
| `Enter` | Run in a background split pane (`--no-focus`, or `default_mode` from config) |
| `Alt-Enter` / `Ctrl-F` | Run in a focused split pane (`--focus`) |
| `Ctrl-T` | Run in a new background tab (`--no-focus`) |
| `Ctrl-B` | Run detached in the background and show a toast notification when finished |
| `Ctrl-A` | Run in the background and paste the output into the workspace agent pane |
| `Esc` | Cancel |

On wide panes (`width >= height * 2.4`), split modes open to the right; on narrower panes they open below.

## Supported task runners

`herdr-taskr` walks up from the focused pane's working directory to the git repository root and collects tasks from each enabled runner in order:

1. `just` (`Justfile`, `justfile`, `.justfile`): parses recipes, parameters, `#` comments, `[doc('...')]` attributes, dependencies, and recipe bodies. Skips `[private]` and `_`-prefixed recipes.
2. `make` (`GNUmakefile`, `makefile`, `Makefile`): follows `include` directives, extracts `##` doc comments, dependencies, and tab-indented recipes.
3. `task` (`Taskfile.yml`, `Taskfile.yaml`, `taskfile.yml`, `taskfile.yaml`): parses `tasks:`, `desc:`/`summary:`, `deps:`, and `cmds:`, skipping `internal: true` tasks.
4. `npm` (`package.json`): lists `"scripts"` and detects `pnpm`, `yarn`, `bun`, or `npm` from `packageManager` and lockfiles (`pnpm-lock.yaml`, `bun.lockb`, `bun.lock`, `yarn.lock`, `package-lock.json`).
5. `cargo` (`Cargo.toml` and `.cargo/config.toml`): provides `check`, `test`, `build`, `clippy`, `run` for binaries, `[[example]]` targets, `[[bench]]` targets, and `[alias]` entries.

Each entry in `fzf` shows a colored runner badge (`[just]`, `[make]`, `[task]`, `[pnpm]`, `[cargo]`) and a live preview of the command and recipe body.

## Configuration

Create `~/.config/herdr/plugins/config/herdr-taskr/config.toml` to customize provider order, default execution mode, or task history behavior:

```toml
# Move the last executed task for the current project to the top of the picker (default: false)
remember_last = true

# Default execution mode when pressing Enter:
# "split-bg", "split-focus", "tab", "background", or "agent"
default_mode = "split-bg"

# Override Node package manager detection ("auto", "npm", "pnpm", "yarn", or "bun")
npm_client = "auto"

# Include standard Cargo commands (check, test, build, clippy) alongside binaries and aliases
cargo_builtin_tasks = true

[providers]
enabled = ["just", "make", "task", "npm", "cargo"]

# Disable individual runners if needed:
[providers.cargo]
enabled = true
```

Environment variables and CLI flags override the config file:

```text
HERDR_TASKR_REMEMBER_LAST=1          --remember-last / --no-remember-last
HERDR_TASKR_PROVIDERS=just,make,npm  -p, --provider <runners>
HERDR_TASKR_DEFAULT_MODE=tab         --default-mode <mode>
HERDR_TASKR_NPM_CLIENT=pnpm
HERDR_TASKR_CARGO_BUILTIN=0
```

## Re-running the last task

`herdr-taskr` records the last task and execution mode per project in `~/.local/state/herdr/plugins/herdr-taskr/history.json`. Run `herdr-taskr rerun` (or bind the `herdr-taskr.rerun` plugin action) to re-execute the previous task immediately without opening the picker:

```toml
[[keys.command]]
key = "prefix+T"
type = "plugin_action"
command = "herdr-taskr.rerun"
description = "Re-run last project task"
```

## Sending output to an agent

Modeled after `herdr-reviewr`, `herdr-taskr` queries `herdr agent list` for active agent panes in the current workspace, verifies that the target agent is not blocked on a permission prompt, sends the formatted output as a bracketed paste over `HERDR_SOCKET_PATH` (`pane.send_text`, unsubmitted so you can add instructions), and focuses the agent pane.

You can trigger this in two ways:

1. Press `Ctrl-A` in the `fzf` picker to run the task in the background and paste its output into the agent once it finishes.
2. Trigger `herdr-taskr.send-to-agent` (`prefix+ctrl+t`) after any split, tab, or background task run to read the recent pane output (via `herdr pane read`) or background log and paste it into the workspace agent.

When multiple agents are running in the same workspace, `herdr-taskr` opens an `fzf` selector so you can pick which agent receives the output.

## CLI

```text
herdr-taskr open-picker      open the popup task picker in herdr
herdr-taskr rerun            re-run the last task for the current project
herdr-taskr send-to-agent    paste the latest task output into a workspace agent
herdr-taskr open-logs        open the latest background run log in a popup
herdr-taskr list             print discovered tasks for the current directory
herdr-taskr preview <key>    render the ANSI preview for a task (e.g. make:test)
```

## Requirements

Python 3.11 or later, `herdr` 0.9.0 or later, and `fzf`.

## License

MIT
