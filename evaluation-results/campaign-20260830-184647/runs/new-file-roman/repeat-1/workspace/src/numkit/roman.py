"""Roman numeral helpers."""


def to_roman(number: int) -> str:
    """Return the Roman numeral for an integer from 1 to 3999.

    Uses standard subtractive notation: 9 is "IX", 40 is "XL", 900 is "CM".
    """
    if not isinstance(number, int) or isinstance(number, bool):
        raise ValueError("number must be between 1 and 3999")
    if number < 1 or number > 3999:
        raise ValueError("number must be between 1 and 3999")

    values = [
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
    ]

    result = []
    for value, numeral in values:
        while number >= value:
            result.append(numeral)
            number -= value
    return "".join(result)