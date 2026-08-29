"""Check that wordkit.counter implements the requested rules.

Oracle contract: argv[1] is the candidate workspace. Exit 0 means the target passed,
exit 1 means it failed, and any other exit code is reported as a harness oracle error.
This script always runs outside the agent workspace.
"""

import sys
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(workspace / "src"))
    try:
        from wordkit.counter import word_counts
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import wordkit.counter: {error}")
        return 1

    cases: list[tuple[str, dict[str, int]]] = [
        ("Tea tea TIME", {"tea": 2, "time": 1}),
        ("  Multiple   spaces  ", {"multiple": 1, "spaces": 1}),
        ("one", {"one": 1}),
        ("   ", {}),
        ("", {}),
        ("a A a", {"a": 3}),
    ]
    for text, expected in cases:
        try:
            actual = word_counts(text)
        except Exception as error:  # noqa: BLE001 - a raising helper is a task failure
            print(f"word_counts({text!r}) raised {error}")
            return 1
        if actual != expected:
            print(f"word_counts({text!r}) returned {actual!r}, expected {expected!r}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
