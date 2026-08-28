"""Collection-time gate and fixtures for the real-model smoke.

The whole directory is marked ``live`` and skipped here, so a new module in this package
cannot forget the marker and silently start calling a model. The gate is applied in this
conftest rather than relying on the root conftest's marker sweep, so it holds regardless of
plugin ordering. Everything else the live tests need lives in ``tests/live/harness.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.live.harness import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    LiveModel,
    LiveRunner,
    model_from_environment,
    timeout_from_environment,
)

_LIVE_DIRECTORY = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every item under ``tests/live`` and skip it unless the switch is set to ``1``."""
    del config
    enabled = os.environ.get("RUN_LIVE_TESTS") == "1"
    live = pytest.mark.live
    skip_live = pytest.mark.skip(reason="set RUN_LIVE_TESTS=1 to run real-model tests")
    for item in items:
        if _LIVE_DIRECTORY != item.path.parent and _LIVE_DIRECTORY not in item.path.parents:
            continue
        item.add_marker(live)
        if not enabled:
            item.add_marker(skip_live)


@pytest.fixture
def primary_model() -> LiveModel:
    """The main configuration under test, taken from the environment only."""
    return model_from_environment(
        "primary",
        model_variable="LIVE_MODEL",
        base_url_variable="LIVE_BASE_URL",
        key_env_variable="LIVE_API_KEY_ENV",
        default_base_url=DEFAULT_BASE_URL,
        default_key_env=DEFAULT_API_KEY_ENV,
    )


@pytest.fixture
def alternate_model(primary_model: LiveModel) -> LiveModel:
    """A genuinely different service or model, required by section 17.4 scenario 4."""
    alternate = model_from_environment(
        "alternate",
        model_variable="LIVE_ALT_MODEL",
        base_url_variable="LIVE_ALT_BASE_URL",
        key_env_variable="LIVE_ALT_API_KEY_ENV",
        default_base_url=primary_model.base_url,
        default_key_env=primary_model.api_key_env,
    )
    if (alternate.base_url, alternate.model) == (primary_model.base_url, primary_model.model):
        pytest.skip("LIVE_ALT_MODEL or LIVE_ALT_BASE_URL must differ from the primary values")
    return alternate


@pytest.fixture
def live_runner(tmp_path: Path) -> LiveRunner:
    """A runner rooted in this test's temporary directory, never inside the repository."""
    return LiveRunner(tmp_path / "live", timeout_from_environment())
