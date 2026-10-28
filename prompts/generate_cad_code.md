Write a CadQuery Python script that builds the part described by the JSON specification below.

# Rules
1. Start with `import cadquery as cq`. The only other allowed import is `math`.
2. Define every dimension as a named variable at the top of the script, using the values from `parameters` and the feature `parameters`. Never hard-code numbers inside the modelling calls.
3. For each entry in `missing_parameters`, choose a reasonable engineering value, define it as a variable and mark it with a comment `# ASSUMED: <short reason>`.
4. Respect every entry in `assumptions`.
5. Assign the final part to a variable named `result`. It must be a single solid.
6. Do not export, show, print, or read/write files. Only build the geometry.
7. Model in the units given in `units`, with Z pointing up and the base body centred on the origin.
8. Choose the pattern that fits the shape of the part. The examples below show the basic layouts; the recipes after them, when there are any, were selected because they are similar to this part: adapt their approach, but take every dimension from the specification, not from the recipe. Holes around a center of a round part go on a circle with `polarArray`; `rect(..., forConstruction=True).vertices()` is only for holes at the corners of a rectangle.
9. Cut holes and bores with `.hole(diameter)`. Never `.circle(...).extrude(...)` a hole: that adds material instead of removing it. Cut holes after all bodies are joined: a `.union(...)` fills holes cut before it.
10. Each feature is created exactly once. Every hole must lie completely inside the part.
11. Only call CadQuery methods and arguments that appear in the API reference, the patterns, the examples or the recipes. Do not invent argument names.
12. Return only the script, inside a single ```python code block. Keep it short.

# Useful CadQuery patterns
- Box centred on the origin: `cq.Workplane("XY").box(length, width, thickness)`
- Disc: `cq.Workplane("XY").circle(diameter / 2).extrude(thickness)`
- Through hole at the centre of the top face: `.faces(">Z").workplane().hole(diameter)`
- Holes at given points: `.faces(">Z").workplane().pushPoints([(x1, y1), (x2, y2)]).hole(diameter)`
- Holes at the corners of a rectangle: `.faces(">Z").workplane().rect(dx, dy, forConstruction=True).vertices().hole(diameter)`
- Holes evenly spaced on a circle: `.faces(">Z").workplane().polarArray(radius, 0, 360, count).hole(diameter)`
- Combine bodies: `body_a.union(body_b)`
- Round vertical edges: `.edges("|Z").fillet(radius)`

# Examples
Two generic examples with different layouts, always shown. Pick the approach that matches the shape of YOUR part.

## Example 1: rectangular part, holes near the corners
Specification:
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

Script:
```python
import cadquery as cq

length = 120.0
width = 60.0
thickness = 8.0
hole_diameter = 5.0
hole_edge_offset = 10.0  # ASSUMED: 2x hole diameter keeps enough material to the edge

result = (
    cq.Workplane("XY")
    .box(length, width, thickness)
    .faces(">Z")
    .workplane()
    .rect(length - 2 * hole_edge_offset, width - 2 * hole_edge_offset, forConstruction=True)
    .vertices()
    .hole(hole_diameter)
)
```

## Example 2: round part, center hole and holes around it
Specification:
{
  "part_type": "round_flange",
  "description": "Round flange with a center hole and four holes around it",
  "units": "mm",
  "parameters": {"diameter": 80, "thickness": 10},
  "features": [
    {"type": "center_hole", "count": 1, "parameters": {"diameter": 20}, "location": "center"},
    {"type": "through_hole", "count": 4, "parameters": {"diameter": 8}, "location": "evenly spaced around the center"}
  ],
  "missing_parameters": ["diameter of the circle the four holes lie on"],
  "assumptions": []
}

Script:
```python
import cadquery as cq

diameter = 80.0
thickness = 10.0
center_hole_diameter = 20.0
hole_diameter = 8.0
hole_count = 4
hole_circle_diameter = 50.0  # ASSUMED: halfway between the center hole and the outer edge

result = (
    cq.Workplane("XY")
    .circle(diameter / 2)
    .extrude(thickness)
    .faces(">Z")
    .workplane()
    .hole(center_hole_diameter)
    .faces(">Z")
    .workplane()
    .polarArray(hole_circle_diameter / 2, 0, 360, hole_count)
    .hole(hole_diameter)
)
```

# Recipes for similar parts
Working scripts retrieved because they are similar to this part, in addition to the examples above. They are references, not the answer.

{recipes}

# CadQuery API reference
Real signatures from the installed CadQuery version. Use only these arguments.

{api_reference}

# Specification
{specification}

Script:
