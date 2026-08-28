"""Check that clamp rejects inverted bounds with ValueError.

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
        from mathkit.ranges import clamp
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import mathkit.ranges: {error}")
        return 1

    for value, lower, upper, expected in [(5, 0, 10, 5), (-3, 0, 10, 0), (42, 0, 10, 10)]:
        try:
            actual = clamp(value, lower, upper)
        except Exception as error:  # noqa: BLE001 - a raising helper is a task failure
            print(f"clamp({value}, {lower}, {upper}) raised {error}")
            return 1
        if actual != expected:
            print(f"clamp({value}, {lower}, {upper}) returned {actual}, expected {expected}")
            return 1

    try:
        result = clamp(5, 10, 0)
    except ValueError as error:
        if str(error) != "lower must not exceed upper":
            print(f"unexpected ValueError message: {error}")
            return 1
        return 0
    except Exception as error:  # noqa: BLE001 - the wrong exception type is a task failure
        print(f"clamp(5, 10, 0) raised {type(error).__name__} instead of ValueError")
        return 1
    print(f"clamp(5, 10, 0) returned {result} instead of raising ValueError")
    return 1


if __name__ == "__main__":
    sys.exit(main())
