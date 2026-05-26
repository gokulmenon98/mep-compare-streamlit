# mep-compare (Streamlit)

Server-side web app that compares two MEP drawing PDF sets and produces a
marked-up V2 PDF with magenta boxes around foreground (MEP) changes and
grey dashed boxes around background (architectural) shifts.

## Why this version

This is the Python-server version, in contrast to the earlier browser-only
attempt. All heavy work (PDF rendering, image registration, change detection,
classification) happens on the Streamlit Cloud server. The browser just
uploads, displays progress, and downloads the result. No WebAssembly, no
CDN dependency hell.

## Deploy to Streamlit Cloud (free)

1. **Make a GitHub repo** and push the contents of this folder to it.
   You can use the same workflow you used for the schedule extractor:
   create a new repo on github.com, click "Add file → Upload files,"
   drag everything in, commit.

2. **Sign in to https://share.streamlit.io** with your GitHub account.

3. Click **"New app"** (top right), then **"Deploy a public app from GitHub"**.

4. Fill in:
   - Repository: `your-username/mep-compare-streamlit` (or whatever you named it)
   - Branch: `main`
   - Main file path: `streamlit_app.py`
   - App URL: pick whatever subdomain you want (e.g. `mep-compare`)

5. Click **Deploy**. Streamlit will install the Python dependencies from
   `requirements.txt` and start the app. Takes about 2 minutes the first
   time.

6. Visit your URL. Done.

## Files

- `streamlit_app.py` — the web UI and pipeline glue.
- `mep_compare/` — the Python package doing the actual work
  (identify, match, register, diff, classify, annotate).
- `requirements.txt` — Python dependencies.
- `.streamlit/config.toml` — base theme + upload size limit (200 MB).

## Local testing

If you want to run it locally before pushing to Streamlit Cloud:

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open the URL it prints (usually http://localhost:8501).

## Limits to know about

- Streamlit Cloud's free tier gives you 1 GB of RAM per app. At 150 DPI,
  rendering one MEP sheet uses ~30-50 MB; a 50-sheet set processes one
  page at a time so it stays well within the limit. Very large
  (200+ page) sets at 300 DPI might push it.
- Apps sleep after about 12 hours of inactivity and take ~10 seconds
  to wake on the next visit.
- File upload limit is set to 200 MB per file in `config.toml`.
  Raise it there if needed.

## Privacy note

Uploaded drawings are processed on Streamlit's servers (AWS-hosted)
and are NOT persisted after the session ends. If your firm has policies
about uploading client drawings to third-party services, check those
before deploying. If self-hosting on internal infrastructure is required
later, the same code drops into a Docker container.
