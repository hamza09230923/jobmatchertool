"""Live /analyze run: Hamza's hyperexponential CV + the Superhighway full-stack JD."""
import json
import os
import sys
import tempfile
from pathlib import Path

import requests
from fpdf import FPDF

API_BASE = os.getenv("API_BASE", "http://localhost:8011")

CV_TEXT = """HAMZA ABDIKADIR
London, UK | Hybrid / Remote
Email: habdikadir99@gmail.com
GitHub: github.com/hamza09230923

PROFILE
Computer Science with Artificial Intelligence graduate with hands-on experience building AI, automation, data and full-stack software solutions across education, recruitment and finance-related use cases. Strong technical foundations in Python, data analysis, backend development, AI tooling, NLP, embeddings, machine learning and workflow automation, with experience turning ambiguous problems into practical technical solutions.

A strong fit for hyperexponential's Graduate Technical Programme, with clear evidence of building real products, experimenting with AI tools, communicating technical ideas to non-technical users and taking ownership of outcomes beyond academic work. Experienced working across Python, FastAPI, React, Firebase, AWS serverless services, Docker, CI/CD, SQL, NLP and model evaluation, while developing commercial awareness through customer-facing work, operational problem-solving and product-focused technical projects.

Interested in how AI, data and model development can improve complex decision-making in insurance pricing, underwriting and portfolio management.

KEY SKILLS
Programming & Data: Python, SQL, MySQL, JavaScript, data analysis, structured datasets, validation checks, KPI reporting, data pipelines, feature engineering, model evaluation
AI, Machine Learning & Automation: Generative AI tools, LLM-assisted analysis, prompt design, embeddings, NLP, semantic matching, FinBERT, XGBoost, reinforcement learning, AI workflow prototyping, automation
Engineering & Product Development: FastAPI, REST APIs, React, Vite, Firebase, Node.js, Docker, Git, GitHub Actions, AWS Lambda, Step Functions, DynamoDB, CI/CD, serverless architecture
Business, Product & Delivery: Agile delivery, stakeholder engagement, requirements gathering, technical documentation, problem discovery, customer-focused product thinking, communicating technical findings, ownership, working in ambiguity

PROFESSIONAL EXPERIENCE

Co-Founder & AI/Data Analyst - MySchola
London | Jan 2019 - Present
- Built and improved digital workflows for an education platform supporting 50+ active users, translating parent, tutor and student needs into progress tracking, KPI reporting and operational improvements.
- Used Python-supported automation, structured records and validation checks to reduce repetitive administration, improve data consistency and make attendance, student progress and operational records more reliable.
- Analysed enquiry, booking, attendance, conversion and campaign data to identify performance trends, improve lead handling and support better operational decision-making.
- Created KPI reporting outputs across enquiries, bookings, attendance, conversion rates and campaign performance.
- Supported the development of a full-stack platform using React, Firebase and serverless backend logic, improving scalability and reducing reliance on manual administration.
- Worked directly with parents, tutors and students to gather requirements, understand pain points, prioritise improvements and communicate technical or operational recommendations clearly.
- Took ownership of ambiguous operational problems, including scheduling clashes, reporting gaps and inconsistent data capture.
- Presented insights and recommendations in parent meetings and webinars, explaining performance trends to non-technical audiences.

Full Stack Developer - MHR
Nottingham | Sept 2023 - May 2024
- Worked in a 7-person agile team to develop a cloud-based educational application over an 8-month delivery cycle.
- Built features across frontend, backend and database layers, including user account management, content storage and learner progress tracking functionality.
- Developed backend services using Python and MySQL, supporting reliable data handling between the application, APIs and database.
- Implemented AI-driven features including quiz generation and content summarisation, using emerging AI tools to improve user engagement and learning outcomes.
- Collaborated through agile ceremonies including stand-ups, sprint planning and retrospectives.
- Used Git workflows including branching, pull requests and code reviews to support maintainable, collaborative and reliable software delivery.
- Contributed to an on-time project delivery by combining technical development with documentation, testing, communication and stakeholder updates.

Customer Support Specialist - Sitel UK, NHS Test & Trace
Jul 2021 - Oct 2021
- Processed 100+ records per day in a regulated environment while maintaining accuracy, confidentiality and data-handling standards.
- Communicated clearly with users while handling sensitive information, following strict operational procedures.

Technical Administrator - Addo
Nottingham | Mar 2022 - May 2022
- Improved Excel-based reporting processes by restructuring templates and reducing manual data entry through formula-driven automation.
- Maintained structured datasets used for internal reporting, scheduling and operational tracking.

SELECTED TECHNICAL PROJECTS

Shortlistly.co.uk - Full-Stack AI CV Matching Platform
Tech: Python, FastAPI, React, Vite, Gemini embeddings, TextRazor, scikit-learn, REST APIs, JSON
- Built a full-stack AI platform that analyses CVs against job descriptions and generates match scores with tailored improvement suggestions.
- Developed a FastAPI backend for PDF parsing, job description processing, semantic analysis and structured scoring.
- Implemented NLP-driven matching using embeddings, TF-IDF fallback methods, keyword extraction, synonym normalisation and section-weighted scoring.
- Built a React/Vite frontend and REST API integration to present complex matching outputs clearly for non-technical users.
- Improved reliability by testing matching outputs, identifying false positives and refining scoring logic.

Automated Expense Fraud Detection & Compliance System
Tech: AWS Lambda, Step Functions, DynamoDB, Python, Amazon Textract, Amazon Comprehend
- Designed a serverless compliance workflow to automate expense receipt reconciliation and flag suspicious financial patterns.
- Used event-driven AWS services including Lambda, Step Functions and DynamoDB to process documents and structure financial records.
- Built Python anomaly-detection logic to reduce manual review effort.

AI-Powered Financial Prediction & Sentiment Analysis Platform
Tech: Python, XGBoost, FinBERT, Docker, AWS, GitHub Actions, Streamlit, Plotly
- Developed a financial prediction platform combining market data with FinBERT sentiment analysis.
- Built automated pipelines for data ingestion, feature engineering and time-series analysis.
- Containerised the application with Docker and deployed it using GitHub Actions CI/CD to AWS.

Multi-Agent Reinforcement Learning Traffic Simulation
Tech: Python, OpenAI Gym, PyGame, PPO, A2C, QMIX
- Built a custom simulation environment to test how reinforcement learning could optimise urban traffic flow.
- Implemented and compared multiple reinforcement learning algorithms including PPO, A2C and QMIX.
- Achieved an 18% reduction in simulated emergency response times through adaptive signal coordination.

EDUCATION
BSc (Hons) Computer Science with Artificial Intelligence
University of Nottingham | 2021 - 2025
"""

