"""Configuration loading and validation for the local application process."""

from __future__ import annotations

import copy
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when a user-visible configuration field is invalid."""


@dataclass(frozen=True, slots=True)
class ServerSettings:
    port: int = 8000
    open_browser: bool = True


@dataclass(frozen=True, slots=True)
class ModelSettings:
    base_url: str = "https://api.anthropic.com"
    model: str = "claude-model-name"
    api_key_env: str = "ANTHROPIC_API_KEY"
    context_window: int = 64000
    max_output_tokens: int = 8192
    stream: bool = True


@dataclass(frozen=True, slots=True)
class AgentSettings:
    max_rounds: int = 30
    tool_argument_retries: int = 2
    doom_loop_threshold: int = 3


@dataclass(frozen=True, slots=True)
class ContextSettings:
    compact_trigger_ratio: float = 0.80
    compact_target_ratio: float = 0.60
    safety_margin_tokens: int = 2048
    summary_max_tokens: int = 2048
    recent_turns_min: int = 2
    recent_budget_ratio: float = 0.40


@dataclass(frozen=True, slots=True)
class RetrySettings:
    max_attempts: int = 5
    initial_delay_seconds: float = 2
    max_delay_seconds: float = 30
    jitter_ratio: float = 0.25


@dataclass(frozen=True, slots=True)
class ToolSettings:
    read_max_lines: int = 800
    read_max_bytes: int = 40960
    command_timeout_seconds: int = 120
    command_output_bytes: int = 40960
    kill_grace_seconds: int = 3
    pass_env: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AppSettings:
    server: ServerSettings
    model: ModelSettings
    agent: AgentSettings
    context: ContextSettings
    retry: RetrySettings
    tools: ToolSettings


_DEFAULTS: dict[str, dict[str, object]] = {
    "server": {"port": 8000, "open_browser": True},
    "model": {
        "base_url": "https://api.anthropic.com",
        "model": "claude-model-name",
        "api_key_env": "ANTHROPIC_API_KEY",
        "context_window": 64000,
        "max_output_tokens": 8192,
        "stream": True,
    },
    "agent": {"max_rounds": 30, "tool_argument_retries": 2, "doom_loop_threshold": 3},
    "context": {
        "compact_trigger_ratio": 0.80,
        "compact_target_ratio": 0.60,
        "safety_margin_tokens": 2048,
        "summary_max_tokens": 2048,
        "recent_turns_min": 2,
        "recent_budget_ratio": 0.40,
    },
    "retry": {
        "max_attempts": 5,
        "initial_delay_seconds": 2,
        "max_delay_seconds": 30,
        "jitter_ratio": 0.25,
    },
    "tools": {
        "read_max_lines": 800,
        "read_max_bytes": 40960,
        "command_timeout_seconds": 120,
        "command_output_bytes": 40960,
        "kill_grace_seconds": 3,
        "pass_env": (),
    },
}


def load_settings(
    config_path: Path | None,
    cli_overrides: Mapping[str, object],
    environ: Mapping[str, str],
) -> AppSettings:
    """Load settings with command-line values taking precedence over TOML values.

    Credentials remain outside the resulting object. ``environ`` is accepted here so
    callers have a uniform configuration boundary; ``resolve_api_key`` performs the
    separate secret lookup immediately before creating a model client.
    """
    _ = environ
    merged = copy.deepcopy(_DEFAULTS)
    if config_path is not None:
        _merge_toml(merged, config_path)
    _merge_cli_overrides(merged, cli_overrides)
    return _make_settings(merged)


def resolve_api_key(settings: AppSettings, environ: Mapping[str, str]) -> str:
    """Return the configured credential without storing it in application settings."""
    env_name = settings.model.api_key_env
    value = environ.get(env_name, "")
    if not value:
        raise ConfigurationError(
            f"model.api_key_env: environment variable {env_name!r} must be set"
        )
    return value


def _merge_toml(merged: dict[str, dict[str, object]], config_path: Path) -> None:
    try:
        with config_path.open("rb") as config_file:
            parsed = tomllib.load(config_file)
    except FileNotFoundError as error:
        raise ConfigurationError(f"config_path: file not found: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigurationError(f"config_path: invalid TOML: {error}") from error

    for section, values in parsed.items():
        if section not in merged:
            raise ConfigurationError(f"{section}: unknown configuration section")
        if not isinstance(values, dict):
            raise ConfigurationError(f"{section}: expected a TOML table")
        for field, value in values.items():
            if field not in merged[section]:
                raise ConfigurationError(f"{section}.{field}: unknown configuration field")
            merged[section][field] = value


def _merge_cli_overrides(
    merged: dict[str, dict[str, object]], cli_overrides: Mapping[str, object]
) -> None:
    for dotted_name, value in cli_overrides.items():
        section, separator, field = dotted_name.partition(".")
        if not separator or "." in field or section not in merged or field not in merged[section]:
            raise ConfigurationError(f"{dotted_name}: unknown CLI override")
        merged[section][field] = value


def _make_settings(values: dict[str, dict[str, object]]) -> AppSettings:
    server = ServerSettings(
        port=_integer(values["server"]["port"], "server.port"),
        open_browser=_boolean(values["server"]["open_browser"], "server.open_browser"),
    )
    model = ModelSettings(
        base_url=_nonempty_string(values["model"]["base_url"], "model.base_url"),
        model=_nonempty_string(values["model"]["model"], "model.model"),
        api_key_env=_nonempty_string(values["model"]["api_key_env"], "model.api_key_env"),
        context_window=_integer(values["model"]["context_window"], "model.context_window"),
        max_output_tokens=_integer(values["model"]["max_output_tokens"], "model.max_output_tokens"),
        stream=_boolean(values["model"]["stream"], "model.stream"),
    )
    agent = AgentSettings(
        max_rounds=_integer(values["agent"]["max_rounds"], "agent.max_rounds"),
        tool_argument_retries=_integer(
            values["agent"]["tool_argument_retries"], "agent.tool_argument_retries"
        ),
        doom_loop_threshold=_integer(
            values["agent"]["doom_loop_threshold"], "agent.doom_loop_threshold"
        ),
    )
    context = ContextSettings(
        compact_trigger_ratio=_number(
            values["context"]["compact_trigger_ratio"], "context.compact_trigger_ratio"
        ),
        compact_target_ratio=_number(
            values["context"]["compact_target_ratio"], "context.compact_target_ratio"
        ),
        safety_margin_tokens=_integer(
            values["context"]["safety_margin_tokens"], "context.safety_margin_tokens"
        ),
        summary_max_tokens=_integer(
            values["context"]["summary_max_tokens"], "context.summary_max_tokens"
        ),
        recent_turns_min=_integer(
            values["context"]["recent_turns_min"], "context.recent_turns_min"
        ),
        recent_budget_ratio=_number(
            values["context"]["recent_budget_ratio"], "context.recent_budget_ratio"
        ),
    )
    retry = RetrySettings(
        max_attempts=_integer(values["retry"]["max_attempts"], "retry.max_attempts"),
        initial_delay_seconds=_number(
            values["retry"]["initial_delay_seconds"], "retry.initial_delay_seconds"
        ),
        max_delay_seconds=_number(values["retry"]["max_delay_seconds"], "retry.max_delay_seconds"),
        jitter_ratio=_number(values["retry"]["jitter_ratio"], "retry.jitter_ratio"),
    )
    tools = ToolSettings(
        read_max_lines=_integer(values["tools"]["read_max_lines"], "tools.read_max_lines"),
        read_max_bytes=_integer(values["tools"]["read_max_bytes"], "tools.read_max_bytes"),
        command_timeout_seconds=_integer(
            values["tools"]["command_timeout_seconds"], "tools.command_timeout_seconds"
        ),
        command_output_bytes=_integer(
            values["tools"]["command_output_bytes"], "tools.command_output_bytes"
        ),
        kill_grace_seconds=_integer(
            values["tools"]["kill_grace_seconds"], "tools.kill_grace_seconds"
        ),
        pass_env=_string_tuple(values["tools"]["pass_env"], "tools.pass_env"),
    )
    _validate(server, model, agent, context, retry, tools)
    return AppSettings(server, model, agent, context, retry, tools)


def _validate(
    server: ServerSettings,
    model: ModelSettings,
    agent: AgentSettings,
    context: ContextSettings,
    retry: RetrySettings,
    tools: ToolSettings,
) -> None:
    _require_positive(server.port, "server.port")
    _require_positive(model.context_window, "model.context_window")
    _require_positive(model.max_output_tokens, "model.max_output_tokens")
    if not model.stream:
        raise ConfigurationError("model.stream: P0 requires stream=true")
    _require_positive(agent.tool_argument_retries, "agent.tool_argument_retries")
    _require_positive(agent.doom_loop_threshold, "agent.doom_loop_threshold")
    if agent.max_rounds < 2:
        raise ConfigurationError("agent.max_rounds: must be at least 2")
    if not 0 < context.compact_target_ratio < context.compact_trigger_ratio < 1:
        raise ConfigurationError(
            "context.compact_target_ratio: must satisfy "
            "0 < compact_target_ratio < compact_trigger_ratio < 1"
        )
    if not 0 < context.recent_budget_ratio < 1:
        raise ConfigurationError("context.recent_budget_ratio: must be between 0 and 1")
    _require_positive(context.safety_margin_tokens, "context.safety_margin_tokens")
    if model.context_window <= model.max_output_tokens + context.safety_margin_tokens:
        raise ConfigurationError(
            "model.context_window: must exceed model.max_output_tokens plus "
            "context.safety_margin_tokens so an input budget remains; raise "
            "model.context_window or lower model.max_output_tokens or "
            "context.safety_margin_tokens"
        )
    _require_positive(context.summary_max_tokens, "context.summary_max_tokens")
    if context.summary_max_tokens >= model.context_window:
        raise ConfigurationError("context.summary_max_tokens: must be below model.context_window")
    if context.recent_turns_min < 2:
        raise ConfigurationError(
            "context.recent_turns_min: must keep at least 2 complete user turns; "
            "set it to 2 or more"
        )
    _require_positive(retry.max_attempts, "retry.max_attempts")
    _require_positive(retry.initial_delay_seconds, "retry.initial_delay_seconds")
    _require_positive(retry.max_delay_seconds, "retry.max_delay_seconds")
    if retry.max_delay_seconds < retry.initial_delay_seconds:
        raise ConfigurationError(
            "retry.max_delay_seconds: must be at least retry.initial_delay_seconds"
        )
    if not 0 <= retry.jitter_ratio <= 1:
        raise ConfigurationError("retry.jitter_ratio: must be between 0 and 1")
    _require_positive(tools.read_max_lines, "tools.read_max_lines")
    _require_positive(tools.read_max_bytes, "tools.read_max_bytes")
    _require_positive(tools.command_timeout_seconds, "tools.command_timeout_seconds")
    _require_positive(tools.command_output_bytes, "tools.command_output_bytes")
    _require_positive(tools.kill_grace_seconds, "tools.kill_grace_seconds")


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field}: must be a non-empty string")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{field}: must be an integer")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigurationError(f"{field}: must be a number")
    return float(value)


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{field}: must be a boolean")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(f"{field}: must be an array of strings")
    return tuple(value)


def _require_positive(value: int | float, field: str) -> None:
    if value <= 0:
        raise ConfigurationError(f"{field}: must be positive")
