Add a new URL slug helper to the `textkit` package in this workspace.

Create the new file `src/textkit/slugify.py` containing a single public function:

```python
def slugify(text: str) -> str:
```

It must:

1. lowercase the text;
2. replace every run of characters that are not ASCII letters or digits with one `-`;
3. remove leading and trailing `-`;
4. return `""` for text that has no letters or digits.

For example `slugify("Hello, World!")` returns `"hello-world"` and
`slugify("  Multiple   Spaces  ")` returns `"multiple-spaces"`.

Do not change any existing file, and do not touch `README.md` or `tests/`.
You can run the existing suite with
`PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
