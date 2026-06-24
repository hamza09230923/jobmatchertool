from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import math
import os
import re
import random
import secrets
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from contextvars import ContextVar
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

_REQUEST_ANALYSIS_CACHE: ContextVar[dict | None] = ContextVar(
    "_REQUEST_ANALYSIS_CACHE",
    default=None,
)


@app.middleware("http")
async def request_analysis_cache_middleware(request, call_next):
    token = _REQUEST_ANALYSIS_CACHE.set({})
    try:
        return await call_next(request)
    finally:
        _REQUEST_ANALYSIS_CACHE.reset(token)


def _request_cache_bucket(name: str) -> dict | None:
    cache = _REQUEST_ANALYSIS_CACHE.get()
    if not isinstance(cache, dict):
        return None
    return cache.setdefault(name, {})


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
GEMINI_PARSE_MODEL = os.getenv("GEMINI_PARSE_MODEL", "gemini-2.5-flash-lite")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
GEMINI_REWRITE_MODEL = os.getenv("GEMINI_REWRITE_MODEL", "gemini-2.5-flash-lite")
GEMINI_LITE_MODEL = os.getenv("GEMINI_LITE_MODEL", "gemini-2.5-flash-lite")
try:
    GEMINI_SEED = int(os.getenv("GEMINI_SEED", "1337"))
except ValueError:
    GEMINI_SEED = 1337
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_REWRITE_MODEL = os.getenv("OPENAI_REWRITE_MODEL", "gpt-5-mini")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


ANALYZE_CORE_TIMEOUT_SECONDS = _env_int("ANALYZE_CORE_TIMEOUT_SECONDS", 100)
ANALYZE_OPTIONAL_TIMEOUT_SECONDS = _env_int("ANALYZE_OPTIONAL_TIMEOUT_SECONDS", 8)
# Per-call HTTP timeout + transient-error retry. Without this the google-genai SDK
# can block a single request for minutes, hanging the whole /analyze pipeline until
# the client aborts (504/timeout in production). Tunable via env on Render.
GEMINI_HTTP_TIMEOUT_MS = _env_int("GEMINI_HTTP_TIMEOUT_MS", 40000)
# Total attempts (incl. the first), matching Google's recommended retry strategy:
# ~1, 2, 4, 8s exponential backoff with jitter, so transient 429/5xx demand spikes
# (e.g. 503 UNAVAILABLE) are ridden out instead of surfacing to the user. Only
# server error codes retry; client-side timeouts do not, so this can't cause long hangs.
GEMINI_HTTP_RETRIES = _env_int("GEMINI_HTTP_RETRIES", 5)


def _genai_http_options():
    # Only the per-call timeout is set here. Transient-error retries are handled at
    # the application level (see _genai_generate / _genai_embed) instead of via the
    # SDK's HttpRetryOptions: requirements.txt does not pin google-genai, so the
    # deployed SDK version may not honor retry_options, and we don't want the two
    # mechanisms compounding into 25 nested attempts during a real outage.
    return types.HttpOptions(timeout=GEMINI_HTTP_TIMEOUT_MS)


GENAI_CLIENT = (
    genai.Client(api_key=GEMINI_API_KEY, http_options=_genai_http_options())
    if genai is not None and GEMINI_API_KEY
    else None
)
SCORER_VERSION = analysis_cache.SCORER_VERSION
ANALYZE_CACHE_MAX_ENTRIES = analysis_cache.ANALYZE_CACHE_MAX_ENTRIES
_secondary_compute_locks: dict[str, threading.Lock] = {}
_secondary_compute_locks_guard = threading.Lock()


def gemini_generation_config(temperature: float = 0.0, **kwargs):
    config_kwargs = {
        "temperature": temperature,
        "seed": GEMINI_SEED,
        "candidate_count": 1,
    }
    config_kwargs.update(kwargs)
    return types.GenerateContentConfig(**config_kwargs)


# Markers for transient AI-provider overload/availability errors that are safe to
# retry (Gemini reports momentary capacity issues as 503 UNAVAILABLE / "high demand"
# and rate spikes as 429 RESOURCE_EXHAUSTED). Kept in sync with _ai_overload_message.
_GEMINI_TRANSIENT_MARKERS = (
    "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "429",
    "HIGH DEMAND", "OVERLOAD", "500", "502", "504",
)


def _is_transient_gemini_error(exc: Exception) -> bool:
    text = str(exc).upper()
    return any(marker in text for marker in _GEMINI_TRANSIENT_MARKERS)


def _genai_retry(call, what: str):
    """Run a Gemini SDK call with explicit exponential-backoff retry on transient
    overload/availability errors. Version-independent (does not rely on the SDK's
    HttpRetryOptions). Non-transient errors are re-raised immediately; the final
    transient error is re-raised after the attempt budget is exhausted."""
    attempts = max(1, GEMINI_HTTP_RETRIES)
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            if attempt == attempts - 1 or not _is_transient_gemini_error(exc):
                raise
            delay = min(8.0, 2.0 ** attempt) + random.uniform(0, 0.75)
            logger.warning(
                "Gemini %s transient error (attempt %d/%d), retrying in %.1fs: %s",
                what, attempt + 1, attempts, delay, str(exc)[:140],
            )
            time.sleep(delay)


def _genai_generate(**kwargs):
    """GENAI_CLIENT.models.generate_content with application-level transient retry."""
    return _genai_retry(lambda: GENAI_CLIENT.models.generate_content(**kwargs), "generate_content")


def _genai_embed(**kwargs):
    """GENAI_CLIENT.models.embed_content with application-level transient retry."""
    return _genai_retry(lambda: GENAI_CLIENT.models.embed_content(**kwargs), "embed_content")


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
RESPONSIBILITY_MATCH_WEIGHT = 0.70
EXPERIENCE_MATCH_WEIGHT = 0.10
SKILLS_MATCH_WEIGHT = 0.10
SEMANTIC_MATCH_WEIGHT = 0.10
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

NATURAL_LANGUAGE_SKILLS = (
    "english",
    "spanish",
    "french",
    "german",
    "italian",
    "polish",
    "portuguese",
    "mandarin",
    "arabic",
    "hindi",
    "japanese",
    "korean",
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
    "communication skills",
    "teamwork",
    "team player",
    "leadership",
    "problem solving",
    "problem-solving",
    "adaptability",
    "time management",
    "time-management",
    "stakeholder management",
    "stakeholder collaboration",
    "collaboration",
    "ethical judgement",
    "professional attitude",
    "work ethic",
    "self-motivated",
    "deadline management",
    "multitasking",
)
TECH_SKILL_ALIASES = {
    "microsoft excel": ("microsoft excel", "excel"),
    "excel": ("excel", "microsoft excel"),
    "microsoft word": ("microsoft word", "word"),
    "word": ("word", "microsoft word"),
    "microsoft outlook": ("microsoft outlook", "outlook"),
    "outlook": ("outlook", "microsoft outlook"),
    "python": ("python", "python3"),
    "rust": ("rust",),
    "docker": ("docker",),
    "kubernetes": ("kubernetes", "k8s"),
    "grpc": ("grpc", "gRPC"),
    "rest": ("rest", "restful", "rest api", "rest apis"),
    "microservices": ("microservices", "microservice"),
    "react": ("react", "react.js", "reactjs"),
    "typescript": ("typescript", "type script", "ts"),
    "javascript": ("javascript", "java script", "js"),
    "c++": ("c++", "cpp", "c plus plus"),
}

# Narrow, explicit equivalences for named tools. Do not add broad capabilities here.
EXACT_ALIAS_REGISTRY = {
    "ci_cd": (
        "ci/cd", "ci cd", "ci-cd", "cicd",
        "continuous integration/deployment",
        "continuous integration and deployment",
        "continuous integration and continuous deployment",
    ),
    "node_js": ("node", "node.js", "node js", "nodejs"),
    "c_sharp": ("c#", "c sharp", "csharp"),
    "dotnet": (".net", "dotnet", "dot net"),
    "javascript": ("javascript", "java script", "js"),
}


def exact_alias_key(text: str) -> str | None:
    norm = normalize_phrase(text)
    for canonical, aliases in EXACT_ALIAS_REGISTRY.items():
        if norm in {normalize_phrase(alias) for alias in aliases}:
            return canonical
    return None


def exact_aliases(text: str) -> List[str]:
    canonical = exact_alias_key(text)
    if not canonical:
        return []
    return list(EXACT_ALIAS_REGISTRY[canonical])


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

CANONICAL_CAPABILITY_TAXONOMY = {
    "cloud_platform": {
        "aliases": ("cloud", "cloud platform", "cloud platforms"),
        "evidence_signals": (
            "aws", "amazon web services", "azure", "gcp", "google cloud",
            "aws lambda", "aws rds", "dynamodb", "step functions",
        ),
    },
    "data_engineering": {
        "aliases": (
            "data engineering", "data pipeline", "data pipelines", "data workflow",
            "data workflows", "etl", "elt",
        ),
        "evidence_signals": (
            "data engineering", "data pipeline", "data pipelines", "etl", "elt",
            "data ingestion", "data flow", "data flows", "batch", "streaming",
        ),
    },
    "ai_development": {
        "aliases": (
            "artificial intelligence", "machine learning", "ai feature", "ai features",
            "ml feature", "ml features", "ai model", "ai models", "ml model", "ml models",
            "ai development", "ai development techniques", "applied ai",
        ),
        "evidence_signals": (
            "artificial intelligence", "machine learning", "ml pipeline", "feature engineering",
            "scikit learn", "xgboost", "finbert", "reinforcement learning", "predictive model",
            "forecasting model", "ai powered", "gemini embedding", "gemini embeddings",
        ),
    },
    "software_development": {
        "aliases": (
            "coding", "scripting", "software development", "application development",
        ),
        "evidence_signals": (
            "software development", "full stack", "full stack platform", "backend service",
            "backend services", "frontend application", "application", "python", "javascript",
            "node js", "fastapi",
        ),
    },
    "automation": {
        "aliases": (
            "automation", "workflow automation", "process automation", "automated workflow",
            "automated workflows",
        ),
        "evidence_signals": (
            "automation", "automated", "workflow automation", "automated workflow",
            "automated workflows", "process automation", "serverless workflow",
        ),
    },
    "application_deployment": {
        "aliases": (
            "application deployment", "deployment", "deploy applications", "deploy solutions",
            "deployment activities",
        ),
        "evidence_signals": (
            "deployed", "deployment", "docker", "github actions", "ci cd", "aws ecr",
            "containerised", "containerized",
        ),
    },
    "application_monitoring": {
        "aliases": ("application monitoring", "platform monitoring", "system monitoring"),
        "evidence_signals": (
            "application monitoring", "platform monitoring", "system monitoring",
            "performance monitoring",
        ),
    },
    "backend_engineering": {
        "aliases": (
            "backend system", "backend systems", "backend service", "backend services",
            "api", "apis", "microservice", "microservices",
        ),
        "evidence_signals": (
            "backend system", "backend systems", "backend service", "backend services",
            "api", "apis", "rest api", "restful", "fastapi", "microservice", "microservices",
        ),
    },
    "data_warehousing": {
        "aliases": ("data warehouse", "data warehouses", "data warehousing"),
        "evidence_signals": ("snowflake", "redshift", "bigquery", "data warehouse", "data warehouses", "lakehouse"),
    },
}

CAPABILITY_EVIDENCE_GROUPS = tuple(
    {
        "concept": concept,
        "targets": definition["aliases"],
        "signals": definition["evidence_signals"],
    }
    for concept, definition in CANONICAL_CAPABILITY_TAXONOMY.items()
)

# Reusable evidence dimensions for abstract capabilities. These classify evidence;
# they never override exact-tool or formal-experience verification.
EVIDENCE_SIGNAL_FAMILIES = {
    "software_delivery": (
        "built", "developed", "implemented", "delivered", "deployed", "shipped",
        "application", "applications", "platform", "feature", "features",
    ),
    "deployment": (
        "deployment", "deployed", "docker", "containerised", "containerized",
        "github actions", "ci cd", "aws ecr", "terraform",
    ),
    "version_control": ("git", "github", "repository", "repositories", "branch", "commit"),
    "testing_quality": (
        "testing", "tested", "tests", "unit test", "integration test", "vitest",
        "pytest", "testing library", "linting", "eslint", "code review", "code reviews",
    ),
    "automation": ("automation", "automated", "workflow automation", "automated workflows"),
    "cloud": ("aws", "azure", "gcp", "cloud", "lambda", "rds", "dynamodb"),
    "collaboration": (
        "collaborated", "collaboration", "team", "teams", "agile", "stakeholder",
        "stakeholders", "sprint", "stand up", "stand ups", "retrospective", "retrospectives",
    ),
    "delivery_management": (
        "delivered", "delivery", "deadline", "deadlines", "on time", "requirements",
        "prioritised", "prioritized", "roadmap",
    ),
    "learning": (
        "learned", "learning", "training", "coursework", "certification",
        "certifications", "upskilling", "self directed",
    ),
}

CAPABILITY_POLICY_HINTS = {
    "devops": ("deployment", "version_control", "automation", "cloud"),
    "modern development": ("software_delivery", "version_control", "testing_quality", "deployment"),
    "software engineering practices": ("software_delivery", "version_control", "testing_quality", "deployment"),
    "collaborative delivery": ("collaboration", "delivery_management"),
    "team based environment": ("collaboration",),
    "technical delivery": ("software_delivery", "delivery_management"),
    "code quality": ("testing_quality", "version_control"),
    "clean code": ("testing_quality", "version_control"),
}

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
    tokens = [t for t in phrase_norm.split() if t]
    if len(tokens) == 1 and tokens[0] in resume_token_set:
        return True
    if len(tokens) == 1:
        return False
    if phrase_norm in resume_text_norm:
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
    try:
        parsed = gemini_parse_resume(resume_text)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected Gemini resume parse shape")
        jobs = parsed.get("work_experience") or []
        parsed["employment_gaps"] = compute_employment_gaps_from_jobs(jobs)
        if debug_info is not None:
            debug_info["parse_method"] = "gemini"
        return parsed
    except Exception as exc:
        logger.warning("Gemini parse failed; API-backed analysis is required: %s", exc)
        if debug_info is not None:
            debug_info["parse_error"] = str(exc)
        raise

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
        "- For quantified_achievements: copy verbatim every bullet from work_experience or projects that contains any number, percentage, currency symbol, or measurable metric.\n"
        "- For management_experience: set has_managed=true if any role mentions managing, leading, or mentoring a team; set max_team_size to the largest team size mentioned.\n"
        "- For industry_domains: list the industries/sectors the candidate has worked in based on company descriptions and role context.\n"
        "- Return ONLY the JSON object, no markdown fences, no extra commentary."
    )
    response = _genai_generate(
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

        response = _genai_generate(
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
                "original_text": str(item.get("original_text") or "").strip(),
                "rewritten_text": str(item.get("rewritten_text") or "").strip(),
                "evidence_source": str(item.get("evidence_source") or "").strip(),
            }
            for item in (payload.get("section_changes") or [])
            if isinstance(item, dict) and str(item.get("change") or "").strip()
        ][:12],
    }
    return normalized


REWRITE_NUMBER_RE = re.compile(r"(?:£|\$|€)?\b\d+(?:[.,]\d+)?\+?%?\b")


def _rewrite_strict_terms() -> List[str]:
    terms: List[str] = []
    terms.extend(STRICT_TOOL_TERMS)
    terms.extend(REGULATED_PROCESS_TERMS)
    terms.extend(CONTROL_GOVERNANCE_TERMS)
    terms.extend(REPORTING_STRICT_TERMS)
    terms.extend(AUDIT_STRICT_TERMS)
    terms.extend(
        (
            "certified",
            "certification",
            "licence",
            "license",
            "aca",
            "acca",
            "cima",
            "qts",
            "team management",
            "people management",
            "line management",
            "managed team",
            "managed a team",
            "supervised",
            "mentored",
            "hired",
        )
    )
    return merge_unique([term for term in terms if str(term).strip()])


def _numbers_from_text(text: str) -> set[str]:
    return {match.group(0).lower().strip() for match in REWRITE_NUMBER_RE.finditer(str(text or ""))}


def _strip_metric_prompt(text: str) -> str:
    return re.sub(r"\[METRIC:\s*[^\]]+\]", "", str(text or ""), flags=re.IGNORECASE).strip()


def _missing_strict_generated_terms(text: str, resume_text: str) -> List[str]:
    text_norm = normalize_phrase(text)
    resume_norm = normalize_phrase(resume_text)
    if not text_norm:
        return []
    missing: List[str] = []
    for term in _rewrite_strict_terms():
        term_norm = normalize_phrase(term)
        if not term_norm:
            continue
        if not _phrase_present_in_normalized_text(term_norm, text_norm):
            continue
        aliases = _requirement_aliases(term)
        if any(_phrase_present_in_normalized_text(normalize_phrase(alias), resume_norm) for alias in aliases):
            continue
        missing.append(_display_ats_keyword(term))
    return merge_unique(missing)


def _invented_numbers_in_text(text: str, resume_text: str) -> List[str]:
    text_without_prompts = _strip_metric_prompt(text)
    resume_numbers = _numbers_from_text(resume_text)
    invented = []
    for number in _numbers_from_text(text_without_prompts):
        if number not in resume_numbers:
            invented.append(number)
    return merge_unique(invented)


