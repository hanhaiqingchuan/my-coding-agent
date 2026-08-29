"""Check that with_suffix avoids doubling the suffix, case-sensitively.

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
        from strkit.naming import drop_prefix, with_suffix
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import strkit.naming: {error}")
        return 1

    cases = [
        ("report", ".md", "report.md"),
        ("report.md", ".md", "report.md"),
        ("", ".md", ".md"),
        ("archive", "", "archive"),
        ("DATA.CSV", ".csv", "DATA.CSV.csv"),
        ("photo.jpeg", ".jpeg", "photo.jpeg"),
    ]
    for name, suffix, expected in cases:
        try:
            actual = with_suffix(name, suffix)
        except Exception as error:  # noqa: BLE001 - a raising helper is a task failure
            print(f"with_suffix({name!r}, {suffix!r}) raised {error}")
            return 1
        if actual != expected:
            print(f"with_suffix({name!r}, {suffix!r}) returned {actual!r}, expected {expected!r}")
            return 1

    if drop_prefix("tmp-report", "tmp-") != "report":
        print("drop_prefix changed while with_suffix was edited")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
