"""
Drawing number identification.

Strategy: the user calibrates a rectangular region (as fractions of page
width/height) where the drawing number lives in the title block. For each
page, we clip the text layer to that region and regex-match the expected
format. Vector PDFs from Revit/AutoCAD make this near-perfectly reliable
without any OCR.

If a page yields no match, we widen the search to the right strip and
finally the whole page. If multiple matches appear, we take the last —
title blocks conventionally place the drawing number at the bottom of
the block, after the project name, sheet title, etc.

Failure to extract is reported, not silently guessed.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Optional
import re
import fitz


# Default region: rightmost 8% of page width, bottom 8% of height.
# Calibrated against the sample Alnylam set; works on right-edge title
# block layouts where the drawing number sits at the bottom of the strip.
DEFAULT_CALIBRATION = (0.92, 0.92, 1.00, 1.00)

# Drawing number patterns. Order matters: tried in sequence.
# - Standard: 04-M-01-130, 04-M-001-100, 04-M-01-131A
# - Permissive fallback for non-standard formats (letters/digits/dashes,
#   reasonable length, not pure numbers or pure letters).
PATTERN_STANDARD = re.compile(r"\b(\d{2}-[A-Z]-\d{2,3}-\d{3}[A-Z]?)\b")
PATTERN_PERMISSIVE = re.compile(r"\b([A-Z]{1,3}-?\d{2,4}[A-Z0-9-]*)\b")


@dataclass
class SheetID:
    """The drawing number for one page, with provenance for debugging."""
    page_index: int               # zero-based
    drawing_number: Optional[str] # None if extraction failed
    source_region: str            # which region produced the match
    raw_text: str                 # text from the calibrated region (truncated)


def _clip_text(page: fitz.Page, region: tuple[float, float, float, float]) -> str:
    """Extract text from a fractional region of the page."""
    x1, y1, x2, y2 = region
    w, h = page.rect.width, page.rect.height
    clip = fitz.Rect(x1 * w, y1 * h, x2 * w, y2 * h)
    return page.get_text("text", clip=clip)


def _match(text: str, patterns: Iterable[re.Pattern]) -> Optional[str]:
    """Try each pattern; return the LAST match from the first pattern
    that finds anything. Last-match handles cases where the title block
    references other drawings before the actual sheet number."""
    for pattern in patterns:
        matches = pattern.findall(text)
        if matches:
            return matches[-1]
    return None


def identify_sheet(
    page: fitz.Page,
    calibration: tuple[float, float, float, float] = DEFAULT_CALIBRATION,
    use_permissive_fallback: bool = False,
) -> SheetID:
    """Extract the drawing number from one page.

    Tries the calibrated region first; if that fails, widens to the full
    right strip; if that fails, scans the whole page. Each widening is a
    separate attempt rather than a single broad search, because the broader
    the search the higher the chance of grabbing a referenced drawing
    number from the body of the sheet instead of the actual sheet ID.
    """
    patterns = [PATTERN_STANDARD]
    if use_permissive_fallback:
        patterns.append(PATTERN_PERMISSIVE)

    attempts = [
        ("calibrated", calibration),
        ("right_strip", (0.85, 0.00, 1.00, 1.00)),
        ("full_page",   (0.00, 0.00, 1.00, 1.00)),
    ]

    for label, region in attempts:
        text = _clip_text(page, region)
        dwg = _match(text, patterns)
        if dwg:
            return SheetID(
                page_index=page.number,
                drawing_number=dwg,
                source_region=label,
                raw_text=text[:200],
            )

    # All attempts failed.
    return SheetID(
        page_index=page.number,
        drawing_number=None,
        source_region="none",
        raw_text=_clip_text(page, calibration)[:200],
    )


def identify_all(
    doc: fitz.Document,
    calibration: tuple[float, float, float, float] = DEFAULT_CALIBRATION,
    use_permissive_fallback: bool = False,
) -> list[SheetID]:
    """Identify every page in a document."""
    return [
        identify_sheet(page, calibration, use_permissive_fallback)
        for page in doc
    ]
