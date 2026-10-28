"""
title: Part revolved from a 2D profile
description: Rotationally symmetric part (pulley, knob, wheel, nozzle, cap) made by drawing its half cross-section as a closed polyline and revolving it 360 degrees around an axis.
tags: revolve, rotational symmetry, pulley, knob, wheel, nozzle, cross-section, profile, polyline, groove
"""

import cadquery as cq

outer_radius = 30.0
hub_radius = 10.0
width = 20.0
groove_depth = 6.0

# Half cross-section in the XY plane (X = radius, Y = height); the axis of revolution is the Y axis
profile = [
    (hub_radius, 0),
    (outer_radius, 0),
    (outer_radius - groove_depth, width / 2),  # V groove in the middle of the rim
    (outer_radius, width),
    (hub_radius, width),
]

result = (
    cq.Workplane("XY")
    .polyline(profile)
    .close()
    .revolve(360, (0, 0, 0), (0, 1, 0))  # axis from (0, 0) to (0, 1) in workplane coordinates: the Y axis
)
