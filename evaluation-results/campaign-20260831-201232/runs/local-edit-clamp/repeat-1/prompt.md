`clamp` in `src/mathkit/ranges.py` silently returns nonsense when it is called with
inverted bounds: `clamp(5, 10, 0)` currently returns `10`.

Make `clamp` raise `ValueError("lower must not exceed upper")` when `lower > upper`,
before any clamping happens. Keep the change local to `clamp`: it should be one to five
new lines, and every existing behaviour must stay the same.

Do not change `midpoint`, `README.md`, or anything under `tests/`. You can run the
existing suite with `PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
