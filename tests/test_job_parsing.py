from types import SimpleNamespace

import main


class FakeModels:
    def __init__(self, text: str):
        self._text = text

    def generate_content(self, **kwargs):
        return SimpleNamespace(text=self._text)


class FakeClient:
    def __init__(self, text: str):
        self.models = FakeModels(text)


BLACKROCK_JD = """
About This Role

Global Infrastructure Solutions - Infrastructure Investment Professional (Associate-Level)

Corporate Title

Associate

Background

BlackRock is one of the world's preeminent asset management firms and a premier provider of global investment management, risk management, and advisory services to institutional, intermediary, and individual investors around the world.

Global Infrastructure Solutions is the firm's infrastructure private equity multi-manager solutions group with offices in New York City, London, Zurich, Budapest and Hong Kong. GIS has one of the most experienced infrastructure multi-manager teams in the industry.

Key Responsibilities

Deal Underwriting & Execution: Supporting the evaluation and execution of transactions, including: conducting market research; developing complex financial models and analyses; coordinating commercial, legal, and tax workstreams; collaborating with team members and cross-functional resources; creating investment memos; and presenting investment recommendations to investment committees and clients
Portfolio Monitoring & Management: Monitoring and managing investments; covering GP relationships; preparing materials in support of performance review meetings

Preferred Qualifications & Skills

Relevant Experience: 2+ years of infrastructure investment experience, with a strong preference for candidates with secondaries underwriting experience (GP-led and LP-led transactions) or principal investing experience
Investment Capabilities: Proven quantitative capabilities and experience with building and analyzing financial models.
Tools: Expert in Microsoft Excel, PowerPoint, Word and a range of AI models (Copilot, ChatGPT, Tolt etc.)
Interpersonal Skills: Team player with strong interpersonal, communication and presentation skills
Language: Fluency in English with professional competency in other language(s) desirable
Education: Bachelor's degree with a strong academic record

About BlackRock

At BlackRock, we are all connected by one mission: to help more and more people experience financial well-being.
"""


def test_blackrock_requirement_extraction_excludes_company_background():
    requirements = main.extract_local_job_requirements(BLACKROCK_JD)
    texts = [req["text"] for req in requirements]
    joined = " ".join(texts).lower()

    assert "preeminent asset management" not in joined
    assert "one of the most experienced infrastructure multi-manager teams" not in joined
    assert "financial well-being" not in joined
    assert "2+ years of infrastructure investment experience" in joined
    assert "financial models" in joined
    assert "microsoft excel" in joined


def test_requirement_merge_rejects_model_company_background_output():
    generated = [
        {
            "text": "BlackRock is one of the world's preeminent asset management firms and a premier provider of global investment management.",
            "category": "essential",
        },
        {
            "text": "2+ years of infrastructure investment experience",
            "category": "essential",
        },
        {
            "text": "Principal investing experience",
            "category": "nice_to_have",
        },
    ]

    merged = main.merge_job_requirements(generated, [], limit=10)
    texts = [req["text"] for req in merged]

    assert texts == [
        "2+ years of infrastructure investment experience",
        "Principal investing experience",
    ]


def test_blackrock_associate_role_is_not_inferred_as_principal():
    seniority = main.infer_role_seniority(BLACKROCK_JD)

    assert seniority["level"] == main.SENIORITY_LEVELS["associate"]
    assert seniority["terms"] == ["associate"]
    assert seniority["label"] == "associate/mid"


def test_principal_investing_phrase_is_not_seniority_term():
    terms = main.extract_seniority_terms(
        "Relevant experience with secondaries underwriting or principal investing experience."
    )

    assert "principal" not in terms


