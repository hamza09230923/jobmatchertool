from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


JD_TEXT = """Data Analyst

Responsibilities
Build Python and SQL reports for business stakeholders.
Improve data quality checks and dashboard reliability.
Collaborate with finance and operations teams.
Automate recurring Excel reporting processes.

Required Skills
Python, SQL, Excel, clear communication, data validation.

Preferred Skills
Power BI or dashboarding experience and Git familiarity.
"""


CV_TEMPLATE = """Alex Morgan {idx}
London, UK | alex{idx}@example.test

Professional Summary
Data analyst with hands-on experience using Python, SQL, Excel, reporting automation, dashboarding, and stakeholder communication.

Core Skills
Python, SQL, Excel, Power BI, dashboarding, data validation, stakeholder communication, Git, Agile

Professional Experience
Data Analyst - Northbridge Analytics | Jan 2023 - Present
- Built Python and SQL reporting workflows for weekly finance and operations dashboards.
- Improved data validation checks and reduced reporting errors by {metric} percent.
- Collaborated with finance, operations, and product stakeholders to prioritise analysis requests.
- Automated Excel reporting packs and saved roughly {hours} hours per month.

Junior Reporting Analyst - Meridian Services | Jun 2021 - Dec 2022
- Maintained KPI reports in Excel and SQL for a customer operations team.
- Documented reporting definitions and improved dashboard consistency.

Education
BSc Mathematics, Example University, 2021
"""


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf_bytes(text: str) -> bytes:
    lines = [_pdf_escape(line[:110]) for line in text.splitlines()]
    content = "BT /F1 10 Tf 50 780 Td 13 TL\n"
    for line in lines:
        content += f"({line}) Tj T*\n"
    content += "ET\n"
    stream = content.encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_at = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


class FakeModels:
    def __init__(self):
        self.lock = threading.Lock()
        self.generate_calls = 0
        self.embed_calls = 0

    def generate_content(self, **kwargs):
        contents = str(kwargs.get("contents") or "")
        with self.lock:
            self.generate_calls += 1
        if "expert CV parser" in contents:
            return SimpleNamespace(text=json.dumps({
                "name": "Alex Morgan",
                "location": "London, UK",
                "summary": "Data analyst with Python, SQL, Excel, dashboarding and stakeholder communication experience.",
                "links": {"linkedin": None, "github": None, "portfolio": None, "other": []},
                "skills": ["Python", "SQL", "Excel", "Power BI", "dashboarding", "data validation", "stakeholder communication", "Git", "Agile"],
                "tools": ["Power BI", "Git", "Excel"],
                "soft_skills": ["communication", "collaboration"],
                "languages": [{"language": "English", "proficiency": "Fluent"}],
                "years_experience": 3,
                "industry_domains": ["analytics"],
                "management_experience": {"has_managed": False, "max_team_size": None},
                "work_experience": [
                    {
                        "company": "Northbridge Analytics",
                        "title": "Data Analyst",
                        "start_date": "01/2023",
                        "end_date": "Present",
                        "bullets": [
                            "Built Python and SQL reporting workflows for weekly finance and operations dashboards.",
                            "Improved data validation checks and reduced reporting errors by 30 percent.",
                            "Collaborated with finance, operations, and product stakeholders to prioritise analysis requests.",
                            "Automated Excel reporting packs and saved roughly 6 hours per month.",
                        ],
                    }
                ],
                "employment_gaps": [],
                "projects": [],
                "education": [{"degree": "BSc Mathematics", "institution": "Example University", "graduation_year": "2021", "gpa": None}],
                "certifications": [],
                "achievements": [],
                "quantified_achievements": [],
            }))
        if "strict job-description preflight judge" in contents:
            return SimpleNamespace(text=json.dumps({
                "items": [],
                "ats_keywords": {
                    "hard_skills": ["Python", "SQL", "Excel", "Power BI", "dashboarding", "Git"],
                    "soft_skills": ["communication", "collaboration"],
                },
                "quality": {"makes_sense": True, "confidence": "medium", "issues": [], "excluded_noise": []},
            }))
        if "strict atom-level CV evidence selector" in contents:
            marker = "ATOM VERIFICATION PACKETS:\n"
            packets = json.loads(contents.split(marker, 1)[1]) if marker in contents else []
            matches, missing = [], []
            for packet in packets:
                evidence = packet.get("candidate_evidence") or []
                if evidence:
                    matches.append({"atom_id": packet["atom_id"], "evidence_id": evidence[0]["evidence_id"], "confidence": "strong"})
                else:
                    missing.append({"atom_id": packet["atom_id"], "gap": "No direct synthetic evidence."})
            return SimpleNamespace(text=json.dumps({"atom_matches": matches, "atom_missing": missing}))
        if "Complete TWO tasks" in contents:
            return SimpleNamespace(text=json.dumps({
                "skills": {
                    "must_have": [
                        {"skill": "Python", "present": True, "cv_where": "SKILLS: Python, SQL, Excel, Power BI"},
                        {"skill": "SQL", "present": True, "cv_where": "SKILLS: Python, SQL, Excel, Power BI"},
                        {"skill": "Excel", "present": True, "cv_where": "SKILLS: Python, SQL, Excel, Power BI"},
                        {"skill": "data validation", "present": True, "cv_where": "Improved data validation checks and reduced reporting errors by 30 percent."},
                        {"skill": "clear communication", "present": True, "cv_where": "Collaborated with finance, operations, and product stakeholders."},
                    ],
                    "nice_to_have": [
                        {"skill": "Power BI", "present": True, "cv_where": "SKILLS: Power BI"},
                        {"skill": "dashboarding experience", "present": False, "cv_where": None},
                        {"skill": "Git familiarity", "present": True, "cv_where": "SKILLS: Git"},
                    ],
                },
                "ats_keywords": {
                    "hard_skills": [{"skill": "Python", "jd_count": 1, "cv_count": 2}, {"skill": "SQL", "jd_count": 1, "cv_count": 2}],
                    "soft_skills": [{"skill": "communication", "jd_count": 1, "cv_count": 1}],
                },
            }))
        return SimpleNamespace(text="{}")

    def embed_content(self, **kwargs):
        contents = kwargs.get("contents") or []
        with self.lock:
            self.embed_calls += 1
        embeddings = []
        for text in contents:
            vector = [0.0] * 12
            for index, token in enumerate(main.normalize_phrase(str(text)).split()[:80]):
                vector[index % len(vector)] += (len(token) % 7) + 1
            embeddings.append(SimpleNamespace(values=vector))
        return SimpleNamespace(embeddings=embeddings)


