"""
title: L-shaped bracket with holes in both legs
description: Angle bracket made of two flat plates (legs) joined at a right angle along one edge, with through holes in each leg. Each leg is built flat with its holes, then rotated and moved into place.
tags: l bracket, angle bracket, two legs, right angle, corner, plate, holes in each leg, rotate, translate, union
"""

import cadquery as cq

leg_1_length = 80.0  # horizontal leg, measured on the outside along X
leg_2_length = 40.0  # vertical leg, measured on the outside along Z
width = 40.0  # along Y, shared by both legs
thickness = 5.0
hole_diameter = 6.0
hole_spacing = 20.0  # ASSUMED: distance between the two holes of a leg, across the width

# Two holes per leg, side by side across the width, at the middle of the leg
hole_points = [(0, -hole_spacing / 2), (0, hole_spacing / 2)]

# Horizontal leg: lies on XY, from x = 0 to leg_1_length, z = 0 to thickness
leg_1 = (
    cq.Workplane("XY")
    .box(leg_1_length, width, thickness)
    .faces(">Z")
    .workplane()
    .pushPoints(hole_points)
    .hole(hole_diameter)
    .translate((leg_1_length / 2, 0, thickness / 2))
)

# Vertical leg: built flat the same way, then rotated -90 degrees around Y so its length points up along Z,
# ending at x = 0 to thickness, z = 0 to leg_2_length
leg_2 = (
    cq.Workplane("XY")
    .box(leg_2_length, width, thickness)
    .faces(">Z")
    .workplane()
    .pushPoints(hole_points)
    .hole(hole_diameter)
    .rotate((0, 0, 0), (0, 1, 0), -90)
    .translate((thickness / 2, 0, leg_2_length / 2))
)

# The legs overlap in the corner, so the union is one solid
result = leg_1.union(leg_2)
