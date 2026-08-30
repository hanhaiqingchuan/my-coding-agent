Add a word-frequency helper to the `wordkit` package in this workspace.

Create the new file `src/wordkit/counter.py` containing a single public function:

```python
def word_counts(text: str) -> dict[str, int]:
```

It must:

1. split the text on runs of whitespace;
2. count words case-insensitively (so `"Tea"` and `"tea"` are the same word);
3. return `{}` for text that has no words.

For example `word_counts("Tea tea TIME")` returns `{"tea": 2, "time": 1}` and
`word_counts("  ")` returns `{}`.

Do not change any existing file, and do not touch `README.md` or `tests/`.
You can run the existing suite with
`PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
