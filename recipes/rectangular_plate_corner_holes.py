"""
title: Rectangular plate with holes near the corners
description: Flat rectangular plate (mounting plate, base plate, cover) with through holes placed at the corners of a rectangle inset from the edges.
tags: plate, rectangular, box, corner holes, mounting holes, through hole, rect vertices
"""

import cadquery as cq

length = 120.0
width = 60.0
thickness = 8.0
hole_diameter = 5.0
hole_edge_offset = 10.0  # ASSUMED: distance from the hole centers to the plate edges

result = (
    cq.Workplane("XY")
    .box(length, width, thickness)
    .faces(">Z")
    .workplane()
    # A construction rectangle only provides its 4 corners as hole positions
    .rect(length - 2 * hole_edge_offset, width - 2 * hole_edge_offset, forConstruction=True)
    .vertices()
    .hole(hole_diameter)
)
