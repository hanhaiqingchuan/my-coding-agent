import unittest

from numkit.parity import is_even, is_odd


class ParityTests(unittest.TestCase):
    def test_even_numbers(self) -> None:
        self.assertTrue(is_even(0))
        self.assertTrue(is_even(2))
        self.assertTrue(is_even(-4))

    def test_odd_numbers(self) -> None:
        self.assertTrue(is_odd(1))
        self.assertTrue(is_odd(-3))

    def test_even_and_odd_are_complementary(self) -> None:
        self.assertNotEqual(is_even(7), is_odd(7))


if __name__ == "__main__":
    unittest.main()
