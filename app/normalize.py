"""Turn what a staff member typed into what the TTS engine should actually say.

This module is the single source of truth for text handling. It is pure and
synchronous so it can be unit-tested exhaustively without a database, an audio
device, or a TTS engine.

Design notes for whoever maintains this next:

* The pipeline is an ORDERED list of small steps and the order matters a lot.
  Times are expanded before generic numbers, otherwise "2:15" turns into
  "two:fifteen" and then into something worse.

* Pronunciation-dictionary hits are pulled OUT of the text before the expansion
  rules run and spliced back in afterwards (see `_split_protected`). An earlier
  version used inline placeholder markers and the generic number rule happily
  expanded the digits inside the markers -- "CCCS" came out as "zero". Do not
  reintroduce inline markers.

* Some abbreviations are deliberately NOT expanded because they are ambiguous
  and guessing wrong sounds worse than not trying at all. See _AMBIGUOUS.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .numbers import decimal_to_words, digits_to_words, number_to_words, ordinal_to_words

# --------------------------------------------------------------------------
# Seed pronunciation dictionary.
#
# These are the built-in defaults. From Phase 3 the admin-editable table in the
# database layers ON TOP of this dict (DB wins on key collision), so a school
# can fix a surname without a code change. Keys match on word boundaries,
# case-insensitively, longest key first.
# --------------------------------------------------------------------------
SEED_PRONUNCIATIONS: Dict[str, str] = {
    # The school itself, spoken as individual letters.
    "CCCS": "C C C S",
    # School acronyms that TTS engines otherwise read as nonsense words.
    "PTA": "P T A",
    "IEP": "I E P",
    "SRO": "S R O",
    "EA": "E A",
    "AV": "A V",
    "PD": "P D",
}

# Abbreviations we expand. Matched case-insensitively on word boundaries, with
# an optional trailing period.
_ABBREVIATIONS: Dict[str, str] = {
    "mr": "mister",
    "mrs": "missus",
    "ms": "miz",
    "mx": "mix",
    "dr": "doctor",       # School context: far more likely a person than a street.
    "prof": "professor",
    "sr": "senior",
    "jr": "junior",
    "dept": "department",
    "approx": "approximately",
    "asap": "as soon as possible",
    "etc": "etcetera",
    "vs": "versus",
}

# Deliberately NOT expanded -- documented so nobody "fixes" it later:
#   St.   Saint or Street?
#   Ave.  reads fine as-is in most voices
#   min / hr / sec   minutes or minimum, hour or human resources
_AMBIGUOUS = ("St.", "Ave.", "min", "hr", "sec")

# Markup / entity stripping. We strip rather than reject so a stray "<" cannot
# block an announcement during a fire drill.
_MARKUP_RE = re.compile(r"<[^>]{0,200}>")
_ENTITY_RE = re.compile(r"&(?:#\d{1,6}|#x[0-9a-fA-F]{1,6}|[a-zA-Z]{2,10});")

# "p.m." / "pm" / "PM." -- written as an alternation rather than optional dots,
# because `([ap])\.?m\.?` greedily swallows the sentence-ending period in
# "... at 12:45 PM." and the announcement loses its full stop.
_MERIDIEM = r"(?:([apAP])\.[mM]\.|([apAP])\.?[mM]\b)"


@dataclass
class NormalizationResult:
    raw: str
    normalized: str
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Cleaning steps (run on the whole string, before dictionary protection)
# --------------------------------------------------------------------------

def strip_control_characters(text: str) -> Tuple[str, List[str]]:
    """Remove anything non-printable.

    Also normalises Unicode to NFKC so smart quotes, non-breaking spaces and
    full-width digits collapse to plain ASCII equivalents.
    """
    text = unicodedata.normalize("NFKC", text)
    cleaned: List[str] = []
    removed = False
    for ch in text:
        if ch in "\n\r\t":
            cleaned.append(" ")
            continue
        if unicodedata.category(ch)[0] == "C":  # Cc, Cf, Co, Cs, Cn
            removed = True
            continue
        cleaned.append(ch)
    return "".join(cleaned), (["Some invisible characters were removed."] if removed else [])


def strip_markup(text: str) -> Tuple[str, List[str]]:
    """Remove SSML/HTML-ish tags and character entities.

    This is the injection guard. Piper does not interpret SSML, but a future
    cloud engine might, and an announcement must never be able to smuggle markup
    or instructions into a synthesis request.
    """
    warnings: List[str] = []
    if _MARKUP_RE.search(text) or _ENTITY_RE.search(text):
        warnings.append("Text that looked like code or markup was removed.")
    text = _MARKUP_RE.sub(" ", text)
    text = _ENTITY_RE.sub(" ", text)
    text = text.replace("<", " ").replace(">", " ")
    return text, warnings


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def collapse_repeated_punctuation(text: str) -> str:
    """'Hello!!!!' -> 'Hello!'  and  'wait......' -> 'wait...'

    Ellipses survive (capped at three dots) because they produce a useful pause.
    """
    text = re.sub(r"\.{3,}", "...", text)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)
    text = re.sub(r"[!?]{2,}", "!", text)
    text = re.sub(r"([,;:-])\1+", r"\1", text)
    # Dashes used as pauses read better as commas.
    text = re.sub(r"\s+[–—-]{1,2}\s+", ", ", text)
    return text


# --------------------------------------------------------------------------
# Pronunciation dictionary protection
# --------------------------------------------------------------------------

def _split_protected(text: str, dictionary: Dict[str, str]) -> List[Tuple[bool, str]]:
    """Split into [(is_fixed, chunk), ...].

    Chunks flagged is_fixed are dictionary replacements and must be passed
    through the rest of the pipeline untouched.
    """
    if not dictionary:
        return [(False, text)]
    keys = sorted(dictionary, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![\w-])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w-])",
        re.IGNORECASE,
    )
    lowered = {k.lower(): v for k, v in dictionary.items()}
    parts = pattern.split(text)
    segments: List[Tuple[bool, str]] = []
    for index, part in enumerate(parts):
        if index % 2 == 0:
            segments.append((False, part))
        else:
            segments.append((True, lowered[part.lower()]))
    return segments


# --------------------------------------------------------------------------
# Expansion steps (run only on unprotected chunks)
# --------------------------------------------------------------------------

_TIME_MERIDIEM_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::\d{2})?\s*" + _MERIDIEM)
_TIME_BARE_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
_HOUR_MERIDIEM_RE = re.compile(r"(?<![\d:.])(\d{1,2})\s*" + _MERIDIEM)


def _spoken_time(hour: int, minute: int, meridiem: str = "") -> str:
    """Render an hour/minute pair the way a person reads a clock aloud."""
    display_hour = hour if hour <= 12 else hour - 12
    if display_hour == 0:
        display_hour = 12
    if minute == 0:
        head = number_to_words(display_hour)
        # "eight a m" reads better than "eight o'clock a m".
        return f"{head} {meridiem}" if meridiem else f"{head} o'clock"
    tail = f"oh {number_to_words(minute)}" if minute < 10 else number_to_words(minute)
    return f"{number_to_words(display_hour)} {tail} {meridiem}".strip()


def _meridiem_words(match: "re.Match[str]", first: int) -> str:
    letter = match.group(first) or match.group(first + 1)
    return f"{letter.lower()} m"


def expand_times(text: str) -> str:
    def _with_meridiem(m: "re.Match[str]") -> str:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            return m.group(0)
        return _spoken_time(hour, minute, _meridiem_words(m, 3))

    def _bare(m: "re.Match[str]") -> str:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            return m.group(0)
        return _spoken_time(hour, minute)

    def _hour_only(m: "re.Match[str]") -> str:
        hour = int(m.group(1))
        if hour > 23:
            return m.group(0)
        return _spoken_time(hour, 0, _meridiem_words(m, 2))

    text = _TIME_MERIDIEM_RE.sub(_with_meridiem, text)
    text = _TIME_BARE_RE.sub(_bare, text)
    text = _HOUR_MERIDIEM_RE.sub(_hour_only, text)
    return text


_ROOM_RE = re.compile(r"\b(?:room|rm)\.?\s*#?\s*(\d{1,4}[A-Za-z]?)\b", re.IGNORECASE)
_BUS_RE = re.compile(r"\bbus(?:\s*(?:number|no\.?|#))?\s*#?\s*(\d{1,4})\b", re.IGNORECASE)
_LOCKER_RE = re.compile(r"\blocker\s*#?\s*(\d{1,4})\b", re.IGNORECASE)
_EXT_RE = re.compile(r"\b(?:ext|extension|x)\.?\s*#?\s*(\d{2,5})\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:(\d{3})[-.\s])?(\d{3})[-.](\d{4})\b")
_RANGE_RE = re.compile(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b")


def expand_room_numbers(text: str) -> str:
    """'Rm 204' -> 'room two oh four'; 'Rm 12B' -> 'room twelve b'.

    Three- and four-digit rooms are read digit by digit, which is how a wing +
    room number is actually said. One- and two-digit rooms read as a quantity,
    because "room one two" sounds wrong where "room twelve" does not.
    """
    def _sub(m: "re.Match[str]") -> str:
        token = m.group(1)
        digits = "".join(c for c in token if c.isdigit())
        suffix = "".join(c for c in token if c.isalpha()).lower()
        if len(digits) <= 2:
            spoken = number_to_words(int(digits))
        else:
            spoken = digits_to_words(digits)
        return ("room " + spoken + " " + suffix).strip()
    return _ROOM_RE.sub(_sub, text)


def expand_bus_numbers(text: str) -> str:
    """'Bus 12' -> 'bus number twelve'; 'Bus 147' -> 'bus number one four seven'.

    Two-digit routes read as a number, longer ones digit by digit -- that is how
    bus routes are actually called out.
    """
    def _sub(m: "re.Match[str]") -> str:
        digits = m.group(1)
        compact = digits.lstrip("0") or "0"
        spoken = number_to_words(int(digits)) if len(compact) <= 2 else digits_to_words(digits)
        return f"bus number {spoken}"
    return _BUS_RE.sub(_sub, text)


def expand_locker_numbers(text: str) -> str:
    return _LOCKER_RE.sub(lambda m: "locker " + digits_to_words(m.group(1)), text)


def expand_extensions(text: str) -> str:
    return _EXT_RE.sub(lambda m: "extension " + digits_to_words(m.group(1)), text)


def expand_phone_numbers(text: str) -> str:
    """Phone numbers are read digit by digit, never as quantities."""
    def _sub(m: "re.Match[str]") -> str:
        groups = [g for g in m.groups() if g]
        return " ".join(digits_to_words(g) for g in groups)
    return _PHONE_RE.sub(_sub, text)


def expand_ranges(text: str) -> str:
    """'8-10' -> 'eight to ten'. Only for short numbers, so it cannot eat a phone number."""
    return _RANGE_RE.sub(lambda m: f"{m.group(1)} to {m.group(2)}", text)


_ORDINAL_RE = re.compile(r"\b(\d{1,4})(?:st|nd|rd|th)\b", re.IGNORECASE)


def expand_ordinals(text: str) -> str:
    return _ORDINAL_RE.sub(lambda m: ordinal_to_words(int(m.group(1))), text)


_SYMBOLS: List[Tuple[str, str]] = [
    (r"\bw/o", " without "),
    (r"\bw/", " with "),
    (r"&", " and "),
    (r"@", " at "),
    (r"%", " percent "),
    (r"\+", " plus "),
    (r"#", " number "),
    (r"\*", " "),
    (r"[_`~^|\\{}\[\]]", " "),
]


def expand_abbreviations(text: str) -> str:
    for pattern, replacement in _SYMBOLS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    keys = sorted(_ABBREVIATIONS, key=len, reverse=True)
    joined = "|".join(keys)
    pattern = re.compile(r"\b(" + joined + r")\.(?=\s|$)|\b(" + joined + r")\b", re.IGNORECASE)

    def _sub(m: "re.Match[str]") -> str:
        return _ABBREVIATIONS[(m.group(1) or m.group(2)).lower()]
    return pattern.sub(_sub, text)


_DECIMAL_RE = re.compile(r"\b(\d+)\.(\d+)\b")
_LONG_RUN_RE = re.compile(r"\b\d{5,}\b")
_INT_RE = re.compile(r"\d+")


def expand_numbers(text: str) -> str:
    """Expand whatever digits the earlier, more specific rules did not claim."""
    text = _DECIMAL_RE.sub(lambda m: decimal_to_words(m.group(1), m.group(2)), text)
    # Long digit runs (student IDs, account numbers) read better one at a time.
    text = _LONG_RUN_RE.sub(lambda m: digits_to_words(m.group(0)), text)
    text = _INT_RE.sub(lambda m: number_to_words(int(m.group(0))), text)
    return text


def _expand_chunk(chunk: str) -> str:
    """The ordered expansion pipeline, applied to one unprotected chunk."""
    chunk = expand_times(chunk)
    chunk = expand_room_numbers(chunk)
    chunk = expand_bus_numbers(chunk)
    chunk = expand_locker_numbers(chunk)
    chunk = expand_extensions(chunk)
    chunk = expand_phone_numbers(chunk)
    chunk = expand_ranges(chunk)
    chunk = expand_ordinals(chunk)
    chunk = expand_abbreviations(chunk)
    chunk = expand_numbers(chunk)
    return chunk


# --------------------------------------------------------------------------
# Final tidy
# --------------------------------------------------------------------------

def tidy_punctuation(text: str) -> str:
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    # Add the missing space after punctuation, but not between the dots of an
    # ellipsis and not inside a decimal-looking pair.
    text = re.sub(r"([,.!?;:])(?=[^\s\d.,!?;:])", r"\1 ", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


def recapitalize(text: str) -> str:
    """Cosmetic only -- TTS ignores case, but the operator sees this string.

    Expansions like 'Bus 12' -> 'bus number twelve' can leave a lowercase word
    at the start of a sentence, which looks like a bug to the person reading it.
    """
    def _upper(m: "re.Match[str]") -> str:
        return m.group(0).upper()
    text = re.sub(r"^\s*[a-z]", _upper, text)
    text = re.sub(r"(?<=[.!?]\s)[a-z]", _upper, text)
    return text


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------

def normalize(text: str, dictionary: Optional[Dict[str, str]] = None) -> NormalizationResult:
    """Run the full pipeline. `dictionary` overrides/extends SEED_PRONUNCIATIONS."""
    raw = text
    merged = dict(SEED_PRONUNCIATIONS)
    if dictionary:
        merged.update(dictionary)

    warnings: List[str] = []
    text, found = strip_control_characters(text)
    warnings += found
    text, found = strip_markup(text)
    warnings += found
    text = collapse_whitespace(text)
    text = collapse_repeated_punctuation(text)

    segments = _split_protected(text, merged)
    text = "".join(chunk if fixed else _expand_chunk(chunk) for fixed, chunk in segments)

    text = collapse_whitespace(text)
    text = tidy_punctuation(text)
    text = recapitalize(text)
    return NormalizationResult(raw=raw, normalized=text, warnings=warnings)
