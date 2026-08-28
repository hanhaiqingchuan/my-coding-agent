from __future__ import annotations

from coding_agent.context.estimator import ESTIMATOR_ID, estimate_input_tokens
from coding_agent.core.models import TextPart, Usage
from coding_agent.model.protocol import ModelMessage


def test_estimate_counts_utf8_bytes_and_explicit_protocol_overheads() -> None:
    # Request + system fixed overhead is 8; two CJK characters are six UTF-8 bytes.
    assert estimate_input_tokens("", (), ()) == 8
    assert estimate_input_tokens("你好", (), ()) == 10

    # 8 top-level + 4 message + ceil(len("user") / 3) + 3 content block
    # + ceil(len(b'{"text":"abc","type":"text"}') / 3) = 27.
    message = ModelMessage("user", (TextPart("abc"),))
    assert estimate_input_tokens("", (message,), ()) == 27


def test_estimate_counts_complete_anthropic_input_schema_wire_json() -> None:
    schema = {
        "name": "read_file",
        "description": "读",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }

    # The compact, sorted wire JSON is 133 UTF-8 bytes. The total includes
    # 8 top-level tokens, 6 schema tokens, and ceil(133 / 3) byte tokens.
    assert estimate_input_tokens("", (), (schema,)) == 59


def test_estimate_is_deterministic_for_code_and_escaped_high_entropy_text() -> None:
    message = ModelMessage(
        "user",
        (TextPart('quote=" slash=\\ code={`x`: [1,2,3]}'),),
    )

    # The escaped content block is 62 wire bytes; this literal expectation is
    # intentionally independent of the estimator implementation.
    estimate = estimate_input_tokens("", (message,), ())
    assert estimate == 38

    # API usage is the calibration source, not an equality oracle: the byte
    # estimator is explicitly heuristic and may differ from recorded usage.
    recorded_usage = Usage(input_tokens=35)
    assert estimate != recorded_usage.input_tokens
    assert ESTIMATOR_ID == "utf8-bytes-over-3-v1"
