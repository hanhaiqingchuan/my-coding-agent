"""Check the repaired validator inside the large generated module.

Oracle contract: argv[1] is the candidate workspace. Exit 0 means the target passed,
exit 1 means it failed, and any other exit code is reported as a harness oracle error.
This script always runs outside the agent workspace.
"""

import sys
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(workspace / "src"))
    module = workspace / "src" / "checks" / "limits.py"
    line_count = len(module.read_text(encoding="utf-8").splitlines())
    if line_count < 600:
        print(f"limits.py shrank to {line_count} lines; the task needs a 600+ line file")
        return 1

    try:
        import checks.limits as limits
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import checks.limits: {error}")
        return 1

    for index in range(120):
        validator = getattr(limits, f"validate_{index:02d}", None)
        if validator is None:
            print(f"validate_{index:02d} disappeared from the module")
            return 1
        limit = 10 + index
        cases = [(limit - 1, True), (limit, False), (0, True), (-1, False)]
        for value, expected in cases:
            try:
                actual = validator(value)
            except Exception as error:  # noqa: BLE001 - a raising helper is a failure
                print(f"validate_{index:02d}({value}) raised {error}")
                return 1
            if actual is not expected:
                print(
                    f"validate_{index:02d}({value}) returned {actual}, expected {expected}"
                )
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
