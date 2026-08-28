"""Small request-metrics helpers shared by runtime model call sites."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from coding_agent.config import ModelSettings
from coding_agent.model.protocol import ModelRequest


def model_config_hash(settings: ModelSettings, request: ModelRequest) -> str:
    """Hash only non-secret model configuration using canonical JSON."""
    tool_names = tuple(
        name for tool in request.tools if isinstance((name := tool.get("name")), str)
    )
    value = {
        "settings": asdict(settings),
        "request": {"max_tokens": request.max_tokens, "tool_names": tool_names},
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["model_config_hash"]
