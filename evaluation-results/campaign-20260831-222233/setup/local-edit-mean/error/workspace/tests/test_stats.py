import unittest

from statkit.stats import mean, median


class MeanTests(unittest.TestCase):
    def test_mean_of_two_values(self) -> None:
        self.assertEqual(mean([2, 4]), 3)

    def test_mean_of_one_value(self) -> None:
        self.assertEqual(mean([7]), 7)


class MedianTests(unittest.TestCase):
    def test_median_of_odd_length_list(self) -> None:
        self.assertEqual(median([1, 2, 3]), 2)

    def test_median_of_even_length_list(self) -> None:
        self.assertEqual(median([1, 2, 3, 4]), 2.5)

    def test_median_sorts_first(self) -> None:
        self.assertEqual(median([5, 1, 3]), 3)


if __name__ == "__main__":
    unittest.main()
