"""
title: Open box or enclosure with constant wall thickness
description: Hollow box open at the top (enclosure, housing, tray, container, cup) made by shelling a solid box with a given wall thickness.
tags: enclosure, housing, box, tray, container, hollow, shell, wall thickness, open top
"""

import cadquery as cq

length = 100.0
width = 60.0
height = 40.0
wall_thickness = 2.5

result = (
    cq.Workplane("XY")
    .box(length, width, height)
    .faces(">Z")  # this face is removed, leaving the box open at the top
    .shell(-wall_thickness)  # negative: the walls grow inwards, outer size stays as given
)