def test_ats_keywords_are_limited_to_candidate_facing_requirements(monkeypatch):
    fake_json = """
    {
      "skills": {"must_have": [], "nice_to_have": []},
      "ats_keywords": {
        "hard_skills": [
          {"skill": "BlackRock", "jd_count": 5, "cv_count": 0},
          {"skill": "assets under management", "jd_count": 1, "cv_count": 0},
          {"skill": "financial models", "jd_count": 1, "cv_count": 0},
          {"skill": "Microsoft Excel", "jd_count": 1, "cv_count": 1}
        ],
        "soft_skills": [
          {"skill": "around the world", "jd_count": 1, "cv_count": 0},
          {"skill": "communication skills", "jd_count": 1, "cv_count": 1}
        ]
      }
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    parsed_resume = {
        "_resume_text": "Experience\nBuilt financial models in Microsoft Excel and presented analysis with clear communication skills.",
        "summary": "",
        "skills": ["Microsoft Excel", "communication skills"],
        "tools": ["Microsoft Excel"],
        "work_experience": [],
        "projects": [],
    }

    result = main.gemini_skills_and_ats(
        BLACKROCK_JD,
        parsed_resume,
        parsed_resume["_resume_text"],
    )
    hard = [item["skill"] for item in result["ats_keywords"]["hard_skills"]]
    soft = [item["skill"] for item in result["ats_keywords"]["soft_skills"]]

    assert hard == ["financial models", "Microsoft Excel"]
    assert soft == ["communication skills"]


FCA_JD = """
About The FCA And Team

Market Oversight is responsible for overseeing primary and secondary market participants through the listing, prospectus, and market abuse regimes.
The Transaction & Position Reporting team is responsible for the supervision & policy of the MiFID (RTS 22/23/24), EMIR & SFTR reporting regimes.

Role Responsibilities

Contributing to the development of transaction reporting policy rules and guidelines, drafting consultations and coordinating industry engagement as part of our ongoing reviews of the UK MiFIR, EMIR & SFTR regimes
Using data to identify trends, outliers and insights to support policy development
Providing technical advice and support to users of the data across the FCA

Minimum

Skills required

Prior experience developing and implementing policies or regulations in a public authority, firm or other relevant organisation
Clear written communication skills, demonstrated through experience writing regulatory documents

Essential

Effective communication skills, with ability to explain complex, technical matters clearly and succinctly to a range of audiences
Effective interpersonal skills with capacity to work collaboratively and engage a range of stakeholders
"""


def test_fca_requirement_extraction_excludes_team_background_and_fragments():
    requirements = main.extract_local_job_requirements(FCA_JD)
    texts = [req["text"] for req in requirements]
    joined = " ".join(texts).lower()

    assert "market oversight is responsible" not in joined
    assert "team is responsible" not in joined
    assert "effective communication skills" in joined
    assert "ability to explain complex" not in texts
    assert any("transaction reporting policy rules" in text.lower() for text in texts)


def test_ats_keyword_filter_rejects_generic_single_word_noise():
    blob = main._candidate_requirement_text_blob(FCA_JD)

    assert not main.is_valid_ats_keyword("data", blob)
    assert not main.is_valid_ats_keyword("policy", blob)
    assert not main.is_valid_ats_keyword("regimes", blob)
    assert not main.is_valid_ats_keyword("technical", blob)
    assert main.is_valid_ats_keyword("transaction reporting policy", blob)
    assert main.is_valid_ats_keyword("MiFIR", blob)
    assert main.is_valid_ats_keyword("EMIR", blob)


EVERNOTE_JD = """
About the job

Evernote is a note-taking and organization platform that millions of users rely on for powerful note taking, project planning, personal knowledge management, and more.
Now part of Bending Spoons, it plays a key role in a portfolio of outstanding digital businesses united by a shared focus on innovation and operational excellence.
By applying through the Evernote brand, you'll be stepping into the wider Bending Spoons team.
You may work directly on Evernote or contribute to one of our other leading products.

A few examples of your responsibilities

Build stuff that matters. Take real ownership from idea to production, creating systems used by millions and evolving them into products at scale.
Amplify your impact with AI. Integrate the most powerful AI tools directly into your development workflow - design, implementation, testing, and documentation - to move faster while maintaining high standards for correctness, reliability, and maintainability.
Master your toolkit. Work across diverse stacks with end-to-end ownership, choosing the right technologies for each challenge. From monoliths to microservices, gRPC to REST, Kubernetes to Docker, Python to Rust - you'll apply technologies thoughtfully, focusing on depth and purpose rather than trends.
Simplify relentlessly. Question every layer of complexity. Improve architectures, pipelines, and codebases to build systems that are simpler, more scalable, and easier to maintain.

