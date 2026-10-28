"""
Analyzes the created geometry and generates a report of the errors found in the generated part.

The checks are deterministic and only rely on the PartSpec, so they work for any part type:
1. Geometry: the shape is valid, a single solid and has a positive volume
2. Overall size: base parameters such as length, width or diameter match the bounding box
3. Holes: every hole diameter in the spec appears the expected number of times, and there are no extra holes or cuts
Anything the validator cannot measure is listed in `unverified`, never silently passed.
"""

import math
from collections import Counter
from pathlib import Path
from typing import Literal

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface  # type: ignore[import-untyped]

from schemas import Check, PartSpec, ValidationReport

ABS_TOL = 0.01  # mm
REL_TOL = 1e-3

# Base parameter names that must match a bounding box extent (errors) or probably do (warnings)
SIZE_KEYWORDS = ("length", "width", "height", "depth", "diameter")
SOFT_SIZE_KEYWORDS = ("thickness",)
# Base parameters describing inner geometry, never a bounding box extent
INTERNAL_KEYWORDS = ("inner", "inside", "bore", "hole")
HOLE_KEYWORDS = ("hole", "bore")
FULL_TURN = 2 * math.pi - 1e-3


def is_close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def load_shape(model_path: Path) -> cq.Shape:
    shapes = [value for value in cq.importers.importStep(str(model_path)).vals() if isinstance(value, cq.Shape)]
    return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)


def find_concave_cylinders(shape: cq.Shape) -> list[tuple[float, bool]]:
    """
    Return (diameter, goes_all_around) for every distinct concave cylindrical surface (its normal points to the axis).
    A full turn is a hole; a partial one is a hole breaking out of the part or an inner rounded corner.
    Convex cylinders such as the outer rim of a disc or rounded outer corners are ignored.
    """
    cylinders: dict[tuple, list[float]] = {}  # key -> [diameter, swept angle]
    for face in shape.Faces():
        if face.geomType() != "CYLINDER":
            continue
        # BRepAdaptor works for every surface; face._geomAdaptor() fails on moved faces (a trimmed surface wrapper)
        cylinder = BRepAdaptor_Surface(face.wrapped).Cylinder()
        axis = cylinder.Axis()
        direction = cq.Vector(axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z()).normalized()
        origin = cq.Vector(axis.Location().X(), axis.Location().Y(), axis.Location().Z())

        point = face.positionAt(0.5, 0.5)
        offset = point - origin
        radial = offset - direction * offset.dot(direction)
        if face.normalAt(point).dot(radial) >= 0:
            continue

        # The same hole can be split in several faces: identify it by diameter and axis line, and add up the angles
        if next(c for c in direction.toTuple() if abs(c) > ABS_TOL) < 0:
            direction = -direction
        axis_point = origin - direction * origin.dot(direction)
        diameter = 2 * cylinder.Radius()
        key = (round(diameter, 2), *(round(c, 2) for c in (*direction.toTuple(), *axis_point.toTuple())))
        u_min, u_max, _, _ = face._uvBounds()
        entry = cylinders.setdefault(key, [diameter, 0.0])
        entry[1] += u_max - u_min
    return [(diameter, angle >= FULL_TURN) for diameter, angle in cylinders.values()]


def find_hole_diameters(shape: cq.Shape) -> list[float]:
    return [diameter for diameter, full in find_concave_cylinders(shape) if full]


def check_geometry(shape: cq.Shape) -> list[Check]:
    solid_count = len(shape.Solids())
    volume = shape.Volume()
    return [
        Check(
            name="valid_geometry",
            passed=shape.isValid(),
            expected=True,
            actual=shape.isValid(),
            message="The shape is not geometrically valid (self intersections or broken faces)",
        ),
        Check(
            name="single_solid",
            passed=solid_count == 1,
            expected=1,
            actual=solid_count,
            message=f"Expected one solid, got {solid_count}. Disconnected bodies must touch or overlap and be joined with union()",
        ),
        Check(
            name="positive_volume",
            passed=volume > 0,
            expected="> 0",
            actual=round(volume, 3),
            message=f"The part has a non-positive volume ({volume:g})",
        ),
    ]


