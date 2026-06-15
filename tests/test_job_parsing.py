import json
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


DATA_LLM_ENGINEER_JD = """
About the job
Key Responsibilities

Design, develop, test, and support robust, production-ready software solutions, adhering to modern engineering best practices.
Build and maintain microservices-based systems, with a strong focus on scalability, resilience, and performance.
Develop and optimise scalable data pipelines, supporting both batch and streaming workloads, using technologies such as Apache Spark.
Work extensively with data technologies, leveraging Python and SQL to deliver high-quality analytical and data-driven solutions.
Lead the design and delivery of data-centric applications, translating complex business and analytical requirements into well-architected technical solutions.
Implement and integrate large language models (LLMs), including:
Utilising both proprietary and open-source models
Fine-tuning models to meet specific business use cases
Delivering solutions via APIs, such as OpenAI APIs
Collaborate closely with product managers, data scientists, and engineering peers to shape technical designs and delivery approaches.
Apply strong problem-solving and analytical skills to diagnose issues, optimise performance, and improve overall system reliability.
Contribute to architectural decision-making, participate in code reviews, and support the continuous improvement of engineering standards and practices.

Required Skills & Experience

Demonstrable hands-on experience developing production-grade backend systems.
Proven experience designing and implementing microservices architectures, ideally within cloud environments.
Strong background in data engineering, including building and maintaining large-scale data pipelines.
Advanced proficiency in Python and SQL.
Practical experience working with large language models, including model fine-tuning and API-based integrations (e.g. OpenAI).
Experience in solution and system design, particularly for data-driven and analytical platforms.
Solid understanding of core software engineering principles, including version control, automated testing, and deployment pipelines.
Excellent analytical thinking and problem-solving skills, with a pragmatic and delivery-focused mindset.

Desirable Skills

Experience working with major cloud platforms such as AWS, Azure, or GCP.
Familiarity with containerisation and orchestration technologies (e.g. Docker, Kubernetes).
Exposure to MLOps practices or deploying AI/ML models into production environments.
Experience working in agile or fast-paced delivery teams.
"""


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


def test_preflight_merge_rejects_requirement_headings():
    merged = main.merge_job_requirements(
        [
            {"text": "Necessary education and experience", "category": "essential"},
            {"text": "Desirable experience", "category": "nice_to_have"},
            {"text": "Strong programming skills in Python", "category": "essential"},
        ],
        [],
    )

    assert [item["text"] for item in merged] == ["Strong programming skills in Python"]


def test_model_skill_cleanup_rejects_sentence_fragments():
    jd_blob = main._candidate_requirement_text_blob(
        "What We're Looking For\n"
        "Expert in Kafka, Flink and Spark. Experience in Databricks is a plus.\n"
        "You will help build and maintain complex data products."
    )

    assert main.clean_model_skill_name("Spark etc. Experience in Databricks is a plus") == "Spark"
    assert main.is_valid_model_skill("Spark", jd_blob)
    assert not main.is_valid_model_skill("You will help build", jd_blob)


def test_local_skill_candidates_exclude_responsibility_clause_fragments():
    jd = """
Key Responsibilities
- Analyzing requirements and implementing new features.
- Debugging complicated engineering and operational problems.
- Working closely with product managers and developers in multiple countries.
- Collaborating closely with other developers in Europe and the United States.
- Participate in various R&D projects.
- Support incident management response processes and follow-up actions.
- Build features for our exchanges.

Required Skills
- Strong Python and SQL knowledge.
- Basic Linux knowledge.
- Experience with Kafka and FastAPI.
- Preferred: Familiarity with Java.
"""

    normalized = {
        main.normalize_phrase(item["skill"])
        for item in main._local_requirement_skill_candidates(jd)
    }

    assert {"python", "sql", "basic linux knowledge", "kafka"}.issubset(normalized)
    assert not {
        "analyzing requirements",
        "implementing new features",
        "debugging complicated engineering",
        "operational problems",
        "working closely with product managers",
        "collaborating closely with other developers in europe",
        "the united states",
    }.intersection(normalized)


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


