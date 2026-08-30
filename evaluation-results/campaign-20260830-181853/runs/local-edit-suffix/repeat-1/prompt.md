`with_suffix` in `src/strkit/naming.py` doubles the suffix when the name already
ends with it: `with_suffix("report.md", ".md")` currently returns `"report.md.md"`.

Make `with_suffix` return the name unchanged when it already ends with the suffix.
The comparison must stay case-sensitive: `with_suffix("DATA.CSV", ".csv")` must
return `"DATA.CSV.csv"` because `.csv` is not a suffix of `"DATA.CSV"`. Keep the
change local to `with_suffix`: it should be one to three new lines, and
`drop_prefix` must stay untouched.

Do not change `README.md` or anything under `tests/`. You can run the existing
suite with `PYTHONPATH=src python3 -B -m unittest discover -s tests -t tests`.