JD_TEXT = """Full-Stack Developer (Front-End Focus) - Superhighway Control Plane

Role:
Full-stack developers with a strong front-end focus, responsible for building the Superhighway control plane and the golden-path tooling engineers use to ship.

This is an AI-enabled role: you'll work agentic-first, using an agentic harness (Claude or similar) to drive implementation, not as an occasional assist. You're as comfortable orchestrating agents to generate, validate, and test code as you are writing it yourself.

The role pairs modern front-end development (React) with working knowledge of Python services and APIs.

Key Responsibilities:
- Develop and maintain front-end components using React for the control plane and audit/compliance/cost views
- Build responsive, scalable, high-quality UI features for self-service data exploration and platform interaction
- Drive implementation through an agentic harness: prompt, steer, and validate agent-generated code rather than hand-cranking it
- Contribute to backend development (Python/FastAPI) where required
- Build templated archetypes and golden-path kits that accelerate onboarding and development for other teams
- Collaborate with UX/UI to ensure accurate implementation of designs
- Ensure performance, security, and maintainability, with a testing-first mindset

Required Experience:
- Strong experience with React and modern JavaScript/TypeScript
- 6+ months hands-on with agentic editors (Claude, etc.) and a clear understanding of agent risks and mitigation
- Experience building web applications with API integrations
- Working knowledge of Python (FastAPI a plus) for backend/API development
- Familiarity with REST APIs and microservices architecture
- Strong focus on acceptance testing and architecture testing (unit tests enforcing standards: line limits, design patterns, imports)
- Experience with version control and CI/CD practices
- Proactive communicator who unblocks themselves and collaborates across a matrix organisation

Desirable Experience:
- Experience integrating AI/ML services (LLM APIs, embeddings, agent frameworks)
- AWS native development and deployment experience
- Policy-as-code expertise
- Snowflake or data-product experience (producing and consuming)
- Data visualisation libraries (charts, dashboards)
- Authentication and RBAC implementation
- Experience building developer platforms, golden paths, or internal tooling
"""


_jd_file = os.getenv("JD_FILE")
if _jd_file:
    JD_TEXT = Path(_jd_file).read_text(encoding="utf-8")


