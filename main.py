from __future__ import annotations

import io
import json
import math
import os
import re
from urllib.parse import urlparse
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, File, Form, UploadFile
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from dotenv import load_dotenv
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer

try:
    from google import genai
    from google.genai import types
    GENAI_IMPORT_ERROR = None
except Exception as exc:
    genai = None
    types = None
    GENAI_IMPORT_ERROR = str(exc)

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ],
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
GENAI_CLIENT = (
    genai.Client(api_key=GEMINI_API_KEY) if genai is not None and GEMINI_API_KEY else None
)
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
    r"(\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b)\s*(%|x|k|m|mm|bn|b)\b|[$€£]\s*\d",
    re.IGNORECASE,
)

ACTION_VERBS = (
    "built",
    "designed",
    "developed",
    "implemented",
    "led",
    "owned",
    "delivered",
    "launched",
    "shipped",
    "optimized",
    "improved",
    "reduced",
    "increased",
    "automated",
    "migrated",
    "created",
    "analyzed",
    "collaborated",
    "architected",
    "refactored",
    "maintained",
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

SECTION_HEADINGS = {
    "experience": ("experience", "work experience", "professional experience", "employment"),
    "projects": ("projects", "technical projects", "personal projects"),
    "education": ("education", "academics"),
    "skills": ("skills", "technical skills"),
    "summary": ("summary", "profile", "objective"),
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
    reader = PdfReader(io.BytesIO(file_bytes))
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


def parse_resume(resume_text: str, debug_info: dict | None = None) -> dict:
    if GENAI_CLIENT:
        try:
            parsed = gemini_parse_resume(resume_text)
            if not isinstance(parsed, dict):
                parsed = {}
            if debug_info is not None:
                debug_info["parse_method"] = "gemini"
            return parsed
        except Exception as exc:
            if debug_info is not None:
                debug_info["parse_error"] = str(exc)
    if debug_info is not None:
        debug_info["parse_method"] = "heuristic"
    return fallback_parse_resume(resume_text)


def gemini_parse_resume(resume_text: str) -> dict:
    if not GENAI_CLIENT:
        detail = "GEMINI_API_KEY is not set."
        if GEMINI_IMPORT_ERROR:
            detail = f"Gemini SDK unavailable: {GEMINI_IMPORT_ERROR}"
        raise HTTPException(status_code=500, detail=detail)
    prompt = (
        "Extract resume data and return ONLY valid JSON. "
        "Keys: skills (list of strings), tools (list), years_experience (number|null), "
        "education (list), certifications (list)."
    )
    response = GENAI_CLIENT.models.generate_content(
        model=GEMINI_PARSE_MODEL,
        contents=f"{prompt}\n\nRESUME:\n{resume_text}",
        config=types.GenerateContentConfig(temperature=0),
    )
    return parse_json_response(getattr(response, "text", "") or "")


def gemini_embed_texts(texts: List[str]) -> List[List[float]]:
    if not GENAI_CLIENT:
        detail = "GEMINI_API_KEY is not set."
        if GEMINI_IMPORT_ERROR:
            detail = f"Gemini SDK unavailable: {GEMINI_IMPORT_ERROR}"
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
    for match in RANGE_YEARS_RE.findall(job_description):
        values.append(int(match[0]))

    scrubbed = RANGE_YEARS_RE.sub("", job_description)
    for match in AT_LEAST_YEARS_RE.findall(scrubbed):
        values.append(int(match))
    for match in PLUS_YEARS_RE.findall(scrubbed):
        values.append(int(match))

    return max(values) if values else None


def extract_resume_years(resume_text: str) -> Optional[int]:
    values = [int(match) for match in PLUS_YEARS_RE.findall(resume_text)]
    return max(values) if values else None


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


def build_section_feedback(
    raw_sections: dict,
    norm_sections: dict,
    job_description: str,
) -> dict:
    feedback = {}
    profile = detect_job_profile(job_description)
    for section in ["summary", "experience", "projects", "skills", "education", "other"]:
        raw_text = (raw_sections or {}).get(section, "") or ""
        norm_text = (norm_sections or {}).get(section, "") or ""
        good: List[str] = []
        not_good: List[str] = []

        word_count = len(re.findall(r"\b\w+\b", raw_text))
        if not raw_text.strip() or word_count < 15:
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
            skills_list = extract_skill_list(raw_text)
            skill_count = len(skills_list)
            if 6 <= skill_count <= 25:
                good.append("Skill list length is ATS-friendly.")
            elif skill_count < 6:
                not_good.append("Too few skills; add core languages and tools.")
            elif skill_count > 35:
                not_good.append("Too many skills; prioritize the most relevant ones.")

            if tech_present:
                good.append("Skills are specific and technical.")
            else:
                not_good.append("Skills are too generic; add specific tools or languages.")

            soft_hits = [term for term in SOFT_SKILLS if term in norm_text]
            if soft_hits:
                not_good.append("Soft skills mixed in; keep this section technical.")

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


@app.post("/analyze")
async def analyze(
    resume: UploadFile = File(...),
    job_description: str = Form(...),
    debug: bool = False,
):
    file_bytes = await resume.read()
    resume_text = extract_pdf_text(file_bytes)
    job_description = clean_text(job_description)

    debug_info = {} if debug else None
    parsed_resume = parse_resume(resume_text, debug_info)
    parsed_skills = parsed_resume.get("skills") or []
    parsed_tools = parsed_resume.get("tools") or []
    parsed_text_blob = " ".join([*parsed_skills, *parsed_tools]).strip()
    # Use raw resume text for missing-skill detection to avoid LLM hallucinations.
    resume_text_for_embeddings = (
        f"{resume_text}\n{parsed_text_blob}" if parsed_text_blob else resume_text
    )

    resume_text_norm = normalize_phrase(resume_text)
    resume_token_set = set(resume_text_norm.split())
    resume_compact = resume_text_norm.replace(" ", "")
    must_have_skills = extract_must_have_skills(job_description)
    missing_must_have: List[str] = []
    for skill in must_have_skills:
        skill_norm = normalize_phrase(skill)
        if not skill_norm:
            continue
        if phrase_in_resume(skill_norm, resume_text_norm, resume_token_set, resume_compact):
            continue
        missing_must_have.append(skill)

    textrazor_terms = textrazor_extract_phrases(job_description, debug_info if debug else None)
    tfidf_terms = extract_tfidf_terms(job_description, limit=40)
    combined_terms = merge_unique(textrazor_terms + tfidf_terms)
    if debug and textrazor_terms:
        presence_sample = []
        present_count = 0
        for skill in textrazor_terms:
            skill_norm = normalize_phrase(skill)
            if not skill_norm:
                continue
            present = phrase_in_resume(
                skill_norm,
                resume_text_norm,
                resume_token_set,
                resume_compact,
            )
            if present:
                present_count += 1
            if len(presence_sample) < 30:
                presence_sample.append({"skill": skill, "present": present})
        debug_info["textrazor_present_count"] = present_count
        debug_info["textrazor_missing_count"] = max(
            0, len(textrazor_terms) - present_count
        )
        debug_info["textrazor_presence_sample"] = presence_sample
        debug_info["textrazor_terms"] = len(textrazor_terms)
        debug_info["tfidf_terms"] = len(tfidf_terms)
        debug_info["combined_terms_sample"] = combined_terms[:20]
    missing_keywords = merge_unique(
        missing_must_have
        + infer_missing_keywords(
            resume_text,
            job_description,
            prefetched_phrases=combined_terms,
        )
    )

    job_skill_candidates = merge_unique(
        (combined_terms or [])
        + extract_keyphrases(job_description, limit=30)
        + extract_skill_tokens(job_description, limit=30)
    )
    must_norms = {normalize_phrase(item) for item in must_have_skills if item}
    nice_to_have = [
        skill for skill in job_skill_candidates if normalize_phrase(skill) not in must_norms
    ]
    resume_sections = split_resume_sections(resume_text)
    resume_sections_raw = split_resume_sections_raw(resume_text)
    must_coverage = compute_coverage(must_have_skills, resume_sections)
    nice_coverage = compute_coverage(nice_to_have, resume_sections)

    required_years = extract_required_years(job_description)
    resume_years = parsed_resume.get("years_experience") or extract_resume_years(resume_text)
    experience_gap = (
        required_years is not None
        and (resume_years is None or resume_years < required_years)
    )
    if experience_gap:
        missing_keywords = merge_unique(
            missing_keywords + [f"{required_years}+ years experience"]
        )

    semantic_score = compute_semantic_score(
        resume_text_for_embeddings,
        job_description,
        debug_info,
    )
    weight_total = SEMANTIC_WEIGHT + MUST_COVERAGE_WEIGHT + NICE_COVERAGE_WEIGHT
    combined_score = (
        semantic_score * SEMANTIC_WEIGHT
        + must_coverage * 100 * MUST_COVERAGE_WEIGHT
        + nice_coverage * 100 * NICE_COVERAGE_WEIGHT
    )
    base_score = combined_score / weight_total if weight_total else semantic_score
    other_missing = max(0, len(missing_keywords) - len(missing_must_have))
    penalty = len(missing_must_have) * PENALTY_MUST_HAVE + other_missing * PENALTY_OTHER
    base_score = max(0.0, round(base_score - penalty, 2))

    ats_result = compute_ats_score(
        resume_sections=resume_sections,
        job_description=job_description,
        semantic_score=semantic_score,
    )
    if ats_result["profile"] == "swe":
        match_score = round(
            (1 - ATS_BLEND_WEIGHT) * base_score
            + ATS_BLEND_WEIGHT * ats_result["score"],
            2,
        )
    else:
        match_score = base_score
    match_score = max(0.0, min(100.0, match_score))

    response = {
        "match_score": match_score,
        "missing_keywords": missing_keywords,
        "resume_text": resume_text,
        "section_feedback": build_section_feedback(
            resume_sections_raw,
            resume_sections,
            job_description,
        ),
    }
    if debug:
        response["debug"] = {
            **(debug_info or {}),
            "missing_keywords_count": len(missing_keywords),
            "missing_keywords_sample": missing_keywords[:20],
            "coverage_must": round(must_coverage * 100, 2),
            "coverage_nice": round(nice_coverage * 100, 2),
            "semantic_score": round(semantic_score, 2),
            "combined_score": round(combined_score / weight_total, 2)
            if weight_total
            else round(semantic_score, 2),
            "base_score": base_score,
            "penalty": penalty,
            "ats_score": ats_result["score"],
            "ats_profile": ats_result["profile"],
            "ats_breakdown": ats_result["breakdown"],
            "weights": {
                "semantic": SEMANTIC_WEIGHT,
                "must_coverage": MUST_COVERAGE_WEIGHT,
                "nice_coverage": NICE_COVERAGE_WEIGHT,
                "ats_blend": ATS_BLEND_WEIGHT,
            },
            "skills_loaded": len(SKILLS_SET),
            "skills_path": SKILLS_PATH,
            "resume_excerpt": resume_text[:1200],
        }
    return response


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
    text = body.get_text(separator="\n", strip=True)

    return {"job_text": text}


@app.post("/extract-resume")
async def extract_resume(resume: UploadFile = File(...)):
    file_bytes = await resume.read()
    resume_text = extract_pdf_text(file_bytes)
    return {"resume_text": resume_text}
