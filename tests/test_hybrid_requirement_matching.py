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
