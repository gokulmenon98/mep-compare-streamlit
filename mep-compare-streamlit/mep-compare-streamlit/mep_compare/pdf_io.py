"""
PDF input/output: load documents and render pages to numpy image arrays.

All rendering goes through render_page() so we can centrally control DPI,
colorspace, and the px-to-PDF-points conversion factor used downstream
when we need to map pixel-space results back to PDF coordinates.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
import fitz  # PyMuPDF
import numpy as np


# PDF native unit is 72 points/inch. Pixel coordinates from a rendered
# image at DPI=D scale by D/72 relative to PDF coordinates.
PDF_POINTS_PER_INCH = 72.0


@dataclass
class RenderedPage:
    """A page rendered to a grayscale numpy array, with metadata
    needed to map pixel-space results back to PDF coordinates."""
    index: int                   # zero-based page index in the source doc
    image: np.ndarray            # H x W uint8 grayscale
    page_width_pts: float        # PDF page width in points
    page_height_pts: float       # PDF page height in points
    dpi: int

    @property
    def px_per_pt(self) -> float:
        """Conversion factor: pixel coords * (1/px_per_pt) = PDF point coords."""
        return self.dpi / PDF_POINTS_PER_INCH


def open_pdf(path: str | Path) -> fitz.Document:
    """Open a PDF. Caller is responsible for closing."""
    return fitz.open(str(path))


def render_page(page: fitz.Page, dpi: int = 200, grayscale: bool = True) -> RenderedPage:
    """Render a single page to a numpy array.

    DPI of 200 is a sensible default — high enough to catch fine drawing
    detail, low enough that a 30x42" sheet stays manageable in memory
    (around 50 megapixels). Bump to 300 for finer detail at 2-3x cost.
    """
    matrix = fitz.Matrix(dpi / PDF_POINTS_PER_INCH, dpi / PDF_POINTS_PER_INCH)
    colorspace = fitz.csGRAY if grayscale else fitz.csRGB
    pix = page.get_pixmap(matrix=matrix, colorspace=colorspace, alpha=False)

    # Pixmap.samples is a bytes buffer; reshape to H x W (x C if RGB)
    if grayscale:
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    else:
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)

    return RenderedPage(
        index=page.number,
        image=arr,
        page_width_pts=page.rect.width,
        page_height_pts=page.rect.height,
        dpi=dpi,
    )


def iter_pages(doc: fitz.Document) -> Iterator[fitz.Page]:
    """Iterate the pages of a document. Thin wrapper to keep the
    rest of the codebase from depending directly on fitz iteration syntax."""
    for page in doc:
        yield page
