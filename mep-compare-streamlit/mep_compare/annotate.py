"""
Output annotation: draw change-region markup onto the V2 PDF with a
visual hierarchy that separates real MEP changes from architectural
background shifts.

Two markup modes:
  - BOXES (default): a thin outline around each change cluster. Clean,
    matches the AEC-industry bounding-box review convention. Best when
    you primarily scan the marked PDF looking for what to verify.
  - LAYERED: three layers per cluster — a faint halo outside the
    cluster (peripheral-vision signal), a translucent fill on the core
    change area (precision signal), and a thin outline around the
    padded extent (the scanning signal from BOXES mode). Best when you
    primarily zoom in to verify specific changes.

Both modes preserve the foreground/background classification: thin
architectural changes get the dashed grey "context" treatment with no
fill or halo, regardless of mode.

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
FOREGROUND_LINE_WIDTH = 1.5

# Background: thin architectural / context changes
BACKGROUND_COLOR_RGB = (0.55, 0.55, 0.55)  # neutral grey
BACKGROUND_LINE_WIDTH = 0.8
BACKGROUND_DASH_PATTERN = [4, 4]  # 4-unit dash, 4-unit gap

# Layered-mode tuning. Per-layer styles (color, line width, opacity).
# Outline-based rather than fill-based because translucent fills render
# inconsistently across PDF viewers (PyMuPDF, Bluebeam, Acrobat all
# composite annotation alpha slightly differently). A stack of three
# outlines at decreasing weights gives a reliable visual hierarchy:
# the thick faded outer one acts as a halo, the medium one frames the
# precise change area, and the thin sharp inner one is the conventional
# bounding box.
HALO_LINE_WIDTH = 6.0       # thick outer ring
HALO_OPACITY = 0.18
HIGHLIGHT_LINE_WIDTH = 2.5  # medium ring on the core change area
HIGHLIGHT_OPACITY = 0.85

# Halo padding: how far the halo extends beyond the cluster's padded extent.
# Expressed in PDF points (1/72 inch) for consistent physical extent.
HALO_PAD_FRACTION = 0.18           # 18% of the cluster's max dimension
HALO_PAD_MIN_PTS = 6.0             # at least 6pt (~0.08")
HALO_PAD_MAX_PTS = 54.0            # at most 54pt (~0.75")

# How to handle background regions
BACKGROUND_MODE_DEEMPHASIZE = "deemphasize"  # default: thin grey dashed
BACKGROUND_MODE_HIDE = "hide"                # don't annotate at all
BACKGROUND_MODE_SAME = "same"                # treat the same as foreground (no classification)

# Markup mode
MARKUP_MODE_BOXES = "boxes"        # outline only (current default behavior)
MARKUP_MODE_LAYERED = "layered"    # halo + highlight + outline


def _pdf_rect_from_pixels(x: int, y: int, w: int, h: int, px_per_pt: float) -> fitz.Rect:
    """Convert pixel-coord rectangle to a PDF-points fitz.Rect."""
    return fitz.Rect(
        x / px_per_pt,
        y / px_per_pt,
        (x + w) / px_per_pt,
        (y + h) / px_per_pt,
    )


def _draw_foreground_layered(page: fitz.Page, r: ChangeRegion, px_per_pt: float, label: str) -> None:
    """Draw the three-layer foreground markup: halo ring + highlight ring + sharp outline.

    All three layers are stroke-only (no fills) so the underlying drawing
    stays visible. Layers are drawn back-to-front so they composite:
      1. Halo: a thick, faded magenta outline outside the padded extent.
         Provides peripheral-vision signal at full-sheet zoom.
      2. Highlight: a medium, mostly-opaque outline on the core change
         extent. Marks more precisely where the change actually is.
      3. Outline: thin sharp outline on the padded extent. The scanning
         layer — same as the BOXES mode.
    """
    # The "core" extent: where the actual change is, before box_margin padding.
    core_x = r.core_x if r.core_w else r.x
    core_y = r.core_y if r.core_h else r.y
    core_w = r.core_w if r.core_w else r.w
    core_h = r.core_h if r.core_h else r.h

    # Layer 1: halo (thick, faded outer ring).
    max_dim_pts = max(r.w, r.h) / px_per_pt
    pad_pts = max(HALO_PAD_MIN_PTS, min(HALO_PAD_MAX_PTS, max_dim_pts * HALO_PAD_FRACTION))
    halo_rect = fitz.Rect(
        r.x / px_per_pt - pad_pts,
        r.y / px_per_pt - pad_pts,
        (r.x + r.w) / px_per_pt + pad_pts,
        (r.y + r.h) / px_per_pt + pad_pts,
    )
    halo = page.add_rect_annot(halo_rect)
    halo.set_colors(stroke=FOREGROUND_COLOR_RGB)
    halo.set_border(width=HALO_LINE_WIDTH)
    halo.set_opacity(HALO_OPACITY)
    halo.set_info(title="mep-compare", content=f"{label} (halo)")
    halo.update()

    # Layer 2: highlight ring (medium weight, mostly opaque, on the core).
    highlight_rect = _pdf_rect_from_pixels(core_x, core_y, core_w, core_h, px_per_pt)
    hl = page.add_rect_annot(highlight_rect)
    hl.set_colors(stroke=FOREGROUND_COLOR_RGB)
    hl.set_border(width=HIGHLIGHT_LINE_WIDTH)
    hl.set_opacity(HIGHLIGHT_OPACITY)
    hl.set_info(title="mep-compare", content=f"{label} (highlight)")
    hl.update()

    # Layer 3: sharp outline on the padded extent — the conventional box.
    outline_rect = _pdf_rect_from_pixels(r.x, r.y, r.w, r.h, px_per_pt)
    outline = page.add_rect_annot(outline_rect)
    outline.set_colors(stroke=FOREGROUND_COLOR_RGB)
    outline.set_border(width=FOREGROUND_LINE_WIDTH)
    outline.set_opacity(1.0)
    outline.set_info(
        title="mep-compare",
        content=f"{label} [MEP] area={r.area}px  stroke≈{r.stroke_width:.1f}px",
    )
    outline.update()


def _draw_foreground_box(page: fitz.Page, r: ChangeRegion, px_per_pt: float, label: str) -> None:
    """Draw the box-only foreground markup: outline rectangle only."""
    outline_rect = _pdf_rect_from_pixels(r.x, r.y, r.w, r.h, px_per_pt)
    annot = page.add_rect_annot(outline_rect)
    annot.set_colors(stroke=FOREGROUND_COLOR_RGB)
    annot.set_border(width=FOREGROUND_LINE_WIDTH)
    annot.set_info(
        title="mep-compare",
        content=f"{label} [MEP] area={r.area}px  stroke≈{r.stroke_width:.1f}px",
    )
    annot.update()


def _draw_background(page: fitz.Page, r: ChangeRegion, px_per_pt: float, label: str) -> None:
    """Draw the background (de-emphasized) markup: thin grey dashed outline only.

    Backgrounds get the same treatment in both markup modes — there's no
    point highlighting architectural shifts more strongly. The dashed
    grey outline is enough to acknowledge they exist without competing
    for attention with the real MEP changes.
    """
    outline_rect = _pdf_rect_from_pixels(r.x, r.y, r.w, r.h, px_per_pt)
    annot = page.add_rect_annot(outline_rect)
    annot.set_colors(stroke=BACKGROUND_COLOR_RGB)
    annot.set_border(width=BACKGROUND_LINE_WIDTH, dashes=BACKGROUND_DASH_PATTERN)
    annot.set_info(
        title="mep-compare",
        content=f"{label} [bg] area={r.area}px  stroke≈{r.stroke_width:.1f}px",
    )
    annot.update()


def annotate_page(
    page: fitz.Page,
    regions: list[ChangeRegion],
    dpi: int,
    label_prefix: str = "",
    background_mode: str = BACKGROUND_MODE_DEEMPHASIZE,
    markup_mode: str = MARKUP_MODE_BOXES,
) -> tuple[int, int]:
    """Add annotations for each change region. Returns (foreground_count,
    background_count) actually drawn."""
    px_per_pt = dpi / 72.0
    fg_count = 0
    bg_count = 0

    for i, r in enumerate(regions, start=1):
        label = f"{label_prefix}#{i}" if label_prefix else f"Change #{i}"

        if r.is_foreground or background_mode == BACKGROUND_MODE_SAME:
            if markup_mode == MARKUP_MODE_LAYERED:
                _draw_foreground_layered(page, r, px_per_pt, label)
            else:
                _draw_foreground_box(page, r, px_per_pt, label)
            fg_count += 1
        else:
            if background_mode == BACKGROUND_MODE_HIDE:
                continue
            _draw_background(page, r, px_per_pt, label)
            bg_count += 1

    return (fg_count, bg_count)


def save_annotated(doc: fitz.Document, output_path: str | Path) -> None:
    """Save the annotated document."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path), garbage=4, deflate=True)
