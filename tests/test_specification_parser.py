import json

import pytest
from fakes import FakeLLM

from schemas import PartSpec
from specification_parser import SpecParseError, check_spec, extract_numbers, parse_specification

WHEEL_SPACER = "Wheel spacer disc 150 mm diameter, 20 mm thick, center bore 65 mm diameter, 5 bolt holes each 15 mm diameter"

GOOD_WHEEL_SPACER = {
    "part_type": "wheel_spacer",
    "description": "Disc with a center bore and five bolt holes",
    "units": "mm",
    "parameters": {"diameter": 150, "thickness": 20},
    "features": [
        {"type": "center_bore", "count": 1, "parameters": {"diameter": 65}},
        {"type": "through_hole", "count": 5, "parameters": {"diameter": 15}, "location": "on a bolt circle"},
    ],
    "missing_parameters": ["bolt circle diameter"],
    "assumptions": ["bolt holes are evenly spaced"],
}


def test_extract_numbers_reads_digits_and_words():
    assert extract_numbers("two 6 mm holes, 80 × 40 mm, 2,5 mm") == {2.0, 6.0, 80.0, 40.0, 2.5}  # noqa: RUF001


def test_valid_spec_is_accepted_first_try():
    llm = FakeLLM(json.dumps(GOOD_WHEEL_SPACER))

    spec = parse_specification(WHEEL_SPACER, llm=llm)

    assert spec.part_type == "wheel_spacer"
    assert spec.features[1].count == 5
    assert spec.warnings == []
    assert not spec.is_complete
    assert len(llm.calls) == 1


def test_invented_dimension_triggers_retry_with_feedback():
    invented = {**GOOD_WHEEL_SPACER, "missing_parameters": []}
    invented["features"] = [*GOOD_WHEEL_SPACER["features"], {"type": "bolt_circle", "parameters": {"diameter": 114.3}}]
    llm = FakeLLM(json.dumps(invented), json.dumps(GOOD_WHEEL_SPACER))

    spec = parse_specification(WHEEL_SPACER, llm=llm)

    assert spec.warnings == []
    feedback = llm.calls[1][-1]["content"]
    assert "114.3" in feedback


def test_invented_dimension_is_allowed_when_justified_in_assumptions():
    justified = {**GOOD_WHEEL_SPACER}
    justified["features"] = [*GOOD_WHEEL_SPACER["features"], {"type": "bolt_circle", "parameters": {"diameter": 114.3}}]
    justified["assumptions"] = ["bolt circle diameter 114.3 mm, the common 5x114.3 automotive pattern"]

    errors, _ = check_spec(PartSpec.model_validate(justified), WHEEL_SPACER)

    assert errors == []


def test_uncaptured_number_is_an_error():
    dropped = {**GOOD_WHEEL_SPACER, "parameters": {"diameter": 150}}

    errors, _ = check_spec(PartSpec.model_validate(dropped), WHEEL_SPACER)

    assert any("20" in error for error in errors)


def test_non_positive_dimension_is_an_error():
    negative = {**GOOD_WHEEL_SPACER, "parameters": {"diameter": 150, "thickness": -20}}

    errors, _ = check_spec(PartSpec.model_validate(negative), WHEEL_SPACER)

    assert any("must be positive" in error for error in errors)


def test_derived_count_is_only_a_warning():
    text = "L-shaped bracket made from 5 mm plate, legs 80 × 40 mm, width 40 mm, two 6 mm diameter holes in each leg"  # noqa: RUF001
    spec = PartSpec(
        part_type="l_bracket",
        description="L bracket",
        parameters={"thickness": 5, "leg_1_length": 80, "leg_2_length": 40, "width": 40},
        features=[{"type": "through_hole", "count": 4, "parameters": {"diameter": 6}, "location": "two in each leg"}],
    )

    errors, warnings = check_spec(spec, text)

    assert errors == []
    assert len(warnings) == 1


def test_schema_error_then_recovery_and_code_fences():
    llm = FakeLLM('{"part_type": "wheel_spacer"}', f"```json\n{json.dumps(GOOD_WHEEL_SPACER)}\n```")

    spec = parse_specification(WHEEL_SPACER, llm=llm)

    assert spec.part_type == "wheel_spacer"
    assert "schema" in llm.calls[1][-1]["content"]


def test_raises_when_never_schema_valid():
    llm = FakeLLM("not json", None, "{}")

    with pytest.raises(SpecParseError):
        parse_specification(WHEEL_SPACER, llm=llm)


def test_returns_best_effort_with_warnings_when_checks_keep_failing():
    dropped = json.dumps({**GOOD_WHEEL_SPACER, "parameters": {"diameter": 150}})
    llm = FakeLLM(dropped, dropped, dropped)

    spec = parse_specification(WHEEL_SPACER, llm=llm)

    assert any("20" in warning for warning in spec.warnings)


def test_empty_specification_is_rejected():
    with pytest.raises(ValueError):
        parse_specification("   ", llm=FakeLLM())


def test_llm_schema_hides_parser_only_fields():
    assert "warnings" not in PartSpec.model_json_schema()["properties"]
