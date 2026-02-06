# MatchCV.io

Full-stack, locally-runnable ATS-style resume/job description matcher. FastAPI backend plus Vite/React frontend. Upload a PDF resume and paste a JD (or scrape via URL) to get semantic match scores, keyword coverage, and actionable suggestions. Optional TextRazor+Gemini support; falls back to TF‑IDF/skill dictionaries when API keys are absent.

## Features
- **PDF ingestion:** pypdf extracts text while keeping layout lines for highlighting.
- **Keyword extraction:** TextRazor API (if key present) + TF‑IDF fallback + curated skill lists (tech/finance/law). Synonym normalization and noise filtering.
- **Semantic scoring:** Gemini embeddings (if key present) blended with coverage/section weights for ATS-like scoring; tunable penalties.
- **Missing keyword insights:** Categorized must-have/nice-to-have lists, overlap chips, and ready-to-copy bullet suggestions.
- **Role profiles:** Tech/Finance/Law presets and custom comma-separated profiles that bias coverage.
- **JD ingest:** Paste text, upload .txt, or scrape via `/scrape-job` with real User-Agent.
- **Interactive UI:** Drag/drop resume, live score preview, animated “what you get” section, login modal shell, and profile selector gate.
- **No paid API required:** Runs fully offline using TF‑IDF and skill lists; optional keys add better extraction.

## Prerequisites
- Python 3.9+ (3.10+ recommended)
- Node.js 18+ and npm

## Environment variables (.env in repo root)
```
GEMINI_API_KEY=your_google_gemini_key      # optional; skip for offline
TEXTRAZOR_API_KEY=your_textrazor_key       # optional; skip for offline
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:5175
```

## Backend setup
```bash
cd C:\Users\habdi\culture_semantic_matcher
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn main:app --reload
```
Backend runs at http://127.0.0.1:8000 (OpenAPI at /docs). CORS allows the Vite dev ports.

### Key endpoints
- `POST /analyze`  
  - Form-data: `resume` (PDF UploadFile), `job_description` (text).  
  - Returns: `match_score`, `missing_keywords`, `resume_text`, `debug` block.
- `POST /scrape-job`  
  - JSON: `{ "url": "https://..." }`  
  - Returns cleaned body text or error codes for invalid/403.
- `POST /extract-resume`  
  - Form-data: `resume` (PDF). Returns extracted text for debugging.

## Frontend setup (Vite)
```bash
cd C:\Users\habdi\culture_semantic_matcher\web
npm install
npm run dev         # defaults to 5173
```
Open http://localhost:5173. For combined dev (frontend + backend) you can run the two commands in separate terminals; a convenience script `npm run dev:full` (if present) starts both using `concurrently`.

## Usage workflow
1) Start backend (`uvicorn main:app --reload`).
2) Start frontend (`npm run dev`).
3) In the UI: choose a profile (Tech/Finance/Law/Custom), drop a resume PDF, paste JD text or scrape via URL, then click “Analyze”.
4) Review match score, missing keywords, ATS coverage bars, and suggestions. Copy bullets into your CV. Highlighted resume text shows matched vs missing tokens.

## Scoring model (high level)
- Semantic similarity (embeddings) ~55% weight.
- Section-weighted keyword coverage (skills > experience > summary).
- Penalties for missing must-haves; softer for nice-to-haves.
- Fallback path when APIs absent: TF‑IDF similarity + skill dictionary overlap.

## Troubleshooting
- **“Request failed. Is the API running?”** Ensure `uvicorn main:app --reload` is active and CORS origin matches your frontend port.
- **TextRazor key_set false:** Verify `.env` loaded in backend shell; run `python -c "from dotenv import load_dotenv;load_dotenv();import os;print(os.getenv('TEXTRAZOR_API_KEY'))"`.
- **429 from Gemini/TextRazor:** The app will fall back automatically; scores may be lower but still functional.
- **PDF not read correctly:** Use text-based PDFs (not scanned images); else OCR externally before upload.

## Deploying
- Backend: containerize FastAPI + `requirements.txt`; mount `.env`.
- Frontend: `npm run build` then serve `web/dist` via any static host (Netlify, Vercel, S3+CloudFront).
- Update `ALLOWED_ORIGINS` to match your deployed frontend URL(s).

## Repo structure
- `main.py` – FastAPI app, scoring, scraping, parsers.
- `requirements.txt` – backend deps.
- `data/skills*.json` – skill dictionaries.
- `web/` – Vite/React frontend (App.jsx, styles, assets).

## Roadmap ideas
- Persist analyses per user (needs DB; consider Supabase/Postgres).
- Queue and rate-limit heavy API calls.
- Add auth to protect keys; role-based dashboards.
- Export annotated CV as PDF/Docx.

## License
n/a
