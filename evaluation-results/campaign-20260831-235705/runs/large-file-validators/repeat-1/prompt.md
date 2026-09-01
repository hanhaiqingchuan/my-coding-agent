`src/checks/limits.py` holds one generated validator per entry. Every validator
accepts values in `[0, limit)`, where the limit grows with the entry index:

```python
def validate_77(value: int) -> bool:
    """Return whether value fits the limit for entry 77."""
    limit = 87
    return 0 <= value < limit
```

`validate_77` has an off-by-one: it accepts its own limit (`<=` instead of `<`),
so `validate_77(87)` wrongly returns `True`.

Fix the comparison in `validate_77` so that its limit itself is rejected, exactly
like its 119 neighbours.

Change only `validate_77`. Leave every other validator, `README.md` and
everything under `tests/` untouched. You can run the existing suite with
`PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
