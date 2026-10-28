Convert the natural language CAD specification below into a structured JSON part specification.

# Rules
1. Extract ONLY what the specification states. Copy every number exactly as written; do not convert units or derive values (keep a diameter as a diameter, do not turn it into a radius).
2. `parameters` holds only the outer dimensions of the base body (length, width, height, thickness, outer diameter, ...). Use snake_case keys. A part with several legs or arms (L-bracket, T-shape, ...) gets one parameter per leg, e.g. `leg1_length` and `leg2_length`: never merge the legs into a single length.
3. `features` holds everything cut or added to the base body: every hole and every bore (including a center bore) is a feature, never a base parameter; also slots, fillets, chamfers, ... Put the number of instances in `count`, not in `parameters`.
4. If a dimension needed to build the part is not given, add a short description of it to `missing_parameters`. Do NOT make up a value for it. Usually missing are the positions of features: the bolt circle (pitch circle) diameter of holes arranged around a center, or the distance from the edges of holes in a plate. A single center bore needs no position, it is on the center.
5. If you interpret something that is not explicit (e.g. "plate" means constant thickness, holes are through holes), write it in `assumptions` with a short justification. Any number that does not appear in the specification MUST be mentioned in an assumption.
6. `units` is "mm" unless the specification uses inches.
7. Every number in the specification must appear somewhere in the output.

# Example
Specification:
Rectangular mounting plate 120 x 60 mm, 8 mm thick, four 5 mm holes near the corners

Output:
{
  "part_type": "mounting_plate",
  "description": "Rectangular mounting plate with four corner holes",
  "units": "mm",
  "parameters": {"length": 120, "width": 60, "thickness": 8},
  "features": [
    {"type": "through_hole", "count": 4, "parameters": {"diameter": 5}, "location": "near the corners"}
  ],
  "missing_parameters": ["distance of the holes from the plate edges"],
  "assumptions": ["holes go through the full thickness of the plate"]
}

# Specification
{specification}

Output:
