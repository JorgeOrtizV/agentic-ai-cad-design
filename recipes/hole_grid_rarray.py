"""
title: Plate with a grid or row of holes
description: Flat plate with holes arranged in a regular rectangular grid or a single straight row (perforated plate, breadboard, mounting rail).
tags: plate, grid of holes, row of holes, pattern, rarray, rectangular array, perforated, spacing, pitch
"""

import cadquery as cq

length = 100.0
width = 60.0
thickness = 4.0
hole_diameter = 5.0
x_spacing = 20.0
y_spacing = 20.0
x_count = 4
y_count = 2  # use 1 for a single row

result = (
    cq.Workplane("XY")
    .box(length, width, thickness)
    .faces(">Z")
    .workplane()
    .rarray(x_spacing, y_spacing, x_count, y_count)  # centered on the workplane origin
    .hole(hole_diameter)
)
