"""
title: Two bodies joined (base with a boss or rib)
description: Part made of two bodies joined with union, e.g. a base plate with a cylindrical boss, post or rib standing on it. The second body is placed on the top face of the first so they touch.
tags: union, boss, post, rib, standoff on plate, combine bodies, join, base plate, place on top face
"""

import cadquery as cq

base_length = 60.0
base_width = 40.0
base_thickness = 6.0
boss_diameter = 16.0
boss_height = 20.0
boss_hole_diameter = 6.0

base = cq.Workplane("XY").box(base_length, base_width, base_thickness)

# A workplane on the top face starts exactly where the base ends, so the boss touches it
result = (
    base.faces(">Z")
    .workplane()
    .circle(boss_diameter / 2)
    .extrude(boss_height)
    .faces(">Z")
    .workplane()
    .hole(boss_hole_diameter)
)
