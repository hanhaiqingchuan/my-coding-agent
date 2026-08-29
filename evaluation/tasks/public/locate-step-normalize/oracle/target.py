"""Check that slashes are dropped and the rule lives in exactly one place.

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
        from pipeline.runner import run_step
        from pipeline.steps import normalize_step
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import the pipeline package: {error}")
        return 1

    for step, expected in [
        (" /Deploy /", "deploy"),
        ("//Build//", "build"),
        ("  deploy  ", "deploy"),
        ("a/b", "a/b"),
        ("deploy", "deploy"),
    ]:
        try:
            actual = normalize_step(step)
        except Exception as error:  # noqa: BLE001 - a raising helper is a task failure
            print(f"normalize_step({step!r}) raised {error}")
            return 1
        if actual != expected:
            print(f"normalize_step({step!r}) returned {actual!r}, expected {expected!r}")
            return 1

    if run_step(" /Deploy ") != "ran deploy":
        print(f"run_step(' /Deploy ') returned {run_step(' /Deploy ')!r}, expected 'ran deploy'")
        return 1

    runner_source = (workspace / "src" / "pipeline" / "runner.py").read_text(encoding="utf-8")
    if "normalize_step" not in runner_source:
        print("the runner still does not use normalize_step")
        return 1
    if ".strip().lower()" in runner_source:
        print("the runner still carries its own inline copy of the rule")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
