import ast

import cadquery as cq

from cadquery_api import api_reference, check_api_calls, explain_runtime_error, format_signature, methods_used


def errors_for(code: str) -> list[str]:
    return check_api_calls(ast.parse(code))


def test_real_bracket_failure_is_caught_before_execution():
    """The 7B model tried centered=, center= and centerX= on workplane() in three attempts."""
    code = 'import cadquery as cq\nresult = cq.Workplane("XY").box(80, 40, 5).faces(">Z").workplane(centerX=40, centerY=0).hole(6)'

    errors = errors_for(code)

    assert len(errors) == 1
    assert "Workplane.workplane() has no argument 'centerX'" in errors[0]
    assert "Did you mean the method .center()?" in errors[0]
    assert "Workplane.center(x, y)" in errors[0]
    assert "origin=None" in errors[0]  # the real signature is included


def test_valid_chain_has_no_errors():
    code = """import cadquery as cq
result = (
    cq.Workplane("XY").circle(75).extrude(20)
    .faces(">Z").workplane(centerOption="CenterOfBoundBox").hole(65)
    .faces(">Z").workplane().polarArray(57.15, 0, 360, 5).hole(15)
    .edges("|Z").fillet(1)
)"""
    assert errors_for(code) == []


def test_unknown_method_on_variable_is_caught_with_suggestion():
    code = "import cadquery as cq\nbase = cq.Workplane().box(1, 2, 3)\nresult = base.fillett(1)"

    errors = errors_for(code)

    assert "Workplane has no method `.fillett()`" in errors[0]
    assert ".fillet()" in errors[0]


def test_too_many_positional_arguments():
    errors = errors_for("import cadquery as cq\nresult = cq.Workplane().circle(1, 2, 3, 4)")

    assert "too many positional arguments" in errors[0]
    assert "Workplane.circle(radius, forConstruction=False)" in errors[0]


def test_chain_nested_in_argument_and_sketch_are_checked():
    code = """import cadquery as cq
result = cq.Workplane().box(10, 10, 10).union(cq.Workplane().sphere(5, radius=2))
plate = cq.Workplane().box(10, 10, 1).faces(">Z").workplane().sketch().rect(2, 3, angel=10).finalize()"""

    errors = errors_for(code)

    assert any("Workplane.sphere()" in error for error in errors)
    assert any("Sketch.rect() has no argument 'angel'" in error and "'angle'" in error for error in errors)


def test_chain_leaving_the_fluent_api_is_not_checked():
    # .val() returns a Shape: what follows cannot be resolved and must not be reported
    assert errors_for("import cadquery as cq\nresult = cq.Workplane().box(1, 1, 1).val().whatever(1)") == []


def test_unknown_names_are_ignored():
    assert errors_for("import math\nx = math.cos(1)\ny = something.unknown(1, 2)") == []


def test_explain_runtime_type_error():
    text = explain_runtime_error("Traceback...\nTypeError: Workplane.workplane() got an unexpected keyword argument 'centered'")

    assert text is not None
    assert "Real signature of Workplane.workplane()" in text
    assert "Workplane.center(x, y)" in text


def test_explain_runtime_attribute_error():
    text = explain_runtime_error("AttributeError: 'Workplane' object has no attribute 'fillett'")

    assert text is not None
    assert ".fillet()" in text


def test_explain_ignores_kernel_errors():
    assert explain_runtime_error("OCP.OCP.StdFail.StdFail_NotDone: BRep_API: command not done") is None


def test_signature_shows_literal_choices():
    assert "(one of 'CenterOfMass', 'ProjectedOrigin', 'CenterOfBoundBox')" in format_signature(cq.Workplane, "workplane")


def test_api_reference_lists_core_and_used_methods_once():
    reference = api_reference(["import cadquery as cq\nr = cq.Workplane().polarArray(1, 0, 360, 4).hole(1)"])

    assert "Workplane.workplane(" in reference
    assert "Workplane.polarArray(" in reference
    assert reference.count("Workplane.hole(") == 1


def test_methods_used_in_order():
    code = "import cadquery as cq\nr = cq.Workplane().box(1, 1, 1).faces('>Z').workplane().hole(1)"

    assert [name for _, name in methods_used(code)] == ["box", "faces", "workplane", "hole"]
