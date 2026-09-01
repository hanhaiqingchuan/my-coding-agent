Add a Roman numeral helper to the `numkit` package in this workspace.

Create the new file `src/numkit/roman.py` containing a single public function:

```python
def to_roman(number: int) -> str:
```

It must:

1. return the standard Roman numeral for integers from 1 to 3999, using the
   subtractive notation (`9` is `"IX"`, `40` is `"XL"`, `900` is `"CM"`);
2. raise `ValueError("number must be between 1 and 3999")` for any integer
   outside that range.

For example `to_roman(9)` returns `"IX"`, `to_roman(58)` returns `"LVIII"` and
`to_roman(1994)` returns `"MCMXCIV"`.

Do not change any existing file, and do not touch `README.md` or `tests/`.
You can run the existing suite with
`PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
