"""Tests for the model-response parsing that sits between a model and the engine.

These are not about intelligence, they are about robustness. Free-tier models
wrap JSON in prose, in code fences, and occasionally in the schema's own
structure. Every one of those was observed in practice during the build, and
each one silently degraded a real benchmark decision to a rules fallback before
it was handled here.
"""

from __future__ import annotations

import pytest

from benchmark_pulse.llm import LLMError, _unwrap_schema_echo, extract_json

FIELDS = {
    "benchmark_ticker": {"type": "string"},
    "confidence": {"type": "number"},
    "rationale": {"type": "string"},
}


# -- extract_json -----------------------------------------------------------

def test_plain_json_parses():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_fenced_json_parses():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_fenced_without_language_tag_parses():
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_json_wrapped_in_prose_parses():
    text = 'Certainly! Here is the result:\n```json\n{"a": [1, 2]}\n```\nHope this helps.'
    assert extract_json(text) == {"a": [1, 2]}


def test_bare_object_inside_a_sentence_parses():
    assert extract_json('The answer is {"score": 68} overall.') == {"score": 68}


def test_unparseable_response_raises_rather_than_returning_empty():
    """Silently returning {} would look like a model that answered nothing."""
    with pytest.raises(LLMError, match="no JSON object found"):
        extract_json("I'm not able to help with that request.")


# -- schema echo ------------------------------------------------------------

def test_correct_payload_is_returned_untouched():
    payload = {"benchmark_ticker": "KSA", "confidence": 0.9, "rationale": "x"}
    assert _unwrap_schema_echo(payload, FIELDS) == payload


def test_properties_wrapper_is_unwrapped():
    """The observed Gemini failure: the schema's shape echoed back verbatim."""
    inner = {"benchmark_ticker": "KSA", "confidence": 0.95, "rationale": "x"}
    assert _unwrap_schema_echo({"properties": inner}, FIELDS) == inner


@pytest.mark.parametrize("wrapper", ["response", "result", "output", "data"])
def test_common_wrapper_keys_are_unwrapped(wrapper):
    inner = {"benchmark_ticker": "^SP500TR", "confidence": 0.8, "rationale": "y"}
    assert _unwrap_schema_echo({wrapper: inner}, FIELDS) == inner


def test_single_unrecognised_wrapper_is_unwrapped():
    inner = {"benchmark_ticker": "VNQ", "confidence": 0.7, "rationale": "z"}
    assert _unwrap_schema_echo({"benchmark_decision": inner}, FIELDS) == inner


def test_unrelated_payload_is_left_alone():
    """Never invent a match: an unrecognised shape must survive for the caller
    to reject, not be mangled into something that looks valid."""
    payload = {"error": {"code": 429, "message": "quota"}}
    assert _unwrap_schema_echo(payload, FIELDS) == payload


def test_partial_match_at_top_level_is_kept():
    """One expected key present means the model answered, however incompletely."""
    payload = {"benchmark_ticker": "KSA"}
    assert _unwrap_schema_echo(payload, FIELDS) == payload
