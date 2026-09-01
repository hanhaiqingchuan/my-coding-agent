"""Roman numeral conversion."""

_VALUE_SYMBOLS = (
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
    """Return the standard Roman numeral for ``number``.

    Uses subtractive notation and accepts integers from 1 to 3999.
    """
    if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number > 3999:
        raise ValueError("number must be between 1 and 3999")

    result = []
    for value, symbol in _VALUE_SYMBOLS:
        count, number = divmod(number, value)
        result.append(symbol * count)
    return "".join(result)