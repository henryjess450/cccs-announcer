"""Text normalization and the pronunciation dictionary.

These are the cheapest tests in the suite and they protect the thing staff
notice first: whether the announcement sounds right.
"""

from __future__ import annotations

import pytest

from app.normalize import SEED_PRONUNCIATIONS, normalize
from app.numbers import digits_to_words, number_to_words, ordinal_to_words


def spoken(text: str, dictionary=None) -> str:
    return normalize(text, dictionary).normalized


# -- numbers ---------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (0, "zero"), (7, "seven"), (13, "thirteen"), (20, "twenty"), (42, "forty two"),
    (100, "one hundred"), (204, "two hundred four"), (1000, "one thousand"),
    (1147, "one thousand one hundred forty seven"),
])
def test_number_to_words(value, expected):
    assert number_to_words(value) == expected


def test_digits_are_read_individually_with_oh_for_zero():
    assert digits_to_words("204") == "two oh four"
    assert digits_to_words("007") == "oh oh seven"


@pytest.mark.parametrize("value,expected", [
    (1, "first"), (2, "second"), (3, "third"), (5, "fifth"), (8, "eighth"),
    (12, "twelfth"), (21, "twenty first"), (40, "fortieth"),
])
def test_ordinals(value, expected):
    assert ordinal_to_words(value) == expected


# -- times -----------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("2:15", "Two fifteen"),
    ("2:00", "Two o'clock"),
    ("10:05", "Ten oh five"),
    ("14:30", "Two thirty"),
    ("8 AM", "Eight a m"),
    ("3:30 p.m.", "Three thirty p m"),
])
def test_time_expansion(text, expected):
    assert spoken(text) == expected


def test_meridiem_does_not_eat_the_sentence_period():
    # Regression: "([ap])\\.?m\\.?" greedily consumed the full stop, so one
    # sentence ran into the next with no pause.
    assert spoken("Dismissal at 12:45 PM.") == "Dismissal at twelve forty five p m."


def test_impossible_clock_values_are_not_spoken_as_times():
    result = spoken("Score was 99:99")
    assert "o'clock" not in result
    assert "ninety nine" in result


# -- rooms, buses, lockers, extensions -------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Rm 204", "Room two oh four"),
    ("Room 204", "Room two oh four"),
    ("rm. 1140", "Room one one four oh"),
    ("Rm 12B", "Room twelve b"),
    ("room 7", "Room seven"),
])
def test_room_numbers(text, expected):
    assert spoken(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Bus 12", "Bus number twelve"),
    ("Bus number 12", "Bus number twelve"),
    ("bus #147", "Bus number one four seven"),
])
def test_bus_numbers(text, expected):
    assert spoken(text) == expected


def test_lockers_and_extensions_are_read_digit_by_digit():
    assert spoken("Locker 0142") == "Locker oh one four two"
    assert spoken("ext 3021") == "Extension three oh two one"


def test_phone_numbers_are_not_read_as_quantities():
    result = spoken("Call 555-1234")
    assert result == "Call five five five one two three four"
    assert "hundred" not in result


def test_short_ranges_still_read_as_ranges():
    assert spoken("Buses 8-10 load now") == "Buses eight to ten load now"


# -- abbreviations and symbols ---------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Mr. Smith", "Mister Smith"),
    ("Mrs. Diaz", "Missus Diaz"),
    ("Dr. Jones", "Doctor Jones"),
    ("staff & students", "Staff and students"),
    ("w/ lunch", "With lunch"),
    ("w/o a coat", "Without a coat"),
    ("50% off", "Fifty percent off"),
    ("ASAP", "As soon as possible"),
])
def test_abbreviations(text, expected):
    assert spoken(text) == expected


def test_ambiguous_abbreviations_are_left_alone():
    # We would rather say "St." than guess wrong between Saint and Street.
    assert "St." in spoken("Meet on Elm St. at noon")


# -- pronunciation dictionary ----------------------------------------------

def test_seed_dictionary_spells_the_school_name():
    assert spoken("Attention CCCS staff") == "Attention C C C S staff"


def test_dictionary_entries_survive_the_number_rules():
    # Regression: dictionary replacements used to be spliced back through the
    # numeric rules, and "CCCS" came out of the pipeline as "zero".
    result = spoken("CCCS bus 12 leaves at 3:15")
    assert result.startswith("C C C S")
    assert "zero" not in result


def test_admin_entries_override_the_seed():
    assert spoken("Welcome to CCCS", {"CCCS": "our school"}) == "Welcome to our school"


def test_dictionary_matching_is_case_insensitive():
    assert spoken("cccs news") == "C C C S news"


def test_dictionary_respects_word_boundaries():
    # "PTAs" is a different word and must not match the "PTA" entry.
    assert "P T A" not in spoken("Several PTAs attended")


def test_seed_dictionary_has_the_school_acronym():
    assert "CCCS" in SEED_PRONUNCIATIONS


# -- safety and hygiene ----------------------------------------------------

def test_markup_is_stripped_and_flagged():
    result = normalize("<speak><prosody rate='x-fast'>hello</prosody></speak>")
    assert "<" not in result.normalized and ">" not in result.normalized
    assert result.normalized == "Hello"
    assert result.warnings


def test_character_entities_are_stripped():
    assert "&amp;" not in normalize("Tom &amp; Jerry").normalized


def test_control_characters_are_removed_and_flagged():
    result = normalize("hello​world")
    assert "​" not in result.normalized
    assert result.warnings


def test_newlines_become_spaces_so_piper_gets_one_line():
    # Piper synthesizes stdin one line at a time; multi-line input would only
    # produce the last line.
    assert "\n" not in normalize("line one\nline two").normalized


def test_runaway_whitespace_and_punctuation_are_collapsed():
    assert spoken("Hello     there!!!!!") == "Hello there!"
    assert spoken("Wait......") == "Wait..."


def test_empty_and_whitespace_only_input_produce_nothing_speakable():
    assert normalize("").normalized == ""
    assert normalize("   \n\t ").normalized == ""


def test_a_realistic_announcement_end_to_end():
    result = normalize(
        "Attention CCCS staff & students: buses 8-10 depart at 3:15 PM "
        "from the north lot. Mr. Alvarez, please report to Rm 204."
    )
    assert result.normalized == (
        "Attention C C C S staff and students: buses eight to ten depart at "
        "three fifteen p m from the north lot. Mister Alvarez, please report "
        "to room two oh four."
    )
