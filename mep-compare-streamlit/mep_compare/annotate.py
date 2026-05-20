"""
Output annotation: draw change-region boxes onto the v2 PDF with
visual hierarchy that separates real MEP changes from architectural
background shifts.

Two styles:
  - Foreground (MEP work, thick strokes): magenta, solid border, 2.0 pt
    width. Pops aggressively. This is what the user needs to verify.
  - Background (architectural backdrop, thin strokes): light grey,
    dashed thin border, 0.8 pt width. Still visible, but the eye skips
    past it when scanning. Context, not alert.

Color + style is intentionally redundant: a B&W print or colorblind
reviewer still gets the distinction via solid-vs-dashed and weight.

We use PyMuPDF rectangle annotations (not baked-in pixels) so markup
stays editable in Bluebeam/Acrobat — reviewers can delete false
positives without re-running the tool.

Coordinate conversion: regions are in pixel space at render DPI.
PDF native units are points (1/72 inch).
   pdf_coord = pixel_coord / (dpi / 72)
"""
from __future__ import annotations
from pathlib import Path
import fitz
from .diff import ChangeRegion


# Foreground: MEP work and other heavy linework changes
FOREGROUND_COLOR_RGB = (1.0, 0.0, 0.8)  # magenta
FOREGROUND_LINE_WIDTH = 2.0

# Background: thin architectural / context changes
BACKGROUND_COLOR_RGB = (0.55, 0.55, 0.55)  # neutral grey
BACKGROUND_LINE_WIDTH = 0.8
BACKGROUND_DASH_PATTERN = [4, 4]  # 4-unit dash, 4-unit gap

# How to handle background regions
BACKGROUND_MODE_DEEMPHASIZE = "deemphasize"  # default: thin grey dashed
BACKGROUND_MODE_HIDE = "hide"                # don't annotate at all
BACKGROUND_MODE_SAME = "same"                # treat the same as foreground (no classification)


def annotate_page(
    page: fitz.Page,
    regions: list[ChangeRegion],
    dpi: int,
    label_prefix: str = "",
    background_mode: str = BACKGROUND_MODE_DEEMPHASIZE,
) -> tuple[int, int]:
    """Add rectangle annotations for each change region.

    Returns (foreground_count, background_count) actually annotated.
    """
    px_per_pt = dpi / 72.0
    fg_count = 0
    bg_count = 0

    for i, r in enumerate(regions, start=1):
        if not r.is_foreground:
            if background_mode == BACKGROUND_MODE_HIDE:
                continue
            bg_count += 1
        else:
            fg_count += 1

        rect = fitz.Rect(
            r.x / px_per_pt,
            r.y / px_per_pt,
            (r.x + r.w) / px_per_pt,
            (r.y + r.h) / px_per_pt,
        )
        annot = page.add_rect_annot(rect)

        if r.is_foreground or background_mode == BACKGROUND_MODE_SAME:
            annot.set_colors(stroke=FOREGROUND_COLOR_RGB)
            annot.set_border(width=FOREGROUND_LINE_WIDTH)
            kind = "MEP"
        else:
            annot.set_colors(stroke=BACKGROUND_COLOR_RGB)
            annot.set_border(width=BACKGROUND_LINE_WIDTH, dashes=BACKGROUND_DASH_PATTERN)
            kind = "bg"

        # Tooltip useful in Bluebeam/Acrobat — shows why something was
        # flagged and what the underlying stroke-width measurement was.
        annot.set_info(
            title="mep-compare",
            content=(
                f"{label_prefix}#{i} [{kind}] "
                f"area={r.area}px  stroke≈{r.stroke_width:.1f}px"
                if label_prefix else
                f"Change #{i} [{kind}] area={r.area}px  stroke≈{r.stroke_width:.1f}px"
            ),
        )
        annot.update()

    return (fg_count, bg_count)


def save_annotated(doc: fitz.Document, output_path: str | Path) -> None:
    """Save the annotated document."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path), garbage=4, deflate=True)
