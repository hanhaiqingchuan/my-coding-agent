"""Roman numeral helpers for the numkit package."""


# (value, symbol) pairs ordered from largest to smallest so that each
# decimal digit is picked greedily using subtractive notation.
_ROMAN_NUMERALS = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)


def to_roman(number: int) -> str:
    """Return the Roman numeral for *number* using subtractive notation.

    The input must be an integer in the range 1 through 3999 inclusive.
    """
    if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number > 3999:
        raise ValueError("number must be between 1 and 3999")

    result: list = []
    for value, symbol in _ROMAN_NUMERALS:
        while number >= value:
            result.append(symbol)
            number -= value
    return "".join(result)