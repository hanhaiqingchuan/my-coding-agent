The fetch timeout is 30 seconds today, and the number lives in two places:
`src/fetch/config.py` declares `TIMEOUT_SECONDS = 30`, while `src/fetch/client.py`
carries its own copy of the same constant instead of importing it.

The timeout must become 45 seconds and live in exactly one place. Make both
coordinated edits:

1. in `src/fetch/config.py`, rename the constant to `REQUEST_TIMEOUT` and raise its
   value to `45`;
2. in `src/fetch/client.py`, delete the local copy and import `REQUEST_TIMEOUT` from
   the config module, so `fetch("...")` reports the timeout taken from config.

Search `src/fetch/` to find both places. Afterwards `fetch.config` must export
`REQUEST_TIMEOUT` and neither module may define `TIMEOUT_SECONDS` any more.

Do not change `README.md` or anything under `tests/`. You can run the existing
suite with `PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