def _is_internal(name: str) -> bool:
    return any(word in name.lower() for word in INTERNAL_KEYWORDS)


def check_overall_size(spec: PartSpec, extents: list[float]) -> tuple[list[Check], list[str]]:
    checks: list[Check] = []
    unverified: list[str] = []
    extents_text = " x ".join(f"{extent:g}" for extent in extents)

    size_parameters = {
        name: value
        for name, value in spec.parameters.items()
        if not _is_internal(name) and any(word in name.lower() for word in SIZE_KEYWORDS + SOFT_SIZE_KEYWORDS)
    }
    unverified += [f"parameter '{name}'" for name in spec.parameters if name not in size_parameters and not _is_internal(name)]

    # A bounding box size that no parameter explains means something is too big, too thick or sticks out
    unexplained = [extent for extent in extents if not any(is_close(value, extent) for value in size_parameters.values())]

    for name, value in size_parameters.items():
        key = name.lower()
        # Thickness is a bounding box size of a plate but not of an L bracket: only an error if some size is unexplained
        is_hard = any(word in key for word in SIZE_KEYWORDS) or bool(unexplained)
        severity: Literal["error", "warning"] = "error" if is_hard else "warning"

        # A round body shows its diameter in two directions of the bounding box
        needed = 2 if "diameter" in key else 1
        matches = sum(is_close(value, extent) for extent in extents)
        checks.append(
            Check(
                name=f"size:{name}",
                passed=matches >= needed,
                expected=value,
                actual=extents,
                severity=severity,
                message=f"The specification requires {name} = {value:g}, but the part measures {extents_text}. "
                f"Keep {name} = {value:g}: the geometry is wrong, not the value.",
            )
        )

    if unexplained:
        sizes = ", ".join(f"{extent:g}" for extent in unexplained)
        checks.append(
            Check(
                name="size:unexplained",
                passed=False,
                expected=sorted(size_parameters.values()),
                actual=extents,
                severity="warning",
                message=f"The part measures {sizes} in some direction, which matches no dimension of the specification: "
                "an operation adds material where there should be none (e.g. an extra extrude or a second body).",
            )
        )
    return checks, unverified


def _diameter_of(parameters: dict[str, float]) -> float | None:
    if "diameter" in parameters:
        return parameters["diameter"]
    for name, value in parameters.items():
        if "diameter" in name:
            return value
        if "radius" in name:
            return 2 * value
    return None


def expected_holes(spec: PartSpec) -> tuple[Counter[float], int, list[str]]:
    """
    Hole diameter -> count, from hole/bore features and from base parameters such as 'center_bore_diameter', and the
    number of holes whose diameter the spec does not give (the generator chooses it, so only their count is checked).
    """
    expected: Counter[float] = Counter()
    unsized = 0
    unverified: list[str] = []
    for feature in spec.features:
        diameter = _diameter_of(feature.parameters)
        is_hole = any(word in feature.type.lower() for word in HOLE_KEYWORDS)
        if is_hole and diameter is not None:
            expected[diameter] += feature.count
        elif is_hole:
            unsized += feature.count
        else:
            unverified.append(f"feature '{feature.type}'")
    feature_diameters = list(expected)
    for name, value in spec.parameters.items():
        if _is_internal(name):
            diameter = _diameter_of({name: value})
            if any(word in name.lower() for word in HOLE_KEYWORDS) and diameter is not None:
                # The parser sometimes lists a bore both as a feature and as a parameter: count it once
                if not any(is_close(diameter, known) for known in feature_diameters):
                    expected[diameter] += 1
            else:
                unverified.append(f"parameter '{name}'")
    return expected, unsized, unverified


