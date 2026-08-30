import unittest

from strkit.naming import drop_prefix, with_suffix


class WithSuffixTests(unittest.TestCase):
    def test_appends_a_missing_suffix(self) -> None:
        self.assertEqual(with_suffix("report", ".md"), "report.md")

    def test_appends_suffix_to_an_empty_name(self) -> None:
        self.assertEqual(with_suffix("", ".md"), ".md")


class DropPrefixTests(unittest.TestCase):
    def test_removes_a_leading_prefix(self) -> None:
        self.assertEqual(drop_prefix("tmp-report", "tmp-"), "report")

    def test_keeps_a_name_without_the_prefix(self) -> None:
        self.assertEqual(drop_prefix("report", "tmp-"), "report")


if __name__ == "__main__":
    unittest.main()
