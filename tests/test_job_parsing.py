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


class RaisingModels:
    def generate_content(self, **kwargs):
        raise RuntimeError("api unavailable")


class RaisingClient:
    def __init__(self):
        self.models = RaisingModels()


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


def test_prima_preface_copy_is_not_treated_as_candidate_requirements():
    jd = """
Since 2015, we have been using our love of data and tech to rethink motor insurance.
Fueled by curiosity, experimentation and collaboration, you'll help deliver scalable solutions.
You'll be joining over 300 engineers across software development, infrastructure, operations and security.

What You'll Do

Build reusable technology that enables teams to ingest, store, transform, and serve their own data products.

What We're Looking For

Proficiency in DevOps, CI/CD pipeline management, and expertise in infrastructure as Code deployment practices.
"""

    requirements = main.extract_local_job_requirements(jd, limit=20)
    joined = " ".join(req["text"] for req in requirements).lower()

    assert "since 2015" not in joined
    assert "fueled by curiosity" not in joined
    assert "joining over 300 engineers" not in joined
    assert "build reusable technology" in joined
    assert "ci/cd pipeline management" in joined


def test_pipeline_management_is_not_line_management_seniority_signal():
    seniority = main.infer_role_seniority(
        "Data Engineer required. Proficiency in DevOps and CI/CD pipeline management is essential."
    )

    assert seniority["level"] == 0
    assert "lead" not in seniority["terms"]


def test_model_skill_cleanup_rejects_sentence_fragments():
    jd_blob = main._candidate_requirement_text_blob(
        "What We're Looking For\n"
        "Expert in Kafka, Flink and Spark. Experience in Databricks is a plus.\n"
        "You will help build and maintain complex data products."
    )

    assert main.clean_model_skill_name("Spark etc. Experience in Databricks is a plus") == "Spark"
    assert main.is_valid_model_skill("Spark", jd_blob)
    assert not main.is_valid_model_skill("You will help build", jd_blob)


def test_ats_keyword_split_keeps_ab_testing_intact():
    jd = """
What We're Looking For
Understanding of A/B testing, attribution, and customer segmentation.
"""

    hard = main._local_ats_keyword_candidates(jd)["hard"]

    assert "A/B testing" in hard
    assert "Understanding of A" not in hard


def test_preflight_job_requirements_cleans_groups_and_judges_keywords(monkeypatch):
    fake_json = """
    {
      "requirements": [
        {"text": "Since 2015, Prima has been using data and tech to rethink motor insurance.", "category": "essential"},
        {"text": "Build reusable technology that enables teams to ingest, store, transform, and serve their own data products.", "category": "essential"},
        {"text": "Experience with Apache Airflow.", "category": "nice_to_have"}
      ],
      "ats_keywords": {
        "hard_skills": ["data products", "Apache Airflow", "Prima"],
        "soft_skills": ["collaboration"]
      },
      "quality": {
        "makes_sense": true,
        "confidence": "high",
        "issues": [],
        "excluded_noise": ["Since 2015 company background"]
      }
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))

    result = main.preflight_job_requirements(
        "Since 2015, Prima has been using data and tech to rethink motor insurance.\n"
        "What You'll Do\n"
        "Build reusable technology that enables teams to ingest, store, transform, and serve their own data products.\n"
        "Nice-to-Have\n"
        "Experience with Apache Airflow."
    )

    cleaned = result["cleaned_job_description"].lower()
    assert "since 2015" not in cleaned
    assert "build reusable technology" in cleaned
    assert "apache airflow" in cleaned
    assert result["requirements_by_category"]["essential"] == [
        "Build reusable technology that enables teams to ingest, store, transform, and serve their own data products."
    ]
    assert result["requirements_by_category"]["nice_to_have"] == ["Experience with Apache Airflow."]
    hard_keywords = [item["skill"] for item in result["ats_keywords"]["hard_skills"]]
    assert "Apache Airflow" in hard_keywords
    assert "Prima" not in hard_keywords
    assert result["quality"]["makes_sense"] is True


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

    assert "financial models" in hard
    assert "Microsoft Excel" in hard
    assert "Word" in hard
    assert "BlackRock" not in hard
    assert "assets under management" not in hard
    assert "communication skills" in soft
    assert "around the world" not in soft


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


AUDIT_ASSOCIATE_JD = """
About us
Example Audit LLP is a growing professional services firm based in London.

