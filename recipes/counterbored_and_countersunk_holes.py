"""
title: Plate with counterbored and countersunk holes
description: Flat plate with counterbored holes (for socket head screws) and countersunk holes (for flat head screws) at given positions.
tags: plate, counterbore, cbore, countersink, csk, screw holes, socket head, flat head, pushPoints
"""

import cadquery as cq

length = 100.0
width = 50.0
thickness = 10.0
hole_diameter = 6.6  # clearance hole for M6
counterbore_diameter = 11.0
counterbore_depth = 6.5
countersink_diameter = 12.5
countersink_angle = 90.0

base = cq.Workplane("XY").box(length, width, thickness)

result = (
    base.faces(">Z")
    .workplane()
    .pushPoints([(-30, 0), (30, 0)])
    .cboreHole(hole_diameter, counterbore_diameter, counterbore_depth)
    .faces(">Z")
    .workplane()
    .pushPoints([(0, 0)])
    .cskHole(hole_diameter, countersink_diameter, countersink_angle)
)
