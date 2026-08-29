"""Check the repaired route inside the large generated module.

Oracle contract: argv[1] is the candidate workspace. Exit 0 means the target passed,
exit 1 means it failed, and any other exit code is reported as a harness oracle error.
This script always runs outside the agent workspace.
"""

import sys
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(workspace / "src"))
    module = workspace / "src" / "router" / "table.py"
    line_count = len(module.read_text(encoding="utf-8").splitlines())
    if line_count < 600:
        print(f"table.py shrank to {line_count} lines; the task needs a 600+ line file")
        return 1

    try:
        from router.table import ROUTES, path_for
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import router.table: {error}")
        return 1

    if len(ROUTES) != 100:
        print(f"the route table holds {len(ROUTES)} entries, expected 100")
        return 1

    for index in range(100):
        name = f"route_{index:02d}"
        try:
            actual = path_for(name)
        except Exception as error:  # noqa: BLE001 - a raising helper is a task failure
            print(f"path_for({name!r}) raised {error}")
            return 1
        expected = f"/v1/routes/{index:02d}"
        if actual != expected:
            print(f"path_for({name!r}) returned {actual!r}, expected {expected!r}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
