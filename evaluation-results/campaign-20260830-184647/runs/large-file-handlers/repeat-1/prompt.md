`src/dispatch/handlers.py` holds one generated handler per event name. Every handler
returns the same envelope:

```python
{"event": "<event name>", "status": "ok", "value": <payload doubled>}
```

`handle_event_42` was left unfinished: it returns `"status": "todo"` and `"value": None`.

Bring `handle_event_42` in line with its neighbours so that
`dispatch("event_42", 21)` returns `{"event": "event_42", "status": "ok", "value": 42}`.

Change only that handler. Leave every other handler, the `HANDLERS` table, `dispatch`,
`README.md` and everything under `tests/` untouched. You can run the existing suite with
`PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
