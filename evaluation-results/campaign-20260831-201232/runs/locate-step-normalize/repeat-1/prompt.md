The step-name rule lives in two places in `src/pipeline/`, and both copies are
missing the same behaviour: surrounding `/` characters are not removed.

Make both changes:

1. `normalize_step` in `src/pipeline/steps.py` must also drop leading and trailing
   `/` characters, so `normalize_step(" /Deploy /")` returns `"deploy"`. Slashes
   between words must stay: `normalize_step("a/b")` returns `"a/b"`.
2. `run_step` in `src/pipeline/runner.py` must call `normalize_step` instead of its
   own inline copy of the rule, so the rule exists in exactly one place.

Search `src/pipeline/` to find both places. Do not change `README.md` or anything
under `tests/`. You can run the existing suite with
`PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
