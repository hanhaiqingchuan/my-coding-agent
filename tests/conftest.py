from __future__ import annotations

import os

import pytest

from coding_agent.config import AppSettings, load_settings


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: requires an explicitly enabled live model service")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        return

    skip_live = pytest.mark.skip(reason="set RUN_LIVE_TESTS=1 to run live tests")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def valid_settings() -> AppSettings:
    return load_settings(None, {}, {})
