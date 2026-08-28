"""Check that the batch size moved to 200 and is declared once.

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
        from pipeline.batcher import batches
        from pipeline.settings import MAX_BATCH_SIZE
    except Exception as error:  # noqa: BLE001 - any import problem is a task failure
        print(f"cannot import the pipeline package: {error}")
        return 1

    if MAX_BATCH_SIZE != 200:
        print(f"MAX_BATCH_SIZE is {MAX_BATCH_SIZE}, expected 200")
        return 1

    source = (workspace / "src" / "pipeline" / "batcher.py").read_text(encoding="utf-8")
    if "MAX_BATCH_SIZE" not in source:
        print("the batch splitter still does not use MAX_BATCH_SIZE")
        return 1
    if "50" in source or "200" in source:
        print("the batch splitter still hard-codes a batch size literal")
        return 1

    produced = batches(list(range(500)))
    if [len(batch) for batch in produced] != [200, 200, 100]:
        print(f"unexpected batch sizes: {[len(batch) for batch in produced]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
