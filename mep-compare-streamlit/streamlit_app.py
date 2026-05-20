"""
MEP Drawing Compare — Streamlit app.

Wraps the mep_compare Python package in a web UI. The user uploads two PDFs;
the server runs the full identify → match → register → diff → classify →
annotate pipeline; the user downloads the marked-up V2 PDF.

This is the server-side version of the tool. All heavy lifting happens on
the Streamlit Cloud machine — no browser WASM, no CDN dependencies.
"""
from __future__ import annotations
import io
import tempfile
from pathlib import Path

import streamlit as st
import fitz

from mep_compare.pdf_io import open_pdf, render_page
from mep_compare.identify import identify_all, DEFAULT_CALIBRATION
from mep_compare.match import match_sheets
from mep_compare.register import register
from mep_compare.diff import detect_changes
from mep_compare.classify import auto_threshold, classify_regions
from mep_compare.annotate import (
    annotate_page, save_annotated,
    BACKGROUND_MODE_DEEMPHASIZE, BACKGROUND_MODE_HIDE,
)

# ─── Page config ──────────────────────────────────────────────────────
# Must be the first Streamlit call.
st.set_page_config(
    page_title="MEP Drawing Compare",
    page_icon="📐",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ─── CSS — schedule extractor design system ───────────────────────────
# Injected via st.markdown(unsafe_allow_html=True). Targets Streamlit's
# internal class names where needed (these are stable across recent
# Streamlit versions but can shift on major releases).
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..900;1,9..144,300..900&family=DM+Sans:opsz,wght@9..40,300..700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
  --bg: #F4F1EA;
  --bg-2: #ECE7DD;
  --ink: #1A1816;
  --ink-2: #4B463F;
  --ink-3: #6B645C;
  --ink-4: #A8A199;
  --rule: rgba(26, 24, 22, 0.14);
  --rule-soft: #E8E2D8;
  --serif: 'Fraunces', Georgia, serif;
  --sans: 'DM Sans', system-ui, sans-serif;
  --mono: 'JetBrains Mono', 'SF Mono', Menlo, monospace;
}

/* Hide Streamlit's default chrome. */
#MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; height: 0; }
[data-testid="stToolbar"] { display: none; }

/* App background — cream with dot grid like the schedule extractor. */
.stApp {
  background-color: var(--bg);
  background-image: radial-gradient(circle at 1px 1px, rgba(26,24,22,0.08) 1px, transparent 0);
  background-size: 22px 22px;
}

/* Body text. */
.stApp, .stMarkdown, p, label, .stTextInput, .stNumberInput {
  font-family: var(--sans);
  color: var(--ink);
}

/* Container padding: match schedule extractor's max-width 1100px. */
.block-container {
  padding-top: 1.5rem !important;
  padding-bottom: 3rem !important;
  max-width: 1100px !important;
}

/* ── Brand header ─────────────────────────────────────────────── */
.brand-row {
  display: flex;
  align-items: center;
  gap: 18px;
  border-bottom: 1px solid var(--rule);
  padding-bottom: 22px;
  margin-bottom: 0;
}
.brand-mark {
  width: 42px;
  height: 42px;
  border: 1.5px solid var(--ink);
  font-family: var(--mono);
  font-weight: 600;
  font-size: 13px;
  letter-spacing: 0.05em;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--ink);
}
.brand-title {
  font-family: var(--serif);
  font-weight: 400;
  font-size: clamp(32px, 5.5vw, 44px);
  letter-spacing: -0.005em;
  line-height: 1;
  font-variation-settings: "opsz" 144;
  margin: 0;
  color: var(--ink);
}
.brand-title .it { font-style: italic; }
.brand-version {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--ink-3);
  line-height: 1.3;
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-self: flex-start;
  padding-top: 4px;
}

/* ── Intro strip ──────────────────────────────────────────────── */
.intro {
  padding: 40px 0 32px;
}
.intro-paragraph {
  font-family: var(--serif);
  font-size: clamp(22px, 3.4vw, 30px);
  line-height: 1.35;
  font-weight: 350;
  margin: 0 0 28px;
  max-width: 880px;
  color: var(--ink);
}
.intro-paragraph .it { font-style: italic; color: var(--ink-3); }
.process {
  font-family: var(--mono);
  font-size: 12px;
  letter-spacing: 0.04em;
  color: var(--ink-3);
  line-height: 2;
}

