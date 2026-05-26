"""
MEP Drawing Compare — Streamlit app.

Wraps the mep_compare Python package in a web UI. Upload two PDFs,
server runs the full pipeline (identify → match → register → diff →
annotate), user downloads the marked-up V2 PDF.

This version uses directional detection: added content (new ink in V2)
is marked with a yellow highlighter, removed content (ink that was in
V1 but is gone from V2) gets a magenta revision cloud. No more
foreground/background classification — the semantic split does the
visual hierarchy work directly.
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
from mep_compare.annotate import annotate_page

# ─── Page config ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="MEP Drawing Compare",
    page_icon="📐",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ─── CSS — schedule extractor design system ───────────────────────────
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
  --added: #C2A300;     /* darker yellow for UI accents — actual marker is brighter */
  --removed: #B8311A;
}

#MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; height: 0; }
[data-testid="stToolbar"] { display: none; }

.stApp {
  background-color: var(--bg);
  background-image: radial-gradient(circle at 1px 1px, rgba(26,24,22,0.08) 1px, transparent 0);
  background-size: 22px 22px;
}
.stApp, .stMarkdown, p, label, .stTextInput, .stNumberInput {
  font-family: var(--sans);
  color: var(--ink);
}
.block-container {
  padding-top: 1.5rem !important;
  padding-bottom: 3rem !important;
  max-width: 1100px !important;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 18px;
  border-bottom: 1px solid var(--rule);
  padding-bottom: 22px;
}
.brand-mark {
  width: 42px; height: 42px;
  border: 1.5px solid var(--ink);
  font-family: var(--mono);
  font-weight: 600; font-size: 13px; letter-spacing: 0.05em;
  display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0; color: var(--ink);
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
  font-size: 11px; letter-spacing: 0.18em;
  text-transform: uppercase; color: var(--ink-3);
  line-height: 1.3;
  margin-left: auto;
  display: flex; flex-direction: column;
  align-self: flex-start;
  padding-top: 4px;
}

.intro { padding: 40px 0 32px; }
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
.process .added { color: var(--added); font-weight: 600; }
.process .removed { color: var(--removed); font-weight: 600; }

.section-head {
  display: flex; align-items: baseline;
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

/* ── File uploader ── */
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
.stButton > button:disabled {
  background: var(--ink-4) !important;
  border-color: var(--ink-4) !important;
}
[data-testid="stDownloadButton"] > button {
  background: #FFFFFF !important;
  color: var(--ink) !important;
  border: 1px solid var(--ink) !important;
}

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

/* ── Legend chips ── */
.legend {
  display: flex;
  gap: 16px;
  align-items: center;
  margin: 0 0 28px;
  flex-wrap: wrap;
}
.legend-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.05em;
  color: var(--ink-2);
}
.legend-swatch {
  width: 24px;
  height: 14px;
  border: 1px solid var(--ink);
  display: inline-block;
}
.legend-swatch.added {
  background: rgba(255, 232, 0, 0.55);
}
.legend-swatch.removed {
  background: transparent;
  border: 1.5px solid #D8255D;
  position: relative;
}
.legend-swatch.removed::before {
  content: "";
  position: absolute;
  inset: -1px;
  border: 1.5px dashed #D8255D;
  border-radius: 0;
}
</style>
""", unsafe_allow_html=True)


# ─── Brand header ─────────────────────────────────────────────────────
st.markdown("""
<div class="brand-row">
  <span class="brand-mark">MEP</span>
  <h1 class="brand-title"><span class="it">Drawing</span> Compare</h1>
  <div class="brand-version"><span>V</span><span>0.2</span></div>
</div>
""", unsafe_allow_html=True)


