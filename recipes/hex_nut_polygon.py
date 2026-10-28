"""
title: Hexagonal part with a center hole (nut, standoff)
description: Regular polygon prism such as a hex nut, hex standoff or spacer, with a center through hole. The polygon size is the diameter of its circumscribed circle.
tags: hexagon, hex nut, standoff, polygon, prism, across corners, across flats, center hole
"""

import math

import cadquery as cq

across_flats = 13.0  # distance between parallel sides, the usual hex size
height = 6.5
hole_diameter = 8.0

# polygon() takes the diameter across the corners: across_flats / cos(30 degrees)
across_corners = across_flats / math.cos(math.radians(30))

result = cq.Workplane("XY").polygon(6, across_corners).extrude(height).faces(">Z").workplane().hole(hole_diameter)