/* ── Section heading (step + title) ───────────────────────────── */
.section-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin: 12px 0 14px;
}
.step-num {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.1em;
  color: var(--ink-3);
}
.section-title {
  font-family: var(--serif);
  font-size: 18px;
  font-weight: 500;
  letter-spacing: -0.005em;
  color: var(--ink);
}
.section-sub {
  font-family: var(--sans);
  font-size: 14px;
  color: var(--ink-3);
  margin: 0 0 14px;
}
.divider {
  border: none;
  border-top: 1.5px dashed var(--ink-4);
  margin: 0 0 22px;
}

/* ── File uploader ────────────────────────────────────────────── */
[data-testid="stFileUploader"] {
  background: #FFFFFF;
  border: 1px solid var(--ink);
}
[data-testid="stFileUploader"] section {
  background: transparent;
  border: none;
  padding: 14px 16px;
}
[data-testid="stFileUploaderDropzone"] {
  background: #FFFFFF;
  border: 1.5px dashed var(--ink-4);
  border-radius: 0;
  padding: 18px;
}
[data-testid="stFileUploaderDropzone"]:hover {
  border-color: var(--ink);
  background: var(--bg);
}
[data-testid="stFileUploaderDropzoneInstructions"] span {
  font-family: var(--serif);
  font-style: italic;
  color: var(--ink);
  font-size: 16px;
}
[data-testid="stFileUploaderDropzoneInstructions"] small {
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: var(--ink-3);
}
/* The "Browse files" button inside the dropzone. */
[data-testid="stBaseButton-secondary"] {
  background: var(--ink) !important;
  color: var(--bg) !important;
  border: 1px solid var(--ink) !important;
  border-radius: 0 !important;
  font-family: var(--mono) !important;
  font-size: 11px !important;
  font-weight: 600 !important;
  letter-spacing: 0.15em !important;
  text-transform: uppercase !important;
  padding: 8px 16px !important;
}
[data-testid="stBaseButton-secondary"]:hover {
  background: var(--ink-2) !important;
  border-color: var(--ink-2) !important;
}

/* ── Primary button ────────────────────────────────────────────── */
.stButton > button {
  background: var(--ink) !important;
  color: var(--bg) !important;
  border: 1px solid var(--ink) !important;
  border-radius: 0 !important;
  font-family: var(--mono) !important;
  font-size: 11px !important;
  font-weight: 600 !important;
  letter-spacing: 0.15em !important;
  text-transform: uppercase !important;
  padding: 11px 22px !important;
}
.stButton > button:hover {
  background: var(--ink-2) !important;
  border-color: var(--ink-2) !important;
}
.stButton > button:disabled {
  background: var(--ink-4) !important;
  border-color: var(--ink-4) !important;
  cursor: not-allowed;
}

/* Download button matches secondary style: cream bg with ink border. */
[data-testid="stDownloadButton"] > button {
  background: #FFFFFF !important;
  color: var(--ink) !important;
  border: 1px solid var(--ink) !important;
}
[data-testid="stDownloadButton"] > button:hover {
  background: var(--bg) !important;
}

/* ── Expanders (Advanced settings) ────────────────────────────── */
.streamlit-expanderHeader, [data-testid="stExpander"] summary {
  font-family: var(--mono) !important;
  font-size: 10px !important;
  letter-spacing: 0.15em !important;
  text-transform: uppercase !important;
  color: var(--ink-3) !important;
}
[data-testid="stExpander"] {
  border: 1px solid var(--rule) !important;
  border-radius: 0 !important;
  background: transparent !important;
}

/* ── Number inputs / sliders ──────────────────────────────────── */
[data-testid="stNumberInput"] input,
[data-testid="stTextInput"] input {
  background: var(--bg) !important;
  border: 1px solid var(--ink) !important;
  border-radius: 0 !important;
  font-family: var(--mono) !important;
  color: var(--ink) !important;
}
[data-testid="stWidgetLabel"] p {
  font-family: var(--mono) !important;
  font-size: 10px !important;
  letter-spacing: 0.1em !important;
  text-transform: uppercase !important;
  color: var(--ink-3) !important;
}

