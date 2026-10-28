"""
title: Block with rounded vertical edges and chamfered top
description: Rectangular block with fillets (rounded edges) on the vertical edges and a chamfer on the top edges. Shows how to select edges for fillet and chamfer.
tags: block, box, fillet, rounded edges, rounded corners, chamfer, bevel, edge selection
"""

import cadquery as cq

length = 60.0
width = 40.0
height = 20.0
corner_radius = 5.0
top_chamfer = 1.5

result = (
    cq.Workplane("XY")
    .box(length, width, height)
    .edges("|Z")  # the 4 vertical edges
    .fillet(corner_radius)
    .faces(">Z")  # top face, then its edges
    .edges()
    .chamfer(top_chamfer)
)
