The ingest pipeline must move from a batch size of 50 to a batch size of 200.

The size is currently declared in one file and duplicated as a literal in another, so
raising it needs two coordinated edits:

1. `MAX_BATCH_SIZE` must become `200`;
2. the batch splitter must import and use `MAX_BATCH_SIZE` instead of its own literal,
   so the size lives in exactly one place.

Search `src/pipeline/` to find both places. Afterwards no module under `src/pipeline/`
except the settings module may contain the batch size as a literal number.

Do not change `README.md` or anything under `tests/`. You can run the existing suite
with `PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
