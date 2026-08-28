from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.config import ConfigurationError, load_settings, resolve_api_key


def test_cli_override_wins_over_toml(tmp_path: Path) -> None:
    """Changing CLI precedence must not let a TOML value win."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("[agent]\nmax_rounds = 7\n", encoding="utf-8")

    settings = load_settings(config_file, {"agent.max_rounds": 9}, {})

    assert settings.agent.max_rounds == 9


def test_secret_value_is_absent_from_settings_repr(valid_settings) -> None:
    """Adding a resolved credential to settings would expose it in diagnostics."""
    key = resolve_api_key(valid_settings, {"ANTHROPIC_API_KEY": "secret-sentinel"})

    assert key == "secret-sentinel"
    assert "secret-sentinel" not in repr(valid_settings)


@pytest.mark.parametrize(
    ("toml", "field"),
    [
        ('[model]\nmodel = ""\n', "model.model"),
        ('[model]\nbase_url = ""\n', "model.base_url"),
        ("[model]\ncontext_window = 8192\nmax_output_tokens = 8193\n", "model.context_window"),
        ("[model]\nstream = false\n", "model.stream"),
        (
            "[context]\ncompact_trigger_ratio = 0.6\ncompact_target_ratio = 0.6\n",
            "context.compact_target_ratio",
        ),
        ("[agent]\nmax_rounds = 1\n", "agent.max_rounds"),
        ("[tools]\ncommand_timeout_seconds = 0\n", "tools.command_timeout_seconds"),
    ],
)
def test_invalid_configuration_reports_its_field(tmp_path: Path, toml: str, field: str) -> None:
    """Removing each required constraint must reject the affected public field."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(toml, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=field):
        load_settings(config_file, {}, {})


def test_unknown_toml_field_is_rejected(tmp_path: Path) -> None:
    """Accepting a misspelled setting would silently discard operator intent."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("[agent]\nmax_rounds = 30\nunknown = true\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="agent.unknown"):
        load_settings(config_file, {}, {})


def test_missing_api_key_names_environment_variable_without_leaking_value(valid_settings) -> None:
    """A missing credential must identify its source without echoing any secret."""
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY") as error:
        resolve_api_key(valid_settings, {"ANTHROPIC_API_KEY": ""})

    assert "secret-sentinel" not in str(error.value)
