"""Tolerant extraction of one JSON object from model-authored text.

Models wrap JSON in a Markdown fence or add surrounding prose even when told not
to, so every caller validating a JSON contract (compaction summary, judge
scores) tries the raw text, a fenced block, and the first balanced ``{...}``
span before giving up.
"""

from __future__ import annotations

import json
import re


def extract_json_object(text: str) -> object:
    """Return the first parseable JSON object in ``text``; raise ValueError otherwise."""
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced is not None:
        candidates.append(fenced.group(1))
    balanced = _first_balanced_object(text)
    if balanced is not None:
        candidates.append(balanced)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("the response is not JSON")


def _first_balanced_object(text: str) -> str | None:
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    return text[start : index + 1]
    return None


__all__ = ["extract_json_object"]
