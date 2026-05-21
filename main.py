from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import math
import os
import re
import secrets
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, quote_plus
from typing import List, Optional

logger = logging.getLogger(__name__)

from dotenv import load_dotenv
# Load env vars BEFORE importing auth_utils/db/email_service, which read os.getenv at import time.
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

import requests
from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, File, Form, Header, UploadFile
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer

import db
import auth_utils
import email_service
import rate_limit
import analysis_cache

try:
    from google import genai
    from google.genai import types
    GENAI_IMPORT_ERROR = None
except Exception as exc:
    genai = None
    types = None
    GENAI_IMPORT_ERROR = str(exc)

app = FastAPI()

_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:5175")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
_allowed_origins += [o.replace("localhost", "127.0.0.1") for o in _allowed_origins if "localhost" in o]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    try:
        app.openapi_schema = get_openapi(
            title="Job Matcher API",
            version="1.0.0",
            description="Job matching and resume analysis API",
            routes=app.routes,
        )
    except Exception:
        # Fallback schema to avoid /openapi.json 500s if schema generation fails.
        app.openapi_schema = {
            "openapi": "3.0.2",
            "info": {"title": "Job Matcher API", "version": "1.0.0"},
            "paths": {},
        }
    return app.openapi_schema


app.openapi = custom_openapi

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_PARSE_MODEL = os.getenv("GEMINI_PARSE_MODEL", "gemini-2.0-flash")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
GEMINI_REWRITE_MODEL = os.getenv("GEMINI_REWRITE_MODEL", "gemini-2.0-flash")
GEMINI_LITE_MODEL = os.getenv("GEMINI_LITE_MODEL", "gemini-3.1-flash-lite")
try:
    GEMINI_SEED = int(os.getenv("GEMINI_SEED", "1337"))
except ValueError:
    GEMINI_SEED = 1337
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_REWRITE_MODEL = os.getenv("OPENAI_REWRITE_MODEL", "gpt-5-mini")
GENAI_CLIENT = (
    genai.Client(api_key=GEMINI_API_KEY) if genai is not None and GEMINI_API_KEY else None
)
SCORER_VERSION = analysis_cache.SCORER_VERSION
ANALYZE_CACHE_MAX_ENTRIES = analysis_cache.ANALYZE_CACHE_MAX_ENTRIES


def gemini_generation_config(temperature: float = 0.0, **kwargs):
    config_kwargs = {
        "temperature": temperature,
        "seed": GEMINI_SEED,
        "candidate_count": 1,
    }
    config_kwargs.update(kwargs)
    return types.GenerateContentConfig(**config_kwargs)


def analyze_cache_key(resume_text: str, job_description: str) -> str:
    return analysis_cache.analyze_cache_key(resume_text, job_description)


def get_cached_analyze_response(cache_key: str) -> dict | None:
    return analysis_cache.get_cached_response(cache_key)


def set_cached_analyze_response(
    cache_key: str,
    response: dict,
    resume_text: str = "",
    job_description: str = "",
) -> None:
    analysis_cache.set_cached_response(cache_key, response, resume_text, job_description)


def attach_analyze_request_context(
    response: dict,
    user: dict,
    job_source: str,
    debug: bool,
    cache_key: str,
    cache_hit: bool,
) -> dict:
    out = copy.deepcopy(response)
    breakdown = out.get("role_fit_breakdown")
    if isinstance(breakdown, dict):
        jd_meta = breakdown.setdefault("job_description", {})
        if isinstance(jd_meta, dict):
            jd_meta["source"] = job_source if job_source in {"paste", "url"} else "paste"
    if debug:
        debug_block = out.get("debug") if isinstance(out.get("debug"), dict) else {}
        debug_block["analysis_cache"] = analysis_cache.debug_metadata(cache_key, cache_hit)
        debug_block["gemini_seed"] = GEMINI_SEED
        debug_block["scorer_version"] = SCORER_VERSION
        out["debug"] = debug_block
    else:
        out.pop("debug", None)
    fresh_user = db.get_user_by_id(user["id"])
    out["user"] = _user_to_public(fresh_user) if fresh_user else None
    return out

# ── Auth & rate limiting ──────────────────────────────────────────────────────
# ACCOUNTS env var format: "email:password:daily_limit,email2:password2:daily_limit"
# e.g. ACCOUNTS=tester1@shortlistly.com:pass123:10,tester2@shortlistly.com:pass456:5
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
DEFAULT_DAILY_LIMIT = int(os.getenv("DEFAULT_DAILY_LIMIT", "10"))
SCAN_COUNTS_FILE = Path(os.getenv("SCAN_COUNTS_FILE", "scan_counts.json"))
FEEDBACK_FILE    = Path(os.getenv("FEEDBACK_FILE", "feedback.json"))

def _load_accounts() -> dict:
    raw = os.getenv("ACCOUNTS", "")
    accounts = {}
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) >= 2:
            email = parts[0].strip().lower()
            password = parts[1].strip()
            limit = int(parts[2]) if len(parts) >= 3 else DEFAULT_DAILY_LIMIT
            if email and password:
                accounts[email] = {"password": password, "daily_limit": limit}
    return accounts

ACCOUNTS: dict = _load_accounts()

# In-memory sessions: token → {email, created_at}
_sessions: dict[str, dict] = {}

def _load_scan_counts() -> dict:
    if SCAN_COUNTS_FILE.exists():
        try:
            return json.loads(SCAN_COUNTS_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_scan_counts(counts: dict) -> None:
    try:
        SCAN_COUNTS_FILE.write_text(json.dumps(counts, indent=2))
    except Exception as exc:
        logger.warning("Could not save scan counts: %s", exc)

_scan_counts: dict = _load_scan_counts()
_scan_counts_lock = threading.Lock()

def _today() -> str:
    return date.today().isoformat()

def get_scans_today(email: str) -> int:
    return _scan_counts.get(email, {}).get(_today(), 0)

def increment_scan(email: str) -> int:
    with _scan_counts_lock:
        today = _today()
        if email not in _scan_counts:
            _scan_counts[email] = {}
        _scan_counts[email][today] = _scan_counts[email].get(today, 0) + 1
        _save_scan_counts(_scan_counts)
        return _scan_counts[email][today]

def get_email_from_token(token: str) -> str | None:
    session = _sessions.get(token)
    if not session:
        return None
    # Sessions expire after 7 days
    created = datetime.fromisoformat(session["created_at"])
    if (datetime.now(timezone.utc) - created).days >= 7:
        _sessions.pop(token, None)
        return None
    return session["email"]

def require_auth(request_data: dict) -> str:
    """Extract and validate token from request body. Returns email or raises 401."""
    token = str((request_data or {}).get("_token") or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    email = get_email_from_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    return email

def check_scan_limit(email: str) -> None:
    """Raise 429 if user has hit their daily scan limit."""
    account = ACCOUNTS.get(email, {})
    limit = account.get("daily_limit", DEFAULT_DAILY_LIMIT)
    used = get_scans_today(email)
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Daily scan limit of {limit} reached. Resets at midnight."
        )

def check_and_increment_scan(email: str) -> int:
    """Atomically check the daily limit and increment. Raises 429 if at limit."""
    with _scan_counts_lock:
        account = ACCOUNTS.get(email, {})
        limit = account.get("daily_limit", DEFAULT_DAILY_LIMIT)
        today = _today()
        used = _scan_counts.get(email, {}).get(today, 0)
        if used >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"Daily scan limit of {limit} reached. Resets at midnight."
            )
        if email not in _scan_counts:
            _scan_counts[email] = {}
        _scan_counts[email][today] = used + 1
        _save_scan_counts(_scan_counts)
        return _scan_counts[email][today]
# ─────────────────────────────────────────────────────────────────────────────
TEXTRAZOR_API_KEY = os.getenv("TEXTRAZOR_API_KEY")
TEXTRAZOR_ENDPOINT = os.getenv("TEXTRAZOR_ENDPOINT", "https://api.textrazor.com")

SKILLS_PATH = os.getenv("SKILLS_PATH", os.path.join("data", "skills.json"))
SKILL_SYNONYMS = {
    "powerbi": "power bi",
    "power bi": "power bi",
    "pbi": "power bi",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "dashboard": "dashboarding",
    "dashboards": "dashboarding",
}
REQUIRED_MARKERS = (
    "must have",
    "must-have",
    "required",
    "requirements",
    "minimum",
    "qualifications",
    "experience with",
    "proficient",
    "strong",
    "hands-on",
    "knowledge of",
    "familiarity with",
    "skills",
    "ability to",
    "you will",
)
MIN_SKILL_OCCURRENCES = 1
PENALTY_MUST_HAVE = 2.0
PENALTY_OTHER = 1.0
SEMANTIC_WEIGHT = 0.55
MUST_COVERAGE_WEIGHT = 0.3
NICE_COVERAGE_WEIGHT = 0.15
ATS_BLEND_WEIGHT = 0.4
RESPONSIBILITY_MATCH_WEIGHT = 0.65
EXPERIENCE_MATCH_WEIGHT = 0.10
SKILLS_MATCH_WEIGHT = 0.05
SEMANTIC_MATCH_WEIGHT = 0.20
RESPONSIBILITY_SIMILARITY_THRESHOLD = 0.34
RESPONSIBILITY_EMBEDDING_THRESHOLD = 0.68

SWE_TITLE_TERMS = (
    "software engineer",
    "software developer",
    "swe",
    "full stack",
    "backend",
    "frontend",
    "full-stack",
)
SWE_CORE_SKILLS = (
    "system design",
    "software architecture",
    "distributed systems",
    "scalability",
    "performance",
    "performance optimization",
    "code review",
    "code reviews",
    "testing",
    "unit testing",
    "integration testing",
    "end-to-end testing",
    "monitoring",
    "observability",
    "logging",
    "ci/cd",
    "apis",
    "api",
    "microservices",
    "backend",
    "frontend",
    "full stack",
)
SWE_NICE_SKILLS = (
    "mobile",
    "android",
    "ios",
    "swift",
    "kotlin",
    "react",
    "react native",
    "kubernetes",
    "docker",
    "cloud",
    "aws",
    "gcp",
    "azure",
    "data structures",
    "algorithms",
    "refactoring",
)
SWE_LEADERSHIP_TERMS = (
    "lead",
    "led",
    "ownership",
    "owner",
    "mentorship",
    "mentor",
    "onboard",
    "initiative",
    "drive",
)
SWE_CROSS_FUNC_TERMS = (
    "cross-functional",
    "stakeholders",
    "product",
    "design",
    "operations",
    "infra",
    "infrastructure",
)
SWE_SCALE_TERMS = (
    "scalable",
    "large-scale",
    "performance",
    "latency",
    "throughput",
    "reliability",
    "availability",
)
SWE_NEGATIVE_TERMS = (
    "risk analyst",
    "performance analyst",
    "power bi",
    "mi reporting",
    "kpi reporting",
    "dashboards",
    "reporting",
    "reconciliation",
)
LANGUAGE_SKILLS = (
    "python",
    "java",
    "c++",
    "c#",
    "c",
    "javascript",
    "typescript",
    "kotlin",
    "swift",
    "go",
    "rust",
    "php",
    "sql",
)


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.#-]{1,}")
CLEAN_EDGE_RE = re.compile(r"^[^A-Za-z0-9+#]+|[^A-Za-z0-9+#]+$")
PHRASE_NORM_RE = re.compile(r"[^a-z0-9+#]+")
STOPWORDS = {
    "and",
    "or",
    "the",
    "a",
    "an",
    "with",
    "for",
    "to",
    "of",
    "in",
    "on",
    "is",
    "are",
    "as",
    "looking",
    "experience",
    "years",
    "year",
    "required",
    "requirement",
    "requirements",
    "must",
    "must-have",
    "have",
    "nice-to-have",
    "preferred",
    "we",
    "our",
    "their",
    "your",
    "you",
    "will",
    "role",
    "team",
    "job",
    "position",
    "responsibilities",
    "responsibility",
    "hiring",
    "candidate",
    "candidates",
    "ideal",
    "looking",
    "seeking",
    "analyst",
}
RAKE_STOPWORDS = STOPWORDS.union(
    {
        "be",
        "by",
        "from",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "our",
        "your",
        "their",
        "we",
        "you",
        "will",
        "able",
        "ability",
        "plus",
        "bonus",
        "including",
        "include",
        "including",
        "etc",
        "etc.",
    }
)
MUST_HAVE_MARKERS = (
    "must have",
    "must-have",
    "required",
    "requirements",
    "minimum qualifications",
    "basic qualifications",
)
STOP_SECTION_MARKERS = ("preferred", "nice to have", "bonus", "plus", "optional")

RANGE_YEARS_RE = re.compile(r"(\d+)\s*-\s*(\d+)\s*(?:years|yrs)\b", re.IGNORECASE)
AT_LEAST_YEARS_RE = re.compile(
    r"(?:at\s+least|minimum|min\.?)\s*(\d+)\s*(?:years|yrs)\b",
    re.IGNORECASE,
)
PLUS_YEARS_RE = re.compile(r"\b(\d+)\s*\+?\s*(?:years|yrs)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
DATE_RANGE_RE = re.compile(r"\b(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}\b")
MONTH_YEAR_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(19|20)\d{2}\b",
    re.IGNORECASE,
)
METRIC_RE = re.compile(
    r"(\b\d{1,3}(?:,\d{3})+(?:\.\d+)?|\b\d+(?:\.\d+)?)\s*(%|(?:x|k|m|mm|bn|b)\b)|[$€£]\s*\d|\b\d+[+]",
    re.IGNORECASE,
)

ACTION_VERBS = (
    "build",
    "built",
    "design",
    "designed",
    "develop",
    "developed",
    "implement",
    "implemented",
    "lead",
    "led",
    "manage",
    "managed",
    "drive",
    "driven",
    "own",
    "owned",
    "deliver",
    "delivered",
    "launch",
    "launched",
    "ship",
    "shipped",
    "optimize",
    "optimized",
    "improve",
    "improved",
    "reduce",
    "reduced",
    "increase",
    "increased",
    "automate",
    "automated",
    "migrate",
    "migrated",
    "create",
    "created",
    "analyze",
    "analyzed",
    "collaborate",
    "collaborated",
    "architect",
    "architected",
    "refactor",
    "refactored",
    "maintain",
    "maintained",
    "mentor",
    "mentored",
    "coordinate",
    "coordinated",
    "support",
    "supported",
)
ACTION_VERB_BASE = {
    "build": "build",
    "built": "build",
    "design": "design",
    "designed": "design",
    "develop": "develop",
    "developed": "develop",
    "implement": "implement",
    "implemented": "implement",
    "lead": "lead",
    "led": "lead",
    "manage": "manage",
    "managed": "manage",
    "drive": "drive",
    "driven": "drive",
    "own": "own",
    "owned": "own",
    "deliver": "deliver",
    "delivered": "deliver",
    "launch": "launch",
    "launched": "launch",
    "ship": "ship",
    "shipped": "ship",
    "optimize": "optimize",
    "optimized": "optimize",
    "improve": "improve",
    "improved": "improve",
    "reduce": "reduce",
    "reduced": "reduce",
    "increase": "increase",
    "increased": "increase",
    "automate": "automate",
    "automated": "automate",
    "migrate": "migrate",
    "migrated": "migrate",
    "create": "create",
    "created": "create",
    "analyze": "analyze",
    "analyzed": "analyze",
    "collaborate": "collaborate",
    "collaborated": "collaborate",
    "architect": "architect",
    "architected": "architect",
    "refactor": "refactor",
    "refactored": "refactor",
    "maintain": "maintain",
    "maintained": "maintain",
    "mentor": "mentor",
    "mentored": "mentor",
    "coordinate": "coordinate",
    "coordinated": "coordinate",
    "support": "support",
    "supported": "support",
}
SENIORITY_ALIASES = {
    "jr": "junior",
    "junior": "junior",
    "mid": "mid",
    "midlevel": "mid",
    "mid-level": "mid",
    "senior": "senior",
    "sr": "senior",
    "lead": "lead",
    "manager": "manager",
    "principal": "principal",
    "staff": "principal",
}
SENIORITY_LEVELS = {
    "junior": 1,
    "mid": 2,
    "senior": 3,
    "lead": 4,
    "manager": 4,
    "principal": 5,
}
RESPONSIBILITY_SECTION_WEIGHTS = {
    "experience": 1.0,
    "projects": 0.75,
    "summary": 0.4,
}
RESPONSIBILITY_HINTS = (
    "you will",
    "responsible for",
    "responsibilities",
    "what you'll do",
    "what you will do",
    "what you ll do",
    "day to day",
    "in this role",
)
SOFT_SKILLS = (
    "communication",
    "teamwork",
    "leadership",
    "problem solving",
    "problem-solving",
    "adaptability",
    "time management",
    "time-management",
    "stakeholder management",
)
TECH_SKILL_ALIASES = {
    "react": ("react", "react.js", "reactjs"),
    "typescript": ("typescript", "type script", "ts"),
    "javascript": ("javascript", "java script", "js"),
    "c++": ("c++", "cpp", "c plus plus"),
}
DOMAIN_EVIDENCE_GROUPS = (
    {
        "targets": (
            "trading",
            "finance",
            "financial",
            "financial markets",
            "proprietary trading",
            "quant",
            "quantitative finance",
            "market making",
        ),
        "signals": (
            "trading",
            "backtesting",
            "backtest",
            "rsi",
            "macd",
            "kelly",
            "market data",
            "financial market",
            "financial markets",
            "equity",
            "equities",
            "options",
            "securities",
            "quant",
            "strategy optimizer",
            "strategy optimiser",
            "strategy optimisation",
        ),
    },
)

SECTION_HEADINGS = {
    "experience": (
        "experience", "work experience", "professional experience", "employment",
        "work history", "career history", "professional background", "relevant experience",
    ),
    "projects": (
        "projects", "technical projects", "personal projects", "side projects",
        "portfolio", "selected projects", "key projects",
    ),
    "education": (
        "education", "academics", "qualifications", "certifications", "certificates",
        "training", "academic background", "courses", "licences",
    ),
    "skills": (
        "skills", "technical skills", "core competencies", "competencies",
        "technologies", "tools and technologies", "key skills", "areas of expertise",
        "expertise", "programming languages", "tools", "languages and tools",
        "core skills", "tech stack", "technical stack", "my stack",
        "software skills", "hard skills", "digital skills", "it skills",
        "proficiencies", "technical proficiencies", "professional skills",
        "technical expertise", "capabilities", "technical capabilities",
        "development tools", "frameworks", "what i know", "my skills",
        "stack", "languages skills", "technical knowledge", "knowledge",
        "software and tools", "tools technologies", "relevant skills",
    ),
    "summary": (
        "summary", "profile", "objective", "professional summary", "about me",
        "personal statement", "career summary", "executive summary", "overview",
    ),
}

SECTION_WEIGHTS = {
    "experience": 1.0,
    "projects": 0.8,
    "skills": 0.6,
    "education": 0.3,
    "summary": 0.25,
    "other": 0.2,
}


