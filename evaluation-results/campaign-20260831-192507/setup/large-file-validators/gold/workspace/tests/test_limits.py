import unittest

from checks.limits import validate_00, validate_119, validate_42


class ValidatorTests(unittest.TestCase):
    def test_values_below_the_limit_are_accepted(self) -> None:
        self.assertTrue(validate_00(0))
        self.assertTrue(validate_00(9))
        self.assertTrue(validate_42(51))

    def test_the_limit_itself_is_rejected(self) -> None:
        self.assertFalse(validate_00(10))
        self.assertFalse(validate_42(52))
        self.assertFalse(validate_119(129))

    def test_negative_values_are_rejected(self) -> None:
        self.assertFalse(validate_119(-1))
        self.assertFalse(validate_42(-1))


if __name__ == "__main__":
    unittest.main()
