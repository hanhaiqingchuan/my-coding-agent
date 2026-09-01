`mean` in `src/statkit/stats.py` crashes with a `ZeroDivisionError` when it is
called with an empty list.

Make `mean` raise `ValueError("values must not be empty")` when `values` is empty,
before any arithmetic happens. Keep the change local to `mean`: it should be one to
three new lines, and every existing behaviour (including `median`) must stay the same.

Do not change `median`, `README.md`, or anything under `tests/`. You can run the
existing suite with `PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