What we look for

Reasoning ability. Given the necessary knowledge, you can solve complex problems.
Drive. You're extremely ambitious in everything you do.
Team spirit. You give generously and without the expectation of receiving in return.
Proficiency in English. You read, write, and speak proficiently in English.

What we offer

Competitive pay and access to equity in the company.
All. These. Benefits. Flexible hours, remote working, health insurance, relocation package, generous parental support, and a yearly retreat.

The selection process

All applications go through our careers page, which is the only way to be considered.
"""


def test_evernote_requirement_extraction_excludes_company_and_application_text():
    requirements = main.extract_local_job_requirements(EVERNOTE_JD)
    texts = [req["text"] for req in requirements]
    joined = " ".join(texts).lower()

    assert "note-taking and organization platform" not in joined
    assert "by applying through the evernote brand" not in joined
    assert "you may work directly on evernote" not in joined
    assert "all applications go through" not in joined
    assert "benefits" not in joined
    assert any("real ownership from idea to production" in text.lower() for text in texts)
    assert any("ai tools directly into your development workflow" in text.lower() for text in texts)
    assert any("grpc to rest" in text.lower() for text in texts)
    assert any("proficiency in english" in text.lower() for text in texts)


def test_evernote_ats_keywords_are_augmented_when_model_under_returns(monkeypatch):
    fake_json = """
    {
      "skills": {"must_have": [], "nice_to_have": []},
      "ats_keywords": {
        "hard_skills": [
          {"skill": "Python", "jd_count": 1, "cv_count": 1},
          {"skill": "Docker", "jd_count": 1, "cv_count": 1},
          {"skill": "Kubernetes", "jd_count": 1, "cv_count": 0}
        ],
        "soft_skills": [
          {"skill": "team spirit", "jd_count": 1, "cv_count": 0}
        ]
      }
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    parsed_resume = {
        "_resume_text": "Skills\nPython, Docker, REST APIs, microservices\nProjects\nBuilt FastAPI services with testing and documentation.",
        "summary": "",
        "skills": ["Python", "Docker", "REST APIs", "microservices", "testing", "documentation"],
        "tools": ["Docker"],
        "work_experience": [],
        "projects": [],
    }

    result = main.gemini_skills_and_ats(
        EVERNOTE_JD,
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    hard = {item["skill"]: item for item in result["ats_keywords"]["hard_skills"]}
    assert {"Python", "Docker", "Kubernetes", "Rust", "gRPC", "REST", "microservices"}.issubset(hard)
    assert hard["REST"]["status"] == "present"
    assert hard["Rust"]["status"] == "missing"
    assert hard["gRPC"]["status"] == "missing"


def test_english_proficiency_does_not_match_unrelated_reading_writing_words():
    parsed_resume = {
        "_resume_text": "Projects\nBuilt a CV matching platform that reads PDF files and writes analysis reports.",
        "summary": "",
        "skills": ["Python", "PDF parsing"],
        "tools": [],
        "education": [],
        "projects": [
            {
                "name": "CV matching platform",
                "bullets": ["Reads PDF files and writes analysis reports for job descriptions."],
            }
        ],
        "work_experience": [],
    }

    result = main.aggregate_requirement_evidence(
        "Proficiency in English. You read, write, and speak proficiently in English.",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert result["status"] == "missing"
    assert result["matched_count"] == 0


def test_million_scale_requirement_is_not_proven_by_small_user_count():
    parsed_resume = {
        "_resume_text": "Projects\nBuilt a production app serving 60+ active users.",
        "summary": "",
        "skills": ["Python", "FastAPI"],
        "tools": [],
        "education": [],
        "projects": [
            {
                "name": "Production app",
                "bullets": ["Built and deployed a production app serving 60+ active users."],
            }
        ],
        "work_experience": [],
    }

    result = main.aggregate_requirement_evidence(
        "creating systems used by millions and evolving them into products at scale",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert result["status"] == "missing"
