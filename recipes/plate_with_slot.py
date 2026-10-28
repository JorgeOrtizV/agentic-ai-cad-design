"""
title: Plate with a through slot
description: Flat plate with an elongated slot (obround, oblong hole) cut through it, e.g. for adjustable mounting.
tags: plate, slot, obround, oblong hole, elongated hole, adjustable, cutThruAll
"""

import cadquery as cq

length = 80.0
width = 40.0
thickness = 6.0
slot_length = 30.0  # end to end
slot_width = 8.0

result = (
    cq.Workplane("XY")
    .box(length, width, thickness)
    .faces(">Z")
    .workplane()
    .slot2D(slot_length, slot_width, 0)  # angle 0: along X
    .cutThruAll()
)
