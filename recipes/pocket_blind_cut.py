"""
title: Block with a pocket (blind cut)
description: Solid block with a rectangular pocket or recess of a given depth cut into the top face, with rounded inner corners, e.g. a tray, cavity or seat for another part.
tags: pocket, recess, cavity, blind cut, cutBlind, depth, tray, rectangular pocket, rounded inner corners, sketch
"""

import cadquery as cq

length = 80.0
width = 50.0
height = 20.0
pocket_length = 60.0
pocket_width = 30.0
pocket_depth = 12.0
pocket_corner_radius = 4.0

# The pocket outline is drawn as a sketch: a rectangle whose corners are rounded before cutting
result = (
    cq.Workplane("XY")
    .box(length, width, height)
    .faces(">Z")
    .workplane()
    .sketch()
    .rect(pocket_length, pocket_width)
    .vertices()
    .fillet(pocket_corner_radius)
    .finalize()
    .cutBlind(-pocket_depth)  # negative: cut down into the part
)
