import cadquery as cq
import pytest

from schemas import PartSpec
from validator import find_hole_diameters, validate_model

WHEEL_SPACER = PartSpec(
    part_type="wheel_spacer",
    description="Disc with a center bore and five bolt holes",
    parameters={"diameter": 150, "thickness": 20},
    features=[
        {"type": "center_bore", "parameters": {"diameter": 65}},
        {"type": "through_hole", "count": 5, "parameters": {"diameter": 15}},
        {"type": "bolt_circle", "parameters": {"diameter": 114.3}},
    ],
    missing_parameters=["bolt circle diameter"],
)


def wheel_spacer(bolt_holes: int = 5, diameter: float = 150) -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .circle(diameter / 2)
        .extrude(20)
        .faces(">Z")
        .workplane()
        .hole(65)
        .faces(">Z")
        .workplane()
        .polarArray(57.15, 0, 360, bolt_holes)
        .hole(15)
    )


@pytest.fixture
def export(tmp_path):
    def _export(part: cq.Workplane | cq.Shape):
        path = tmp_path / "part.step"
        cq.exporters.export(part, str(path))
        return path

    return _export


def test_correct_part_passes(export):
    report = validate_model(export(wheel_spacer()), WHEEL_SPACER, code="x = 1  # ASSUMED: bolt circle")

    assert report.passed, report.errors
    assert report.warnings == []
    assert report.solid_count == 1
    assert report.bounding_box == (150, 150, 20)
    assert "feature 'bolt_circle'" in report.unverified


def test_wrong_hole_count_is_reported(export):
    report = validate_model(export(wheel_spacer(bolt_holes=4)), WHEEL_SPACER)

    assert not report.passed
    assert any("Expected 5 complete hole(s) of diameter 15, found 4" in error for error in report.errors)


def test_wrong_overall_size_is_reported(export):
    report = validate_model(export(wheel_spacer(diameter=140)), WHEEL_SPACER)

    assert any("requires diameter = 150" in error for error in report.errors)


def test_two_disconnected_solids_fail(export):
    a = cq.Workplane().box(10, 10, 10)
    b = cq.Workplane().center(50, 0).box(10, 10, 10)
    spec = PartSpec(part_type="block", description="block", parameters={"length": 10})

    report = validate_model(export(cq.Compound.makeCompound([a.val(), b.val()])), spec)

    assert any("Expected one solid, got 2" in error for error in report.errors)


def test_missing_assumption_comment_is_only_a_warning(export):
    report = validate_model(export(wheel_spacer()), WHEEL_SPACER, code="x = 114.3")

    assert report.passed
    assert len(report.warnings) == 1


def test_hole_detection_ignores_outer_rim_and_fillets():
    plate = cq.Workplane().box(40, 40, 5).edges("|Z").fillet(4).faces(">Z").workplane().rect(20, 20, forConstruction=True).vertices().hole(6)

    assert sorted(find_hole_diameters(plate.val())) == pytest.approx([6, 6, 6, 6])


def test_l_bracket_with_holes_in_both_legs(export):
    spec = PartSpec(
        part_type="l_bracket",
        description="L bracket",
        parameters={"thickness": 5, "leg_1_length": 80, "leg_2_length": 40, "width": 40},
        features=[{"type": "through_hole", "count": 4, "parameters": {"diameter": 6}}],
    )
    base = cq.Workplane("XY").box(80, 40, 5, centered=(False, True, False))
    upright = cq.Workplane("XY").box(5, 40, 40, centered=(False, True, False))
    bracket = (
        base.union(upright)
        .faces(">Z[-2]")
        .workplane()
        .pushPoints([(50, 10), (50, -10)])
        .hole(6)
        .faces("<X")
        .workplane()
        .pushPoints([(10, 25), (-10, 25)])
        .hole(6)
    )

    report = validate_model(export(bracket), spec)

    assert report.passed, report.errors