def test_data_llm_jd_local_extraction_keeps_all_responsibilities_and_drops_headings():
    requirements = main.extract_local_job_requirements(DATA_LLM_ENGINEER_JD, limit=80)
    texts = [item["text"] for item in requirements]
    normalized = [main.normalize_phrase(text) for text in texts]

    assert "required skills experience" not in normalized
    assert "desirable skills" not in normalized
    assert any("architectural decision making" in text and "code reviews" in text for text in normalized)
    assert sum("microservices" in text for text in normalized) == 2
    assert sum("data pipelines" in text for text in normalized) == 2


def test_local_extraction_keeps_exposure_and_qualification_requirements():
    jd = """
    Required Skills & Experience
    ACA or ACCA qualification.

    Nice to Have
    Exposure to MLOps practices.
    """

    requirements = main.extract_local_job_requirements(jd)
    texts = {main.normalize_phrase(item["text"]): item["category"] for item in requirements}

    assert texts["aca or acca qualification"] == "essential"
    assert texts["exposure to mlops practices"] == "nice_to_have"


def test_live_job_patterns_keep_background_preferred_items_and_operational_actions():
    jd = """
    What you'll do
    Define and track success through business metrics and feedback loops.
    Resolve integration and data issues with external vendors.
    Complete all required reconciliations for each reporting period.

    Preferred requirements
    Background in consulting or customer-facing technical roles.
    Experience with low-code or no-code orchestration tools.
    Experience mentoring junior engineers.

    About the team
    We work remotely and value collaboration.
    """

    requirements = main.extract_local_job_requirements(jd, limit=80)
    texts = {main.normalize_phrase(item["text"]): item["category"] for item in requirements}

    assert texts["define and track success through business metrics and feedback loops"] == "essential"
    assert texts["resolve integration and data issues with external vendors"] == "essential"
    assert texts["complete all required reconciliations for each reporting period"] == "essential"
    assert texts["background in consulting or customer facing technical roles"] == "nice_to_have"
    assert texts["experience with low code or no code orchestration tools"] == "nice_to_have"
    assert texts["experience mentoring junior engineers"] == "nice_to_have"
    assert not any("work remotely" in text for text in texts)


def test_candidate_sections_keep_unfamiliar_cross_domain_requirements_without_keyword_rules():
    jd = """
    Company
    ExampleCo provides services worldwide and offers flexible working.

    Key Duties
    Calibrate optical sensors before each production run.
    Sterilise surgical instruments according to infection-control protocols.
    Catalogue archival manuscripts using the institution's metadata standard.
    Reconcile controlled-substance inventory at the end of each shift.

    Person Specification
    Registered practitioner status with the relevant professional body.
    Fluency in British Sign Language.

    Perks
    Private healthcare and generous annual leave.
    """

    requirements = main.extract_local_job_requirements(jd, limit=80)
    texts = {main.normalize_phrase(item["text"]) for item in requirements}

    assert "calibrate optical sensors before each production run" in texts
    assert "sterilise surgical instruments according to infection control protocols" in texts
    assert "catalogue archival manuscripts using the institution s metadata standard" in texts
    assert "reconcile controlled substance inventory at the end of each shift" in texts
    assert "registered practitioner status with the relevant professional body" in texts
    assert "fluency in british sign language" in texts
    assert not any("private healthcare" in text for text in texts)


def test_unrecognized_heading_still_keeps_structured_requirement_bullets():
    jd = """
    Your Mission
    - Tune concert pianos before performances.
    2. Preserve archaeological samples under controlled humidity.
    3) Facilitate restorative conversations between participants.
    """

    requirements = main.extract_local_job_requirements(jd, limit=80)
    texts = {main.normalize_phrase(item["text"]) for item in requirements}

    assert "tune concert pianos before performances" in texts
    assert "preserve archaeological samples under controlled humidity" in texts
    assert "facilitate restorative conversations between participants" in texts


def test_responsible_for_heading_and_generic_posting_noise_are_classified():
    jd = """
    In this role you'll be responsible for:
    Working closely with colleagues to deliver new services.
    Debugging complicated operational problems.

    Benefits And Perks
    Private GP access and complimentary lunch.

    More About ExampleCo
    We work with purpose and celebrate our communities.

    Equal Employment Opportunity
    We are proud to be an equal opportunity employer.
    """

    requirements = main.extract_local_job_requirements(jd, limit=80)
    texts = {main.normalize_phrase(item["text"]) for item in requirements}

    assert "working closely with colleagues to deliver new services" in texts
    assert "debugging complicated operational problems" in texts
    assert not any("private gp" in text for text in texts)
    assert not any("complimentary lunch" in text for text in texts)
    assert not any("celebrate our communities" in text for text in texts)
    assert not any("equal opportunity employer" in text for text in texts)


