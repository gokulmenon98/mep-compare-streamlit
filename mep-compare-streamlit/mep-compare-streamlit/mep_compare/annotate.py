"""
Output annotation: mark added and removed content on the V2 PDF.

Semantically distinct markup for two semantically distinct events:

  - ADDED (new ink in V2): a yellow translucent highlighter rectangle
    on the bounding box. Highlight annotations use multiply blending
    in standard PDF renderers, so the underlying line work stays
    visible while the new element gets tinted yellow.

  - REMOVED (ink in V1 that's gone from V2): a magenta revision cloud
    around the area where the missing content used to be. Industry-
    standard convention for "something used to be here." We use PDF's
    native Border Effect dictionary (/BE with /S /C), which Bluebeam
    and Acrobat render as proper bumpy revision clouds. Some lighter
    PDF viewers (preview apps, browser viewers) may show this as a
    plain rectangle — the markup is correct, the rendering is the
    viewer's limitation. Open the marked PDF in Bluebeam for the
    full revision-cloud experience.

These two markup styles answer different questions:
  - Yellow highlighter answers "what's new?" by pointing at the
    actual added element.
  - Cloud answers "what's missing?" by drawing attention to the
    empty space where something used to live.

All annotations are PDF-native (not baked-in pixels) so reviewers can
delete false positives in Bluebeam/Acrobat without re-running.
"""
from __future__ import annotations
from pathlib import Path
import fitz
from .diff import ChangeRegion


# ADDED markup: yellow highlighter
ADDED_COLOR_RGB = (1.0, 0.92, 0.0)  # bright yellow, slightly warm

# REMOVED markup: magenta revision cloud
REMOVED_COLOR_RGB = (0.90, 0.05, 0.45)  # rich magenta — high contrast against drawings
REMOVED_LINE_WIDTH = 1.8
REMOVED_CLOUD_INTENSITY = 1  # 0 = no cloud, 1 = small bumps, 2 = larger bumps


def _pdf_rect_from_pixels(x: int, y: int, w: int, h: int, px_per_pt: float) -> fitz.Rect:
    """Convert a pixel-coord rectangle to a PDF-points fitz.Rect."""
    return fitz.Rect(
        x / px_per_pt,
        y / px_per_pt,
        (x + w) / px_per_pt,
        (y + h) / px_per_pt,
    )


def _annotate_added(page: fitz.Page, r: ChangeRegion, px_per_pt: float, label: str) -> None:
    """Draw a yellow highlighter on a region that was added in V2.

    Uses PDF's Highlight annotation (multiply blend mode), so the
    underlying drawing stays visible — yellow over black = black,
    yellow over white = yellow. Highlight on the *padded* rect rather
    than the core, so the new element has visual breathing room.
    """
    rect = _pdf_rect_from_pixels(r.x, r.y, r.w, r.h, px_per_pt)
    # add_highlight_annot accepts either a Rect or a list of Quads. A
    # Rect produces a single-quad highlight covering that rectangle.
    annot = page.add_highlight_annot(rect)
    annot.set_colors(stroke=ADDED_COLOR_RGB)
    annot.set_info(
        title="mep-compare",
        content=f"{label} [ADDED]  area={r.area}px",
    )
    annot.update()


def _annotate_removed(page: fitz.Page, r: ChangeRegion, px_per_pt: float, label: str) -> None:
    """Draw a magenta revision cloud around a region where content
    was removed from V2 (existed in V1 but is gone now).

    Uses PDF's native Border Effect "Cloudy" style, supported by
    Bluebeam and Acrobat. If the running PyMuPDF version doesn't
    support the cloud parameter, falls back to a solid magenta
    rectangle outline — visually distinct from yellow highlight,
    just without the bumpy edge.
    """
    rect = _pdf_rect_from_pixels(r.x, r.y, r.w, r.h, px_per_pt)
    annot = page.add_rect_annot(rect)
    annot.set_colors(stroke=REMOVED_COLOR_RGB)

    # Try the cloud border effect. Older PyMuPDF API exposes this via
    # the `clouds` keyword on set_border; newer versions accept a
    # `border` dict with PDF-spec keys. We try both, then plain.
    cloud_applied = False
    try:
        annot.set_border(width=REMOVED_LINE_WIDTH, clouds=REMOVED_CLOUD_INTENSITY)
        cloud_applied = True
    except (TypeError, ValueError):
        try:
            annot.set_border({
                "width": REMOVED_LINE_WIDTH,
                "style": "C",
                "clouds": REMOVED_CLOUD_INTENSITY,
            })
            cloud_applied = True
        except (TypeError, ValueError, KeyError):
            annot.set_border(width=REMOVED_LINE_WIDTH)

    annot.set_info(
        title="mep-compare",
        content=(
            f"{label} [REMOVED]  area={r.area}px"
            if cloud_applied
            else f"{label} [REMOVED]  area={r.area}px  (cloud-effect unsupported, plain outline)"
        ),
    )
    annot.update()


def annotate_page(
    page: fitz.Page,
    regions: list[ChangeRegion],
    dpi: int,
    label_prefix: str = "",
) -> tuple[int, int]:
    """Annotate one page with all its change regions. Returns
    (added_count, removed_count) for the summary panel."""
    px_per_pt = dpi / 72.0
    added_count = 0
    removed_count = 0

    for i, r in enumerate(regions, start=1):
        label = f"{label_prefix}#{i}" if label_prefix else f"Change #{i}"
        if r.kind == "added":
            _annotate_added(page, r, px_per_pt, label)
            added_count += 1
        elif r.kind == "removed":
            _annotate_removed(page, r, px_per_pt, label)
            removed_count += 1
        # Unknown kinds are silently skipped — future-proofing.

    return (added_count, removed_count)


def save_annotated(doc: fitz.Document, output_path: str | Path) -> None:
    """Save the annotated document."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path), garbage=4, deflate=True)
