"""Tests for the numeric guard.

This is the component the pitch rests on: the claim is not that the model is
careful, it is that an unverifiable figure cannot reach a client. That claim is
only as good as these tests.
"""

from __future__ import annotations

import pytest

from benchmark_pulse.narrative import (
    Fact, Narrative, _numbers_in, tamper, verify,
)

FACTS = [
    Fact("portfolio.irr", "Portfolio IRR", 0.167, "16.7%"),
    Fact("portfolio.benchmark_irr", "Benchmark", 0.177, "17.7%"),
    Fact("portfolio.alpha", "Alpha", -0.008, "-0.8%"),
    Fact("portfolio.ks_pme", "KS-PME", 0.96, "0.96"),
    Fact("count.holdings", "Holdings", 24, "24"),
    Fact("count.beating", "Beating", 7, "7"),
    Fact("capital.value", "Value", 38300000, "38,300,000"),
    Fact("mandate.ticker", "Mandate", "ACWI", "ACWI"),
]


# -- number extraction ------------------------------------------------------

def test_extracts_plain_and_formatted_numbers():
    assert _numbers_in("16.7% and 24 holdings") == ["16.7", "24"]


def test_thousands_separators_are_normalised():
    assert _numbers_in("$38,300,000") == ["38300000"]


def test_negative_sign_is_stripped_for_comparison():
    """-0.8% and 0.8% are the same figure differently framed; the guard checks
    the magnitude was computed, not the direction it was written in."""
    assert _numbers_in("-0.8%") == ["0.8"]


def test_trailing_zeros_do_not_make_a_different_number():
    assert _numbers_in("16.70%") == _numbers_in("16.7%")


# -- the guard --------------------------------------------------------------

def test_prose_using_only_computed_figures_passes():
    text = ("The portfolio returned 16.7% against 17.7% for ACWI, a shortfall "
            "of 0.8%. Of 24 holdings, 7 beat their sector benchmark.")
    assert verify(text, FACTS) == []


def test_an_invented_figure_is_caught():
    """The failure this exists to prevent."""
    text = "The portfolio returned 16.7%, comfortably ahead of the 12.3% peer median."
    violations = verify(text, FACTS)
    assert len(violations) == 1
    assert violations[0].number == "12.3"


def test_a_computed_figure_the_model_altered_is_caught():
    text = "The portfolio returned 18.4% against 17.7% for ACWI."
    violations = verify(text, FACTS)
    assert [v.number for v in violations] == ["18.4"]


def test_arithmetic_the_model_performed_itself_is_caught():
    """17.7 - 16.7 = 1.0 is correct arithmetic and still blocked: the engine did
    not compute it, so nothing verifies it."""
    text = "The portfolio trailed its benchmark by 1.0 percentage points."
    assert [v.number for v in verify(text, FACTS)] == ["1"]


def test_small_counts_and_years_need_no_fact():
    """Otherwise the guard fires on ordinary prose and gets ignored."""
    text = ("Two of the three weakest names are in one sector, and the position "
            "was opened in 2022.")
    assert verify(text, FACTS) == []


def test_violation_reports_its_context():
    text = "Returns were 16.7%. The peer group managed 9.9% over the period."
    violations = verify(text, FACTS)
    assert len(violations) == 1
    assert "peer group" in violations[0].context


def test_multiple_violations_are_all_reported():
    text = "It returned 11.1% against 22.2%, a gap of 33.3%."
    assert len(verify(text, FACTS)) == 3


# -- publishing -------------------------------------------------------------

def test_clean_narrative_publishes():
    n = Narrative(text="Returned 16.7% against 17.7%.", facts=FACTS)
    n.violations = verify(n.text, FACTS)
    assert n.publishable
    assert n.status == "verified"


def test_narrative_with_a_bad_figure_does_not_publish():
    n = Narrative(text="Returned 16.7% against a 44.4% benchmark.", facts=FACTS)
    n.violations = verify(n.text, FACTS)
    assert not n.publishable
    assert "blocked" in n.status
    assert "1 unverified figure" in n.status


def test_status_pluralises_correctly():
    n = Narrative(text="Figures of 44.4% and 55.5%.", facts=FACTS)
    n.violations = verify(n.text, FACTS)
    assert "2 unverified figures" in n.status


# -- the demonstration ------------------------------------------------------

def test_tamper_produces_a_draft_the_guard_rejects():
    """The demo beat: alter one figure, watch the guard refuse."""
    clean = Narrative(text="The portfolio returned 16.7% against 17.7%.",
                      facts=FACTS)
    clean.violations = verify(clean.text, FACTS)
    assert clean.publishable

    broken = tamper(clean)
    assert not broken.publishable
    assert broken.text != clean.text
    # The fact table is untouched, so the guard had every chance to notice.
    assert broken.facts == clean.facts


def test_tamper_leaves_an_unmatchable_narrative_alone():
    empty = Narrative(text="No figures appear in this sentence.", facts=FACTS)
    assert tamper(empty).text == empty.text
