"""
title: Tube or bushing (hollow cylinder)
description: Hollow cylinder such as a pipe, tube, bushing, sleeve or ring, made by extruding two concentric circles; the inner circle becomes the bore.
tags: tube, pipe, bushing, sleeve, ring, washer, hollow cylinder, inner diameter, outer diameter, concentric circles
"""

import cadquery as cq

outer_diameter = 30.0
inner_diameter = 20.0
length = 50.0

# Two concentric circles extrude to a ring section: the area between them
result = cq.Workplane("XY").circle(outer_diameter / 2).circle(inner_diameter / 2).extrude(length)
