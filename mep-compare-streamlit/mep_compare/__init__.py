"""
mep-compare: detect visual differences between two MEP drawing sets,
output a marked-up v2 PDF highlighting changes.

Pipeline:
  1. Extract per-page drawing numbers from a calibrated title-block region
  2. Match sheets across the two sets by drawing number
  3. For each matched pair: render, register, diff, cluster changes
  4. Annotate the v2 PDF with bounding boxes around change regions
"""
__version__ = "0.1.0"
