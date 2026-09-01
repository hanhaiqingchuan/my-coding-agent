import unittest

from mathkit.ranges import clamp, midpoint


class ClampTests(unittest.TestCase):
    def test_value_inside_the_range_is_returned(self) -> None:
        self.assertEqual(clamp(5, 0, 10), 5)

    def test_value_below_the_range_is_raised_to_the_lower_bound(self) -> None:
        self.assertEqual(clamp(-3, 0, 10), 0)

    def test_value_above_the_range_is_lowered_to_the_upper_bound(self) -> None:
        self.assertEqual(clamp(42, 0, 10), 10)


class MidpointTests(unittest.TestCase):
    def test_midpoint_of_an_even_range(self) -> None:
        self.assertEqual(midpoint(0, 10), 5)


if __name__ == "__main__":
    unittest.main()
