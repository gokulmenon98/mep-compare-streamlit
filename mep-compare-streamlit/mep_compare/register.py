"""
Image registration: align a v2 page image onto its v1 counterpart so
that pixel-wise diffing is meaningful.

Why this is needed: even when both PDFs come from the same Revit model
with no real geometry changes, the rendered pixel grids will not be
identical. Different export DPI, slight scale changes, anti-aliasing,
or a rev cloud nudging the print extents will misalign the two pages.
Without registration, every pixel reads as "changed" and the tool
produces nothing useful.

Approach:
  1. Mask the title block in both images. Revision marks in the title
     block are real changes but they pull feature matching off the
     drawing body. We compute alignment from the drawing only.
  2. Detect ORB features in both masked images.
  3. Match descriptors with a brute-force matcher + Lowe's ratio test.
  4. Estimate homography with RANSAC.
  5. Inlier ratio gates whether we trust the result. Below threshold,
     the page is flagged as substantial rework and skipped — registering
     a wholesale-redrawn sheet would produce a garbage diff.
  6. Warp the FULL v2 image (not the masked one) using the homography
     so the title block area is still present in the output for context.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
import cv2


# Tuning knobs. Defaults chosen for typical large-format MEP sheets at 200 DPI.
ORB_FEATURES = 8000          # generous; drawings have lots of corner-like features
RATIO_TEST = 0.75            # Lowe's ratio threshold for "good" matches
MIN_GOOD_MATCHES = 50        # below this, registration fails outright
MIN_INLIER_RATIO = 0.20      # below this, declare substantial rework


@dataclass
class RegistrationResult:
    """Result of attempting to align v2 onto v1."""
    aligned_v2: Optional[np.ndarray]  # warped v2 image, same shape as v1, or None
    homography: Optional[np.ndarray]  # 3x3 transform, or None
    inlier_ratio: float
    good_matches: int
    reason: str                       # "ok", "too_few_matches", "low_inliers", "homography_failed"

    @property
    def ok(self) -> bool:
        return self.aligned_v2 is not None


def _mask_region(
    img: np.ndarray,
    region: tuple[float, float, float, float],
) -> np.ndarray:
    """Return a copy of img with a fractional region zeroed out.

    Zeroing (rather than cropping) preserves spatial coordinates,
    which matters because we need the homography to be valid for the
    full image when we later warp the unmasked v2.
    """
    out = img.copy()
    h, w = img.shape[:2]
    x1, y1, x2, y2 = region
    out[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)] = 0
    return out


def register(
    v1_image: np.ndarray,
    v2_image: np.ndarray,
    title_block_region: tuple[float, float, float, float] = (0.90, 0.00, 1.00, 1.00),
) -> RegistrationResult:
    """Align v2_image onto v1_image and return the warped v2.

    Inputs should be grayscale uint8. Output `aligned_v2` (when present)
    has the same shape as v1_image. The title_block_region is masked
    only for feature detection, not for the final warp.
    """
    v1_masked = _mask_region(v1_image, title_block_region)
    v2_masked = _mask_region(v2_image, title_block_region)

    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    kp1, des1 = orb.detectAndCompute(v1_masked, None)
    kp2, des2 = orb.detectAndCompute(v2_masked, None)

    if des1 is None or des2 is None or len(kp1) < MIN_GOOD_MATCHES or len(kp2) < MIN_GOOD_MATCHES:
        return RegistrationResult(None, None, 0.0, 0, "too_few_features")

    # Brute-force matcher with Hamming distance (ORB descriptors are binary).
    # knnMatch + ratio test rejects ambiguous matches that would corrupt the
    # homography estimation.
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn = matcher.knnMatch(des1, des2, k=2)
    good = [m for m, n in knn if m.distance < RATIO_TEST * n.distance]

    if len(good) < MIN_GOOD_MATCHES:
        return RegistrationResult(None, None, 0.0, len(good), "too_few_matches")

    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    # We want the transform that maps v2 -> v1 (so we can warp v2 into v1's frame).
    # findHomography with src=v2_pts, dst=v1_pts gives exactly that.
    H, mask = cv2.findHomography(dst_pts, src_pts, cv2.RANSAC, ransacReprojThreshold=5.0)
    if H is None:
        return RegistrationResult(None, None, 0.0, len(good), "homography_failed")

    inliers = int(mask.sum())
    inlier_ratio = inliers / len(good)

    if inlier_ratio < MIN_INLIER_RATIO:
        return RegistrationResult(None, H, inlier_ratio, len(good), "low_inliers")

    h, w = v1_image.shape[:2]
    aligned = cv2.warpPerspective(
        v2_image, H, (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,  # white background for areas outside v2 — looks like blank paper, not a change
    )

    return RegistrationResult(aligned, H, inlier_ratio, len(good), "ok")
