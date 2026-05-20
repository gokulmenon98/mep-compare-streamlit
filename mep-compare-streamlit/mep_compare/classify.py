"""
Classify change regions as foreground (MEP work) or background
(architectural backdrop) based on stroke width.

The principle: architectural backgrounds in MEP drawings are drawn
with the thinnest line weights so they recede visually, while MEP
linework, equipment, and tags use heavier weights. By measuring the
typical stroke width of differing pixels within each change region,
we can distinguish "the wall moved" from "a piece of ductwork was added"
without any per-project setup — the bimodal distribution of stroke
widths in MEP drawings makes the split self-calibrating.

This module has no opinion about what to DO with the classification —
it just produces a label per region. The annotation stage picks the
visual treatment (different color, dashed border, hide entirely, etc.).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable
import numpy as np
import cv2


@dataclass
class StrokeStats:
    """Stroke width measurement for one change region."""
    width: float       # estimated typical stroke width in pixels
    confidence: float  # 0-1, lower when too few sample pixels


def estimate_stroke_width(diff_mask: np.ndarray, x: int, y: int, w: int, h: int) -> StrokeStats:
    """Estimate the typical stroke width of changed pixels in a rectangular region.

    Algorithm:
      1. Crop the binary diff mask to the region.
      2. Distance transform: each foreground pixel's value is its
         distance to the nearest non-changed pixel.
      3. The upper percentile of those distances, doubled, approximates
         the typical stroke half-width × 2 = stroke width. We use the
         75th percentile rather than median because distance values are
         small near stroke edges and peak at stroke centers — the upper
         percentile represents centers, which is what we want to measure.

    `diff_mask` must be the PRE-DILATION binary mask. Measuring on the
    dilated mask would give widths inflated by the dilation kernel size
    and destroy the foreground/background distinction.
    """
    H, W = diff_mask.shape
    # Clip to mask bounds (regions are post-dilation, may extend slightly).
    x1 = max(0, x);  y1 = max(0, y)
    x2 = min(W, x + w); y2 = min(H, y + h)
    sub = diff_mask[y1:y2, x1:x2]

    if sub.size == 0:
        return StrokeStats(width=0.0, confidence=0.0)

    positive_count = int((sub > 0).sum())
    if positive_count < 10:
        # Too few changed pixels for a reliable measurement.
        return StrokeStats(width=0.0, confidence=0.0)

    # distanceTransform expects uint8 input; we ensure the mask is binary 0/255 or 0/1.
    bin_input = (sub > 0).astype(np.uint8)
    dist = cv2.distanceTransform(bin_input, cv2.DIST_L2, 3)
    positive = dist[bin_input > 0]

    width = float(np.percentile(positive, 75)) * 2.0
    # Confidence saturates at 100+ changed pixels; below that, less reliable.
    confidence = min(1.0, positive_count / 100.0)

    return StrokeStats(width=width, confidence=confidence)


def auto_threshold(stroke_widths: Iterable[float]) -> float:
    """Find the stroke width threshold separating background from foreground.

    Uses Otsu's method on the (outlier-capped) histogram of stroke widths.
    Otsu maximizes between-class variance — finds the value that best
    splits the data into two groups whose internal variance is minimized
    relative to the variance between their means.

    Why Otsu over k-means for this problem: in MEP drawings the
    foreground/background distribution is often heavily skewed —
    hundreds of thin background regions, only a handful of thick MEP
    regions, plus a couple of very-thick outliers (filled equipment
    symbols). K-means at the midpoint between cluster means gets pulled
    upward by the outliers; Otsu finds the actual valley in the
    histogram, which is where we want the split.

    Robustness measures:

    1. **Cap outliers at 20 px.** Filled-shape change regions can
       produce distance-transform values of 100+ px. Capping keeps the
       histogram focused on the line-width band we actually care about.
    2. **Hard floor of 3.0 px.** Strokes below 3 px at any reasonable
       DPI are sub-line-weight artifacts. Always treat as background
       regardless of what the histogram says. Without this, a drawing
       with NO real background-style changes would auto-calibrate to a
       split inside the foreground band and incorrectly demote real
       changes.

    Edge cases handled:
      - Too few samples (<5): return the floor of 3.0 px.
      - All strokes roughly equal: return a threshold below the minimum
        so everything classifies as foreground.
    """
    HARD_FLOOR = 3.0
    OUTLIER_CAP = 20.0

    samples = [w for w in stroke_widths if w > 0]
    if len(samples) < 5:
        return HARD_FLOOR

    arr = np.array([min(w, OUTLIER_CAP) for w in samples], dtype=np.float32)
    if float(arr.max() - arr.min()) < 1.5:
        return float(arr.min()) - 0.1

    # Detect unimodal distributions before running Otsu. If the data is
    # tightly clustered around a single mode (low coefficient of variation),
    # there is no real bimodal split — Otsu will find one anyway by
    # picking an arbitrary middle point, which would incorrectly demote
    # half the foreground regions to background. Treat as all-foreground.
    arr_mean = float(arr.mean())
    arr_std = float(arr.std())
    cv = arr_std / max(arr_mean, 0.1)
    if cv < 0.3:
        return float(arr.min()) - 0.1

    # Otsu's method on a histogram.
    # Bin count scales with sample size; minimum 10, capped at 64 to
    # keep things stable on smaller datasets.
    n_bins = int(np.clip(np.sqrt(len(samples)) * 2, 10, 64))
    hist, edges = np.histogram(arr, bins=n_bins, range=(arr.min(), arr.max()))
    centers = (edges[:-1] + edges[1:]) / 2.0
    total = hist.sum()

    if total == 0:
        return HARD_FLOOR

    total_weighted = float((hist * centers).sum())
    best_var = -1.0
    best_threshold = float(np.median(arr))
    cum_count = 0
    cum_weighted = 0.0

    # Iterate threshold positions between bins; pick the one that
    # maximizes between-class variance.
    for i in range(len(hist) - 1):
        cum_count += int(hist[i])
        cum_weighted += float(hist[i] * centers[i])
        w0 = cum_count
        w1 = total - w0
        if w0 == 0 or w1 == 0:
            continue
        m0 = cum_weighted / w0
        m1 = (total_weighted - cum_weighted) / w1
        between_var = w0 * w1 * (m0 - m1) ** 2
        if between_var > best_var:
            best_var = between_var
            best_threshold = float(edges[i + 1])

    return max(HARD_FLOOR, best_threshold)


def classify_regions(regions, threshold: float) -> None:
    """Set is_foreground on each region by comparing stroke_width to threshold.

    Mutates regions in place. A region with stroke_width=0 (couldn't be
    measured) defaults to foreground — the conservative choice for a
    QA tool: better to mark too much than to miss something.
    """
    for r in regions:
        if r.stroke_width <= 0:
            r.is_foreground = True  # unmeasured → don't hide
        else:
            r.is_foreground = r.stroke_width >= threshold
