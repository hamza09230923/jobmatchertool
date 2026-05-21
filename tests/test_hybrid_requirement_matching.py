from types import SimpleNamespace

import pytest

import main


class FakeModels:
    def __init__(self, text: str):
        self._text = text

    def generate_content(self, **kwargs):
        return SimpleNamespace(text=self._text)


class FakeClient:
    def __init__(self, text: str):
        self.models = FakeModels(text)


def sample_resume():
    resume_text = """
    Projects
    AI-Powered Trading Strategy Backtesting Platform
    Built a React dashboard for a trading backtesting platform using Python and SQL.
    Implemented RSI and MACD strategy backtesting with Kelly sizing and market data analysis.
    """
    return {
        "_resume_text": resume_text,
        "summary": "Python developer with data and analytics experience.",
        "skills": ["Python", "SQL"],
        "tools": [],
        "projects": [
            {
                "name": "AI-Powered Trading Strategy Backtesting Platform",
                "tech_stack": ["React", "Python", "SQL"],
                "bullets": [
                    "Built a React dashboard for a trading backtesting platform.",
                    "Implemented RSI and MACD strategy backtesting with Kelly sizing and market data analysis.",
                ],
            }
        ],
        "work_experience": [],
    }


def degree_resume():
    resume_text = """
    Education
    BA English Literature, University of Nottingham, 2024

    Projects
    Built a legal case summarisation tool using Python and embeddings.
    Built a financial prediction dashboard using market data and sentiment signals.
    """
    return {
        "_resume_text": resume_text,
        "summary": "Graduate with Python project experience in legal and finance-adjacent tools.",
        "skills": ["Python", "communication"],
        "tools": [],
        "education": [
            {
                "degree": "BA English Literature",
                "institution": "University of Nottingham",
                "graduation_year": "2024",
            }
        ],
        "projects": [
            {
                "name": "Legal Case Summarisation Tool",
                "tech_stack": ["Python"],
                "bullets": ["Built a legal case summarisation tool using Python and embeddings."],
            },
            {
                "name": "Financial Prediction Dashboard",
                "tech_stack": ["Python"],
                "bullets": ["Built a financial prediction dashboard using market data and sentiment signals."],
            },
        ],
        "work_experience": [],
    }


def test_aggregate_requirement_marks_react_typescript_as_partial():
    parsed_resume = sample_resume()
    result = main.aggregate_requirement_evidence(
        "Front end development with React and Typescript",
        parsed_resume,
        parsed_resume["_resume_text"],
    )
    assert result["status"] == "partial"
    assert result["matched_count"] == 1
    assert result["total_count"] == 3
    assert any(
        atom["requirement"].lower() == "react" and atom["status"] == "present"
        for atom in result["atomic_breakdown"]
    )
    assert any(
        atom["requirement"].lower() == "typescript" and atom["status"] == "missing"
        for atom in result["atomic_breakdown"]
    )