def make_pdf(text: str) -> Path:
    pdf = FPDF(format="A4")
    pdf.set_margins(left=10, top=10, right=10)
    pdf.add_page()
    pdf.set_font("Helvetica", size=9)
    for line in text.split("\n"):
        safe = line.encode("latin-1", "replace").decode("latin-1")
        if not safe.strip():
            pdf.ln(3)
            continue
        pdf.multi_cell(w=190, h=4, text=safe)
    path = Path(tempfile.gettempdir()) / "hx_cv.pdf"
    pdf.output(str(path))
    return path


def main():
    rand = os.urandom(4).hex()
    email = f"audit+{rand}@shortlistly.co.uk"
    r = requests.post(f"{API_BASE}/auth/signup", json={"email": email, "password": "password123"})
    r.raise_for_status()
    token = r.json()["token"]
    print(f"Auth: signed up {email}, got JWT")

    pdf_path = make_pdf(CV_TEXT)
    print(f"PDF: {pdf_path} ({pdf_path.stat().st_size} bytes)")

    with open(pdf_path, "rb") as f:
        files = {"resume": ("cv.pdf", f, "application/pdf")}
        data = {"job_description": JD_TEXT, "job_source": "paste"}
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.post(f"{API_BASE}/analyze", files=files, data=data, headers=headers, timeout=180)
    r.raise_for_status()
    result = r.json()

    # Dump full result for inspection
    out_path = Path(tempfile.gettempdir()) / "hx_result.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Full result written to {out_path}")

    print("\n" + "=" * 70)
    print(f"MATCH SCORE: {result.get('match_score')}")
    print("=" * 70)

    breakdown = result.get("role_fit_breakdown", {}) or {}
    print(f"\nJob meta: {json.dumps(breakdown.get('job_description', {}), indent=2)}")

    rd = breakdown.get("responsibility_detail", {}) or {}
    print(f"\nResponsibility match: {rd.get('matched_count')} of {rd.get('total_responsibilities')}")

    ed = breakdown.get("experience_detail", {}) or {}
    print(f"Experience: {ed.get('candidate_years')} yrs vs required {ed.get('required_years')} (meets={ed.get('meets_requirement')})")

    sd = breakdown.get("skills_detail", {}) or {}
    print(f"\nSkills must-have ({len(sd.get('must_have', []))}):")
    for s in sd.get("must_have", []):
        print(f"  - {s}")
    print(f"\nSkills nice-to-have ({len(sd.get('nice_to_have', []))}):")
    for s in sd.get("nice_to_have", []):
        print(f"  - {s}")

    matched = breakdown.get("matched_responsibilities", []) or []
    missing = breakdown.get("missing_responsibilities", []) or []
    print(f"\nMATCHED responsibilities ({len(matched)}):")
    for m in matched:
        ev = (m.get("evidence") or "")[:140]
        print(f"  [{m.get('confidence')}] {m.get('responsibility')}")
        print(f"       evidence: {ev}")

    print(f"\nMISSING responsibilities ({len(missing)}):")
    for m in missing:
        print(f"  - {m.get('responsibility')} (category={m.get('category')})")

    ats = result.get("ats_keywords", {}) or {}
    hard = ats.get("hard_skills", []) or []
    soft = ats.get("soft_skills", []) or []
    print(f"\nATS hard skills: {len(hard)} found")
    by_status = {}
    for s in hard:
        kw = s.get("keyword") or s.get("term") or s.get("name") or str(s)
        by_status.setdefault(s.get("status", "?"), []).append(kw)
    for st, ks in by_status.items():
        ks_safe = [k for k in ks if k]
        print(f"  {st}: {', '.join(ks_safe)}")
    print(f"ATS soft skills: {len(soft)} found")

    sb = result.get("score_breakdown") or {}
    print("\n" + "=" * 70)
    print("SCORE EXPLAINER")
    print("=" * 70)
    if sb:
        print(f"  current: {sb.get('current_score')}  potential: {sb.get('potential_score')}")
        print(f"  verdict: {sb.get('verdict_line')}")
        print("  Pulling DOWN:")
        for f in sb.get("factors_pulling_down", []):
            print(f"    -{f.get('points_lost')} pts: {f.get('label')} | fix: {f.get('fix')}")
        print("  Pulling UP:")
        for f in sb.get("factors_pulling_up", []):
            print(f"    + {f}")
    else:
        print("  (none)")

    print(f"\nTop-level keys: {list(result.keys())}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        print(f"Response body: {e.response.text}", file=sys.stderr)
        sys.exit(1)
