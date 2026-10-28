"""
title: Extruded profile (U-channel, T or angle section)
description: Long part with a constant cross-section such as a U-channel, C-channel, T-profile or rail, made by drawing the cross-section as a closed polyline and extruding it.
tags: profile, extrusion, cross-section, channel, u channel, c channel, t profile, rail, polyline, constant section
"""

import cadquery as cq

channel_width = 40.0
channel_height = 25.0
wall_thickness = 3.0
length = 150.0

# Cross-section of a U-channel open at the top, drawn in the XZ plane, then extruded along Y
section = [
    (0, 0),
    (channel_width, 0),
    (channel_width, channel_height),
    (channel_width - wall_thickness, channel_height),
    (channel_width - wall_thickness, wall_thickness),
    (wall_thickness, wall_thickness),
    (wall_thickness, channel_height),
    (0, channel_height),
]

result = cq.Workplane("XZ").polyline(section).close().extrude(length)