def clean_text(text: str) -> str:
    text = re.sub(r"-\n", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def extract_pdf_text(file_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to read PDF upload.") from exc

    pages_text: List[str] = []
    for page in reader.pages:
        pages_text.append(page.extract_text() or "")
    return clean_text("\n".join(pages_text))


def split_resume_sections(text: str) -> dict:
    sections = {"other": ""}
    current = "other"
    lines = text.splitlines()
    for line in lines:
        norm = normalize_phrase(line)
        if not norm:
            continue
        matched = False
        for key, aliases in SECTION_HEADINGS.items():
            if any(norm.startswith(alias) for alias in aliases):
                current = key
                sections.setdefault(current, "")
                matched = True
                break
        if not matched:
            sections[current] = sections.get(current, "") + " " + line
    # Normalize whitespace
    for key, val in list(sections.items()):
        sections[key] = normalize_phrase(val)
    return sections


def split_resume_sections_raw(text: str) -> dict:
    sections: dict[str, List[str]] = {"other": []}
    current = "other"
    lines = text.splitlines()
    for line in lines:
        if not line.strip():
            continue
        norm = normalize_phrase(line)
        matched = False
        for key, aliases in SECTION_HEADINGS.items():
            if any(norm.startswith(alias) for alias in aliases):
                current = key
                sections.setdefault(current, [])
                matched = True
                break
        if not matched:
            sections.setdefault(current, []).append(line)
    return {key: "\n".join(val).strip() for key, val in sections.items()}


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    denom = norm_a * norm_b
    return 0.0 if denom == 0 else dot / denom


def tfidf_similarity(text_a: str, text_b: str) -> float:
    if not text_a.strip() or not text_b.strip():
        return 0.0
    vectorizer = TfidfVectorizer(
        stop_words=list(STOPWORDS)
        + ["job", "description", "responsibilities", "requirements", "resume", "cv"],
        ngram_range=(1, 2),
        lowercase=True,
        max_features=5000,
    )
    try:
        tfidf = vectorizer.fit_transform([text_a, text_b])
    except ValueError:
        return 0.0
    vectors = tfidf.toarray()
    return cosine_similarity(vectors[0].tolist(), vectors[1].tolist())


def normalize_token(token: str) -> str:
    cleaned = CLEAN_EDGE_RE.sub("", token)
    return cleaned.lower()


def normalize_phrase(text: str) -> str:
    normalized = PHRASE_NORM_RE.sub(" ", text.lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def normalize_skill(text: str) -> str:
    normalized = normalize_phrase(text)
    return SKILL_SYNONYMS.get(normalized, normalized)


def canonical_skill(text: str) -> str:
    normalized = normalize_skill(text)
    return SKILL_DISPLAY.get(normalized, text)


def expand_phrase_to_skills(phrase: str) -> List[str]:
    if not phrase:
        return []
    normalized = normalize_phrase(phrase)
    if not normalized:
        return []
    tokens = [t for t in normalized.replace("/", " ").split() if t]
    tokens = [t for t in tokens if t not in STOPWORDS and t not in {"and", "or"}]
    candidates = set()
    for token in tokens:
        candidates.add(token)
    for i in range(len(tokens)):
        if i + 1 < len(tokens):
            candidates.add(f"{tokens[i]} {tokens[i + 1]}")
        if i + 2 < len(tokens):
            candidates.add(f"{tokens[i]} {tokens[i + 1]} {tokens[i + 2]}")
    results = []
    for item in candidates:
        norm = normalize_skill(item)
        if SKILLS_SET and norm in SKILLS_SET:
            results.append(canonical_skill(item))
    return results


def load_skills(path: str):
    display_map = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            skills = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return set(), display_map

    for item in skills:
        if not isinstance(item, str):
            continue
        normalized = normalize_skill(item)
        if normalized:
            display_map[normalized] = item
    return set(display_map.keys()), display_map


SKILLS_SET, SKILL_DISPLAY = load_skills(SKILLS_PATH)


def textrazor_extract_phrases(text: str, debug_info: dict | None = None) -> List[str]:
    if not TEXTRAZOR_API_KEY or TEXTRAZOR_API_KEY.startswith("PASTE_") or not text.strip():
        if debug_info is not None:
            debug_info["textrazor_error"] = "missing_or_placeholder_key"
        return []

    headers = {"X-TextRazor-Key": TEXTRAZOR_API_KEY}
    data = {
        "extractors": "words,phrases,entities,topics",
        "text": text,
    }
    try:
        response = requests.post(TEXTRAZOR_ENDPOINT, headers=headers, data=data, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        if debug_info is not None:
            debug_info["textrazor_error"] = str(exc)
        return []

    try:
        payload = response.json().get("response", {})
    except ValueError:
        if debug_info is not None:
            debug_info["textrazor_error"] = "invalid_json_response"
        return []
    phrases = []

    words = payload.get("words", []) or []
    if not words:
        sentences = payload.get("sentences", []) or []
        flattened = []
        for sentence in sentences:
            for word in sentence.get("words", []) or []:
                flattened.append(word)
        words = flattened
    noun_phrases = payload.get("nounPhrases", []) or []
    for phrase in noun_phrases:
        positions = phrase.get("wordPositions") or []
        tokens = []
        for pos in positions:
            if not isinstance(pos, int) or pos < 0 or pos >= len(words):
                continue
            word = words[pos]
            token = word.get("token")
            if not token and "inputStartOffset" in word and "inputEndOffset" in word:
                token = text[word["inputStartOffset"] : word["inputEndOffset"]]
            if not token and "startOffset" in word and "endOffset" in word:
                token = text[word["startOffset"] : word["endOffset"]]
            if token:
                tokens.append(token)
        if tokens:
            phrases.append(" ".join(tokens))

    entities = payload.get("entities", []) or []
    for entity in entities:
        for key in ("matchedText", "entityId", "entity"):
            value = entity.get(key)
            if value:
                phrases.append(value)
                break

    topics = payload.get("topics", []) or []
    for topic in topics:
        value = topic.get("label") or topic.get("topic")
        if value:
            phrases.append(value)

    if debug_info is not None:
        debug_info["textrazor_phrases_sample"] = phrases[:20]

    if debug_info is not None:
        debug_info["textrazor_words"] = len(words)
        debug_info["textrazor_noun_phrases"] = len(noun_phrases)
        debug_info["textrazor_entities"] = len(entities)
        debug_info["textrazor_topics"] = len(topics)

    extracted = []
    for phrase in phrases:
        extracted.extend(expand_phrase_to_skills(phrase))

    if not SKILLS_SET:
        return phrases

    for word in words:
        token = word.get("token")
        if not token:
            continue
        extracted.extend(expand_phrase_to_skills(token))

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for item in extracted:
        norm = normalize_skill(item)
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(item)

    if debug_info is not None:
        debug_info["textrazor_extracted"] = len(deduped)
        debug_info["textrazor_extracted_sample"] = deduped[:20]
    return deduped


def build_skill_confidence(job_description: str):
    normalized = normalize_phrase(job_description)
    if not normalized or not SKILLS_SET:
        return {}, set()

    freq = {}
    for skill in SKILLS_SET:
        if not skill:
            continue
        count = normalized.count(skill)
        if count:
            freq[skill] = count

    required_lines = []
    for line in job_description.splitlines():
        lower = line.lower()
        if any(marker in lower for marker in REQUIRED_MARKERS):
            required_lines.append(normalize_phrase(line))

    required = set()
    if required_lines:
        for line in required_lines:
            for skill in SKILLS_SET:
                if skill in line:
                    required.add(skill)

    return freq, required


def skill_is_confident(skill_norm: str, freq: dict, required: set) -> bool:
    if not SKILLS_SET:
        return True
    if skill_norm in required:
        return True
    return freq.get(skill_norm, 0) >= MIN_SKILL_OCCURRENCES


def phrase_in_resume(
    phrase_norm: str,
    resume_text_norm: str,
    resume_token_set: set,
    resume_compact: str,
) -> bool:
    if not phrase_norm:
        return False
    if phrase_norm in resume_text_norm:
        return True
    tokens = [t for t in phrase_norm.split() if t]
    if len(tokens) == 1 and tokens[0] in resume_token_set:
        return True
    compact_phrase = phrase_norm.replace(" ", "")
    if compact_phrase and compact_phrase in resume_compact:
        return True
    return False


def extract_skill_tokens(text: str, limit: int = 30) -> List[str]:
    tokens: List[str] = []
    seen = set()
    for token in TOKEN_RE.findall(text):
        normalized = normalize_token(token)
        if not normalized or normalized in STOPWORDS or normalized in seen:
            continue
        if SKILLS_SET and normalize_skill(normalized) not in SKILLS_SET:
            continue
        seen.add(normalized)
        cleaned = CLEAN_EDGE_RE.sub("", token)
        tokens.append(canonical_skill(cleaned))
        if len(tokens) >= limit:
            break
    return tokens


def extract_keyphrases(text: str, limit: int = 20) -> List[str]:
    if not text.strip():
        return []
    keyphrases = extract_keyphrases_rake(text, limit=limit)
    if not SKILLS_SET:
        return keyphrases
    filtered = []
    for phrase in keyphrases:
        if normalize_skill(phrase) in SKILLS_SET:
            filtered.append(canonical_skill(phrase))
    return filtered


def extract_tfidf_terms(text: str, limit: int = 30) -> List[str]:
    if not text.strip():
        return []
    vectorizer = TfidfVectorizer(
        stop_words=list(STOPWORDS) + ["job", "description", "responsibilities", "requirements"],
        ngram_range=(1, 3),
        lowercase=True,
        max_features=500,
    )
    try:
        tfidf = vectorizer.fit_transform([text])
    except ValueError:
        return []
    scores = tfidf.toarray()[0]
    terms = vectorizer.get_feature_names_out()
    ranked = sorted(zip(terms, scores), key=lambda x: x[1], reverse=True)
    top_terms = []
    seen = set()
    for term, _score in ranked:
        norm = normalize_phrase(term)
        if not norm or norm in seen:
            continue
        if SKILLS_SET and normalize_skill(norm) not in SKILLS_SET:
            continue
        seen.add(norm)
        top_terms.append(term)
        if len(top_terms) >= limit:
            break
    return top_terms


def extract_keyphrases_rake(text: str, limit: int = 20) -> List[str]:
    # Lightweight RAKE-style extraction without external dependencies.
    words = [normalize_token(tok) for tok in TOKEN_RE.findall(text)]
    phrases: List[List[str]] = []
    current: List[str] = []
    for word in words:
        if not word or word in RAKE_STOPWORDS:
            if current:
                phrases.append(current)
                current = []
            continue
        current.append(word)
    if current:
        phrases.append(current)

    # Build word scores (degree / frequency).
    freq = {}
    degree = {}
    for phrase in phrases:
        unique = phrase
        phrase_len = len(unique)
        for w in unique:
            freq[w] = freq.get(w, 0) + 1
            degree[w] = degree.get(w, 0) + phrase_len

    scores = {w: (degree[w] / freq[w]) for w in freq}
    ranked = []
    for phrase in phrases:
        if not phrase:
            continue
        if len(phrase) > 3:
            for i in range(0, len(phrase), 3):
                chunk = phrase[i : i + 3]
                if not chunk:
                    continue
                phrase_score = sum(scores[w] for w in chunk)
                phrase_text = " ".join(chunk)
                ranked.append((phrase_text, phrase_score))
        else:
            phrase_score = sum(scores[w] for w in phrase)
            phrase_text = " ".join(phrase)
            ranked.append((phrase_text, phrase_score))

    ranked.sort(key=lambda item: item[1], reverse=True)
    cleaned = []
    seen = set()
    for phrase, _score in ranked:
        normalized = normalize_phrase(phrase)
        if not normalized or normalized in seen:
            continue
        tokens = normalized.split()
        if not tokens or any(token in STOPWORDS for token in tokens):
            continue
        seen.add(normalized)
        cleaned.append(phrase)
        if len(cleaned) >= limit:
            break
    return cleaned


def parse_json_response(text: str) -> dict:
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = cleaned.rstrip("`").rstrip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
    return {}


def fallback_parse_resume(resume_text: str) -> dict:
    skills = merge_unique(
        extract_keyphrases(resume_text, limit=25)
        + extract_skill_tokens(resume_text, limit=25)
    )
    years = extract_resume_years(resume_text)
    return {
        "skills": skills[:40],
        "tools": [],
        "years_experience": years,
        "education": [],
        "certifications": [],
    }


_DATE_RE = re.compile(
    r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)?"
    r"[a-z]*[\s,/\-]*"
    r"(?P<year>\d{4})",
    re.IGNORECASE,
)
_MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_month_year(token: str) -> Optional[tuple[int, int]]:
    """Parse 'May 2022', '05/2022', '2022', 'Present', etc. Returns (year, month)."""
    if not token:
        return None
    s = str(token).strip().lower()
    if s in ("present", "current", "now", "today"):
        now = datetime.now(timezone.utc)
        return (now.year, now.month)
    # Try month-name form first
    m = _DATE_RE.search(s)
    if m:
        year = int(m.group("year"))
        month_token = (m.group("month") or "").lower()[:4]
        month = _MONTH_NUM.get(month_token) or _MONTH_NUM.get(month_token[:3], 1)
        return (year, month)
    # Try MM/YYYY
    parts = re.split(r"[\s/\-]+", s)
    if len(parts) >= 2:
        try:
            m_num = int(parts[0])
            year = int(parts[1])
            if 1 <= m_num <= 12 and 1900 < year < 2100:
                return (year, m_num)
        except ValueError:
            pass
    return None


def compute_employment_gaps_from_jobs(jobs: list[dict], min_gap_months: int = 3) -> list[dict]:
    """Deterministically compute employment gaps from work_experience entries.
    Replaces the model's unreliable employment_gaps output."""
    if not jobs:
        return []
    parsed_jobs = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        start = _parse_month_year(job.get("start_date") or job.get("start") or "")
        end = _parse_month_year(job.get("end_date") or job.get("end") or "Present")
        if not start or not end:
            continue
        parsed_jobs.append((start, end))
    # Sort by start date ascending so consecutive entries are properly ordered.
    parsed_jobs.sort(key=lambda p: (p[0][0], p[0][1]))
    gaps = []
    for prev, nxt in zip(parsed_jobs, parsed_jobs[1:]):
        prev_end_year, prev_end_month = prev[1]
        next_start_year, next_start_month = nxt[0]
        months = (next_start_year - prev_end_year) * 12 + (next_start_month - prev_end_month)
        if months >= min_gap_months:
            gaps.append({
                "start": f"{prev[1][1]:02d}/{prev[1][0]}",
                "end": f"{nxt[0][1]:02d}/{nxt[0][0]}",
                "duration_months": months,
            })
    return gaps


def parse_resume(resume_text: str, debug_info: dict | None = None) -> dict:
    if GENAI_CLIENT:
        try:
            parsed = gemini_parse_resume(resume_text)
            if not isinstance(parsed, dict):
                parsed = {}
            # Always recompute employment_gaps deterministically from work_experience —
            # the model frequently produces reversed dates and negative durations.
            jobs = parsed.get("work_experience") or []
            parsed["employment_gaps"] = compute_employment_gaps_from_jobs(jobs)
            if debug_info is not None:
                debug_info["parse_method"] = "gemini"
            return parsed
        except Exception as exc:
            logger.warning("Gemini parse failed, falling back to heuristics: %s", exc)
            if debug_info is not None:
                debug_info["parse_error"] = str(exc)
    if debug_info is not None:
        debug_info["parse_method"] = "heuristic"
    return fallback_parse_resume(resume_text)


def gemini_parse_resume(resume_text: str) -> dict:
    if not GENAI_CLIENT:
        detail = "GEMINI_API_KEY is not set."
        if GENAI_IMPORT_ERROR:
            detail = f"Gemini SDK unavailable: {GENAI_IMPORT_ERROR}"
        raise HTTPException(status_code=500, detail=detail)
    prompt = (
        "You are an expert CV parser. Extract ALL data from the CV/resume below and return ONLY valid JSON "
        "with exactly this structure (use null for missing strings, [] for missing lists, {} for missing objects):\n"
        "{\n"
        '  "name": "string or null",\n'
        '  "location": "City, Country or null",\n'
        '  "summary": "string or null",\n'
        '  "links": {"linkedin": "url or null", "github": "url or null", "portfolio": "url or null", "other": []},\n'
        '  "skills": ["technical skills, frameworks, languages, tools"],\n'
        '  "tools": ["additional tools not in skills"],\n'
        '  "soft_skills": ["communication", "leadership", ...],\n'
        '  "languages": [{"language": "English", "proficiency": "Native/Fluent/Conversational/Basic"}],\n'
        '  "years_experience": number_or_null,\n'
        '  "seniority_level": "junior or mid or senior or lead or principal or director or vp or c-suite",\n'
        '  "industry_domains": ["fintech", "healthcare", "saas", "e-commerce", etc.],\n'
        '  "management_experience": {"has_managed": false, "max_team_size": null},\n'
        '  "work_experience": [\n'
        '    {"company": "string", "title": "string", "start_date": "MM/YYYY or YYYY",\n'
        '     "end_date": "MM/YYYY or YYYY or Present", "bullets": ["bullet text verbatim"]}\n'
        '  ],\n'
        '  "employment_gaps": [{"start": "MM/YYYY", "end": "MM/YYYY", "duration_months": number}],\n'
        '  "projects": [\n'
        '    {"name": "string", "tech_stack": ["tech1", "tech2"], "bullets": ["description"]}\n'
        '  ],\n'
        '  "education": [\n'
        '    {"degree": "string", "institution": "string", "graduation_year": "string or null", "gpa": "string or null"}\n'
        '  ],\n'
        '  "certifications": ["string"],\n'
        '  "achievements": ["award, honour, publication, or recognition text"],\n'
        '  "quantified_achievements": ["verbatim bullet text containing a number, %, £, $, or metric"]\n'
        "}\n\n"
        "RULES:\n"
        "- Extract ALL work bullets verbatim — do not summarise or truncate.\n"
        "- For employment_gaps: compare consecutive work_experience entries by date; list any gap > 3 months with start/end dates and duration_months.\n"
        "- For seniority_level: infer from most recent job title and total years of experience.\n"
        "- For quantified_achievements: copy verbatim every bullet from work_experience or projects that contains any number, percentage, currency symbol, or measurable metric.\n"
        "- For management_experience: set has_managed=true if any role mentions managing, leading, or mentoring a team; set max_team_size to the largest team size mentioned.\n"
        "- For industry_domains: list the industries/sectors the candidate has worked in based on company descriptions and role context.\n"
        "- Return ONLY the JSON object, no markdown fences, no extra commentary."
    )
    response = GENAI_CLIENT.models.generate_content(
        model=GEMINI_PARSE_MODEL,
        contents=f"{prompt}\n\nRESUME:\n{resume_text}",
        config=gemini_generation_config(0),
    )
    return parse_json_response(getattr(response, "text", "") or "")


def analyze_cv_sections(
    resume_text: str,
    parsed_resume: dict,
    job_description: str,
) -> dict:
    """Deep per-section CV analysis powered by Gemini. Returns {} if Gemini unavailable."""
    if not GENAI_CLIENT:
        return {}
    try:
        parsed_summary = json.dumps({
            "name": parsed_resume.get("name"),
            "summary": parsed_resume.get("summary"),
            "seniority_level": parsed_resume.get("seniority_level"),
            "years_experience": parsed_resume.get("years_experience"),
            "skills": parsed_resume.get("skills", [])[:30],
            "soft_skills": parsed_resume.get("soft_skills", [])[:15],
            "work_experience": [
                {
                    "company": r.get("company"), "title": r.get("title"),
                    "start_date": r.get("start_date"), "end_date": r.get("end_date"),
                    "bullets": r.get("bullets", [])
                }
                for r in (parsed_resume.get("work_experience") or [])
            ],
            "projects": parsed_resume.get("projects", []),
            "education": parsed_resume.get("education", []),
            "certifications": parsed_resume.get("certifications", []),
            "employment_gaps": parsed_resume.get("employment_gaps", []),
            "management_experience": parsed_resume.get("management_experience", {}),
            "industry_domains": parsed_resume.get("industry_domains", []),
        }, indent=2)

        prompt = (
            "You are a world-class CV coach analysing a candidate's CV against a job description. "
            "Return ONLY a valid JSON object with no markdown fences matching this exact structure:\n\n"
            "{\n"
            '  "overall_quality_score": 0-100,\n'
            '  "career_narrative": "One sentence describing the candidate\'s career arc and progression.",\n'
            '  "ats_compatibility": {\n'
            '    "score": 0-100,\n'
            '    "issues": ["list of ATS compatibility problems found"],\n'
            '    "strengths": ["list of ATS strengths"]\n'
            '  },\n'
            '  "red_flags": ["e.g. Two roles under 8 months", "Unexplained 18-month gap"],\n'
            '  "interview_questions": [\n'
            '    "Specific question tied to a CV detail or gap, e.g. Walk me through the 40% improvement at Acme — what was your specific contribution?"\n'
            '  ],\n'
            '  "sections": {\n'
            '    "intro": {\n'
            '      "score": 0-100,\n'
            '      "grade": "A/B/C/D",\n'
            '      "strengths": ["strength 1"],\n'
            '      "issues": ["issue 1"],\n'
            '      "rewrite": "Improved summary tailored to this job description"\n'
            '    },\n'
            '    "skills": {\n'
            '      "score": 0-100,\n'
            '      "grade": "A/B/C/D",\n'
            '      "strengths": ["strength 1"],\n'
            '      "issues": ["issue 1"],\n'
            '      "jd_skills_present": ["skills from JD found in CV"],\n'
            '      "jd_skills_missing": ["skills from JD not found in CV"],\n'
            '      "listed_but_unevidenced": ["skills listed but not demonstrated in work or projects"]\n'
            '    },\n'
            '    "experience": {\n'
            '      "score": 0-100,\n'
            '      "grade": "A/B/C/D",\n'
            '      "overall_strengths": ["strength 1"],\n'
            '      "overall_issues": ["issue 1"],\n'
            '      "roles": [\n'
            '        {\n'
            '          "company": "Company name",\n'
            '          "title": "Job title",\n'
            '          "dates": "Start – End",\n'
            '          "role_score": 0-100,\n'
            '          "quantification_rate": "X/Y bullets have metrics",\n'
            '          "bullets": [\n'
            '            {\n'
            '              "text": "Original bullet text",\n'
            '              "quality": "strong or good or weak",\n'
            '              "issue": "Specific problem or null",\n'
            '              "rewrite": "Improved bullet or null"\n'
            '            }\n'
            '          ]\n'
            '        }\n'
            '      ]\n'
            '    },\n'
            '    "education": {\n'
            '      "score": 0-100,\n'
            '      "grade": "A/B/C/D",\n'
            '      "strengths": ["strength 1"],\n'
            '      "issues": ["issue 1"]\n'
            '    },\n'
            '    "projects": {\n'
            '      "score": 0-100,\n'
            '      "grade": "A/B/C/D",\n'
            '      "strengths": ["strength 1"],\n'
            '      "issues": ["issue 1"]\n'
            '    }\n'
            '  }\n'
            "}\n\n"
            "RULES:\n"
            "- overall_quality_score: holistic CV quality ignoring JD fit (writing, structure, impact clarity).\n"
            "- For experience.roles: include ALL roles from the parsed data. For each bullet, rate quality as 'strong' (has action verb + metric + impact), 'good' (has action verb or metric), or 'weak' (vague, passive, no metric). For 'weak' bullets, provide a specific rewrite.\n"
            "- For skills.jd_skills_missing: list JD skills/tools absent from the entire CV after checking the skills section, work bullets, project names, project descriptions, and project tech stacks.\n"
            "- For interview_questions: generate 4-6 questions a hiring manager would actually ask based on the CV's gaps, ambiguities, or impressive claims that need substantiation.\n"
            "- For intro.rewrite: write a crisp 3-sentence professional summary tailored to the JD.\n"
            "- If projects section is empty in the CV, set projects score=null and omit issues/strengths.\n"
            "- Return ONLY the JSON object, no markdown fences."
        )

        full_prompt = (
            f"{prompt}\n\n"
            f"PARSED CV DATA:\n{parsed_summary}\n\n"
            f"JOB DESCRIPTION:\n{job_description[:3000]}"
        )

        response = GENAI_CLIENT.models.generate_content(
            model=GEMINI_PARSE_MODEL,
            contents=full_prompt,
            config=gemini_generation_config(0),
        )
        result = parse_json_response(getattr(response, "text", "") or "")
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        logger.warning("analyze_cv_sections failed: %s", exc)
        return {}


def normalize_rewrite_response(payload: dict) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    diagnosis = payload.get("diagnosis")
    if not isinstance(diagnosis, dict):
        diagnosis = {}

    def normalize_section_items(items, key_name: str):
        normalized_items = []
        if not isinstance(items, list):
            return normalized_items
        for item in items:
            if not isinstance(item, dict):
                continue
            heading = str(item.get("heading") or item.get(key_name) or "").strip()
            bullets = item.get("bullets")
            if not isinstance(bullets, list):
                bullets = []
            clean_bullets = [str(bullet).strip() for bullet in bullets if str(bullet).strip()]
            if heading or clean_bullets:
                normalized_items.append(
                    {
                        "heading": heading,
                        "bullets": clean_bullets[:8],
                    }
                )
        return normalized_items

    contact_raw = payload.get("contact") or {}
    if not isinstance(contact_raw, dict):
        contact_raw = {}

    normalized = {
        "name": str(payload.get("name") or "").strip(),
        "contact": {
            k: str(contact_raw.get(k) or "").strip()
            for k in ("email", "phone", "linkedin", "location")
        },
        "role_target": str(payload.get("role_target") or "").strip(),
        "diagnosis": {
            "current_positioning": str(diagnosis.get("current_positioning") or "").strip(),
            "target_positioning": str(diagnosis.get("target_positioning") or "").strip(),
            "key_gaps": [
                str(item).strip()
                for item in (diagnosis.get("key_gaps") or [])
                if str(item).strip()
            ][:8],
        },
        "rewritten_summary": str(payload.get("rewritten_summary") or "").strip(),
        "skills_section": [
            {
                "category": str(item.get("category") or "").strip(),
                "items": [str(s).strip() for s in (item.get("items") or []) if str(s).strip()],
            }
            for item in (payload.get("skills_section") or [])
            if isinstance(item, dict)
        ],
        "education_section": [
            {
                "heading": str(item.get("heading") or "").strip(),
                "details": str(item.get("details") or "").strip(),
            }
            for item in (payload.get("education_section") or [])
            if isinstance(item, dict) and str(item.get("heading") or "").strip()
        ],
        "experience_section": normalize_section_items(payload.get("experience_section"), "company"),
        "projects_section": normalize_section_items(payload.get("projects_section"), "project_name"),
        "additional_keywords_to_include": [
            str(item).strip()
            for item in (payload.get("additional_keywords_to_include") or [])
            if str(item).strip()
        ][:15],
        "missing_information": [
            str(item).strip()
            for item in (payload.get("missing_information") or [])
            if str(item).strip()
        ][:12],
        "section_changes": [
            {
                "section": str(item.get("section") or "").strip(),
                "label": str(item.get("label") or "").strip(),
                "type": str(item.get("type") or "improved").strip(),
                "change": str(item.get("change") or "").strip(),
            }
            for item in (payload.get("section_changes") or [])
            if isinstance(item, dict) and str(item.get("change") or "").strip()
        ][:12],
    }
    return normalized


def validate_rewrite_skills(rewrite: dict, resume_text: str) -> dict:
    """Remove generated skills that are not evidenced in the source CV."""
    if not isinstance(rewrite, dict):
        return rewrite
    parsed_for_validation = {"_resume_text": resume_text}
    resume_norm = normalize_phrase(resume_text)
    resume_tokens = set(resume_norm.split())
    resume_compact = resume_norm.replace(" ", "")
    additional = [
        str(item).strip()
        for item in (rewrite.get("additional_keywords_to_include") or [])
        if str(item).strip()
    ]
    validated_sections = []
    removed = []

    for section in rewrite.get("skills_section") or []:
        if not isinstance(section, dict):
            continue
        kept_items = []
        for skill in section.get("items") or []:
            skill_text = str(skill).strip()
            if not skill_text:
                continue
            evidence = find_cv_evidence_for_requirement(skill_text, parsed_for_validation, resume_text)
            alias_present = any(
                phrase_in_resume(
                    normalize_phrase(alias),
                    resume_norm,
                    resume_tokens,
                    resume_compact,
                )
                for alias in _requirement_aliases(skill_text)
            )
            if evidence or alias_present:
                kept_items.append(skill_text)
            else:
                removed.append(skill_text)
        if kept_items:
            validated_sections.append({
                "category": str(section.get("category") or "").strip(),
                "items": merge_unique(kept_items),
            })

    for skill in removed:
        note = f"{skill} (add only if accurate)"
        if normalize_phrase(note) not in {normalize_phrase(item) for item in additional}:
            additional.append(note)

    rewrite["skills_section"] = validated_sections
    rewrite["additional_keywords_to_include"] = additional[:15]
    if removed:
        missing = rewrite.setdefault("missing_information", [])
        missing.append(
            "Removed unevidenced generated skills from the rewritten skills section: "
            + ", ".join(removed[:8])
        )
    return rewrite


def gemini_lite_audit_rewrite(rewrite: dict, resume_text: str, job_description: str) -> dict:
    """Cheap second-pass audit for generated CV claims. Local validators remain final authority."""
    if not GENAI_CLIENT or not isinstance(rewrite, dict):
        return {}

    audit_payload = {
        "rewritten_summary": rewrite.get("rewritten_summary") or "",
        "skills_section": rewrite.get("skills_section") or [],
        "experience_section": rewrite.get("experience_section") or [],
        "projects_section": rewrite.get("projects_section") or [],
    }
    prompt = f"""
You are auditing a generated CV rewrite against the original CV.

Return ONLY valid JSON with this exact schema:
{{
  "unsupported_claims": [
    {{
      "claim": "exact generated claim or skill",
      "source_section": "summary|skills_section|experience_section|projects_section",
      "reason": "short explanation",
      "severity": "remove|downgrade|keep"
    }}
  ],
  "safe_claims": ["claim text"]
}}

Rules:
- Flag a claim as remove if it is not evidenced in the original CV.
- Flag a claim as downgrade if it sounds stronger than the source CV proves.
- Do not flag a claim just because it is reworded; only flag unsupported or overclaimed content.
- Be strict for degrees, certifications, exact tools, regulated processes, formal reporting, compliance, management, and finance/control terms.

ORIGINAL CV:
{resume_text[:5000]}

JOB DESCRIPTION:
{job_description[:2500]}

GENERATED CV REWRITE:
{json.dumps(audit_payload, ensure_ascii=False, indent=2)[:5000]}
""".strip()

    try:
        response = GENAI_CLIENT.models.generate_content(
            model=GEMINI_LITE_MODEL,
            contents=prompt,
            config=gemini_generation_config(0),
        )
        parsed = parse_json_response(getattr(response, "text", "") or "")
    except Exception as exc:
        logger.warning("Gemini Lite rewrite audit failed: %s", exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def apply_rewrite_audit(rewrite: dict, audit: dict) -> dict:
    if not isinstance(rewrite, dict) or not isinstance(audit, dict):
        return rewrite

    unsupported = [
        item for item in (audit.get("unsupported_claims") or [])
        if isinstance(item, dict) and str(item.get("severity") or "").lower() in {"remove", "downgrade"}
    ]
    if not unsupported:
        return rewrite

    remove_claims = {
        normalize_phrase(item.get("claim") or "")
        for item in unsupported
        if str(item.get("severity") or "").lower() == "remove"
    }
    remove_claims.discard("")

    removed = []
    if remove_claims:
        cleaned_sections = []
        for section in rewrite.get("skills_section") or []:
            if not isinstance(section, dict):
                continue
            kept_items = []
            for skill in section.get("items") or []:
                skill_text = str(skill).strip()
                if normalize_phrase(skill_text) in remove_claims:
                    removed.append(skill_text)
                    continue
                kept_items.append(skill_text)
            if kept_items:
                cleaned_sections.append({
                    "category": str(section.get("category") or "").strip(),
                    "items": merge_unique(kept_items),
                })
        rewrite["skills_section"] = cleaned_sections

    additional = [
        str(item).strip()
        for item in (rewrite.get("additional_keywords_to_include") or [])
        if str(item).strip()
    ]
    for claim in removed:
        note = f"{claim} (add only if accurate)"
        if normalize_phrase(note) not in {normalize_phrase(item) for item in additional}:
            additional.append(note)
    rewrite["additional_keywords_to_include"] = additional[:15]

    audit_notes = []
    for item in unsupported[:8]:
        claim = str(item.get("claim") or "").strip()
        reason = str(item.get("reason") or "").strip()
        severity = str(item.get("severity") or "").strip()
        if claim:
            audit_notes.append(f"{severity}: {claim}" + (f" - {reason}" if reason else ""))
    if audit_notes:
        missing = rewrite.setdefault("missing_information", [])
        missing.append("Gemini Lite rewrite audit flagged unsupported or overclaimed content: " + "; ".join(audit_notes))
    return rewrite


def audit_and_validate_rewrite(rewrite: dict, resume_text: str, job_description: str) -> dict:
    rewrite = validate_rewrite_skills(rewrite, resume_text)
    audit = gemini_lite_audit_rewrite(rewrite, resume_text, job_description)
    rewrite = apply_rewrite_audit(rewrite, audit)
    rewrite["rewrite_audit"] = {
        "enabled": bool(GENAI_CLIENT),
        "model": GEMINI_LITE_MODEL,
        "unsupported_count": len(audit.get("unsupported_claims") or []) if isinstance(audit, dict) else 0,
    }
    return rewrite


def extract_openai_output_text(response_json: dict) -> str:
    if not isinstance(response_json, dict):
        return ""

    output = response_json.get("output")
    if not isinstance(output, list):
        return ""

    parts: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "output_text":
                text = str(block.get("text") or "").strip()
                if text:
                    parts.append(text)
    return "\n".join(parts).strip()


def openai_rewrite_cv(
    resume_text: str,
    job_description: str,
    role_fit_breakdown: dict | None = None,
) -> dict:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set.")

    analysis_blob = json.dumps(role_fit_breakdown or {}, indent=2)
    instructions = (
        "You rewrite CVs for one specific role. "
        "Ground every rewrite in the source CV and the structured analysis. "
        "Do not invent employers, titles, dates, metrics, technologies, certifications, or outcomes. "
        "If evidence is missing, keep the wording factual and list the missing fact in missing_information. "
        "Return only valid JSON."
    )
    prompt = f"""
Rewrite this CV for the target role.

Return ONLY valid JSON with this exact schema:
{{
  "name": "Full name from CV",
  "contact": {{
    "email": "email from CV or empty string",
    "phone": "phone from CV or empty string",
    "linkedin": "linkedin URL or handle from CV or empty string",
    "location": "city/country from CV or empty string"
  }},
  "role_target": "string",
  "diagnosis": {{
    "current_positioning": "string",
    "target_positioning": "string",
    "key_gaps": ["string"]
  }},
  "rewritten_summary": "string",
  "skills_section": [
    {{
      "category": "Technical Skills",
      "items": ["string"]
    }}
  ],
  "education_section": [
    {{
      "heading": "Degree | University | Year",
      "details": "Classification or empty string"
    }}
  ],
  "experience_section": [
    {{
      "heading": "Role | Company | Dates",
      "bullets": ["string"]
    }}
  ],
  "projects_section": [
    {{
      "heading": "project name",
      "bullets": ["string"]
    }}
  ],
  "additional_keywords_to_include": ["string"],
  "missing_information": ["string"]
}}

Use concise UK CV style — action verb + outcome, no first-person pronouns. Optimize for role fit. For skills_section, include ONLY skills explicitly evidenced in the CV. Do not add JD skills unless the source CV directly proves them. Preserve education as-is. Do not invent facts. For bullets missing a quantitative metric that would strengthen them, append [METRIC: short question] at the end. Add section_changes entries explaining what changed and why for each major rewrite. The rewritten_summary MUST end with a formal closing sentence explicitly naming the exact role title and company from the JD (e.g. "Eager to bring this expertise to the [Role Title] role at [Company]."). For additional_keywords_to_include, list every JD skill/keyword NOT already evidenced in the CV — these are skills the candidate should review and add only if accurate.

Source CV:
{resume_text}

Job description:
{job_description}

Structured analysis:
{analysis_blob}
""".strip()

    response = requests.post(
        f"{OPENAI_BASE_URL.rstrip('/')}/responses",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_REWRITE_MODEL,
            "instructions": instructions,
            "input": prompt,
            "max_output_tokens": 2200,
        },
        timeout=60,
    )
    if response.status_code >= 400:
        detail = f"OpenAI rewrite request failed ({response.status_code})."
        try:
            error_payload = response.json()
            message = ((error_payload.get("error") or {}).get("message") or "").strip()
            if message:
                detail = f"OpenAI rewrite request failed ({response.status_code}): {message}"
        except ValueError:
            pass
        raise HTTPException(status_code=502, detail=detail)

    payload = response.json()
    text = extract_openai_output_text(payload)
    parsed = parse_json_response(text)
    normalized = normalize_rewrite_response(parsed)
    normalized = audit_and_validate_rewrite(normalized, resume_text, job_description)
    if not normalized["rewritten_summary"] and not normalized["experience_section"]:
        raise HTTPException(status_code=502, detail="OpenAI rewrite generation returned an invalid response.")
    return normalized


def generate_cv_rewrite(
    resume_text: str,
    job_description: str,
    role_fit_breakdown: dict | None = None,
) -> dict:
    if OPENAI_API_KEY:
        return openai_rewrite_cv(
            resume_text=resume_text,
            job_description=job_description,
            role_fit_breakdown=role_fit_breakdown,
        )
    return gemini_rewrite_cv(
        resume_text=resume_text,
        job_description=job_description,
        role_fit_breakdown=role_fit_breakdown,
    )


def gemini_rewrite_cv(
    resume_text: str,
    job_description: str,
    role_fit_breakdown: dict | None = None,
) -> dict:
    if not GENAI_CLIENT:
        detail = "GEMINI_API_KEY is not set."
        if GENAI_IMPORT_ERROR:
            detail = f"Gemini SDK unavailable: {GENAI_IMPORT_ERROR}"
        raise HTTPException(status_code=503, detail=detail)

    analysis_blob = json.dumps(role_fit_breakdown or {}, indent=2)
    prompt = f"""
You are rewriting a CV for one specific role.

Your job:
1. Extract the candidate's name and contact details from the CV header.
2. Diagnose how the current CV is positioned.
3. Rewrite the summary, skills, experience, projects, and preserve education for the target role.
4. Ground every rewrite in the source CV and analysis evidence.
5. Do NOT invent employers, titles, dates, metrics, technologies, certifications, or outcomes.
6. If a metric is missing, write a strong factual bullet without inventing the number, and list the missing metric in missing_information.
7. Use concise UK CV style bullet points — action verb + outcome, no first-person pronouns.
8. Optimize for role fit: responsibilities, ownership, governance, stakeholder communication.
9. For skills_section, include ONLY skills explicitly evidenced in the CV. Do not add JD skills unless the source CV directly proves them. Put missing JD keywords in additional_keywords_to_include with the note that they should be added only if accurate. Group into Technical Skills and Soft Skills (or other logical groups).
10. For education_section, extract as-is from the CV — do not rewrite or omit.
11. For every bullet where a specific quantitative metric (%, £/$, number, timeframe) is absent but would materially change how a recruiter reads it, append exactly [METRIC: <short question>] at the end of the bullet. E.g. "Reduced churn [METRIC: by what %? over what period?]". Only add [METRIC:] where a real number would noticeably strengthen the line.
12. For section_changes, write one concise entry per major rewrite — what changed and why it improves the candidate's positioning for this role. Be specific, not generic.
13. The rewritten_summary MUST end with a formal closing sentence that explicitly names the exact role title and company from the JD. E.g. "Eager to bring this expertise to the Senior Data Engineer role at Currys." Extract the company name and role title directly from the job description — do not use placeholders.
14. For additional_keywords_to_include, list every skill and keyword from the JD that is NOT already evidenced in the CV. These are skills the candidate should review and add if accurate — do not include skills already in the CV.

Return ONLY valid JSON with this exact schema:
{{
  "name": "Full name from CV",
  "contact": {{
    "email": "email from CV or empty string",
    "phone": "phone from CV or empty string",
    "linkedin": "linkedin URL or handle from CV or empty string",
    "location": "city/country from CV or empty string"
  }},
  "role_target": "string",
  "diagnosis": {{
    "current_positioning": "string",
    "target_positioning": "string",
    "key_gaps": ["string"]
  }},
  "rewritten_summary": "string",
  "skills_section": [
    {{
      "category": "Technical Skills",
      "items": ["string"]
    }}
  ],
  "education_section": [
    {{
      "heading": "Degree | University | Year",
      "details": "Classification or extra detail, or empty string"
    }}
  ],
  "experience_section": [
    {{
      "heading": "Role | Company | Dates",
      "bullets": ["bullet text, optionally ending with [METRIC: question]"]
    }}
  ],
  "projects_section": [
    {{
      "heading": "project name",
      "bullets": ["bullet text, optionally ending with [METRIC: question]"]
    }}
  ],
  "additional_keywords_to_include": ["string"],
  "missing_information": ["string"],
  "section_changes": [
    {{
      "section": "summary|skills|experience|projects",
      "label": "Short label — e.g. role heading or section name",
      "type": "repositioned|optimised|restructured|added",
      "change": "One specific sentence: what changed and why it helps for this role"
    }}
  ]
}}

Source CV:
{resume_text}

Job description:
{job_description}

Structured analysis:
{analysis_blob}
""".strip()

    response = GENAI_CLIENT.models.generate_content(
        model=GEMINI_REWRITE_MODEL,
        contents=prompt,
        config=gemini_generation_config(0.2),
    )
    parsed = parse_json_response(getattr(response, "text", "") or "")
    normalized = normalize_rewrite_response(parsed)
    normalized = audit_and_validate_rewrite(normalized, resume_text, job_description)
    if not normalized["rewritten_summary"] and not normalized["experience_section"]:
        raise HTTPException(status_code=502, detail="CV rewrite generation returned an invalid response.")
    return normalized


def gemini_embed_texts(texts: List[str]) -> List[List[float]]:
    if not GENAI_CLIENT:
        detail = "GEMINI_API_KEY is not set."
        if GENAI_IMPORT_ERROR:
            detail = f"Gemini SDK unavailable: {GENAI_IMPORT_ERROR}"
        raise HTTPException(status_code=500, detail=detail)
    result = GENAI_CLIENT.models.embed_content(
        model=GEMINI_EMBED_MODEL,
        contents=texts,
    )
    embeddings: List[List[float]] = []
    for embedding in result.embeddings:
        if isinstance(embedding, dict) and "values" in embedding:
            embeddings.append(embedding["values"])
        elif hasattr(embedding, "values"):
            embeddings.append(embedding.values)
        elif hasattr(embedding, "embedding"):
            embeddings.append(embedding.embedding)
        else:
            embeddings.append(embedding)
    return embeddings


def compute_semantic_score(
    resume_text: str,
    job_description: str,
    debug_info: dict | None = None,
) -> float:
    if GENAI_CLIENT:
        try:
            embeddings = gemini_embed_texts([resume_text, job_description])
            similarity = cosine_similarity(embeddings[0], embeddings[1])
            if debug_info is not None:
                debug_info["semantic_method"] = "gemini"
            return max(0.0, min(100.0, similarity * 100))
        except Exception as exc:
            logger.warning("Gemini semantic scoring failed, falling back to TF-IDF: %s", exc)
            if debug_info is not None:
                debug_info["semantic_error"] = str(exc)
    similarity = tfidf_similarity(resume_text, job_description)
    if debug_info is not None:
        debug_info["semantic_method"] = "tfidf"
    return max(0.0, min(100.0, similarity * 100))


def extract_must_have_skills(job_description: str, limit: int = 20) -> List[str]:
    lines = [line.strip() for line in job_description.splitlines()]
    capture = False
    chunks: List[str] = []
    for line in lines:
        lower = line.lower()
        if any(marker in lower for marker in MUST_HAVE_MARKERS):
            capture = True
            if ":" in line:
                chunks.append(line.split(":", 1)[1])
            continue
        if capture:
            if not line or any(marker in lower for marker in STOP_SECTION_MARKERS):
                capture = False
                continue
            chunks.append(line)

    section_text = "\n".join(chunks).strip()
    if not section_text:
        must_lines = [line for line in lines if "must" in line.lower() or "required" in line.lower()]
        section_text = "\n".join(must_lines) if must_lines else job_description

    return extract_keyphrases(section_text, limit=limit)


def extract_required_years(job_description: str) -> Optional[int]:
    values: List[int] = []
    for match in AT_LEAST_YEARS_RE.findall(job_description):
        values.append(int(match))
    scrubbed = AT_LEAST_YEARS_RE.sub("", job_description)
    for match in RANGE_YEARS_RE.findall(scrubbed):
        values.append(int(match[0]))
    scrubbed2 = RANGE_YEARS_RE.sub("", scrubbed)
    for match in PLUS_YEARS_RE.findall(scrubbed2):
        v = int(match)
        if v <= 20:  # ignore obvious noise like founding years
            values.append(v)
    return min(values) if values else None


def extract_resume_years(resume_text: str) -> Optional[int]:
    values = [int(match) for match in PLUS_YEARS_RE.findall(resume_text)]
    return max(values) if values else None


def _parse_work_date(date_str: str) -> Optional[int]:
    """Return year as int from a date string like '03/2021', '2021', 'Present', or None."""
    if not date_str:
        return None
    s = str(date_str).strip().lower()
    if s in ("present", "current", "now", "ongoing", "till date", "to date"):
        import datetime
        return datetime.date.today().year
    match = re.search(r"\b(20\d{2}|19\d{2})\b", s)
    if match:
        return int(match.group(1))
    return None


def years_from_work_experience(work_exp: list) -> Optional[int]:
    """Calculate total years of experience from parsed work_experience date ranges."""
    if not work_exp or not isinstance(work_exp, list):
        return None
    periods: List[tuple] = []
    for entry in work_exp:
        if not isinstance(entry, dict):
            continue
        start = _parse_work_date(entry.get("start_date") or "")
        end = _parse_work_date(entry.get("end_date") or "")
        if start and end and end >= start:
            periods.append((start, end))
    if not periods:
        return None
    # Sum unique years across periods (handle overlaps via union)
    all_years: set = set()
    for start, end in periods:
        for yr in range(start, end + 1):
            all_years.add(yr)
    return len(all_years) if all_years else None


def split_text_units(text: str) -> List[str]:
    if not text:
        return []

    units: List[str] = []
    seen = set()
    raw_chunks = re.split(r"[\r\n]+", text)
    for chunk in raw_chunks:
        parts = re.split(r"[;•]+", chunk)
        for part in parts:
            line = part.strip().strip("-*• ").strip()
            if len(line.split()) < 3:
                continue
            normalized = normalize_phrase(line)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            units.append(line)

    if units:
        return units

    for part in re.split(r"(?<=[.!?])\s+", text):
        line = part.strip().strip("-*• ").strip()
        if len(line.split()) < 3:
            continue
        normalized = normalize_phrase(line)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        units.append(line)
    return units


def extract_action_phrases(text: str) -> List[str]:
    tokens = [normalize_token(tok) for tok in TOKEN_RE.findall(text)]
    phrases: List[str] = []
    seen = set()
    for idx, token in enumerate(tokens):
        if token not in ACTION_VERBS:
            continue
        canonical_verb = ACTION_VERB_BASE.get(token, token)
        obj_tokens: List[str] = []
        for nxt in tokens[idx + 1 :]:
            if nxt in STOPWORDS:
                continue
            if nxt in ACTION_VERBS and obj_tokens:
                break
            if not nxt:
                continue
            obj_tokens.append(nxt)
            if len(obj_tokens) >= 2:
                break
        if not obj_tokens:
            continue
        phrase = " ".join([canonical_verb, *obj_tokens])
        if phrase in seen:
            continue
        seen.add(phrase)
        phrases.append(phrase)
    return phrases


def extract_seniority_terms(text: str) -> List[str]:
    normalized = normalize_phrase(text)
    terms: List[str] = []
    seen = set()
    for raw, canonical in SENIORITY_ALIASES.items():
        if raw in normalized and canonical not in seen:
            seen.add(canonical)
            terms.append(canonical)
    return terms


_COMPANY_SUBJECT_RE = re.compile(
    r'^\s*(\([A-Za-z]+:[A-Za-z]+\)|we\b|our\b)',
    re.IGNORECASE,
)
_COMPANY_DESC_SIGNALS = (
    "is a leading", "is an industry", "is dedicated to", "dedicated to helping",
    "solutions provider", "our portfolio", "our comprehensive", "our mission",
    "our vision", "our customers", "helping customers",
)
_CANDIDATE_HINTS = (
    "you will", "you'll", "you are", "you should", "you must",
    "candidate will", "the candidate", "successful candidate",
    "responsible for", "responsibilities", "what you'll do",
    "what you will do", "day to day", "in this role",
)
_ACTION_VERBS_SET = set(ACTION_VERBS)


def extract_job_responsibilities(job_description: str, limit: int = 25) -> List[dict]:
    """Extract essential + nice-to-have requirements from a JD using Gemini; regex fallback."""
    if GENAI_CLIENT:
        try:
            response = GENAI_CLIENT.models.generate_content(
                model=GEMINI_PARSE_MODEL,
                contents=(
                    "Extract the candidate requirements from this job description — the things the candidate must HAVE or BRING to be hired.\n\n"
                    "Separate them into:\n"
                    "- essential: mandatory requirements labelled 'Essential', 'Required', 'Must have', 'Qualifications', 'What you need', etc.\n"
                    "- nice_to_have: preferred/optional requirements labelled 'Nice to have', 'Desirable', 'Preferred', 'Bonus', 'Advantageous', etc.\n\n"
                    "Return ONLY valid JSON:\n"
                    "{\"requirements\": [{\"text\": \"...\", \"category\": \"essential\"}, {\"text\": \"...\", \"category\": \"nice_to_have\"}, ...]}\n\n"
                    "STRICT rules:\n"
                    "- Include ONLY things the candidate must HAVE or DEMONSTRATE — skills, experience, qualifications, behaviours.\n"
                    "- Exclude entirely: company benefits, perks, salary, equity, flexible working, onboarding, about-the-company text, job duties/tasks.\n"
                    "- Each requirement should be a concise standalone statement.\n"
                    "- Remove bullet markers, numbers, and leading dashes.\n"
                    f"- Return at most {limit} requirements total.\n\n"
                    f"{job_description[:4000]}"
                ),
                config=gemini_generation_config(0),
            )
            raw = getattr(response, "text", "") or ""
            parsed = parse_json_response(raw)
            if isinstance(parsed, dict):
                reqs = parsed.get("requirements") or []
                if isinstance(reqs, list) and reqs:
                    result = []
                    seen = set()
                    for req in reqs[:limit]:
                        if not isinstance(req, dict):
                            continue
                        text = str(req.get("text", "")).strip().strip("-*• ").strip()
                        category = str(req.get("category", "essential")).strip()
                        if category not in ("essential", "nice_to_have"):
                            category = "essential"
                        if len(text.split()) < 3:
                            continue
                        norm = normalize_phrase(text)
                        if norm in seen:
                            continue
                        seen.add(norm)
                        result.append({
                            "text": text,
                            "normalized": norm,
                            "action_phrases": extract_action_phrases(text),
                            "category": category,
                        })
                    if result:
                        return result
        except Exception as exc:
            logger.warning("Gemini requirement extraction failed, using regex: %s", exc)

    # Regex fallback
    responsibilities: List[dict] = []
    seen: set = set()
    for line in split_text_units(job_description):
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            for clause in re.split(r",|\band\b", sentence, flags=re.IGNORECASE):
                clause_text = clause.strip().strip("-*• ").strip()
                if len(clause_text.split()) < 4:
                    continue
                normalized = normalize_phrase(clause_text)
                tokens = normalized.split()
                starts_imperative = bool(tokens) and tokens[0] in _ACTION_VERBS_SET
                has_candidate_signal = any(h in normalized for h in _CANDIDATE_HINTS)
                if not starts_imperative and not has_candidate_signal:
                    continue
                action_phrases = extract_action_phrases(clause_text)
                if not action_phrases and not has_candidate_signal:
                    continue
                if _COMPANY_SUBJECT_RE.match(clause_text):
                    continue
                if any(sig in normalized for sig in _COMPANY_DESC_SIGNALS):
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                responsibilities.append({
                    "text": clause_text,
                    "normalized": normalized,
                    "action_phrases": action_phrases,
                })
                if len(responsibilities) >= limit:
                    return responsibilities
    return responsibilities


ATOMIC_REQUIREMENT_HINTS = (
    "especially",
    "including",
    "such as",
    "with",
)


STRICT_TOOL_TERMS = {
    "react",
    "typescript",
    "javascript",
    "docker",
    "kubernetes",
    "c++",
    "c#",
    "cpp",
    "equities",
    "equity",
    "options",
    "securities",
    "security",
    "time series",
    "timeseries",
    "lakehouse",
    "messaging middleware",
}


ALTERNATIVE_LIST_MARKERS = (
    "at least one",
    "one of",
    "any of",
)


DEGREE_TERMS = (
    "degree", "bachelor", "bachelors", "bachelor's", "master", "masters",
    "master's", "ba", "bsc", "bs", "ma", "msc", "ms", "llb", "jd", "phd",
)
SHORT_DEGREE_TERMS = {"ba", "bs", "ma", "ms", "jd"}

DEGREE_SUBJECT_ALIASES = {
    "accounting": ("accounting", "accountancy"),
    "finance": ("finance", "financial"),
    "law": ("law", "llb", "legal studies", "juris", "juris doctor"),
    "english": ("english", "english literature", "english language"),
    "computer science": ("computer science", "computing", "software engineering"),
    "engineering": ("engineering",),
    "mathematics": ("mathematics", "maths", "math"),
    "economics": ("economics",),
    "business": ("business", "business administration", "management"),
}

POSTGRADUATE_DEGREE_TERMS = (
    "post graduate", "postgraduate", "masters", "master's", "master",
    "msc", "ma", "phd", "doctorate",
)

UNDERGRADUATE_DEGREE_TERMS = (
    "undergraduate degree", "bachelor", "bachelors", "bachelor's",
    "bsc", "ba", "bs", "llb",
)


SHORT_POSTGRADUATE_DEGREE_TERMS = {"ma", "ms", "msc", "phd"}


def _is_postgraduate_degree_requirement(req_norm: str, tokens: set) -> bool:
    if "post graduate qualification" in req_norm or "postgraduate qualification" in req_norm:
        return True
    if "post graduate degree" in req_norm or "postgraduate degree" in req_norm:
        return True
    if re.search(r"\b(master|masters|master s|msc|ma|phd|doctorate)\b\s+(degree|qualification)\b", req_norm):
        return True
    if re.search(r"\b(degree|qualification)\b\s+(at\s+)?\b(master|masters|master s|msc|ma|phd|doctorate)\b", req_norm):
        return True
    if tokens.intersection({"msc", "phd", "doctorate"}) and (
        "qualification" in tokens or "degree" in tokens
    ):
        return True
    return False

REGULATED_PROCESS_TERMS = (
    "regulatory reporting", "regulatory reports", "regulatory filing",
    "regulatory filings", "securities reporting", "statutory reporting",
    "compliance reporting", "hipaa", "gdpr", "sox",
    "regulatory requirement", "regulatory requirements",
)

CONTROL_GOVERNANCE_TERMS = (
    "financial control", "product control", "balance sheet governance",
    "control environment", "proofing", "reconciliation", "reconcile",
    "reconciliations", "attestation", "attestations",
)

REPORTING_STRICT_TERMS = (
    "financial statements", "balance sheet", "income statement",
    "quarterly reporting", "annual reporting", "management reporting",
    "external reporting",
)


def _extract_degree_subjects(req_norm: str) -> set:
    subjects = set()
    for canonical, aliases in DEGREE_SUBJECT_ALIASES.items():
        if any(_phrase_present_in_normalized_text(normalize_phrase(alias), req_norm) for alias in aliases):
            subjects.add(canonical)
    return subjects


def _requirement_policy(requirement: str) -> dict:
    """Classify which evidence sections and specificity can prove a requirement."""
    req_norm = normalize_phrase(requirement)
    tokens = set(req_norm.split())
    policy = {
        "type": "general",
        "strict": False,
        "allowed_sections": {"skills", "experience", "projects", "summary", "education", "certifications"},
        "subjects": set(),
    }

    is_degree_project_requirement = bool(re.search(r"\bdegree\s+projects?\b", req_norm))
    if _is_postgraduate_degree_requirement(req_norm, tokens):
        policy.update({
            "type": "postgraduate_degree",
            "strict": True,
            "allowed_sections": {"education"},
            "subjects": _extract_degree_subjects(req_norm),
        })
        return policy

    has_degree_token = not is_degree_project_requirement and any(
        (term in tokens) if term in SHORT_DEGREE_TERMS else (term in tokens or term in req_norm)
        for term in DEGREE_TERMS
    )
    if has_degree_token:
        policy.update({
            "type": "degree",
            "strict": True,
            "allowed_sections": {"education"},
            "subjects": _extract_degree_subjects(req_norm),
        })
        return policy

    if any(term in req_norm for term in ("certification", "certificate", "certified", "licence", "license", "qts")):
        policy.update({
            "type": "certification",
            "strict": True,
            "allowed_sections": {"certifications", "education"},
        })
        return policy

    if any(term in req_norm for term in REGULATED_PROCESS_TERMS):
        policy.update({
            "type": "regulated_process",
            "strict": True,
            "allowed_sections": {"skills", "experience", "projects", "certifications"},
        })
        return policy

    if any(term in req_norm for term in CONTROL_GOVERNANCE_TERMS):
        policy.update({
            "type": "control_or_governance",
            "strict": True,
            "allowed_sections": {"skills", "experience", "projects"},
        })
        return policy

    if any(term in req_norm for term in REPORTING_STRICT_TERMS):
        policy.update({
            "type": "reporting",
            "strict": True,
            "allowed_sections": {"skills", "experience", "projects"},
        })
        return policy

    if any(term in req_norm for term in STRICT_TOOL_TERMS):
        policy.update({
            "type": "exact_tool",
            "strict": True,
            "allowed_sections": {"skills", "experience", "projects"},
        })
        return policy

    raw_req = str(requirement or "").lower()
    is_early_career_range = (
        bool(re.search(r"\b0\s*(?:-|–|—|to)\s*1\s+years?\b", raw_req))
        or "0 1 years" in req_norm
    )
    if is_early_career_range and any(
        term in req_norm
        for term in ("academic project", "academic projects", "internship", "internships", "placement", "placements", "early professional")
    ):
        policy.update({
            "type": "early_career_experience",
            "strict": False,
            "allowed_sections": {"education", "experience", "projects"},
        })
        return policy

    if re.search(r"\b\d+\+?\s+years?\b", req_norm):
        policy.update({
            "type": "years_experience",
            "strict": True,
            "allowed_sections": {"experience"},
        })
        return policy

    if "project management" in req_norm or "manage multiple deadlines" in req_norm or "deliverables" in req_norm:
        policy.update({
            "type": "project_management",
            "allowed_sections": {"experience", "projects", "summary"},
        })
        return policy

    if any(term in req_norm for term in ("people management", "line management", "managed team", "manage team", "mentoring", "mentor")):
        policy.update({
            "type": "management",
            "strict": True,
            "allowed_sections": {"experience"},
        })
        return policy

    if "roadmap" in req_norm or "roadmap ownership" in req_norm:
        policy.update({
            "type": "product_ownership",
            "allowed_sections": {"experience", "projects", "summary"},
        })
        return policy

    if any(term in req_norm for term in ("communication", "communicate", "written", "verbal", "presentation", "stakeholder")):
        policy.update({
            "type": "communication",
            "allowed_sections": {"skills", "experience", "projects", "summary"},
        })
        return policy

    if any(term in req_norm for term in ("data quality", "data integrity", "validation", "accuracy", "completeness")):
        policy.update({
            "type": "data_quality",
            "allowed_sections": {"skills", "experience", "projects", "summary"},
        })
        return policy

    if any(term in req_norm for term in ("experience in", "experience with", "exposure to", "professional experience")):
        policy.update({
            "type": "professional_experience",
            "allowed_sections": {"experience", "projects", "summary"},
        })
        return policy

    if any(term in req_norm for term in ("interest in", "knowledge of", "market knowledge", "industry knowledge", "domain")):
        policy.update({
            "type": "domain_exposure",
            "allowed_sections": {"experience", "projects", "education", "summary"},
        })
    return policy


def _evidence_has_degree_subject(evidence_norm: str, subjects: set) -> bool:
    if not subjects:
        return True
    for subject in subjects:
        aliases = DEGREE_SUBJECT_ALIASES.get(subject, (subject,))
        if any(_phrase_present_in_normalized_text(normalize_phrase(alias), evidence_norm) for alias in aliases):
            return True
    return False


def _evidence_has_postgraduate_degree(evidence_norm: str) -> bool:
    return any(
        _phrase_present_in_normalized_text(normalize_phrase(term), evidence_norm)
        for term in POSTGRADUATE_DEGREE_TERMS
    )


def _policy_explicit_terms(policy: dict, requirement: str) -> List[str]:
    req_norm = normalize_phrase(requirement)
    if policy["type"] in {"degree", "postgraduate_degree"}:
        terms = []
        for subject in policy.get("subjects") or []:
            terms.extend(DEGREE_SUBJECT_ALIASES.get(subject, (subject,)))
        return merge_unique(terms)
    if policy["type"] == "regulated_process":
        return [term for term in REGULATED_PROCESS_TERMS if term in req_norm] or list(REGULATED_PROCESS_TERMS)
    if policy["type"] == "control_or_governance":
        return [term for term in CONTROL_GOVERNANCE_TERMS if term in req_norm] or list(CONTROL_GOVERNANCE_TERMS)
    if policy["type"] == "reporting":
        return [term for term in REPORTING_STRICT_TERMS if term in req_norm] or list(REPORTING_STRICT_TERMS)
    return []


def validate_evidence_for_requirement(requirement: str, evidence: str, confidence: str) -> dict | None:
    policy = _requirement_policy(requirement)
    section = infer_evidence_section(evidence)
    evidence_norm = normalize_phrase(evidence)
    if section not in policy["allowed_sections"]:
        return None

    if policy["type"] == "degree":
        if not _evidence_has_degree_subject(evidence_norm, policy.get("subjects") or set()):
            return None
        return {"confidence": "strong", "section": section, "policy": policy}

    if policy["type"] == "postgraduate_degree":
        if not _evidence_has_postgraduate_degree(evidence_norm):
            return None
        if not _evidence_has_degree_subject(evidence_norm, policy.get("subjects") or set()):
            return None
        return {"confidence": "strong", "section": section, "policy": policy}

    explicit_terms = _policy_explicit_terms(policy, requirement)
    if policy["strict"] and explicit_terms:
        explicit_hit = any(
            _phrase_present_in_normalized_text(normalize_phrase(term), evidence_norm)
            for term in explicit_terms
        )
        if not explicit_hit:
            return None

    adjusted = confidence if confidence in ("strong", "partial") else "partial"
    if policy["type"] == "professional_experience" and section in {"projects", "summary"}:
        adjusted = "partial"
    if policy["type"] == "project_management" and section in {"projects", "summary"}:
        adjusted = "partial"
    return {"confidence": adjusted, "section": section, "policy": policy}


def _split_list_fragments(text: str) -> List[str]:
    raw_parts = re.split(r",|/|&|\band\b|\bor\b", text, flags=re.IGNORECASE)
    parts: List[str] = []
    for part in raw_parts:
        cleaned = re.sub(
            r"^(and|or)\s+",
            "",
            str(part or "").strip(" -:;,.()"),
            flags=re.IGNORECASE,
        )
        if not cleaned:
            continue
        parts.append(cleaned)
    return merge_unique(parts)


def _strip_requirement_lead(text: str) -> str:
    return re.sub(
        r"^(excellent|solid|strong|modern|very strong|significant|hands on|hands-on|knowledge of|experience with|experience in|ability to|an interest in|interest in)\s+",
        "",
        str(text or "").strip(),
        flags=re.IGNORECASE,
    ).strip(" -:;,.")


def _looks_like_alternative_list(source: str, right: str) -> bool:
    source_norm = normalize_phrase(source)
    right_norm = normalize_phrase(right)
    return (
        any(marker in source_norm for marker in ALTERNATIVE_LIST_MARKERS)
        or f" {right_norm} ".find(" or ") != -1
    )


def extract_alternative_skill_groups(text: str) -> List[set[str]]:
    groups: List[set[str]] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for marker in ("such as", "including"):
            match = re.search(rf"\b{re.escape(marker)}\b(.+)$", line, flags=re.IGNORECASE)
            if not match:
                continue
            right = match.group(1).strip(" -:;,.")
            if not _looks_like_alternative_list(line, right):
                continue
            fragments = _split_list_fragments(right)
            group = {normalize_phrase(fragment) for fragment in fragments if normalize_phrase(fragment)}
            if len(group) >= 2:
                groups.append(group)
            break
    return groups


def filter_satisfied_alternative_missing_skills(items: List[dict], job_description: str) -> List[dict]:
    groups = extract_alternative_skill_groups(job_description)
    if not groups:
        return items

    item_norms = {id(item): normalize_phrase(item.get("skill") or "") for item in items}
    suppress: set[str] = set()
    for group in groups:
        matching_items = [item for item in items if item_norms.get(id(item)) in group]
        if not matching_items:
            continue
        group_satisfied = any((item.get("status") or "") in {"present", "partial"} for item in matching_items)
        if group_satisfied:
            suppress.update(
                item_norms[id(item)]
                for item in matching_items
                if (item.get("status") or "missing") == "missing"
            )
    if not suppress:
        return items
    return [item for item in items if item_norms.get(id(item)) not in suppress]


def _append_alternative_atoms(atoms: List[dict], fragments: List[str], group_id: str) -> bool:
    added = False
    for fragment in fragments:
        norm = normalize_phrase(fragment)
        if not norm:
            continue
        policy = _requirement_policy(fragment)
        atoms.append({
            "text": fragment,
            "normalized": norm,
            "strict": policy["strict"] or requires_strict_evidence(fragment) or norm in STRICT_TOOL_TERMS,
            "requirement_type": policy["type"],
            "group_id": group_id,
            "group_mode": "any",
        })
        added = True
    return added


def decompose_requirement_text(text: str) -> List[dict]:
    """Split bundled JD requirements into atomic sub-requirements."""
    source = str(text or "").strip()
    if not source:
        return []

    parent_policy = _requirement_policy(source)
    if parent_policy["type"] in {"degree", "postgraduate_degree", "certification", "years_experience", "early_career_experience"}:
        normalized = normalize_phrase(source)
        return [{
            "text": source,
            "normalized": normalized,
            "strict": parent_policy["strict"],
            "requirement_type": parent_policy["type"],
        }]

    atoms: List[str | dict] = []

    paren_chunks = re.findall(r"\(([^)]+)\)", source)
    for chunk in paren_chunks:
        atoms.extend(_split_list_fragments(chunk))
    without_parens = re.sub(r"\([^)]*\)", "", source).strip(" ,;")

    marker_matched = False
    for marker in ATOMIC_REQUIREMENT_HINTS:
        pattern = rf"^(.*?)(?:\b{re.escape(marker)}\b)(.+)$"
        match = re.search(pattern, without_parens, flags=re.IGNORECASE)
        if not match:
            continue
        left = _strip_requirement_lead(match.group(1))
        right = match.group(2).strip(" -:;,.")
        if marker in {"such as", "including"} and _looks_like_alternative_list(source, right):
            fragments = _split_list_fragments(right)
            group_id = f"any:{normalize_phrase(right)}"
            if _append_alternative_atoms(atoms, fragments, group_id):
                marker_matched = True
                break
        if left:
            atoms.append(left)
        atoms.extend(_split_list_fragments(right))
        marker_matched = True
        break

    if not marker_matched:
        especially_match = re.search(r"^(.*?)[, ]+\bespecially\b(.+)$", without_parens, flags=re.IGNORECASE)
        if especially_match:
            left = _strip_requirement_lead(especially_match.group(1))
            right = especially_match.group(2).strip(" -:;,.")
            if left:
                atoms.extend(_split_list_fragments(left))
            fragments = _split_list_fragments(right)
            if _looks_like_alternative_list(source, right):
                _append_alternative_atoms(atoms, fragments, f"any:{normalize_phrase(right)}")
            else:
                atoms.extend(fragments)
            marker_matched = True

    if not marker_matched:
        stripped = _strip_requirement_lead(without_parens)
        split_atoms = _split_list_fragments(stripped)
        if 1 < len(split_atoms) <= 4:
            atoms.extend(split_atoms)
        elif stripped:
            atoms.append(stripped)

    normalized_parent = normalize_phrase(source)
    normalized_atoms = []
    seen = set()
    for atom in atoms:
        if isinstance(atom, dict):
            norm = atom.get("normalized") or normalize_phrase(atom.get("text"))
            if not norm or norm == normalized_parent or norm in seen:
                continue
            seen.add(norm)
            normalized_atoms.append(atom)
            continue

        atom = atom.strip()
        if len(atom.split()) == 1:
            atom = atom.replace(".", "")
        norm = normalize_phrase(atom)
        if not norm or norm == normalized_parent or norm in seen:
            continue
        seen.add(norm)
        policy = _requirement_policy(atom)
        normalized_atoms.append({
            "text": atom,
            "normalized": norm,
            "strict": policy["strict"] or requires_strict_evidence(atom) or norm in STRICT_TOOL_TERMS,
            "requirement_type": policy["type"],
        })

    if not normalized_atoms:
        policy = _requirement_policy(source)
        normalized_atoms.append({
            "text": source,
            "normalized": normalized_parent,
            "strict": policy["strict"] or requires_strict_evidence(source) or normalized_parent in STRICT_TOOL_TERMS,
            "requirement_type": policy["type"],
        })

    return normalized_atoms


def aggregate_requirement_evidence(
    requirement: str,
    parsed_resume: dict,
    resume_text: str = "",
    ai_present: bool = False,
    ai_evidence: str | None = None,
    ai_confidence: str | None = None,
) -> dict:
    """Evaluate one requirement using atomic deterministic checks, with AI as an ambiguity hint."""
    parent_found = find_cv_evidence_for_requirement(requirement, parsed_resume, resume_text)
    atoms = decompose_requirement_text(requirement)
    breakdown = []

    for atom in atoms:
        found = find_cv_evidence_for_requirement(atom["text"], parsed_resume, resume_text)
        status = "missing"
        evidence = None
        section = None
        confidence = "missing"

        if found:
            evidence = found["evidence"]
            section = found["section"]
            confidence = found["confidence"]
            status = "present" if found["confidence"] == "strong" else "partial"
        elif ai_present and ai_evidence and not atom["strict"]:
            candidate_confidence = "partial" if ai_confidence not in ("strong", "partial") else ai_confidence
            validated = validate_evidence_for_requirement(atom["text"], ai_evidence, candidate_confidence)
            if validated:
                evidence = ai_evidence
                section = validated["section"]
                confidence = validated["confidence"]
                status = "partial" if confidence == "partial" else "present"

        breakdown.append({
            "requirement": atom["text"],
            "status": status,
            "confidence": confidence,
            "evidence": evidence,
            "section": section,
            "strict": atom["strict"],
            "requirement_type": atom.get("requirement_type") or _requirement_policy(atom["text"])["type"],
            "group_id": atom.get("group_id"),
            "group_mode": atom.get("group_mode"),
        })

    coverage_items = []
    grouped_items: dict[str, list[dict]] = {}
    for item in breakdown:
        group_id = item.get("group_id")
        if group_id and item.get("group_mode") == "any":
            grouped_items.setdefault(group_id, []).append(item)
        else:
            coverage_items.append(item)

    for group_id, items in grouped_items.items():
        matched = [item for item in items if item["status"] in {"present", "partial"}]
        if any(item["status"] == "present" for item in matched):
            group_status = "present"
            group_confidence = "strong"
        elif matched:
            group_status = "partial"
            group_confidence = "partial"
        else:
            group_status = "missing"
            group_confidence = "missing"
        for item in items:
            item["group_status"] = group_status
        best = next((item for item in matched if item.get("evidence")), None)
        coverage_items.append({
            "requirement": " / ".join(item["requirement"] for item in items),
            "status": group_status,
            "confidence": group_confidence,
            "evidence": best.get("evidence") if best else None,
            "section": best.get("section") if best else None,
            "strict": any(item.get("strict") for item in items),
            "requirement_type": "alternative_group",
            "group_id": group_id,
            "group_mode": "any",
        })

    present_count = sum(1 for item in coverage_items if item["status"] == "present")
    partial_count = sum(1 for item in coverage_items if item["status"] == "partial")
    matched_count = present_count + partial_count
    total_count = max(1, len(coverage_items))

    if parent_found and matched_count == 0:
        overall_status = "present" if parent_found["confidence"] == "strong" else "partial"
        overall_confidence = parent_found["confidence"]
        best_evidence = parent_found["evidence"]
        best_section = parent_found["section"]
    elif matched_count == total_count and partial_count == 0:
        overall_status = "present"
        overall_confidence = "strong"
        best = next((item for item in coverage_items if item["evidence"]), None)
        best_evidence = best["evidence"] if best else None
        best_section = best["section"] if best else None
    elif matched_count > 0:
        overall_status = "partial"
        overall_confidence = "partial"
        best = next((item for item in coverage_items if item["status"] in ("present", "partial") and item["evidence"]), None)
        best_evidence = best["evidence"] if best else None
        best_section = best["section"] if best else None
    elif ai_present and ai_evidence and not _requirement_policy(requirement)["strict"]:
        candidate_confidence = "strong" if ai_confidence == "strong" else "partial"
        validated = validate_evidence_for_requirement(requirement, ai_evidence, candidate_confidence)
        if validated:
            overall_status = "present" if validated["confidence"] == "strong" else "partial"
            overall_confidence = validated["confidence"]
            best_evidence = ai_evidence
            best_section = validated["section"]
        else:
            overall_status = "missing"
            overall_confidence = "missing"
            best_evidence = None
            best_section = None
    else:
        overall_status = "missing"
        overall_confidence = "missing"
        best_evidence = None
        best_section = None

    coverage_ratio = matched_count / total_count
    return {
        "status": overall_status,
        "present": overall_status != "missing",
        "confidence": overall_confidence,
        "cv_where": best_evidence,
        "section": best_section,
        "matched_count": matched_count,
        "total_count": total_count,
        "coverage_ratio": round(coverage_ratio, 3),
        "atomic_breakdown": breakdown,
    }


def extract_resume_evidence_units(raw_sections: dict) -> List[dict]:
    evidence_units: List[dict] = []
    for section, weight in RESPONSIBILITY_SECTION_WEIGHTS.items():
        raw_text = (raw_sections or {}).get(section, "") or ""
        for line in split_text_units(raw_text):
            evidence_units.append(
                {
                    "section": section,
                    "weight": weight,
                    "text": line,
                    "normalized": normalize_phrase(line),
                    "action_phrases": extract_action_phrases(line),
                }
            )
    return evidence_units


def _as_string_list(value) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _project_tech_stack(project: dict) -> List[str]:
    tech: List[str] = []
    for key in ("tech_stack", "technologies", "tools", "stack"):
        tech.extend(_as_string_list(project.get(key)))
    return merge_unique(tech)


def cv_work_evidence_lines(parsed_resume: dict, limit: int = 80) -> List[str]:
    lines: List[str] = []
    for job in (parsed_resume.get("work_experience") or []):
        if not isinstance(job, dict):
            continue
        title = str(job.get("title") or "").strip()
        company = str(job.get("company") or "").strip()
        role_bits = [bit for bit in (title, company) if bit]
        prefix = f"[{' @ '.join(role_bits)}]" if role_bits else "[Experience]"
        for bullet in (job.get("bullets") or []):
            if isinstance(bullet, str) and bullet.strip():
                lines.append(f"{prefix} {bullet.strip()}")
                if len(lines) >= limit:
                    return lines
    return lines


def cv_project_evidence_lines(parsed_resume: dict, limit: int = 80) -> List[str]:
    lines: List[str] = []
    seen_norms: set = set()

    def _append(line: str) -> None:
        norm = normalize_phrase(line)
        if not norm or norm in seen_norms:
            return
        seen_norms.add(norm)
        lines.append(line)

    for project in (parsed_resume.get("projects") or []):
        if isinstance(project, str):
            if project.strip():
                _append(f"[Project] {project.strip()}")
            continue
        if not isinstance(project, dict):
            continue
        name = str(
            project.get("name")
            or project.get("project_name")
            or project.get("heading")
            or "Project"
        ).strip()
        prefix = f"[Project: {name}]" if name else "[Project]"
        tech = _project_tech_stack(project)
        headline_parts = []
        if name:
            headline_parts.append(name)
        for key in ("description", "summary", "details"):
            val = str(project.get(key) or "").strip()
            if val:
                headline_parts.append(val)
        if tech:
            headline_parts.append("Tech stack: " + ", ".join(tech[:12]))
        if headline_parts:
            _append(f"{prefix} " + " | ".join(headline_parts))
        for bullet in (project.get("bullets") or []):
            if isinstance(bullet, str) and bullet.strip():
                _append(f"{prefix} {bullet.strip()}")
        if len(lines) >= limit:
            return lines[:limit]
    raw_resume = str(parsed_resume.get("_resume_text") or "")
    if raw_resume:
        raw_projects = (split_resume_sections_raw(raw_resume) or {}).get("projects", "") or ""
        for raw_line in split_text_units(raw_projects):
            _append(f"[Project] {raw_line}")
            if len(lines) >= limit:
                return lines[:limit]
    return lines[:limit]


def cv_education_evidence_lines(parsed_resume: dict, limit: int = 40) -> List[str]:
    lines: List[str] = []
    seen_norms: set = set()

    def _append(line: str) -> None:
        norm = normalize_phrase(line)
        if not norm or norm in seen_norms:
            return
        seen_norms.add(norm)
        lines.append(line)

    for education in (parsed_resume.get("education") or []):
        if isinstance(education, str):
            if education.strip():
                _append(f"[Education] {education.strip()}")
            continue
        if not isinstance(education, dict):
            continue
        parts = []
        for key in ("degree", "institution", "graduation_year", "gpa"):
            value = str(education.get(key) or "").strip()
            if value:
                parts.append(value)
        if parts:
            _append("[Education] " + " | ".join(parts))
        if len(lines) >= limit:
            return lines[:limit]

    raw_resume = str(parsed_resume.get("_resume_text") or "")
    if raw_resume:
        raw_education = (split_resume_sections_raw(raw_resume) or {}).get("education", "") or ""
        for raw_line in split_text_units(raw_education):
            _append(f"[Education] {raw_line}")
            if len(lines) >= limit:
                return lines[:limit]
    return lines[:limit]


def cv_certification_evidence_lines(parsed_resume: dict, limit: int = 30) -> List[str]:
    lines: List[str] = []
    for cert in (parsed_resume.get("certifications") or []):
        if isinstance(cert, str) and cert.strip():
            lines.append(f"[Certification] {cert.strip()}")
            if len(lines) >= limit:
                return lines
    return lines


def cv_skills_evidence_line(parsed_resume: dict, limit: int = 80) -> str:
    skills = []
    for key in ("skills", "tools", "soft_skills"):
        skills.extend(_as_string_list(parsed_resume.get(key)))
    skills = merge_unique(skills)
    return "SKILLS: " + ", ".join(skills[:limit]) if skills else ""


def format_cv_match_evidence(
    parsed_resume: dict,
    work_limit: int = 60,
    project_limit: int = 60,
) -> str:
    """Compact CV evidence used by matching prompts. Projects are first-class evidence."""
    cv_parts: List[str] = []
    summary = str(parsed_resume.get("summary") or "").strip()
    if summary:
        cv_parts.append(f"SUMMARY:\n{summary}")
    education_lines = cv_education_evidence_lines(parsed_resume, limit=20)
    if education_lines:
        cv_parts.append("EDUCATION EVIDENCE:\n" + "\n".join(education_lines))
    certification_lines = cv_certification_evidence_lines(parsed_resume, limit=20)
    if certification_lines:
        cv_parts.append("CERTIFICATION EVIDENCE:\n" + "\n".join(certification_lines))
    skills_line = cv_skills_evidence_line(parsed_resume)
    if skills_line:
        cv_parts.append(skills_line)
    project_lines = cv_project_evidence_lines(parsed_resume, limit=project_limit)
    if project_lines:
        cv_parts.append("PROJECT EVIDENCE:\n" + "\n".join(project_lines))
    work_lines = cv_work_evidence_lines(parsed_resume, limit=work_limit)
    if work_lines:
        cv_parts.append("EXPERIENCE EVIDENCE:\n" + "\n".join(work_lines))
    return "\n\n".join(cv_parts)


def infer_evidence_section(evidence: str) -> str:
    text = (evidence or "").strip()
    if text.startswith("[Project"):
        return "projects"
    if text.startswith("[Education"):
        return "education"
    if text.startswith("[Certification"):
        return "certifications"
    if text.startswith("SKILLS:"):
        return "skills"
    if text.startswith("SUMMARY:"):
        return "summary"
    return "experience"


def _phrase_present_in_normalized_text(phrase_norm: str, text_norm: str) -> bool:
    tokens = [token for token in phrase_norm.split() if token]
    if len(tokens) == 1 and len(tokens[0]) <= 3:
        return tokens[0] in set(text_norm.split())
    return phrase_in_resume(
        phrase_norm,
        text_norm,
        set(text_norm.split()),
        text_norm.replace(" ", ""),
    )


def _requirement_aliases(requirement: str) -> List[str]:
    req_norm = normalize_phrase(requirement)
    aliases = [req_norm] if req_norm else []
    for canonical, values in TECH_SKILL_ALIASES.items():
        normalized_values = [normalize_phrase(v) for v in values]
        if req_norm == canonical or req_norm in normalized_values:
            aliases.extend(normalized_values)
    return merge_unique([alias for alias in aliases if alias])


STRICT_EVIDENCE_TERMS = (
    "typescript",
    "react",
    "docker",
    "kubernetes",
    "c++",
    "c#",
    "financial markets",
    "securities",
    "equities",
    "options",
    "time series",
    "serialisation",
    "serialization",
    "messaging middleware",
    "lakehouse",
)


def requires_strict_evidence(requirement: str) -> bool:
    req_norm = normalize_phrase(requirement)
    return _requirement_policy(requirement)["strict"] or any(term in req_norm for term in STRICT_EVIDENCE_TERMS)


def classify_requirement_evidence_match(requirement: str, evidence_text: str) -> str | None:
    req_norm = normalize_phrase(requirement)
    evidence_norm = normalize_phrase(evidence_text)
    if not req_norm or not evidence_norm:
        return None

    policy = _requirement_policy(requirement)
    if policy["type"] in {"degree", "postgraduate_degree"}:
        has_degree_signal = any(
            _phrase_present_in_normalized_text(normalize_phrase(term), evidence_norm)
            for term in DEGREE_TERMS
            if term not in SHORT_DEGREE_TERMS or f" {term} " in f" {evidence_norm} "
        )
        if policy["type"] == "postgraduate_degree" and not _evidence_has_postgraduate_degree(evidence_norm):
            return None
        if has_degree_signal and _evidence_has_degree_subject(evidence_norm, policy.get("subjects") or set()):
            return "strong"
        return None

    if policy["type"] == "early_career_experience":
        section = infer_evidence_section(evidence_text)
        if section == "education":
            education_signals = {"bsc", "bachelor", "bachelors", "degree", "university", "graduate", "graduation"}
            if set(evidence_norm.split()).intersection(education_signals):
                return "strong"
        if section in {"projects", "experience"}:
            experience_signals = {
                "project", "projects", "internship", "intern", "placement", "developer",
                "engineer", "analyst", "built", "developed", "implemented", "collaborated",
            }
            if set(evidence_norm.split()).intersection(experience_signals):
                return "strong"

    if policy["type"] == "project_management":
        evidence_tokens = set(evidence_norm.split())
        management_signals = {
            "coordinated", "coordinate", "managed", "manage", "tracked", "planned",
            "delivered", "delivery", "stakeholder", "stakeholders", "deadline",
            "deadlines", "deliverables", "process", "improvements", "initiative",
            "initiatives",
        }
        if evidence_tokens.intersection(management_signals):
            return "partial"

    if policy["type"] == "communication":
        evidence_tokens = set(evidence_norm.split())
        communication_signals = {
            "communication", "communicated", "communicate", "stakeholder", "stakeholders",
            "presented", "presentation", "reported", "reports", "documentation",
            "documented", "explained", "written", "verbal",
        }
        if evidence_tokens.intersection(communication_signals):
            if {"written", "verbal"}.intersection(set(req_norm.split())) and not {"written", "verbal"}.intersection(evidence_tokens):
                return "partial"
            return "strong"

    if policy["type"] == "management":
        evidence_tokens = set(evidence_norm.split())
        people_management_signals = {
            "managed", "manage", "manager", "mentored", "mentor", "mentoring",
            "supervised", "supervise", "led", "lead", "hired", "coached",
        }
        if evidence_tokens.intersection(people_management_signals):
            return "strong"

    if policy["type"] == "product_ownership":
        evidence_tokens = set(evidence_norm.split())
        roadmap_signals = {"roadmap", "owned", "ownership", "prioritised", "prioritized", "backlog", "release", "releases"}
        if "roadmap" in evidence_tokens and evidence_tokens.intersection(roadmap_signals):
            return "strong"

    for alias in _requirement_aliases(requirement):
        alias_norm = normalize_phrase(alias)
        if not alias_norm or alias_norm in STOPWORDS:
            continue
        if _phrase_present_in_normalized_text(alias_norm, evidence_norm):
            return "strong"

    for group in DOMAIN_EVIDENCE_GROUPS:
        target_hit = any(
            _phrase_present_in_normalized_text(normalize_phrase(target), req_norm)
            for target in group["targets"]
        )
        if not target_hit:
            continue
        signal_hit = any(
            _phrase_present_in_normalized_text(normalize_phrase(signal), evidence_norm)
            for signal in group["signals"]
        )
        if signal_hit:
            return "strong"

    generic_words = {
        "ability", "abilities", "candidate", "demonstrate", "demonstrated",
        "excellent", "experience", "experienced", "familiarity", "good",
        "hands", "interest", "knowledge", "passion", "proven", "skill",
        "skills", "solid", "strong", "understanding", "working",
    }
    req_tokens = [
        token
        for token in req_norm.split()
        if token not in STOPWORDS and token not in generic_words and len(token) > 1
    ]
    if 1 <= len(req_tokens) <= 4:
        evidence_tokens = set(evidence_norm.split())
        if all(token in evidence_tokens for token in req_tokens):
            return "strong"
    return None


def find_cv_evidence_for_requirement(
    requirement: str,
    parsed_resume: dict,
    resume_text: str = "",
) -> dict | None:
    """Find deterministic CV evidence across skills, work, projects, and raw CV text."""
    req_norm = normalize_phrase(requirement)
    if not req_norm:
        return None

    evidence_lines = []
    evidence_lines.extend(cv_education_evidence_lines(parsed_resume, limit=60))
    evidence_lines.extend(cv_certification_evidence_lines(parsed_resume, limit=40))
    skills_line = cv_skills_evidence_line(parsed_resume)
    if skills_line:
        evidence_lines.append(skills_line)
    evidence_lines.extend(cv_project_evidence_lines(parsed_resume, limit=100))
    evidence_lines.extend(cv_work_evidence_lines(parsed_resume, limit=100))

    for raw_line in split_text_units(resume_text)[:160]:
        line = f"[CV] {raw_line}"
        line_norm = normalize_phrase(line)
        if not any(normalize_phrase(existing) == line_norm for existing in evidence_lines):
            evidence_lines.append(line)

    for line in evidence_lines:
        confidence = classify_requirement_evidence_match(requirement, line)
        if confidence:
            validated = validate_evidence_for_requirement(requirement, line, confidence)
            if not validated:
                continue
            return {
                "evidence": line,
                "section": validated["section"],
                "confidence": validated["confidence"],
                "requirement_type": validated["policy"]["type"],
            }

    return None


def extract_domain_evidence_notes(
    resume_text: str,
    job_description: str,
    limit: int = 8,
) -> List[str]:
    jd_norm = normalize_phrase(job_description)
    if not jd_norm:
        return []
    notes: List[str] = []
    seen = set()
    for group in DOMAIN_EVIDENCE_GROUPS:
        target_hit = any(
            _phrase_present_in_normalized_text(normalize_phrase(target), jd_norm)
            for target in group["targets"]
        )
        if not target_hit:
            continue
        for line in split_text_units(resume_text):
            line_norm = normalize_phrase(line)
            if line_norm in seen:
                continue
            signal_hit = any(
                _phrase_present_in_normalized_text(normalize_phrase(signal), line_norm)
                for signal in group["signals"]
            )
            if signal_hit:
                seen.add(line_norm)
                notes.append(line.strip())
                if len(notes) >= limit:
                    return notes
    return notes


def evidence_units_from_parsed(parsed_resume: dict) -> List[dict]:
    """Build precise evidence units from structured Gemini-parsed CV data."""
    if not parsed_resume or not isinstance(parsed_resume, dict):
        return []
    units: List[dict] = []
    seen: set = set()

    def _add(text: str, section: str, weight: float) -> None:
        if not text or not text.strip():
            return
        norm = normalize_phrase(text)
        if not norm or norm in seen:
            return
        seen.add(norm)
        units.append({
            "section": section,
            "weight": weight,
            "text": text.strip(),
            "normalized": norm,
            "action_phrases": extract_action_phrases(text),
        })

    summary = parsed_resume.get("summary")
    if summary and isinstance(summary, str):
        _add(summary, "summary", 0.25)

    skills_line = cv_skills_evidence_line(parsed_resume)
    if skills_line:
        _add(skills_line, "skills", 0.65)

    for line in cv_education_evidence_lines(parsed_resume, limit=40):
        _add(line, "education", 0.9)

    for line in cv_certification_evidence_lines(parsed_resume, limit=30):
        _add(line, "certifications", 0.9)

    for line in cv_work_evidence_lines(parsed_resume, limit=100):
        _add(line, "experience", 1.0)

    for line in cv_project_evidence_lines(parsed_resume, limit=100):
        _add(line, "projects", 0.85)

    return units


def score_responsibility_match(
    responsibilities: List[dict],
    evidence_units: List[dict],
) -> dict:
    if not responsibilities:
        return {
            "score": 0.0,
            "matched_responsibilities": [],
            "missing_responsibilities": [],
            "matched_action_phrases": [],
            "missing_action_phrases": [],
            "evidence_by_section": {"experience": 0, "projects": 0, "summary": 0, "skills": 0},
        }

    matched_items: List[dict] = []
    missing_items: List[dict] = []
    matched_action_phrases: List[str] = []
    missing_action_phrases: List[str] = []
    evidence_by_section = {"experience": 0, "projects": 0, "summary": 0, "skills": 0}
    total_strength = 0.0

    for responsibility in responsibilities:
        best_match = None
        best_strength = 0.0
        best_similarity = 0.0
        best_match_type = None
        responsibility_norm = responsibility["normalized"]

        for unit in evidence_units:
            direct_phrase = False
            for phrase in responsibility["action_phrases"]:
                if phrase and phrase in unit["action_phrases"]:
                    direct_phrase = True
                    break
            if not direct_phrase and responsibility_norm and responsibility_norm in unit["normalized"]:
                direct_phrase = True

            evidence_confidence = classify_requirement_evidence_match(
                responsibility["text"],
                unit["text"],
            )
            if evidence_confidence:
                strength = unit["weight"]
                similarity = 1.0
                match_type = "evidence"
            elif direct_phrase:
                strength = unit["weight"]
                similarity = 1.0
                match_type = "phrase"
            else:
                similarity = tfidf_similarity(responsibility["text"], unit["text"])
                if similarity < RESPONSIBILITY_SIMILARITY_THRESHOLD:
                    continue
                strength = unit["weight"] * max(
                    0.0,
                    min(
                        1.0,
                        similarity / max(RESPONSIBILITY_SIMILARITY_THRESHOLD, 0.0001),
                    ),
                )
                match_type = "semantic"

            if strength > best_strength or (
                math.isclose(strength, best_strength) and similarity > best_similarity
            ):
                best_match = unit
                best_strength = strength
                best_similarity = similarity
                best_match_type = match_type

        if best_match is None:
            missing_items.append(
                {
                    "responsibility": responsibility["text"],
                    "action_phrases": responsibility["action_phrases"],
                }
            )
            missing_action_phrases.extend(responsibility["action_phrases"])
            continue

        evidence_by_section[best_match["section"]] = evidence_by_section.get(best_match["section"], 0) + 1
        total_strength += best_strength
        matched_items.append(
            {
                "responsibility": responsibility["text"],
                "action_phrases": responsibility["action_phrases"],
                "evidence": best_match["text"],
                "section": best_match["section"],
                "similarity": round(best_similarity, 3),
                "match_type": best_match_type,
            }
        )
        matched_action_phrases.extend(
            [phrase for phrase in responsibility["action_phrases"] if phrase in best_match["normalized"]]
        )

    score = 100.0 * (total_strength / len(responsibilities))
    return {
        "score": round(max(0.0, min(100.0, score)), 2),
        "matched_responsibilities": matched_items,
        "missing_responsibilities": missing_items,
        "matched_action_phrases": merge_unique(matched_action_phrases),
        "missing_action_phrases": merge_unique(missing_action_phrases),
        "evidence_by_section": evidence_by_section,
    }


def score_responsibility_match_semantic(
    responsibilities: List[dict],
    evidence_units: List[dict],
) -> dict:
    """Responsibility matching using Gemini embeddings; falls back to TF-IDF on any error."""
    if not responsibilities:
        return {
            "score": 0.0,
            "matched_responsibilities": [],
            "missing_responsibilities": [],
            "matched_action_phrases": [],
            "missing_action_phrases": [],
            "evidence_by_section": {"experience": 0, "projects": 0, "summary": 0, "skills": 0},
        }
    if not GENAI_CLIENT or not evidence_units:
        return score_responsibility_match(responsibilities, evidence_units)

    all_texts = [r["text"] for r in responsibilities] + [u["text"] for u in evidence_units]
    try:
        # Batch in chunks of 100 (Gemini embedding API limit)
        all_embeddings: List[List[float]] = []
        for i in range(0, len(all_texts), 100):
            all_embeddings.extend(gemini_embed_texts(all_texts[i:i + 100]))
    except Exception as exc:
        logger.warning("Gemini embedding failed for responsibility match, using TF-IDF: %s", exc)
        return score_responsibility_match(responsibilities, evidence_units)

    resp_embeddings = all_embeddings[:len(responsibilities)]
    unit_embeddings = all_embeddings[len(responsibilities):]

    matched_items: List[dict] = []
    missing_items: List[dict] = []
    matched_action_phrases: List[str] = []
    missing_action_phrases: List[str] = []
    evidence_by_section = {"experience": 0, "projects": 0, "summary": 0, "skills": 0}
    total_strength = 0.0

    for i, responsibility in enumerate(responsibilities):
        best_match = None
        best_strength = 0.0
        best_similarity = 0.0
        best_match_type = None
        responsibility_norm = responsibility["normalized"]

        for j, unit in enumerate(evidence_units):
            direct_phrase = False
            for phrase in responsibility["action_phrases"]:
                if phrase and phrase in unit["action_phrases"]:
                    direct_phrase = True
                    break
            if not direct_phrase and responsibility_norm and responsibility_norm in unit["normalized"]:
                direct_phrase = True

            evidence_confidence = classify_requirement_evidence_match(
                responsibility["text"],
                unit["text"],
            )
            if evidence_confidence:
                strength = unit["weight"]
                similarity = 1.0
                match_type = "evidence"
            elif direct_phrase:
                strength = unit["weight"]
                similarity = 1.0
                match_type = "phrase"
            else:
                similarity = cosine_similarity(resp_embeddings[i], unit_embeddings[j])
                if similarity < RESPONSIBILITY_EMBEDDING_THRESHOLD:
                    continue
                strength = unit["weight"] * similarity
                match_type = "semantic"

            if strength > best_strength or (math.isclose(strength, best_strength) and similarity > best_similarity):
                best_match = unit
                best_strength = strength
                best_similarity = similarity
                best_match_type = match_type

        if best_match is None:
            missing_items.append({"responsibility": responsibility["text"], "action_phrases": responsibility["action_phrases"]})
            missing_action_phrases.extend(responsibility["action_phrases"])
            continue

        evidence_by_section[best_match["section"]] = evidence_by_section.get(best_match["section"], 0) + 1
        total_strength += best_strength
        matched_items.append({
            "responsibility": responsibility["text"],
            "action_phrases": responsibility["action_phrases"],
            "evidence": best_match["text"],
            "section": best_match["section"],
            "similarity": round(best_similarity, 3),
            "match_type": best_match_type,
        })
        matched_action_phrases.extend(
            [p for p in responsibility["action_phrases"] if p in best_match["normalized"]]
        )

    score = 100.0 * (total_strength / len(responsibilities))
    return {
        "score": round(max(0.0, min(100.0, score)), 2),
        "matched_responsibilities": matched_items,
        "missing_responsibilities": missing_items,
        "matched_action_phrases": merge_unique(matched_action_phrases),
        "missing_action_phrases": merge_unique(missing_action_phrases),
        "evidence_by_section": evidence_by_section,
    }


def gemini_responsibility_match(
    responsibilities: List[dict],
    parsed_resume: dict,
) -> dict:
    """Use Gemini to intelligently match JD responsibilities against CV evidence."""
    if not GENAI_CLIENT or not responsibilities:
        ev_units = evidence_units_from_parsed(parsed_resume)
        return score_responsibility_match_semantic(responsibilities, ev_units)

    resp_list = "\n".join(f"{i + 1}. {r['text']}" for i, r in enumerate(responsibilities))
    cv_section = format_cv_match_evidence(parsed_resume, work_limit=70, project_limit=70)

    prompt = (
        "You are an expert recruiter matching a CV against job responsibilities.\n"
        "For each numbered job responsibility, decide whether the candidate's CV demonstrates it — using semantic understanding, not just keyword matching.\n\n"
        "Return ONLY valid JSON with exactly this structure:\n"
        "{\n"
        '  "matches": [\n'
        '    {\n'
        '      "index": 1,\n'
        '      "responsibility": "exact text from the numbered list",\n'
        '      "evidence": "quote the specific CV evidence line that proves this, keeping the [Title @ Company], [Project: Name], or SKILLS prefix",\n'
        '      "confidence": "strong or partial"\n'
        '    }\n'
        '  ],\n'
        '  "missing": [\n'
        '    {\n'
        '      "index": 2,\n'
        '      "responsibility": "exact text from the numbered list",\n'
        '      "gap": "one sentence: what specific experience or evidence is absent from this CV"\n'
        '    }\n'
        '  ]\n'
        "}\n\n"
        "Rules:\n"
        "- Use semantic matching: 'managed client relationships' can match 'led stakeholder engagement across 3 enterprise accounts'\n"
        "\n"
        "CONFIDENCE LEVELS — apply strictly:\n"
        "- 'strong': The evidence line contains the SAME concept or a clearly equivalent term as the responsibility. The candidate has provably done this thing.\n"
        "    Example STRONG: responsibility 'experience with AWS Lambda' matched by bullet '...using Python and AWS Lambda, reducing manual workload by 70%...'\n"
        "- 'partial': The evidence shows ADJACENT but not equivalent experience — same family of work, smaller scale, related tool, or domain-adjacent.\n"
        "    Example PARTIAL: responsibility 'distributed systems' matched by bullet '...backend data systems and pipelines...' (pipelines are not necessarily distributed systems).\n"
        "    Example PARTIAL: responsibility 'mentoring junior engineers' matched by bullet '...trained two new joiners...' (training != formal mentorship).\n"
        "\n"
        "EVIDENCE QUALITY RULES (CRITICAL — read carefully):\n"
        "- The evidence MUST contain an explicit signal of the responsibility's core concept. If the line only LOOSELY relates, demote to 'partial'. If the line doesn't relate at all, the item belongs in 'missing'.\n"
        "- Do NOT match 'unit and integration testing' with 'data validation' or 'integrity checks'. Those are different concepts. If the CV's tools list explicitly names testing frameworks (Jest, Pytest, Vitest, JUnit, Testing Library, etc.), use THAT as evidence instead. If neither exists, mark this responsibility as 'missing'.\n"
        "- Do NOT match 'distributed systems' with 'backend services' or 'data pipelines'. Distributed systems means designing for multiple machines/regions/services coordinating — needs explicit signal (microservices, queues, replication, sharding, consensus, etc.).\n"
        "- Do NOT match a specific named technology (Neo4j, Kafka, Snowflake, dbt, Airflow, Terraform, etc.) with a different technology. If the CV doesn't name the exact tool or a near-synonym, the item is 'missing' or at best 'partial' if there's a closely related tool.\n"
        "- If the CV's skills/tools list names a relevant tool (e.g. responsibility says 'unit testing', CV skills list includes 'Vitest, Testing Library'), use the SKILLS line as evidence: 'SKILLS: Vitest, Testing Library, ...' — this is valid evidence.\n"
        "- When in doubt between 'strong' and 'partial' → choose 'partial'. When in doubt between 'partial' and 'missing' → choose 'missing'. False positives mislead the candidate; false negatives just push them to add real evidence.\n"
        "\n"
        "- Treat PROJECT EVIDENCE as valid first-class evidence, especially where the JD asks for domain interest, finance/trading exposure, or tools demonstrated in projects.\n"
        "- A trading/backtesting project with signals like RSI, MACD, Kelly sizing, market data, or strategy optimisation is valid evidence of trading/finance interest.\n"
        "- Every responsibility index must appear in exactly one of matches or missing.\n"
        "- evidence: quote the CV evidence line verbatim including its prefix so the candidate knows exactly where it came from. If using skills line, write the SKILLS line verbatim.\n"
        "- gap: be specific — e.g. 'No experience leading cross-functional teams, only individual contributor roles shown' not 'needs more leadership'\n"
        "- Return ONLY the JSON object, no markdown fences.\n"
    )
    contents = f"{prompt}\n\nJOB RESPONSIBILITIES:\n{resp_list}\n\nCANDIDATE CV:\n{cv_section}"

    try:
        response = GENAI_CLIENT.models.generate_content(
            model=GEMINI_REWRITE_MODEL,
            contents=contents,
            config=gemini_generation_config(0),
        )
        raw = getattr(response, "text", "") or ""
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected response shape")
    except Exception as exc:
        logger.warning("Gemini responsibility match failed, using embedding fallback: %s", exc)
        ev_units = evidence_units_from_parsed(parsed_resume)
        return score_responsibility_match_semantic(responsibilities, ev_units)

    def _find_original(item: dict) -> dict | None:
        idx = item.get("index")
        if idx is not None:
            try:
                return responsibilities[int(idx) - 1]
            except (IndexError, ValueError, TypeError):
                pass
        resp_text = str(item.get("responsibility") or "").strip()
        resp_norm = normalize_phrase(resp_text)
        return next(
            (r for r in responsibilities if r["text"] == resp_text or r["normalized"] == resp_norm),
            None,
        )

    resume_text = str(parsed_resume.get("_resume_text") or "")
    matched_items: List[dict] = []
    missing_items: List[dict] = []
    STRONG_W, PARTIAL_W = 1.0, 0.55
    ai_match_map: dict[str, dict] = {}
    ai_gap_map: dict[str, str] = {}

    for m in (parsed.get("matches") or []):
        if not isinstance(m, dict):
            continue
        original = _find_original(m)
        if original is None:
            continue
        ai_match_map[original["normalized"]] = {
            "confidence": str(m.get("confidence") or "partial").lower().strip(),
            "evidence": str(m.get("evidence") or "").strip() or None,
        }

    for m in (parsed.get("missing") or []):
        if not isinstance(m, dict):
            continue
        original = _find_original(m)
        if original is None:
            continue
        ai_gap_map[original["normalized"]] = str(m.get("gap") or "").strip()

    for original in responsibilities:
        ai_match = ai_match_map.get(original["normalized"]) or {}
        aggregate = aggregate_requirement_evidence(
            original["text"],
            parsed_resume,
            resume_text,
            ai_present=bool(ai_match),
            ai_evidence=ai_match.get("evidence"),
            ai_confidence=ai_match.get("confidence"),
        )
        if aggregate["status"] == "missing":
            missing_items.append({
                "responsibility": original["text"],
                "action_phrases": original["action_phrases"],
                "gap": ai_gap_map.get(original["normalized"]) or "No explicit CV evidence found for this requirement.",
                "category": original.get("category", "essential"),
                "atomic_breakdown": aggregate["atomic_breakdown"],
            })
            continue

        confidence = "strong" if aggregate["status"] == "present" and aggregate["confidence"] == "strong" else "partial"
        matched_items.append({
            "responsibility": original["text"],
            "action_phrases": original["action_phrases"],
            "evidence": aggregate["cv_where"],
            "section": aggregate["section"] or "experience",
            "similarity": 1.0 if confidence == "strong" else max(0.6, aggregate["coverage_ratio"]),
            "match_type": "ai_atomic" if ai_match else "local_atomic",
            "confidence": confidence,
            "category": original.get("category", "essential"),
            "matched_count": aggregate["matched_count"],
            "total_count": aggregate["total_count"],
            "atomic_breakdown": aggregate["atomic_breakdown"],
        })

    total = len(responsibilities)
    total_weight = sum(
        STRONG_W if item.get("confidence") == "strong" else PARTIAL_W
        for item in matched_items
    )
    score = round(max(0.0, min(100.0, 100.0 * total_weight / total)), 2) if total else 0.0
    evidence_by_section = {"experience": 0, "projects": 0, "summary": 0, "skills": 0}
    for item in matched_items:
        section = item.get("section") or "experience"
        evidence_by_section[section] = evidence_by_section.get(section, 0) + 1

    return {
        "score": score,
        "matched_responsibilities": matched_items,
        "missing_responsibilities": missing_items,
        "matched_action_phrases": merge_unique([p for item in matched_items for p in item.get("action_phrases", [])]),
        "missing_action_phrases": merge_unique([p for item in missing_items for p in item.get("action_phrases", [])]),
        "evidence_by_section": evidence_by_section,
    }


def compute_title_alignment(job_description: str, resume_text: str) -> dict:
    job_terms = extract_seniority_terms(job_description)
    resume_terms = extract_seniority_terms(resume_text)
    if not job_terms:
        return {
            "score": None,
            "job_terms": [],
            "resume_terms": resume_terms,
            "aligned": None,
        }

    job_level = max(SENIORITY_LEVELS.get(term, 0) for term in job_terms)
    resume_level = max((SENIORITY_LEVELS.get(term, 0) for term in resume_terms), default=0)
    if resume_level == 0:
        score = 0.0
    else:
        score = max(0.0, 1.0 - (abs(job_level - resume_level) / 4))

    return {
        "score": round(score * 100, 2),
        "job_terms": job_terms,
        "resume_terms": resume_terms,
        "aligned": bool(score >= 0.75),
    }


def compute_experience_match(
    raw_sections: dict,
    resume_text: str,
    job_description: str,
    responsibilities: List[dict],
    responsibility_result: dict,
    required_years: Optional[int],
    resume_years: Optional[int],
) -> dict:
    experience_text = (raw_sections or {}).get("experience", "") or ""
    experience_lines = split_text_units(experience_text)
    lines_with_dates = [line for line in experience_lines if has_dates(line)]
    estimated_entries = max(1, min(4, max(len(experience_lines), 1) // 3 or 1))
    date_coverage_ratio = min(1.0, len(lines_with_dates) / estimated_entries) if experience_text else 0.0

    if required_years is None:
        years_score = None
    elif resume_years is None:
        years_score = 0.0
    else:
        years_score = min(1.0, resume_years / required_years) if required_years > 0 else 1.0

    title_alignment = compute_title_alignment(job_description, resume_text)

    matched_experience = [
        item
        for item in responsibility_result["matched_responsibilities"]
        if item.get("section") == "experience"
    ]
    evidence_density = min(
        1.0,
        len(matched_experience) / max(1, len(responsibilities)),
    )

    component_weights = [
        ("years", 0.4, years_score),
        ("dates", 0.2, date_coverage_ratio),
        ("evidence", 0.2, evidence_density),
        (
            "title_alignment",
            0.2,
            None if title_alignment["score"] is None else title_alignment["score"] / 100,
        ),
    ]
    total_weight = sum(weight for _name, weight, value in component_weights if value is not None)
    weighted_total = sum(weight * value for _name, weight, value in component_weights if value is not None)
    experience_score = (weighted_total / total_weight) * 100 if total_weight else 0.0

    experience_evidence: List[str] = []
    experience_gaps: List[str] = []
    if resume_years is not None:
        experience_evidence.append(f"Detected {resume_years}+ years of experience on the CV.")
    if required_years is not None:
        if resume_years is not None and resume_years >= required_years:
            experience_evidence.append(f"Meets the role's {required_years}+ years requirement.")
        else:
            experience_gaps.append(f"Role asks for {required_years}+ years; the CV does not clearly show that level yet.")
    if date_coverage_ratio >= 0.75:
        experience_evidence.append("Experience entries include visible date ranges.")
    else:
        experience_gaps.append("Experience section needs clearer date ranges across roles.")
    if matched_experience:
        experience_evidence.append(
            f"{len(matched_experience)} responsibility matches are backed by experience bullets."
        )
    else:
        experience_gaps.append("Experience bullets do not strongly prove the job's key responsibilities yet.")
    if title_alignment["score"] is not None:
        if title_alignment["aligned"]:
            experience_evidence.append("Seniority language in the CV aligns with the role level.")
        else:
            experience_gaps.append("Seniority/title language is not clearly aligned with the role level.")

    return {
        "score": round(max(0.0, min(100.0, experience_score)), 2),
        "required_years": required_years,
        "resume_years": resume_years,
        "years_score": None if years_score is None else round(years_score * 100, 2),
        "date_coverage_score": round(date_coverage_ratio * 100, 2),
        "responsibility_evidence_score": round(evidence_density * 100, 2),
        "title_alignment_score": title_alignment["score"],
        "title_alignment": title_alignment,
        "experience_evidence": experience_evidence,
        "experience_gaps": experience_gaps,
    }


def has_dates(text: str) -> bool:
    if not text:
        return False
    if DATE_RANGE_RE.search(text):
        return True
    if YEAR_RE.search(text):
        return True
    return bool(MONTH_YEAR_RE.search(text))


def has_metrics(text: str) -> bool:
    if not text:
        return False
    return bool(METRIC_RE.search(text))


def count_action_verbs(text: str) -> int:
    if not text:
        return 0
    normalized = normalize_phrase(text)
    return sum(1 for verb in ACTION_VERBS if verb in normalized)


def extract_skill_list(text: str) -> List[str]:
    if not text:
        return []
    items = re.split(r"[,|;/\n•]+", text)
    cleaned = []
    seen = set()
    for item in items:
        token = normalize_phrase(item)
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        cleaned.append(item.strip())
    return cleaned


def has_tech_terms(text: str) -> bool:
    if not text:
        return False
    if extract_skill_tokens(text, limit=3):
        return True
    normalized = normalize_phrase(text)
    return any(term in normalized for term in LANGUAGE_SKILLS)


def gemini_section_feedback(
    parsed_resume: dict,
    job_description: str,
    role_fit_breakdown: dict | None = None,
) -> dict:
    """Produce per-section feedback using Gemini, grounded in actual CV content and JD requirements.

    Returns dict keyed by section name with:
        verdict: 'strong' | 'good' | 'needs_work' | 'weak'
        summary_line: one-sentence diagnosis
        strengths: list[str] (max 2 — specific, naming CV content)
        improvements: list[{issue, fix}] (max 2 — surgical edits, not generic advice)
    """
    if not GENAI_CLIENT:
        return {}

    # Build a compact CV summary for the prompt
    summary = (parsed_resume.get("summary") or "").strip()
    skills = [str(s) for s in (parsed_resume.get("skills") or [])[:40]]
    jobs = parsed_resume.get("work_experience") or []
    projects = parsed_resume.get("projects") or []
    education = parsed_resume.get("education") or []

    exp_lines = []
    for job in jobs[:5]:
        if not isinstance(job, dict):
            continue
        title = job.get("title", "")
        company = job.get("company", "")
        bullets = job.get("bullets") or []
        bullet_text = "\n".join(f"      - {str(b).strip()}" for b in bullets[:8] if str(b).strip())
        exp_lines.append(f"   [{title} @ {company}]\n{bullet_text}")
    exp_blob = "\n".join(exp_lines) if exp_lines else "(no work experience parsed)"

    proj_lines = []
    for proj in projects[:5]:
        if isinstance(proj, dict):
            name = proj.get("name", "")
            bullets = proj.get("bullets") or []
            bullet_text = "\n".join(f"      - {str(b).strip()}" for b in bullets[:5] if str(b).strip())
            proj_lines.append(f"   [{name}]\n{bullet_text}")
        elif isinstance(proj, str):
            proj_lines.append(f"   - {proj}")
    proj_blob = "\n".join(proj_lines) if proj_lines else "(no projects parsed)"

    edu_lines = []
    for edu in education[:3]:
        if isinstance(edu, dict):
            edu_lines.append(f"   - {edu.get('degree', '')} | {edu.get('institution', '')} | {edu.get('graduation_year', '')}")
        elif isinstance(edu, str):
            edu_lines.append(f"   - {edu}")
    edu_blob = "\n".join(edu_lines) if edu_lines else "(no education parsed)"

    breakdown = role_fit_breakdown or {}
    missing_essential = [
        r.get("responsibility", "")
        for r in (breakdown.get("missing_responsibilities") or [])
        if (r.get("category") or "essential") != "nice_to_have"
    ]
    missing_skills_summary = ""
    skills_detail = breakdown.get("skills_detail") or {}
    must_missing = [
        s for s in (skills_detail.get("must_have") or [])
        if (s.get("status") or ("present" if s.get("present") else "missing")) == "missing"
    ]
    if must_missing:
        names = []
        for s in must_missing[:8]:
            nm = s.get("skill") or s.get("keyword") or ""
            if nm:
                names.append(nm)
        if names:
            missing_skills_summary = "Missing from CV but required by JD: " + ", ".join(names)

    prompt = f"""You are a senior career coach reviewing a CV against a specific job description, section by section. Produce concrete, surgical feedback grounded in what's actually written in this CV — never generic advice.

Return ONLY valid JSON with this exact structure:

{{
  "summary":     {{ "verdict": "strong|good|needs_work|weak", "summary_line": "one sentence", "strengths": ["string", ...], "improvements": [{{"issue": "string", "fix": "string"}}, ...] }},
  "experience":  {{ "verdict": "strong|good|needs_work|weak", "summary_line": "one sentence", "strengths": [...], "improvements": [...] }},
  "projects":    {{ "verdict": "strong|good|needs_work|weak", "summary_line": "one sentence", "strengths": [...], "improvements": [...] }},
  "skills":      {{ "verdict": "strong|good|needs_work|weak", "summary_line": "one sentence", "strengths": [...], "improvements": [...] }},
  "education":   {{ "verdict": "strong|good|needs_work|weak", "summary_line": "one sentence", "strengths": [...], "improvements": [...] }}
}}

VERDICTS (apply strictly):
- "strong":     Section is competitive for this role. Nothing material is missing.
- "good":       Section is solid but has 1-2 improvements that would meaningfully strengthen it.
- "needs_work": Section has clear gaps vs the JD or weak content. Multiple improvements needed.
- "weak":       Section is missing critical content for this role, or is poorly written. Major rework needed.

STRICT RULES:
1. Every strength must NAME the specific bullet, role, project, or content from the CV. Bad: "Uses action verbs". Good: "The MySchola bullet about AWS Lambda automation quantifies impact (70% reduction)."
2. Every improvement.fix must be a SURGICAL EDIT, not generic advice. Bad: "Add more metrics". Good: "In your MHR role, the bullet 'Built end-to-end data flows...' could end with the system reliability gain (you mention 40% elsewhere — see if it applies here)."
3. Cross-reference the JD. If the JD wants 'distributed systems' and the CV doesn't mention it, that's a concrete improvement for the relevant section (usually summary or experience).
4. Maximum 2 strengths and 2 improvements per section. Pick the top items only. Quality over quantity.
5. The summary_line must be ONE sentence that gives the candidate the gist of how this section reads to a recruiter for THIS specific role.
6. If a section is genuinely missing from the CV (e.g. no projects, no education), verdict = "weak", strengths = [], improvements = one item explaining what to add.
7. DO NOT mention employment gaps, dates, or tenure — that's covered elsewhere.
8. DO NOT use em-dashes, en-dashes, or smart quotes. Use plain hyphens and straight quotes only.

CONTEXT FROM THE ANALYSIS:
{missing_skills_summary}
{("Missing essential responsibilities from JD: " + "; ".join(missing_essential[:5])) if missing_essential else ""}

JOB DESCRIPTION:
{job_description}

CANDIDATE CV:
SUMMARY: {summary or "(no summary parsed)"}

WORK EXPERIENCE:
{exp_blob}

PROJECTS:
{proj_blob}

SKILLS: {", ".join(skills) if skills else "(no skills parsed)"}

EDUCATION:
{edu_blob}
"""

    try:
        response = GENAI_CLIENT.models.generate_content(
            model=GEMINI_REWRITE_MODEL,
            contents=prompt,
            config=gemini_generation_config(0.2),
        )
        raw = (getattr(response, "text", "") or "").strip()
        # Strip stray markdown fences just in case
        for marker in ("```json", "```"):
            raw = raw.replace(marker, "")
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict):
            return {}
        # Normalize each section's shape
        normalized = {}
        for section in ("summary", "experience", "projects", "skills", "education"):
            data = parsed.get(section) or {}
            if not isinstance(data, dict):
                continue
            verdict = str(data.get("verdict", "good")).lower()
            if verdict not in ("strong", "good", "needs_work", "weak"):
                verdict = "good"
            normalized[section] = {
                "verdict": verdict,
                "summary_line": str(data.get("summary_line") or "").strip(),
                "strengths": [str(s).strip() for s in (data.get("strengths") or [])[:2] if str(s).strip()],
                "improvements": [
                    {
                        "issue": str(item.get("issue") or "").strip(),
                        "fix": str(item.get("fix") or "").strip(),
                    }
                    for item in (data.get("improvements") or [])[:2]
                    if isinstance(item, dict) and (item.get("issue") or item.get("fix"))
                ],
            }
        return normalized
    except Exception as exc:
        logger.warning("Gemini section feedback failed, using heuristic fallback: %s", exc)
        return {}


def build_section_feedback(
    raw_sections: dict,
    norm_sections: dict,
    job_description: str,
    parsed_resume: dict | None = None,
) -> dict:
    feedback = {}
    profile = detect_job_profile(job_description)
    for section in ["summary", "experience", "projects", "skills", "education", "other"]:
        raw_text = (raw_sections or {}).get(section, "") or ""
        norm_text = (norm_sections or {}).get(section, "") or ""
        good: List[str] = []
        not_good: List[str] = []

        # Fall back to Gemini-parsed data when heading detection misses the section.
        # PDFs with multi-column layouts or unusual headings often fail the regex-based
        # split, but the Gemini-parsed structured resume usually has the data correctly.
        if section == "skills" and len(re.findall(r"\b\w+\b", raw_text)) < 15:
            parsed_skills_fallback = (parsed_resume or {}).get("skills") or []
            if parsed_skills_fallback:
                raw_text = ", ".join(str(s) for s in parsed_skills_fallback[:40])
                norm_text = normalize_phrase(raw_text)

        if section == "summary" and len(re.findall(r"\b\w+\b", raw_text)) < 15:
            parsed_summary_fallback = str((parsed_resume or {}).get("summary") or "")
            if parsed_summary_fallback.strip():
                raw_text = parsed_summary_fallback
                norm_text = normalize_phrase(raw_text)

        if section == "education" and len(re.findall(r"\b\w+\b", raw_text)) < 15:
            edu_list = (parsed_resume or {}).get("education") or []
            if edu_list:
                pieces = []
                for entry in edu_list:
                    if isinstance(entry, dict):
                        pieces.append(" ".join(str(v) for v in entry.values() if v))
                    elif isinstance(entry, str):
                        pieces.append(entry)
                edu_blob = " | ".join(p for p in pieces if p.strip())
                if edu_blob:
                    raw_text = edu_blob
                    norm_text = normalize_phrase(raw_text)

        if section == "experience" and len(re.findall(r"\b\w+\b", raw_text)) < 15:
            jobs = (parsed_resume or {}).get("work_experience") or []
            if jobs:
                pieces = []
                for job in jobs:
                    if isinstance(job, dict):
                        title = str(job.get("title") or "")
                        company = str(job.get("company") or "")
                        dates = str(job.get("dates") or "")
                        bullets = " ".join(str(b) for b in (job.get("bullets") or []) if b)
                        pieces.append(f"{title} {company} {dates} {bullets}".strip())
                exp_blob = "\n".join(p for p in pieces if p)
                if exp_blob:
                    raw_text = exp_blob
                    norm_text = normalize_phrase(raw_text)

        if section == "projects" and len(re.findall(r"\b\w+\b", raw_text)) < 15:
            projects_list = (parsed_resume or {}).get("projects") or []
            if projects_list:
                pieces = []
                for proj in projects_list:
                    if isinstance(proj, dict):
                        name = str(proj.get("name") or "")
                        desc = str(proj.get("description") or "")
                        tech = " ".join(str(t) for t in (proj.get("technologies") or []) if t)
                        bullets = " ".join(str(b) for b in (proj.get("bullets") or []) if b)
                        pieces.append(f"{name} {desc} {tech} {bullets}".strip())
                    elif isinstance(proj, str):
                        pieces.append(proj)
                proj_blob = "\n".join(p for p in pieces if p)
                if proj_blob:
                    raw_text = proj_blob
                    norm_text = normalize_phrase(raw_text)

        word_count = len(re.findall(r"\b\w+\b", raw_text))
        # Per-section minimum word counts. Education is intentionally short (just degree
        # + institution + year is ~6 words and that's enough to evaluate).
        min_words = {
            "education": 4,
            "skills": 6,
            "projects": 8,
        }.get(section, 15)
        if not raw_text.strip() or word_count < min_words:
            not_good.append("Section is missing or too short to be useful.")
            feedback[section] = {"good": good, "not_good": not_good}
            continue

        dates_present = has_dates(raw_text)
        metrics_present = has_metrics(raw_text)
        verbs_count = count_action_verbs(norm_text)
        tech_present = has_tech_terms(raw_text)

        if section == "summary":
            if 40 <= word_count <= 110:
                good.append("Summary length is concise and easy to scan.")
            elif word_count > 130:
                not_good.append("Summary is long; aim for 2-4 sentences.")
            elif word_count < 30:
                not_good.append("Summary is very short; add a focused value statement.")

            if profile == "swe" and any(term in norm_text for term in SWE_TITLE_TERMS):
                good.append("Target role is clear and aligned with the job description.")
            elif profile == "swe":
                not_good.append("Target role is not explicit; call out your software role.")

            if PLUS_YEARS_RE.search(raw_text):
                good.append("Includes years of experience for quick context.")
            else:
                not_good.append("Missing years of experience; add a brief years summary.")

            if tech_present:
                good.append("Mentions core technologies or domains.")
            else:
                not_good.append("No technical focus; add 1-2 core specialties.")

        elif section == "experience":
            if dates_present:
                good.append("Role dates are present, which helps recruiters scan quickly.")
            else:
                not_good.append("Role dates are missing; add clear date ranges.")

            if verbs_count >= 3:
                good.append("Uses action verbs that emphasize ownership and impact.")
            else:
                not_good.append("Few action verbs; rewrite bullets to start with strong verbs.")

            if metrics_present:
                good.append("Includes measurable impact (numbers or percentages).")
            else:
                not_good.append("Little quantified impact; add metrics where possible.")

            if tech_present:
                good.append("Mentions relevant tools/technologies used.")
            else:
                not_good.append("Tech stack is unclear; add key tools or languages.")

            if word_count > 380:
                not_good.append("Experience section is long; trim to most relevant roles.")
            elif word_count < 80:
                not_good.append("Experience section is short; add 2-4 bullets per role.")

        elif section == "projects":
            if verbs_count >= 2:
                good.append("Project descriptions use action verbs.")
            else:
                not_good.append("Project bullets read vague; lead with action verbs.")

            if tech_present:
                good.append("Tech stack is visible in project descriptions.")
            else:
                not_good.append("Missing tech stack; list languages, frameworks, or tools.")

            if metrics_present:
                good.append("Projects include measurable outcomes.")
            else:
                not_good.append("No quantified outcomes; add results or performance gains.")

            if re.search(r"https?://|github\.com", raw_text, re.IGNORECASE):
                good.append("Includes links to work or repos.")
            else:
                not_good.append("No project links; add a GitHub or demo link if available.")

            if word_count < 40:
                not_good.append("Projects section is short; add 2-3 strong projects.")

        elif section == "skills":
            parsed_skills_list = list((parsed_resume or {}).get("skills") or [])
            skills_list = parsed_skills_list if parsed_skills_list else extract_skill_list(raw_text)
            skill_count = len(skills_list)

            if 6 <= skill_count <= 25:
                good.append(f"Skill count ({skill_count}) is in the ATS-friendly range.")
            elif skill_count < 6:
                not_good.append("Too few skills listed; add core languages, tools, and frameworks.")
            elif skill_count > 35:
                not_good.append(f"Too many skills ({skill_count}); trim to the 15–25 most relevant.")

            tech_skills_found = [str(s) for s in skills_list if has_tech_terms(str(s))][:6]
            if tech_skills_found:
                good.append(f"Technical skills detected: {', '.join(tech_skills_found)}.")
            elif tech_present:
                good.append("Skills section includes technical tools or technologies.")
            else:
                not_good.append("Skills are too generic; add specific tools, languages, or frameworks.")

            soft_hits = [term for term in SOFT_SKILLS if term in norm_text]
            if soft_hits:
                not_good.append(f"Soft skills detected ({', '.join(soft_hits[:3])}); keep this section technical only.")

        elif section == "education":
            degree_present = bool(
                re.search(
                    r"\b(bsc|bs|msc|ms|phd|bachelor|master|doctorate)\b",
                    norm_text,
                )
            )
            institution_present = bool(
                re.search(r"\b(university|college|institute|school)\b", norm_text)
            )
            if degree_present and institution_present:
                good.append("Degree and institution are clearly listed.")
            else:
                not_good.append("Degree or institution is missing; add both explicitly.")

            if dates_present:
                good.append("Education dates are present.")
            else:
                not_good.append("Education dates are missing; add graduation year.")

            if word_count > 120:
                not_good.append("Education section is long; keep it to key credentials.")

        else:
            if word_count > 40:
                good.append("Additional content provides extra context.")
            else:
                not_good.append("Other section is brief; remove or expand if relevant.")

        feedback[section] = {"good": good, "not_good": not_good}

    return feedback


def merge_unique(items: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for item in items:
        normalized = normalize_phrase(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(item)
    return merged


def compute_coverage(
    skills: List[str],
    resume_sections: dict,
    section_weights: dict | None = None,
) -> float:
    if not skills or not resume_sections:
        return 0.0
    weights = section_weights or SECTION_WEIGHTS
    max_w = max(weights.values()) if weights else 1.0
    seen = set()
    gained = 0.0
    total = 0.0
    for skill in skills:
        normalized = normalize_phrase(skill)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        total += max_w
        best = 0.0
        for section, text in resume_sections.items():
            if not text:
                continue
            w = weights.get(section, weights.get("other", 0.2))
            if normalized in text:
                best = max(best, w)
        gained += best
    return 0.0 if total == 0 else gained / total


def detect_job_profile(job_description: str) -> str:
    normalized = normalize_phrase(job_description)
    for term in SWE_TITLE_TERMS:
        if term in normalized:
            return "swe"
    return "general"


def compute_ats_score(
    resume_sections: dict,
    job_description: str,
    semantic_score: float,
) -> dict:
    profile = detect_job_profile(job_description)
    if profile != "swe":
        return {
            "score": round(semantic_score, 2),
            "profile": profile,
            "breakdown": {"semantic": round(semantic_score, 2)},
        }

    title_match = any(
        normalize_phrase(term) in " ".join(resume_sections.values())
        for term in SWE_TITLE_TERMS
    )
    core_coverage = compute_coverage(list(SWE_CORE_SKILLS), resume_sections)
    nice_coverage = compute_coverage(list(SWE_NICE_SKILLS), resume_sections)
    leadership = compute_coverage(list(SWE_LEADERSHIP_TERMS), resume_sections)
    cross_func = compute_coverage(list(SWE_CROSS_FUNC_TERMS), resume_sections)
    scale_terms = compute_coverage(list(SWE_SCALE_TERMS), resume_sections)
    languages = compute_coverage(list(LANGUAGE_SKILLS), resume_sections)
    negative_hits = compute_coverage(list(SWE_NEGATIVE_TERMS), resume_sections)

    # Weighted ATS-style blend (made less punitive, more semantic-heavy).
    score = (
        (15 if title_match else 0)
        + core_coverage * 40
        + nice_coverage * 12
        + leadership * 8
        + cross_func * 6
        + scale_terms * 8
        + languages * 6
        + semantic_score * 0.25
    )
    score -= negative_hits * 5
    score = max(0.0, min(100.0, score))

    return {
        "score": round(score, 2),
        "profile": profile,
        "breakdown": {
            "title_match": 15 if title_match else 0,
            "core_coverage": round(core_coverage * 100, 2),
            "nice_coverage": round(nice_coverage * 100, 2),
            "leadership": round(leadership * 100, 2),
            "cross_functional": round(cross_func * 100, 2),
            "scale_perf": round(scale_terms * 100, 2),
            "languages": round(languages * 100, 2),
            "semantic": round(semantic_score, 2),
            "negative_terms": round(negative_hits * 100, 2),
        },
    }


def infer_missing_keywords(
    resume_text: str,
    job_description: str,
    limit: int = 10,
    prefetched_phrases: List[str] | None = None,
) -> List[str]:
    resume_text_norm = normalize_phrase(resume_text)
    resume_token_set = set(resume_text_norm.split())
    resume_compact = resume_text_norm.replace(" ", "")
    freq, required = build_skill_confidence(job_description)
    ordered_keywords: List[str] = []
    seen = set()

    keyphrases = prefetched_phrases if prefetched_phrases is not None else textrazor_extract_phrases(job_description)
    if not keyphrases:
        keyphrases = extract_keyphrases(job_description, limit=limit * 3)
    if len(keyphrases) < limit:
        keyphrases = merge_unique(keyphrases + extract_tfidf_terms(job_description, limit=limit * 2))
    for phrase in keyphrases:
        normalized = normalize_phrase(phrase)
        if not normalized or normalized in seen:
            continue
        if len(normalized.split()) > 3:
            continue
        if phrase_in_resume(normalized, resume_text_norm, resume_token_set, resume_compact):
            continue
        if SKILLS_SET and normalize_skill(normalized) not in SKILLS_SET:
            continue
        if not skill_is_confident(normalize_skill(normalized), freq, required):
            continue
        seen.add(normalized)
        ordered_keywords.append(canonical_skill(phrase))
        if len(ordered_keywords) >= limit:
            return ordered_keywords

    resume_tokens = {normalize_token(t) for t in TOKEN_RE.findall(resume_text)}
    for token in TOKEN_RE.findall(job_description):
        normalized = normalize_token(token)
        if not normalized or normalized in STOPWORDS or normalized in seen:
            continue
        if normalized in resume_tokens:
            continue
        if SKILLS_SET and normalize_skill(normalized) not in SKILLS_SET:
            continue
        if not skill_is_confident(normalize_skill(normalized), freq, required):
            continue
        seen.add(normalized)
        cleaned = CLEAN_EDGE_RE.sub("", token)
        if cleaned.lower() in STOPWORDS:
            continue
        if cleaned:
            ordered_keywords.append(canonical_skill(cleaned))
        if len(ordered_keywords) >= limit:
            break

    return ordered_keywords


_SECTION_LABELS = {
    "summary": "Summary",
    "experience": "Experience",
    "projects": "Projects",
    "skills": "Skills",
    "education": "Education",
    "other": "Other",
}


def annotate_cv_lines(
    raw_sections_raw: dict,
    responsibility_result: dict,
    parsed_resume: dict = None,
) -> list:
    """Build per-section line annotations. Uses AI-parsed structure when available,
    falls back to raw section text so every section always has content."""
    matched_evidence_set = {
        normalize_phrase(item.get("evidence", ""))
        for item in (responsibility_result.get("matched_responsibilities") or [])
        if item.get("evidence")
    }
    pr = parsed_resume or {}
    raw = raw_sections_raw or {}

    def _score(text: str) -> dict:
        norm = normalize_phrase(text)
        if len(text.split()) < 5:
            return {"text": text, "quality": "neutral", "reason": None}
        if norm in matched_evidence_set:
            return {"text": text, "quality": "strong", "reason": "Directly evidences a job requirement."}
        has_verb = count_action_verbs(norm) > 0
        has_num = has_metrics(text)
        if has_verb and has_num:
            return {"text": text, "quality": "strong", "reason": "Action verb with measurable impact."}
        if has_verb or has_num:
            return {"text": text, "quality": "good", "reason": "Has action verb." if has_verb else "Includes a metric."}
        return {"text": text, "quality": "weak", "reason": "No action verb or quantified result — consider strengthening."}

    def _neutral(text: str) -> dict:
        return {"text": text, "quality": "neutral", "reason": None}

    def _raw_lines(section_key: str, scored: bool = False) -> list:
        """Split a raw section string into annotated line dicts."""
        text = raw.get(section_key, "") or ""
        out = []
        seen: set = set()
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("-•* ").strip()
            norm = normalize_phrase(line)
            if line and norm and norm not in seen:
                seen.add(norm)
                out.append(_score(line) if scored else _neutral(line))
        return out

    annotated = []

    # ── Summary ──────────────────────────────────────────
    summary_text = (pr.get("summary") or "").strip()
    lines_out: list = []
    if summary_text:
        seen: set = set()
        for sentence in re.split(r"(?<=[.!?])\s+", summary_text):
            s = sentence.strip()
            norm = normalize_phrase(s)
            if s and norm and norm not in seen:
                seen.add(norm)
                lines_out.append(_score(s))
    if not lines_out:
        lines_out = _raw_lines("summary", scored=True)
    if lines_out:
        annotated.append({"section": "summary", "section_label": "Summary", "lines": lines_out})

    # ── Experience ───────────────────────────────────────
    work_exp = pr.get("work_experience") or []
    lines_out = []
    if work_exp:
        for role in work_exp:
            title   = (role.get("title")      or "").strip()
            company = (role.get("company")    or "").strip()
            start   = (role.get("start_date") or "").strip()
            end     = (role.get("end_date")   or "").strip()
            dates   = f"{start} – {end}".strip(" –") if (start or end) else ""
            header  = "  ·  ".join(filter(None, [title, company]))
            if dates:
                header += f"  ({dates})"
            if header:
                lines_out.append(_neutral(header))
            for bullet in (role.get("bullets") or []):
                b = bullet.strip().lstrip("-•* ").strip()
                if b:
                    lines_out.append(_score(b))
    if not lines_out:
        lines_out = _raw_lines("experience", scored=True)
    if lines_out:
        annotated.append({"section": "experience", "section_label": "Experience", "lines": lines_out})

    # ── Projects ─────────────────────────────────────────
    projects = pr.get("projects") or []
    lines_out = []
    if projects:
        for proj in projects:
            name = (proj.get("name") or "Project").strip()
            tech = proj.get("tech_stack") or []
            header = name + ("  ·  " + ", ".join(tech[:6]) if tech else "")
            lines_out.append(_neutral(header))
            for bullet in (proj.get("bullets") or []):
                b = bullet.strip().lstrip("-•* ").strip()
                if b:
                    lines_out.append(_score(b))
    if not lines_out:
        lines_out = _raw_lines("projects", scored=True)
    if lines_out:
        annotated.append({"section": "projects", "section_label": "Projects", "lines": lines_out})

    # ── Skills ───────────────────────────────────────────
    skills_raw = raw.get("skills", "") or ""
    if not skills_raw.strip():
        skill_list = pr.get("skills") or []
        if skill_list:
            skills_raw = "\n".join(skill_list)
    lines_out = []
    seen = set()
    for raw_line in skills_raw.splitlines():
        line = raw_line.strip()
        norm = normalize_phrase(line)
        if line and norm and norm not in seen:
            seen.add(norm)
            lines_out.append(_neutral(line))
    if lines_out:
        annotated.append({"section": "skills", "section_label": "Skills", "lines": lines_out})

    # ── Education + Certifications ───────────────────────
    edu_lines = []
    seen = set()
    for entry in (pr.get("education") or []):
        degree      = (entry.get("degree")           or "").strip()
        institution = (entry.get("institution")      or "").strip()
        year        = (entry.get("graduation_year")  or "").strip()
        gpa         = (entry.get("gpa")              or "").strip()
        line = "  ·  ".join(filter(None, [degree, institution]))
        if year:
            line += f" ({year})"
        if gpa:
            line += f"  ·  GPA {gpa}"
        norm = normalize_phrase(line)
        if line and norm not in seen:
            seen.add(norm)
            edu_lines.append(_neutral(line))
    for cert in (pr.get("certifications") or []):
        c = cert.strip()
        norm = normalize_phrase(c)
        if c and norm not in seen:
            seen.add(norm)
            edu_lines.append(_neutral(c))
    if not edu_lines:
        edu_lines = _raw_lines("education")
    if edu_lines:
        annotated.append({"section": "education", "section_label": "Education", "lines": edu_lines})

    return annotated


FREE_TIER_SCAN_LIMIT = int(os.getenv("FREE_TIER_SCAN_LIMIT", "2"))


def _user_to_public(user: dict) -> dict:
    """Shape a user row into the JSON we return to the frontend."""
    lifetime = int(user.get("lifetime_scans") or 0)
    tier = user.get("tier") or "free"
    if tier == "paid":
        scans_remaining = None  # unlimited
    else:
        scans_remaining = max(0, FREE_TIER_SCAN_LIMIT - lifetime)
    return {
        "id": user["id"],
        "email": user["email"],
        "tier": tier,
        "email_verified": bool(user.get("email_verified")),
        "lifetime_scans": lifetime,
        "scans_remaining": scans_remaining,
        "free_tier_limit": FREE_TIER_SCAN_LIMIT,
    }


def _require_user(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency: returns the user row for the bearer token, or 401."""
    user_id = auth_utils.get_current_user_id(authorization=authorization)
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session no longer valid.")
    return user


@app.post("/auth/signup", dependencies=[Depends(rate_limit.require_auth_rate_limit)])
async def auth_signup(payload: dict):
    email = str((payload or {}).get("email") or "").strip().lower()
    password = str((payload or {}).get("password") or "")
    if not auth_utils.is_valid_email(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if len(password) < auth_utils.MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Password must be at least {auth_utils.MIN_PASSWORD_LEN} characters.")
    if db.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="An account with that email already exists.")

    verification_token = auth_utils.generate_secure_token()
    user = db.create_user(
        email=email,
        password_hash=auth_utils.hash_password(password),
        verification_token=verification_token,
    )
    # Fire-and-forget — failure shouldn't block signup. User can request resend.
    try:
        email_service.send_verification_email(email, verification_token)
    except Exception as exc:
        logger.warning("Could not send verification email to %s: %s", email, exc)

    token = auth_utils.create_jwt(user["id"], user["email"])
    return {"token": token, "user": _user_to_public(user)}


@app.post("/auth/login", dependencies=[Depends(rate_limit.require_auth_rate_limit)])
async def auth_login(payload: dict):
    email = str((payload or {}).get("email") or "").strip().lower()
    password = str((payload or {}).get("password") or "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required.")
    user = db.get_user_by_email(email)
    if not user or not auth_utils.verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = auth_utils.create_jwt(user["id"], user["email"])
    return {"token": token, "user": _user_to_public(user)}


@app.post("/auth/verify-email")
async def auth_verify_email(payload: dict):
    token = str((payload or {}).get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Verification token required.")
    user = db.get_user_by_verification_token(token)
    if not user:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has already been used.")
    db.mark_email_verified(user["id"])
    fresh = db.get_user_by_id(user["id"])
    return {"ok": True, "user": _user_to_public(fresh)}


@app.post("/auth/resend-verification")
async def auth_resend_verification(user: dict = Depends(_require_user)):
    if user.get("email_verified"):
        return {"ok": True, "already_verified": True}
    new_token = auth_utils.generate_secure_token()
    db.set_verification_token(user["id"], new_token)
    try:
        email_service.send_verification_email(user["email"], new_token)
    except Exception as exc:
        logger.warning("Could not resend verification: %s", exc)
        raise HTTPException(status_code=502, detail="Could not send the verification email. Please try again in a moment.")
    return {"ok": True}


@app.post("/auth/forgot-password", dependencies=[Depends(rate_limit.require_auth_rate_limit)])
async def auth_forgot_password(payload: dict):
    email = str((payload or {}).get("email") or "").strip().lower()
    if not auth_utils.is_valid_email(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    user = db.get_user_by_email(email)
    # Don't leak whether the email exists — always return 200.
    if user:
        reset_token = auth_utils.generate_secure_token()
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        db.set_password_reset_token(user["id"], reset_token, expires)
        try:
            email_service.send_password_reset_email(email, reset_token)
        except Exception as exc:
            logger.warning("Could not send password reset email to %s: %s", email, exc)
    return {"ok": True}


@app.post("/auth/reset-password")
async def auth_reset_password(payload: dict):
    token = str((payload or {}).get("token") or "").strip()
    new_password = str((payload or {}).get("password") or "")
    if not token:
        raise HTTPException(status_code=400, detail="Reset token required.")
    if len(new_password) < auth_utils.MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Password must be at least {auth_utils.MIN_PASSWORD_LEN} characters.")
    user = db.get_user_by_reset_token(token)
    if not user:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used.")
    expires_iso = user.get("password_reset_expires") or ""
    try:
        expires_at = datetime.fromisoformat(expires_iso)
    except (ValueError, TypeError):
        expires_at = None
    if not expires_at or datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="This reset link has expired. Request a new one.")
    db.update_password(user["id"], auth_utils.hash_password(new_password))
    return {"ok": True}


@app.get("/auth/me")
async def auth_me(user: dict = Depends(_require_user)):
    return {"user": _user_to_public(user)}


@app.post("/auth/delete-account")
async def auth_delete_account(payload: dict, user: dict = Depends(_require_user)):
    """GDPR right-to-be-forgotten. Requires password confirmation to prevent
    a stolen JWT from nuking the account."""
    password = str((payload or {}).get("password") or "")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required to confirm account deletion.")
    if not auth_utils.verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Password is incorrect.")
    deleted = db.delete_user_by_id(user["id"])
    if not deleted:
        # Should never happen — we just authenticated as this user.
        raise HTTPException(status_code=500, detail="Could not delete account.")
    return {"ok": True}


def _check_admin(authorization: Optional[str], x_admin_token: Optional[str]) -> None:
    """Admin endpoints accept the token via X-Admin-Token header (preferred)
    or fall back to Authorization: Bearer <token>. Either must match ADMIN_TOKEN."""
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Admin endpoints are disabled (no ADMIN_TOKEN set).")
    bearer = auth_utils.extract_bearer_token(authorization) or ""
    candidate = (x_admin_token or "").strip() or bearer
    if not candidate or candidate != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token.")


@app.get("/admin/users")
async def admin_list_users(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    limit: int = 100,
):
    _check_admin(authorization, x_admin_token)
    return {"users": db.list_users(limit=max(1, min(limit, 500)))}


@app.post("/admin/promote-user")
async def admin_promote_user(
    payload: dict,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
):
    _check_admin(authorization, x_admin_token)
    email = str((payload or {}).get("email") or "").strip().lower()
    tier = str((payload or {}).get("tier") or "paid").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Missing email.")
    if tier not in ("free", "paid"):
        raise HTTPException(status_code=400, detail="tier must be 'free' or 'paid'.")
    user = db.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail=f"No user with email {email}.")
    db.set_tier(user["id"], tier)
    fresh = db.get_user_by_id(user["id"])
    return {"ok": True, "user": _user_to_public(fresh) if fresh else None}


@app.post("/auth/status")
async def auth_status(payload: dict):
    """Legacy endpoint kept for backwards compatibility — pulls token from {'_token': ...} body."""
    token = str((payload or {}).get("_token") or "").strip()
    decoded = auth_utils.decode_jwt(token) if token else None
    if not decoded or "sub" not in decoded:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    try:
        user_id = int(decoded["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid session.")
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session no longer valid.")
    return _user_to_public(user)


@app.post("/feedback")
async def submit_feedback(payload: dict):
    rating  = str((payload or {}).get("rating") or "").strip()
    issues  = (payload or {}).get("issues") or []
    note    = str((payload or {}).get("note") or "").strip()[:500]
    score   = (payload or {}).get("match_score")
    email   = str((payload or {}).get("email") or "").strip()
    if rating not in ("accurate", "inaccurate"):
        raise HTTPException(status_code=400, detail="Invalid rating.")
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rating": rating,
        "issues": issues if isinstance(issues, list) else [],
        "note": note,
        "match_score": score,
        "email": email,
    }
    try:
        existing = json.loads(FEEDBACK_FILE.read_text()) if FEEDBACK_FILE.exists() else []
        existing.append(entry)
        FEEDBACK_FILE.write_text(json.dumps(existing, indent=2))
    except Exception as exc:
        logger.warning("Could not save feedback: %s", exc)
    return {"ok": True}


@app.get("/admin/feedback")
async def admin_feedback(token: str = ""):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token.")
    try:
        entries = json.loads(FEEDBACK_FILE.read_text()) if FEEDBACK_FILE.exists() else []
    except Exception:
        entries = []
    total      = len(entries)
    accurate   = sum(1 for e in entries if e.get("rating") == "accurate")
    inaccurate = sum(1 for e in entries if e.get("rating") == "inaccurate")
    issue_counts: dict = {}
    for e in entries:
        for issue in (e.get("issues") or []):
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
    return {
        "total_feedback": total,
        "accurate": accurate,
        "inaccurate": inaccurate,
        "accuracy_rate": f"{round(accurate/total*100)}%" if total else "n/a",
        "top_issues": sorted(issue_counts.items(), key=lambda x: -x[1]),
        "recent": list(reversed(entries))[:20],
    }


@app.get("/admin/usage")
async def admin_usage(token: str = ""):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token.")
    today = _today()
    rows = []
    for email, account in ACCOUNTS.items():
        limit = account.get("daily_limit", DEFAULT_DAILY_LIMIT)
        used_today = _scan_counts.get(email, {}).get(today, 0)
        total_all_time = sum(_scan_counts.get(email, {}).values())
        rows.append({
            "email": email,
            "daily_limit": limit,
            "scans_today": used_today,
            "scans_remaining": max(0, limit - used_today),
            "total_all_time": total_all_time,
            "history": _scan_counts.get(email, {}),
        })
    rows.sort(key=lambda r: r["scans_today"], reverse=True)
    return {"date": today, "users": rows}


@app.get("/status")
async def status():
    return {
        "gemini_key_set": bool(GEMINI_API_KEY),
        "gemini_client_ready": GENAI_CLIENT is not None,
        "genai_import_error": GENAI_IMPORT_ERROR,
        "models": {
            "parse": GEMINI_PARSE_MODEL,
            "embed": GEMINI_EMBED_MODEL,
            "rewrite": GEMINI_REWRITE_MODEL,
        },
        "gemini_seed": GEMINI_SEED,
        "analyze_cache": analysis_cache.status_metadata(),
    }


def gemini_skills_match(job_description: str, parsed_resume: dict) -> dict:
    """Use Gemini to extract skills from JD and semantically match against CV."""
    resume_text = str(parsed_resume.get("_resume_text") or "")
    cv_section = format_cv_match_evidence(parsed_resume, work_limit=70, project_limit=70)

    if GENAI_CLIENT and cv_section:
        try:
            prompt = (
                "You are matching a candidate's CV against a job description.\n\n"
                "Step 1: Extract ALL skills, tools, technologies, and competencies from the job description. "
                "Classify each as 'must_have' (explicitly required, essential) or 'nice_to_have' (preferred, bonus, or desirable).\n\n"
                "Step 2: For each skill, check whether the candidate's CV demonstrates it — using semantic understanding "
                "(e.g. 'AWS Lambda' counts for 'serverless', 'led a team of 5' counts for 'team leadership').\n\n"
                "Return ONLY valid JSON:\n"
                "{\n"
                '  "must_have": [\n'
                '    {"skill": "Python", "present": true, "cv_where": "[Data Engineer @ Acme] Built Python ETL pipelines..."}\n'
                "  ],\n"
                '  "nice_to_have": [\n'
                '    {"skill": "Kubernetes", "present": false, "cv_where": null}\n'
                "  ]\n"
                "}\n\n"
                "Rules:\n"
                "- Use semantic matching, not keyword matching.\n"
                "- Treat PROJECT EVIDENCE as valid first-class evidence. A project name, tech stack, or project bullet can prove a skill even if it is absent from the skills list.\n"
                "- A trading/backtesting project with signals like RSI, MACD, Kelly sizing, market data, or strategy optimisation proves trading/finance interest.\n"
                "- cv_where: quote the specific CV evidence line (keeping [Title @ Company], [Project: Name], or SKILLS prefix) that proves the skill. null if not found.\n"
                "- If a skill appears in both must_have and nice_to_have sections of the JD, put it in must_have only.\n"
                "- Don't duplicate skills across the two lists.\n"
                "- Return ONLY the JSON object, no markdown fences.\n"
            )
            contents = f"{prompt}\n\nJOB DESCRIPTION:\n{job_description[:3000]}\n\nCANDIDATE CV:\n{cv_section}"
            response = GENAI_CLIENT.models.generate_content(
                model=GEMINI_REWRITE_MODEL,
                contents=contents,
                config=gemini_generation_config(0),
            )
            raw = getattr(response, "text", "") or ""
            parsed = parse_json_response(raw)
            if isinstance(parsed, dict):
                def _clean_items(lst):
                    out = []
                    seen = set()
                    for item in (lst or []):
                        if not isinstance(item, dict):
                            continue
                        skill = str(item.get("skill") or "").strip()
                        if not skill or skill.lower() in seen:
                            continue
                        seen.add(skill.lower())
                        aggregate = aggregate_requirement_evidence(
                            skill,
                            parsed_resume,
                            resume_text,
                            ai_present=bool(item.get("present")),
                            ai_evidence=str(item.get("cv_where") or "").strip() or None,
                            ai_confidence="strong" if item.get("present") else "missing",
                        )
                        out.append({
                            "skill": skill,
                            "present": aggregate["present"],
                            "status": aggregate["status"],
                            "cv_where": aggregate["cv_where"],
                            "matched_count": aggregate["matched_count"],
                            "total_count": aggregate["total_count"],
                            "atomic_breakdown": aggregate["atomic_breakdown"],
                        })
                    return out
                return {
                    "must_have": filter_satisfied_alternative_missing_skills(
                        _clean_items(parsed.get("must_have")),
                        job_description,
                    ),
                    "nice_to_have": filter_satisfied_alternative_missing_skills(
                        _clean_items(parsed.get("nice_to_have")),
                        job_description,
                    ),
                }
        except Exception as exc:
            logger.warning("Gemini skills match failed, using fallback: %s", exc)

    # Fallback: text-based
    must_have_skills = extract_must_have_skills(job_description)
    resume_text = parsed_resume.get("_resume_text", "")
    resume_text_norm = normalize_phrase(resume_text)
    resume_token_set = set(resume_text_norm.split())
    resume_compact = resume_text_norm.replace(" ", "")

    def _text_present(skill):
        norm = normalize_phrase(skill)
        if phrase_in_resume(norm, resume_text_norm, resume_token_set, resume_compact):
            return True
        return find_cv_evidence_for_requirement(skill, parsed_resume, resume_text) is not None

    def _text_where(skill):
        found = find_cv_evidence_for_requirement(skill, parsed_resume, resume_text)
        return found["evidence"] if found else None

    must_items = []
    for s in must_have_skills:
        aggregate = aggregate_requirement_evidence(s, parsed_resume, resume_text)
        must_items.append({
            "skill": s,
            "present": aggregate["present"],
            "status": aggregate["status"],
            "cv_where": aggregate["cv_where"],
            "matched_count": aggregate["matched_count"],
            "total_count": aggregate["total_count"],
            "atomic_breakdown": aggregate["atomic_breakdown"],
        })
    combined = merge_unique(extract_keyphrases(job_description, limit=30) + extract_skill_tokens(job_description, limit=30))
    must_norms = {normalize_phrase(s) for s in must_have_skills}
    nice_items = []
    for s in combined:
        if normalize_phrase(s) in must_norms:
            continue
        aggregate = aggregate_requirement_evidence(s, parsed_resume, resume_text)
        nice_items.append({
            "skill": s,
            "present": aggregate["present"],
            "status": aggregate["status"],
            "cv_where": aggregate["cv_where"],
            "matched_count": aggregate["matched_count"],
            "total_count": aggregate["total_count"],
            "atomic_breakdown": aggregate["atomic_breakdown"],
        })
    return {
        "must_have": filter_satisfied_alternative_missing_skills(must_items, job_description),
        "nice_to_have": filter_satisfied_alternative_missing_skills(nice_items, job_description),
    }


def gemini_skills_and_ats(job_description: str, parsed_resume: dict, resume_text: str) -> dict:
    """Single Gemini call that does both skills matching and ATS keyword extraction.

    Replaces separate gemini_skills_match + gemini_ats_keywords to save one API round-trip.
    Returns {"skills": {...}, "ats_keywords": {...}}.
    """
    if not GENAI_CLIENT:
        return {
            "skills": gemini_skills_match(job_description, parsed_resume),
            "ats_keywords": {"hard_skills": [], "soft_skills": []},
        }

    cv_section = format_cv_match_evidence(parsed_resume, work_limit=70, project_limit=70)

    if not cv_section:
        return {
            "skills": gemini_skills_match(job_description, parsed_resume),
            "ats_keywords": {"hard_skills": [], "soft_skills": []},
        }

    prompt = (
        "You are matching a candidate's CV against a job description. Complete TWO tasks in one response.\n\n"
        "TASK 1 — SKILLS MATCHING:\n"
        "Extract ALL skills, tools, technologies, and competencies from the JD. "
        "Classify each as 'must_have' (required/essential) or 'nice_to_have' (preferred/bonus). "
        "For each skill, check whether the CV demonstrates it semantically "
        "(e.g. 'AWS Lambda' counts for 'serverless', 'led a team of 5' counts for 'team leadership').\n\n"
        "TASK 2 — ATS KEYWORDS:\n"
        "Extract EVERY keyword from the JD that an ATS would use to rank candidates. "
        "For each keyword count exact appearances in the JD (jd_count) and CV (cv_count, case-insensitive). "
        "Categorise into hard_skills (technical, tools, methodologies, certifications, domain terms) "
        "and soft_skills (behavioural, interpersonal, leadership qualities).\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "skills": {\n'
        '    "must_have": [{"skill": "Python", "present": true, "cv_where": "[Title @ Co] Built Python ETL..."}],\n'
        '    "nice_to_have": [{"skill": "Kubernetes", "present": false, "cv_where": null}]\n'
        "  },\n"
        '  "ats_keywords": {\n'
        '    "hard_skills": [{"skill": "Python", "jd_count": 3, "cv_count": 2}],\n'
        '    "soft_skills": [{"skill": "stakeholder management", "jd_count": 2, "cv_count": 0}]\n'
        "  }\n"
        "}\n\n"
        "Rules:\n"
        "- skills: semantic matching only; cv_where quotes the exact CV evidence line proving the skill, or null.\n"
        "- skills: treat PROJECT EVIDENCE as valid first-class evidence. Project names, tech stacks, and bullets can prove skills or domain interest even if the skills list omits them.\n"
        "- skills: a trading/backtesting project with signals like RSI, MACD, Kelly sizing, market data, or strategy optimisation proves trading/finance interest.\n"
        "- skills: if a skill is in both required and preferred sections of the JD, put it in must_have only.\n"
        "- ats_keywords: be exhaustive for hard_skills; sort each list by jd_count descending.\n"
        "- ats_keywords: exclude the company name, the exact job title of the post, and generic filler words.\n"
        "- Return ONLY the JSON object, no markdown fences.\n"
    )
    contents = (
        f"{prompt}\n\n"
        f"JOB DESCRIPTION:\n{job_description[:4000]}\n\n"
        f"CANDIDATE CV:\n{cv_section}\n\n"
        f"FULL CV TEXT (for ats cv_count):\n{resume_text[:3000]}"
    )

    try:
        response = GENAI_CLIENT.models.generate_content(
            model=GEMINI_REWRITE_MODEL,
            contents=contents,
            config=gemini_generation_config(0),
        )
        raw = getattr(response, "text", "") or ""
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected response shape")
    except Exception as exc:
        logger.warning("gemini_skills_and_ats failed, using fallback: %s", exc)
        return {
            "skills": gemini_skills_match(job_description, parsed_resume),
            "ats_keywords": {"hard_skills": [], "soft_skills": []},
        }

    def _clean_skills(lst):
        out, seen = [], set()
        for item in (lst or []):
            if not isinstance(item, dict):
                continue
            skill = str(item.get("skill") or "").strip()
            if not skill or skill.lower() in seen:
                continue
            seen.add(skill.lower())
            aggregate = aggregate_requirement_evidence(
                skill,
                parsed_resume,
                resume_text,
                ai_present=bool(item.get("present")),
                ai_evidence=str(item.get("cv_where") or "").strip() or None,
                ai_confidence="strong" if item.get("present") else "missing",
            )
            out.append({
                "skill": skill,
                "present": aggregate["present"],
                "status": aggregate["status"],
                "cv_where": aggregate["cv_where"],
                "matched_count": aggregate["matched_count"],
                "total_count": aggregate["total_count"],
                "atomic_breakdown": aggregate["atomic_breakdown"],
            })
        return out

    resume_text_norm_for_ats = normalize_phrase(resume_text)
    resume_token_set_for_ats = set(resume_text_norm_for_ats.split())
    resume_compact_for_ats = resume_text_norm_for_ats.replace(" ", "")

    def _clean_ats(lst):
        out, seen = [], set()
        for item in (lst or []):
            if not isinstance(item, dict):
                continue
            skill = str(item.get("skill") or "").strip()
            if not skill or skill.lower() in seen:
                continue
            seen.add(skill.lower())
            jd_count = max(1, int(item.get("jd_count") or 1))
            cv_count = max(0, int(item.get("cv_count") or 0))
            if cv_count == 0:
                for alias in _requirement_aliases(skill):
                    if phrase_in_resume(
                        normalize_phrase(alias),
                        resume_text_norm_for_ats,
                        resume_token_set_for_ats,
                        resume_compact_for_ats,
                    ):
                        cv_count = 1
                        break
            status = "missing" if cv_count == 0 else ("low" if cv_count < max(1, jd_count // 2) else "present")
            out.append({"skill": skill, "jd_count": jd_count, "cv_count": cv_count, "status": status})
        return out

    skills_raw = parsed.get("skills") or {}
    ats_raw = parsed.get("ats_keywords") or {}
    return {
        "skills": {
            "must_have": filter_satisfied_alternative_missing_skills(
                _clean_skills(skills_raw.get("must_have")),
                job_description,
            ),
            "nice_to_have": filter_satisfied_alternative_missing_skills(
                _clean_skills(skills_raw.get("nice_to_have")),
                job_description,
            ),
        },
        "ats_keywords": {
            "hard_skills": _clean_ats(ats_raw.get("hard_skills")),
            "soft_skills": _clean_ats(ats_raw.get("soft_skills")),
        },
    }


@app.post("/analyze")
async def analyze(
    resume: UploadFile = File(...),
    job_description: str = Form(...),
    job_source: str = Form("paste"),
    session_token: str = Form(""),
    authorization: Optional[str] = Header(None),
    debug: bool = False,
):
    # Resolve user: prefer Authorization: Bearer, fall back to legacy session_token form field.
    token = auth_utils.extract_bearer_token(authorization) or session_token.strip()
    decoded = auth_utils.decode_jwt(token) if token else None
    if not decoded or "sub" not in decoded:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        user_id = int(decoded["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid session.")
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session no longer valid.")

    # Free-tier lifetime gate. Paid users skip this.
    if (user.get("tier") or "free") != "paid":
        if int(user.get("lifetime_scans") or 0) >= FREE_TIER_SCAN_LIMIT:
            raise HTTPException(
                status_code=402,
                detail=f"You've used your {FREE_TIER_SCAN_LIMIT} free scans. Email gptc2903@gmail.com to upgrade for unlimited scans + company insights.",
            )

    file_bytes = await resume.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded resume file is empty.")

    resume_text = extract_pdf_text(file_bytes)
    job_description = clean_text(job_description)
    if not resume_text:
        raise HTTPException(
            status_code=400,
            detail="Could not extract any text from the uploaded PDF.",
        )
    if not job_description:
        raise HTTPException(status_code=400, detail="Job description is empty.")

    cache_key = analyze_cache_key(resume_text, job_description)
    cached_response = get_cached_analyze_response(cache_key)
    if cached_response is not None:
        return attach_analyze_request_context(
            cached_response,
            user=user,
            job_source=job_source,
            debug=debug,
            cache_key=cache_key,
            cache_hit=True,
        )

    debug_info = {} if debug else None

    # Phase 1: Parse resume — everything else depends on this
    parsed_resume = await asyncio.to_thread(parse_resume, resume_text, debug_info)
    parsed_resume["_resume_text"] = resume_text

    # Local computation — no API calls
    parsed_skills = parsed_resume.get("skills") or []
    parsed_tools = parsed_resume.get("tools") or []
    resume_text_norm = normalize_phrase(resume_text)
    resume_token_set = set(resume_text_norm.split())
    resume_compact = resume_text_norm.replace(" ", "")
    resume_sections = split_resume_sections(resume_text)
    resume_sections_raw = split_resume_sections_raw(resume_text)
    tfidf_terms = extract_tfidf_terms(job_description, limit=40)
    required_years = extract_required_years(job_description)
    resume_years = (
        years_from_work_experience(parsed_resume.get("work_experience") or [])
        or parsed_resume.get("years_experience")
        or extract_resume_years(resume_text)
    )
    responsibility_candidates = extract_job_responsibilities(job_description)

    # Phase 2: All independent API calls run in parallel (70 s hard cap, 20 s before frontend timeout)
    try:
        (
            cv_sections_analysis,
            unified_result,
            responsibility_result,
            semantic_score,
            textrazor_terms,
            gemini_sections,
        ) = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(analyze_cv_sections, resume_text, parsed_resume, job_description),
                asyncio.to_thread(gemini_skills_and_ats, job_description, parsed_resume, resume_text),
                asyncio.to_thread(gemini_responsibility_match, responsibility_candidates, parsed_resume),
                asyncio.to_thread(compute_semantic_score, resume_text, job_description, None),
                asyncio.to_thread(textrazor_extract_phrases, job_description, None),
                # 6th task: per-section feedback via Gemini. Runs in parallel so no added latency.
                # Skips role_fit_breakdown context here (chicken-and-egg) — the prompt is strong
                # enough to infer JD vs CV gaps on its own.
                asyncio.to_thread(gemini_section_feedback, parsed_resume, job_description, None),
            ),
            timeout=70,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Analysis timed out — the AI service is under load. Please try again in a moment.",
        )

    skills_result = unified_result["skills"]
    ats_keywords_result = unified_result["ats_keywords"]
    must_have_items   = skills_result["must_have"]
    nice_to_have_items = skills_result["nice_to_have"]
    present_must_have  = [s["skill"] for s in must_have_items if s.get("status") == "present"]
    partial_must_have  = [s["skill"] for s in must_have_items if s.get("status") == "partial"]
    missing_must_have  = [s["skill"] for s in must_have_items if s.get("status", "missing") == "missing"]
    present_nice_to_have = [s["skill"] for s in nice_to_have_items if s.get("status") == "present"]
    partial_nice_to_have = [s["skill"] for s in nice_to_have_items if s.get("status") == "partial"]
    missing_nice_to_have = [s["skill"] for s in nice_to_have_items if s.get("status", "missing") == "missing"]

    def _coverage_score(items: list[dict]) -> float:
        if not items:
            return 0.0
        total = 0.0
        for item in items:
            status = item.get("status") or ("present" if item.get("present") else "missing")
            if status == "present":
                total += 1.0
            elif status == "partial":
                total += 0.5
        return total / len(items)

    must_coverage = _coverage_score(must_have_items)
    nice_coverage = _coverage_score(nice_to_have_items)
    combined_terms = merge_unique(textrazor_terms + tfidf_terms)

    experience_result = compute_experience_match(
        raw_sections=resume_sections_raw,
        resume_text=resume_text,
        job_description=job_description,
        responsibilities=responsibility_candidates,
        responsibility_result=responsibility_result,
        required_years=required_years,
        resume_years=resume_years,
    )
    skills_match_score = round(
        ((must_coverage * 0.7) + (nice_coverage * 0.3)) * 100,
        2,
    )

    inferred_missing = infer_missing_keywords(
        resume_text,
        job_description,
        prefetched_phrases=combined_terms,
    )
    skills_present = merge_unique(present_must_have + present_nice_to_have)
    skills_partial = merge_unique(partial_must_have + partial_nice_to_have)
    skills_missing = merge_unique(missing_must_have + missing_nice_to_have)

    match_score = round(
        (
            responsibility_result["score"] * RESPONSIBILITY_MATCH_WEIGHT
            + experience_result["score"] * EXPERIENCE_MATCH_WEIGHT
            + skills_match_score * SKILLS_MATCH_WEIGHT
            + semantic_score * SEMANTIC_MATCH_WEIGHT
        ),
        2,
    )
    match_score = max(0.0, min(100.0, match_score))
    cv_highlights = annotate_cv_lines(resume_sections_raw, responsibility_result, parsed_resume)

    role_fit_breakdown = {
        "responsibility_match_score": responsibility_result["score"],
        "experience_match_score": experience_result["score"],
        "skills_match_score": skills_match_score,
        "semantic_score": round(semantic_score, 2),
        "final_match_score": match_score,
        "matched_responsibilities": responsibility_result["matched_responsibilities"],
        "missing_responsibilities": responsibility_result["missing_responsibilities"],
        "matched_action_phrases": responsibility_result["matched_action_phrases"],
        "missing_action_phrases": responsibility_result["missing_action_phrases"],
        "experience_evidence": experience_result["experience_evidence"],
        "experience_gaps": experience_result["experience_gaps"],
        "skills_present": skills_present,
        "skills_partial": skills_partial,
        "skills_missing": skills_missing,
        "weights": {
            "responsibility": RESPONSIBILITY_MATCH_WEIGHT,
            "experience": EXPERIENCE_MATCH_WEIGHT,
            "skills": SKILLS_MATCH_WEIGHT,
            "semantic": SEMANTIC_MATCH_WEIGHT,
        },
        "responsibility_detail": {
            "total_responsibilities": len(responsibility_candidates),
            "matched_count": len(responsibility_result["matched_responsibilities"]),
            "missing_count": len(responsibility_result["missing_responsibilities"]),
            "evidence_by_section": responsibility_result["evidence_by_section"],
            "similarity_threshold": RESPONSIBILITY_SIMILARITY_THRESHOLD,
        },
        "experience_detail": {
            "required_years": experience_result["required_years"],
            "resume_years": experience_result["resume_years"],
            "years_score": experience_result["years_score"],
            "date_coverage_score": experience_result["date_coverage_score"],
            "responsibility_evidence_score": experience_result["responsibility_evidence_score"],
            "title_alignment_score": experience_result["title_alignment_score"],
            "title_alignment": experience_result["title_alignment"],
        },
        "skills_detail": {
            "must_have": must_have_items,
            "nice_to_have": nice_to_have_items,
            "must_have_present": merge_unique(present_must_have),
            "must_have_partial": merge_unique(partial_must_have),
            "must_have_missing": merge_unique(missing_must_have),
            "nice_to_have_present": merge_unique(present_nice_to_have),
            "nice_to_have_partial": merge_unique(partial_nice_to_have),
            "nice_to_have_missing": merge_unique(missing_nice_to_have),
            "must_coverage": round(must_coverage * 100, 2),
            "nice_coverage": round(nice_coverage * 100, 2),
        },
        "job_description": {
            "source": job_source if job_source in {"paste", "url"} else "paste",
            "char_count": len(job_description),
        },
    }

    response = {
        "match_score": match_score,
        "missing_keywords": skills_missing,
        "resume_text": resume_text,
        "cv_highlights": cv_highlights,
        "role_fit_breakdown": role_fit_breakdown,
        # Prefer the Gemini-powered section feedback (richer, JD-aware, references actual CV content);
        # fall back to the heuristic checklist if the Gemini call returned nothing.
        "section_feedback": (
            gemini_sections
            if isinstance(gemini_sections, dict) and gemini_sections
            else build_section_feedback(
                resume_sections_raw,
                resume_sections,
                job_description,
                parsed_resume,
            )
        ),
        "candidate_profile": {
            "seniority_level": parsed_resume.get("seniority_level"),
            "industry_domains": parsed_resume.get("industry_domains") or [],
            "location": parsed_resume.get("location"),
            "management_experience": parsed_resume.get("management_experience") or {},
            "employment_gaps": parsed_resume.get("employment_gaps") or [],
            "languages": parsed_resume.get("languages") or [],
            "links": parsed_resume.get("links") or {},
            "quantified_achievements": parsed_resume.get("quantified_achievements") or [],
            "achievements": parsed_resume.get("achievements") or [],
        },
        "cv_sections_analysis": cv_sections_analysis,
        "ats_keywords": ats_keywords_result,
    }
    if debug:
        response["debug"] = {
            **(debug_info or {}),
            "missing_keywords_count": len(skills_missing),
            "missing_keywords_sample": skills_missing[:20],
            "coverage_must": round(must_coverage * 100, 2),
            "coverage_nice": round(nice_coverage * 100, 2),
            "responsibility_match_score": responsibility_result["score"],
            "experience_match_score": experience_result["score"],
            "skills_match_score": skills_match_score,
            "semantic_score": round(semantic_score, 2),
            "weights": {
                "responsibility": RESPONSIBILITY_MATCH_WEIGHT,
                "experience": EXPERIENCE_MATCH_WEIGHT,
                "skills": SKILLS_MATCH_WEIGHT,
                "semantic": SEMANTIC_MATCH_WEIGHT,
            },
            "skills_loaded": len(SKILLS_SET),
            "skills_path": SKILLS_PATH,
            "resume_excerpt": resume_text[:1200],
            "responsibilities_detected": len(responsibility_candidates),
        }

    # Add a "why your score is X" explainer so the frontend can render an actionable
    # callout under the score ring.
    response["score_breakdown"] = build_score_explainer(response)
    set_cached_analyze_response(cache_key, response, resume_text, job_description)

    # Only count the scan now that we know the response is valid.
    # Skip for paid tier; we leave their counter alone.
    if (user.get("tier") or "free") != "paid":
        db.increment_lifetime_scans(user["id"])
    return attach_analyze_request_context(
        response,
        user=user,
        job_source=job_source,
        debug=debug,
        cache_key=cache_key,
        cache_hit=False,
    )


def build_score_explainer(analyze_response: dict) -> dict:
    """Turn the raw score/breakdown into a user-facing explainer.

    Returns:
      {
        "current_score": int,
        "potential_score": int,         # achievable if top fixes are addressed
        "verdict_line": str,            # one-sentence framing
        "factors_pulling_down": [       # ranked, highest-impact first
          {"label": str, "points_lost": int, "fix": str}
        ],
        "factors_pulling_up": [str],   # what's already working
      }
    """
    score = round(float(analyze_response.get("match_score") or 0))
    breakdown = analyze_response.get("role_fit_breakdown") or {}
    ats = analyze_response.get("ats_keywords") or {}

    resp_detail = breakdown.get("responsibility_detail") or {}
    matched = breakdown.get("matched_responsibilities") or []
    missing_resps = breakdown.get("missing_responsibilities") or []
    skills_detail = breakdown.get("skills_detail") or {}
    must = skills_detail.get("must_have") or []
    nice = skills_detail.get("nice_to_have") or []
    exp_detail = breakdown.get("experience_detail") or {}

    factors_down: list[dict] = []
    factors_up: list[str] = []

    # 1) Missing essential responsibilities = biggest single lever
    essential_missing = [m for m in missing_resps if (m.get("category") or "essential") != "nice_to_have"]
    if essential_missing:
        names = [m.get("responsibility") or "" for m in essential_missing[:3]]
        names = [n for n in names if n]
        factors_down.append({
            "label": f"{len(essential_missing)} essential responsibilit{'ies' if len(essential_missing) != 1 else 'y'} not evidenced",
            "points_lost": min(20, len(essential_missing) * 6),
            "fix": "Add bullets that explicitly evidence: " + "; ".join(names[:3]) if names else "",
        })

    # 2) Partial responsibilities — could be upgraded to strong with evidence
    partial = [m for m in matched if m.get("confidence") == "partial"]
    if partial:
        partial_names = [p.get("responsibility") or "" for p in partial[:3]]
        partial_names = [n for n in partial_names if n]
        factors_down.append({
            "label": f"{len(partial)} responsibilit{'ies' if len(partial) != 1 else 'y'} only partially evidenced",
            "points_lost": min(15, len(partial) * 4),
            "fix": "Strengthen evidence with metrics or exact-keyword bullets for: " + "; ".join(partial_names[:3]) if partial_names else "",
        })

    # 3) Missing must-have skills
    must_missing = [s for s in must if (s.get("status") or ("present" if s.get("present") else "missing")) == "missing"]
    if must_missing:
        missing_names = []
        for s in must_missing[:5]:
            kw = s.get("skill") or s.get("keyword") or ""
            if kw:
                missing_names.append(kw)
        factors_down.append({
            "label": f"{len(must_missing)} must-have skill{'s' if len(must_missing) != 1 else ''} missing from CV",
            "points_lost": min(15, len(must_missing) * 2),
            "fix": "Add to your skills/tools section if you have any experience with: " + ", ".join(missing_names) if missing_names else "",
        })

    must_partial = [s for s in must if (s.get("status") or ("present" if s.get("present") else "missing")) == "partial"]
    if must_partial:
        partial_names = []
        for s in must_partial[:5]:
            kw = s.get("skill") or s.get("keyword") or ""
            if kw:
                partial_names.append(kw)
        factors_down.append({
            "label": f"{len(must_partial)} must-have skill{'s' if len(must_partial) != 1 else ''} only partially evidenced",
            "points_lost": min(10, len(must_partial) * 2),
            "fix": "Make the missing sub-skills explicit for: " + ", ".join(partial_names) if partial_names else "",
        })

    # 4) ATS hard-skill coverage
    hard = ats.get("hard_skills") or []
    ats_missing = [
        (s.get("keyword") or s.get("term") or "")
        for s in hard
        if (s.get("status") == "missing") and (s.get("keyword") or s.get("term"))
    ]
    if ats_missing:
        factors_down.append({
            "label": f"{len(ats_missing)} ATS keyword{'s' if len(ats_missing) != 1 else ''} from the JD not present",
            "points_lost": min(10, len(ats_missing) * 2),
            "fix": "Mirror these JD keywords in your CV (only if you genuinely have the experience): "
                   + ", ".join(ats_missing[:6]),
        })

    # 5) Experience years gap
    req_years = exp_detail.get("required_years")
    cand_years = exp_detail.get("candidate_years")
    meets = exp_detail.get("meets_requirement")
    if req_years and cand_years is not None and meets is False:
        gap = max(0, int(req_years) - int(cand_years))
        if gap > 0:
            factors_down.append({
                "label": f"{gap} year{'s' if gap != 1 else ''} short of required experience",
                "points_lost": min(15, gap * 5),
                "fix": "If your CV understates total experience, expand earlier roles or projects with dates.",
            })

    # Sort factors_down by points_lost desc, cap at top 3
    factors_down.sort(key=lambda x: -x.get("points_lost", 0))
    factors_down = factors_down[:3]

    # Build factors_up (what's working)
    strong = [m for m in matched if m.get("confidence") == "strong"]
    if strong:
        factors_up.append(f"{len(strong)} responsibilit{'ies' if len(strong) != 1 else 'y'} clearly evidenced in your experience")
    if must:
        must_present = [s for s in must if (s.get("status") or ("present" if s.get("present") else "missing")) == "present"]
        must_partial_up = [s for s in must if (s.get("status") or ("present" if s.get("present") else "missing")) == "partial"]
        if must_present:
            factors_up.append(f"{len(must_present)} of {len(must)} must-have skills already in your CV")
        if must_partial_up:
            factors_up.append(f"{len(must_partial_up)} must-have skill{'s' if len(must_partial_up) != 1 else ''} are partially covered and could be strengthened")
    if exp_detail.get("meets_requirement"):
        factors_up.append("Years of experience meets or exceeds the requirement")

    # Potential score = current + points recoverable if user addresses top fixes
    recoverable = sum(f.get("points_lost", 0) for f in factors_down)
    potential = min(100, score + recoverable)

    # Verdict line
    if score >= 80:
        verdict = "Your CV is already a strong match. Small refinements could push you higher."
    elif score >= 60:
        verdict = f"You're in competitive range. Closing the gaps below could lift you to {potential}."
    elif score >= 40:
        verdict = f"You have a foundation but several gaps are pulling the score down. Addressing the items below could get you to {potential}."
    else:
        verdict = "Significant gaps between this CV and the role. Focus on the items below before applying."

    return {
        "current_score": score,
        "potential_score": potential,
        "verdict_line": verdict,
        "factors_pulling_down": factors_down,
        "factors_pulling_up": factors_up,
    }


@app.post("/rewrite-cv")
async def rewrite_cv(payload: dict):
    resume_text = clean_text(str((payload or {}).get("resume_text") or ""))
    job_description = clean_text(str((payload or {}).get("job_description") or ""))
    role_fit_breakdown = (payload or {}).get("role_fit_breakdown") or {}

    if not resume_text:
        raise HTTPException(status_code=400, detail="Missing resume_text.")
    if not job_description:
        raise HTTPException(status_code=400, detail="Missing job_description.")

    rewrite = generate_cv_rewrite(
        resume_text=resume_text,
        job_description=job_description,
        role_fit_breakdown=role_fit_breakdown if isinstance(role_fit_breakdown, dict) else {},
    )
    return {"rewrite": rewrite}


COVER_LETTER_SYSTEM_PROMPT = """You are a senior career writer producing a professional, tailored cover letter for a real human applying to a real job. A cover letter is a marketing tool, not an autobiography. Every paragraph must do specific work. Target length: 420–500 words.

Required structure: five paragraphs, single blank line between each. Each paragraph below has a fixed role — do not merge them or change their order.

PARAGRAPH 1 — Introduction (2–3 sentences, ~60–80 words)
- Sentence 1: Introduce the candidate by their academic/professional background AND state hands-on experience areas in a three-part list (e.g. "with hands-on experience building scalable backend systems, automated workflows, and robust data pipelines").
- Sentence 2: Name the target company's specific mission/values (drawn from the JD) and state that it resonates with the candidate; in the same sentence or a third, express excitement to contribute as the specific role and mention the skill themes the candidate brings.
- Example shape: "I am a [degree/background] and [other distinguishing fact like co-founder of X], with hands-on experience building [A], [B], and [C]. [Company]'s mission to [paraphrased mission from JD] resonates deeply with me, and I am excited to contribute my skills in [skill 1], [skill 2], and [skill 3] as a [Role] on the [team]."

PARAGRAPH 2 — DEEP DIVE on FIRST major role (~85–110 words, 4–5 sentences)
- Name the role title AND employer (e.g. "As Co-Founder and Data Analyst at MySchola, I...").
- Pack in 3–4 specific actions FROM THIS ROLE'S CV BULLETS, each with a metric or concrete scope (e.g. "designed and maintained backend pipelines using Python, Node.js, and AWS Lambda, supporting 60+ active users").
- Use commas/em-dashes to chain actions densely: "I implemented X improving Y by N%, automated Z reducing manual workload by M%, and integrated multiple APIs for seamless data flow."
- Close with one sentence linking this role to one or two SPECIFIC JD requirements, paraphrased cleanly: "This experience aligns closely with [Company]'s focus on [JD theme 1], [JD theme 2], and [JD theme 3]."

PARAGRAPH 3 — DEEP DIVE on SECOND major role (~70–95 words, 3–4 sentences)
- Name the role title AND employer (e.g. "In my role as a Full Stack Developer at MHR, I...").
- Pack in 2–3 specific actions from THIS role's bullets with metrics or tooling names.
- Close with a sentence linking this role's experience to JD-relevant capabilities, paraphrased: "My experience with [X], [Y], and [Z] equips me to contribute effectively to the [team]'s cross-functional projects and technical design initiatives."

PARAGRAPH 4 — Additional projects, skills, and JD-aligned capabilities (~80–110 words)
- Open with: "Additionally, I have developed expertise in [theme 1], [theme 2], and [theme 3] through projects such as..."
- Name 1 specific project from the CV's projects section, with what was built and the metric (e.g. "a serverless expense compliance system using AWS Step Functions and DynamoDB, which reduced manual auditing effort by 90%").
- Choose the project with the strongest JD/domain overlap, not the project with the flashiest metric. If the JD mentions trading, finance, markets, securities, equities, options, or proprietary trading and the CV contains a trading/backtesting/market-data project, paragraph 4 MUST mention that project by name and include the trading evidence (e.g. RSI, MACD, Kelly sizing, strategy optimisation, market data).
- Close with one tight sentence naming a SHORT list (NO MORE THAN 4 items) of higher-level capability areas the candidate is comfortable with — pick the areas most relevant to the JD. DO NOT list 8+ individual tools. Wrong: "Python, JavaScript, SQL, Node.js, AWS Lambda, RDS, DynamoDB, Step Functions, Firebase, Firestore, REST APIs, FastAPI, MySQL". Right: "I am comfortable using ETL orchestration tools, managing cloud infrastructure, and implementing robust testing practices". Use the format: " - skills directly relevant to [Company]'s requirements for [JD-derived themes]." (single hyphen with spaces, NOT an em-dash, to avoid encoding glitches).

PARAGRAPH 5 — Why this company + conclusion (~75–95 words, 4 sentences + thank you)
- Sentence 1: "I am particularly drawn to [Company]'s [specific value 1 from JD], [specific value 2 from JD], and [specific value 3 from JD]."
- Sentence 2: "I am confident that my experience in [candidate's strength 1] and [strength 2], combined with my [trait — e.g. collaborative mindset / analytical approach] and passion for [field/work], makes me a strong fit for the [Role] role."
- If sentence 2 mentions passion or interest in the target field, that field interest must be evidenced by a concrete CV project, role, or achievement. Otherwise use "eagerness to deepen my exposure to [field/work]" instead.
- Sentence 3: "I look forward to contributing to your team and supporting [Company]'s mission to [paraphrase from JD]."
- Then on its own line, with blank line above: "Thank you for your time and consideration."

Sign-off: blank line, then "Sincerely," on its own line, then the candidate's full name on the next line (no extra blank line between Sincerely and the name).

CRITICAL EXTRACTION RULES
- Extract the COMPANY NAME and ROLE TITLE from the JD — use them verbatim throughout, never with placeholders.
- Extract the CANDIDATE NAME from the top of the CV.
- Pull measurable achievements (numbers, percentages, counts) only from the CV. Do not invent.
- Do not over-claim years of experience. If unsure, write "over the past few years" or "across my recent roles" instead of a specific number.
- Do not assert passion, interest, or domain motivation as a bare claim. If you write "passion for trading", "interest in finance", or similar, anchor it to a concrete CV item in the same paragraph, preferably the most JD-relevant project. If there is no concrete CV evidence, write "I am eager to deepen my exposure to [domain]" instead.
- Mirror JD vocabulary in paragraphs 3 and 4 — recruiters scan for matched terminology.
- If the CV has no name, omit it and just write "Sincerely," with nothing after.

ABSOLUTE NO-HALLUCINATION RULE FOR SKILLS AND TOOLS
This is the single most important rule. When writing paragraph 3, you MUST ONLY name programming languages, frameworks, databases, tools, cloud services, and methodologies that appear EXPLICITLY in the candidate's CV. If a tool is required by the JD but the CV does not list it, you must NOT claim the candidate is "familiar with", "comfortable with", "experienced in", or "has practical expertise in" that tool.
Examples of forbidden moves:
- JD requires Terraform; CV does not list Terraform → DO NOT mention Terraform
- JD requires Kubernetes; CV mentions only Docker → DO NOT mention Kubernetes (and DO mention Docker)
- JD requires Snowflake/Neo4j/Kafka; CV does not list them → DO NOT name those tools
Instead, in paragraph 3 only name CV-attested skills. In paragraph 5 you may write "I am eager to deepen my experience in [JD-required tool/area]" — this honestly signals interest without claiming experience the candidate doesn't have.
Before finalising your output, re-read paragraph 3 and verify EVERY tool/language/framework/service mentioned appears somewhere in the CV. If any do not, remove them.

OUTPUT REQUIREMENTS
- Return ONLY the cover letter text. No preamble, no markdown fences, no "[Start of Cover Letter]" markers.
- First line: "Dear Hiring Manager," (or use a specific name only if explicitly named in the JD).
- Five paragraphs separated by single blank lines.
- End with the sign-off block as described above.
- Total length: 420 to 480 words. Hit the lower bound at minimum. If your draft is under 400 words, EXPAND paragraphs 2 and 4 with more specifics from the CV and JD before returning. Be dense, not padded.
- DO NOT use em-dashes ("—") or en-dashes ("–") ANYWHERE in the output. Use commas, semicolons, full stops, or a hyphen with spaces ( - ) instead. Em-dashes cause encoding issues in some downstream systems.
- DO NOT use smart/curly quotation marks ("smart" or 'smart'). Use straight quotes only (" and ').
- Use ASCII-safe punctuation throughout (regular hyphens, periods, commas, semicolons, colons, parentheses).

────────────────────────────────────────────────────────────────────
STYLE REFERENCE (FOR STRUCTURE ONLY — DO NOT COPY ANY FACTS, NAMES, PHRASES, OR DETAILS)

The cover letter below is a model of the DENSITY, RHYTHM, and SENTENCE-LEVEL STRUCTURE we want. Study how each paragraph:
- packs multiple specific actions into each sentence,
- weaves the candidate's experience with the target role's responsibilities,
- uses three-part lists ("X, Y, and Z" / "A, B, and C"),
- transitions between paragraphs with natural connectors ("Over the past few years…", "I bring strong skills in…", "I am particularly drawn to…", "I am confident that…"),
- closes with a "Thank you for your time and consideration." line on its own before "Sincerely,".

You MUST mirror this style, but you MUST NOT copy any specific fact, employer, product, role, value statement, or phrase from it into your output. Your output must come entirely from the actual CV and JD provided after this reference.

──── Begin style reference ────

Dear Hiring Manager,

I am a Computer Science (Artificial Intelligence) graduate and co-founder of a data-driven EdTech platform, with hands-on experience building scalable backend systems, automated workflows, and robust data pipelines. [Company]'s mission to [paraphrased mission] resonates deeply with me, and I am excited to contribute my skills in [skill area 1], [skill area 2], and [skill area 3] as a [Role] on the [team name].

As [Role 1 Title] at [Employer 1], I designed and maintained backend pipelines and serverless workflows using [Tool 1], [Tool 2], and [Tool 3], supporting [scale e.g. "60+ active users"]. I implemented data validation and integrity checks that improved system reliability by [N]%, automated operational processes reducing manual workload by [N]%, and integrated multiple APIs for seamless data flow. This experience aligns closely with [Company]'s focus on [JD theme 1], [JD theme 2], and [JD theme 3].

In my role as a [Role 2 Title] at [Employer 2], I built backend services, end-to-end data flows, and cloud-based application infrastructure on [Cloud Platform]. I contributed to data migration, workflow automation, and structured reporting for stakeholders, ensuring reliable, high-quality outputs and [achievement metric, e.g. "100% on-time project delivery"]. My experience with [Skill X], [Skill Y], and [Methodology Z] equips me to contribute effectively to the [team name]'s cross-functional projects and technical design initiatives.

Additionally, I have developed expertise in [theme 1], [theme 2], and [theme 3] through projects such as [project name], a [brief description] using [tools from CV], which [measurable outcome, e.g. "reduced manual auditing effort by 90%"]. I am comfortable using [skill area from CV], managing [skill area from CV], and implementing [skill area from CV] - skills directly relevant to [Company]'s requirements for [JD-derived themes].

I am particularly drawn to [Company]'s [specific value 1 from JD], [specific value 2 from JD], and [specific value 3 from JD]. I am confident that my experience in [candidate strength 1] and [strength 2], combined with my collaborative mindset and passion for [field/work], makes me a strong fit for the [Role] role. I look forward to contributing to your team and supporting [Company]'s mission to [paraphrase from JD].

Thank you for your time and consideration.

Sincerely,
[Candidate Name]

──── End style reference ────

REMEMBER: the reference above used placeholders [Company], [Role], [Candidate Name], [Project A], [Company B] only because we are showing the SHAPE. In your actual output for the real candidate below, fill EVERY field with the real specifics drawn from the provided CV and JD. NEVER output a literal "[Company]" or other placeholder. Adapt the SENTENCE STRUCTURE to the candidate's real CV: if their CV is in a non-technical field (marketing, finance, healthcare, etc.), still mirror the rhythm but draw on THEIR actual roles, tools, achievements, and the target company's actual mission and values.
"""


def gemini_generate_cover_letter(resume_text: str, job_description: str) -> str:
    if not GENAI_CLIENT:
        detail = "GEMINI_API_KEY is not set."
        if GENAI_IMPORT_ERROR:
            detail = f"Gemini SDK unavailable: {GENAI_IMPORT_ERROR}"
        raise HTTPException(status_code=503, detail=detail)

    domain_notes = extract_domain_evidence_notes(resume_text, job_description)
    domain_notes_block = (
        "\n".join(f"- {note}" for note in domain_notes)
        if domain_notes
        else "(No special domain evidence detected by the pre-scan.)"
    )

    prompt = f"""{COVER_LETTER_SYSTEM_PROMPT}

---
SOURCE CV:
{resume_text}

---
JOB DESCRIPTION:
{job_description}

---
JD-RELEVANT CV EVIDENCE TO PRIORITISE:
{domain_notes_block}

---
Generate the cover letter now."""

    response = GENAI_CLIENT.models.generate_content(
        model=GEMINI_REWRITE_MODEL,
        contents=prompt,
        config=gemini_generation_config(0.4),
    )
    text = (getattr(response, "text", "") or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="Cover letter generation returned an empty response.")
    # Strip any stray markdown fences or wrapper markers the model sometimes adds.
    for marker in ("```", "[Start of Cover Letter]", "[End of Cover Letter]"):
        text = text.replace(marker, "")
    # Replace problematic Unicode punctuation that the model often adds despite instructions,
    # which can render as mangled chars in some terminals / downstream apps.
    text = (
        text
        .replace("—", " - ")   # em dash
        .replace("–", "-")     # en dash
        .replace("‘", "'")     # left single quote
        .replace("’", "'")     # right single quote
        .replace("“", '"')     # left double quote
        .replace("”", '"')     # right double quote
        .replace("…", "...")  # ellipsis
    )
    # Collapse any double spaces created by the em-dash replacement.
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()


@app.post("/generate-cover-letter")
async def generate_cover_letter(payload: dict, authorization: Optional[str] = Header(None)):
    # Auth required — cover letters cost real API calls.
    token = auth_utils.extract_bearer_token(authorization)
    decoded = auth_utils.decode_jwt(token) if token else None
    if not decoded or "sub" not in decoded:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        user = db.get_user_by_id(int(decoded["sub"]))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid session.")
    if not user:
        raise HTTPException(status_code=401, detail="Session no longer valid.")

    resume_text = clean_text(str((payload or {}).get("resume_text") or ""))
    job_description = clean_text(str((payload or {}).get("job_description") or ""))
    if not resume_text:
        raise HTTPException(status_code=400, detail="Missing resume_text.")
    if not job_description:
        raise HTTPException(status_code=400, detail="Missing job_description.")

    letter = gemini_generate_cover_letter(resume_text, job_description)
    return {"cover_letter": letter}


@app.post("/scrape-job")
async def scrape_job(payload: dict):
    url = (payload or {}).get("url", "")
    if not isinstance(url, str) or not url:
        raise HTTPException(status_code=400, detail="Missing or invalid url.")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL.")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Request failed: {exc}") from exc

    if response.status_code == 403:
        raise HTTPException(status_code=403, detail="Forbidden (403) from target site.")
    if response.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch URL (status {response.status_code}).",
        )

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    body = soup.body or soup
    text = clean_text(body.get_text(separator="\n", strip=True))
    if not text:
        raise HTTPException(status_code=400, detail="No readable job description text found at that URL.")

    return {"job_text": text}


@app.post("/extract-resume")
async def extract_resume(resume: UploadFile = File(...)):
    file_bytes = await resume.read()
    resume_text = extract_pdf_text(file_bytes)
    return {"resume_text": resume_text}


def gemini_business_fit(resume_text: str, job_description: str) -> dict:
    if not GENAI_CLIENT:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY is required for business fit analysis.",
        )
    _model = GEMINI_LITE_MODEL
    prompt = (
        "You are a senior business analyst and career strategist. Analyse how well this specific candidate's background maps "
        "to the real business problems behind this job description. Every field must reference what is actually in the CV — never generalise.\n\n"
        "Return ONLY valid JSON with exactly this structure:\n\n"
        "{\n"
        '  "company_problems": [\n'
        '    {"title": "short problem title", "description": "1-2 sentences: the real underlying business need — infer the actual pain this hire is meant to fix, not the job ad copy"}\n'
        "  ],\n"
        '  "how_cv_solves": [\n'
        '    {"problem": "matching title from company_problems", "cv_evidence": "quote or closely paraphrase the specific CV bullet, role, or achievement that addresses this problem — name the company and title it came from", "strength": "strong|partial|missing"}\n'
        "  ],\n"
        '  "cv_strengths": ["name the specific role, achievement, or CV line and explain exactly why it is an asset for this company and problem — e.g. \'Led 0-to-1 product launch at X, directly relevant to the company\'s current scaling challenge\'"],\n'
        '  "cv_gaps": ["name the exact missing experience and which business problem it leaves unaddressed — e.g. \'No enterprise sales experience: CV shows only SMB deals, but this role requires closing 6-figure contracts\'"],\n'
        '  "positioning_note": "one paragraph: what story this CV currently tells, how a recruiter or hiring manager would read it, and specifically what needs to change in framing or content to land this role"\n'
        "}\n\n"
        "Rules:\n"
        "- Do NOT use vague language. Every sentence must name something specific from the CV or JD.\n"
        "- company_problems: 3-5 items. Infer the real business need behind the job posting.\n"
        "- how_cv_solves: one entry per company_problem. If the CV is silent on a problem, use 'missing' and say so explicitly.\n"
        "- cv_strengths: 3-5 items. Each must cite a named role, company, or line. No generic compliments.\n"
        "- cv_gaps: 3-5 items. Name the exact gap, not a watered-down version of it.\n"
        "- positioning_note: be honest and specific. This is the most valuable field for the candidate.\n"
        "- Return ONLY the JSON object, no markdown fences.\n"
    )
    contents = f"{prompt}\n\nJOB DESCRIPTION:\n{job_description}\n\nCANDIDATE CV:\n{resume_text}"
    try:
        response = GENAI_CLIENT.models.generate_content(
            model=_model,
            contents=contents,
            config=gemini_generation_config(0.1),
        )
        raw = getattr(response, "text", "") or ""
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected response shape")
    except Exception as exc:
        logger.warning("Business fit analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail="Business fit analysis could not be completed. Please try again.")

    def clean_list(val, limit):
        if not isinstance(val, list):
            return []
        return [str(item).strip() for item in val if str(item).strip()][:limit]

    def clean_obj_list(val, required_keys, limit):
        if not isinstance(val, list):
            return []
        out = []
        for item in val:
            if not isinstance(item, dict):
                continue
            cleaned = {k: str(item.get(k) or "").strip() for k in required_keys}
            if any(cleaned.values()):
                out.append(cleaned)
        return out[:limit]

    return {
        "company_problems": clean_obj_list(
            parsed.get("company_problems"), ["title", "description"], 5
        ),
        "how_cv_solves": clean_obj_list(
            parsed.get("how_cv_solves"), ["problem", "cv_evidence", "strength"], 5
        ),
        "cv_strengths": clean_list(parsed.get("cv_strengths"), 5),
        "cv_gaps": clean_list(parsed.get("cv_gaps"), 5),
        "positioning_note": str(parsed.get("positioning_note") or "").strip(),
    }


def gemini_ats_keywords(job_description: str, resume_text: str) -> dict:
    """Extract all role-relevant keywords from the JD and check exact-match presence in CV."""
    if not GENAI_CLIENT:
        return {"hard_skills": [], "soft_skills": []}
    prompt = (
        "You are an ATS (Applicant Tracking System) expert. Your job is to extract EVERY keyword "
        "from the job description that a recruiter or ATS system would use to rank candidates.\n\n"
        "For each keyword:\n"
        "1. Count exactly how many times it appears in the JD (jd_count). Include all forms — singular/plural, "
        "capitalised/lowercase — but use the most prominent spelling from the JD as the keyword.\n"
        "2. Check whether the EXACT same spelling (case-insensitive) appears in the CV (cv_count).\n\n"
        "Categorise keywords into two groups:\n"
        "- hard_skills: ALL technical, domain-specific, and role-specific keywords — tools, languages, "
        "frameworks, platforms, cloud services, databases, methodologies (e.g. Agile, Scrum), certifications, "
        "industry terms, domain knowledge, qualifications, role titles used as skills, compliance standards, "
        "sector-specific terminology, and any noun or noun phrase that describes a concrete skill or knowledge area.\n"
        "- soft_skills: Behavioural, interpersonal, and leadership qualities — communication styles, "
        "management approaches, collaboration patterns (e.g. 'stakeholder management', 'cross-functional', "
        "'people management', 'strategic thinking').\n\n"
        "Be EXHAUSTIVE for hard_skills — include every meaningful keyword, not just the obvious ones. "
        "If a term appears in the JD and relates to what the role requires, include it.\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "hard_skills": [{"skill": "exact spelling from JD", "jd_count": 3, "cv_count": 1}],\n'
        '  "soft_skills": [{"skill": "exact spelling from JD", "jd_count": 2, "cv_count": 0}]\n'
        "}\n\n"
        "Rules:\n"
        "- Use the EXACT spelling and casing as it appears most often in the job description.\n"
        "- Sort each list by jd_count descending, then alphabetically for ties.\n"
        "- No artificial limit on quantity — include every relevant keyword.\n"
        "- Exclude: company name, job title of the post itself, generic filler words, salary/benefits text.\n"
        "- Return ONLY the JSON object, no markdown fences.\n"
    )
    contents = f"{prompt}\n\nJOB DESCRIPTION:\n{job_description[:4000]}\n\nCANDIDATE CV:\n{resume_text[:3000]}"
    try:
        response = GENAI_CLIENT.models.generate_content(
            model=GEMINI_REWRITE_MODEL,
            contents=contents,
            config=gemini_generation_config(0),
        )
        raw = getattr(response, "text", "") or ""
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected shape")
    except Exception as exc:
        logger.warning("ATS keyword extraction failed: %s", exc)
        return {"hard_skills": [], "soft_skills": []}

    def clean_skills(val):
        if not isinstance(val, list):
            return []
        out = []
        seen = set()
        for item in val:
            if not isinstance(item, dict):
                continue
            skill = str(item.get("skill") or "").strip()
            if not skill or skill.lower() in seen:
                continue
            seen.add(skill.lower())
            jd_count = max(1, int(item.get("jd_count") or 1))
            cv_count = max(0, int(item.get("cv_count") or 0))
            if cv_count == 0:
                status = "missing"
            elif cv_count < max(1, jd_count // 2):
                status = "low"
            else:
                status = "present"
            out.append({"skill": skill, "jd_count": jd_count, "cv_count": cv_count, "status": status})
        return out

    return {
        "hard_skills": clean_skills(parsed.get("hard_skills")),
        "soft_skills": clean_skills(parsed.get("soft_skills")),
    }


def gemini_recruiter_view(resume_text: str, job_description: str, role_fit_breakdown: dict | None = None) -> dict:
    if not GENAI_CLIENT:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY is required for recruiter view.",
        )
    _model = GEMINI_LITE_MODEL

    # Build concrete match summary from computed data
    breakdown = role_fit_breakdown or {}
    matched = breakdown.get("matched_responsibilities") or []
    missing = breakdown.get("missing_responsibilities") or []

    essential_matched = [r for r in matched if r.get("category") != "nice_to_have"]
    essential_missing = [r for r in missing if r.get("category") != "nice_to_have"]
    nice_matched = [r for r in matched if r.get("category") == "nice_to_have"]
    nice_missing = [r for r in missing if r.get("category") == "nice_to_have"]

    match_summary_lines = [
        f"Essential requirements matched: {len(essential_matched)}, missing: {len(essential_missing)}",
        f"Nice-to-have requirements matched: {len(nice_matched)}, missing: {len(nice_missing)}",
    ]
    if essential_missing:
        match_summary_lines.append("Missing essential requirements: " + "; ".join(r.get("responsibility", "") for r in essential_missing[:8]))
    if nice_missing:
        match_summary_lines.append("Missing nice-to-have requirements: " + "; ".join(r.get("responsibility", "") for r in nice_missing[:6]))
    if essential_matched:
        match_summary_lines.append("Matched essential requirements: " + "; ".join(r.get("responsibility", "") for r in essential_matched[:8]))

    match_summary = "\n".join(match_summary_lines)

    # Hard rule: determine the maximum allowed verdict from the data
    if essential_missing:
        forced_cap = "pass"  # missing essentials → cannot be shortlist or maybe
    elif nice_missing:
        forced_cap = "maybe"  # all essentials met but some nice-to-haves missing
    else:
        forced_cap = "shortlist"  # all essentials AND all nice-to-haves met

    prompt = (
        "You are a senior recruiter with 15 years of experience placing candidates into competitive roles. "
        "Assess whether this specific candidate is genuinely the right person for this specific company and role. "
        "Every sentence must be grounded in what is actually written in the CV — never speak in generalities.\n\n"
        "You have been given a computed requirements match summary. Use this as the factual basis for your verdict — "
        "do not override it with your own impression.\n\n"
        "Return ONLY valid JSON with exactly this structure:\n\n"
        "{\n"
        '  "verdict": {\n'
        f'    "decision": "shortlist|maybe|pass",\n'
        '    "reasoning": "2-3 sentences citing specific CV evidence and the matched/missing requirements above. Name actual roles, companies, or achievements."\n'
        '  },\n'
        '  "first_impression": "one sentence naming who this person actually is based on their CV — their current/most recent role, their industry, and whether that profile matches what this company is hiring for",\n'
        '  "company_fit": "2-3 sentences grounded in the CV. Reference specific industries, company types, or team sizes from their history and compare to what this company is. Do not generalise.",\n'
        '  "role_fit": "3-5 sentences of deep, personalised analysis. Name specific roles and achievements from the CV. For each key requirement in the JD, say explicitly whether the CV demonstrates it and cite the evidence — or name the gap. End with one honest verdict sentence on overall depth of fit.",\n'
        '  "quick_wins": [\n'
        '    {"action": "specific CV edit: name the exact section and what to change or add", "why": "one sentence on why this specific change increases chances with this specific company and role", "cv_section": "the exact job title, section name, or bullet this applies to"}\n'
        '  ],\n'
        '  "screening_keywords": ["keyword or phrase that a recruiter or ATS for this role would search for that is absent or buried in this CV — only include if genuinely missing"],\n'
        '  "green_flags": ["cite a specific CV line, role, or achievement that is a genuine strength for this company and role — never generic praise"],\n'
        '  "red_flags": ["a genuine fit concern grounded in what is absent or mismatched — name the exact gap vs the exact JD requirement. Empty array is fine."]\n'
        "}\n\n"
        "Rules:\n"
        "- Do NOT use vague language like 'likely', 'possibly', 'may', 'could suggest', or 'appears to'. Be direct and specific.\n"
        "- Do NOT mention employment gaps, dates, or tenure. Focus only on skill and experience fit.\n"
        f"- verdict.decision MUST be '{forced_cap}' or lower — this is enforced by the match data above. "
        "'shortlist' = ALL essential AND ALL nice-to-have requirements met. "
        "'maybe' = all essentials met but at least one nice-to-have missing. "
        "'pass' = one or more essential requirements missing.\n"
        "- role_fit: this is the most important field. Go deep. Every sentence must name something specific from the CV or JD.\n"
        "- quick_wins: 3-5 items. Each must name the exact CV section or bullet to change. Not generic advice — specific surgical edits.\n"
        "- screening_keywords: 5-8 terms, only those genuinely absent from the CV.\n"
        "- green_flags: 3-5 items, each tied to a named role, achievement, or line from the CV.\n"
        "- red_flags: 0-3 items, only genuine blockers. Empty array is fine and preferred over nitpicks.\n"
        "- Return ONLY the JSON object, no markdown fences.\n"
    )
    contents = (
        f"{prompt}\n\n"
        f"COMPUTED MATCH SUMMARY:\n{match_summary}\n\n"
        f"JOB DESCRIPTION:\n{job_description}\n\n"
        f"CANDIDATE CV:\n{resume_text}"
    )
    try:
        response = GENAI_CLIENT.models.generate_content(
            model=_model,
            contents=contents,
            config=gemini_generation_config(0.1),
        )
        raw = getattr(response, "text", "") or ""
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected response shape")
    except Exception as exc:
        logger.warning("Recruiter view failed: %s", exc)
        raise HTTPException(status_code=500, detail="Recruiter view could not be completed. Please try again.")

    def clean_list(val, limit):
        if not isinstance(val, list):
            return []
        return [str(item).strip() for item in val if str(item).strip()][:limit]

    def clean_obj_list(val, required_keys, limit):
        if not isinstance(val, list):
            return []
        out = []
        for item in val:
            if not isinstance(item, dict):
                continue
            cleaned = {k: str(item.get(k) or "").strip() for k in required_keys}
            if any(cleaned.values()):
                out.append(cleaned)
        return out[:limit]

    verdict_raw = parsed.get("verdict") or {}
    if not isinstance(verdict_raw, dict):
        verdict_raw = {}
    decision = str(verdict_raw.get("decision") or "maybe").lower().strip()
    if decision not in ("shortlist", "maybe", "pass"):
        decision = "maybe"

    # Hard override: never allow a higher verdict than the data supports
    cap_order = {"pass": 0, "maybe": 1, "shortlist": 2}
    if cap_order.get(decision, 1) > cap_order.get(forced_cap, 1):
        decision = forced_cap

    return {
        "verdict": {
            "decision": decision,
            "reasoning": str(verdict_raw.get("reasoning") or "").strip(),
        },
        "first_impression": str(parsed.get("first_impression") or "").strip(),
        "company_fit": str(parsed.get("company_fit") or "").strip(),
        "role_fit": str(parsed.get("role_fit") or "").strip(),
        "quick_wins": clean_obj_list(parsed.get("quick_wins"), ["action", "why", "cv_section"], 5),
        "screening_keywords": clean_list(parsed.get("screening_keywords"), 8),
        "green_flags": clean_list(parsed.get("green_flags"), 5),
        "red_flags": clean_list(parsed.get("red_flags"), 3),
    }


@app.post("/business-fit")
async def business_fit(payload: dict):
    resume_text = clean_text(str((payload or {}).get("resume_text") or ""))
    job_description = clean_text(str((payload or {}).get("job_description") or ""))
    if not resume_text:
        raise HTTPException(status_code=400, detail="Missing resume_text.")
    if not job_description:
        raise HTTPException(status_code=400, detail="Missing job_description.")
    result = gemini_business_fit(resume_text, job_description)
    return {"business_fit": result}


@app.post("/recruiter-view")
async def recruiter_view(payload: dict):
    resume_text = clean_text(str((payload or {}).get("resume_text") or ""))
    job_description = clean_text(str((payload or {}).get("job_description") or ""))
    role_fit_breakdown = (payload or {}).get("role_fit_breakdown") or {}
    if not resume_text:
        raise HTTPException(status_code=400, detail="Missing resume_text.")
    if not job_description:
        raise HTTPException(status_code=400, detail="Missing job_description.")
    result = gemini_recruiter_view(resume_text, job_description, role_fit_breakdown)
    return {"recruiter_view": result}


def gemini_interview_prep(resume_text: str, job_description: str, role_fit_breakdown: dict | None = None) -> dict:
    if not GENAI_CLIENT:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is required for interview prep.")

    breakdown = role_fit_breakdown or {}
    matched = breakdown.get("matched_responsibilities") or []
    missing = breakdown.get("missing_responsibilities") or []

    match_lines = []
    if matched:
        match_lines.append("Matched requirements: " + "; ".join(r.get("responsibility", "") for r in matched[:8]))
    if missing:
        match_lines.append("Missing/weak requirements: " + "; ".join(r.get("responsibility", "") for r in missing[:8]))
    match_summary = "\n".join(match_lines)

    prompt = (
        "You are an expert interview coach helping a candidate prepare for a specific job interview. "
        "Based on the candidate's CV and the job description, generate targeted interview questions they are likely to face. "
        "Every question must be grounded in the actual CV content or JD requirements — no generic questions.\n\n"
        "Return ONLY valid JSON with exactly this structure:\n\n"
        "{\n"
        '  "role_questions": [\n'
        '    {"question": "a technical or role-specific question tied to this JD", "why_asked": "one sentence: what the interviewer is trying to evaluate", "tip": "one sentence: how to frame your answer for this specific role"}\n'
        "  ],\n"
        '  "behavioral": [\n'
        '    {"question": "a behavioural/situational question tied to this role\'s demands", "competency": "the competency being tested e.g. Leadership", "star_hint": "one sentence: what specific experience from this CV to structure a STAR answer around"}\n'
        "  ],\n"
        '  "cv_deep_dive": [\n'
        '    {"question": "a question drilling into a specific CV claim, achievement, or role — closely reference the actual CV line", "cv_reference": "the exact CV item this is based on", "tip": "how to answer without underselling or overpromising"}\n'
        "  ],\n"
        '  "gap_challenges": [\n'
        '    {"question": "a tough question the interviewer will ask because of a gap or mismatch vs this JD", "gap": "the specific gap this challenges", "how_to_handle": "concrete strategy to address this gap honestly and confidently"}\n'
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- role_questions: 4-5 items. Specific to this role's industry, function, and technical requirements.\n"
        "- behavioral: 3-4 items. Each tied to a real demand from this JD.\n"
        "- cv_deep_dive: 3-4 items. Quote or closely reference actual lines from the CV — name roles, companies, achievements.\n"
        "- gap_challenges: 2-3 items. Only real gaps visible from CV vs JD mismatch. Omit section if no clear gaps.\n"
        "- Every field must be specific to THIS candidate and THIS role. No generic interview advice.\n"
        "- Return ONLY the JSON object, no markdown fences.\n"
    )

    contents = (
        f"{prompt}\n\n"
        f"REQUIREMENTS MATCH SUMMARY:\n{match_summary}\n\n"
        f"JOB DESCRIPTION:\n{job_description}\n\n"
        f"CANDIDATE CV:\n{resume_text}"
    )

    try:
        response = GENAI_CLIENT.models.generate_content(
            model=GEMINI_LITE_MODEL,
            contents=contents,
            config=gemini_generation_config(0.2),
        )
        raw = getattr(response, "text", "") or ""
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected response shape")
    except Exception as exc:
        logger.warning("Interview prep failed: %s", exc)
        raise HTTPException(status_code=500, detail="Interview prep could not be completed. Please try again.")

    def clean_obj_list(val, required_keys, limit):
        if not isinstance(val, list):
            return []
        out = []
        for item in val:
            if not isinstance(item, dict):
                continue
            cleaned = {k: str(item.get(k) or "").strip() for k in required_keys}
            if any(cleaned.values()):
                out.append(cleaned)
        return out[:limit]

    return {
        "role_questions": clean_obj_list(parsed.get("role_questions"), ["question", "why_asked", "tip"], 5),
        "behavioral": clean_obj_list(parsed.get("behavioral"), ["question", "competency", "star_hint"], 4),
        "cv_deep_dive": clean_obj_list(parsed.get("cv_deep_dive"), ["question", "cv_reference", "tip"], 4),
        "gap_challenges": clean_obj_list(parsed.get("gap_challenges"), ["question", "gap", "how_to_handle"], 3),
    }


@app.post("/interview-prep")
async def interview_prep(payload: dict):
    resume_text = clean_text(str((payload or {}).get("resume_text") or ""))
    job_description = clean_text(str((payload or {}).get("job_description") or ""))
    role_fit_breakdown = (payload or {}).get("role_fit_breakdown") or {}
    if not resume_text:
        raise HTTPException(status_code=400, detail="Missing resume_text.")
    if not job_description:
        raise HTTPException(status_code=400, detail="Missing job_description.")
    result = gemini_interview_prep(resume_text, job_description, role_fit_breakdown)
    return {"interview_prep": result}


def fetch_company_news(company_name: str, max_articles: int = 6) -> list:
    url = f"https://news.google.com/rss/search?q={quote_plus(company_name)}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        articles = []
        for item in root.findall(".//item")[:max_articles]:
            raw_title = item.findtext("title") or ""
            source_el = item.find("source")
            source = source_el.text.strip() if source_el is not None and source_el.text else ""
            title = re.sub(r"\s*[-–]\s*" + re.escape(source) + r"\s*$", "", raw_title).strip() if source else raw_title.strip()
            articles.append({
                "title": title,
                "url": (item.findtext("link") or "").strip(),
                "source": source,
                "pub_date": (item.findtext("pubDate") or "").strip(),
            })
        return articles
    except Exception as exc:
        logger.warning("News fetch failed for %s: %s", company_name, exc)
        return []


def extract_company_name(job_description: str) -> str:
    if not GENAI_CLIENT:
        m = re.search(r'\bAbout\s+([A-Z][A-Za-z0-9\s&\.,]+?)(?:\n|\.)', job_description)
        return m.group(1).strip()[:80] if m else ""
    try:
        response = GENAI_CLIENT.models.generate_content(
            model=GEMINI_PARSE_MODEL,
            contents=(
                "Extract only the company name from this job description. "
                "Return just the company name as a plain string, nothing else. "
                "If you cannot determine it, return an empty string.\n\n"
                f"{job_description[:2000]}"
            ),
            config=gemini_generation_config(0),
        )
        return (getattr(response, "text", "") or "").strip().strip('"\'')[:80]
    except Exception:
        return ""


def gemini_company_insights(company_name: str, job_description: str) -> dict:
    if not GENAI_CLIENT:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is required for company insights.")

    def clean_list(val, limit):
        if not isinstance(val, list):
            return []
        cleaned = [re.sub(r'\s*\[cite:[^\]]*\]|\s*\[\d+\]', '', str(item)).strip() for item in val]
        return [s for s in cleaned if s][:limit]

    prompt = (
        f"You are helping a job candidate prepare their application for a role at {company_name}. "
        f"Search the web for '{company_name}' — check their official site, LinkedIn, Crunchbase, press releases, "
        "recent news, and funding announcements.\n\n"
        "Use what you find to return ONLY valid JSON with exactly this structure:\n\n"
        "{\n"
        '  "why_hiring_now": {\n'
        '    "reason": "1-2 sentences: what specific business event or need is driving this hire — '
        'e.g. Series B in Jan 2025 to scale engineering, launched new product in Q1 requiring support, expanding into EU market. '
        'If you found a concrete news event, lead with it. If you can only infer from the JD, say so explicitly.",\n'
        '    "confidence": "high | medium | low",\n'
        '    "source": "what you based this on — e.g. \'TechCrunch funding article March 2025\', \'LinkedIn headcount growth\', '
        '\'inferred from JD language only\'"\n'
        "  },\n"
        '  "company_momentum": [\n'
        "    {\n"
        '      "fact": "the specific event — include real numbers, names, or metrics where possible. e.g. \'Raised $40M Series B led by Accel, March 2025\' or \'Headcount grew from 120 to 340 employees over 12 months\'",\n'
        '      "date": "when this happened — e.g. \'March 2025\', \'Q1 2025\', \'2024\'. Leave empty string if unknown.",\n'
        '      "source": "where you found this — e.g. \'TechCrunch\', \'LinkedIn\', \'company blog\', \'Crunchbase\'. Leave empty string if unclear.",\n'
        '      "candidate_relevance": "one sentence: why this matters to someone applying for this role right now"\n'
        "    }\n"
        "  ],\n"
        '  "current_focus": ["specific real thing this company is actively working on that is directly relevant to this role — '
        'e.g. \'Rebuilding their data pipeline after migrating from Snowflake to BigQuery\', '
        '\'Rolling out a new B2B product to enterprise clients in the UK\', '
        '\'Scaling their ML infrastructure to support real-time recommendations\'. '
        'Find these from job postings, engineering blogs, product announcements, LinkedIn updates, or press releases. '
        'Each bullet should be concrete enough that a candidate could reference it in a cover letter."],\n'
        '  "watch_outs": ["honest signals a candidate should know — leadership changes, layoffs, press coverage concerns, '
        'high turnover signals, recent pivots. Omit if nothing found."],\n'
        '  "apply_intel": ["specific actionable insight for tailoring this application or acing an interview at this company"]\n'
        "}\n\n"
        "Rules:\n"
        f"- Everything must be specific to {company_name} — no generic advice.\n"
        "- why_hiring_now.confidence: 'high' only if you found a concrete news event. 'medium' if you found signals. 'low' if JD inference only.\n"
        "- company_momentum: 2-4 items, each a real verifiable event. Prioritise the most recent and most relevant to this role. Do NOT include items you cannot verify.\n"
        "- current_focus: 3-5 bullets, each specific enough to reference in a cover letter. Prioritise things tied to the role's function. No generic statements like 'growing fast' or 'scaling the team'.\n"
        "- watch_outs: 1-3 items if genuinely found. Return empty array otherwise.\n"
        "- apply_intel: 2-4 items specific to this company and role.\n"
        "- Return ONLY the JSON object, no markdown fences.\n"
    )
    contents = f"{prompt}\n\nCOMPANY: {company_name}\n\nJOB DESCRIPTION:\n{job_description}"

    grounded_ok = False
    raw = ""
    if types is not None:
        try:
            grounded_response = GENAI_CLIENT.models.generate_content(
                model=GEMINI_REWRITE_MODEL,
                contents=contents,
                config=gemini_generation_config(
                    0.2,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )
            raw = getattr(grounded_response, "text", "") or ""
            if raw.strip():
                grounded_ok = True
        except Exception as exc:
            logger.warning("Grounded company insights failed, falling back: %s", exc)

    if not grounded_ok:
        try:
            fallback_response = GENAI_CLIENT.models.generate_content(
                model=GEMINI_REWRITE_MODEL,
                contents=contents,
                config=gemini_generation_config(0.2),
            )
            raw = getattr(fallback_response, "text", "") or ""
        except Exception as exc:
            logger.warning("Company insights fallback also failed: %s", exc)
            raise HTTPException(status_code=500, detail="Company insights could not be completed.")

    parsed = parse_json_response(raw)
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=500, detail="Company insights returned unexpected output.")

    def strip_citations(text: str) -> str:
        # Remove Gemini grounding citation markers like [cite: JD], [1], [cite: 2], etc.
        return re.sub(r'\s*\[cite:[^\]]*\]|\s*\[\d+\]', '', str(text)).strip()

    raw_why = parsed.get("why_hiring_now") or {}
    if isinstance(raw_why, str):
        raw_why = {"reason": raw_why, "confidence": "low", "source": ""}
    why_hiring_now = {
        "reason": strip_citations(raw_why.get("reason") or ""),
        "confidence": str(raw_why.get("confidence") or "low").strip().lower(),
    }

    raw_momentum = parsed.get("company_momentum") or []
    if not isinstance(raw_momentum, list):
        raw_momentum = []
    company_momentum = []
    for item in raw_momentum[:4]:
        if isinstance(item, str):
            fact = strip_citations(item)
            if fact:
                company_momentum.append({"fact": fact, "date": "", "candidate_relevance": ""})
        elif isinstance(item, dict):
            fact = strip_citations(item.get("fact") or "")
            if fact:
                company_momentum.append({
                    "fact": fact,
                    "date": strip_citations(item.get("date") or ""),
                    "candidate_relevance": strip_citations(item.get("candidate_relevance") or ""),
                })

    return {
        "company_name": company_name,
        "why_hiring_now": why_hiring_now,
        "company_momentum": company_momentum,
        "current_focus": clean_list(parsed.get("current_focus"), 5),
        "watch_outs": clean_list(parsed.get("watch_outs"), 3),
        "apply_intel": clean_list(parsed.get("apply_intel"), 4),
        "grounded": grounded_ok,
    }


@app.post("/company-insights")
async def company_insights(payload: dict, authorization: Optional[str] = Header(None)):
    # Gate behind tier. Free-tier users get a structured "locked" response so the
    # frontend can render an upgrade CTA without a 4xx.
    token = auth_utils.extract_bearer_token(authorization)
    decoded = auth_utils.decode_jwt(token) if token else None
    if not decoded or "sub" not in decoded:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        user = db.get_user_by_id(int(decoded["sub"]))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid session.")
    if not user:
        raise HTTPException(status_code=401, detail="Session no longer valid.")
    if (user.get("tier") or "free") != "paid":
        return {
            "company_insights": None,
            "locked": True,
            "upgrade_message": "Company research is available on the full plan. Email gptc2903@gmail.com to upgrade.",
        }

    job_description = clean_text(str((payload or {}).get("job_description") or ""))
    if not job_description:
        raise HTTPException(status_code=400, detail="Missing job_description.")
    if not GENAI_CLIENT:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is required for company insights.")
    company_name = extract_company_name(job_description)
    if not company_name:
        raise HTTPException(status_code=422, detail="Could not identify company name from job description.")
    result = gemini_company_insights(company_name, job_description)
    return {"company_insights": result, "locked": False}
