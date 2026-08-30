import unittest

from textkit.normalize import squeeze_spaces


class SqueezeSpacesTests(unittest.TestCase):
    def test_collapses_runs_of_whitespace(self) -> None:
        self.assertEqual(squeeze_spaces("a   b\tc"), "a b c")

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(squeeze_spaces("  padded  "), "padded")

    def test_empty_text_stays_empty(self) -> None:
        self.assertEqual(squeeze_spaces("   "), "")


if __name__ == "__main__":
    unittest.main()
