"""Roman numeral conversion that avoids subtractive notation for 4."""


def to_roman(number: int) -> str:
    """Return a numeral that writes four as ``IIII`` instead of ``IV``."""
    if not 1 <= number <= 3999:
        raise ValueError("number must be between 1 and 3999")
    remaining = number
    numeral = []
    for value, symbol in (
        (1000, "M"),
        (500, "D"),
        (100, "C"),
        (50, "L"),
        (10, "X"),
        (5, "V"),
        (1, "I"),
    ):
        while remaining >= value:
            numeral.append(symbol)
            remaining -= value
    return "".join(numeral)