What You'll Be Doing
Perform external audits for not-for-profit organisations.
Undertake audit planning in accordance with auditing standards.
Prepare statutory accounts and consolidated accounts from accounting records.
Complete the audit file, management letter and letter of representation.

What We're Looking For
ACA or ACCA qualified
Experience of external audits from planning through to full audit completion
Audit; planning; preparation of statutory accounts
Excel, Word, Outlook
Sage; ProAudit desirable
Team player with ethical judgement
"""


def test_accounting_ats_keywords_are_not_empty_when_model_under_returns(monkeypatch):
    fake_json = """
    {
      "skills": {
        "must_have": [
          {"skill": "external audits", "present": false, "cv_where": null},
          {"skill": "preparation of statutory accounts", "present": false, "cv_where": null},
          {"skill": "Excel", "present": true, "cv_where": "SKILLS: Excel"},
          {"skill": "Outlook", "present": false, "cv_where": null}
        ],
        "nice_to_have": [
          {"skill": "Sage", "present": false, "cv_where": null},
          {"skill": "ProAudit", "present": false, "cv_where": null}
        ]
      },
      "ats_keywords": {"hard_skills": [], "soft_skills": []}
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    parsed_resume = {
        "_resume_text": "Experience\nBuilt Excel reporting templates and maintained financial data.",
        "summary": "",
        "skills": ["Excel", "financial data"],
        "tools": ["Excel"],
        "work_experience": [],
        "projects": [],
    }

    result = main.gemini_skills_and_ats(
        AUDIT_ASSOCIATE_JD,
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    hard = {item["skill"]: item for item in result["ats_keywords"]["hard_skills"]}
    assert {"external audits", "statutory accounts", "Excel", "Outlook", "Sage", "ProAudit"}.issubset(hard)
    assert hard["Excel"]["status"] == "present"
    assert hard["Outlook"]["status"] == "missing"


def test_ats_keywords_extract_hard_and_soft_skills_when_model_returns_none(monkeypatch):
    jd = """
    About us
    ExampleCo builds internal platforms.

    Requirements
    Python, SQL, and stakeholder reporting.
    Project management and risk assessment experience.
    Communication skills, teamwork, leadership, and adaptability.
    """
    fake_json = """
    {
      "skills": {"must_have": [], "nice_to_have": []},
      "ats_keywords": {"hard_skills": [], "soft_skills": []}
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    parsed_resume = {
        "_resume_text": "Skills\nPython, SQL, communication skills\nExperience\nProduced stakeholder reporting.",
        "summary": "",
        "skills": ["Python", "SQL", "communication skills"],
        "tools": ["Python", "SQL"],
        "work_experience": [],
        "projects": [],
    }

    result = main.gemini_skills_and_ats(jd, parsed_resume, parsed_resume["_resume_text"])
    hard = {item["skill"]: item for item in result["ats_keywords"]["hard_skills"]}
    soft = {item["skill"]: item for item in result["ats_keywords"]["soft_skills"]}

    assert {"Python", "SQL", "stakeholder reporting", "project management", "risk assessment"}.issubset(hard)
    assert {"communication skills", "teamwork", "leadership", "adaptability"}.issubset(soft)
    assert hard["Python"]["status"] == "present"
    assert hard["risk assessment"]["status"] == "missing"
    assert soft["communication skills"]["status"] == "present"


def test_skills_are_augmented_when_model_under_extracts_requirements(monkeypatch):
    jd = """
    What you'll do
    Maintain content in a content management system and proofread campaign copy.

    What we're looking for
    Excellent written English, proofreading, copyediting, and attention to detail.
    Content management system experience is essential.
    SEO knowledge is desirable.
    Google Analytics or similar reporting tools are desirable.
    """
    fake_json = """
    {
      "skills": {
        "must_have": [
          {"skill": "proofreading", "present": true, "cv_where": "SKILLS: proofreading"}
        ],
        "nice_to_have": []
      },
      "ats_keywords": {"hard_skills": [], "soft_skills": []}
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    parsed_resume = {
        "_resume_text": "Profile\nStrong written English.\nSkills\nProofreading, copyediting.",
        "summary": "Strong written English.",
        "skills": ["proofreading", "copyediting", "written English"],
        "tools": [],
        "work_experience": [],
        "projects": [],
    }

    result = main.gemini_skills_and_ats(jd, parsed_resume, parsed_resume["_resume_text"])
    must = {item["skill"]: item for item in result["skills"]["must_have"]}
    nice = {item["skill"]: item for item in result["skills"]["nice_to_have"]}

    assert must["written English"]["status"] == "present"
    assert must["copyediting"]["status"] == "present"
    assert must["content management system"]["status"] == "missing"
    assert nice["SEO"]["status"] == "missing"
    assert nice["Google Analytics"]["status"] == "missing"


def test_model_claimed_skill_evidence_must_survive_validator(monkeypatch):
    jd = """
    What we're looking for
    Content management system experience is essential.
    Adobe InDesign experience is desirable.
    """
    fake_json = """
    {
      "skills": {
        "must_have": [
          {
            "skill": "content management system",
            "present": true,
            "cv_where": "[Communications Intern @ Charity] Maintained a shared content calendar in Microsoft Excel."
          }
        ],
        "nice_to_have": [
          {
            "skill": "Adobe InDesign",
            "present": true,
            "cv_where": "[Editor @ Newspaper] Managed weekly style consistency and image captions."
          }
        ]
      },
      "ats_keywords": {"hard_skills": [], "soft_skills": []}
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    parsed_resume = {
        "_resume_text": "Experience\nMaintained a shared content calendar in Microsoft Excel.\nManaged weekly style consistency and image captions.",
        "summary": "",
        "skills": ["content calendars", "Microsoft Excel", "style consistency"],
        "tools": ["Microsoft Excel"],
        "work_experience": [
            {
                "title": "Communications Intern",
                "company": "Charity",
                "bullets": ["Maintained a shared content calendar in Microsoft Excel."],
            },
            {
                "title": "Editor",
                "company": "Newspaper",
                "bullets": ["Managed weekly style consistency and image captions."],
            },
        ],
        "projects": [],
    }

    result = main.gemini_skills_and_ats(jd, parsed_resume, parsed_resume["_resume_text"])
    must = {item["skill"]: item for item in result["skills"]["must_have"]}
    nice = {item["skill"]: item for item in result["skills"]["nice_to_have"]}

    assert must["content management system"]["status"] == "missing"
    assert must["content management system"]["cv_where"] is None
    assert nice["Adobe InDesign"]["status"] == "missing"
    assert nice["Adobe InDesign"]["cv_where"] is None


def test_ats_recovery_ignores_company_about_text_even_when_keywords_present(monkeypatch):
    jd = """
    About us
    ExampleCo is known for leadership, teamwork, Python, AWS, and risk assessment across the industry.

    What You'll Do
    Build monthly reporting packs in Excel.

    What We're Looking For
    Clear communication skills and stakeholder management.
    """
    fake_json = """
    {
      "skills": {"must_have": [], "nice_to_have": []},
      "ats_keywords": {"hard_skills": [], "soft_skills": []}
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    parsed_resume = {
        "_resume_text": "Skills\nExcel, communication skills",
        "summary": "",
        "skills": ["Excel", "communication skills"],
        "tools": ["Excel"],
        "work_experience": [],
        "projects": [],
    }

    result = main.gemini_skills_and_ats(jd, parsed_resume, parsed_resume["_resume_text"])
    hard = {item["skill"] for item in result["ats_keywords"]["hard_skills"]}
    soft = {item["skill"] for item in result["ats_keywords"]["soft_skills"]}

    assert "Excel" in hard
    assert "Python" not in hard
    assert "AWS" not in hard
    assert "risk assessment" not in hard
    assert "communication skills" in soft
    assert "stakeholder management" in soft
    assert "leadership" not in soft
    assert "teamwork" not in soft


def test_ba_style_requirements_do_not_keep_candidate_section_heading():
    jd = """
    About the job
    A Career Without Limits

    As the nation's flag carrier, we take great pride in connecting Britain with the world.

    The role: Flight Data Software Engineer

    Develop and maintain British Airways Flight Data Software System and solutions around business needs.

    What You'll Do
    Contribute in the development, design and maintenance of Amazon Web Services platform, written in Python and TypeScript, running in containers and serverless functions.
    Develop prognostics and alerts to diagnose and predict aircraft issues.
    Create visualisations support the understanding of aircraft conditions.
    Support incident investigation of aircraft and present information that provides root cause understanding, which may involve analysing historical flight data.
    Collaborate with Technical Engineers on the development of airborne software.
    Maintenance of data decode documentation.
    Maintain Flight Data Recording hardware and 3rd party software.
    Interface with Flight Operations, Corporate Safety, Analytics Teams and other parts of IAG and its companies in developing solutions.

    What You'll Bring To British Airways
    Engineering, Scientific or IT Degree with programming skills or an experienced programmer - Essential
    Software certifications e.g., AWS Certified, A Cloud Guru - Desirable
    Usage of programming languages Python, SQL, TypeScript, Net -Essential
    Interest in developing responsive web applications - Essential.
    Working with Git source control and deployment pipelines.
    Capable of supporting windows applications - Essential
    Must be capable of understanding primitive data types on a binary level - Essential.

    Why British Airways?
    Join a world-class airline.
    """

    requirements = main.extract_local_job_requirements(jd, limit=80)
    texts = [item["text"] for item in requirements]
    joined = "\n".join(texts).lower()

    assert not any("what you'll bring" in text.lower() for text in texts)
    assert "software certifications" in joined
    assert "usage of programming languages python, sql, typescript, net" in joined
    assert "windows applications" in joined
    assert "primitive data types" in joined
    assert not any("join a world-class airline" in text.lower() for text in texts)


def test_ba_style_ats_keywords_are_skills_not_sentence_fragments():
    jd = """
    What You'll Do
    Contribute in the development, design and maintenance of Amazon Web Services platform, written in Python and TypeScript, running in containers and serverless functions.
    Develop prognostics and alerts to diagnose and predict aircraft issues.
    Support incident investigation of aircraft, which may involve analysing historical flight data.
    Interface with Flight Operations, Corporate Safety, Analytics Teams and other parts of IAG and its companies in developing solutions.

    What You'll Bring To British Airways
    Software certifications e.g., AWS Certified, A Cloud Guru - Desirable
    Usage of programming languages Python, SQL, TypeScript, Net -Essential
    Working with Git source control and deployment pipelines.
    Capable of supporting windows applications - Essential
    Must be capable of understanding primitive data types on a binary level - Essential.
    """

    keywords = main._local_ats_keyword_candidates(jd)
    hard = {item for item in keywords["hard"]}

    assert {"Python", "AWS", "TypeScript", "SQL", ".NET", "Git"}.issubset(hard)
    assert "AWS Certified" in hard
    assert "deployment pipelines" in hard
    assert "responsive web applications" not in hard
    assert "Contribute in the development" not in hard
    assert "maintenance of Amazon Web Services platform" not in hard
    assert "other parts of IAG" not in hard
    assert "data pipelines" not in hard


def test_editorial_ats_keywords_are_tools_and_skills_not_fragments():
    jd = """
    What you'll do
    Draft, edit, and proofread web pages, newsletters, case studies, and campaign copy in line with our editorial style guide.
    Manage updates in a content management system and maintain an organised editorial calendar.

    What we're looking for
    English, Journalism, Communications, or Humanities degree.
    Excellent written English, proofreading, copyediting, and attention to detail.
    Strong research, synthesis, and stakeholder communication skills.
    Content management system experience is essential.
    SEO knowledge is desirable.
    Google Analytics or similar reporting tools are desirable.
    Adobe InDesign experience is desirable.
    QTS is not required.
    """

    keywords = main._local_ats_keyword_candidates(jd)
    hard = set(keywords["hard"])
    soft = set(keywords["soft"])

    assert "content management system" in hard
    assert "SEO" in hard
    assert "Google Analytics" in hard
    assert "Adobe InDesign" in hard
    assert "proofreading" in hard
    assert "copyediting" in hard
    assert "stakeholder communication" in hard
    assert "attention to detail" in soft
    assert "Adobe InDesign experience is" not in hard
    assert "Interview subject matter experts" not in hard
    assert "Experience writing for web" not in hard
    assert "charity" not in hard
    assert "QTS" not in hard
    assert "proofread web pages" not in hard
    assert "Use research" not in hard
    assert "Handle confidential student" not in hard
    assert "approved on time" not in hard


def test_editorial_local_requirements_keep_candidate_owned_action_responsibilities():
    jd = """
    What you'll do
    Draft, edit, and proofread web pages, newsletters, case studies, and campaign copy.
    Use research, reader feedback, and engagement data to recommend content improvements.
    Handle confidential student and partner information responsibly when preparing stories and reports.

    What we're looking for
    Content management system experience is essential.
    QTS is not required.
    """

    requirements = main.extract_local_job_requirements(jd, limit=80)
    texts = [item["text"] for item in requirements]
    joined = "\n".join(texts).lower()

    assert "draft, edit, and proofread web pages" in joined
    assert "use research, reader feedback" in joined
    assert "handle confidential student" in joined
    assert not any("qts is not required" in text.lower() for text in texts)


def test_single_word_tool_match_uses_token_boundary_not_substring():
    text_norm = main.normalize_phrase("Built keyword coverage and workload reporting.")
    tokens = set(text_norm.split())

    assert not main.phrase_in_resume("word", text_norm, tokens, text_norm.replace(" ", ""))
    assert not main.phrase_in_resume("outlook", text_norm, tokens, text_norm.replace(" ", ""))


def test_office_tool_bundle_does_not_invent_word_or_outlook():
    parsed_resume = {
        "_resume_text": "Experience\nBuilt keyword coverage and Excel-based reporting templates.",
        "summary": "",
        "skills": ["Excel"],
        "tools": ["Excel"],
        "work_experience": [
            {
                "title": "Administrator",
                "company": "Example",
                "bullets": ["Built keyword coverage and Excel-based reporting templates."],
            }
        ],
        "projects": [],
    }

    result = main.aggregate_requirement_evidence(
        "Excel, Word, Outlook proficiency",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert result["status"] == "partial"
    breakdown = {item["requirement"]: item["status"] for item in result["atomic_breakdown"]}
    assert breakdown["Excel"] == "present"
    assert breakdown["Word"] == "missing"
    assert breakdown["Outlook proficiency"] == "missing"


def test_supervising_juniors_is_not_proven_by_collaboration():
    parsed_resume = {
        "_resume_text": "Experience\nCollaborated through code reviews, sprint planning, stand-ups and retrospectives in a structured team environment.",
        "summary": "",
        "skills": ["collaboration"],
        "tools": [],
        "work_experience": [
            {
                "title": "Developer",
                "company": "Example",
                "bullets": [
                    "Collaborated through code reviews, sprint planning, stand-ups and retrospectives in a structured team environment."
                ],
            }
        ],
        "projects": [],
    }

    result = main.aggregate_requirement_evidence(
        "Brief, supervise and review the work of junior members of the team",
        parsed_resume,
        parsed_resume["_resume_text"],
        ai_present=True,
        ai_evidence="[Developer @ Example] Collaborated through code reviews, sprint planning, stand-ups and retrospectives in a structured team environment.",
        ai_confidence="strong",
    )

    assert result["status"] == "missing"


def test_supervising_juniors_matches_explicit_people_management():
    parsed_resume = {
        "_resume_text": "Experience\nSupervised two junior analysts, reviewed their workpapers, and provided weekly feedback.",
        "summary": "",
        "skills": [],
        "tools": [],
        "work_experience": [
            {
                "title": "Senior Analyst",
                "company": "Example",
                "bullets": ["Supervised two junior analysts, reviewed their workpapers, and provided weekly feedback."],
            }
        ],
        "projects": [],
    }

    result = main.aggregate_requirement_evidence(
        "Brief, supervise and review the work of junior members of the team",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert result["status"] == "present"


def test_generic_planning_requirement_from_model_is_rejected():
    generated = [{"text": "Experience in planning", "category": "essential"}]
    supplemental = [{"text": "Audit planning in accordance with auditing standards", "category": "essential"}]

    result = main.merge_job_requirements(generated, supplemental)
    texts = [item["text"] for item in result]

    assert "Experience in planning" not in texts
    assert "Audit planning in accordance with auditing standards" in texts


def test_why_company_section_is_excluded_from_candidate_requirements():
    jd = """
    What You'll Be Doing
    Prepare statutory accounts and complete audit files.

    Why ExampleCo?
    You'll work alongside experienced professionals who will support your continued development.
    """

    requirements = main.extract_local_job_requirements(jd)
    texts = [item["text"] for item in requirements]
    joined = " ".join(texts).lower()

    assert any("statutory accounts" in text.lower() for text in texts)
    assert "continued development" not in joined
    assert "experienced professionals" not in joined


def test_audit_document_terms_require_exact_audit_evidence():
    parsed_resume = {
        "_resume_text": "Skills\nData storytelling, stakeholder reporting, representation learning",
        "summary": "",
        "skills": ["Data storytelling", "stakeholder reporting", "representation learning"],
        "tools": [],
        "work_experience": [],
        "projects": [],
    }

    result = main.aggregate_requirement_evidence(
        "drafting of Audit Highlights Memorandum, management letter and letter of representation",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert result["status"] == "missing"


GENERIC_SOFTWARE_JD = """
About the job

ExampleCo is a productivity platform that millions of users rely on for planning, knowledge management, and collaboration.
Now part of ParentCo, it plays a key role in a portfolio of digital businesses united by innovation and operational excellence.
By applying through the ExampleCo brand, you'll be stepping into the wider ParentCo team.
You may work directly on ExampleCo or contribute to one of our other leading products.

A few examples of your responsibilities

Build stuff that matters. Take real ownership from idea to production, creating reliable systems and evolving them into products at scale.
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


def test_candidate_requirement_extraction_excludes_company_and_application_text():
    requirements = main.extract_local_job_requirements(GENERIC_SOFTWARE_JD)
    texts = [req["text"] for req in requirements]
    joined = " ".join(texts).lower()

    assert "millions of users rely on" not in joined
    assert "by applying through the exampleco brand" not in joined
    assert "you may work directly on exampleco" not in joined
    assert "all applications go through" not in joined
    assert "benefits" not in joined
    assert any("real ownership from idea to production" in text.lower() for text in texts)
    assert any("ai tools directly into your development workflow" in text.lower() for text in texts)
    assert any("grpc to rest" in text.lower() for text in texts)
    assert any("proficiency in english" in text.lower() for text in texts)


def test_ats_keywords_are_augmented_when_model_under_returns(monkeypatch):
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
        GENERIC_SOFTWARE_JD,
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    hard = {item["skill"]: item for item in result["ats_keywords"]["hard_skills"]}
    assert {"Python", "Docker", "Kubernetes", "Rust", "gRPC", "REST", "microservices"}.issubset(hard)
    assert hard["REST"]["status"] == "present"
    assert hard["Rust"]["status"] == "missing"
    assert hard["gRPC"]["status"] == "missing"


def test_api_backed_requirement_extraction_does_not_fallback_to_local(monkeypatch):
    monkeypatch.setattr(main, "GENAI_CLIENT", None)

    try:
        main.extract_job_responsibilities(GENERIC_SOFTWARE_JD)
    except RuntimeError as exc:
        assert "Gemini API is required" in str(exc)
    else:
        raise AssertionError("expected requirement extraction to require the Gemini API")


def test_api_backed_skills_and_ats_does_not_fallback_on_api_failure(monkeypatch):
    monkeypatch.setattr(main, "GENAI_CLIENT", RaisingClient())
    parsed_resume = {
        "_resume_text": "Skills\nPython, Docker",
        "summary": "",
        "skills": ["Python", "Docker"],
        "tools": ["Docker"],
        "work_experience": [],
        "projects": [],
    }

    try:
        main.gemini_skills_and_ats(GENERIC_SOFTWARE_JD, parsed_resume, parsed_resume["_resume_text"])
    except RuntimeError as exc:
        assert "api unavailable" in str(exc)
    else:
        raise AssertionError("expected skills and ATS analysis to require the Gemini API")


def test_api_backed_responsibility_match_does_not_fallback_on_api_failure(monkeypatch):
    monkeypatch.setattr(main, "GENAI_CLIENT", RaisingClient())
    responsibilities = [
        {
            "text": "Integrate AI tools directly into the development workflow",
            "normalized": "integrate ai tools directly into the development workflow",
            "action_phrases": ["integrate ai tools"],
            "category": "essential",
        }
    ]
    parsed_resume = {
        "_resume_text": "Projects\nBuilt Python services.",
        "summary": "",
        "skills": ["Python"],
        "tools": [],
        "work_experience": [],
        "projects": [],
    }

    try:
        main.gemini_responsibility_match(responsibilities, parsed_resume)
    except RuntimeError as exc:
        assert "api unavailable" in str(exc)
    else:
        raise AssertionError("expected responsibility matching to require the Gemini API")


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