def configure_app(use_real_api: bool):
    main.auth_utils.decode_jwt = lambda token: {"sub": "999999", "email": "stress@example.test"} if token else None
    main.db.get_user_by_id = lambda user_id: {
        "id": int(user_id),
        "email": "stress@example.test",
        "tier": "paid",
        "email_verified": True,
        "lifetime_scans": 0,
    }
    main.db.increment_lifetime_scans = lambda user_id: None
    main.textrazor_extract_phrases = lambda text, debug_info=None: []
    cache = {}
    main.get_cached_analyze_response = lambda key: cache.get(key)
    main.set_cached_analyze_response = lambda key, response, resume_text="", job_description="": cache.setdefault(key, response)
    fake_models = None
    if not use_real_api:
        fake_models = FakeModels()
        main.GENAI_CLIENT = SimpleNamespace(models=fake_models)
    return fake_models


def run_one(index: int, payload: bytes) -> dict:
    client = TestClient(main.app)
    start = time.perf_counter()
    response = client.post(
        "/analyze?debug=true",
        files={"resume": (f"synthetic-{index}.pdf", io.BytesIO(payload), "application/pdf")},
        data={"job_description": JD_TEXT, "job_source": "paste"},
        headers={"Authorization": "Bearer stress-token"},
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    return {
        "index": index,
        "status": response.status_code,
        "elapsed_ms": round(elapsed_ms, 1),
        "score": body.get("match_score"),
        "missing_keywords": body.get("missing_keywords"),
        "error": body.get("detail") if response.status_code != 200 else None,
    }


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Synthetic /analyze stress test.")
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--real-api", action="store_true", help="Use configured APIs with synthetic data. Default uses mocked AI.")
    args = parser.parse_args()

    fake_models = configure_app(use_real_api=args.real_api)
    payloads = [
        make_pdf_bytes(CV_TEMPLATE.format(idx=i, metric=20 + i, hours=3 + (i % 5)))
        for i in range(args.requests)
    ]

    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(run_one, i, payloads[i]) for i in range(args.requests)]
        for future in as_completed(futures):
            results.append(future.result())
    wall_ms = (time.perf_counter() - started) * 1000

    latencies = [item["elapsed_ms"] for item in results]
    status_counts = {}
    for item in results:
        status_counts[str(item["status"])] = status_counts.get(str(item["status"]), 0) + 1

    summary = {
        "mode": "real-api" if args.real_api else "mock-ai",
        "requests": len(results),
        "concurrency": args.concurrency,
        "wall_ms": round(wall_ms, 1),
        "status_counts": status_counts,
        "latency_ms": {
            "min": min(latencies),
            "median": round(statistics.median(latencies), 1),
            "p90": round(sorted(latencies)[max(0, int(len(latencies) * 0.9) - 1)], 1),
            "max": max(latencies),
        },
        "score_range": [
            min(item["score"] for item in results if item["score"] is not None),
            max(item["score"] for item in results if item["score"] is not None),
        ] if any(item["score"] is not None for item in results) else None,
        "sample_missing_keywords": next((item["missing_keywords"] for item in results if item["status"] == 200), []),
        "errors": [item for item in sorted(results, key=lambda row: row["index"]) if item["status"] != 200][:5],
    }
    if fake_models is not None:
        summary["fake_ai_calls"] = {
            "generate_content": fake_models.generate_calls,
            "embed_content": fake_models.embed_calls,
        }
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0 if all(item["status"] == 200 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
