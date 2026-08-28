"""Check that textkit.slugify implements the requested rules.

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
        from textkit.slugify import slugify
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import textkit.slugify: {error}")
        return 1

    cases = [
        ("Hello, World!", "hello-world"),
        ("  Multiple   Spaces  ", "multiple-spaces"),
        ("Already-slugged", "already-slugged"),
        ("A1 b2 C3", "a1-b2-c3"),
        ("--edges--", "edges"),
        ("!!!", ""),
        ("", ""),
    ]
    for text, expected in cases:
        try:
            actual = slugify(text)
        except Exception as error:  # noqa: BLE001 - a raising helper is a task failure
            print(f"slugify({text!r}) raised {error}")
            return 1
        if actual != expected:
            print(f"slugify({text!r}) returned {actual!r}, expected {expected!r}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
