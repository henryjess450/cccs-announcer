"""Integer -> spoken-English helpers.

Kept separate from normalize.py so the number logic can be tested on its own.
Everything here is pure: no I/O, no config, no state.
"""

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

# Words that change shape when they become ordinals. Everything else just takes "th".
_ORDINAL_IRREGULAR = {
    "one": "first", "two": "second", "three": "third", "five": "fifth",
    "eight": "eighth", "nine": "ninth", "twelve": "twelfth",
}


def number_to_words(n: int) -> str:
    """123 -> 'one hundred twenty three'. Handles negatives and very large values."""
    if n < 0:
        return "minus " + number_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens = _TENS[n // 10]
        return tens if n % 10 == 0 else f"{tens} {_ONES[n % 10]}"
    if n < 1000:
        head = f"{_ONES[n // 100]} hundred"
        return head if n % 100 == 0 else f"{head} {number_to_words(n % 100)}"
    for divisor, label in ((1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand")):
        if n >= divisor:
            head = f"{number_to_words(n // divisor)} {label}"
            rest = n % divisor
            return head if rest == 0 else f"{head} {number_to_words(rest)}"
    # Unreachable for ints; digit-spell anything pathological rather than crash mid-announcement.
    return digits_to_words(str(n))


def digits_to_words(digits: str) -> str:
    """'204' -> 'two oh four'. Used for room numbers, phone numbers, long ID-like runs.

    Zero is spoken as 'oh' here, which is how people read room and bus numbers aloud.
    """
    out = []
    for ch in digits:
        if ch.isdigit():
            out.append("oh" if ch == "0" else _ONES[int(ch)])
        elif ch.isalpha():
            out.append(ch.lower())
    return " ".join(out)


def ordinal_to_words(n: int) -> str:
    """3 -> 'third', 21 -> 'twenty first'."""
    words = number_to_words(n).split(" ")
    last = words[-1]
    if last in _ORDINAL_IRREGULAR:
        words[-1] = _ORDINAL_IRREGULAR[last]
    elif last.endswith("y"):
        words[-1] = last[:-1] + "ieth"
    else:
        words[-1] = last + "th"
    return " ".join(words)


def decimal_to_words(whole: str, frac: str) -> str:
    """'3', '25' -> 'three point two five'. Digits after the point are spoken as 'zero', not 'oh'."""
    spoken_frac = " ".join("zero" if c == "0" else _ONES[int(c)] for c in frac if c.isdigit())
    return f"{number_to_words(int(whole))} point {spoken_frac}"
