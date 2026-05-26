"""
Directional difference detection.

The previous version computed `abs(V1 - V2)` and treated everything as
generic "changes." That answers the wrong question. An MEP reviewer
doesn't need to know "what differs" — they need to know "what was
added" and "what was removed." Those two are visually and
semantically distinct events.

This module computes them separately:

  - ADDED: pixels that are ink in V2 but were blank in V1.
    Something new shows up here.
  - REMOVED: pixels that were ink in V1 but are blank in V2.
    Something used to be here.

In a grayscale image, ink = low value (dark, near 0), blank = high
value (light, near 255). So:

  added_pixels  = where v1 is light AND v2 is dark = (v1 - v2) > threshold
  removed_pixels = where v1 is dark AND v2 is light = (v2 - v1) > threshold

cv2.subtract(a, b) clamps negative results to 0, which is exactly what
we want for directional differencing.

Pipeline per direction:
  1. Directional subtract + binary threshold.
  2. Mask out the title block region.
  3. Morphological dilation to merge nearby specks into a single region.
  4. Connected components → regions.
  5. Filter by minimum area.

Defaults are aggressive (high min_area, high dilation, high pixel
threshold) because real revisions are larger than registration noise,
and the old defaults swamped pages with thousands of noise regions.
The user can relax these in Advanced Settings.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
import numpy as np
import cv2


# Defaults tuned for typical 200 DPI MEP drawings. The driving principle:
# real revisions are bigger than registration / anti-aliasing noise.
DEFAULT_PIXEL_THRESHOLD = 60       # 0-255; diff value above this counts as a hit
DEFAULT_DILATION_PX = 25            # merge changes within this many pixels
DEFAULT_MIN_AREA_PX = 1500          # ignore noise smaller than this
DEFAULT_BOX_MARGIN_PX = 8           # outward padding on each bounding box

ChangeKind = Literal["added", "removed"]


@dataclass
class ChangeRegion:
    """A cluster of pixels that changed in one direction (added or removed).

    All coordinates are in pixels relative to the V1 image frame (which
    is also the aligned-V2 frame).

    Fields:
      kind: 'added' (ink in V2 only) or 'removed' (ink in V1 only).
      x, y, w, h: the padded bounding box used for annotation placement.
      core_x, core_y, core_w, core_h: the un-padded extent — the actual
        bounding box of the changed pixels. Used when more precise
        markup is needed than the padded box.
      area: count of changed pixels in the region (not the box area).
    """
    kind: ChangeKind
    x: int
    y: int
    w: int
    h: int
    area: int
    core_x: int = 0
    core_y: int = 0
    core_w: int = 0
    core_h: int = 0


def _detect_one_direction(
    diff_image: np.ndarray,
    kind: ChangeKind,
    pixel_threshold: int,
    dilation_px: int,
    min_area_px: int,
    box_margin_px: int,
    title_block_region: tuple[float, float, float, float],
) -> list[ChangeRegion]:
    """Run the threshold → mask → dilate → component pipeline on a single
    directional difference image, tagging all resulting regions with `kind`.
    """
    h, w = diff_image.shape
    _, binary = cv2.threshold(diff_image, pixel_threshold, 255, cv2.THRESH_BINARY)

    # Mask out the title block. Revision triangles and stamp updates in
    # the title block are real changes but they aren't the
    # un-clouded-changes-in-the-drawing-body that this tool exists to find.
    x1, y1, x2, y2 = title_block_region
    binary[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)] = 0

    # Dilate to merge nearby specks into single regions per real revision.
    k = max(3, dilation_px | 1)  # force odd for a well-defined center
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    dilated = cv2.dilate(binary, kernel, iterations=1)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(dilated, connectivity=8)

    regions: list[ChangeRegion] = []
    for label in range(1, num_labels):  # 0 is the background
        x, y, ww, hh, area = stats[label]
        if area < min_area_px:
            continue
        x_pad = max(0, x - box_margin_px)
        y_pad = max(0, y - box_margin_px)
        x2_pad = min(w, x + ww + box_margin_px)
        y2_pad = min(h, y + hh + box_margin_px)
        regions.append(ChangeRegion(
            kind=kind,
            x=x_pad,
            y=y_pad,
            w=x2_pad - x_pad,
            h=y2_pad - y_pad,
            area=int(area),
            core_x=int(x),
            core_y=int(y),
            core_w=int(ww),
            core_h=int(hh),
        ))
    return regions


def detect_changes(
    v1_image: np.ndarray,
    v2_aligned: np.ndarray,
    title_block_region: tuple[float, float, float, float] = (0.85, 0.85, 1.00, 1.00),
    pixel_threshold: int = DEFAULT_PIXEL_THRESHOLD,
    dilation_px: int = DEFAULT_DILATION_PX,
    min_area_px: int = DEFAULT_MIN_AREA_PX,
    box_margin_px: int = DEFAULT_BOX_MARGIN_PX,
) -> list[ChangeRegion]:
    """Find clusters of ink that were added in V2 or removed from V1.

    Returns a combined list with each region tagged as 'added' or 'removed'.
    """
    if v1_image.shape != v2_aligned.shape:
        raise ValueError(f"shape mismatch: {v1_image.shape} vs {v2_aligned.shape}")

    # Directional diffs. cv2.subtract clamps negative results to 0.
    # added: pixels darker in V2 than in V1 (V2 has ink where V1 was blank)
    added_diff = cv2.subtract(v1_image, v2_aligned)
    # removed: pixels darker in V1 than in V2 (V1 had ink where V2 is blank)
    removed_diff = cv2.subtract(v2_aligned, v1_image)

    added_regions = _detect_one_direction(
        added_diff, "added",
        pixel_threshold, dilation_px, min_area_px, box_margin_px,
        title_block_region,
    )
    removed_regions = _detect_one_direction(
        removed_diff, "removed",
        pixel_threshold, dilation_px, min_area_px, box_margin_px,
        title_block_region,
    )

    return added_regions + removed_regions
