from __future__ import annotations

import pytest

from coding_agent.core.json_extract import extract_json_object


def test_raw_json_object_is_accepted() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_fenced_json_is_unwrapped() -> None:
    text = 'Here you go:\n```json\n{"a": {"b": 2}}\n```\nDone.'
    assert extract_json_object(text) == {"a": {"b": 2}}


def test_prose_wrapped_json_uses_the_first_balanced_object() -> None:
    text = 'Sure! {"a": "with } brace and { inside"} — hope this helps.'
    assert extract_json_object(text) == {"a": "with } brace and { inside"}


def test_escaped_quotes_do_not_end_strings_early() -> None:
    text = '{"a": "he said \\"no\\" }"} trailing'
    assert extract_json_object(text) == {"a": 'he said "no" }'}


def test_non_object_json_is_returned_for_caller_validation() -> None:
    assert extract_json_object("[1, 2]") == [1, 2]


def test_garbage_raises_value_error() -> None:
    with pytest.raises(ValueError):
        extract_json_object("not json at all")
