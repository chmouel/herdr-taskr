"""Configuration and per-project task history persistence for herdr-taskr."""

from __future__ import annotations

import json
import os
import time
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .providers import DEFAULT_PROVIDERS, normalize_provider_list, normalize_provider_name

PLUGIN_ID = "herdr-taskr"

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
VALID_MODES = ("split-bg", "split-focus", "tab", "background", "agent")


@dataclass(frozen=True)
class TaskrConfig:
    remember_last: bool = False
    providers: list[str] = field(default_factory=lambda: list(DEFAULT_PROVIDERS))
    default_mode: str = "split-bg"
    npm_client: str = "auto"
    cargo_builtin_tasks: bool = True


@dataclass(frozen=True)
class LastRun:
    task_key: str
    provider: str = ""
    target: str = ""
    command: str = ""
    mode: str = "split-bg"
    pane_id: str = ""
    log_file: str = ""
    updated_at: float = 0.0


def plugin_config_dir(env: Mapping[str, str] | None = None) -> Path:
    env = env if env is not None else os.environ
    explicit = env.get("HERDR_PLUGIN_CONFIG_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    xdg = env.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "herdr" / "plugins" / "config" / PLUGIN_ID


def plugin_state_dir(env: Mapping[str, str] | None = None) -> Path:
    env = env if env is not None else os.environ
    explicit = env.get("HERDR_PLUGIN_STATE_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    xdg = env.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "herdr" / "plugins" / PLUGIN_ID


def parse_bool_env(val: str) -> bool | None:
    cleaned = val.strip().lower()
    if cleaned in TRUE_VALUES:
        return True
    if cleaned in FALSE_VALUES:
        return False
    return None


def load_config(config_dir: Path | None = None, env: Mapping[str, str] | None = None) -> dict:
    cdir = config_dir if config_dir is not None else plugin_config_dir(env)
    cfg_file = cdir / "config.toml"
    if not cfg_file.is_file():
        return {}
    try:
        return tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def should_remember_last(
    cli_override: bool | None = None,
    env: Mapping[str, str] | None = None,
    config_dir: Path | None = None,
) -> bool:
    """Resolve whether remember_last is enabled (default: False)."""
    if cli_override is not None:
        return cli_override

    env = env if env is not None else os.environ
    for env_key in ("HERDR_TASKR_REMEMBER_LAST", "HERDR_MAKE_REMEMBER_LAST"):
        env_raw = env.get(env_key, "")
        if env_raw:
            parsed = parse_bool_env(env_raw)
            if parsed is not None:
                return parsed

    cfg = load_config(config_dir=config_dir, env=env)
    if isinstance(cfg.get("remember_last"), bool):
        return cfg["remember_last"]
    picker_section = cfg.get("picker")
    if isinstance(picker_section, dict) and isinstance(picker_section.get("remember_last"), bool):
        return picker_section["remember_last"]

    return False


def resolve_providers(
    cli_providers: Sequence[str] | str | None = None,
    env: Mapping[str, str] | None = None,
    config_dir: Path | None = None,
) -> list[str]:
    """Resolve ordered list of enabled task providers."""
    if cli_providers:
        if isinstance(cli_providers, str):
            return normalize_provider_list([cli_providers])
        return normalize_provider_list(cli_providers)

    env = env if env is not None else os.environ
    env_raw = env.get("HERDR_TASKR_PROVIDERS", "").strip()
    if env_raw:
        return normalize_provider_list([env_raw])

    cfg = load_config(config_dir=config_dir, env=env)
    raw_list: list[str] | None = None
    if isinstance(cfg.get("providers"), list):
        raw_list = [str(x) for x in cfg["providers"]]
    elif isinstance(cfg.get("providers"), dict):
        prov_sec = cfg["providers"]
        if isinstance(prov_sec.get("enabled"), list):
            raw_list = [str(x) for x in prov_sec["enabled"]]

    ordered = normalize_provider_list(raw_list) if raw_list else list(DEFAULT_PROVIDERS)

    # Filter out any provider explicitly disabled via `disabled_providers = [...]`
    # or `[providers.<name>] enabled = false`
    disabled: set[str] = set()
    if isinstance(cfg.get("disabled_providers"), list):
        for item in cfg["disabled_providers"]:
            norm = normalize_provider_name(str(item))
            if norm:
                disabled.add(norm)
    prov_table = cfg.get("providers")
    if isinstance(prov_table, dict):
        if isinstance(prov_table.get("disabled"), list):
            for item in prov_table["disabled"]:
                norm = normalize_provider_name(str(item))
                if norm:
                    disabled.add(norm)
        for key, sub in prov_table.items():
            norm = normalize_provider_name(str(key))
            if norm and isinstance(sub, dict) and sub.get("enabled") is False:
                disabled.add(norm)
    if disabled:
        ordered = [p for p in ordered if p not in disabled]

    return ordered


def resolve_default_mode(
    cli_mode: str | None = None,
    env: Mapping[str, str] | None = None,
    config_dir: Path | None = None,
) -> str:
    """Resolve the default execution mode for Enter (default: split-bg)."""
    if cli_mode and cli_mode in VALID_MODES:
        return cli_mode

    env = env if env is not None else os.environ
    env_mode = env.get("HERDR_TASKR_DEFAULT_MODE", "").strip().lower()
    if env_mode in VALID_MODES:
        return env_mode

    cfg = load_config(config_dir=config_dir, env=env)
    for candidate in (
        cfg.get("default_mode"),
        (cfg.get("picker") or {}).get("default_mode")
        if isinstance(cfg.get("picker"), dict)
        else None,
    ):
        if isinstance(candidate, str) and candidate.strip().lower() in VALID_MODES:
            return candidate.strip().lower()

    return "split-bg"


def resolve_taskr_config(
    *,
    remember_last: bool | None = None,
    providers: Sequence[str] | str | None = None,
    default_mode: str | None = None,
    env: Mapping[str, str] | None = None,
    config_dir: Path | None = None,
) -> TaskrConfig:
    env = env if env is not None else os.environ
    cfg = load_config(config_dir=config_dir, env=env)
    prov_table = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}

    # npm_client
    npm_client = env.get("HERDR_TASKR_NPM_CLIENT", "").strip().lower()
    if not npm_client:
        if isinstance(cfg.get("npm_client"), str):
            npm_client = cfg["npm_client"].strip().lower()
        elif isinstance(prov_table.get("npm"), dict) and isinstance(
            prov_table["npm"].get("client"), str
        ):
            npm_client = prov_table["npm"]["client"].strip().lower()
    if not npm_client:
        npm_client = "auto"

    # cargo_builtin_tasks
    cargo_builtin: bool | None = None
    env_cargo = env.get("HERDR_TASKR_CARGO_BUILTIN", "")
    if env_cargo:
        cargo_builtin = parse_bool_env(env_cargo)
    if cargo_builtin is None:
        if isinstance(cfg.get("cargo_builtin_tasks"), bool):
            cargo_builtin = cfg["cargo_builtin_tasks"]
        elif isinstance(prov_table.get("cargo"), dict) and isinstance(
            prov_table["cargo"].get("builtin_tasks"), bool
        ):
            cargo_builtin = prov_table["cargo"]["builtin_tasks"]
    if cargo_builtin is None:
        cargo_builtin = True

    return TaskrConfig(
        remember_last=should_remember_last(
            cli_override=remember_last, env=env, config_dir=config_dir
        ),
        providers=resolve_providers(cli_providers=providers, env=env, config_dir=config_dir),
        default_mode=resolve_default_mode(cli_mode=default_mode, env=env, config_dir=config_dir),
        npm_client=npm_client,
        cargo_builtin_tasks=cargo_builtin,
    )


def _history_file(state_dir: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    sdir = state_dir if state_dir is not None else plugin_state_dir(env)
    sdir.mkdir(parents=True, exist_ok=True)
    return sdir / "history.json"


def _normalize_scope_key(scope: str | Path) -> str:
    p = Path(scope).expanduser().resolve()
    if p.is_file():
        p = p.parent
    return str(p)


def get_last_run(
    scope: str | Path,
    state_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> LastRun | None:
    resolved = Path(scope).expanduser().resolve()
    dir_key = _normalize_scope_key(resolved)
    exact_key = str(resolved)
    hfile = _history_file(state_dir=state_dir, env=env)
    if not hfile.is_file():
        return None
    try:
        data = json.loads(hfile.read_text(encoding="utf-8"))
        entry = data.get(dir_key) or data.get(exact_key)
        if not isinstance(entry, dict):
            return None
        task_key = str(entry.get("task_key") or "")
        target = str(entry.get("target") or "")
        provider = str(entry.get("provider") or "")
        if not task_key and target:
            task_key = f"{provider or 'make'}:{target}"
        if not task_key:
            return None
        if ":" in task_key and (not provider or not target):
            p_part, t_part = task_key.split(":", 1)
            provider = provider or p_part
            target = target or t_part
        return LastRun(
            task_key=task_key,
            provider=provider,
            target=target,
            command=str(entry.get("command") or ""),
            mode=str(entry.get("mode") or "split-bg"),
            pane_id=str(entry.get("pane_id") or ""),
            log_file=str(entry.get("log_file") or ""),
            updated_at=float(entry.get("updated_at") or 0.0),
        )
    except Exception:
        return None


def record_last_run(
    scope: str | Path,
    task_key: str,
    mode: str = "split-bg",
    *,
    provider: str = "",
    target: str = "",
    command: str = "",
    pane_id: str = "",
    log_file: str = "",
    state_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    key = _normalize_scope_key(scope)
    if ":" in task_key and (not provider or not target):
        p_part, t_part = task_key.split(":", 1)
        provider = provider or p_part
        target = target or t_part
    elif not target:
        target = task_key
        provider = provider or "make"
        task_key = f"{provider}:{target}"

    hfile = _history_file(state_dir=state_dir, env=env)
    data: dict = {}
    if hfile.is_file():
        try:
            loaded = json.loads(hfile.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    data[key] = {
        "task_key": task_key,
        "provider": provider,
        "target": target,
        "command": command,
        "mode": mode,
        "pane_id": pane_id,
        "log_file": log_file,
        "updated_at": time.time(),
    }
    tmp_file = hfile.with_suffix(".json.tmp")
    tmp_file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_file.replace(hfile)
