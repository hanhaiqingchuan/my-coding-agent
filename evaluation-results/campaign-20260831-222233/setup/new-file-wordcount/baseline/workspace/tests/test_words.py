import unittest

from wordkit.words import split_words


class SplitWordsTests(unittest.TestCase):
    def test_splits_on_every_whitespace_run(self) -> None:
        self.assertEqual(split_words("a  b\tc\nd"), ["a", "b", "c", "d"])

    def test_whitespace_only_text_yields_no_words(self) -> None:
        self.assertEqual(split_words("   \t\n"), [])

    def test_words_keep_their_case(self) -> None:
        self.assertEqual(split_words("Tea TIME"), ["Tea", "TIME"])


if __name__ == "__main__":
    unittest.main()
