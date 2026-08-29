"""Check that numkit.roman implements the requested rules.

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
        from numkit.roman import to_roman
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import numkit.roman: {error}")
        return 1

    cases = [
        (1, "I"),
        (4, "IV"),
        (9, "IX"),
        (14, "XIV"),
        (40, "XL"),
        (58, "LVIII"),
        (90, "XC"),
        (400, "CD"),
        (900, "CM"),
        (1994, "MCMXCIV"),
        (3999, "MMMCMXCIX"),
    ]
    for number, expected in cases:
        try:
            actual = to_roman(number)
        except Exception as error:  # noqa: BLE001 - a raising helper is a task failure
            print(f"to_roman({number}) raised {error}")
            return 1
        if actual != expected:
            print(f"to_roman({number}) returned {actual!r}, expected {expected!r}")
            return 1

    for number in (0, -1, 4000):
        try:
            result = to_roman(number)
        except ValueError as error:
            if str(error) != "number must be between 1 and 3999":
                print(f"unexpected ValueError message: {error}")
                return 1
            continue
        except Exception as error:  # noqa: BLE001 - the wrong exception type fails
            print(f"to_roman({number}) raised {type(error).__name__} instead of ValueError")
            return 1
        print(f"to_roman({number}) returned {result!r} instead of raising ValueError")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