def test_global_style_job_noise_is_excluded_from_requirements():
    jd = """
    AI Engineer - Python

    Reporting of the Role
    This role reports to the Lead AI Engineer.

    Overview of job
    Global:IQ brings together a suite of 1st party and partner data, tools and capabilities.

    Measures of success
    Gained a comprehensive understanding of the Global:IQ architecture and data flows.
    Delivered first outcomes at pace into production with an eye to modern AI-enabled development methodology.
    Contributed considerably in bringing our new Global:IQ product offering to production.
    Established yourself as a technical leader within the team.

    Key Responsibilities
    Backend Engineering (50%): Design, build, and maintain robust APIs using Python and FastAPI.

    What you will need
    3+ years of experience in backend engineering.

    Preferred / Bonus
    Experience with end-to-end development of AI features.
    """

    requirements = main.extract_local_job_requirements(jd, limit=80)
    texts = {main.normalize_phrase(item["text"]): item["category"] for item in requirements}
    joined = " ".join(texts)

    assert "reports to the lead ai engineer" not in joined
    assert "brings together a suite" not in joined
    assert "gained a comprehensive understanding" not in joined
    assert "delivered first outcomes" not in joined
    assert "contributed considerably" not in joined
    assert "established yourself" not in joined
    assert any("design build and maintain robust apis" in text for text in texts)
    assert texts["3+ years of experience in backend engineering"] == "essential"
    assert texts["experience with end to end development of ai features"] == "nice_to_have"


def test_merge_rejects_ai_generated_reporting_and_future_success_noise():
    generated = [
        {"text": "This role reports to the Lead AI Engineer.", "category": "essential"},
        {"text": "Gained a comprehensive understanding of the Global:IQ architecture and data flows.", "category": "essential"},
        {"text": "Delivered first outcomes at pace into production with an eye to modern AI-enabled development methodology.", "category": "essential"},
        {"text": "Contributed considerably in bringing our new Global:IQ product offering to production.", "category": "essential"},
        {"text": "Established yourself as a technical leader within the team.", "category": "essential"},
        {"text": "Strong proficiency in Python and FastAPI.", "category": "essential"},
    ]

    merged = main.merge_job_requirements(generated, [])
    texts = {main.normalize_phrase(item["text"]) for item in merged}

    assert texts == {"strong proficiency in python and fastapi"}


def test_healthcare_role_keeps_candidate_requirements_and_drops_company_noise():
    jd = """
    Healthcare Data Platform Engineer

    About MedSignal
    MedSignal builds secure analytics platforms that help hospitals understand patient pathways.

    What you will do
    - Implement healthcare data privacy controls and support GDPR-compliant processing.

    What you will bring
    - Knowledge of healthcare or clinical data.
    - Experience using dbt and BigQuery.
    """

    requirements = main.extract_local_job_requirements(jd, limit=80)
    texts = {main.normalize_phrase(item["text"]) for item in requirements}

    assert not any("medsignal builds" in text for text in texts)
    assert "what you will bring" not in texts
    assert any("healthcare data privacy controls" in text for text in texts)
    assert "knowledge of healthcare or clinical data" in texts
    assert "experience using dbt and bigquery" in texts


def test_jd_source_sentences_classify_role_overview_and_candidate_sections():
    jd = """
    About Canvas Reply
    Canvas Reply provides end-to-end product design and development services.

    Role Overview
    This role is ideal for graduates seeking to build a broad technical foundation.

    Responsibilities
    Support and develop solutions using JavaScript.

    About the Candidate
    Some exposure to coding or scripting through university or personal projects.
    """

    sources = main.build_jd_source_sentences(jd)
    by_text = {main.normalize_phrase(item["text"]): item for item in sources}

    assert by_text["canvas reply provides end to end product design and development services"]["section_type"] == "excluded"
    assert by_text["this role is ideal for graduates seeking to build a broad technical foundation"]["section_type"] == "excluded"
    assert by_text["support and develop solutions using javascript"]["section_type"] == "candidate"
    assert by_text["some exposure to coding or scripting through university or personal projects"]["section_type"] == "candidate"


