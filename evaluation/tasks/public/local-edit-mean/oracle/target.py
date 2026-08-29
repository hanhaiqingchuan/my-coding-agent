"""Check that mean rejects empty input with the exact ValueError.

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
        from statkit.stats import mean, median
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import statkit.stats: {error}")
        return 1

    for values, expected in [([2, 4], 3.0), ([7], 7.0), ([1, 2, 3, 4], 2.5)]:
        try:
            actual = mean(values)
        except Exception as error:  # noqa: BLE001 - a raising helper is a task failure
            print(f"mean({values}) raised {error}")
            return 1
        if actual != expected:
            print(f"mean({values}) returned {actual}, expected {expected}")
            return 1

    try:
        result = mean([])
    except ValueError as error:
        if str(error) != "values must not be empty":
            print(f"unexpected ValueError message: {error}")
            return 1
        if median([1, 2, 3]) != 2 or median([1, 2, 3, 4]) != 2.5:
            print("median changed while mean was edited")
            return 1
        return 0
    except Exception as error:  # noqa: BLE001 - the wrong exception type is a failure
        print(f"mean([]) raised {type(error).__name__} instead of ValueError")
        return 1
    print(f"mean([]) returned {result!r} instead of raising ValueError")
    return 1


if __name__ == "__main__":
    sys.exit(main())