/* ── Status / progress blocks ──────────────────────────────────── */
[data-testid="stStatus"], [data-testid="stStatusContainer"] {
  border: 1px solid var(--ink) !important;
  border-radius: 0 !important;
  background: var(--ink) !important;
  color: var(--bg) !important;
}
[data-testid="stStatus"] *, [data-testid="stStatusContainer"] * {
  color: var(--bg) !important;
  font-family: var(--mono) !important;
  font-size: 12px !important;
}
.stProgress > div > div {
  background: var(--ink) !important;
}

/* ── Footer strip ─────────────────────────────────────────────── */
.footer {
  border-top: 1px solid var(--rule);
  margin-top: 40px;
  padding: 18px 0;
  display: flex;
  justify-content: space-between;
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: var(--ink-3);
}
</style>
""", unsafe_allow_html=True)


# ─── Brand header ─────────────────────────────────────────────────────
st.markdown("""
<div class="brand-row">
  <span class="brand-mark">MEP</span>
  <h1 class="brand-title"><span class="it">Drawing</span> Compare</h1>
  <div class="brand-version"><span>V</span><span>0.1</span></div>
</div>
""", unsafe_allow_html=True)


# ─── Intro strip ──────────────────────────────────────────────────────
st.markdown("""
<div class="intro">
  <p class="intro-paragraph">
    Upload an older and newer set of MEP drawings.
    <span class="it">The tool aligns each sheet, detects pixel-level changes, classifies MEP work apart from architectural background shifts, and marks them on the new set.</span>
  </p>
  <div class="process">
    <div>01 → IDENTIFY DRAWING NUMBERS &nbsp;·&nbsp; MATCH SHEETS ACROSS SETS</div>
    <div>02 → ALIGN PAGES &nbsp;·&nbsp; DETECT PIXEL-LEVEL CHANGES</div>
    <div>03 → CLASSIFY MEP WORK vs. ARCHITECTURAL BACKGROUND</div>
    <div>04 → EXPORT &nbsp;·&nbsp; ANNOTATED .PDF READY FOR BLUEBEAM / ACROBAT</div>
  </div>
</div>
""", unsafe_allow_html=True)


# ─── Upload cards ─────────────────────────────────────────────────────
col1, col2 = st.columns(2, gap="medium")

with col1:
    st.markdown("""
    <div class="section-head">
      <span class="step-num">01</span>
      <span class="section-title">Older Set</span>
    </div>
    <p class="section-sub">The earlier issue — multi-sheet vector PDF (V1)</p>
    """, unsafe_allow_html=True)
    v1_file = st.file_uploader(
        "Older set (V1)",
        type=["pdf"],
        key="v1",
        label_visibility="collapsed",
    )

with col2:
    st.markdown("""
    <div class="section-head">
      <span class="step-num">02</span>
      <span class="section-title">Newer Set</span>
    </div>
    <p class="section-sub">The set to mark up — multi-sheet vector PDF (V2)</p>
    """, unsafe_allow_html=True)
    v2_file = st.file_uploader(
        "Newer set (V2)",
        type=["pdf"],
        key="v2",
        label_visibility="collapsed",
    )


# ─── Advanced settings ────────────────────────────────────────────────
st.markdown("""
<div class="section-head" style="margin-top: 32px">
  <span class="step-num">03</span>
  <span class="section-title">Settings</span>
