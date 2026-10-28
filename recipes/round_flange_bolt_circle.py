"""
title: Round flange with center hole and bolt circle
description: Round disc (flange, spacer, adapter plate) with a center hole or bore and several holes evenly spaced on a bolt circle (pitch circle) around the center.
tags: disc, round, cylinder, flange, spacer, center bore, bolt circle, pitch circle, polar array, holes around center
"""

import cadquery as cq

diameter = 80.0
thickness = 10.0
center_hole_diameter = 20.0
bolt_hole_diameter = 8.0
bolt_hole_count = 4
bolt_circle_diameter = 60.0  # ASSUMED: halfway between the center hole and the outer edge

result = (
    cq.Workplane("XY")
    .circle(diameter / 2)
    .extrude(thickness)
    .faces(">Z")
    .workplane()
    .hole(center_hole_diameter)
    .faces(">Z")
    .workplane()
    # polarArray takes the RADIUS of the bolt circle and places `count` points evenly around the center
    .polarArray(bolt_circle_diameter / 2, 0, 360, bolt_hole_count)
    .hole(bolt_hole_diameter)
)