def test_local_job_requirements_reject_generic_job_title_preamble():
    requirements = main.extract_local_job_requirements(
        "Graduate Software Developer\n\n"
        "Responsibilities\n"
        "Support and develop solutions using JavaScript."
    )

    assert [item["text"] for item in requirements] == [
        "Support and develop solutions using JavaScript."
    ]


def test_grounded_requirement_verifier_rejects_wrong_owner_unknown_ids_and_paraphrases():
    sources = main.build_jd_source_sentences(
        "Responsibilities\n"
        "Support and develop solutions using JavaScript.\n"
        "About the Candidate\n"
        "Some exposure to coding or scripting through university or personal projects."
    )
    items = [
        {
            "requirement": "Expert JavaScript engineer",
            "source_sentence_id": "s1",
            "owner": "candidate",
            "type": "candidate_responsibility",
            "scoreable_against_cv": True,
            "category": "essential",
        },
        {
            "source_sentence_id": "s2",
            "owner": "company",
            "type": "candidate_experience",
            "scoreable_against_cv": True,
            "category": "essential",
        },
        {
            "source_sentence_id": "s99",
            "owner": "candidate",
            "type": "candidate_skill",
            "scoreable_against_cv": True,
            "category": "essential",
        },
    ]

    verified, rejected = main.verify_grounded_job_requirements(items, sources)

    assert [item["text"] for item in verified] == [
        "Support and develop solutions using JavaScript."
    ]
    assert any("non-candidate owner" in reason for reason in rejected)
    assert any("unknown source sentence ID" in reason for reason in rejected)


def test_preflight_only_scores_verified_source_sentences(monkeypatch):
    jd = """
    About Canvas Reply
    Canvas Reply provides end-to-end product design and development services.

    Role Overview
    This role is ideal for graduates seeking to build a broad technical foundation.

    Responsibilities
    Support and develop solutions using JavaScript across front-end and back-end components.

    About the Candidate
    Some exposure to coding or scripting through university, personal projects, or placements.
    """
    generated = {
        "items": [
            {
                "requirement": "Work for a leading digital consultancy",
                "source_sentence_id": "s1",
                "owner": "company",
                "type": "non_scoreable",
                "scoreable_against_cv": False,
                "category": "essential",
                "confidence": 0.99,
            },
            {
                "requirement": "Build a broad technical foundation",
                "source_sentence_id": "s2",
                "owner": "role",
                "type": "non_scoreable",
                "scoreable_against_cv": False,
                "category": "essential",
                "confidence": 0.99,
            },
            {
                "requirement": "Expert full-stack JavaScript development",
                "source_sentence_id": "s3",
                "owner": "candidate",
                "type": "candidate_responsibility",
                "scoreable_against_cv": True,
                "category": "essential",
                "confidence": 0.95,
            },
            {
                "requirement": "Coding exposure",
                "source_sentence_id": "s4",
                "owner": "candidate",
                "type": "candidate_experience",
                "scoreable_against_cv": True,
                "category": "essential",
                "confidence": 0.95,
            },
            {
                "requirement": "Invented requirement",
                "source_sentence_id": "s99",
                "owner": "candidate",
                "type": "candidate_skill",
                "scoreable_against_cv": True,
                "category": "essential",
                "confidence": 0.95,
            },
        ],
        "ats_keywords": {"hard_skills": ["JavaScript"], "soft_skills": []},
        "quality": {"makes_sense": True, "confidence": "high", "issues": [], "excluded_noise": []},
    }
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(json.dumps(generated)))

    result = main.preflight_job_requirements(jd)
    cleaned = result["cleaned_job_description"]

    assert "Canvas Reply provides" not in cleaned
    assert "This role is ideal" not in cleaned
    assert "Expert full-stack JavaScript development" not in cleaned
    assert "Support and develop solutions using JavaScript" in cleaned
    assert "Some exposure to coding or scripting" in cleaned
    assert any("unknown source sentence ID" in reason for reason in result["quality"]["excluded_noise"])


