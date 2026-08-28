"""Vendor-neutral model protocol and Anthropic Messages implementation."""

from coding_agent.model.protocol import (
    DeltaSink,
    ModelAPIError,
    ModelGateway,
    ModelMessage,
    ModelProtocolError,
    ModelRequest,
    ModelTransportError,
    TextDelta,
)

__all__ = [
    "DeltaSink",
    "ModelAPIError",
    "ModelGateway",
    "ModelMessage",
    "ModelProtocolError",
    "ModelRequest",
    "ModelTransportError",
    "TextDelta",
]
