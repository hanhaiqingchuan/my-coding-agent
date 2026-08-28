"""Check the repaired handler inside the large generated module.

Oracle contract: argv[1] is the candidate workspace. Exit 0 means the target passed,
exit 1 means it failed, and any other exit code is reported as a harness oracle error.
This script always runs outside the agent workspace.
"""

import sys
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(workspace / "src"))
    module = workspace / "src" / "dispatch" / "handlers.py"
    line_count = len(module.read_text(encoding="utf-8").splitlines())
    if line_count < 600:
        print(f"handlers.py shrank to {line_count} lines; the task needs a 600+ line file")
        return 1

    try:
        from dispatch.handlers import HANDLERS, dispatch
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import dispatch.handlers: {error}")
        return 1

    if len(HANDLERS) != 80:
        print(f"the handler table holds {len(HANDLERS)} entries, expected 80")
        return 1

    try:
        actual = dispatch("event_42", 21)
    except Exception as error:  # noqa: BLE001 - a raising handler is a task failure
        print(f"dispatch('event_42', 21) raised {error}")
        return 1
    expected = {"event": "event_42", "status": "ok", "value": 42}
    if actual != expected:
        print(f"dispatch('event_42', 21) returned {actual}, expected {expected}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
