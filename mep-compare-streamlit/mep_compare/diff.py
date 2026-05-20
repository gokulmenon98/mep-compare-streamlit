"""
Difference detection.

Given two co-registered grayscale images (v1 and aligned v2), produce a
list of bounding boxes around regions of meaningful pixel-level change.

Pipeline:
  1. Absolute difference + binary threshold. Threshold needs to be loose
     enough to ignore anti-aliasing jitter but tight enough to catch real
     line work changes.
  2. Morphological dilation. A revision typically affects many nearby
     pixels (a new symbol, a moved line). Dilating before contouring
     merges them into a single region per real change, instead of
     producing dozens of specks per revision.
  3. Connected component analysis to get regions.
  4. Filter by minimum area to drop noise (stray dots, JPEG artifacts).
  5. Mask out the title block — revision stamps there are legitimate
     changes but they aren't the unclouded-changes-in-the-drawing-body
     that this tool exists to find. They'd flood the output.

Outputs bounding boxes in pixel coordinates of the v1 image. Caller is
responsible for converting these to PDF coordinates for annotation.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import cv2

from .classify import estimate_stroke_width


# Defaults. All exposed via the CLI for tuning per project.
DEFAULT_PIXEL_THRESHOLD = 40       # 0-255; pixel diff above this counts as changed
DEFAULT_DILATION_PX = 15           # merge changes within this many pixels of each other
DEFAULT_MIN_AREA_PX = 200          # ignore regions smaller than this (noise)
DEFAULT_BOX_MARGIN_PX = 10         # pad each bounding box outward by this amount


@dataclass
class ChangeRegion:
    """A bounding box around a region of detected change. Pixel coords
    in the v1 image frame (which is also the aligned-v2 frame).

    `x, y, w, h` is the padded bounding box (includes box_margin_px) — what
    we draw as the outer outline.
    `core_x, core_y, core_w, core_h` is the actual extent of the change
    before padding — used for the highlighter layer in layered markup mode.
    `stroke_width` and `is_foreground` are populated by the classify
    stage. They have defaults so older callers that don't classify still
    get sensible behavior — unclassified regions are treated as foreground."""
    x: int
    y: int
    w: int
    h: int
    area: int  # number of changed pixels inside the box (not box area)
    core_x: int = 0   # un-padded change extent (defaults to padded if not set)
    core_y: int = 0
    core_w: int = 0
    core_h: int = 0
    stroke_width: float = 0.0  # typical stroke width of changed pixels (px); 0 = unmeasured
    is_foreground: bool = True  # False = thin/background, True = thick/MEP-work or unclassified


def detect_changes(
    v1_image: np.ndarray,
    v2_aligned: np.ndarray,
    title_block_region: tuple[float, float, float, float] = (0.90, 0.00, 1.00, 1.00),
    pixel_threshold: int = DEFAULT_PIXEL_THRESHOLD,
    dilation_px: int = DEFAULT_DILATION_PX,
    min_area_px: int = DEFAULT_MIN_AREA_PX,
    box_margin_px: int = DEFAULT_BOX_MARGIN_PX,
) -> list[ChangeRegion]:
    """Find regions where v2 differs from v1.

    Both inputs must be the same shape, grayscale uint8.
    """
    if v1_image.shape != v2_aligned.shape:
        raise ValueError(f"shape mismatch: {v1_image.shape} vs {v2_aligned.shape}")

    # Absolute difference. Both are grayscale so this is a simple subtraction
    # with sign handling.
    diff = cv2.absdiff(v1_image, v2_aligned)
    _, binary = cv2.threshold(diff, pixel_threshold, 255, cv2.THRESH_BINARY)

    # Mask out the title block — its revision marks are legitimate changes
    # but aren't what this tool is for.
    h, w = binary.shape
    x1, y1, x2, y2 = title_block_region
    binary[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)] = 0

    # Dilate so nearby specks merge into single regions per real change.
    # Odd kernel size so it has a well-defined center.
    k = max(3, dilation_px | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    dilated = cv2.dilate(binary, kernel, iterations=1)

    # connectedComponentsWithStats is faster than findContours for this and
    # gives us pixel area directly per component.
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(dilated, connectivity=8)

    regions: list[ChangeRegion] = []
    # Label 0 is the background — skip it.
    for label in range(1, num_labels):
        x, y, ww, hh, area = stats[label]
        if area < min_area_px:
            continue
        # Apply margin while clamping to image bounds.
        x_pad = max(0, x - box_margin_px)
        y_pad = max(0, y - box_margin_px)
        x2_pad = min(w, x + ww + box_margin_px)
        y2_pad = min(h, y + hh + box_margin_px)

        # Measure stroke width on the PRE-DILATION mask. Using the dilated
        # mask would inflate widths by the dilation kernel size and erase
        # the foreground/background distinction.
        # We measure within the un-padded region to keep the sample tight.
        stroke = estimate_stroke_width(binary, int(x), int(y), int(ww), int(hh))

        regions.append(ChangeRegion(
            x=x_pad,
            y=y_pad,
            w=x2_pad - x_pad,
            h=y2_pad - y_pad,
            area=int(area),
            core_x=int(x),
            core_y=int(y),
            core_w=int(ww),
            core_h=int(hh),
            stroke_width=stroke.width,
            # is_foreground defaults True; classify.classify_regions() sets it properly.
        ))

    return regions
