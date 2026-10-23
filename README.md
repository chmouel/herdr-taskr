<img src="assets/logo.svg" alt="herdr-taskr logo" width="120" align="right">

# herdr-taskr

Run your project's tasks from inside [herdr](https://herdr.dev) without typing them, and hand the results to your coding agent.

Press a key, type a few letters to find the task you want, and press Enter. The list goes away and the task runs next to you while you keep working. When a test or build fails, one more key pastes the output into the agent working in the same workspace, so you can ask it to fix the problem.

It finds tasks in Justfiles, Makefiles, Taskfiles, `package.json` scripts and Cargo projects.

![herdr-taskr picker listing make targets with a preview of the selected task](assets/screenshot.png)

## Install

```sh
herdr plugin install chmouel/herdr-taskr
```

You also need `fzf` and Python 3.11 or newer.

Then add two keys to `~/.config/herdr/config.toml`, one to pick a task and one to send its output to your agent:

```toml
[[keys.command]]
key = "prefix+t"
type = "plugin_action"
command = "herdr-taskr.launch"
description = "Run project task"

[[keys.command]]
key = "prefix+ctrl+t"
type = "plugin_action"
command = "herdr-taskr.send-to-agent"
description = "Send task output to agent"
```

## Keys

| Key | Where the task runs |
| --- | --- |
| `Enter` | in a split next to you |
| `Ctrl-F` | in a split, and moves you there |
| `Ctrl-T` | in a new tab |
| `Ctrl-B` | out of sight, with a notification when it's done |
| `Ctrl-A` | out of sight, then gives the output to your agent |

## Sending results to your agent

If you run Claude Code, Codex, Copilot or another agent in a herdr pane, herdr-taskr can give it what your task printed. There are two ways to do it.

Pick a task with `Ctrl-A` and it runs out of sight. When it finishes, the output lands in your agent's prompt.

Or run the task however you like, look at the result, and press `prefix+ctrl+t` to send that output over.

herdr-taskr pastes the output without pressing Enter for you, so you can add a line like "fix this" before sending. It moves you to the agent's pane and picks the agent in your current workspace. If you have more than one, it asks which. If the agent is waiting for you to approve something, herdr-taskr tells you instead of pasting into the wrong place.

## Re-running the last task

Bind the `herdr-taskr.rerun` action to a key the same way as above to run your last task again without opening the list.

## Settings

All settings are optional. Put them in `~/.config/herdr/plugins/config/herdr-taskr/config.toml`:

```toml
# Put the last task you ran at the top of the list
remember_last = true

# What Enter does: "split-bg", "split-focus", "tab", "background" or "agent"
default_mode = "split-bg"

# Which kinds of tasks to look for, in this order
providers = ["just", "make", "task", "npm", "cargo"]
```

## Author

[![Sponsor](https://img.shields.io/badge/Sponsor-❤️-ff69b4?style=for-the-badge&logo=github)](https://github.com/sponsors/chmouel)

### Chmouel Boudjnah

- Fediverse - <[@chmouel@chmouel.com](https://fosstodon.org/@chmouel)>
- Twitter - <[@chmouel](https://twitter.com/chmouel)>
- Blog  - <[https://blog.chmouel.com](https://blog.chmouel.com)>

## Licence

[MIT](LICENSE).