def check_holes(spec: PartSpec, cylinders: list[tuple[float, bool]]) -> tuple[list[Check], list[str]]:
    expected, unsized, unverified = expected_holes(spec)
    holes = [diameter for diameter, full in cylinders if full]
    partial_cuts = [diameter for diameter, full in cylinders if not full]

    found = Counter(round(diameter, 3) for diameter in holes)
    found_text = ", ".join(f"{count} x {diameter:g}" for diameter, count in sorted(found.items())) or "none"

    checks = []
    for diameter, count in sorted(expected.items()):
        actual = sum(1 for hole in holes if is_close(hole, diameter))
        broken_out = sum(1 for cut in partial_cuts if is_close(cut, diameter))
        message = f"Expected {count} complete hole(s) of diameter {diameter:g}, found {actual}."
        if broken_out:
            message += (
                f" {broken_out} cut(s) of this diameter break out through the edge of the part:"
                " their positions are wrong, every hole must lie fully inside the part."
            )
        else:
            message += f" Holes in the part (count x diam.): {found_text}."
        checks.append(Check(name=f"holes:{diameter:g}", passed=actual == count, expected=count, actual=actual, message=message))

    extra = Counter(round(hole, 3) for hole in holes if not any(is_close(hole, diameter) for diameter in expected))
    if unsized:
        # Holes whose diameter the spec leaves open can have any diameter: only their number can be wrong
        found_unsized = sum(extra.values())
        checks.append(
            Check(
                name="holes:unsized",
                passed=found_unsized == unsized,
                expected=unsized,
                actual=found_unsized,
                message=f"Expected {unsized} hole(s) whose diameter is not specified (any diameter you chose), "
                f"found {found_unsized}. Holes in the part (count x diam.): {found_text}.",
            )
        )
        extra = Counter()
    checks += [
        Check(
            name=f"extra_holes:{diameter:g}",
            passed=False,
            expected=0,
            actual=count,
            message=f"Found {count} hole(s) of diameter {diameter:g} that are not in the specification",
        )
        for diameter, count in sorted(extra.items())
    ]

    rounded_corners = [d for f in spec.features if not any(w in f.type.lower() for w in HOLE_KEYWORDS) and (d := _diameter_of(f.parameters))]
    explained = rounded_corners + list(expected)
    unexpected_cuts = Counter(round(cut, 3) for cut in partial_cuts if not any(is_close(cut, d) for d in explained))
    checks += [
        Check(
            name=f"partial_cuts:{diameter:g}",
            passed=False,
            expected=0,
            actual=count,
            severity="warning",
            message=f"Found {count} round cut(s) of diameter {diameter:g} that do not go all the way around, "
            "e.g. a hole breaking out through the edge of the part",
        )
        for diameter, count in sorted(unexpected_cuts.items())
    ]
    return checks, unverified


def check_assumptions_declared(spec: PartSpec, code: str) -> list[Check]:
    if not spec.missing_parameters:
        return []
    return [
        Check(
            name="assumptions_declared",
            passed="# ASSUMED" in code,
            severity="warning",
            message="The spec has missing parameters but the code has no `# ASSUMED:` comments for the chosen values",
        )
    ]


def validate_model(model_path: Path, spec: PartSpec, code: str | None = None) -> ValidationReport:
    shape = load_shape(model_path)
    bbox = shape.BoundingBox()
    extents = [round(bbox.xlen, 3), round(bbox.ylen, 3), round(bbox.zlen, 3)]

    checks = check_geometry(shape)
    size_checks, size_unverified = check_overall_size(spec, extents)
    hole_checks, hole_unverified = check_holes(spec, find_concave_cylinders(shape))
    checks += size_checks + hole_checks
    if code is not None:
        checks += check_assumptions_declared(spec, code)

    failed = [check for check in checks if not check.passed]
    return ValidationReport(
        valid_solid=shape.isValid(),
        solid_count=len(shape.Solids()),
        volume=shape.Volume(),
        bounding_box=(extents[0], extents[1], extents[2]),
        checks=checks,
        errors=[check.message for check in failed if check.severity == "error"],
        warnings=[check.message for check in failed if check.severity == "warning"],
        unverified=size_unverified + hole_unverified,
    )