# ─── Intro strip ──────────────────────────────────────────────────────
st.markdown("""
<div class="intro">
  <p class="intro-paragraph">
    Upload an older and newer set of MEP drawings.
    <span class="it">The tool aligns each sheet, identifies what was added or removed between versions, and marks the new set with yellow highlights and revision clouds.</span>
  </p>
  <div class="legend">
    <span class="legend-chip"><span class="legend-swatch added"></span><span class="added" style="color:#9F8500">ADDED</span> &nbsp;new ink in V2 — yellow highlighter</span>
    <span class="legend-chip"><span class="legend-swatch removed"></span><span class="removed" style="color:#B8311A">REMOVED</span> &nbsp;ink missing from V2 — magenta cloud</span>
  </div>
  <div class="process">
    <div>01 → IDENTIFY DRAWING NUMBERS &nbsp;·&nbsp; MATCH SHEETS ACROSS SETS</div>
    <div>02 → ALIGN PAGES &nbsp;·&nbsp; DETECT <span class="added">ADDED</span> AND <span class="removed">REMOVED</span> CONTENT</div>
    <div>03 → EXPORT &nbsp;·&nbsp; ANNOTATED .PDF READY FOR BLUEBEAM / ACROBAT</div>
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
    v1_file = st.file_uploader("Older set (V1)", type=["pdf"], key="v1", label_visibility="collapsed")

with col2:
    st.markdown("""
    <div class="section-head">
      <span class="step-num">02</span>
      <span class="section-title">Newer Set</span>
    </div>
    <p class="section-sub">The set to mark up — multi-sheet vector PDF (V2)</p>
    """, unsafe_allow_html=True)
    v2_file = st.file_uploader("Newer set (V2)", type=["pdf"], key="v2", label_visibility="collapsed")


# ─── Settings ─────────────────────────────────────────────────────────
st.markdown("""
<div class="section-head" style="margin-top: 32px">
  <span class="step-num">03</span>
  <span class="section-title">Settings</span>
</div>
<p class="section-sub">Defaults are tuned for typical 200 DPI vector MEP drawings. Loosen if you're missing real changes; tighten if there's too much noise.</p>
""", unsafe_allow_html=True)

with st.expander("Title block region — where the drawing number sits"):
    st.markdown(
        "Fractions of page width/height. Defaults to the bottom-right corner where MEP title blocks usually live. "
        "If sheet matching fails (no drawing numbers found), try widening this region."
    )
    tb_col1, tb_col2, tb_col3, tb_col4 = st.columns(4)
    tb_x1 = tb_col1.number_input("Left", value=0.85, min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
    tb_y1 = tb_col2.number_input("Top",  value=0.85, min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
    tb_x2 = tb_col3.number_input("Right",  value=1.00, min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
    tb_y2 = tb_col4.number_input("Bottom", value=1.00, min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
    title_block = (tb_x1, tb_y1, tb_x2, tb_y2)

with st.expander("Detection sensitivity"):
    sensitivity = st.select_slider(
        "Detection level",
        options=[
            "1 — Strict (only obvious changes)",
            "2 — Conservative",
            "3 — Default",
            "4 — Aggressive",
            "5 — Maximum (will be noisy)",
        ],
        value="3 — Default",
        help=(
            "Controls how small a change needs to be before it gets marked. "
            "Strict catches only large, obvious changes. Maximum catches everything "
            "including fine detail — but may produce a lot of noise."
        ),
    )
    # Map slider to detection parameters. The defaults at step 3 are tuned
    # for typical MEP drawings; steps 1-2 loosen filtering, 4-5 tighten it.
    SETTINGS_BY_SENSITIVITY = {
        "1 — Strict (only obvious changes)":    {"min_area": 4000, "dilation": 30, "threshold": 80},
        "2 — Conservative":                     {"min_area": 2500, "dilation": 28, "threshold": 70},
        "3 — Default":                          {"min_area": 1500, "dilation": 25, "threshold": 60},
        "4 — Aggressive":                       {"min_area": 800,  "dilation": 20, "threshold": 50},
        "5 — Maximum (will be noisy)":          {"min_area": 300,  "dilation": 15, "threshold": 40},
    }
    s = SETTINGS_BY_SENSITIVITY[sensitivity]

    st.markdown('<div style="height: 8px"></div>', unsafe_allow_html=True)
    st.caption(f"Min area: {s['min_area']}px · Dilation: {s['dilation']}px · Pixel threshold: {s['threshold']}")

    st.markdown('<div style="height: 12px"></div>', unsafe_allow_html=True)
    dpi = st.number_input(
        "DPI", value=200, min_value=72, max_value=300, step=25,
        help="Higher = finer detail but slower processing and more memory. 200 is a good default.",
    )


# ─── Compare button ───────────────────────────────────────────────────
st.markdown('<div style="margin-top: 32px"></div>', unsafe_allow_html=True)

ready = v1_file is not None and v2_file is not None
if not ready:
    st.markdown(
        '<p style="font-family: var(--mono); font-size: 12px; color: var(--ink-3); '
        'letter-spacing: 0.02em;">Awaiting both files…</p>',
        unsafe_allow_html=True,
    )

run = st.button("Compare drawings", disabled=not ready, use_container_width=False)


# ─── Pipeline ─────────────────────────────────────────────────────────
if run and v1_file and v2_file:
    with tempfile.TemporaryDirectory() as tmpdir:
        v1_path = Path(tmpdir) / "v1.pdf"
        v2_path = Path(tmpdir) / "v2.pdf"
        v1_path.write_bytes(v1_file.getvalue())
        v2_path.write_bytes(v2_file.getvalue())

        with st.status("Processing…", expanded=True) as status:
            st.write("Opening PDFs…")
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
                st.error("No drawing numbers detected. Try widening the title block region.")
                st.stop()

            st.write("Matching sheets…")
            report = match_sheets(v1_ids, v2_ids)
            st.write(
                f"  Matched: {len(report.matched)}  |  "
                f"Added in V2: {len(report.added_in_v2)}  |  "
                f"Removed from V1: {len(report.removed_from_v1)}"
            )

            if not report.matched:
                status.update(label="No matched sheets", state="error")
                st.error("No sheets matched between the two sets. Drawing numbers may differ.")
                st.stop()

            # ─── Per-page comparison ──────────────────────────────────
            st.write(f"Comparing {len(report.matched)} matched pages at {dpi} DPI…")
            progress = st.progress(0.0)
            # Per-page result: (v1_id, v2_id, regions, inlier_ratio, skipped, reason)
            page_results = []

            for i, (v1_id, v2_id) in enumerate(report.matched):
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
                        pixel_threshold=s["threshold"],
                        dilation_px=s["dilation"],
                        min_area_px=s["min_area"],
                    )
                    page_results.append((v1_id, v2_id, regions, reg.inlier_ratio, False, ""))

                progress.progress((i + 1) / len(report.matched))

            total_regions = sum(len(r) for _, _, r, _, _, _ in page_results)
            st.write(f"  Detected {total_regions} regions across {len(page_results)} pages")

            # Warn if region count looks suspicious — usually means registration
            # failed on some pages and we're surfacing residual misalignment.
            per_page_counts = [(v1_id.drawing_number, len(regs)) for v1_id, _, regs, _, sk, _
                                in page_results if not sk]
            noisy_pages = [(d, n) for d, n in per_page_counts if n > 100]
            if noisy_pages:
                st.warning(
                    f"⚠️ {len(noisy_pages)} page(s) detected >100 regions — likely a "
                    f"registration issue rather than real changes. Check these sheets "
                    f"manually: {', '.join(d for d, _ in noisy_pages[:5])}"
                    + (f" and {len(noisy_pages) - 5} more" if len(noisy_pages) > 5 else "")
                )

            # ─── Annotate ─────────────────────────────────────────────
            st.write(f"Marking up V2 PDF…")
            annotate_progress = st.progress(0.0)
            total_added = 0
            total_removed = 0
            for j, (v1_id, v2_id, regs, _, skipped, _) in enumerate(page_results):
                if not skipped and regs:
                    v2_page = v2_doc[v2_id.page_index]
                    added, removed = annotate_page(
                        v2_page, regs, dpi=dpi,
                        label_prefix=v1_id.drawing_number,
                    )
                    total_added += added
                    total_removed += removed
                annotate_progress.progress((j + 1) / len(page_results))

            # Save to bytes for download.
            st.write("Saving…")
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

        skipped_pages = [pr for pr in page_results if pr[4]]
        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("Added regions", total_added, help="New content in V2 — yellow highlighter")
        rc2.metric("Removed regions", total_removed, help="Content gone from V2 — magenta cloud")
        rc3.metric("Pages skipped", len(skipped_pages), help="Pages where alignment failed")

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

        # Per-sheet detail table
        with st.expander("Per-sheet details"):
            import pandas as pd
            rows = []
            for v1_id, v2_id, regs, inlier, skipped, reason in page_results:
                added_n = sum(1 for r in regs if r.kind == "added")
                removed_n = sum(1 for r in regs if r.kind == "removed")
                status_text = "skipped" if skipped else ("changes" if regs else "no changes")
                rows.append({
                    "Drawing #": v1_id.drawing_number,
                    "Status": status_text,
                    "Added": added_n,
                    "Removed": removed_n,
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
