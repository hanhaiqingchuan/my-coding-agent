`src/router/table.py` holds one generated route helper per route name. Every
helper returns its own canonical path, `"/v1/routes/<id>"`:

```python
def route_path_57() -> str:
    """Return the canonical path for route_57."""
    prefix = "/v1/routes/"
    return prefix + "57"
```

`route_path_57` was registered with the wrong id: it returns
`"/v1/routes/999"` instead of `"/v1/routes/57"`.

Bring `route_path_57` in line with its neighbours so that
`path_for("route_57")` returns `"/v1/routes/57"`.

Change only that helper. Leave every other helper, the `ROUTES` table,
`path_for`, `README.md` and everything under `tests/` untouched. You can run
the existing suite with
`PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
