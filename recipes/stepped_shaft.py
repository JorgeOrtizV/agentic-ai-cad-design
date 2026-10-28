"""
title: Stepped shaft (cylinders of different diameters)
description: Shaft, pin, axle or spindle made of coaxial cylinders of different diameters stacked along Z, each extruded on top of the previous one.
tags: shaft, pin, axle, spindle, stepped, cylinder, coaxial, shoulder, extrude on top
"""

import cadquery as cq

main_diameter = 20.0
main_length = 60.0
shoulder_diameter = 28.0
shoulder_length = 8.0
end_diameter = 12.0
end_length = 25.0

result = (
    cq.Workplane("XY")
    .circle(main_diameter / 2)
    .extrude(main_length)
    .faces(">Z")
    .workplane()
    .circle(shoulder_diameter / 2)
    .extrude(shoulder_length)  # stacking on top is intended here: each section adds length
    .faces(">Z")
    .workplane()
    .circle(end_diameter / 2)
    .extrude(end_length)
)