</div>
<p class="section-sub">Defaults work for most MEP sets. Adjust if results are off.</p>
""", unsafe_allow_html=True)

with st.expander("Title block region — where the drawing number sits on each sheet"):
    st.markdown(
        "Fractions of page width/height. Defaults to the bottom-right corner where MEP title blocks usually live.",
        help="If sheet matching fails (no drawing numbers found), try widening this region."
    )
    tb_col1, tb_col2, tb_col3, tb_col4 = st.columns(4)
    tb_x1 = tb_col1.number_input("Left", value=0.85, min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
    tb_y1 = tb_col2.number_input("Top",  value=0.85, min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
    tb_x2 = tb_col3.number_input("Right",  value=1.00, min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
    tb_y2 = tb_col4.number_input("Bottom", value=1.00, min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
    title_block = (tb_x1, tb_y1, tb_x2, tb_y2)

with st.expander("Detection sensitivity"):
    s_col1, s_col2, s_col3 = st.columns(3)
    dpi = s_col1.number_input("DPI", value=150, min_value=72, max_value=300, step=25,
                              help="Higher = more sensitive but slower and more memory")
    pixel_threshold = s_col2.number_input("Pixel threshold", value=40, min_value=5, max_value=200,
                                          help="Lower = more sensitive to small color differences")
    dilation_px = s_col3.number_input("Dilation (px)", value=15, min_value=1, max_value=40,
                                      help="How aggressively to merge nearby changes")
    min_area_px = st.number_input("Minimum change area (px)", value=200, min_value=20, max_value=5000,
                                  help="Drop tiny specks below this size")
    hide_background = st.checkbox(
        "Hide thin architectural background changes entirely",
        value=False,
        help="By default these are shown as grey-dashed boxes. Check to suppress them.",
    )


# ─── Compare button ───────────────────────────────────────────────────
st.markdown('<div style="margin-top: 32px"></div>', unsafe_allow_html=True)

ready = v1_file is not None and v2_file is not None
if not ready:
    st.markdown('<p style="font-family: var(--mono); font-size: 12px; color: var(--ink-3); letter-spacing: 0.02em;">Awaiting both files…</p>',
                unsafe_allow_html=True)

run = st.button("Compare drawings", disabled=not ready, use_container_width=False)


# ─── Pipeline ─────────────────────────────────────────────────────────
if run and v1_file and v2_file:
    # Persist uploads to temp files so PyMuPDF can open them by path.
    with tempfile.TemporaryDirectory() as tmpdir:
        v1_path = Path(tmpdir) / "v1.pdf"
        v2_path = Path(tmpdir) / "v2.pdf"
        v1_path.write_bytes(v1_file.getvalue())
        v2_path.write_bytes(v2_file.getvalue())

        with st.status("Processing…", expanded=True) as status:
            st.write(f"Opening PDFs…")
            v1_doc = open_pdf(v1_path)
            v2_doc = open_pdf(v2_path)
            st.write(f"  V1: {v1_doc.page_count} pages  |  V2: {v2_doc.page_count} pages")

            st.write("Identifying drawing numbers…")
            v1_ids = identify_all(v1_doc, title_block)
            v2_ids = identify_all(v2_doc, title_block)
            v1_hits = sum(1 for s in v1_ids if s.drawing_number)
            v2_hits = sum(1 for s in v2_ids if s.drawing_number)
            st.write(f"  V1: {v1_hits}/{len(v1_ids)} identified  |  V2: {v2_hits}/{len(v2_ids)} identified")

            if v1_hits == 0 or v2_hits == 0:
                status.update(label="No drawing numbers found", state="error")
                st.error("No drawing numbers detected. Try widening the title block region in Settings.")
                st.stop()

            st.write("Matching sheets…")
            report = match_sheets(v1_ids, v2_ids)
            st.write(f"  Matched: {len(report.matched)}  |  Added in V2: {len(report.added_in_v2)}  |  Removed from V1: {len(report.removed_from_v1)}")

            if not report.matched:
                status.update(label="No matched sheets", state="error")
                st.error("No sheets matched between the two sets. Drawing numbers may differ.")
                st.stop()

            # First pass: detect changes on every matched page, collecting
            # stroke widths so we can compute a global classification threshold.
            st.write(f"Comparing {len(report.matched)} matched pages at {dpi} DPI…")
            progress = st.progress(0.0)
            page_results = []  # list of (v1_id, v2_id, regions, inlier_ratio, skipped, reason)

            for i, (v1_id, v2_id) in enumerate(report.matched):
                dwg = v1_id.drawing_number
                v1_page = v1_doc[v1_id.page_index]
                v2_page = v2_doc[v2_id.page_index]
                v1_rendered = render_page(v1_page, dpi=dpi)
                v2_rendered = render_page(v2_page, dpi=dpi)

                reg = register(v1_rendered.image, v2_rendered.image, title_block)
                if not reg.ok:
                    page_results.append((v1_id, v2_id, [], reg.inlier_ratio, True, reg.reason))
                else:
                    regions = detect_changes(
                        v1_rendered.image, reg.aligned_v2,
                        title_block_region=title_block,
                        pixel_threshold=pixel_threshold,
                        dilation_px=dilation_px,
                        min_area_px=min_area_px,
                    )
                    page_results.append((v1_id, v2_id, regions, reg.inlier_ratio, False, ""))

                progress.progress((i + 1) / len(report.matched))

            # Auto-calibrate threshold and classify all regions.
            st.write("Classifying foreground vs background…")
            all_widths = [r.stroke_width for _, _, regs, _, _, _ in page_results
                          for r in regs if r.stroke_width > 0]
            threshold = auto_threshold(all_widths) if all_widths else 3.0
            st.write(f"  Stroke threshold: {threshold:.2f}px  (from {len(all_widths)} regions)")
            for _, _, regs, _, _, _ in page_results:
                classify_regions(regs, threshold)

            # Annotate.
            st.write("Annotating V2 PDF…")
            bg_mode = BACKGROUND_MODE_HIDE if hide_background else BACKGROUND_MODE_DEEMPHASIZE
            total_fg = 0
            total_bg = 0
            for v1_id, v2_id, regs, _, skipped, _ in page_results:
                if skipped or not regs:
                    continue
                v2_page = v2_doc[v2_id.page_index]
                fg, bg = annotate_page(v2_page, regs, dpi=dpi,
                                       label_prefix=v1_id.drawing_number,
                                       background_mode=bg_mode)
                total_fg += fg
                total_bg += bg

            # Save to bytes for download.
            output_buf = io.BytesIO()
            v2_doc.save(output_buf, garbage=4, deflate=True)
            output_bytes = output_buf.getvalue()

            v1_doc.close()
            v2_doc.close()

            status.update(label="Done.", state="complete")

        # ─── Summary + download ──────────────────────────────────────
        st.markdown("""
        <div class="section-head" style="margin-top: 36px">
          <span class="step-num">·</span>
          <span class="section-title">Results</span>
        </div>
        """, unsafe_allow_html=True)

        skipped = [pr for pr in page_results if pr[4]]
        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("Foreground (MEP) regions", total_fg)
        rc2.metric("Background regions", total_bg)
        rc3.metric("Pages skipped", len(skipped))

        if report.added_in_v2:
            added_list = ", ".join(s.drawing_number for s in report.added_in_v2)
            st.info(f"**Sheets added in V2 (review manually):** {added_list}")
        if report.removed_from_v1:
            removed_list = ", ".join(s.drawing_number for s in report.removed_from_v1)
            st.info(f"**Sheets removed from V1:** {removed_list}")

        st.download_button(
            label="Download marked V2 PDF",
            data=output_bytes,
            file_name="marked_v2.pdf",
            mime="application/pdf",
        )

        # Per-sheet detail table (collapsed by default to keep main view clean).
        with st.expander("Per-sheet details"):
            import pandas as pd
            rows = []
            for v1_id, v2_id, regs, inlier, skipped, reason in page_results:
                fg = sum(1 for r in regs if r.is_foreground)
                bg = sum(1 for r in regs if not r.is_foreground)
                status_text = "skipped" if skipped else ("changes" if regs else "no changes")
                rows.append({
                    "Drawing #": v1_id.drawing_number,
                    "Status": status_text,
                    "Foreground": fg,
                    "Background": bg,
                    "Inlier ratio": f"{inlier:.2f}",
                    "Notes": reason if skipped else "",
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# ─── Footer ───────────────────────────────────────────────────────────
st.markdown("""
<div class="footer">
  <div>WORKS WITH ANY MEP DRAWING SET &nbsp;·&nbsp; PYTHON &nbsp;·&nbsp; SERVER-SIDE</div>
  <div>OUTPUT &nbsp;·&nbsp; ANNOTATED .PDF</div>
</div>
""", unsafe_allow_html=True)
