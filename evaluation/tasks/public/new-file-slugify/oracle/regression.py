"""Check that the pre-existing textkit suite still passes.

Oracle contract: argv[1] is the candidate workspace. Exit 0 means the target passed,
exit 1 means it failed, and any other exit code is reported as a harness oracle error.
This script always runs outside the agent workspace.
"""

import sys
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(workspace / "src"))
    import unittest

    suite = unittest.TestLoader().discover(
        str(workspace / "tests"), top_level_dir=str(workspace / "tests")
    )
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    if result.testsRun == 0:
        print("the pre-existing test suite disappeared")
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