def validate_rewrite_no_inventions(rewrite: dict, resume_text: str) -> dict:
    """Deterministically remove generated claims with exact tools/metrics absent from the source CV."""
    if not isinstance(rewrite, dict):
        return rewrite

    additional = [
        str(item).strip()
        for item in (rewrite.get("additional_keywords_to_include") or [])
        if str(item).strip()
    ]
    missing_info = [
        str(item).strip()
        for item in (rewrite.get("missing_information") or [])
        if str(item).strip()
    ]
    removed_notes: List[str] = []

    def record_removed(text: str, reasons: List[str]) -> None:
        reason_text = ", ".join(merge_unique([r for r in reasons if r]))
        removed_notes.append(f"{text}" + (f" ({reason_text})" if reason_text else ""))
        note = f"{text} (add only if accurate)"
        if normalize_phrase(note) not in {normalize_phrase(item) for item in additional}:
            additional.append(note)

    def unsupported_reasons(text: str) -> List[str]:
        reasons = []
        missing_terms = _missing_strict_generated_terms(text, resume_text)
        if missing_terms:
            reasons.append("unsupported exact terms: " + ", ".join(missing_terms[:5]))
        invented_numbers = _invented_numbers_in_text(text, resume_text)
        if invented_numbers:
            reasons.append("invented metrics: " + ", ".join(invented_numbers[:5]))
        return reasons

    for section_key in ("experience_section", "projects_section"):
        cleaned_items = []
        for item in rewrite.get(section_key) or []:
            if not isinstance(item, dict):
                continue
            bullets = []
            for bullet in item.get("bullets") or []:
                bullet_text = str(bullet).strip()
                if not bullet_text:
                    continue
                reasons = unsupported_reasons(bullet_text)
                if reasons:
                    record_removed(bullet_text, reasons)
                    continue
                bullets.append(bullet_text)
            heading = str(item.get("heading") or "").strip()
            if heading or bullets:
                cleaned_items.append({"heading": heading, "bullets": bullets[:8]})
        rewrite[section_key] = cleaned_items

    validated_skill_sections = []
    for section in rewrite.get("skills_section") or []:
        if not isinstance(section, dict):
            continue
        kept_skills = []
        for skill in section.get("items") or []:
            skill_text = str(skill).strip()
            if not skill_text:
                continue
            reasons = unsupported_reasons(skill_text)
            if reasons:
                record_removed(skill_text, reasons)
                continue
            kept_skills.append(skill_text)
        if kept_skills:
            validated_skill_sections.append({
                "category": str(section.get("category") or "").strip(),
                "items": merge_unique(kept_skills),
            })
    if "skills_section" in rewrite:
        rewrite["skills_section"] = validated_skill_sections

    summary = str(rewrite.get("rewritten_summary") or "").strip()
    if summary:
        sentences = re.split(r"(?<=[.!?])\s+", summary)
        kept_sentences = []
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            reasons = unsupported_reasons(sentence)
            if reasons:
                record_removed(sentence, reasons)
                continue
            kept_sentences.append(sentence)
        rewrite["rewritten_summary"] = " ".join(kept_sentences).strip()

    rewrite["additional_keywords_to_include"] = additional[:15]
    if removed_notes:
        missing_info.append(
            "Removed unsupported generated claims after deterministic validation: "
            + "; ".join(removed_notes[:8])
        )
    rewrite["missing_information"] = missing_info[:12]
    rewrite.setdefault("rewrite_audit", {})["deterministic_removed_count"] = len(removed_notes)
    return rewrite


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
        response = _genai_generate(
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

    def should_remove_text(text: str) -> bool:
        text_norm = normalize_phrase(text)
        if not text_norm:
            return False
        return any(claim == text_norm or claim in text_norm for claim in remove_claims)

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

        for section_key in ("experience_section", "projects_section"):
            cleaned_items = []
            for item in rewrite.get(section_key) or []:
                if not isinstance(item, dict):
                    continue
                bullets = []
                for bullet in item.get("bullets") or []:
                    bullet_text = str(bullet).strip()
                    if should_remove_text(bullet_text):
                        removed.append(bullet_text)
                        continue
                    if bullet_text:
                        bullets.append(bullet_text)
                heading = str(item.get("heading") or "").strip()
                if heading or bullets:
                    cleaned_items.append({"heading": heading, "bullets": bullets[:8]})
            rewrite[section_key] = cleaned_items

        summary = str(rewrite.get("rewritten_summary") or "").strip()
        if summary and should_remove_text(summary):
            removed.append(summary)
            rewrite["rewritten_summary"] = ""

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
    rewrite = validate_rewrite_no_inventions(rewrite, resume_text)
    audit = gemini_lite_audit_rewrite(rewrite, resume_text, job_description)
    rewrite = apply_rewrite_audit(rewrite, audit)
    rewrite = validate_rewrite_no_inventions(rewrite, resume_text)
    deterministic_removed = (
        (rewrite.get("rewrite_audit") or {}).get("deterministic_removed_count")
        if isinstance(rewrite.get("rewrite_audit"), dict)
        else 0
    )
    rewrite["rewrite_audit"] = {
        "enabled": bool(GENAI_CLIENT),
        "model": GEMINI_LITE_MODEL,
        "unsupported_count": len(audit.get("unsupported_claims") or []) if isinstance(audit, dict) else 0,
        "deterministic_removed_count": int(deterministic_removed or 0),
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


def _truncate_text(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0].strip() + "\n[truncated]"


def _compact_role_fit_for_rewrite(role_fit_breakdown: dict | None) -> dict:
    breakdown = role_fit_breakdown or {}
    skills_detail = breakdown.get("skills_detail") or {}
    return {
        "final_match_score": breakdown.get("final_match_score"),
        "application_positioning": breakdown.get("application_positioning") or {},
        "matched_responsibilities": [
            {
                "responsibility": item.get("responsibility"),
                "evidence": item.get("evidence"),
                "category": item.get("category"),
            }
            for item in (breakdown.get("matched_responsibilities") or [])[:8]
            if isinstance(item, dict)
        ],
        "missing_responsibilities": [
            {
                "responsibility": item.get("responsibility"),
                "gap": item.get("gap"),
                "category": item.get("category"),
            }
            for item in (breakdown.get("missing_responsibilities") or [])[:8]
            if isinstance(item, dict)
        ],
        "skills_missing": [
            str(item.get("skill") or item.get("keyword") or "").strip()
            for item in (skills_detail.get("must_have") or [])[:12]
            if isinstance(item, dict)
            and (item.get("status") or ("present" if item.get("present") else "missing")) == "missing"
        ],
    }


def _extract_cv_header_text(resume_text: str) -> str:
    lines = [line.strip() for line in str(resume_text or "").splitlines() if line.strip()]
    header = []
    for line in lines[:12]:
        if normalize_phrase(line) in {alias for aliases in SECTION_HEADINGS.values() for alias in aliases}:
            break
        header.append(line)
    return "\n".join(header[:8])


def _local_education_rewrite_items(raw_education: str) -> List[dict]:
    items = []
    for line in str(raw_education or "").splitlines():
        cleaned = line.strip(" -*\t")
        if cleaned:
            items.append({"heading": cleaned, "details": ""})
    return items[:6]


def build_rewrite_evidence_packets(
    resume_text: str,
    job_description: str,
    role_fit_breakdown: dict | None = None,
) -> dict:
    """Build strict source-evidence packets for section-by-section CV rewriting."""
    raw_sections = split_resume_sections_raw(resume_text)
    resume_terms = merge_unique(
        extract_skill_tokens(resume_text, limit=80)
        + extract_keyphrases(resume_text, limit=50)
        + _known_ats_hard_terms()
    )
    resume_norm = normalize_phrase(resume_text)
    resume_terms = [
        term
        for term in resume_terms
        if _phrase_present_in_normalized_text(normalize_phrase(term), resume_norm)
    ][:80]
    missing_keywords = infer_missing_keywords(resume_text, job_description)[:20]
    local_ats = _local_ats_keyword_candidates(job_description)
    jd_keywords = merge_unique(local_ats.get("hard", []) + local_ats.get("soft", []) + missing_keywords)[:40]
    compact_fit = _compact_role_fit_for_rewrite(role_fit_breakdown)
    shared = {
        "job_description": _truncate_text(job_description, 3500),
        "role_fit_summary": compact_fit,
        "cv_header": _extract_cv_header_text(resume_text),
        "allowed_cv_terms": resume_terms,
        "source_numbers": sorted(_numbers_from_text(resume_text)),
        "jd_keywords_for_gap_list_only": jd_keywords,
        "rules": [
            "Use only facts in the source CV evidence packet.",
            "Do not add JD-only skills to the rewritten CV. Put them in additional_keywords_to_include with '(add only if accurate)'.",
            "Do not invent metrics. If a useful metric is absent, append [METRIC: short question].",
            "Preserve employers, job titles, dates, education, certifications, tools, and technologies exactly unless the source CV proves them.",
            "Write concise UK CV style with action verb plus outcome and no first-person pronouns.",
        ],
    }
    return {
        "shared": shared,
        "overview": {
            **shared,
            "source_section": _truncate_text(
                "\n".join(
                    part
                    for part in (
                        raw_sections.get("summary", ""),
                        raw_sections.get("other", ""),
                    )
                    if part
                ),
                2500,
            ),
        },
        "skills": {
            **shared,
            "source_section": _truncate_text(raw_sections.get("skills", ""), 2500),
        },
        "experience": {
            **shared,
            "source_section": _truncate_text(raw_sections.get("experience", ""), 6500),
        },
        "projects": {
            **shared,
            "source_section": _truncate_text(raw_sections.get("projects", ""), 4500),
        },
        "education": {
            **shared,
            "source_section": _truncate_text(raw_sections.get("education", ""), 2500),
            "local_items": _local_education_rewrite_items(raw_sections.get("education", "")),
        },
    }


def _rewrite_section_prompt(section: str, packet: dict) -> str:
    schemas = {
        "overview": """
{
  "name": "Full name from CV or empty string",
  "contact": {"email": "", "phone": "", "linkedin": "", "location": ""},
  "role_target": "target role and company, if extractable",
  "diagnosis": {
    "current_positioning": "one sentence",
    "target_positioning": "one sentence",
    "key_gaps": ["string"]
  },
  "rewritten_summary": "3-4 sentence professional summary grounded only in the CV"
}
""",
        "skills": """
{
  "skills_section": [{"category": "Technical Skills", "items": ["CV-evidenced skill only"]}],
  "additional_keywords_to_include": ["JD-only keyword (add only if accurate)"],
  "missing_information": ["string"],
  "section_changes": [{"section": "skills", "label": "Skills", "type": "optimised", "change": "what changed and why", "original_text": "source text", "rewritten_text": "new text", "evidence_source": "skills packet"}]
}
""",
        "experience": """
{
  "experience_section": [{"heading": "Role | Company | Dates from source", "bullets": ["rewritten factual bullet"]}],
  "missing_information": ["string"],
  "section_changes": [{"section": "experience", "label": "role or bullet", "type": "optimised", "change": "what changed and why", "original_text": "source bullet", "rewritten_text": "rewritten bullet", "evidence_source": "experience packet"}]
}
""",
        "projects": """
{
  "projects_section": [{"heading": "project name from source", "bullets": ["rewritten factual bullet"]}],
  "missing_information": ["string"],
  "section_changes": [{"section": "projects", "label": "project or bullet", "type": "optimised", "change": "what changed and why", "original_text": "source bullet", "rewritten_text": "rewritten bullet", "evidence_source": "projects packet"}]
}
""",
    }
    return f"""
You are rewriting ONLY the {section.upper()} section of a CV for one specific role.

Return ONLY valid JSON matching this schema:
{schemas[section]}

Rules:
- Use ONLY facts inside SOURCE CV SECTION and CV HEADER below.
- You may use the JD and match summary only to choose emphasis and wording.
- Never add a tool, certification, qualification, employer, title, date, metric, or responsibility unless it appears in SOURCE CV SECTION or CV HEADER.
- JD-only keywords must go in additional_keywords_to_include, suffixed with "(add only if accurate)", not into the rewritten CV.
- If a missing number would materially strengthen a bullet, append [METRIC: short question] instead of inventing it.
- section_changes must explain what changed and why, and include original_text, rewritten_text, and evidence_source where possible.
- Use plain ASCII punctuation only.

CV HEADER:
{packet.get("cv_header") or "(none)"}

SOURCE CV SECTION:
{packet.get("source_section") or "(section missing from CV)"}

ALLOWED CV TERMS:
{", ".join(packet.get("allowed_cv_terms") or []) or "(none detected)"}

SOURCE NUMBERS:
{", ".join(packet.get("source_numbers") or []) or "(none detected)"}

JD KEYWORDS FOR GAP LIST ONLY:
{", ".join(packet.get("jd_keywords_for_gap_list_only") or []) or "(none)"}

MATCH SUMMARY:
{json.dumps(packet.get("role_fit_summary") or {}, ensure_ascii=False, indent=2)}

JOB DESCRIPTION:
{packet.get("job_description") or ""}
""".strip()


def rewrite_json_with_openai(prompt: str, max_output_tokens: int) -> dict:
    response = requests.post(
        f"{OPENAI_BASE_URL.rstrip('/')}/responses",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_REWRITE_MODEL,
            "instructions": (
                "Rewrite CV sections as strict JSON. Ground every generated claim in the supplied source evidence. "
                "Do not invent facts."
            ),
            "input": prompt,
            "max_output_tokens": max_output_tokens,
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
    return parse_json_response(extract_openai_output_text(response.json()))


def rewrite_json_with_gemini(prompt: str, max_output_tokens: int) -> dict:
    if not GENAI_CLIENT:
        detail = "GEMINI_API_KEY is not set."
        if GENAI_IMPORT_ERROR:
            detail = f"Gemini SDK unavailable: {GENAI_IMPORT_ERROR}"
        raise HTTPException(status_code=503, detail=detail)
    response = _genai_generate(
        model=GEMINI_REWRITE_MODEL,
        contents=prompt,
        config=gemini_generation_config(0.2, max_output_tokens=max_output_tokens),
    )
    return parse_json_response(getattr(response, "text", "") or "")


def generate_sectional_cv_rewrite(
    resume_text: str,
    job_description: str,
    role_fit_breakdown: dict | None,
    provider: str,
) -> dict:
    packets = build_rewrite_evidence_packets(resume_text, job_description, role_fit_breakdown)
    call_json = rewrite_json_with_openai if provider == "openai" else rewrite_json_with_gemini

    section_limits = {
        "overview": 900,
        "skills": 900,
        "experience": 1800,
        "projects": 1100,
    }
    parts: dict[str, dict] = {}
    for section, limit in section_limits.items():
        prompt = _rewrite_section_prompt(section, packets[section])
        parsed = call_json(prompt, limit)
        if not isinstance(parsed, dict):
            parsed = {}
        parts[section] = parsed

    combined = {
        **parts.get("overview", {}),
        "skills_section": parts.get("skills", {}).get("skills_section") or [],
        "education_section": packets["education"].get("local_items") or [],
        "experience_section": parts.get("experience", {}).get("experience_section") or [],
        "projects_section": parts.get("projects", {}).get("projects_section") or [],
        "additional_keywords_to_include": merge_unique(
            [
                *(
                    str(item).strip()
                    for item in (parts.get("skills", {}).get("additional_keywords_to_include") or [])
                    if str(item).strip()
                ),
                *(
                    f"{item} (add only if accurate)"
                    for item in (packets["shared"].get("jd_keywords_for_gap_list_only") or [])[:12]
                    if str(item).strip()
                ),
            ]
        )[:15],
        "missing_information": merge_unique(
            [
                *[str(item).strip() for item in (parts.get("skills", {}).get("missing_information") or []) if str(item).strip()],
                *[str(item).strip() for item in (parts.get("experience", {}).get("missing_information") or []) if str(item).strip()],
                *[str(item).strip() for item in (parts.get("projects", {}).get("missing_information") or []) if str(item).strip()],
            ]
        )[:12],
        "section_changes": [
            *[item for item in (parts.get("skills", {}).get("section_changes") or []) if isinstance(item, dict)],
            *[item for item in (parts.get("experience", {}).get("section_changes") or []) if isinstance(item, dict)],
            *[item for item in (parts.get("projects", {}).get("section_changes") or []) if isinstance(item, dict)],
        ],
    }
    normalized = normalize_rewrite_response(combined)
    normalized = audit_and_validate_rewrite(normalized, resume_text, job_description)
    normalized["rewrite_pipeline"] = {
        "mode": "sectional",
        "provider": provider,
        "sections": ["overview", "skills", "experience", "projects", "education"],
    }
    if not normalized["rewritten_summary"] and not normalized["experience_section"]:
        raise HTTPException(status_code=502, detail="CV rewrite generation returned an invalid response.")
    return normalized


def openai_rewrite_cv(
    resume_text: str,
    job_description: str,
    role_fit_breakdown: dict | None = None,
) -> dict:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set.")
    return generate_sectional_cv_rewrite(
        resume_text=resume_text,
        job_description=job_description,
        role_fit_breakdown=role_fit_breakdown,
        provider="openai",
    )

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
    return generate_sectional_cv_rewrite(
        resume_text=resume_text,
        job_description=job_description,
        role_fit_breakdown=role_fit_breakdown,
        provider="gemini",
    )

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

    response = _genai_generate(
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
    result = _genai_embed(
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


_COMPANY_SUBJECT_RE = re.compile(
    r'^\s*(\([A-Za-z]+:[A-Za-z]+\)|we\b|our\b|at\s+[A-Z][A-Za-z0-9& .-]+\b)',
    re.IGNORECASE,
)
_COMPANY_DESC_SIGNALS = (
    "is a leading", "is an industry", "is dedicated to", "dedicated to helping",
    "solutions provider", "our portfolio", "our comprehensive", "our mission",
    "our vision", "our customers", "helping customers",
    "is one of", "has one of", "manage a combined", "manages a combined",
    "assets under management", "around the world", "part of blackrock",
    "with offices in",
    "note taking platform", "organization platform", "digital businesses",
    "portfolio of outstanding", "serves a huge number of customers",
    "shareowners", "own and operate", "company culture",
    "trusted by", "number one online", "online motor insurance provider",
    "expanding to help", "future of insurance",
    "roster of", "blue chip", "career development opportunities",
    "our team helps", "impressive roster",
)
_JD_META_SIGNALS = (
    "by applying", "stepping into", "you may work directly", "part of a high performing",
    "what we offer", "selection process", "all applications", "careers page",
    "apply now", "before you apply", "permanent or fixed term",
    "salary", "base compensation", "total compensation", "compensation range",
    "benefits", "access to equity", "tax cut",
    "remote working", "flexible hours", "start date", "relocation", "parental",
    "health insurance", "reasonable accommodations", "offer stage",
    "experienced professionals who will support your continued development",
    "continued development", "meaningful personal connections",
)
_CANDIDATE_HINTS = (
    "you will", "you'll", "you are", "you should", "you must",
    "candidate will", "the candidate", "successful candidate",
    "responsible for", "responsibilities", "what you'll do",
    "what you will do", "day to day", "in this role",
)
_ACTION_VERBS_SET = set(ACTION_VERBS)
JD_REQUIREMENT_ACTION_VERBS = {
    "draft", "edit", "proofread", "interview", "use", "work", "handle",
    "write", "prepare", "publish", "coordinate", "analyse", "analyze",
}

GENERAL_ACTION_FAMILIES = {
    "analyse": {
        "analyse", "analysed", "analyses", "analysing", "analyze", "analyzed",
        "analyzes", "analyzing", "analysis", "assess", "assessed", "assessing",
        "clarify", "clarified", "clarifying", "evaluate", "evaluated", "evaluating",
    },
    "collaborate": {
        "collaborate", "collaborated", "collaborating", "collaboration",
        "coordinate", "coordinated", "coordinating", "partner", "partnered",
        "partnering", "team", "teams", "teamwork", "agile",
    },
    "deliver": {
        "build", "building", "built", "create", "created", "creating", "deliver", "delivered",
        "delivering", "develop", "developed", "developing", "implement",
        "implemented", "implementing", "implementation",
    },
    "research": {
        "experiment", "experimented", "experimenting", "explore", "explored",
        "exploring", "investigate", "investigated", "investigating", "research",
        "researched", "researching",
    },
    "test": {
        "test", "tested", "testing", "validate", "validated", "validating",
        "validation", "verify", "verified", "verifying",
    },
    "troubleshoot": {
        "correct", "corrected", "correcting", "debug", "debugged", "debugging",
        "diagnose", "diagnosed", "diagnosing", "fix", "fixed", "fixing",
        "resolve", "resolved", "resolving", "troubleshoot", "troubleshot",
        "troubleshooting",
    },
}
GENERAL_ACTION_TOKEN_TO_FAMILY = {
    token: family
    for family, tokens in GENERAL_ACTION_FAMILIES.items()
    for token in tokens
}


def _general_action_families(text: str) -> set[str]:
    return {
        GENERAL_ACTION_TOKEN_TO_FAMILY[token]
        for token in normalize_phrase(text).split()
        if token in GENERAL_ACTION_TOKEN_TO_FAMILY
    }

ROLE_REQUIREMENT_SIGNALS = (
    "experience", "knowledge", "skills", "ability", "proficiency",
    "proficient", "familiar", "awareness", "exposure", "comfortable", "comfort",
    "understanding", "strength", "background", "willingness", "expertise", "expert",
    "track record",
    "responsible", "accountable", "expected to", "you will", "you'll",
    "you ll", "you can", "you are able", "lead", "mentor", "manage",
    "design", "develop", "test", "testing", "deploy", "scale", "secure", "monitor",
    "collaborate", "communicate", "stakeholder", "technical direction",
    "technical vision", "delivery", "architecture", "agile", "creating",
    "pipeline", "pipelines",
    "source control", "deployment pipeline", "deployment pipelines",
    "programming language", "programming languages", "programming skills",
    "certification", "certifications", "qualification", "qualifications", "qualified", "degree", "capable",
    "responsive web applications", "windows applications",
    "primitive data types", "binary level", "serverless", "containers",
    "ownership", "end-to-end ownership", "ai tools", "workflow",
    "correctness", "reliability", "maintainability", "scalable",
    "audit", "auditing", "accounting", "statutory accounts",
    "external audits", "consolidated accounts",
    "microsoft excel", "powerpoint", "microsoft word",
)

NICE_SECTION_HEADERS = (
    "nice to have", "preferred", "desirable", "bonus", "advantageous",
    "ideal experience", "nice-to-have", "preferred bonus",
)

ESSENTIAL_SECTION_HEADERS = (
    "what we're looking for", "what we are looking for", "required",
    "requirements", "qualifications", "essential", "skills you will have",
    "how you'll spend your time", "how you ll spend your time",
    "responsibilities", "job responsibilities", "about you",
    "what we look for",
)

EXCLUDED_JD_SECTION_HEADERS = (
    "background",
    "company",
    "our company",
    "who we are",
    "our culture",
    "culture",
    "perks",
    "application process",
    "salary range",
    "compensation and benefits",
    "about this role",
    "an overview of this role",
    "overview of this role",
    "about the team",
    "our benefits",
    "benefits",
    "benefits and perks",
    "our hybrid work model",
    "hybrid work model",
    "about blackrock",
    "about the fca",
    "about the fca and team",
    "about the company",
    "about the job",
    "overview of job",
    "role overview",
    "reporting of the role",
    "measures of success",
    "3 best things about the job",
    "company overview",
    "about us",
    "what we offer",
    "commitment contract",
    "commitment and contract",
    "location",
    "the selection process",
    "selection process",
    "before you apply",
    "equal opportunity employer",
    "equal employment opportunity",
)

CANDIDATE_JD_SECTION_HEADERS = (
    "description",
    "a few examples of your responsibilities",
    "duties",
    "key duties",
    "your responsibilities",
    "job responsibilities",
    "what you'll do",
    "what you ll do",
    "what you will do",
    "what you'll be doing",
    "what you ll be doing",
    "what we're looking for",
    "what we re looking for",
    "what you'll bring",
    "what you ll bring",
    "what you will bring",
    "what you will need",
    "about you",
    "about the candidate",
    "key responsibilities",
    "role responsibilities",
    "responsibilities",
    "preferred qualifications skills",
    "preferred qualifications capabilities and skills",
    "preferred qualifications",
    "preferred requirements",
    "preferred experience",
    "minimum qualifications",
    "basic qualifications",
    "required qualifications capabilities and skills",
    "essential criteria",
    "desirable criteria",
    "person specification",
    "qualifications skills",
    "qualifications",
    "skills",
    "skills knowledge and expertise",
    "skills knowledge expertise",
    "skills and expertise",
    "knowledge and expertise",
    "skills knowledge and experience",
    "requirements",
    "required skills",
    "required experience",
    "required skills experience",
    "required skills and experience",
    "essential skills",
    "essential skills experience",
    "essential skills and experience",
    "desirable skills",
    "preferred bonus",
)

JD_LIST_MARKER_RE = re.compile(
    r"^(?:[-*]|\u2022|\u25aa|\u25e6|\u2023|\d+[.)])\s+"
)


def _strip_jd_list_marker(line: str) -> tuple[str, bool]:
    raw = str(line or "").strip()
    is_list_item = bool(JD_LIST_MARKER_RE.match(raw))
    return JD_LIST_MARKER_RE.sub("", raw, count=1).strip(), is_list_item


def _classify_jd_section_heading(line_norm: str) -> str | None:
    if not line_norm or len(line_norm.split()) > 10:
        return None
    if line_norm.startswith("why ") and len(line_norm.split()) <= 4:
        return "excluded"
    if line_norm.startswith("more about ") and len(line_norm.split()) <= 8:
        return "excluded"
    if re.match(r"^(?:how|ways) .{0,40} support (?:you|employees|team members)$", line_norm):
        return "excluded"
    if "responsible for" in line_norm and len(line_norm.split()) <= 12:
        return "candidate"
    if line_norm in EXCLUDED_JD_SECTION_HEADERS:
        return "excluded"
    if line_norm in CANDIDATE_JD_SECTION_HEADERS:
        return "candidate"
    if any(line_norm.startswith(f"{header} ") for header in CANDIDATE_JD_SECTION_HEADERS if header.startswith("what ")):
        return "candidate"
    if line_norm.startswith("about ") and len(line_norm.split()) <= 8:
        return "excluded"
    if any(
        line_norm.startswith(f"{header} ")
        for header in EXCLUDED_JD_SECTION_HEADERS
        if header.startswith("about ")
    ):
        return "excluded"
    return None


ATS_NOISE_TERMS = {
    "role", "candidate", "candidates", "team", "teams", "business", "division",
    "location", "company", "firm", "client", "clients", "work", "working",
    "support", "supports", "management", "solutions", "global", "professional",
    "professionals", "investment professional", "associate", "about", "background",
    "benefits", "hybrid", "office", "travel", "around the world",
    "data", "policy", "policies", "regime", "regimes", "analysis", "technical",
    "industry", "engagement", "requirements", "guidelines", "rules", "advice",
    "support", "users", "audiences", "priorities", "judgement", "insights",
    "corporate safety", "analytics teams", "iag",
    "experience is", "knowledge is",
}

ATS_SINGLE_TOKEN_ALLOWLIST = {
    "mifir", "emir", "sftr", "mifid", "rts", "excel", "powerpoint", "word",
    "python", "sql", "aws", "docker", "kubernetes", "react", "typescript",
    "javascript", "java", "c++", "c#", "scala", "spark", "hadoop",
    "kafka", "flink", "databricks", "redshift", "postgresql", "lightgbm",
    "xgboost", "mlops", "airflow", "oozie", "rdbms",
    "rust", "grpc", "rest", "microservices", "monoliths", "testing",
    "documentation", "correctness", "reliability", "maintainability",
    "scalability", "cms", "seo", "qts", "copywriting", "proofreading", "copyediting",
    "audit", "auditing", "accounting", "aca", "acca", "outlook", "sage",
    "proaudit", "cpd", "charities",
    "net", "binary", "git",
    "communication", "teamwork", "leadership", "collaboration",
    "adaptability", "deadlines", "multitasking",
}

LOCAL_ATS_HARD_KEYWORDS = (
    "Python",
    "Amazon Web Services",
    "AWS",
    "AWS Certified",
    "A Cloud Guru",
    "SQL",
    "TypeScript",
    ".NET",
    "Git",
    "source control",
    "deployment pipelines",
    "containers",
    "serverless functions",
    "responsive web applications",
    "Windows applications",
    "primitive data types",
    "binary level",
    "flight data",
    "airborne software",
    "aircraft issues",
    "prognostics",
    "alerts",
    "visualisations",
    "Rust",
    "gRPC",
    "REST",
    "Kubernetes",
    "Docker",
    "microservices",
    "monoliths",
    "testing",
    "A/B testing",
    "documentation",
    "correctness",
    "reliability",
    "maintainability",
    "scalability",
    "AI tools",
    "ACA",
    "ACCA",
    "external audits",
    "audit planning",
    "audit completion",
    "statutory accounts",
    "consolidated accounts",
    "accounting standards",
    "auditing standards",
    "working papers",
    "audit file",
    "management letter",
    "letter of representation",
    "CPD",
    "Excel",
    "Word",
    "Outlook",
    "Sage",
    "ProAudit",
    "not-for-profit organisations",
    "charities",
    "copywriting",
    "proofreading",
    "copyediting",
    "written English",
    "English degree",
    "Humanities degree",
    "research synthesis",
    "stakeholder communication",
    "content management system",
    "CMS",
    "SEO",
    "Google Analytics",
    "Adobe InDesign",
    "InDesign",
    "editorial calendar",
    "editorial style guide",
    "content calendar",
)

LOCAL_ATS_SOFT_KEYWORDS = (
    "reasoning ability",
    "ownership",
    "team spirit",
    "collaborative",
    "collaboration",
    "drive",
    "communication skills",
    "stakeholder management",
    "stakeholder collaboration",
    "leadership",
    "team player",
    "ethical judgement",
    "professional attitude",
    "work ethic",
    "self-motivated",
    "deadline management",
    "multitasking",
    "problem solving",
    "attention to detail",
)


def _candidate_requirement_text_blob(job_description: str) -> str:
    """JD text from candidate-facing sections only, used as a strict ATS keyword source."""
    lines: List[str] = []
    active_section = "unknown"
    for raw_line in str(job_description or "").splitlines():
        line, is_bullet = _strip_jd_list_marker(raw_line)
        if not line:
            continue
        line_norm = normalize_phrase(line)
        section_type = _classify_jd_section_heading(line_norm)
        if section_type:
            active_section = section_type
            continue
        if active_section == "excluded":
            continue
        if active_section == "candidate" or is_bullet or _should_keep_requirement_line(line):
            lines.append(line)
    return normalize_phrase(" ".join(lines))


def is_valid_ats_keyword(keyword: str, candidate_jd_blob: str) -> bool:
    norm = normalize_phrase(keyword)
    if not norm:
        return False
    tokens = norm.split()
    if len(tokens) > 6:
        return False
    if len(tokens) == 1 and norm not in ATS_SINGLE_TOKEN_ALLOWLIST:
        return False
    if norm in STOPWORDS or norm in ATS_NOISE_TERMS:
        return False
    if all(token in STOPWORDS or token in ATS_NOISE_TERMS for token in tokens):
        return False
    if any(sig in norm for sig in _COMPANY_DESC_SIGNALS):
        return False
    if re.match(
        r"^(?:blackrock|gip|gis|global infrastructure solutions|global infrastructure partners|the firm)\b",
        norm,
    ):
        return False
    if _ats_term_is_negated(norm, candidate_jd_blob):
        return False
    if norm in candidate_jd_blob:
        return True
    candidate_tokens = set(candidate_jd_blob.split())
    if _looks_like_soft_ats_keyword(norm) and len(tokens) <= 3 and all(token in candidate_tokens for token in tokens):
        return True
    return False


def _ats_term_is_negated(term_norm: str, candidate_jd_blob: str) -> bool:
    if not term_norm or not candidate_jd_blob:
        return False
    term = re.escape(term_norm)
    negation = r"(?:not\s+required|not\s+essential|not\s+necessary|is\s+not\s+required|required\s+not)"
    patterns = (
        rf"{term}(?:\s+\w+){{0,2}}\s+{negation}",
        rf"{negation}(?:\s+\w+){{0,2}}\s+{term}",
    )
    return any(re.search(pattern, candidate_jd_blob) for pattern in patterns)


def _count_normalized_phrase(phrase: str, text_norm: str) -> int:
    phrase_norm = normalize_phrase(phrase)
    if not phrase_norm or not text_norm:
        return 0
    pattern = rf"(?<![a-z0-9+#]){re.escape(phrase_norm)}(?![a-z0-9+#])"
    return len(re.findall(pattern, text_norm))


def _looks_like_soft_ats_keyword(skill: str) -> bool:
    norm = normalize_phrase(skill)
    soft_terms = set(normalize_phrase(term) for term in SOFT_SKILLS)
    soft_terms.update({
        "team player",
        "teamwork",
        "collaborative",
        "collaboration",
        "communication",
        "stakeholder collaboration",
        "ethical judgement",
        "professional attitude",
        "work ethic",
        "self motivated",
        "deadline management",
        "multi tasker",
        "multitasker",
        "personable",
        "conscientious",
        "attention to detail",
    })
    return norm in soft_terms or any(term in norm for term in soft_terms if len(term.split()) > 1)


def _soft_ats_keyword_key(skill: str) -> str:
    """Collapse wording variants so one soft skill is not scored multiple times."""
    norm = normalize_phrase(skill)
    norm = re.sub(
        r"^(?:(?:apply|demonstrate|show|use|excellent|strong|good|clear|advanced|pragmatic)\s+)+",
        "",
        norm,
    ).strip()
    norm = re.sub(r"\s+(?:skill|skills|ability|abilities)$", "", norm).strip()
    aliases = {
        "collaborative": "collaboration",
        "problem solving": "problem solving",
        "problem solver": "problem solving",
        "analytical thinking": "analytical",
    }
    return aliases.get(norm, norm)


def _merge_similar_soft_ats_keywords(items: List[str]) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()
    for item in items or []:
        key = _soft_ats_keyword_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _display_ats_keyword(phrase: str) -> str:
    norm = normalize_phrase(phrase)
    display = {
        "aca": "ACA",
        "acca": "ACCA",
        "cpd": "CPD",
        "excel": "Excel",
        "word": "Word",
        "outlook": "Outlook",
        "sage": "Sage",
        "proaudit": "ProAudit",
        "python": "Python",
        "grpc": "gRPC",
        "rest": "REST",
        "aws": "AWS",
        "amazon web services": "Amazon Web Services",
        "sql": "SQL",
        "typescript": "TypeScript",
        "net": ".NET",
        "git": "Git",
        "cms": "CMS",
        "seo": "SEO",
        "qts": "QTS",
        "google analytics": "Google Analytics",
        "adobe indesign": "Adobe InDesign",
        "indesign": "InDesign",
        "written english": "written English",
        "a b testing": "A/B testing",
    }
    return display.get(norm, str(phrase or "").strip())


def clean_model_skill_name(skill: str) -> str:
    cleaned = str(skill or "").strip(" -:;,.")
    cleaned = re.split(r"\betc\.?\b|\.|;", cleaned, maxsplit=1, flags=re.IGNORECASE)[0].strip(" -:;,.")
    # Drop a dangling unbalanced parenthesis fragment left by sentence splitting,
    # e.g. "data-product experience (producing" or "Data visualisation libraries (charts".
    if cleaned.count("(") > cleaned.count(")"):
        cleaned = re.sub(r"\s*\([^)]*$", "", cleaned).strip(" -:;,.")
    cleaned = re.sub(
        r"^(?:expert in|proficient in|proficiency in|experience (?:building|developing|with|in|of)|"
        r"strong experience (?:with|in|of)|working knowledge of|knowledge on|knowledge of|exposure to|"
        r"familiarity with|(?:strong )?focus on|hands-on experience in|hands-on with|ability to|"
        r"technologies like|ensure|a|an|the)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip(" -:;,.")
    # Drop trailing qualifier noise: "FastAPI a plus", "X (a plus)", "X where required",
    # "X experience" -> "X", "X practices" -> "X".
    cleaned = re.sub(
        r"\s+(?:is\s+)?a\s+(?:strong\s+)?plus$|\s+a\s+bonus$|\s+where\s+(?:required|applicable)$|"
        r"\s+if\s+required$|\s+preferred$|\s+desirable$|\s+experience$|\s+practices$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip(" -:;,.")
    cleaned = re.sub(r"\s+", " ", cleaned)
    display = _display_ats_keyword(cleaned)
    return display.strip(" -:;,.")


def is_valid_model_skill(skill: str, candidate_jd_blob: str) -> bool:
    norm = normalize_phrase(skill)
    if not norm:
        return False
    if norm.startswith((
        "you will ",
        "you ll ",
        "we ",
        "our ",
        "since ",
        "fueled by ",
        "fuelled by ",
        "embrace ",
    )):
        return False
    if any(sig in norm for sig in _COMPANY_DESC_SIGNALS):
        return False
    if _looks_like_noisy_ats_fragment(skill):
        return False
    return is_valid_ats_keyword(skill, candidate_jd_blob)


ATS_FRAGMENT_NOISE_PREFIXES = (
    "contribute in",
    "contribute to",
    "written in",
    "running in",
    "working with",
    "working closely",
    "learn from",
    "learning from",
    "basic familiarity",
    "interest in",
    "usage of",
    "support ",
    "maintain ",
    "maintenance of",
    "interview ",
    "experience writing",
    "proofread ",
    "use ",
    "handle ",
    "interface with",
    "predict ",
    "capable of",
    "other parts of",
    "its companies",
    "an experienced",
    "software certifications e g",
    "what you",
    "approved on time",
)


def _looks_like_noisy_ats_fragment(phrase: str) -> bool:
    norm = normalize_phrase(phrase)
    if not norm:
        return True
    if any(norm.startswith(prefix) for prefix in ATS_FRAGMENT_NOISE_PREFIXES):
        return True
    if " to " in f" {norm} " and not any(term in norm for term in ("source control", "deployment pipelines")):
        return True
    if norm in ATS_NOISE_TERMS:
        return True
    tokens = norm.split()
    if tokens and tokens[0] in _ACTION_VERBS_SET:
        return True
    if len(tokens) > 1 and tokens[0] in GENERAL_ACTION_TOKEN_TO_FAMILY:
        return True
    # Requirement-sentence fragments that are not skills: trailing filler nouns and
    # experience-duration phrases ("6+ months hands-on", "platform interaction",
    # "audit/compliance/cost views", "a testing-first mindset", "development for other teams").
    non_skill_tail_nouns = {"mindset", "views", "interaction", "focus", "teams", "needs", "field"}
    if len(tokens) > 1 and tokens[-1] in non_skill_tail_nouns:
        return True
    if re.search(r"\b\d+\s*\+?\s*(?:month|months|year|years)\b", norm):
        return True
    extra_leading_verbs = JD_REQUIREMENT_ACTION_VERBS | {
        "take", "run", "ensure", "ship", "drive", "steer", "unblock", "accelerate", "surface",
    }
    if len(tokens) > 1 and tokens[0] in extra_leading_verbs:
        return True
    contextual_heads = {
        "actions", "colleagues", "countries", "developers", "issues",
        "managers", "problems", "processes", "projects", "states",
    }
    if tokens and (
        tokens[0] == "the"
        or contextual_heads.intersection(tokens)
        or {"countries", "states"}.intersection(tokens)
        or "our" in tokens
        or tokens[0] in {"preferred", "highly"}
        or tokens[-1] == "preferred"
    ):
        return True
    if len(tokens) > 4 and not any(
        _phrase_present_in_normalized_text(normalize_phrase(term), norm)
        for term in _known_ats_hard_terms()
        if len(normalize_phrase(term).split()) >= 2
    ):
        return True
    return False


def _known_ats_hard_terms() -> List[str]:
    terms: List[str] = []
    terms.extend(LOCAL_ATS_HARD_KEYWORDS)
    terms.extend(STRICT_TOOL_TERMS)
    terms.extend(REGULATED_PROCESS_TERMS)
    terms.extend(CONTROL_GOVERNANCE_TERMS)
    terms.extend(REPORTING_STRICT_TERMS)
    terms.extend(AUDIT_STRICT_TERMS)
    terms.extend(
        (
            "audit",
            "auditing",
            "accounting",
            "external audits",
            "financial reporting",
            "statutory accounts",
            "audit planning",
            "audit completion",
            "not-for-profit organisations",
            "charities",
            "stakeholder reporting",
            "data analysis",
            "data modelling",
            "project management",
            "risk assessment",
            "internal controls",
        )
    )
    return merge_unique([_display_ats_keyword(term) for term in terms if str(term).strip()])


def _known_ats_soft_terms() -> List[str]:
    terms: List[str] = []
    terms.extend(LOCAL_ATS_SOFT_KEYWORDS)
    terms.extend(SOFT_SKILLS)
    terms.extend(
        (
            "personable",
            "conscientious",
            "able to work to deadlines",
            "work under pressure",
            "multi-tasker",
            "support junior members",
            "develop junior members",
            "ethical judgement",
            "professional attitude",
            "good work ethic",
            "self motivated",
            "self-motivated",
            "attention to detail",
        )
    )
    return merge_unique([_display_ats_keyword(term) for term in terms if str(term).strip()])


def _local_ats_keyword_candidates(job_description: str) -> dict:
    """Deterministically recover hard/soft ATS terms from candidate-facing JD text."""
    candidate_blob = _candidate_requirement_text_blob(job_description)
    hard: List[str] = []
    soft: List[str] = []

    for term in _known_ats_hard_terms():
        if is_valid_ats_keyword(term, candidate_blob) and _count_normalized_phrase(term, candidate_blob) > 0:
            hard.append(_display_ats_keyword(term))
    for term in _known_ats_soft_terms():
        if is_valid_ats_keyword(term, candidate_blob) and _count_normalized_phrase(term, candidate_blob) > 0:
            soft.append(_display_ats_keyword(term))

    for req in extract_local_job_requirements(job_description, limit=80):
        text = str(req.get("text") or "")
        for fragment in re.split(r";|,|\s+/\s+|\band\b|\bor\b", text, flags=re.IGNORECASE):
            cleaned = re.sub(
                r"^(?:must have|should have|required|essential|desirable|able to|ability to|experience of|experience in|experience with|strong|good|excellent)\s+",
                "",
                fragment.strip(" -:.()"),
                flags=re.IGNORECASE,
            ).strip(" -:.()")
            cleaned = re.sub(
                r"\s*[-–—]?\s*(?:essential|desirable|required)\s*$",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip(" -:.()")
            cleaned = re.sub(
                r"\b(?:experience|knowledge|proficiency)\s+is$",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip(" -:.()")
            norm = normalize_phrase(cleaned)
            if not norm or len(norm.split()) > 6:
                continue
            if _looks_like_noisy_ats_fragment(cleaned):
                continue
            display = _display_ats_keyword(cleaned)
            if not is_valid_ats_keyword(display, candidate_blob):
                continue
            if _looks_like_soft_ats_keyword(display):
                soft.append(display)
            elif len(norm.split()) > 1 or norm in ATS_SINGLE_TOKEN_ALLOWLIST:
                hard.append(display)

    return {
        "hard": merge_unique(hard),
        "soft": _merge_similar_soft_ats_keywords(soft),
    }


def _infer_requirement_category_from_context(line_norm: str, current_category: str) -> str:
    if any(header in line_norm for header in NICE_SECTION_HEADERS):
        return "nice_to_have"
    if any(header in line_norm for header in ESSENTIAL_SECTION_HEADERS):
        return "essential"
    return current_category


def _looks_like_job_title_line(text: str) -> bool:
    norm = normalize_phrase(text)
    tokens = norm.split()
    if not 2 <= len(tokens) <= 7 or re.search(r"[.!?;:]", str(text or "")):
        return False
    if any(signal in norm for signal in (
        "experience", "knowledge", "ability", "skills", "proficiency", "degree",
        "responsible", "required", "preferred", "develop solutions", "build ",
    )):
        return False
    role_terms = {
        "engineer", "developer", "analyst", "manager", "consultant", "specialist",
        "architect", "scientist", "designer", "associate", "officer", "coordinator",
        "administrator", "lead",
    }
    return bool(role_terms.intersection(tokens))


def _should_keep_requirement_line(text: str, in_candidate_section: bool = False) -> bool:
    norm = normalize_phrase(text)
    if len(norm.split()) < 3:
        return False
    if _looks_like_job_title_line(text):
        return False
    if re.match(
        r"^(?!you\b|candidate\b|the candidate\b)[a-z0-9&.-]+\s+"
        r"(?:builds|creates|develops|offers|provides)\b.*\b(?:helps?|serves?|supports?)\b",
        norm,
    ):
        return False
    if re.search(r"\b(?:reports?|reporting)\s+to\s+(?:the\s+)?[\w -]+", norm):
        return False
    if norm.startswith((
        "gained a comprehensive understanding of ",
        "delivered first outcomes at pace ",
        "contributed considerably in bringing our ",
        "established yourself as ",
    )):
        return False
    if "brings together a suite of" in norm:
        return False
    if _classify_jd_section_heading(norm):
        return False
    if re.match(r"^since\s+\d{4}\b", norm):
        return False
    if norm.startswith(("fueled by ", "fuelled by ", "you ll be joining ", "you will be joining ")):
        return False
    if norm in {"experience in planning", "planning", "strong planning"}:
        return False
    if not in_candidate_section and _COMPANY_SUBJECT_RE.match(text):
        return False
    if any(sig in norm for sig in _COMPANY_DESC_SIGNALS):
        return False
    if any(sig in norm for sig in _JD_META_SIGNALS):
        return False
    if not in_candidate_section and re.match(r"^[a-z0-9 &.-]{2,60}\s+is\s+(?:a|an|one of|part of|the)\b", norm) and not any(
        hint in norm for hint in _CANDIDATE_HINTS
    ):
        return False
    if not in_candidate_section and re.match(
        r"^(?:blackrock|gip|gis|global infrastructure solutions|global infrastructure partners|the firm)\b",
        norm,
    ) and not any(hint in norm for hint in _CANDIDATE_HINTS):
        return False
    if any(noise in norm for noise in ("salary", "benefit", "pension", "annual leave", "dress code")):
        return False
    # Work-eligibility / legal lines are never scoreable against CV evidence.
    if re.search(
        r"\b(?:right to work|eligible to work|work authoris\w*|work authoriz\w*|"
        r"visa sponsor\w*|require sponsorship|work permit)\b",
        norm,
    ):
        return False
    if re.search(r"\b(?:not required|not essential|not necessary|optional only)\b", norm):
        return False
    if in_candidate_section:
        return True
    tokens = norm.split()
    starts_action = bool(tokens) and (tokens[0] in _ACTION_VERBS_SET or tokens[0] in JD_REQUIREMENT_ACTION_VERBS)
    return starts_action or any(sig in norm for sig in ROLE_REQUIREMENT_SIGNALS)


def _looks_like_requirement_heading(text: str) -> bool:
    norm = normalize_phrase(text)
    if not norm:
        return True
    if _classify_jd_section_heading(norm):
        return True
    if norm in {
        "necessary education and experience",
        "education and experience",
        "required education and experience",
        "skills and experience",
        "required qualifications",
        "necessary qualifications",
        "desirable experience",
        "desirable qualifications",
    }:
        return True
    return bool(
        len(norm.split()) <= 5
        and re.fullmatch(
            r"(?:necessary|required|essential|desirable|preferred|minimum|basic)?\s*"
            r"(?:education|experience|skills|qualifications)"
            r"(?:\s+and\s+(?:education|experience|skills|qualifications))?",
            norm,
        )
    )


def _requirement_dict(text: str, category: str, in_candidate_section: bool = False) -> dict | None:
    cleaned = str(text or "").strip().strip("-*â€¢ ").strip()
    if _looks_like_requirement_heading(cleaned):
        return None
    if not _should_keep_requirement_line(cleaned, in_candidate_section=in_candidate_section):
        return None
    norm = normalize_phrase(cleaned)
    return {
        "text": cleaned,
        "normalized": norm,
        "action_phrases": extract_action_phrases(cleaned),
        "category": category if category in {"essential", "nice_to_have"} else "essential",
    }


ALLOWED_GROUNDED_REQUIREMENT_TYPES = {
    "candidate_skill",
    "candidate_experience",
    "candidate_qualification",
    "candidate_behaviour",
    "candidate_responsibility",
    "candidate_tool",
}


def build_jd_source_sentences(job_description: str) -> List[dict]:
    """Build stable, section-aware source records that model output must cite."""
    sources: List[dict] = []
    active_section = "unknown"
    source_section = "unknown"
    category = "essential"

    for raw_line in str(job_description or "").splitlines():
        line, is_bullet = _strip_jd_list_marker(raw_line)
        if not line:
            continue
        line_norm = normalize_phrase(line)
        section_type = _classify_jd_section_heading(line_norm)
        if section_type:
            active_section = section_type
            source_section = line
            category = (
                _infer_requirement_category_from_context(line_norm, "essential")
                if section_type == "candidate"
                else "essential"
            )
            continue

        next_category = _infer_requirement_category_from_context(line_norm, category)
        if next_category != category and len(line_norm.split()) <= 6:
            category = next_category
            continue
        category = next_category

        clauses = [line]
        if len(line.split()) > 28:
            clauses = [
                part.strip()
                for part in re.split(r"(?<=[.!?])\s+", line)
                if part.strip()
            ] or [line]
        for clause in clauses:
            sources.append({
                "sentence_id": f"s{len(sources) + 1}",
                "text": clause,
                "source_section": source_section,
                "section_type": active_section,
                "is_bullet": is_bullet,
                "category": category,
            })
    return sources


def verify_grounded_job_requirements(
    items: List[dict],
    source_sentences: List[dict],
    limit: int = 35,
) -> tuple[List[dict], List[str]]:
    """Accept only candidate-owned model nominations grounded in exact JD sources."""
    source_by_id = {
        str(source.get("sentence_id") or ""): source
        for source in source_sentences or []
        if isinstance(source, dict)
    }
    verified: List[dict] = []
    rejected: List[str] = []
    seen: set[str] = set()

    for item in items or []:
        if not isinstance(item, dict):
            rejected.append("Rejected malformed model item.")
            continue
        source_id = str(item.get("source_sentence_id") or "").strip()
        source = source_by_id.get(source_id)
        if not source:
            rejected.append(f"Rejected unknown source sentence ID: {source_id or 'missing'}.")
            continue
        if item.get("scoreable_against_cv") is not True:
            rejected.append(f"Rejected non-scoreable source: {source_id}.")
            continue
        if str(item.get("owner") or "").strip().lower() != "candidate":
            rejected.append(f"Rejected non-candidate owner: {source_id}.")
            continue
        if str(item.get("type") or "").strip() not in ALLOWED_GROUNDED_REQUIREMENT_TYPES:
            rejected.append(f"Rejected unsupported requirement type: {source_id}.")
            continue
        section_type = str(source.get("section_type") or "unknown")
        if section_type == "excluded":
            rejected.append(f"Rejected excluded JD section: {source_id}.")
            continue
        in_candidate_context = section_type == "candidate" or (
            section_type == "unknown" and bool(source.get("is_bullet"))
        )
        category = str(item.get("category") or source.get("category") or "essential")
        if source.get("category") == "nice_to_have":
            category = "nice_to_have"
        requirement = _requirement_dict(
            str(source.get("text") or ""),
            category,
            in_candidate_section=in_candidate_context,
        )
        if not requirement:
            rejected.append(f"Rejected invalid source text: {source_id}.")
            continue
        norm = requirement["normalized"]
        if norm in seen:
            continue
        seen.add(norm)
        requirement["source_sentence_id"] = source_id
        verified.append(requirement)
        if len(verified) >= limit:
            break
    return verified, rejected


def extract_local_job_requirements(job_description: str, limit: int = 35) -> List[dict]:
    """Deterministically extract candidate-owned requirements and responsibilities from a JD."""
    requirements: List[dict] = []
    seen: set[str] = set()
    category = "essential"
    active_section = "unknown"

    for raw_line in str(job_description or "").splitlines():
        line, is_bullet = _strip_jd_list_marker(raw_line)
        if not line:
            continue
        line_norm = normalize_phrase(line)
        section_type = _classify_jd_section_heading(line_norm)
        if section_type:
            category = (
                _infer_requirement_category_from_context(line_norm, "essential")
                if section_type == "candidate"
                else "essential"
            )
            active_section = section_type
            continue
        if active_section == "excluded":
            continue
        next_category = _infer_requirement_category_from_context(line_norm, category)
        if next_category != category and len(line_norm.split()) <= 6:
            category = next_category
            continue
        category = next_category
        in_candidate_context = active_section == "candidate" or (
            active_section == "unknown" and is_bullet
        )

        clauses = [line]
        if len(line.split()) > 28:
            clauses = [
                part.strip()
                for part in re.split(r"(?<=[.!?])\s+", line)
                if part.strip()
            ] or [line]

        for clause in clauses:
            candidates = [clause]
            added_for_clause = False
            for candidate in candidates:
                req = _requirement_dict(
                    candidate,
                    category,
                    in_candidate_section=in_candidate_context,
                )
                if not req:
                    continue
                norm = req["normalized"]
                if norm in seen:
                    continue
                seen.add(norm)
                requirements.append(req)
                added_for_clause = True
                if len(requirements) >= limit:
                    return requirements
            if not added_for_clause and candidates != [clause]:
                req = _requirement_dict(
                    clause,
                    category,
                    in_candidate_section=in_candidate_context,
                )
                if req and req["normalized"] not in seen:
                    seen.add(req["normalized"])
                    requirements.append(req)
                    if len(requirements) >= limit:
                        return requirements
    return requirements


REQUIREMENT_MERGE_GENERIC_TOKENS = set(STOPWORDS).union({
    "ability", "abilities", "able", "candidate", "essential", "desirable",
    "experience", "experienced", "skill", "skills", "strong", "excellent",
    "good", "knowledge", "proficiency", "proficient", "required",
})

def _requirement_meaningful_tokens(norm: str) -> set[str]:
    return {
        token
        for token in str(norm or "").split()
        if token not in REQUIREMENT_MERGE_GENERIC_TOKENS and len(token) > 2
    }


def _requirements_substantially_overlap(norm: str, existing_norm: str) -> bool:
    tokens = _requirement_meaningful_tokens(norm)
    existing_tokens = _requirement_meaningful_tokens(existing_norm)
    if not tokens or not existing_tokens:
        return False
    overlap = tokens.intersection(existing_tokens)
    smaller = min(len(tokens), len(existing_tokens))
    return smaller >= 2 and len(overlap) / smaller >= 0.8


def merge_job_requirements(primary: List[dict], supplemental: List[dict], limit: int = 35) -> List[dict]:
    merged: List[dict] = []
    seen: set[str] = set()
    for req in [*(primary or []), *(supplemental or [])]:
        if not isinstance(req, dict):
            continue
        text = str(req.get("text") or "").strip()
        cleaned = _requirement_dict(
            text,
            req.get("category") or "essential",
            in_candidate_section=True,
        )
        if not cleaned:
            continue
        norm = cleaned["normalized"]
        if not norm or norm in seen:
            continue
        norm_tokens = norm.split()
        replace_indexes: set[int] = set()
        subsumed = False
        for idx, existing in enumerate(merged):
            existing_norm = existing.get("normalized") or normalize_phrase(existing.get("text") or "")
            existing_tokens = existing_norm.split()
            if len(norm_tokens) >= 3 and norm in existing_norm:
                subsumed = True
                break
            if len(existing_tokens) >= 3 and existing_norm in norm and len(norm_tokens) > len(existing_tokens):
                replace_indexes.add(idx)
            if _requirements_substantially_overlap(norm, existing_norm):
                if len(norm_tokens) <= len(existing_tokens):
                    subsumed = True
                    break
                replace_indexes.add(idx)
        if subsumed:
            continue
        for idx in sorted(replace_indexes, reverse=True):
            seen.discard(merged[idx].get("normalized") or normalize_phrase(merged[idx].get("text") or ""))
            merged.pop(idx)
        seen.add(norm)
        merged.append(cleaned)
        if len(merged) >= limit:
            break
    return merged


def _requirements_by_category(requirements: List[dict]) -> dict:
    essential = []
    nice_to_have = []
    for req in requirements or []:
        text = str((req or {}).get("text") or "").strip()
        if not text:
            continue
        if (req or {}).get("category") == "nice_to_have":
            nice_to_have.append(text)
        else:
            essential.append(text)
    return {
        "essential": essential,
        "nice_to_have": nice_to_have,
    }


def _cleaned_job_description_from_requirements(requirements: List[dict]) -> str:
    lines = []
    for req in requirements or []:
        text = str((req or {}).get("text") or "").strip()
        if not text:
            continue
        lines.append(text)
    return "\n".join(lines)


def preflight_job_requirements(job_description: str, limit: int = 35) -> dict:
    """API-backed JD preflight used before CV parsing/matching.

    It extracts candidate-owned essentials/desirables, removes company/perk prose,
    and produces a clean JD slice for downstream requirement and keyword judging.
    """
    job_description = clean_text(job_description)
    if not job_description:
        raise ValueError("Job description is empty.")

    effective_limit = max(limit, 35)
    local_requirements = extract_local_job_requirements(job_description, limit=effective_limit)
    local_ats = _local_ats_keyword_candidates(job_description)
    source_sentences = build_jd_source_sentences(job_description)

    if not GENAI_CLIENT:
        merged = merge_job_requirements([], local_requirements, limit=effective_limit)
        cleaned_jd = _cleaned_job_description_from_requirements(merged)
        return {
            "source": "local",
            "cleaned_job_description": cleaned_jd,
            "requirements": merged,
            "requirements_by_category": _requirements_by_category(merged),
            "ats_keywords": {
                "hard_skills": [{"skill": skill, "source": "local"} for skill in local_ats["hard"][:12]],
                "soft_skills": [{"skill": skill, "source": "local"} for skill in local_ats["soft"][:8]],
            },
            "quality": {
                "makes_sense": bool(merged),
                "confidence": "medium" if merged else "low",
                "issues": [] if merged else ["No candidate-owned requirements were found."],
                "excluded_noise": [],
            },
        }

    prompt = (
        "You are a strict job-description preflight judge. Nominate only source sentence IDs that contain candidate-owned hiring requirements.\n\n"
        "Return ONLY valid JSON with this exact shape:\n"
        "{\n"
        '  "items": [{\n'
        '    "requirement": "concise description",\n'
        '    "source_sentence_id": "s1",\n'
        '    "owner": "candidate|company|team|product|role",\n'
        '    "type": "candidate_skill|candidate_experience|candidate_qualification|candidate_behaviour|candidate_responsibility|candidate_tool|non_scoreable",\n'
        '    "scoreable_against_cv": true,\n'
        '    "category": "essential|nice_to_have",\n'
        '    "confidence": 0.95\n'
        "  }],\n"
        '  "ats_keywords": {\n'
        '    "hard_skills": ["Python", "Kafka"],\n'
        '    "soft_skills": ["collaboration"]\n'
        "  },\n"
        '  "quality": {\n'
        '    "makes_sense": true,\n'
        '    "confidence": "high|medium|low",\n'
        '    "issues": ["short issue if extraction looks weak"],\n'
        '    "excluded_noise": ["company background/perk phrase you deliberately ignored"]\n'
        "  }\n"
        "}\n\n"
        "Rules:\n"
        "- Every item must cite exactly one source_sentence_id from the provided records.\n"
        "- Never invent, combine, or alter source_sentence_id values.\n"
        "- essentials are mandatory requirements, responsibilities, qualifications, skills, tools, or behaviours.\n"
        "- nice_to_have means explicitly preferred, desirable, bonus, advantage, or plus.\n"
        "- Exclude company history, mission, size, benefits, perks, salary, location/flexibility, application instructions, and generic marketing copy.\n"
        "- Do not include company names or the job title as a requirement.\n"
        "- Set scoreable_against_cv true only when evidence could reasonably appear in a CV.\n"
        "- ATS keywords must be high-signal skills/tools/domains/behaviours from the kept requirements only.\n"
        f"- Return at most {limit} requirements.\n\n"
        f"SOURCE SENTENCE RECORDS:\n{json.dumps(source_sentences, ensure_ascii=True)}"
    )

    try:
        response = _genai_generate(
            model=GEMINI_PARSE_MODEL,
            contents=prompt,
            config=gemini_generation_config(0, response_mime_type="application/json"),
        )
        parsed = parse_json_response(getattr(response, "text", "") or "")
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected preflight response shape")
    except Exception as exc:
        logger.warning("Gemini JD preflight failed; API-backed analysis is required: %s", exc)
        raise

    generated_items = parsed.get("items") or []
    if not generated_items and parsed.get("requirements"):
        source_id_by_text = {
            normalize_phrase(source.get("text") or ""): source.get("sentence_id")
            for source in source_sentences
        }
        generated_items = [
            {
                "source_sentence_id": source_id_by_text.get(normalize_phrase(req.get("text") or "")),
                "owner": "candidate",
                "type": "candidate_responsibility",
                "scoreable_against_cv": True,
                "category": req.get("category") or "essential",
            }
            for req in parsed.get("requirements") or []
            if isinstance(req, dict)
        ]
    generated_reqs, verifier_rejections = verify_grounded_job_requirements(
        generated_items,
        source_sentences,
        limit=effective_limit,
    )
    merged = merge_job_requirements(generated_reqs, local_requirements, limit=effective_limit)
    cleaned_jd = _cleaned_job_description_from_requirements(merged)
    candidate_blob = normalize_phrase(cleaned_jd) or _candidate_requirement_text_blob(job_description)

    def clean_keyword_list(values, limit_count):
        out = []
        seen = set()
        for value in values or []:
            skill = clean_model_skill_name(str(value or ""))
            norm = normalize_phrase(skill)
            if not skill or norm in seen:
                continue
            if not is_valid_model_skill(skill, candidate_blob):
                continue
            seen.add(norm)
            out.append({"skill": skill, "source": "preflight"})
            if len(out) >= limit_count:
                break
        return out

    raw_ats = parsed.get("ats_keywords") or {}
    hard = clean_keyword_list(raw_ats.get("hard_skills"), 12)
    soft = clean_keyword_list(raw_ats.get("soft_skills"), 8)
    soft = [
        item
        for idx, item in enumerate(soft)
        if _soft_ats_keyword_key(item["skill"])
        and _soft_ats_keyword_key(item["skill"])
        not in {_soft_ats_keyword_key(previous["skill"]) for previous in soft[:idx]}
    ]
    for skill in local_ats["hard"]:
        if len(hard) >= 12:
            break
        norm = normalize_phrase(skill)
        if norm and not any(normalize_phrase(item["skill"]) == norm for item in hard):
            hard.append({"skill": skill, "source": "local"})
    for skill in local_ats["soft"]:
        if len(soft) >= 8:
            break
        key = _soft_ats_keyword_key(skill)
        if key and not any(_soft_ats_keyword_key(item["skill"]) == key for item in soft):
            soft.append({"skill": skill, "source": "local"})

    quality = parsed.get("quality") if isinstance(parsed.get("quality"), dict) else {}
    issues = [str(item).strip() for item in quality.get("issues") or [] if str(item).strip()]
    if not merged:
        issues.append("No candidate-owned requirements survived cleanup.")
    return {
        "source": "gemini",
        "cleaned_job_description": cleaned_jd,
        "requirements": merged,
        "requirements_by_category": _requirements_by_category(merged),
        "ats_keywords": {
            "hard_skills": hard,
            "soft_skills": soft,
        },
        "quality": {
            "makes_sense": bool(merged) and bool(quality.get("makes_sense", True)),
            "confidence": str(quality.get("confidence") or ("high" if merged else "low")),
            "issues": merge_unique(issues),
            "excluded_noise": [
                str(item).strip()
                for item in [*(quality.get("excluded_noise") or []), *verifier_rejections]
                if str(item).strip()
            ][:12],
        },
    }


def cached_preflight_job_requirements(job_description: str, limit: int = 35) -> dict:
    """`preflight_job_requirements` with a JD-keyed, version-scoped cache.

    Preflight output is a pure function of (job_description, limit, SCORER_VERSION), so a
    cache hit is byte-identical to recomputation — quality is unchanged. It removes the
    ~12s Gemini preflight call from repeat analyses of the same JD (screening several CVs
    against one role, or a retry after a failed downstream step). The raw
    `preflight_job_requirements` stays uncached so unit tests exercise it directly.
    """
    cleaned = clean_text(job_description)
    if not cleaned:
        return preflight_job_requirements(job_description, limit)

    cache_key = analysis_cache.secondary_cache_key(
        "jd_preflight", {"jd": cleaned, "limit": int(limit)}
    )
    cached = analysis_cache.get_cached_secondary_response(cache_key, "jd_preflight")
    if cached is not None:
        return cached

    result = preflight_job_requirements(job_description, limit)
    # Only cache a high-confidence API extraction; never freeze the local (no-key) fallback
    # or an empty extraction, so a transient run can't poison later requests.
    if result.get("source") == "gemini" and result.get("requirements"):
        analysis_cache.set_cached_secondary_response(cache_key, "jd_preflight", result)
    return result


def extract_job_responsibilities(job_description: str, limit: int = 25) -> List[dict]:
    """Extract essential + nice-to-have requirements from a JD using Gemini."""
    effective_limit = max(limit, 35)
    local_requirements = extract_local_job_requirements(job_description, limit=effective_limit)
    if not GENAI_CLIENT:
        raise RuntimeError("Gemini API is required for requirement extraction.")
    if GENAI_CLIENT:
        try:
            response = _genai_generate(
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
                    "- Include candidate-owned delivery expectations, technical responsibilities, and demonstrable behaviours.\n"
                    "- Exclude entirely: company benefits, perks, salary, equity, flexible working, onboarding, about-the-company text, application process, contract/location text, and selection process text.\n"
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
                        return merge_job_requirements(result, local_requirements, limit=effective_limit)
        except Exception as exc:
            logger.warning("Gemini requirement extraction failed; API-backed analysis is required: %s", exc)
            raise

    raise RuntimeError("Gemini returned no candidate-owned requirements.")


ATOMIC_REQUIREMENT_HINTS = (
    "especially",
    "including",
    "such as",
    "with",
    "using",
)


STRICT_TOOL_TERMS = {
    "python",
    "fastapi",
    "postgres",
    "postgresql",
    "snowflake",
    "aws",
    "azure",
    "gcp",
    "rust",
    "grpc",
    "rest",
    "rest api",
    "microservices",
    "aws lambda",
    "step functions",
    "github actions",
    "ci/cd",
    "ci cd",
    "ci-cd",
    "cicd",
    "continuous integration/deployment",
    "continuous integration and deployment",
    "ecs",
    "aws ecs",
    "terraform",
    "excel",
    "word",
    "outlook",
    "sage",
    "proaudit",
    "react",
    "react native",
    "typescript",
    "javascript",
    "node",
    "node.js",
    "node js",
    "nodejs",
    "docker",
    "kubernetes",
    "c++",
    "c#",
    "c sharp",
    "csharp",
    ".net",
    "dotnet",
    "dot net",
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
    "content management system",
    "cms",
    "seo",
    "google analytics",
    "reporting tools",
    "adobe indesign",
    "indesign",
    "qts",
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
    "business administration": ("business administration", "mba"),
    "nursing": ("nursing",),
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
    has_undergraduate_option = any(
        _phrase_present_in_normalized_text(normalize_phrase(term), req_norm)
        for term in UNDERGRADUATE_DEGREE_TERMS
    )
    if has_undergraduate_option and " or " in f" {req_norm} ":
        return False
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
    "financial statement", "financial statements", "balance sheet", "income statement",
    "quarterly reporting", "annual reporting", "management reporting",
    "external reporting",
)

AUDIT_STRICT_TERMS = (
    "audit file", "audit highlights memorandum", "management letter",
    "letter of representation", "finalisation checklist", "finalisation checklists",
    "audit regulation", "auditing standards", "accounting standards",
    "statutory accounts", "consolidated accounts", "working papers", "cpd",
)

CONFIDENTIAL_INFORMATION_TERMS = (
    "confidential information", "confidential data", "sensitive information",
    "sensitive data", "personal information", "personal data", "student information",
    "student data", "partner information", "data protection", "privacy", "gdpr",
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

    language_hit = tokens.intersection(NATURAL_LANGUAGE_SKILLS)
    if language_hit and (
        {"proficiency", "proficient", "fluency", "fluent", "language", "spoken", "speaking", "written", "writing", "read", "reading"}.intersection(tokens)
        or re.search(r"\bproficiency\s+in\s+\w+\b", req_norm)
    ):
        policy.update({
            "type": "language",
            "strict": True,
            "allowed_sections": {"skills", "experience", "education", "summary"},
        })
        return policy

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

    if any(term in req_norm for term in AUDIT_STRICT_TERMS):
        policy.update({
            "type": "audit_specific",
            "strict": True,
            "allowed_sections": {"skills", "experience", "projects", "certifications"},
        })
        return policy

    if any(term in req_norm for term in CONFIDENTIAL_INFORMATION_TERMS):
        policy.update({
            "type": "confidential_information",
            "strict": True,
            "allowed_sections": {"skills", "experience", "projects", "certifications"},
        })
        return policy

    if any(term in req_norm for term in ("incident investigation", "incident investigations", "root cause", "root-cause")):
        policy.update({
            "type": "incident_root_cause",
            "strict": True,
            "allowed_sections": {"experience", "projects"},
        })
        return policy

    if exact_alias_key(requirement) or any(
        _phrase_present_in_normalized_text(normalize_phrase(term), req_norm)
        for term in STRICT_TOOL_TERMS
    ):
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
    is_early_career_exposure = any(
        term in req_norm
        for term in (
            "exposure to coding", "exposure to scripting", "coding or scripting",
            "academic project", "academic projects", "personal project", "personal projects",
            "internship", "internships", "placement", "placements", "early professional",
        )
    )
    if is_early_career_exposure and (
        is_early_career_range
        or any(term in req_norm for term in ("coding", "scripting", "project", "internship", "placement"))
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

    if any(
        _phrase_present_in_normalized_text(normalize_phrase(term), req_norm)
        for term in (
            "people management",
            "line management",
            "managed team",
            "manage team",
            "mentoring",
            "mentor",
            "supervise",
            "supervising",
            "review junior",
            "review the work of junior",
            "delegate",
            "junior members",
            "support and develop junior",
        )
    ):
        policy.update({
            "type": "management",
            "strict": True,
            "allowed_sections": {"experience"},
        })
        return policy

    if any(term in req_norm for term in ("fluency", "fluent", "language proficiency")):
        policy.update({
            "type": "language",
            "allowed_sections": {"skills", "experience", "education", "summary"},
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
    if policy["type"] == "language":
        return [term for term in NATURAL_LANGUAGE_SKILLS if term in req_norm]
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
    if policy["type"] == "audit_specific":
        return [term for term in AUDIT_STRICT_TERMS if term in req_norm] or list(AUDIT_STRICT_TERMS)
    if policy["type"] == "confidential_information":
        return [term for term in CONFIDENTIAL_INFORMATION_TERMS if term in req_norm] or list(CONFIDENTIAL_INFORMATION_TERMS)
    if policy["type"] == "incident_root_cause":
        return [
            term
            for term in ("incident investigation", "incident investigations", "root cause", "root-cause")
            if term in req_norm
        ] or ["incident investigation", "root cause"]
    if policy["type"] == "exact_tool":
        explicit_tool_terms = {
            "python", "fastapi", "postgres", "postgresql", "snowflake", "aws", "azure", "gcp",
            "rust", "grpc", "rest", "rest api", "microservices",
            "aws lambda", "step functions", "github actions", "ci/cd", "ci cd",
            "ecs", "aws ecs", "terraform",
            "excel", "word", "outlook", "sage", "proaudit", "react", "react native",
            "typescript", "javascript", "docker", "kubernetes", "c++", "c#",
            "cpp", "lakehouse", "messaging middleware",
            "content management system", "cms", "seo", "google analytics",
            "reporting tools", "adobe indesign", "indesign", "qts",
            "node", "node.js", "node js", "nodejs", ".net", "dotnet", "dot net",
            "c sharp", "csharp", "ci-cd", "cicd",
            "continuous integration/deployment", "continuous integration and deployment",
        }
        matches = [
            term for term in explicit_tool_terms
            if _phrase_present_in_normalized_text(normalize_phrase(term), req_norm)
        ]
        filtered = [
            term for term in matches
            if not any(
                term != other
                and normalize_phrase(term) != normalize_phrase(other)
                and normalize_phrase(term) in normalize_phrase(other)
                for other in matches
            )
        ]
        if "content management system" in filtered or "cms" in filtered:
            filtered.extend(["content management system", "cms"])
        if "adobe indesign" in filtered or "indesign" in filtered:
            filtered.extend(["adobe indesign", "indesign"])
        aliases = []
        for term in filtered:
            aliases.extend(exact_aliases(term))
        return merge_unique([*filtered, *aliases])
    return []


GENERAL_EVIDENCE_STOPWORDS = set(STOPWORDS).union({
    "ability", "abilities", "candidate", "demonstrate", "demonstrated",
    "excellent", "experience", "experienced", "essential", "desirable",
    "familiarity", "good", "hands", "interest", "knowledge", "passion",
    "proven", "skill", "skills", "solid", "strong", "understanding",
    "working", "work", "works", "make", "sure", "line", "responsible",
    "responsibly", "information", "using", "use", "used",
})


def _non_strict_evidence_has_signal(requirement: str, evidence_norm: str) -> bool:
    if any(
        _phrase_present_in_normalized_text(normalize_phrase(alias), evidence_norm)
        for alias in _requirement_aliases(requirement)
    ):
        return True
    shared_actions = _general_action_families(requirement).intersection(
        _general_action_families(evidence_norm)
    )
    return bool(shared_actions.difference({"deliver"}))


def _simple_word_stem(token: str) -> str:
    token = str(token or "").lower()
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ied"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _core_capability_tokens(text: str) -> set[str]:
    context_tokens = {
        "degree", "engineering", "feature", "features", "project", "projects",
        "run", "solution", "solutions", "system", "systems", "team", "technology",
        "technologies",
    }
    return {
        _simple_word_stem(token)
        for token in normalize_phrase(text).split()
        if token not in GENERAL_EVIDENCE_STOPWORDS
        and token not in GENERAL_ACTION_TOKEN_TO_FAMILY
        and token not in context_tokens
        and token not in {"high", "level", "modern", "specific", "specifically", "robust", "scalable"}
        and len(token) > 2
    }


def _canonical_capability_keys(text: str) -> set[str]:
    norm = normalize_phrase(text)
    return {
        concept
        for concept, definition in CANONICAL_CAPABILITY_TAXONOMY.items()
        if any(
            _phrase_present_in_normalized_text(normalize_phrase(alias), norm)
            for alias in definition["aliases"]
        )
    }


def _has_action_evidence(requirement: str, evidence_norm: str) -> bool:
    evidence_tokens = set(evidence_norm.split())
    if _general_action_families(evidence_norm) or evidence_tokens.intersection(_ACTION_VERBS_SET):
        return True
    requirement_tokens = normalize_phrase(requirement).split()
    if not requirement_tokens:
        return False
    first_token = requirement_tokens[0]
    first_stem = _simple_word_stem(first_token)
    return len(first_stem) >= 4 and any(
        _simple_word_stem(token) == first_stem
        or token.startswith(first_token)
        or first_token.startswith(_simple_word_stem(token))
        for token in evidence_tokens
    )


def _general_capability_evidence_confidence(requirement: str, evidence_text: str) -> str | None:
    section = infer_evidence_section(evidence_text)
    if section == "skills":
        return None

    req_norm = normalize_phrase(requirement)
    evidence_norm = normalize_phrase(evidence_text)
    action_proof = _has_action_evidence(requirement, evidence_norm) and section in {"experience", "projects"}
    project_proof = section == "projects"

    target_capabilities = _canonical_capability_keys(requirement)
    for group in CAPABILITY_EVIDENCE_GROUPS:
        if group["concept"] not in target_capabilities:
            continue
        signal_hit = any(
            _phrase_present_in_normalized_text(normalize_phrase(signal), evidence_norm)
            for signal in group["signals"]
        )
        if signal_hit and action_proof:
            return "strong"
        if signal_hit and project_proof:
            return "partial"

    req_tokens = _core_capability_tokens(requirement)
    if not req_tokens:
        return None
    evidence_tokens = {_simple_word_stem(token) for token in evidence_norm.split()}
    overlap = req_tokens.intersection(evidence_tokens)
    if len(req_tokens) <= 2:
        lexical_proof = len(overlap) == len(req_tokens)
    else:
        lexical_proof = len(overlap) >= 2 and len(overlap) / len(req_tokens) >= 0.4
    if not lexical_proof:
        return None
    if action_proof:
        return "strong"
    if project_proof:
        return "partial"
    return None


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

    if policy["type"] == "language":
        req_tokens = set(normalize_phrase(requirement).split())
        evidence_tokens = set(evidence_norm.split())
        language_tokens = req_tokens.intersection(NATURAL_LANGUAGE_SKILLS)
        proficiency_tokens = {"fluent", "fluency", "native", "proficient", "proficiency", "bilingual"}
        mode_tokens = {"written", "writing", "read", "reading", "spoken", "speaking", "verbal", "oral"}
        if language_tokens and language_tokens.issubset(evidence_tokens) and evidence_tokens.intersection(proficiency_tokens | mode_tokens):
            return {"confidence": "strong", "section": section, "policy": policy}
        return None

    if policy["type"] == "early_career_experience":
        verified = classify_requirement_evidence_match(requirement, evidence)
        if not verified:
            return None
        return {
            "confidence": verified,
            "section": section,
            "policy": policy,
        }

    explicit_terms = _policy_explicit_terms(policy, requirement)
    if policy["strict"] and explicit_terms:
        explicit_hit = any(
            _phrase_present_in_normalized_text(normalize_phrase(term), evidence_norm)
            for term in explicit_terms
        )
        if not explicit_hit:
            return None
    elif (
        policy["type"] == "general"
        and not _concept_evidence_confidence(requirement, evidence)
        and not _non_strict_evidence_has_signal(requirement, evidence_norm)
    ):
        return None

    adjusted = confidence if confidence in ("strong", "partial") else "partial"
    if policy["type"] == "professional_experience" and section in {"projects", "summary"}:
        adjusted = "partial"
    if policy["type"] == "project_management" and section in {"projects", "summary"}:
        adjusted = "partial"
    return {"confidence": adjusted, "section": section, "policy": policy}


def _split_list_fragments(text: str) -> List[str]:
    raw_parts = re.split(r",|\s+/\s+|&|\band\b|\bor\b", text, flags=re.IGNORECASE)
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
    cleaned = re.sub(
        r"^[A-Za-z][A-Za-z &/-]{2,45}:\s*",
        "",
        str(text or "").strip(),
    )
    return re.sub(
        r"^(excellent|solid|strong|modern|very strong|significant|hands on|hands-on|knowledge of|experience with|experience in|ability to|an interest in|interest in)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip(" -:;,.")


def _looks_like_atomic_noise(text: str) -> bool:
    norm = normalize_phrase(text)
    if not norm or _looks_like_requirement_heading(text):
        return True
    if norm in {"data", "intelligence", "etc"} or re.fullmatch(r"\d+%?", norm):
        return True
    core_tokens = _core_capability_tokens(text)
    if core_tokens and core_tokens.issubset({"data", "intelligence"}):
        return True
    if norm in {
        "data skills experience",
        "programming proficiency",
        "software engineering fundamentals",
        "problem solving skills",
        "communication collaboration",
        "cloud and mlops exposure",
        "core ml knowledge",
        "advanced education",
        "basic familiarity",
    }:
        return True
    if re.match(r"^learn(?:ing)?\s+from\s+senior\b", norm):
        return True
    return False


def _requirement_concept_key(requirement: str) -> str | None:
    norm = normalize_phrase(requirement)
    tokens = set(norm.split())
    if not norm:
        return None
    if any(term in norm for term in ("bachelor", "bsc", "degree", "quantitative field")):
        return "undergraduate_degree"
    if "python" in tokens:
        return "python"
    if "sql" in tokens or "relational database" in norm:
        return "sql_relational_databases"
    if "data structures" in norm or "algorithms" in tokens:
        return "data_structures_algorithms"
    if "git" in tokens or "version control" in norm or "repository tool" in norm:
        return "version_control"
    if "ci cd" in norm or "continuous integration" in norm or "continuous delivery" in norm:
        return "ci_cd"
    if "clean code" in norm or "well documented code" in norm or "documented code" in norm:
        return "clean_documented_code"
    if "problem solving" in norm or "analytical mindset" in norm or "analytical thinking" in norm:
        return "analytical_problem_solving"
    if any(term in norm for term in ("eagerness to learn", "learning new skills", "self development", "proactive learning")):
        return "learning_attitude"
    if "communication" in norm or "written and verbal" in norm or "verbal and written" in norm:
        return "communication"
    if "collaboration" in norm or "team environment" in norm or "teamwork" in norm:
        return "collaboration"
    if "aws" in tokens or "gcp" in tokens or "azure" in tokens or "cloud platform" in norm:
        return "cloud_platform"
    if "docker" in tokens or "containerization" in norm or "containerisation" in norm:
        return "containerization"
    if "model evaluation" in norm or "evaluation techniques" in norm:
        return "model_evaluation"
    if "feature engineering" in norm:
        return "feature_engineering"
    if "supervised" in tokens or "unsupervised" in tokens:
        return "supervised_unsupervised_learning"
    canonical_capabilities = _canonical_capability_keys(requirement)
    if len(canonical_capabilities) == 1:
        return next(iter(canonical_capabilities))
    return None


def _concept_evidence_confidence(requirement: str, evidence_text: str) -> str | None:
    concept = _requirement_concept_key(requirement)
    evidence_norm = normalize_phrase(evidence_text)
    evidence_tokens = set(evidence_norm.split())
    action_families = _general_action_families(evidence_norm)
    if concept == "sql_relational_databases":
        database_signals = {"sql", "mysql", "postgresql", "postgres", "sqlite", "database", "databases"}
        query_signals = {"query", "queried", "querying", "join", "joins", "joined", "relational"}
        if evidence_tokens.intersection(database_signals) and (
            evidence_tokens.intersection(query_signals) or action_families.intersection({"analyse", "deliver"})
        ):
            return "strong"
    if concept == "version_control":
        if evidence_tokens.intersection({"git", "repository", "repositories", "branch", "branches", "commit", "commits"}):
            return "strong"
    if concept == "clean_documented_code":
        if evidence_tokens.intersection({"documented", "documentation", "readme", "linting", "tests", "testing", "modular"}):
            return "strong" if action_families else "partial"
    if concept == "analytical_problem_solving":
        if action_families.intersection({"analyse", "troubleshoot"}) or evidence_tokens.intersection(
            {"requirements", "problems", "problem", "pain", "blockers", "leakage"}
        ):
            return "strong" if action_families else "partial"
    if concept == "learning_attitude":
        if evidence_tokens.intersection({
            "coursework",
            "course",
            "courses",
            "certification",
            "certifications",
            "training",
            "upskilling",
        }) or any(phrase in evidence_norm for phrase in (
            "self development",
            "self directed",
            "learned new",
            "learning new skills",
            "professional development",
        )):
            return "partial"
    if concept == "collaboration":
        if evidence_tokens.intersection({"team", "teams", "agile", "collaborated", "collaboration", "stakeholders"}):
            return "strong" if action_families else "partial"
    return None


def _looks_like_alternative_list(source: str, right: str) -> bool:
    source_norm = normalize_phrase(source)
    right_norm = normalize_phrase(right)
    return (
        any(marker in source_norm for marker in ALTERNATIVE_LIST_MARKERS)
        or f" {right_norm} ".find(" or ") != -1
    )


def _alternative_fragment_norms(fragment: str) -> set[str]:
    cleaned = _strip_requirement_lead(fragment)
    variants = {cleaned}
    stripped_suffix = re.sub(
        r"\s+(experience|familiarity|knowledge|skills?|proficiency|capability|capabilities|background)$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip(" -:;,.")
    if stripped_suffix and stripped_suffix != cleaned:
        variants.add(stripped_suffix)
    return {normalize_phrase(variant) for variant in variants if normalize_phrase(variant)}


def _add_alternative_group(groups: List[set[str]], fragments: List[str]) -> None:
    source_fragments = [fragment for fragment in fragments if normalize_phrase(fragment)]
    if len(source_fragments) < 2:
        return
    group: set[str] = set()
    for fragment in source_fragments:
        group.update(_alternative_fragment_norms(fragment))
    if len(group) >= 2:
        groups.append(group)


def _plain_or_alternative_fragments(line: str) -> List[List[str]]:
    groups: List[List[str]] = []
    for match in re.finditer(r"\bor\b", line, flags=re.IGNORECASE):
        left_context = line[: match.start()]
        right_context = line[match.end() :]
        left_parts = re.split(r"[,;:.]|\band\b", left_context, flags=re.IGNORECASE)
        right_parts = re.split(r"[,;:.]|\band\b", right_context, flags=re.IGNORECASE)
        left = _strip_requirement_lead(left_parts[-1] if left_parts else "")
        right = _strip_requirement_lead(right_parts[0] if right_parts else "")
        if left and right:
            groups.append([left, right])
    return groups


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
            _add_alternative_group(groups, fragments)
            break
        for fragments in _plain_or_alternative_fragments(line):
            _add_alternative_group(groups, fragments)
    return groups


def filter_satisfied_alternative_missing_skills(items: List[dict], job_description: str) -> List[dict]:
    groups = extract_alternative_skill_groups(job_description)
    if not groups:
        return items

    item_norms = {id(item): _skill_identity_norms(str(item.get("skill") or "")) for item in items}
    suppress_ids: set[int] = set()
    for group in groups:
        matching_items = [item for item in items if item_norms.get(id(item), set()).intersection(group)]
        if not matching_items:
            continue
        group_satisfied = any((item.get("status") or "") in {"present", "partial"} for item in matching_items)
        if group_satisfied:
            suppress_ids.update(
                id(item)
                for item in matching_items
                if (item.get("status") or "missing") == "missing"
            )
    if not suppress_ids:
        return items
    return [item for item in items if id(item) not in suppress_ids]


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


def _clone_atom_list(atoms: List[dict]) -> List[dict]:
    return [dict(atom) for atom in atoms]


def _store_requirement_atoms(source: str, atoms: List[dict]) -> List[dict]:
    bucket = _request_cache_bucket("requirement_atoms")
    if bucket is not None:
        bucket[source] = _clone_atom_list(atoms)
    return _clone_atom_list(atoms)


def decompose_requirement_text(text: str) -> List[dict]:
    """Split bundled JD requirements into atomic sub-requirements."""
    source = str(text or "").strip()
    if not source:
        return []
    bucket = _request_cache_bucket("requirement_atoms")
    if bucket is not None and source in bucket:
        return _clone_atom_list(bucket[source])

    parent_policy = _requirement_policy(source)
    parent_concept = _requirement_concept_key(source)
    if parent_policy["type"] in {
        "degree",
        "postgraduate_degree",
        "certification",
        "years_experience",
        "language",
    } or parent_concept in {"learning_attitude"}:
        normalized = normalize_phrase(source)
        return _store_requirement_atoms(source, [{
            "text": source,
            "normalized": normalized,
            "strict": parent_policy["strict"],
            "requirement_type": parent_policy["type"],
        }])

    atoms: List[str | dict] = []

    paren_chunks = re.findall(r"\(([^)]+)\)", source)
    for chunk in paren_chunks:
        fragments = _split_list_fragments(chunk)
        if _looks_like_alternative_list(source, chunk):
            _append_alternative_atoms(atoms, fragments, f"any:{normalize_phrase(chunk)}")
        else:
            atoms.extend(fragments)
    without_parens = re.sub(r"\([^)]*\)", "", source).strip(" ,;")

    marker_matched = False
    for marker in ATOMIC_REQUIREMENT_HINTS:
        pattern = rf"^(.*?)(?:\b{re.escape(marker)}\b)(.+)$"
        match = re.search(pattern, without_parens, flags=re.IGNORECASE)
        if not match:
            continue
        left = _strip_requirement_lead(match.group(1))
        right = match.group(2).strip(" -:;,.")
        if _looks_like_alternative_list(source, right):
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
            if " or " in f" {normalize_phrase(stripped)} ":
                _append_alternative_atoms(atoms, split_atoms, f"any:{normalize_phrase(stripped)}")
            else:
                atoms.extend(split_atoms)
        elif stripped:
            atoms.append(stripped)

    normalized_parent = normalize_phrase(source)
    normalized_atoms = []
    seen = set()
    for atom in atoms:
        if isinstance(atom, dict):
            norm = atom.get("normalized") or normalize_phrase(atom.get("text"))
            if not norm or norm == normalized_parent or norm in seen or _looks_like_atomic_noise(atom.get("text") or ""):
                continue
            seen.add(norm)
            normalized_atoms.append(atom)
            continue

        atom = atom.strip()
        if len(atom.split()) == 1:
            atom = atom.replace(".", "")
        norm = normalize_phrase(atom)
        if not norm or norm == normalized_parent or norm in seen or _looks_like_atomic_noise(atom):
            continue
        if parent_policy["type"] == "management" and norm in {"brief"}:
            continue
        seen.add(norm)
        policy = parent_policy if parent_policy["type"] == "management" else _requirement_policy(atom)
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

    deduped_atoms = []
    seen_concepts = set()
    for atom in normalized_atoms:
        concept = _requirement_concept_key(atom.get("text") or "")
        if concept and concept in seen_concepts:
            continue
        if concept:
            seen_concepts.add(concept)
        deduped_atoms.append(atom)
    return _store_requirement_atoms(source, deduped_atoms)


def aggregate_requirement_evidence(
    requirement: str,
    parsed_resume: dict,
    resume_text: str = "",
    ai_present: bool = False,
    ai_evidence: str | None = None,
    ai_confidence: str | None = None,
    ai_atom_matches: dict[str, dict] | None = None,
) -> dict:
    """Evaluate one requirement using atomic deterministic checks, with AI as an ambiguity hint."""
    atoms = decompose_requirement_text(requirement)
    breakdown = []

    for atom in atoms:
        atom_match = (ai_atom_matches or {}).get(atom["normalized"]) or {}
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
        elif atom_match and not atom["strict"]:
            candidate_confidence = "partial"
            atom_evidence = str(atom_match.get("evidence") or "")
            validated = validate_evidence_for_requirement(atom["text"], atom_evidence, candidate_confidence)
            if validated:
                evidence = atom_evidence
                section = validated["section"]
                confidence = validated["confidence"]
                status = "partial" if confidence == "partial" else "present"
            elif (
                _requirement_policy(atom["text"])["type"]
                not in {"early_career_experience", "years_experience"}
                and infer_evidence_section(atom_evidence) in {"experience", "projects"}
                and _has_action_evidence(atom["text"], normalize_phrase(atom_evidence))
            ):
                evidence = atom_evidence
                section = infer_evidence_section(atom_evidence)
                confidence = "partial"
                status = "partial"
        elif ai_present and ai_evidence and not atom["strict"]:
            candidate_confidence = "partial" if ai_confidence not in ("strong", "partial") else ai_confidence
            validated = validate_evidence_for_requirement(atom["text"], ai_evidence, candidate_confidence)
            if validated:
                evidence = ai_evidence
                section = validated["section"]
                confidence = validated["confidence"]
                status = "partial" if confidence == "partial" else "present"

        evidence_policy = atom_evidence_policy(atom["text"])
        matched_signal_families = sorted(
            set(evidence_policy["requested_signal_families"]).intersection(
                _evidence_signal_families(evidence or "")
            )
        )
        breakdown.append({
            "requirement": atom["text"],
            "canonical_atom": _canonical_atom_name(atom["text"]),
            "status": status,
            "verification_result": status,
            "confidence": confidence,
            "evidence": evidence,
            "selected_evidence": evidence,
            "section": section,
            "strict": atom["strict"],
            "requirement_type": atom.get("requirement_type") or _requirement_policy(atom["text"])["type"],
            "group_id": atom.get("group_id"),
            "group_mode": atom.get("group_mode"),
            "alternative_group": atom.get("group_id"),
            "evidence_policy": evidence_policy,
            "matched_signal_families": matched_signal_families,
            "selected_evidence_id": atom_match.get("evidence_id"),
        })

    scoring_units = []
    grouped_items: dict[str, list[dict]] = {}
    for item in breakdown:
        group_id = item.get("group_id")
        if group_id and item.get("group_mode") == "any":
            grouped_items.setdefault(group_id, []).append(item)
        else:
            scoring_units.append({
                **item,
                "scoring_unit_id": f"atom:{item['canonical_atom']}",
                "satisfied_by": item["canonical_atom"] if item["status"] in {"present", "partial"} else None,
            })

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
        best = next((item for item in matched if item["status"] == "present" and item.get("evidence")), None)
        best = best or next((item for item in matched if item.get("evidence")), None)
        scoring_units.append({
            "scoring_unit_id": group_id,
            "requirement": " / ".join(item["requirement"] for item in items),
            "canonical_atom": " / ".join(item["canonical_atom"] for item in items),
            "status": group_status,
            "verification_result": group_status,
            "confidence": group_confidence,
            "evidence": best.get("evidence") if best else None,
            "selected_evidence": best.get("evidence") if best else None,
            "section": best.get("section") if best else None,
            "strict": any(item.get("strict") for item in items),
            "requirement_type": "alternative_group",
            "group_id": group_id,
            "group_mode": "any",
            "alternative_group": group_id,
            "satisfied_by": best.get("canonical_atom") if best else None,
        })

    present_count = sum(1 for item in scoring_units if item["status"] == "present")
    partial_count = sum(1 for item in scoring_units if item["status"] == "partial")
    matched_count = present_count + partial_count
    total_count = max(1, len(scoring_units))
    missing_count = total_count - matched_count

    if matched_count == total_count and partial_count == 0:
        overall_status = "present"
        overall_confidence = "strong"
        best = next((item for item in scoring_units if item["evidence"]), None)
        best_evidence = best["evidence"] if best else None
        best_section = best["section"] if best else None
    elif matched_count > 0:
        overall_status = "partial"
        overall_confidence = "partial"
        best = next((item for item in scoring_units if item["status"] in ("present", "partial") and item["evidence"]), None)
        best_evidence = best["evidence"] if best else None
        best_section = best["section"] if best else None
    else:
        overall_status = "missing"
        overall_confidence = "missing"
        best_evidence = None
        best_section = None

    coverage_points = present_count + (partial_count * 0.5)
    coverage_ratio = coverage_points / total_count
    return {
        "original_requirement": requirement,
        "status": overall_status,
        "present": overall_status != "missing",
        "confidence": overall_confidence,
        "cv_where": best_evidence,
        "section": best_section,
        "matched_count": matched_count,
        "missing_count": missing_count,
        "total_count": total_count,
        "coverage_ratio": round(coverage_ratio, 3),
        "final_score": round(coverage_ratio, 3),
        "matched_scoring_units": [
            item["scoring_unit_id"]
            for item in scoring_units
            if item["status"] in {"present", "partial"}
        ],
        "missing_scoring_units": [
            item["scoring_unit_id"]
            for item in scoring_units
            if item["status"] == "missing"
        ],
        "scoring_units": scoring_units,
        "atomic_breakdown": breakdown,
    }


SKILL_AUGMENT_ALLOWED_TYPES = {
    "degree",
    "postgraduate_degree",
    "certification",
    "language",
    "exact_tool",
    "communication",
    "confidential_information",
    "regulated_process",
    "control_or_governance",
    "reporting",
    "audit_specific",
    "incident_root_cause",
    "years_experience",
    "professional_experience",
    "domain_exposure",
    "data_quality",
}


def _skill_identity_norms(skill: str) -> set[str]:
    norms = {normalize_phrase(skill)}
    norms.update(normalize_phrase(alias) for alias in _requirement_aliases(skill))
    concept = _requirement_concept_key(skill)
    if concept:
        norms.add(f"concept:{concept}")
    return {norm for norm in norms if norm}


def _skill_item(
    skill: str,
    parsed_resume: dict,
    resume_text: str,
) -> dict:
    aggregate = aggregate_requirement_evidence(skill, parsed_resume, resume_text)
    return {
        "skill": skill,
        "present": aggregate["present"],
        "status": aggregate["status"],
        "cv_where": aggregate["cv_where"],
        "matched_count": aggregate["matched_count"],
        "total_count": aggregate["total_count"],
        "atomic_breakdown": aggregate["atomic_breakdown"],
        "evidence_source": "deterministic",
    }


def _dedupe_skill_items(items: List[dict]) -> List[dict]:
    deduped: List[dict] = []
    identity_sets: List[set[str]] = []
    status_rank = {"missing": 0, "partial": 1, "present": 2}
    for item in items or []:
        identities = _skill_identity_norms(str(item.get("skill") or ""))
        match_index = next(
            (idx for idx, existing in enumerate(identity_sets) if identities.intersection(existing)),
            None,
        )
        if match_index is None:
            deduped.append(item)
            identity_sets.append(set(identities))
            continue
        existing = deduped[match_index]
        existing_status = existing.get("status") or ("present" if existing.get("present") else "missing")
        item_status = item.get("status") or ("present" if item.get("present") else "missing")
        if status_rank.get(item_status, 0) > status_rank.get(existing_status, 0):
            deduped[match_index] = item
        identity_sets[match_index].update(identities)
    return deduped


def _local_skill_category(skill: str, local_requirements: List[dict], job_description: str = "") -> str:
    skill_norm = normalize_phrase(skill)
    for req in local_requirements:
        req_norm = req.get("normalized") or normalize_phrase(req.get("text") or "")
        if not req_norm:
            continue
        if _phrase_present_in_normalized_text(skill_norm, req_norm) or _requirements_substantially_overlap(skill_norm, req_norm):
            return req.get("category") or "essential"
    for raw_line in str(job_description or "").splitlines():
        line_norm = normalize_phrase(raw_line)
        if not line_norm or not _phrase_present_in_normalized_text(skill_norm, line_norm):
            continue
        if any(marker in line_norm for marker in NICE_SECTION_HEADERS) or any(
            marker in line_norm for marker in ("desirable", "preferred", "nice to have", "bonus")
        ):
            return "nice_to_have"
    return "essential"


def _local_requirement_skill_candidates(job_description: str, limit: int = 40) -> List[dict]:
    local_requirements = extract_local_job_requirements(job_description, limit=80)
    candidate_blob = _candidate_requirement_text_blob(job_description)
    candidates: List[dict] = []
    seen: set[str] = set()

    def add(skill: str, category: str, source: str) -> None:
        cleaned = clean_model_skill_name(str(skill or ""))
        norm = normalize_phrase(cleaned)
        if not norm or norm in seen:
            return
        if not is_valid_model_skill(cleaned, candidate_blob):
            return
        if _ats_term_is_negated(norm, candidate_blob):
            return
        seen.add(norm)
        candidates.append({
            "skill": _display_ats_keyword(cleaned),
            "category": category if category in {"essential", "nice_to_have"} else "essential",
            "source": source,
        })

    local_ats = _local_ats_keyword_candidates(job_description)
    for skill in local_ats.get("hard", []):
        add(skill, _local_skill_category(skill, local_requirements, job_description), "local_ats_hard")
    for skill in local_ats.get("soft", []):
        add(skill, _local_skill_category(skill, local_requirements, job_description), "local_ats_soft")

    for req in local_requirements:
        category = req.get("category") or "essential"
        for atom in decompose_requirement_text(req.get("text") or ""):
            text = str(atom.get("text") or "").strip()
            norm = atom.get("normalized") or normalize_phrase(text)
            if not text or not norm:
                continue
            policy_type = atom.get("requirement_type") or _requirement_policy(text)["type"]
            token_count = len(norm.split())
            starts_action = norm.split()[0] in (_ACTION_VERBS_SET | JD_REQUIREMENT_ACTION_VERBS)
            if starts_action and policy_type == "general":
                continue
            if token_count > 8 and policy_type not in SKILL_AUGMENT_ALLOWED_TYPES:
                continue
            if policy_type == "general" and token_count > 5 and not _looks_like_soft_ats_keyword(text):
                continue
            if _looks_like_noisy_ats_fragment(text) and policy_type not in SKILL_AUGMENT_ALLOWED_TYPES:
                continue
            add(text, category, "local_requirement")
            if len(candidates) >= limit:
                return candidates

    return candidates[:limit]


def _augment_skills_with_local_requirements(
    skills_result: dict,
    job_description: str,
    parsed_resume: dict,
    resume_text: str,
    limit_per_bucket: int = 35,
) -> dict:
    must = list((skills_result or {}).get("must_have") or [])
    nice = list((skills_result or {}).get("nice_to_have") or [])

    def item_norms(item: dict) -> set[str]:
        return _skill_identity_norms(str(item.get("skill") or ""))

    must_norms = set().union(*(item_norms(item) for item in must)) if must else set()
    nice_norms = set().union(*(item_norms(item) for item in nice)) if nice else set()

    for candidate in _local_requirement_skill_candidates(job_description):
        skill = candidate["skill"]
        category = candidate.get("category") or "essential"
        norms = _skill_identity_norms(skill)
        if not norms:
            continue
        if norms.intersection(must_norms):
            continue
        if category != "nice_to_have" and norms.intersection(nice_norms):
            moved = []
            kept_nice = []
            for item in nice:
                if norms.intersection(item_norms(item)):
                    moved.append(item)
                else:
                    kept_nice.append(item)
            nice = kept_nice
            nice_norms = set().union(*(item_norms(item) for item in nice)) if nice else set()
            for item in moved:
                must.append(item)
                must_norms.update(item_norms(item))
            continue
        if norms.intersection(nice_norms):
            continue
        item = _skill_item(skill, parsed_resume, resume_text)
        if category == "nice_to_have":
            nice.append(item)
            nice_norms.update(norms)
        else:
            must.append(item)
            must_norms.update(norms)

    deduped_must = _dedupe_skill_items(must)
    deduped_nice = _dedupe_skill_items(nice)
    return {
        "must_have": filter_satisfied_alternative_missing_skills(deduped_must[:limit_per_bucket], job_description),
        "nice_to_have": filter_satisfied_alternative_missing_skills(deduped_nice[:limit_per_bucket], job_description),
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
    aliases.extend(normalize_phrase(alias) for alias in exact_aliases(requirement))
    for canonical, values in TECH_SKILL_ALIASES.items():
        normalized_values = [normalize_phrase(v) for v in values]
        if req_norm == canonical or req_norm in normalized_values:
            aliases.extend(normalized_values)
    return merge_unique([alias for alias in aliases if alias])


def _canonical_atom_name(atom: str) -> str:
    exact = exact_alias_key(atom)
    if exact:
        return exact
    concept = _requirement_concept_key(atom)
    return concept or normalize_phrase(atom)


def _evidence_signal_families(text: str) -> set[str]:
    norm = normalize_phrase(text)
    return {
        family
        for family, signals in EVIDENCE_SIGNAL_FAMILIES.items()
        if any(
            _phrase_present_in_normalized_text(normalize_phrase(signal), norm)
            for signal in signals
        )
    }


def atom_evidence_policy(requirement: str) -> dict:
    policy = _requirement_policy(requirement)
    norm = normalize_phrase(requirement)
    requested_families = set()
    for hint, families in CAPABILITY_POLICY_HINTS.items():
        if _phrase_present_in_normalized_text(normalize_phrase(hint), norm):
            requested_families.update(families)

    if policy["type"] == "exact_tool":
        policy_type = "exact_tool"
    elif policy["type"] in {"early_career_experience", "years_experience"}:
        policy_type = "formal_or_early_career_experience"
    elif requested_families:
        policy_type = "broad_capability" if len(requested_families) >= 2 else "narrow_capability"
    elif policy["type"] in {"communication", "management"}:
        policy_type = "behaviour"
    else:
        policy_type = policy["type"]

    requires_action = policy_type in {"broad_capability", "narrow_capability", "behaviour"}
    if "devops" in norm or policy_type == "exact_tool":
        requires_action = False
    minimum_signals = 2 if policy_type == "broad_capability" else 1
    return {
        "policy_type": policy_type,
        "requested_signal_families": sorted(requested_families),
        "minimum_signal_families": minimum_signals,
        "requires_action_evidence": requires_action,
        "allowed_sections": sorted(policy["allowed_sections"]),
    }


def verify_evidence_policy(requirement: str, evidence: str) -> dict | None:
    policy = atom_evidence_policy(requirement)
    requested = set(policy["requested_signal_families"])
    if not requested:
        return None
    evidence_families = _evidence_signal_families(evidence)
    matched_families = requested.intersection(evidence_families)
    if len(matched_families) < policy["minimum_signal_families"]:
        return None
    section = infer_evidence_section(evidence)
    if section not in policy["allowed_sections"]:
        return None
    if policy["requires_action_evidence"] and not (
        _has_action_evidence(requirement, normalize_phrase(evidence))
        and section in {"experience", "projects"}
    ):
        return None
    return {
        "confidence": "strong" if requested.issubset(matched_families) else "partial",
        "section": section,
        "matched_signal_families": sorted(matched_families),
        "policy": policy,
    }


STRICT_EVIDENCE_TERMS = (
    "python",
    "fastapi",
    "postgres",
    "postgresql",
    "snowflake",
    "aws",
    "azure",
    "gcp",
    "rust",
    "grpc",
    "rest",
    "rest api",
    "microservices",
    "excel",
    "word",
    "outlook",
    "sage",
    "proaudit",
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
    "content management system",
    "cms",
    "seo",
    "google analytics",
    "reporting tools",
    "adobe indesign",
    "indesign",
    "qts",
)


def requires_strict_evidence(requirement: str) -> bool:
    req_norm = normalize_phrase(requirement)
    return _requirement_policy(requirement)["strict"] or any(
        _phrase_present_in_normalized_text(normalize_phrase(term), req_norm)
        for term in STRICT_EVIDENCE_TERMS
    )


def _strip_evidence_provenance(text: str) -> str:
    """Drop a leading '[Role @ Company]' / '[Project: Name]' provenance tag.

    The bracket is metadata, not a candidate-authored achievement, yet proper nouns
    inside it (e.g. the employer 'NHS Test & Trace' or a job title) otherwise leak in
    as capability/action signals. Capability must be proven by the achievement sentence
    that follows the tag, not by the name of the employer or role.
    """
    return re.sub(r"^\s*\[[^\]]*\]\s*", "", str(text or ""))


def classify_requirement_evidence_match(requirement: str, evidence_text: str) -> str | None:
    req_norm = normalize_phrase(requirement)
    evidence_norm = normalize_phrase(evidence_text)
    if not req_norm or not evidence_norm:
        return None

    policy = _requirement_policy(requirement)
    if policy["type"] == "years_experience":
        return None
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
        formal_markers = {"internship", "internships", "intern", "placement", "placements", "graduate scheme", "graduate programme"}
        required_formal_markers = {
            marker for marker in formal_markers
            if _phrase_present_in_normalized_text(normalize_phrase(marker), req_norm)
        }
        if required_formal_markers:
            if any(
                _phrase_present_in_normalized_text(normalize_phrase(marker), evidence_norm)
                for marker in required_formal_markers
            ):
                return "strong"
            return None
        if section == "education":
            education_signals = {"bsc", "bachelor", "bachelors", "degree", "university", "graduate", "graduation"}
            if set(evidence_norm.split()).intersection(education_signals):
                return "strong"
        if section in {"projects", "experience"}:
            experience_signals = {
                "built", "developed", "implemented", "programmed",
                "coded", "coding", "scripted", "api", "apis", "application", "applications",
                "automation", "automated", "technical",
            }
            if set(evidence_norm.split()).intersection(experience_signals):
                return "strong"

    if policy["type"] == "exact_tool":
        explicit_terms = _policy_explicit_terms(policy, requirement)
        if explicit_terms and any(
            _phrase_present_in_normalized_text(normalize_phrase(term), evidence_norm)
            for term in explicit_terms
        ):
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
            "presented", "presenting", "presentation", "presentations", "reported",
            "reports", "reporting", "documentation", "documented", "explained",
            "explain", "written", "verbal", "client", "clients",
        }
        if evidence_tokens.intersection(communication_signals):
            req_tokens = set(req_norm.split())
            req_modes = {
                "written": bool({"written", "writing"}.intersection(req_tokens)),
                "verbal": bool({"verbal", "spoken", "oral"}.intersection(req_tokens)),
                "presentation": bool({"presentation", "presentations", "presenting"}.intersection(req_tokens)),
            }
            evidence_modes = {
                "written": bool({"written", "documentation", "documented", "reported", "reports", "reporting"}.intersection(evidence_tokens)),
                "verbal": bool({"verbal", "communicated", "communicate", "explained", "explain", "stakeholder", "stakeholders", "client", "clients"}.intersection(evidence_tokens)),
                "presentation": bool({"presented", "presenting", "presentation", "presentations"}.intersection(evidence_tokens)),
            }
            requested_modes = [mode for mode, requested in req_modes.items() if requested]
            if requested_modes:
                covered_modes = sum(1 for mode in requested_modes if evidence_modes.get(mode))
                if covered_modes == len(requested_modes) or covered_modes >= 2 or "communication" in evidence_tokens:
                    return "strong"
                return "partial"
            return "strong"

    if policy["type"] == "management":
        section = infer_evidence_section(evidence_text)
        if section != "experience":
            return None
        evidence_tokens = set(evidence_norm.split())
        people_management_signals = {
            "managed", "manage", "manager", "mentored", "mentor", "mentoring",
            "supervised", "supervise", "led", "lead", "hired", "coached",
        }
        people_scope_signals = {
            "team", "teams", "people", "direct", "reports", "analyst", "analysts",
            "engineer", "engineers", "staff", "hires", "performance", "reviews",
        }
        if evidence_tokens.intersection(people_management_signals) and evidence_tokens.intersection(people_scope_signals):
            return "strong"

    if policy["type"] == "incident_root_cause":
        section = infer_evidence_section(evidence_text)
        if section not in {"experience", "projects"}:
            return None
        evidence_tokens = set(evidence_norm.split())
        incident_signals = {"incident", "incidents", "outage", "outages", "postmortem", "postmortems", "root", "cause", "rca", "investigation", "investigations"}
        if evidence_tokens.intersection(incident_signals):
            return "strong"
        return None

    if policy["type"] == "language":
        req_tokens = set(req_norm.split())
        evidence_tokens = set(evidence_norm.split())
        language_tokens = req_tokens.intersection(NATURAL_LANGUAGE_SKILLS)
        if language_tokens and language_tokens.issubset(evidence_tokens):
            if evidence_tokens.intersection({
                "fluent", "fluency", "native", "proficient", "proficiency", "bilingual",
                "written", "writing", "read", "reading", "spoken", "speaking", "verbal", "oral",
            }):
                return "strong"

    if policy["type"] == "product_ownership":
        evidence_tokens = set(evidence_norm.split())
        roadmap_signals = {"roadmap", "owned", "ownership", "prioritised", "prioritized", "backlog", "release", "releases"}
        if "roadmap" in evidence_tokens and evidence_tokens.intersection(roadmap_signals):
            return "strong"

    concept_confidence = _concept_evidence_confidence(requirement, evidence_text)
    if concept_confidence:
        return concept_confidence

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

    if policy["type"] == "general":
        # Only count action families that appear in the achievement body, not in the
        # provenance tag — otherwise an employer/role proper noun (e.g. "Test & Trace"
        # -> "test" family) falsely partial-matches unrelated capabilities.
        evidence_body_norm = normalize_phrase(_strip_evidence_provenance(evidence_text))
        if _general_action_families(requirement).intersection(_general_action_families(evidence_body_norm)):
            return "partial"

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

    candidates = []
    for line in evidence_lines:
        confidence = classify_requirement_evidence_match(requirement, line)
        if confidence:
            validated = validate_evidence_for_requirement(requirement, line, confidence)
            if not validated:
                continue
            section = validated["section"]
            evidence_norm = normalize_phrase(line)
            section_score = {
                "experience": 30,
                "projects": 25,
                "education": 30,
                "certifications": 30,
                "summary": 15,
                "skills": 40,
            }.get(section, 0)
            action_score = 12 if _general_action_families(evidence_norm) else 0
            metric_score = 8 if re.search(r"\b\d+(?:\.\d+)?%?\b", line) else 0
            confidence_score = 30 if validated["confidence"] == "strong" else 15
            policy = validated["policy"]
            if policy["type"] in {"degree", "postgraduate_degree"} and section == "education":
                section_score = 50
            if policy["strict"] and section == "skills":
                section_score = 50
            if policy["type"] in {"professional_experience", "general"}:
                section_score = {
                    "experience": 45,
                    "projects": 40,
                    "education": 25,
                    "certifications": 25,
                    "summary": 20,
                    "skills": 10,
                }.get(section, section_score)
            if line.startswith("[CV]"):
                section_score = min(section_score, 5)
            candidates.append((confidence_score + section_score + action_score + metric_score, {
                "evidence": line,
                "section": section,
                "confidence": validated["confidence"],
                "requirement_type": policy["type"],
            }))

    return max(candidates, key=lambda item: item[0])[1] if candidates else None


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
    bucket = _request_cache_bucket("parsed_evidence_units")
    cache_key = id(parsed_resume)
    if bucket is not None and cache_key in bucket:
        return [dict(unit) for unit in bucket[cache_key]]
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

    if bucket is not None:
        bucket[cache_key] = [dict(unit) for unit in units]
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


def _responsibility_core_concepts(requirement: str) -> List[str]:
    concepts: List[str] = []
    seen: set[str] = set()
    for atom in decompose_requirement_text(requirement):
        label = _requirement_concept_key(atom.get("text") or "") or normalize_phrase(atom.get("text") or "")
        if not label or label in seen or _looks_like_atomic_noise(label):
            continue
        seen.add(label)
        concepts.append(label)
    return concepts[:8]


def retrieve_responsibility_evidence_candidates(
    responsibilities: List[dict],
    parsed_resume: dict,
    top_k: int = 5,
) -> dict[str, List[dict]]:
    """Retrieve plausible lines for verification. Retrieval never proves a match."""
    evidence_units = evidence_units_from_parsed(parsed_resume)
    candidates_by_requirement: dict[str, List[dict]] = {}
    for resp_index, responsibility in enumerate(responsibilities, 1):
        scored = []
        for unit in evidence_units:
            confidence = classify_requirement_evidence_match(responsibility["text"], unit["text"])
            concept_confidence = _concept_evidence_confidence(responsibility["text"], unit["text"])
            score = tfidf_similarity(responsibility["text"], unit["text"])
            if responsibility["normalized"] and responsibility["normalized"] in unit["normalized"]:
                score += 3.0
            score += {"strong": 2.5, "partial": 1.0}.get(confidence, 0.0)
            score += {"strong": 2.0, "partial": 0.75}.get(concept_confidence, 0.0)
            if unit["section"] in {"experience", "projects", "education", "certifications"}:
                score += 0.15
            if unit["section"] == "skills":
                score -= 0.1
            scored.append((score, unit))
        ranked = sorted(scored, key=lambda item: item[0], reverse=True)[:top_k]
        candidates_by_requirement[responsibility["normalized"]] = [
            {
                "candidate_id": f"r{resp_index}c{candidate_index}",
                "text": unit["text"],
                "section": unit["section"],
                "retrieval_score": round(score, 4),
            }
            for candidate_index, (score, unit) in enumerate(ranked, 1)
        ]
    return candidates_by_requirement


def retrieve_atom_evidence_candidates(
    responsibilities: List[dict],
    parsed_resume: dict,
    top_k: int = 5,
) -> tuple[List[dict], dict[str, List[dict]]]:
    """Retrieve CV-only candidate lines for each independently scoreable JD atom."""
    evidence_units = evidence_units_from_parsed(parsed_resume)
    atom_packets: List[dict] = []
    candidates_by_atom: dict[str, List[dict]] = {}
    for resp_index, responsibility in enumerate(responsibilities, 1):
        for atom_index, atom in enumerate(decompose_requirement_text(responsibility["text"]), 1):
            atom_id = f"r{resp_index}a{atom_index}"
            scored = []
            for unit in evidence_units:
                confidence = classify_requirement_evidence_match(atom["text"], unit["text"])
                score = tfidf_similarity(atom["text"], unit["text"])
                score += {"strong": 3.0, "partial": 1.25}.get(confidence, 0.0)
                if atom["normalized"] and atom["normalized"] in unit["normalized"]:
                    score += 3.0
                if unit["section"] in {"experience", "projects", "education", "certifications"}:
                    score += 0.15
                scored.append((score, unit))
            ranked = sorted(scored, key=lambda item: item[0], reverse=True)[:top_k]
            candidates = [
                {
                    "evidence_id": f"{atom_id}e{candidate_index}",
                    "text": unit["text"],
                    "section": unit["section"],
                    "retrieval_score": round(score, 4),
                }
                for candidate_index, (score, unit) in enumerate(ranked, 1)
            ]
            candidates_by_atom[atom_id] = candidates
            atom_packets.append({
                "atom_id": atom_id,
                "responsibility_index": resp_index,
                "atom": atom["text"],
                "canonical_atom": _canonical_atom_name(atom["text"]),
                "strict": atom["strict"],
                "requirement_type": atom.get("requirement_type") or _requirement_policy(atom["text"])["type"],
                "group_id": atom.get("group_id"),
                "group_mode": atom.get("group_mode"),
                "candidate_evidence": candidates,
            })
    return atom_packets, candidates_by_atom


def _evidence_reuse_limit(evidence: str, requirement: str) -> int:
    if infer_evidence_section(evidence) == "skills":
        return 4
    if _requirement_policy(requirement)["type"] in {
        "degree", "postgraduate_degree", "certification", "exact_tool",
    }:
        return 3
    return 2


def _evidence_proves_any_core_concept(requirement: str, evidence: str) -> bool:
    for atom in decompose_requirement_text(requirement):
        atom_text = atom.get("text") or ""
        if _looks_like_atomic_noise(atom_text):
            continue
        if _concept_evidence_confidence(atom_text, evidence):
            return True
        if _general_capability_evidence_confidence(atom_text, evidence):
            return True
        confidence = classify_requirement_evidence_match(atom_text, evidence)
        if confidence and _requirement_policy(atom_text)["type"] != "general":
            return True
    return False


def _evidence_proves_all_core_concepts(requirement: str, evidence: str) -> bool:
    atoms = [
        atom
        for atom in decompose_requirement_text(requirement)
        if not _looks_like_atomic_noise(atom.get("text") or "")
    ]
    if not atoms:
        return False
    for atom in atoms:
        atom_text = atom.get("text") or ""
        if _concept_evidence_confidence(atom_text, evidence):
            continue
        if _general_capability_evidence_confidence(atom_text, evidence):
            continue
        confidence = classify_requirement_evidence_match(atom_text, evidence)
        if confidence and _requirement_policy(atom_text)["type"] != "general":
            continue
        return False
    return True


def gemini_responsibility_match(
    responsibilities: List[dict],
    parsed_resume: dict,
) -> dict:
    """Use Gemini to intelligently match JD responsibilities against CV evidence."""
    if not GENAI_CLIENT:
        raise RuntimeError("Gemini API is required for responsibility matching.")
    if not responsibilities:
        return score_responsibility_match_semantic(responsibilities, [])

    atom_packets, candidates_by_atom = retrieve_atom_evidence_candidates(
        responsibilities,
        parsed_resume,
        top_k=5,
    )
    candidates_by_requirement: dict[str, List[dict]] = {}
    for packet in atom_packets:
        try:
            original = responsibilities[int(packet["responsibility_index"]) - 1]
        except (IndexError, TypeError, ValueError):
            continue
        existing = candidates_by_requirement.setdefault(original["normalized"], [])
        seen_text = {normalize_phrase(item.get("text") or "") for item in existing}
        for candidate in packet.get("candidate_evidence") or []:
            norm = normalize_phrase(candidate.get("text") or "")
            if norm and norm not in seen_text:
                existing.append(candidate)
                seen_text.add(norm)
    verification_packets = []
    for index, responsibility in enumerate(responsibilities, 1):
        verification_packets.append({
            "index": index,
            "responsibility": responsibility["text"],
            "core_concepts": _responsibility_core_concepts(responsibility["text"]),
            "candidate_evidence": [
                candidate["text"]
                for candidate in candidates_by_requirement.get(responsibility["normalized"], [])
            ],
        })

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
    contents = (
        "STRICT VERIFICATION OVERRIDE:\n"
        "- For each responsibility, choose evidence ONLY from its candidate_evidence list.\n"
        "- If none directly proves a core concept, put the responsibility in missing.\n"
        "- Topical similarity, shared generic actions, and keyword-only mentions are not evidence.\n"
        "- Broad capabilities require action-based experience or project evidence, not only the SKILLS line.\n\n"
        f"{prompt}\n\nVERIFICATION PACKETS:\n{json.dumps(verification_packets, ensure_ascii=False)}"
    )
    contents = (
        "You are a strict atom-level CV evidence selector. Return ONLY valid JSON shaped as "
        '{"atom_matches":[{"atom_id":"r1a1","evidence_id":"r1a1e1","confidence":"strong|partial"}],'
        '"atom_missing":[{"atom_id":"r1a2","gap":"specific missing evidence"}]}.\n'
        "Select only evidence_id values listed inside that same atom packet. Judge each atom "
        "independently. Named tools require the same tool or exact alias. Internship, placement, "
        "and formal-program atoms require explicit formal markers. Skills-only evidence does not "
        "prove broad responsibilities or behaviours. Every atom_id must appear exactly once.\n\n"
        f"ATOM VERIFICATION PACKETS:\n{json.dumps(atom_packets, ensure_ascii=False)}"
    )

    try:
        response = _genai_generate(
            model=GEMINI_REWRITE_MODEL,
            contents=contents,
            config=gemini_generation_config(0, response_mime_type="application/json"),
        )
        raw = getattr(response, "text", "") or ""
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected response shape")
    except Exception as exc:
        logger.warning("Gemini responsibility match failed; API-backed analysis is required: %s", exc)
        raise

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
    ai_atom_match_map: dict[str, dict[str, dict]] = {}
    ai_gap_map: dict[str, str] = {}

    atom_packet_by_id = {packet["atom_id"]: packet for packet in atom_packets}
    for match in parsed.get("atom_matches") or []:
        if not isinstance(match, dict):
            continue
        atom_id = str(match.get("atom_id") or "")
        evidence_id = str(match.get("evidence_id") or "")
        packet = atom_packet_by_id.get(atom_id)
        candidate = next(
            (
                item
                for item in candidates_by_atom.get(atom_id, [])
                if item.get("evidence_id") == evidence_id
            ),
            None,
        )
        if not packet or not candidate:
            continue
        try:
            original = responsibilities[int(packet["responsibility_index"]) - 1]
        except (IndexError, TypeError, ValueError):
            continue
        atom_norm = normalize_phrase(packet.get("atom") or "")
        if not atom_norm:
            continue
        ai_atom_match_map.setdefault(original["normalized"], {})[atom_norm] = {
            "evidence_id": evidence_id,
            "evidence": candidate["text"],
            "confidence": str(match.get("confidence") or "partial").lower().strip(),
        }

    for missing in parsed.get("atom_missing") or []:
        if not isinstance(missing, dict):
            continue
        packet = atom_packet_by_id.get(str(missing.get("atom_id") or ""))
        if not packet:
            continue
        try:
            original = responsibilities[int(packet["responsibility_index"]) - 1]
        except (IndexError, TypeError, ValueError):
            continue
        gap = str(missing.get("gap") or "").strip()
        if gap:
            ai_gap_map[original["normalized"]] = gap

    for m in (parsed.get("matches") or []):
        if not isinstance(m, dict):
            continue
        original = _find_original(m)
        if original is None:
            continue
        evidence = str(m.get("evidence") or "").strip() or None
        candidate_texts = {
            normalize_phrase(candidate["text"]): candidate["text"]
            for candidate in candidates_by_requirement.get(original["normalized"], [])
        }
        canonical_evidence = candidate_texts.get(normalize_phrase(evidence or ""))
        if not canonical_evidence:
            ai_gap_map[original["normalized"]] = "No supplied candidate evidence directly proved this requirement."
            continue
        policy = _requirement_policy(original["text"])
        if infer_evidence_section(canonical_evidence) == "skills" and policy["type"] in {
            "general", "professional_experience", "project_management", "management",
        }:
            ai_gap_map[original["normalized"]] = "Only a skills-list mention was found; no action-based evidence proved this capability."
            continue
        if not _evidence_proves_any_core_concept(original["text"], canonical_evidence):
            ai_gap_map[original["normalized"]] = "The selected line was topically related but did not prove a core requirement concept."
            continue
        ai_match_map[original["normalized"]] = {
            "confidence": str(m.get("confidence") or "partial").lower().strip(),
            "evidence": canonical_evidence,
        }

    for m in (parsed.get("missing") or []):
        if not isinstance(m, dict):
            continue
        original = _find_original(m)
        if original is None:
            continue
        ai_gap_map[original["normalized"]] = str(m.get("gap") or "").strip()

    evidence_use_counts: dict[str, int] = {}
    for original in responsibilities:
        ai_match = ai_match_map.get(original["normalized"]) or {}
        atom_matches = ai_atom_match_map.get(original["normalized"]) or {}
        aggregate = aggregate_requirement_evidence(
            original["text"],
            parsed_resume,
            resume_text,
            ai_present=bool(ai_match),
            ai_evidence=ai_match.get("evidence"),
            ai_confidence=ai_match.get("confidence"),
            ai_atom_matches=atom_matches,
        )
        if (
            not ai_match
            and aggregate["status"] == "partial"
            and aggregate["matched_count"] == 0
            and _requirement_policy(original["text"])["type"] == "general"
        ):
            aggregate_evidence = aggregate.get("cv_where") or ""
            if not _concept_evidence_confidence(original["text"], aggregate_evidence):
                aggregate["status"] = "missing"
                aggregate["present"] = False
                aggregate["confidence"] = "missing"
                aggregate["cv_where"] = None
                aggregate["section"] = None
        if aggregate["status"] == "missing":
            missing_items.append({
                "responsibility": original["text"],
                "action_phrases": original["action_phrases"],
                "gap": ai_gap_map.get(original["normalized"]) or "No explicit CV evidence found for this requirement.",
                "category": original.get("category", "essential"),
                "atomic_breakdown": aggregate["atomic_breakdown"],
                "verification_debug": aggregate,
            })
            continue

        confidence = "strong" if aggregate["status"] == "present" and aggregate["confidence"] == "strong" else "partial"
        selected_evidence = ai_match.get("evidence") or aggregate["cv_where"]
        if confidence == "strong" and selected_evidence and not _evidence_proves_all_core_concepts(
            original["text"],
            selected_evidence,
        ):
            confidence = "partial"
        evidence_key = normalize_phrase(selected_evidence or "")
        if evidence_key and infer_evidence_section(selected_evidence or "") == "skills":
            # The skills line names many distinct skills; crediting different skills from
            # it (Python, Docker, React...) is not evidence reuse. Key the cap by the
            # specific matched skill(s) so only the SAME skill cited repeatedly is capped.
            matched_units = "+".join(sorted(aggregate.get("matched_scoring_units") or []))
            if matched_units:
                evidence_key = f"{evidence_key}|{matched_units}"
        if evidence_key:
            reuse_limit = _evidence_reuse_limit(selected_evidence, original["text"])
            if evidence_use_counts.get(evidence_key, 0) >= reuse_limit:
                missing_items.append({
                    "responsibility": original["text"],
                    "action_phrases": original["action_phrases"],
                    "gap": "The available evidence was too generic and already used for other requirements.",
                    "category": original.get("category", "essential"),
                    "atomic_breakdown": aggregate["atomic_breakdown"],
                    "verification_debug": aggregate,
                })
                continue
            evidence_use_counts[evidence_key] = evidence_use_counts.get(evidence_key, 0) + 1
        matched_items.append({
            "responsibility": original["text"],
            "action_phrases": original["action_phrases"],
            "evidence": selected_evidence,
            "section": infer_evidence_section(selected_evidence) if selected_evidence else (aggregate["section"] or "experience"),
            "similarity": 1.0 if confidence == "strong" else min(0.75, aggregate["coverage_ratio"]),
            "match_type": "ai_atomic" if (ai_match or atom_matches) else "local_atomic",
            "confidence": confidence,
            "category": original.get("category", "essential"),
            "matched_count": aggregate["matched_count"],
            "total_count": aggregate["total_count"],
            "atomic_breakdown": aggregate["atomic_breakdown"],
            "verification_debug": aggregate,
        })

    category_weights = {"essential": 1.0, "nice_to_have": 0.25}
    total = sum(category_weights.get(item.get("category", "essential"), 1.0) for item in responsibilities)
    total_weight = 0.0
    for item in matched_items:
        category_weight = category_weights.get(item.get("category", "essential"), 1.0)
        if item.get("confidence") == "strong":
            evidence_weight = STRONG_W
        else:
            evidence_weight = max(0.0, min(1.0, float(item.get("similarity") or PARTIAL_W)))
        total_weight += category_weight * evidence_weight
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


def compute_technical_relevance_score(
    responsibility_score: float,
    semantic_score: float,
    skills_score: float,
    experience_result: dict,
) -> float:
    evidence_score = experience_result.get("responsibility_evidence_score")
    if evidence_score is None:
        evidence_score = experience_result.get("score") or 0
    score = (
        responsibility_score * 0.55
        + semantic_score * 0.10
        + skills_score * 0.25
        + float(evidence_score or 0) * 0.10
    )
    return round(max(0.0, min(100.0, score)), 2)


def build_application_positioning(match_score: float, technical_score: float) -> dict:
    if match_score >= 75:
        headline = "Strong fit"
        tone = "confident and evidence-led"
        cover_guidance = "Use confident fit language backed by specific CV evidence."
    else:
        headline = "Developing fit"
        tone = "balanced and evidence-led"
        cover_guidance = "Focus on transferable evidence and avoid unsupported claims."
    return {
        "headline": headline,
        "cover_letter_tone": tone,
        "cover_letter_guidance": cover_guidance,
        "technical_relevance_score": technical_score,
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
    return {
        "score": round(max(0.0, min(100.0, experience_score)), 2),
        "required_years": required_years,
        "resume_years": resume_years,
        "years_score": None if years_score is None else round(years_score * 100, 2),
        "date_coverage_score": round(date_coverage_ratio * 100, 2),
        "responsibility_evidence_score": round(evidence_density * 100, 2),
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
        response = _genai_generate(
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


def _legacy_account_matches(email: str, password: str) -> bool:
    account = ACCOUNTS.get(email)
    return bool(account and secrets.compare_digest(str(account.get("password") or ""), password))


def _migrate_legacy_account(email: str, password: str) -> dict:
    user = db.get_user_by_email(email)
    password_hash = auth_utils.hash_password(password)
    if user:
        db.update_password(user["id"], password_hash)
    else:
        user = db.create_user(email=email, password_hash=password_hash)
    db.mark_email_verified(user["id"])
    account_limit = int((ACCOUNTS.get(email) or {}).get("daily_limit") or DEFAULT_DAILY_LIMIT)
    if account_limit > FREE_TIER_SCAN_LIMIT:
        db.set_tier(user["id"], "paid")
    return db.get_user_by_id(user["id"]) or user


@app.post("/auth/login", dependencies=[Depends(rate_limit.require_auth_rate_limit)])
async def auth_login(payload: dict):
    email = str((payload or {}).get("email") or "").strip().lower()
    password = str((payload or {}).get("password") or "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required.")
    user = db.get_user_by_email(email)
    if user and not auth_utils.verify_password(password, user["password_hash"]):
        if _legacy_account_matches(email, password):
            user = _migrate_legacy_account(email, password)
        else:
            raise HTTPException(status_code=401, detail="Invalid email or password.")
    elif not user:
        if _legacy_account_matches(email, password):
            user = _migrate_legacy_account(email, password)
        else:
            raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not user:
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
            "lite": GEMINI_LITE_MODEL,
            "openai_rewrite": OPENAI_REWRITE_MODEL,
        },
        "gemini_seed": GEMINI_SEED,
        "users_db": db.status_metadata(),
        "analyze_cache": analysis_cache.status_metadata(),
    }


@app.post("/extract-job-requirements")
async def extract_job_requirements_endpoint(payload: dict):
    job_description = clean_text(str((payload or {}).get("job_description") or ""))
    if not job_description:
        raise HTTPException(status_code=400, detail="Missing job_description.")
    try:
        return {"job_preflight": cached_preflight_job_requirements(job_description)}
    except Exception as exc:
        overload = _ai_overload_message(exc)
        raise HTTPException(
            status_code=503 if overload else 502,
            detail=overload or f"AI preflight failed while extracting job requirements: {exc}",
        )


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
            response = _genai_generate(
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
                return _augment_skills_with_local_requirements({
                    "must_have": filter_satisfied_alternative_missing_skills(
                        _clean_items(parsed.get("must_have")),
                        job_description,
                    ),
                    "nice_to_have": filter_satisfied_alternative_missing_skills(
                        _clean_items(parsed.get("nice_to_have")),
                        job_description,
                    ),
                }, job_description, parsed_resume, resume_text)
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
    return _augment_skills_with_local_requirements({
        "must_have": filter_satisfied_alternative_missing_skills(must_items, job_description),
        "nice_to_have": filter_satisfied_alternative_missing_skills(nice_items, job_description),
    }, job_description, parsed_resume, resume_text)


def gemini_skills_and_ats(
    job_description: str,
    parsed_resume: dict,
    resume_text: str,
    job_preflight: dict | None = None,
) -> dict:
    """Single Gemini call that does both skills matching and ATS keyword extraction.

    Replaces separate gemini_skills_match + gemini_ats_keywords to save one API round-trip.
    Returns {"skills": {...}, "ats_keywords": {...}}.
    """
    if not GENAI_CLIENT:
        raise RuntimeError("Gemini API is required for skills and ATS analysis.")

    cv_section = format_cv_match_evidence(parsed_resume, work_limit=70, project_limit=70)

    if not cv_section:
        raise RuntimeError("Parsed CV evidence is required for skills and ATS analysis.")

    preflight_requirements = (job_preflight or {}).get("requirements") or []
    job_description_for_prompt = (job_preflight or {}).get("cleaned_job_description") or job_description
    requirement_lines = "\n".join(
        f"- {req.get('category', 'essential')}: {req.get('text')}"
        for req in preflight_requirements
        if isinstance(req, dict) and req.get("text")
    )

    prompt = (
        "You are matching a candidate's CV against a job description. Complete TWO tasks in one response.\n\n"
        "Use the PRE-VALIDATED REQUIREMENTS as the source of truth when provided. "
        "Ignore raw JD company background, benefits, marketing prose, location/flexibility, and application instructions.\n\n"
        "TASK 1 — SKILLS MATCHING:\n"
        "Extract ALL skills, tools, technologies, and competencies from the JD. "
        "Classify each as 'must_have' (required/essential) or 'nice_to_have' (preferred/bonus). "
        "For each skill, check whether the CV demonstrates it semantically "
        "(e.g. 'AWS Lambda' counts for 'serverless', 'led a team of 5' counts for 'team leadership').\n\n"
        "TASK 2 — ATS KEYWORDS:\n"
        "Extract only the high-signal keywords from candidate-facing JD requirements/responsibilities. "
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
        "- ats_keywords: return at most 12 hard_skills and 8 soft_skills.\n"
        "- ats_keywords: exclude company background, company names, exact job title, generic nouns, perks, benefits, and boilerplate.\n"
        "- ats_keywords: every keyword must be a candidate-owned skill, tool, domain experience, qualification, or behaviour.\n"
        "- ats_keywords: sort each list by importance to the candidate fit, then jd_count descending.\n"
        "- Return ONLY the JSON object, no markdown fences.\n"
    )
    contents = (
        f"{prompt}\n\n"
        f"PRE-VALIDATED REQUIREMENTS:\n{requirement_lines or '(none provided)'}\n\n"
        f"JOB DESCRIPTION:\n{job_description_for_prompt[:4000]}\n\n"
        f"CANDIDATE CV:\n{cv_section}\n\n"
        f"FULL CV TEXT (for ats cv_count):\n{resume_text[:3000]}"
    )

    try:
        response = _genai_generate(
            model=GEMINI_REWRITE_MODEL,
            contents=contents,
            config=gemini_generation_config(0),
        )
        raw = getattr(response, "text", "") or ""
        parsed = parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected response shape")
    except Exception as exc:
        logger.warning("gemini_skills_and_ats failed; API-backed analysis is required: %s", exc)
        raise

    def _clean_skills(lst):
        out, seen = [], set()
        for item in (lst or []):
            if not isinstance(item, dict):
                continue
            skill = clean_model_skill_name(str(item.get("skill") or ""))
            norm_skill = normalize_phrase(skill)
            if not skill or norm_skill in seen:
                continue
            has_ai_evidence = bool(item.get("present")) or bool(str(item.get("cv_where") or "").strip())
            hard_term_hits = {
                normalize_phrase(term)
                for term in _known_ats_hard_terms()
                if _phrase_present_in_normalized_text(normalize_phrase(term), norm_skill)
            }
            if not is_valid_model_skill(skill, candidate_jd_blob_for_ats) and not (
                has_ai_evidence and len(hard_term_hits) >= 2
            ):
                continue
            seen.add(norm_skill)
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
    candidate_jd_blob_for_ats = (
        normalize_phrase(job_description_for_prompt)
        if (job_preflight or {}).get("cleaned_job_description")
        else _candidate_requirement_text_blob(job_description)
    )

    def _clean_ats(lst, limit):
        out, seen = [], set()
        for item in (lst or []):
            if not isinstance(item, dict):
                continue
            skill = str(item.get("skill") or "").strip()
            norm_skill = normalize_phrase(skill)
            if not skill or norm_skill in seen:
                continue
            if _looks_like_noisy_ats_fragment(skill) or not is_valid_ats_keyword(skill, candidate_jd_blob_for_ats):
                continue
            seen.add(norm_skill)
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
            if len(out) >= limit:
                break
        return out

    def _augment_ats_with_local_keywords(items, candidates, limit):
        out = list(items or [])
        seen = set()
        for item in out:
            if not isinstance(item, dict):
                continue
            existing_skill = str(item.get("skill") or "")
            existing_norm = normalize_phrase(existing_skill)
            if existing_norm:
                seen.add(existing_norm)
            for alias in _requirement_aliases(existing_skill):
                alias_norm = normalize_phrase(alias)
                if alias_norm:
                    seen.add(alias_norm)
        for skill in candidates:
            norm_skill = normalize_phrase(skill)
            if not norm_skill or norm_skill in seen:
                continue
            if not is_valid_ats_keyword(skill, candidate_jd_blob_for_ats):
                continue
            jd_count = _count_normalized_phrase(skill, candidate_jd_blob_for_ats)
            if jd_count <= 0:
                continue
            cv_count = 0
            for alias in _requirement_aliases(skill):
                alias_count = _count_normalized_phrase(alias, resume_text_norm_for_ats)
                if alias_count:
                    cv_count = max(cv_count, alias_count)
            status = "missing" if cv_count == 0 else ("low" if cv_count < max(1, jd_count // 2) else "present")
            out.append({"skill": skill, "jd_count": jd_count, "cv_count": cv_count, "status": status})
            seen.add(norm_skill)
            if len(out) >= limit:
                break
        return out[:limit]

    skills_raw = parsed.get("skills") or {}
    ats_raw = parsed.get("ats_keywords") or {}
    cleaned_must = filter_satisfied_alternative_missing_skills(
        _clean_skills(skills_raw.get("must_have")),
        job_description_for_prompt or job_description,
    )
    cleaned_nice = filter_satisfied_alternative_missing_skills(
        _clean_skills(skills_raw.get("nice_to_have")),
        job_description_for_prompt or job_description,
    )
    extracted_skill_candidates = [
        str(item.get("skill") or "").strip()
        for item in [*cleaned_must, *cleaned_nice]
        if str(item.get("skill") or "").strip()
    ]
    local_ats_candidates = _local_ats_keyword_candidates(job_description_for_prompt or job_description)
    preflight_ats = (job_preflight or {}).get("ats_keywords") or {}
    preflight_hard = [
        item.get("skill") if isinstance(item, dict) else item
        for item in preflight_ats.get("hard_skills") or []
    ]
    preflight_soft = [
        item.get("skill") if isinstance(item, dict) else item
        for item in preflight_ats.get("soft_skills") or []
    ]
    hard_ats = _augment_ats_with_local_keywords(
        _clean_ats(ats_raw.get("hard_skills"), 12),
        [
            *(skill for skill in extracted_skill_candidates if not _looks_like_soft_ats_keyword(skill)),
            *preflight_hard,
            *local_ats_candidates["hard"],
            *LOCAL_ATS_HARD_KEYWORDS,
        ],
        12,
    )
    soft_ats = _augment_ats_with_local_keywords(
        _clean_ats(ats_raw.get("soft_skills"), 8),
        [
            *(skill for skill in extracted_skill_candidates if _looks_like_soft_ats_keyword(skill)),
            *preflight_soft,
            *local_ats_candidates["soft"],
            *LOCAL_ATS_SOFT_KEYWORDS,
        ],
        8,
    )
    return {
        "skills": {
            **_augment_skills_with_local_requirements(
                {
                    "must_have": cleaned_must,
                    "nice_to_have": cleaned_nice,
                },
                job_description_for_prompt or job_description,
                parsed_resume,
                resume_text,
            ),
        },
        "ats_keywords": {
            "hard_skills": hard_ats,
            "soft_skills": soft_ats,
        },
    }


def _ai_overload_message(exc: Exception) -> str | None:
    """If the error is a transient AI-provider overload, return a friendly retry
    message; otherwise None. Lets the handler return a clean 503 instead of raw
    '503 UNAVAILABLE ...' text when Gemini is momentarily at capacity."""
    text = str(exc).upper()
    markers = ("503", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "429", "HIGH DEMAND", "OVERLOAD")
    if any(marker in text for marker in markers):
        return (
            "The AI service is busy right now (temporary high demand). "
            "Please wait a few seconds and try again."
        )
    return None


@app.post("/analyze")
async def analyze(
    resume: UploadFile = File(...),
    job_description: str = Form(""),
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
    # Phase 1: preflight (JD-only) and resume parsing (resume-only) are independent —
    # run them concurrently to cut one Gemini round-trip off the critical path.
    job_preflight, parsed_resume = await asyncio.gather(
        asyncio.to_thread(cached_preflight_job_requirements, job_description),
        asyncio.to_thread(parse_resume, resume_text, debug_info),
        return_exceptions=True,
    )
    if isinstance(job_preflight, Exception):
        overload = _ai_overload_message(job_preflight)
        raise HTTPException(
            status_code=503 if overload else 502,
            detail=overload or f"AI preflight failed while extracting job requirements: {job_preflight}",
        )
    if isinstance(parsed_resume, Exception):
        if isinstance(parsed_resume, HTTPException):
            raise parsed_resume
        overload = _ai_overload_message(parsed_resume)
        raise HTTPException(
            status_code=503 if overload else 502,
            detail=overload or f"AI analysis failed while parsing the CV: {parsed_resume}",
        )

    analysis_job_description = job_preflight.get("cleaned_job_description") or job_description
    if debug_info is not None:
        debug_info["job_preflight"] = {
            "source": job_preflight.get("source"),
            "requirements_count": len(job_preflight.get("requirements") or []),
            "quality": job_preflight.get("quality") or {},
        }
    parsed_resume["_resume_text"] = resume_text

    # Local computation — no API calls
    parsed_skills = parsed_resume.get("skills") or []
    parsed_tools = parsed_resume.get("tools") or []
    resume_text_norm = normalize_phrase(resume_text)
    resume_token_set = set(resume_text_norm.split())
    resume_compact = resume_text_norm.replace(" ", "")
    resume_sections = split_resume_sections(resume_text)
    resume_sections_raw = split_resume_sections_raw(resume_text)
    tfidf_terms = extract_tfidf_terms(analysis_job_description, limit=40)
    required_years = extract_required_years(analysis_job_description)
    resume_years = (
        years_from_work_experience(parsed_resume.get("work_experience") or [])
        or parsed_resume.get("years_experience")
        or extract_resume_years(resume_text)
    )
    try:
        responsibility_candidates = job_preflight.get("requirements") or extract_job_responsibilities(analysis_job_description)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AI analysis failed while extracting job requirements: {exc}",
        )

    # Phase 2: Run required scoring calls in parallel. Optional section-level AI extras
    # also start immediately, but they no longer fail the whole analysis if Gemini is slow.
    cv_sections_task = asyncio.create_task(
        asyncio.to_thread(analyze_cv_sections, resume_text, parsed_resume, analysis_job_description)
    )
    gemini_sections_task = asyncio.create_task(
        # Skips role_fit_breakdown context here (chicken-and-egg) — the prompt is strong
        # enough to infer JD vs CV gaps on its own.
        asyncio.to_thread(gemini_section_feedback, parsed_resume, analysis_job_description, None)
    )
    optional_tasks = (cv_sections_task, gemini_sections_task)
    try:
        (
            unified_result,
            responsibility_result,
            semantic_score,
            textrazor_terms,
        ) = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(gemini_skills_and_ats, analysis_job_description, parsed_resume, resume_text, job_preflight),
                asyncio.to_thread(gemini_responsibility_match, responsibility_candidates, parsed_resume),
                asyncio.to_thread(compute_semantic_score, resume_text, analysis_job_description, None),
                asyncio.to_thread(textrazor_extract_phrases, analysis_job_description, None),
            ),
            timeout=ANALYZE_CORE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        for task in optional_tasks:
            task.cancel()
        raise HTTPException(
            status_code=504,
            detail="Analysis timed out; the AI service is under load. Please try again in a moment.",
        )
    except Exception as exc:
        for task in optional_tasks:
            task.cancel()
        overload = _ai_overload_message(exc)
        raise HTTPException(
            status_code=503 if overload else 502,
            detail=overload or f"AI analysis failed. Please try again in a moment. Details: {exc}",
        )
    try:
        cv_sections_analysis, gemini_sections = await asyncio.wait_for(
            asyncio.gather(*optional_tasks),
            timeout=ANALYZE_OPTIONAL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        for task in optional_tasks:
            task.cancel()
        logger.warning("Optional CV section analysis timed out; returning core analysis with fallbacks.")
        cv_sections_analysis = {}
        gemini_sections = {}
    except Exception as exc:
        logger.warning("Optional CV section analysis failed; returning core analysis with fallbacks: %s", exc)
        cv_sections_analysis = {}
        gemini_sections = {}
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
        job_description=analysis_job_description,
        responsibilities=responsibility_candidates,
        responsibility_result=responsibility_result,
        required_years=required_years,
        resume_years=resume_years,
    )
    skills_match_score = round(
        ((must_coverage * 0.85) + (nice_coverage * 0.15)) * 100,
        2,
    )

    inferred_missing = infer_missing_keywords(
        resume_text,
        analysis_job_description,
        prefetched_phrases=combined_terms,
    )
    skills_present = merge_unique(present_must_have + present_nice_to_have)
    skills_partial = merge_unique(partial_must_have + partial_nice_to_have)
    skills_missing = merge_unique(missing_must_have + missing_nice_to_have)

    base_match_score = round(
        (
            responsibility_result["score"] * RESPONSIBILITY_MATCH_WEIGHT
            + experience_result["score"] * EXPERIENCE_MATCH_WEIGHT
            + skills_match_score * SKILLS_MATCH_WEIGHT
            + semantic_score * SEMANTIC_MATCH_WEIGHT
        ),
        2,
    )
    base_match_score = max(0.0, min(100.0, base_match_score))
    technical_relevance_score = compute_technical_relevance_score(
        responsibility_result["score"],
        semantic_score,
        skills_match_score,
        experience_result,
    )
    match_score = base_match_score
    application_positioning = build_application_positioning(match_score, technical_relevance_score)
    cv_highlights = annotate_cv_lines(resume_sections_raw, responsibility_result, parsed_resume)

    role_fit_breakdown = {
        "responsibility_match_score": responsibility_result["score"],
        "experience_match_score": experience_result["score"],
        "skills_match_score": skills_match_score,
        "semantic_score": round(semantic_score, 2),
        "base_match_score": base_match_score,
        "technical_relevance_score": technical_relevance_score,
        "application_positioning": application_positioning,
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
            "cleaned_char_count": len(analysis_job_description),
            "preflight": job_preflight,
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
                analysis_job_description,
                parsed_resume,
            )
        ),
        "candidate_profile": {
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


def _secondary_payload_hash_payload(
    resume_text: str = "",
    job_description: str = "",
    role_fit_breakdown: dict | None = None,
    company_name: str = "",
) -> dict:
    return {
        "resume": analysis_cache._sha256(analysis_cache._normalize_text(resume_text)) if resume_text else "",
        "job": analysis_cache._sha256(analysis_cache._normalize_text(job_description)) if job_description else "",
        "role_fit": analysis_cache._sha256(
            json.dumps(role_fit_breakdown or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        ) if role_fit_breakdown else "",
        "company": company_name,
    }


def get_cached_secondary(kind: str, payload: dict) -> tuple[dict | None, str]:
    cache_key = analysis_cache.secondary_cache_key(kind, payload)
    return analysis_cache.get_cached_secondary_response(cache_key, kind), cache_key


def set_cached_secondary(kind: str, cache_key: str, response: dict) -> None:
    analysis_cache.set_cached_secondary_response(cache_key, kind, response)


def get_secondary_compute_lock(cache_key: str) -> threading.Lock:
    with _secondary_compute_locks_guard:
        lock = _secondary_compute_locks.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _secondary_compute_locks[cache_key] = lock
        return lock


def run_cached_secondary(kind: str, payload: dict, compute):
    cached, cache_key = get_cached_secondary(kind, payload)
    if cached is not None:
        return cached
    lock = get_secondary_compute_lock(cache_key)
    with lock:
        cached, _ = get_cached_secondary(kind, payload)
        if cached is not None:
            return cached
        response = compute()
        set_cached_secondary(kind, cache_key, response)
        return response


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
    cover_positioning = build_application_positioning(0, 0)

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
APPLICATION POSITIONING RULES:
- Fit headline: {cover_positioning['headline']}
- Tone: {cover_positioning['cover_letter_tone']}
- Guidance: {cover_positioning['cover_letter_guidance']}

---
Generate the cover letter now."""

    response = _genai_generate(
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
        response = _genai_generate(
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
        response = _genai_generate(
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
        response = _genai_generate(
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
    cache_payload = _secondary_payload_hash_payload(
        resume_text=resume_text,
        job_description=job_description,
    )
    return run_cached_secondary(
        "business-fit",
        cache_payload,
        lambda: {"business_fit": gemini_business_fit(resume_text, job_description)},
    )


@app.post("/recruiter-view")
async def recruiter_view(payload: dict):
    resume_text = clean_text(str((payload or {}).get("resume_text") or ""))
    job_description = clean_text(str((payload or {}).get("job_description") or ""))
    role_fit_breakdown = (payload or {}).get("role_fit_breakdown") or {}
    if not resume_text:
        raise HTTPException(status_code=400, detail="Missing resume_text.")
    if not job_description:
        raise HTTPException(status_code=400, detail="Missing job_description.")
    cache_payload = _secondary_payload_hash_payload(
        resume_text=resume_text,
        job_description=job_description,
        role_fit_breakdown=role_fit_breakdown if isinstance(role_fit_breakdown, dict) else {},
    )
    return run_cached_secondary(
        "recruiter-view",
        cache_payload,
        lambda: {"recruiter_view": gemini_recruiter_view(resume_text, job_description, role_fit_breakdown)},
    )


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
        response = _genai_generate(
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
    cache_payload = _secondary_payload_hash_payload(
        resume_text=resume_text,
        job_description=job_description,
        role_fit_breakdown=role_fit_breakdown if isinstance(role_fit_breakdown, dict) else {},
    )
    return run_cached_secondary(
        "interview-prep",
        cache_payload,
        lambda: {"interview_prep": gemini_interview_prep(resume_text, job_description, role_fit_breakdown)},
    )


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
        response = _genai_generate(
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
            grounded_response = _genai_generate(
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
            fallback_response = _genai_generate(
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
    cache_payload = _secondary_payload_hash_payload(job_description=job_description)
    def compute_company_insights():
        if not GENAI_CLIENT:
            raise HTTPException(status_code=500, detail="GEMINI_API_KEY is required for company insights.")
        company_name = extract_company_name(job_description)
        if not company_name:
            raise HTTPException(status_code=422, detail="Could not identify company name from job description.")
        return {"company_insights": gemini_company_insights(company_name, job_description), "locked": False}

    return run_cached_secondary("company-insights", cache_payload, compute_company_insights)