def test_failed_trial_reports_every_problem(export):
    """Reproduces the first real trial: a second disc extruded on top and the 65 mm bores cut outside the disc."""
    spec = PartSpec(
        part_type="wheel_spacer_disc",
        description="Wheel spacer",
        parameters={"diameter": 150, "thickness": 20, "center_bore_diameter": 65},
        features=[{"type": "through_hole", "count": 5, "parameters": {"diameter": 15}}],
    )
    part = (
        cq.Workplane("XY")
        .circle(75)
        .extrude(20)
        .faces(">Z")
        .workplane()
        .circle(32.5)
        .extrude(20)
        .faces(">Z")
        .workplane()
        .rect(130, 130, forConstruction=True)
        .vertices()
        .hole(65)
    )

    report = validate_model(export(part), spec)

    assert any("requires thickness = 20" in error and "Keep thickness = 20" in error for error in report.errors)
    assert any("Expected 1 complete hole(s) of diameter 65, found 0" in error for error in report.errors)
    assert any("Expected 5 complete hole(s) of diameter 15" in error for error in report.errors)
    bore_error = next(error for error in report.errors if "diameter 65" in error)
    assert "4 cut(s) of this diameter break out through the edge" in bore_error
    assert not any("diameter 65" in warning for warning in report.warnings)  # already explained by the error
    assert report.unverified == []


def test_bore_listed_as_feature_and_parameter_is_counted_once(export):
    """Second real trial: the parser put the 65 mm bore both in `features` and in `parameters`."""
    spec = WHEEL_SPACER.model_copy(update={"parameters": {"diameter": 150, "thickness": 20, "center_bore_diameter": 65}})

    report = validate_model(export(wheel_spacer()), spec)

    assert report.passed, report.errors


def test_extra_holes_are_an_error(export):
    spec = PartSpec(part_type="plate", description="plate", parameters={"length": 40, "width": 40, "thickness": 5})
    plate = cq.Workplane().box(40, 40, 5).faces(">Z").workplane().hole(6)

    report = validate_model(export(plate), spec)

    assert report.errors == ["Found 1 hole(s) of diameter 6 that are not in the specification"]


def test_hole_without_diameter_in_the_spec_is_only_counted(export):
    """The underspecified run kept failing on 'hole not in the specification' although the spec gave no diameter."""
    spec = PartSpec(
        part_type="bracket",
        description="bracket",
        parameters={"length": 40, "width": 40, "thickness": 5},
        features=[{"type": "through_hole", "count": 1, "parameters": {}}],
    )
    one_hole = cq.Workplane().box(40, 40, 5).faces(">Z").workplane().hole(10)
    two_holes = cq.Workplane().box(40, 40, 5).faces(">Z").workplane().pushPoints([(-10, 0), (10, 0)]).hole(6)

    assert validate_model(export(one_hole), spec).passed
    errors = validate_model(export(two_holes), spec).errors
    assert errors == [
        "Expected 1 hole(s) whose diameter is not specified (any diameter you chose), found 2. Holes in the part (count x diam.): 2 x 6."
    ]


def test_inner_rounded_corner_is_not_a_hole(export):
    spec = PartSpec(
        part_type="l_bracket",
        description="L bracket",
        parameters={"leg_1_length": 80, "leg_2_length": 40, "width": 40, "thickness": 5},
        features=[{"type": "fillet", "parameters": {"radius": 3}}],
    )
    base = cq.Workplane("XY").box(80, 40, 5, centered=(False, True, False))
    upright = cq.Workplane("XY").box(5, 40, 40, centered=(False, True, False))
    bracket = base.union(upright).edges("|Y").edges(cq.selectors.NearestToPointSelector((5, 0, 5))).fillet(3)

    report = validate_model(export(bracket), spec)

    # The rounded inner corner is a concave cylinder too, but neither a hole nor an unexpected cut
    assert report.passed, report.errors
    assert not any("hole" in warning or "round cut" in warning for warning in report.warnings)
    assert report.unverified == ["feature 'fillet'"]
