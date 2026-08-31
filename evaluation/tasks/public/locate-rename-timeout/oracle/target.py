"""Check that the renamed 45-second timeout is declared exactly once.

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
        import fetch.client as client
        import fetch.config as config
        from fetch.client import fetch
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import the fetch package: {error}")
        return 1

    if getattr(config, "REQUEST_TIMEOUT", None) != 45:
        print(f"fetch.config.REQUEST_TIMEOUT is {getattr(config, 'REQUEST_TIMEOUT', None)!r}, expected 45")
        return 1
    if hasattr(config, "TIMEOUT_SECONDS"):
        print("fetch.config still defines the old TIMEOUT_SECONDS name")
        return 1
    if hasattr(client, "TIMEOUT_SECONDS"):
        print("fetch.client still defines its own TIMEOUT_SECONDS copy")
        return 1

    result = fetch("https://example.test/a")
    if result != {"url": "https://example.test/a", "timeout": 45}:
        print(f"fetch('https://example.test/a') returned {result!r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