def test_gemini_responsibility_match_downgrades_trading_project_to_partial(monkeypatch):
    parsed_resume = sample_resume()
    fake_json = """
    {
      "matches": [
        {
          "index": 1,
          "responsibility": "Knowledge of financial markets & securities, especially equities and options",
          "evidence": "[Project: AI-Powered Trading Strategy Backtesting Platform] Implemented RSI and MACD strategy backtesting with Kelly sizing and market data analysis.",
          "confidence": "strong"
        }
      ],
      "missing": []
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    responsibilities = [
        {
            "text": "Knowledge of financial markets & securities, especially equities and options",
            "normalized": main.normalize_phrase("Knowledge of financial markets & securities, especially equities and options"),
            "action_phrases": [],
            "category": "nice_to_have",
        }
    ]

    result = main.gemini_responsibility_match(responsibilities, parsed_resume)

    assert result["missing_responsibilities"] == []
    assert len(result["matched_responsibilities"]) == 1
    match = result["matched_responsibilities"][0]
    assert match["confidence"] == "partial"
    assert match["matched_count"] < match["total_count"]
    assert any(
        atom["requirement"].lower() in {"equities", "options"} and atom["status"] == "missing"
        for atom in match["atomic_breakdown"]
    )


def test_gemini_skills_and_ats_preserves_partial_status(monkeypatch):
    parsed_resume = sample_resume()
    fake_json = """
    {
      "skills": {
        "must_have": [
          {
            "skill": "Front end development with React and Typescript",
            "present": true,
            "cv_where": "[Project: AI-Powered Trading Strategy Backtesting Platform] Built a React dashboard for a trading backtesting platform."
          }
        ],
        "nice_to_have": []
      },
      "ats_keywords": {
        "hard_skills": [
          {"skill": "React", "jd_count": 1, "cv_count": 1}
        ],
        "soft_skills": []
      }
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))

    result = main.gemini_skills_and_ats(
        "Front end development with React and Typescript",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    skill = result["skills"]["must_have"][0]
    assert skill["status"] == "partial"
    assert skill["present"] is True
    assert skill["matched_count"] < skill["total_count"]
    assert skill["total_count"] == 3
    assert any(
        atom["requirement"].lower() == "typescript" and atom["status"] == "missing"
        for atom in skill["atomic_breakdown"]
    )


def test_degree_requirements_only_match_education_subjects():
    parsed_resume = degree_resume()

    english = main.aggregate_requirement_evidence(
        "Bachelor's degree in English",
        parsed_resume,
        parsed_resume["_resume_text"],
    )
    law = main.aggregate_requirement_evidence(
        "Law degree",
        parsed_resume,
        parsed_resume["_resume_text"],
    )
    finance = main.aggregate_requirement_evidence(
        "Bachelor's degree in Finance",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert english["status"] == "present"
    assert english["section"] == "education"
    assert law["status"] == "missing"
    assert finance["status"] == "missing"


def test_finance_reporting_terms_do_not_match_financial_market_project():
    parsed_resume = degree_resume()

    statements = main.aggregate_requirement_evidence(
        "financial statements",
        parsed_resume,
        parsed_resume["_resume_text"],
    )
    control = main.aggregate_requirement_evidence(
        "financial control",
        parsed_resume,
        parsed_resume["_resume_text"],
    )
    reporting = main.aggregate_requirement_evidence(
        "regulatory reporting",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert statements["status"] == "missing"
    assert control["status"] == "missing"
    assert reporting["status"] == "missing"


def test_project_management_can_be_partial_from_project_delivery_evidence():
    parsed_resume = {
        "_resume_text": "",
        "summary": "",
        "skills": [],
        "tools": [],
        "education": [],
        "projects": [
            {
                "name": "Data Quality Migration",
                "tech_stack": ["SQL"],
                "bullets": [
                    "Coordinated stakeholders, tracked deliverables, and delivered process improvements for a data quality migration."
                ],
            }
        ],
        "work_experience": [],
    }

    result = main.aggregate_requirement_evidence(
        "Project management experience supporting process improvements and data-quality initiatives",
        parsed_resume,
        "",
    )

    assert result["status"] == "partial"
    assert result["section"] == "projects"


def test_rewrite_skill_validator_removes_unevidenced_jd_skills():
    parsed_resume = degree_resume()
    rewrite = {
        "skills_section": [
            {
                "category": "Skills",
                "items": [
                    "Python",
                    "English",
                    "Law",
                    "Accounting",
                    "Regulatory Reporting",
                ],
            }
        ],
        "additional_keywords_to_include": [],
        "missing_information": [],
    }

    result = main.validate_rewrite_skills(rewrite, parsed_resume["_resume_text"])
    skills = result["skills_section"][0]["items"]

    assert "Python" in skills
    assert "English" in skills
    assert "Law" not in skills
    assert "Accounting" not in skills
    assert "Regulatory Reporting" not in skills
    assert any("Law" in item and "add only if accurate" in item for item in result["additional_keywords_to_include"])
    assert any("Regulatory Reporting" in item and "add only if accurate" in item for item in result["additional_keywords_to_include"])


ROLE_FIXTURES = [
    pytest.param(
        "law_degree_present",
        {
            "_resume_text": "Education\nLLB Law, University of Leeds, 2024",
            "summary": "",
            "skills": [],
            "tools": [],
            "education": [{"degree": "LLB Law", "institution": "University of Leeds", "graduation_year": "2024"}],
            "projects": [{"name": "Legal NLP", "tech_stack": ["Python"], "bullets": ["Built a legal case summarisation tool."]}],
            "work_experience": [],
        },
        "Law degree",
        "present",
        "education",
        id="law degree only from education",
    ),
    pytest.param(
        "law_degree_missing_from_project",
        {
            "_resume_text": "Projects\nBuilt a legal case summarisation tool using Python.",
            "summary": "Python developer with legal-tech project exposure.",
            "skills": ["Python"],
            "tools": [],
            "education": [{"degree": "BA English Literature", "institution": "University of Nottingham"}],
            "projects": [{"name": "Legal NLP", "tech_stack": ["Python"], "bullets": ["Built a legal case summarisation tool."]}],
            "work_experience": [],
        },
        "Law degree",
        "missing",
        None,
        id="law project does not prove law degree",
    ),
    pytest.param(
        "english_degree_present",
        {
            "_resume_text": "Education\nBA English Literature, University of Nottingham, 2024",
            "summary": "",
            "skills": ["copywriting"],
            "tools": [],
            "education": [{"degree": "BA English Literature", "institution": "University of Nottingham"}],
            "projects": [],
            "work_experience": [],
        },
        "Bachelor's degree in English",
        "present",
        "education",
        id="english degree present",
    ),
    pytest.param(
        "qts_cert_missing_from_project",
        {
            "_resume_text": "Projects\nBuilt a QTS exam revision app.",
            "summary": "",
            "skills": ["lesson planning"],
            "tools": [],
            "education": [{"degree": "BA English Literature", "institution": "University of Nottingham"}],
            "certifications": [],
            "projects": [{"name": "QTS Revision App", "tech_stack": ["React"], "bullets": ["Built a QTS exam revision app."]}],
            "work_experience": [],
        },
        "QTS certification",
        "missing",
        None,
        id="teaching certification requires certification or education evidence",
    ),
    pytest.param(
        "qts_cert_present",
        {
            "_resume_text": "Certifications\nQualified Teacher Status (QTS)",
            "summary": "",
            "skills": [],
            "tools": [],
            "education": [],
            "certifications": ["Qualified Teacher Status (QTS)"],
            "projects": [],
            "work_experience": [],
        },
        "QTS certification",
        "present",
        "certifications",
        id="teaching certification present",
    ),
    pytest.param(
        "healthcare_domain_not_hipaa",
        {
            "_resume_text": "Projects\nBuilt a healthcare appointment scheduling app.",
            "summary": "Built healthcare scheduling tools.",
            "skills": ["Python"],
            "tools": [],
            "education": [],
            "projects": [{"name": "Healthcare Scheduler", "tech_stack": ["Python"], "bullets": ["Built a healthcare appointment scheduling app."]}],
            "work_experience": [],
        },
        "HIPAA compliance",
        "missing",
        None,
        id="healthcare project does not prove HIPAA compliance",
    ),
    pytest.param(
        "healthcare_compliance_present",
        {
            "_resume_text": "Experience\nImplemented HIPAA compliance checks for patient-data export workflows.",
            "summary": "",
            "skills": ["HIPAA compliance"],
            "tools": [],
            "education": [],
            "projects": [],
            "work_experience": [
                {
                    "title": "Healthcare Data Analyst",
                    "company": "ClinicCo",
                    "bullets": ["Implemented HIPAA compliance checks for patient-data export workflows."],
                }
            ],
        },
        "HIPAA compliance",
        "present",
        "skills",
        id="healthcare compliance explicit",
    ),
    pytest.param(
        "docker_not_kubernetes",
        {
            "_resume_text": "Skills\nDocker, Python, AWS\nProjects\nContainerised an API with Docker.",
            "summary": "",
            "skills": ["Docker", "Python", "AWS"],
            "tools": [],
            "education": [],
            "projects": [{"name": "API", "tech_stack": ["Docker"], "bullets": ["Containerised an API with Docker."]}],
            "work_experience": [],
        },
        "Kubernetes",
        "missing",
        None,
        id="docker does not prove kubernetes",
    ),
    pytest.param(
        "kubernetes_present",
        {
            "_resume_text": "Skills\nKubernetes, Docker, Python",
            "summary": "",
            "skills": ["Kubernetes", "Docker", "Python"],
            "tools": [],
            "education": [],
            "projects": [],
            "work_experience": [],
        },
        "Kubernetes",
        "present",
        "skills",
        id="kubernetes exact tool present",
    ),
    pytest.param(
        "sales_analytics_not_sales_experience",
        {
            "_resume_text": "Projects\nBuilt a B2B SaaS sales analytics dashboard.",
            "summary": "Built analytics dashboards for SaaS metrics.",
            "skills": ["analytics", "dashboards"],
            "tools": [],
            "education": [],
            "projects": [{"name": "Sales Dashboard", "tech_stack": ["React"], "bullets": ["Built a B2B SaaS sales analytics dashboard."]}],
            "work_experience": [],
        },
        "enterprise sales experience",
        "missing",
        None,
        id="sales analytics project does not prove sales experience",
    ),
    pytest.param(
        "stakeholder_communication_present",
        {
            "_resume_text": "Experience\nPresented weekly reports to stakeholders and explained performance trends clearly.",
            "summary": "",
            "skills": ["stakeholder communication"],
            "tools": [],
            "education": [],
            "projects": [],
            "work_experience": [
                {
                    "title": "Operations Analyst",
                    "company": "OpsCo",
                    "bullets": ["Presented weekly reports to stakeholders and explained performance trends clearly."],
                }
            ],
        },
        "Strong written and verbal communication skills",
        "partial",
        "skills",
        id="communication can use skills or work evidence",
    ),
    pytest.param(
        "people_management_missing_from_teamwork",
        {
            "_resume_text": "Experience\nWorked in a seven-person Agile team to deliver a web app.",
            "summary": "",
            "skills": ["teamwork"],
            "tools": [],
            "education": [],
            "projects": [],
            "work_experience": [
                {
                    "title": "Developer",
                    "company": "MHR",
                    "bullets": ["Worked in a seven-person Agile team to deliver a web app."],
                }
            ],
        },
        "people management experience",
        "missing",
        None,
        id="teamwork does not prove people management",
    ),
    pytest.param(
        "people_management_present",
        {
            "_resume_text": "Experience\nManaged a team of 4 analysts and mentored two junior hires.",
            "summary": "",
            "skills": ["people management", "mentoring"],
            "tools": [],
            "education": [],
            "projects": [],
            "work_experience": [
                {
                    "title": "Analytics Lead",
                    "company": "DataCo",
                    "bullets": ["Managed a team of 4 analysts and mentored two junior hires."],
                }
            ],
        },
        "people management experience",
        "present",
        "experience",
        id="people management explicit",
    ),
    pytest.param(
        "product_roadmap_present",
        {
            "_resume_text": "Experience\nOwned the product roadmap, prioritised backlog items, and aligned releases with stakeholders.",
            "summary": "",
            "skills": ["product roadmap", "stakeholder management"],
            "tools": [],
            "education": [],
            "projects": [],
            "work_experience": [
                {
                    "title": "Associate Product Manager",
                    "company": "SaaSCo",
                    "bullets": ["Owned the product roadmap, prioritised backlog items, and aligned releases with stakeholders."],
                }
            ],
        },
        "roadmap ownership",
        "present",
        "experience",
        id="product roadmap ownership explicit",
    ),
    pytest.param(
        "gdpr_missing_from_privacy_project",
        {
            "_resume_text": "Projects\nBuilt a privacy settings page for a consumer app.",
            "summary": "",
            "skills": ["privacy design"],
            "tools": [],
            "education": [],
            "projects": [{"name": "Privacy Settings", "tech_stack": ["React"], "bullets": ["Built a privacy settings page for a consumer app."]}],
            "work_experience": [],
        },
        "GDPR compliance",
        "missing",
        None,
        id="privacy project does not prove GDPR compliance",
    ),
    pytest.param(
        "gdpr_present",
        {
            "_resume_text": "Experience\nDocumented GDPR compliance requirements for data-retention workflows.",
            "summary": "",
            "skills": ["GDPR compliance"],
            "tools": [],
            "education": [],
            "projects": [],
            "work_experience": [
                {
                    "title": "Data Governance Analyst",
                    "company": "DataCo",
                    "bullets": ["Documented GDPR compliance requirements for data-retention workflows."],
                }
            ],
        },
        "GDPR compliance",
        "present",
        "skills",
        id="GDPR compliance explicit",
    ),
    pytest.param(
        "data_quality_present",
        {
            "_resume_text": "Experience\nImplemented validation checks and reconciliation controls to improve data integrity.",
            "summary": "",
            "skills": ["data validation", "data integrity"],
            "tools": [],
            "education": [],
            "projects": [],
            "work_experience": [
                {
                    "title": "Data Analyst",
                    "company": "DataCo",
                    "bullets": ["Implemented validation checks and reconciliation controls to improve data integrity."],
                }
            ],
        },
        "attention to detail and data integrity",
        "partial",
        "skills",
        id="data quality explicit",
    ),
    pytest.param(
        "cissp_cert_missing_from_security_project",
        {
            "_resume_text": "Projects\nBuilt a security dashboard for vulnerability trends.",
            "summary": "",
            "skills": ["security dashboards"],
            "tools": [],
            "education": [],
            "certifications": [],
            "projects": [{"name": "Security Dashboard", "tech_stack": ["Python"], "bullets": ["Built a security dashboard for vulnerability trends."]}],
            "work_experience": [],
        },
        "CISSP certification",
        "missing",
        None,
        id="security project does not prove CISSP certification",
    ),
]


@pytest.mark.parametrize("name,parsed_resume,requirement,expected_status,expected_section", ROLE_FIXTURES)
def test_generic_role_evidence_policy_fixtures(name, parsed_resume, requirement, expected_status, expected_section):
    result = main.aggregate_requirement_evidence(
        requirement,
        parsed_resume,
        parsed_resume.get("_resume_text", ""),
    )

    assert result["status"] == expected_status, name
    if expected_section:
        assert result["section"] == expected_section, name