def test_merge_rejects_generated_company_description_and_candidate_heading():
    merged = main.merge_job_requirements(
        [
            {
                "text": "MedSignal builds secure analytics platforms that help hospitals understand patient pathways.",
                "category": "essential",
            },
            {"text": "What you will bring", "category": "essential"},
            {"text": "Knowledge of healthcare or clinical data.", "category": "essential"},
        ],
        [],
    )

    assert [main.normalize_phrase(item["text"]) for item in merged] == [
        "knowledge of healthcare or clinical data"
    ]


def test_merge_keeps_distinct_tool_familiarity_alongside_action_evidence():
    requirements = [
        {"text": "Maintain accurate equity records in Equity Edge Online.", "category": "essential"},
        {"text": "Familiarity with E*TRADE and Equity Edge Online.", "category": "essential"},
    ]

    merged = main.merge_job_requirements(requirements, [])
    texts = [main.normalize_phrase(item["text"]) for item in merged]

    assert len(texts) == 2
    assert any("e trade" in text for text in texts)


def test_data_llm_jd_preflight_removes_noise_without_domain_specific_deduplication(monkeypatch):
    generated = {
        "requirements": [
            {
                "text": "Build and maintain microservices-based systems, with a strong focus on scalability, resilience, and performance.",
                "category": "essential",
            },
            {
                "text": "Develop and optimise scalable data pipelines, supporting both batch and streaming workloads, using technologies such as Apache Spark.",
                "category": "essential",
            },
            {
                "text": "Work extensively with data technologies, leveraging Python and SQL to deliver high-quality analytical and data-driven solutions.",
                "category": "essential",
            },
            {
                "text": "Lead the design and delivery of data-centric applications, translating complex business and analytical requirements into well-architected technical solutions.",
                "category": "essential",
            },
            {
                "text": "Implement and integrate large language models (LLMs), including utilising both proprietary and open-source models, fine-tuning models to meet specific business use cases, and delivering solutions via APIs, such as OpenAI APIs.",
                "category": "essential",
            },
            {
                "text": "Required Skills & Experience",
                "category": "essential",
            },
            {
                "text": "Proven experience designing and implementing microservices architectures, ideally within cloud environments.",
                "category": "essential",
            },
            {
                "text": "Strong background in data engineering, including building and maintaining large-scale data pipelines.",
                "category": "essential",
            },
            {
                "text": "Advanced proficiency in Python and SQL.",
                "category": "essential",
            },
            {
                "text": "Practical experience working with large language models, including model fine-tuning and API-based integrations (e.g. OpenAI).",
                "category": "essential",
            },
            {
                "text": "Experience in solution and system design, particularly for data-driven and analytical platforms.",
                "category": "essential",
            },
        ],
        "ats_keywords": {
            "hard_skills": ["Python", "SQL", "microservices", "data pipelines"],
            "soft_skills": [
                "problem-solving",
                "problem-solving skills",
                "Apply strong problem-solving",
                "analytical skills",
            ],
        },
        "quality": {
            "makes_sense": True,
            "confidence": "high",
            "issues": [],
            "excluded_noise": ["Required Skills & Experience"],
        },
    }
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(json.dumps(generated)))

    result = main.preflight_job_requirements(DATA_LLM_ENGINEER_JD)
    texts = [item["text"] for item in result["requirements"]]
    normalized = [main.normalize_phrase(text) for text in texts]
    soft = result["ats_keywords"]["soft_skills"]

    assert "required skills experience" not in normalized
    assert any("architectural decision making" in text and "code reviews" in text for text in normalized)
    assert sum("microservices" in text for text in normalized) == 2
    assert sum("data pipelines" in text or "data engineering" in text for text in normalized) == 2
    assert sum("python and sql" in text for text in normalized) == 2
    assert sum("large language models" in text for text in normalized) == 2
    assert sum("data centric applications" in text or "solution and system design" in text for text in normalized) == 2
    assert len(result["requirements_by_category"]["nice_to_have"]) == 4
    assert main._soft_ats_keyword_key("Apply strong problem-solving") == "problem solving"
    assert sum(main._soft_ats_keyword_key(item["skill"]) == "problem solving" for item in soft) == 1


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
