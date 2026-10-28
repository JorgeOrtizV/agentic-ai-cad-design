The numbered CadQuery script below was written for the given specification, but measuring the part it builds revealed problems. Find the lines that cause them and fix only those lines.

# How to answer
Answer with JSON:
- "diagnosis": for each problem, the line that causes it and why, in one or two sentences.
- "edits": the changes that fix the problems. Each edit replaces the lines `first_line` to `last_line` (both included, numbered as in the script below) with `replacement`: the new code for those lines, without line numbers, keeping the indentation. An empty `replacement` deletes the lines. To add code, replace a line with that same line plus the new lines.

# Rules
- Start from "Where the problems come from": it names the lines whose operations change the part in the wrong way, measured while the script ran. Fix those lines.
- The values in the specification are correct. NEVER change a dimension from the specification to match a measured value: the problems come from the modelling operations, so fix the operations.
- Typical causes:
  - the part is too thick or too tall: an extra `.extrude(...)` adds material on top
  - a cut removes no material: its positions are outside the body (for holes around a center use `polarArray`)
  - holes break out through the edge: they are placed too close to the edge, or cut in a body that is too small
  - a hole is missing entirely: it was made with `.circle(...).extrude(...)`, which adds material, instead of `.hole(...)`
  - holes disappear at a `.union(...)`: the other body fills them. Join the bodies first and cut the holes last, on the joined solid
  - a union adds no material: the joined body lies inside the other one. Move or rotate it to where it belongs
- Change as few lines as possible and keep every line that already works. Edits must not overlap.
- Returning the script unchanged is not a fix: it would fail the same way.
- If a problem shows a method or argument that does not exist, use the real signature given with it. Do not guess another name.
- Keep all the rules of the original task: only `cadquery` and `math` imports, dimensions as named variables, `# ASSUMED:` comments for chosen values, the final single solid in `result`, no export/show/print/file access, no checks or measurements in the script.

# Recipes for similar parts
Working scripts for parts similar to this one. If the problems show the part has the wrong shape, follow the approach of a recipe.

{recipes}

# CadQuery API reference
Real signatures from the installed CadQuery version. Use only these arguments.

{api_reference}

# Specification
{specification}

# Problems found by measuring the part
{problems}

# Where the problems come from
{locations}

# What each line did to the part
{trace}

# Script (the numbers before `|` are line numbers, not code)
{numbered_code}
