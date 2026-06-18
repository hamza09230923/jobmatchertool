import json
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


class RaisingModels:
    def generate_content(self, **kwargs):
        raise RuntimeError("network unavailable")


class RaisingClient:
    models = RaisingModels()


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


def transferable_action_resume():
    resume_text = """
    Experience
    Worked across product, engineering and stakeholder needs to clarify requirements,
    define practical solutions and deliver features aligned with user outcomes.
    Delivered production features within a seven-person Agile team and participated in code reviews.
    Identified and corrected machine learning pipeline data leakage.
    Evaluated new AI technologies through selected data projects.
    """
    return {
        "_resume_text": resume_text,
        "summary": "",
        "skills": ["Python", "testing"],
        "tools": [],
        "projects": [
            {
                "name": "AI evaluation projects",
                "tech_stack": ["Python"],
                "bullets": ["Evaluated new AI technologies through selected data projects."],
            }
        ],
        "work_experience": [
            {
                "company": "Example",
                "role": "Developer",
                "bullets": [
                    "Worked across product, engineering and stakeholder needs to clarify requirements, define practical solutions and deliver features aligned with user outcomes.",
                    "Delivered production features within a seven-person Agile team and participated in code reviews.",
                    "Identified and corrected machine learning pipeline data leakage.",
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "requirement",
    [
        "Analyzing requirements and implementing new features",
        "Collaborating closely with other developers",
        "Debugging complicated engineering and operational problems",
    ],
)
def test_general_responsibilities_match_transferable_action_evidence(requirement):
    parsed_resume = transferable_action_resume()

    result = main.aggregate_requirement_evidence(requirement, parsed_resume, parsed_resume["_resume_text"])

    assert result["status"] in {"present", "partial"}
    assert result["matched_count"] >= 1


def test_transferable_action_matching_does_not_satisfy_explicit_tools():
    parsed_resume = transferable_action_resume()

    linux = main.aggregate_requirement_evidence("Basic Linux knowledge", parsed_resume, parsed_resume["_resume_text"])
    kafka = main.aggregate_requirement_evidence("Experience with Kafka", parsed_resume, parsed_resume["_resume_text"])

    assert linux["status"] == "missing"
    assert kafka["status"] == "missing"


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


def test_requirement_decomposition_removes_labels_and_duplicate_concepts():
    atoms = main.decompose_requirement_text(
        "Software Engineering Fundamentals: A demonstrable understanding of version control "
        "and repository tools (Git, CI/CD) and a commitment to writing clean, well-documented code."
    )
    concepts = [main._requirement_concept_key(atom["text"]) for atom in atoms]
    normalized = [atom["normalized"] for atom in atoms]

    assert "software engineering fundamentals" not in normalized
    assert concepts.count("version_control") == 1
    assert concepts.count("ci_cd") == 1
    assert concepts.count("clean_documented_code") == 1


def test_find_cv_evidence_prefers_concrete_project_over_skills_list():
    parsed = parsed_resume(
        resume_text="""
        Skills
        Python, SQL
        Projects
        Built a Python service that queried MySQL relational tables and generated operational reports.
        """,
        skills=["Python", "SQL"],
        projects=[
            {
                "name": "Operational Reporting",
                "tech_stack": ["Python", "MySQL"],
                "bullets": ["Built a Python service that queried MySQL relational tables and generated operational reports."],
            }
        ],
    )

    result = main.find_cv_evidence_for_requirement(
        "Experience with SQL for querying relational databases",
        parsed,
        parsed["_resume_text"],
    )

    assert result is not None
    assert result["section"] == "projects"
    assert "queried MySQL relational tables" in result["evidence"]


def test_skill_items_dedupe_equivalent_requirement_wording():
    items = [
        {"skill": "Git", "status": "present", "present": True},
        {"skill": "version control", "status": "missing", "present": False},
        {"skill": "repository tools", "status": "missing", "present": False},
        {"skill": "SQL", "status": "present", "present": True},
        {"skill": "SQL for querying relational databases", "status": "missing", "present": False},
    ]

    result = main._dedupe_skill_items(items)

    assert [item["skill"] for item in result] == ["Git", "SQL"]


def test_gemini_responsibility_score_uses_atomic_coverage_and_downweights_desirables(monkeypatch):
    parsed = parsed_resume(
        resume_text="Skills\nPython\nEducation\nBSc Computer Science",
        skills=["Python"],
        education=[{"degree": "BSc Computer Science", "institution": "Nottingham"}],
    )
    fake_json = """
    {
      "matches": [
        {"index": 1, "responsibility": "Strong Python programming and data structures and algorithms", "evidence": "SKILLS: Python", "confidence": "partial"}
      ],
      "missing": [
        {"index": 2, "responsibility": "Master's degree or PhD", "gap": "No postgraduate degree"}
      ]
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    responsibilities = [
        {
            "text": "Strong Python programming and data structures and algorithms",
            "normalized": main.normalize_phrase("Strong Python programming and data structures and algorithms"),
            "action_phrases": [],
            "category": "essential",
        },
        {
            "text": "Master's degree or PhD",
            "normalized": main.normalize_phrase("Master's degree or PhD"),
            "action_phrases": [],
            "category": "nice_to_have",
        },
    ]

    result = main.gemini_responsibility_match(responsibilities, parsed)

    assert 25 < result["score"] < 50
    assert result["matched_responsibilities"][0]["similarity"] < 1


def test_responsibility_candidate_retrieval_ranks_direct_evidence_above_unrelated_lines():
    parsed = parsed_resume(
        resume_text="""
        Experience
        Analysed marketing funnel performance and prepared campaign reports.
        Built Python APIs backed by MySQL and wrote SQL queries for operational reporting.
        """,
        work_experience=[
            {
                "title": "Engineer",
                "company": "Example",
                "bullets": [
                    "Analysed marketing funnel performance and prepared campaign reports.",
                    "Built Python APIs backed by MySQL and wrote SQL queries for operational reporting.",
                ],
            }
        ],
    )
    responsibility = {
        "text": "Experience with SQL for querying relational databases",
        "normalized": main.normalize_phrase("Experience with SQL for querying relational databases"),
        "action_phrases": [],
        "category": "essential",
    }

    candidates = main.retrieve_responsibility_evidence_candidates([responsibility], parsed)

    assert "wrote SQL queries" in candidates[responsibility["normalized"]][0]["text"]


def test_atom_candidate_retrieval_uses_stable_ids_and_cv_lines_only():
    parsed = parsed_resume(
        resume_text="Projects\nBuilt a Python API.\nExperience\nPrepared monthly reports.",
        projects=[{"name": "API", "bullets": ["Built a Python API."]}],
        work_experience=[{"title": "Analyst", "company": "Example", "bullets": ["Prepared monthly reports."]}],
    )
    responsibilities = [{
        "text": "Build APIs and communicate progress",
        "normalized": main.normalize_phrase("Build APIs and communicate progress"),
        "action_phrases": [],
        "category": "essential",
    }]

    packets, candidates = main.retrieve_atom_evidence_candidates(responsibilities, parsed)

    assert {packet["atom_id"] for packet in packets} == {"r1a1", "r1a2"}
    assert all(item["evidence_id"].startswith(atom_id) for atom_id, items in candidates.items() for item in items)
    assert all(
        item["text"] in {unit["text"] for unit in main.evidence_units_from_parsed(parsed)}
        for items in candidates.values()
        for item in items
    )


def test_gemini_atom_match_rejects_evidence_id_outside_atom_packet(monkeypatch):
    parsed = parsed_resume(
        resume_text="Experience\nPrepared monthly reports.",
        work_experience=[{"title": "Analyst", "company": "Example", "bullets": ["Prepared monthly reports."]}],
    )
    fake_json = json.dumps({
        "atom_matches": [
            {"atom_id": "r1a1", "evidence_id": "r99a99e1", "confidence": "strong"}
        ],
        "atom_missing": [],
    })
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    responsibilities = [{
        "text": "Design distributed systems",
        "normalized": main.normalize_phrase("Design distributed systems"),
        "action_phrases": [],
        "category": "essential",
    }]

    result = main.gemini_responsibility_match(responsibilities, parsed)

    assert result["matched_responsibilities"] == []
    assert result["missing_responsibilities"][0]["verification_debug"]["matched_count"] == 0


def test_gemini_atom_match_uses_grounded_cv_evidence_id(monkeypatch):
    evidence = "[Project: Platform] Built and tested an application using Git and Docker for deployment."
    parsed = parsed_resume(
        resume_text=f"Projects\n{evidence}",
        projects=[{"name": "Platform", "bullets": ["Built and tested an application using Git and Docker for deployment."]}],
    )
    fake_json = json.dumps({
        "atom_matches": [
            {"atom_id": "r1a1", "evidence_id": "r1a1e2", "confidence": "strong"}
        ],
        "atom_missing": [],
    })
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    responsibilities = [{
        "text": "Interest in modern development practices",
        "normalized": main.normalize_phrase("Interest in modern development practices"),
        "action_phrases": [],
        "category": "essential",
    }]

    result = main.gemini_responsibility_match(responsibilities, parsed)
    match = result["matched_responsibilities"][0]

    assert match["confidence"] == "partial"
    assert match["verification_debug"]["atomic_breakdown"][0]["selected_evidence_id"] == "r1a1e2"
    assert match["evidence"] == evidence


def test_gemini_responsibility_match_rejects_evidence_not_in_retrieved_candidates(monkeypatch):
    parsed = parsed_resume(
        resume_text="Experience\nAnalysed marketing funnel performance.",
        work_experience=[
            {
                "title": "Analyst",
                "company": "Example",
                "bullets": ["Analysed marketing funnel performance."],
            }
        ],
    )
    fake_json = """
    {
      "matches": [
        {
          "index": 1,
          "responsibility": "Experience designing distributed systems",
          "evidence": "[Engineer @ InventedCo] Designed distributed systems using replication and sharding.",
          "confidence": "strong"
        }
      ],
      "missing": []
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    responsibilities = [
        {
            "text": "Experience designing distributed systems",
            "normalized": main.normalize_phrase("Experience designing distributed systems"),
            "action_phrases": [],
            "category": "essential",
        }
    ]

    result = main.gemini_responsibility_match(responsibilities, parsed)

    assert result["matched_responsibilities"] == []
    assert len(result["missing_responsibilities"]) == 1


def test_broad_responsibility_is_not_proved_by_skills_line_only(monkeypatch):
    parsed = parsed_resume(
        resume_text="Skills\nAnalytical problem solving, stakeholder management",
        skills=["Analytical problem solving", "stakeholder management"],
    )
    skills_line = main.cv_skills_evidence_line(parsed)
    fake_json = json.dumps({
        "matches": [
            {
                "index": 1,
                "responsibility": "Break down complex operational problems into manageable steps",
                "evidence": skills_line,
                "confidence": "strong",
            }
        ],
        "missing": [],
    })
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    responsibilities = [
        {
            "text": "Break down complex operational problems into manageable steps",
            "normalized": main.normalize_phrase("Break down complex operational problems into manageable steps"),
            "action_phrases": [],
            "category": "essential",
        }
    ]

    result = main.gemini_responsibility_match(responsibilities, parsed)

    assert result["matched_responsibilities"] == []
    assert len(result["missing_responsibilities"]) == 1


def test_learning_product_feature_does_not_prove_eagerness_to_learn(monkeypatch):
    evidence = (
        "Implemented AI-driven features including quiz generation and content "
        "summarisation using Hugging Face, adding intelligent learning functionality."
    )
    parsed = parsed_resume(
        resume_text=f"Projects\n{evidence}",
        projects=[{"name": "Learning Platform", "tech_stack": ["Hugging Face"], "bullets": [evidence]}],
    )
    fake_json = json.dumps({
        "matches": [
            {
                "index": 1,
                "responsibility": "A proactive attitude towards self-development and learning new skills",
                "evidence": evidence,
                "confidence": "strong",
            }
        ],
        "missing": [],
    })
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(fake_json))
    responsibilities = [
        {
            "text": "A proactive attitude towards self-development and learning new skills",
            "normalized": main.normalize_phrase(
                "A proactive attitude towards self-development and learning new skills"
            ),
            "action_phrases": [],
            "category": "essential",
        }
    ]

    result = main.gemini_responsibility_match(responsibilities, parsed)

    assert result["matched_responsibilities"] == []
    assert len(result["missing_responsibilities"]) == 1


@pytest.mark.parametrize(
    "requirement,evidence",
    [
        (
            "Hands-on experience with cloud platforms",
            "[Engineer @ Example] Built Python and AWS Lambda automation workflows that reduced manual workload by 70%.",
        ),
        (
            "Deliver data engineering solutions and pipelines",
            "[Engineer @ Example] Designed and maintained backend data systems and pipelines for large datasets.",
        ),
        (
            "Experience with end-to-end development of AI or Machine Learning features",
            "[Project: Predictor] Built and deployed a machine learning pipeline with feature engineering and XGBoost.",
        ),
        (
            "Calibrate optical sensors before each production run",
            "[Engineer @ Example] Calibrated optical sensors before production runs and documented the results.",
        ),
    ],
)
def test_general_capability_verifier_accepts_action_based_core_evidence(requirement, evidence):
    assert main._evidence_proves_any_core_concept(requirement, evidence)


@pytest.mark.parametrize(
    "requirement,evidence",
    [
        (
            "Hands-on experience with cloud platforms",
            "[Analyst @ Example] Analysed marketing funnel performance and prepared campaign reports.",
        ),
        (
            "Deliver data engineering solutions and pipelines",
            "[Project: Learning Platform] Implemented quiz generation and content summarisation.",
        ),
        (
            "Calibrate optical sensors before each production run",
            "[Researcher @ Example] Preserved archaeological samples under controlled humidity.",
        ),
        (
            "Technical leadership within an engineering team",
            "[Developer @ Example] Delivered a feature within a seven-person Agile team.",
        ),
    ],
)
def test_general_capability_verifier_rejects_generic_or_unrelated_evidence(requirement, evidence):
    assert not main._evidence_proves_any_core_concept(requirement, evidence)


def test_general_capability_verifier_rejects_skills_only_evidence():
    assert not main._evidence_proves_any_core_concept(
        "Hands-on experience with cloud platforms",
        "SKILLS: AWS, Azure, GCP",
    )


@pytest.mark.parametrize(
    "requirement,evidence,canonical",
    [
        (
            "Assist in automation and agent development to help scale solutions",
            "[Engineer @ Example] Automated operational workflows using Python and AWS Lambda.",
            "automation",
        ),
        (
            "Exposure to AI development techniques",
            "[Project: Predictor] Built an AI-powered forecasting model using XGBoost and FinBERT.",
            "ai_development",
        ),
        (
            "Support application deployment activities",
            "[Project: Platform] Containerised the application and deployed it through GitHub Actions to AWS ECR.",
            "application_deployment",
        ),
        (
            "Exposure to coding and scripting",
            "[Project: Platform] Built a full-stack platform using Python, FastAPI, and JavaScript.",
            "software_development",
        ),
    ],
)
def test_canonical_capability_equivalence_accepts_action_based_evidence(
    requirement,
    evidence,
    canonical,
):
    assert canonical in main._canonical_capability_keys(requirement)
    assert main._evidence_proves_any_core_concept(requirement, evidence)


@pytest.mark.parametrize(
    "requirement,evidence",
    [
        (
            "Exposure to AI development techniques",
            "[Analyst @ Example] Automated monthly spreadsheet reporting.",
        ),
        (
            "Support application deployment activities",
            "[Analyst @ Example] Monitored marketing campaign performance.",
        ),
        (
            "Assist in automation",
            "SKILLS: Python, AWS Lambda, workflow automation",
        ),
    ],
)
def test_canonical_capability_equivalence_rejects_unrelated_or_skills_only_evidence(
    requirement,
    evidence,
):
    assert not main._evidence_proves_any_core_concept(requirement, evidence)


def test_early_career_coding_exposure_is_proved_by_project_delivery():
    parsed = parsed_resume(
        resume_text="Projects\nBuilt a full-stack CV matching platform using Python and FastAPI.",
        projects=[
            {
                "name": "CV Matcher",
                "tech_stack": ["Python", "FastAPI"],
                "bullets": ["Built a full-stack CV matching platform using Python and FastAPI."],
            }
        ],
    )

    result = main.aggregate_requirement_evidence(
        "Some exposure to coding or scripting through university, personal projects, or placements",
        parsed,
        parsed["_resume_text"],
    )

    assert result["status"] == "present"
    assert all(item.get("group_status") == "present" for item in result["atomic_breakdown"])


def test_compound_capabilities_are_normalized_matched_and_aggregated_by_atom():
    parsed = parsed_resume(
        resume_text=(
            "Experience\nAutomated operational workflows using Python and AWS Lambda.\n"
            "Projects\nBuilt an AI-powered full-stack platform and deployed it with Docker and GitHub Actions."
        ),
        work_experience=[
            {
                "title": "Engineer",
                "company": "Example",
                "bullets": ["Automated operational workflows using Python and AWS Lambda."],
            }
        ],
        projects=[
            {
                "name": "AI Platform",
                "tech_stack": ["Python", "Docker", "GitHub Actions"],
                "bullets": ["Built an AI-powered full-stack platform and deployed it with Docker and GitHub Actions."],
            }
        ],
    )

    result = main.aggregate_requirement_evidence(
        "Exposure to coding, automation, AI development and application deployment",
        parsed,
        parsed["_resume_text"],
    )
    statuses = {
        main.normalize_phrase(item["requirement"]): item["status"]
        for item in result["atomic_breakdown"]
    }

    assert result["status"] == "partial"
    assert result["coverage_ratio"] == 0.25
    assert statuses == {
        "exposure to coding": "present",
        "automation": "missing",
        "ai development": "missing",
        "application deployment": "missing",
    }


def test_zero_verified_atoms_forces_missing_even_when_parent_or_ai_match_exists(monkeypatch):
    requirement = "Build dashboards and maintain data pipelines"
    parent_evidence = "[Developer @ Example] Delivered technical work across the platform."

    def parent_only_match(candidate, parsed, resume_text=""):
        if candidate == requirement:
            return {"evidence": parent_evidence, "section": "experience", "confidence": "partial"}
        return None

    monkeypatch.setattr(main, "find_cv_evidence_for_requirement", parent_only_match)
    result = main.aggregate_requirement_evidence(
        requirement,
        parsed_resume(resume_text=parent_evidence),
        parent_evidence,
        ai_present=True,
        ai_evidence=parent_evidence,
        ai_confidence="partial",
    )

    assert result["matched_count"] == 0
    assert result["status"] == "missing"
    assert result["final_score"] == 0


def test_one_strong_alternative_satisfies_group_without_penalizing_missing_options():
    parsed = parsed_resume(resume_text="Skills\nVue", skills=["Vue"])

    result = main.aggregate_requirement_evidence(
        "Experience with React, Angular, or Vue",
        parsed,
        parsed["_resume_text"],
    )

    assert result["status"] == "present"
    assert result["matched_count"] == 1
    assert result["missing_count"] == 0
    assert result["total_count"] == 1
    assert result["scoring_units"][0]["satisfied_by"] == "vue"


@pytest.mark.parametrize(
    "variant,canonical",
    [
        ("CI/CD", "ci_cd"),
        ("ci cd", "ci_cd"),
        ("CI-CD", "ci_cd"),
        ("cicd", "ci_cd"),
        ("continuous integration and deployment", "ci_cd"),
        ("Node", "node_js"),
        ("Node.js", "node_js"),
        ("C sharp", "c_sharp"),
        (".NET", "dotnet"),
        ("JS", "javascript"),
    ],
)
def test_exact_alias_variants_use_same_canonical_key(variant, canonical):
    assert main.exact_alias_key(variant) == canonical


def test_exact_alias_registry_does_not_match_unrelated_named_tools():
    assert main.exact_alias_key("Java") is None
    assert main.classify_requirement_evidence_match(
        "Experience with JavaScript",
        "SKILLS: Java",
    ) is None


@pytest.mark.parametrize("evidence_variant", ["CI/CD", "CI-CD", "cicd", "continuous integration and deployment"])
def test_exact_alias_variants_can_verify_same_named_tool(evidence_variant):
    parsed = parsed_resume(resume_text=f"Skills\n{evidence_variant}", skills=[evidence_variant])

    result = main.aggregate_requirement_evidence(
        "Experience with CI/CD",
        parsed,
        parsed["_resume_text"],
    )

    assert result["status"] == "present"
    assert result["atomic_breakdown"][0]["canonical_atom"] == "ci_cd"


def test_evidence_for_one_atom_cannot_prove_a_different_atom():
    parsed = parsed_resume(resume_text="Skills\nPython", skills=["Python"])

    result = main.aggregate_requirement_evidence(
        "Python and Java",
        parsed,
        parsed["_resume_text"],
    )
    statuses = {
        main.normalize_phrase(item["requirement"]): item["status"]
        for item in result["atomic_breakdown"]
    }

    assert statuses == {"python": "present", "java": "missing"}
    assert result["status"] == "partial"
    assert result["matched_count"] == 1
    assert result["missing_count"] == 1


@pytest.mark.parametrize("formal_atom", ["internship experience", "placement experience"])
def test_founder_evidence_does_not_prove_formal_early_career_programmes(formal_atom):
    evidence = "[Co-Founder @ Example] Built a full-stack platform used by 60 customers."
    parsed = parsed_resume(
        resume_text=evidence,
        work_experience=[{"title": "Co-Founder", "company": "Example", "bullets": [evidence]}],
    )

    result = main.aggregate_requirement_evidence(formal_atom, parsed, parsed["_resume_text"])

    assert result["status"] == "missing"
    assert result["matched_count"] == 0


def test_project_evidence_proves_coding_exposure_only_with_technical_delivery_signal():
    technical = parsed_resume(
        resume_text="Projects\nBuilt a Python API for processing job applications.",
        projects=[{"name": "API", "bullets": ["Built a Python API for processing job applications."]}],
    )
    nontechnical = parsed_resume(
        resume_text="Projects\nReviewed market trends and prepared a presentation.",
        projects=[{"name": "Research", "bullets": ["Reviewed market trends and prepared a presentation."]}],
    )
    requirement = "Exposure to coding through personal projects"

    assert main.aggregate_requirement_evidence(
        requirement,
        technical,
        technical["_resume_text"],
    )["status"] == "present"
    assert main.aggregate_requirement_evidence(
        requirement,
        nontechnical,
        nontechnical["_resume_text"],
    )["status"] == "missing"


def test_atom_scoring_debug_output_is_complete_and_authoritative():
    parsed = parsed_resume(resume_text="Skills\nPython", skills=["Python"])

    result = main.aggregate_requirement_evidence(
        "Python or Java",
        parsed,
        parsed["_resume_text"],
    )

    assert result["original_requirement"] == "Python or Java"
    assert result["final_score"] == 1.0
    assert result["matched_scoring_units"] == [result["scoring_units"][0]["scoring_unit_id"]]
    assert result["missing_scoring_units"] == []
    assert result["matched_count"] == 1
    assert result["total_count"] == 1
    assert result["status"] == "present"
    assert result["scoring_units"][0]["satisfied_by"] == "python"
    assert all(
        {
            "canonical_atom",
            "selected_evidence",
            "verification_result",
            "alternative_group",
        }.issubset(atom)
        for atom in result["atomic_breakdown"]
    )


@pytest.mark.parametrize(
    "requirement,evidence,expected_families",
    [
        (
            "Understanding of DevOps concepts",
            "SKILLS: AWS, Docker, GitHub Actions, CI/CD",
            {"deployment", "cloud"},
        ),
        (
            "Interest in modern development practices",
            "[Project: Platform] Built and tested an application using Git, Docker, and GitHub Actions for deployment.",
            {"software_delivery", "version_control", "testing_quality", "deployment"},
        ),
        (
            "Experience in collaborative delivery environments",
            "[Developer @ Example] Delivered features within a seven-person Agile team against stakeholder requirements.",
            {"collaboration", "delivery_management"},
        ),
    ],
)
def test_general_evidence_policy_verifies_abstract_capabilities(
    requirement,
    evidence,
    expected_families,
):
    verified = main.verify_evidence_policy(requirement, evidence)

    assert verified
    assert expected_families.issubset(set(verified["matched_signal_families"]))


@pytest.mark.parametrize(
    "requirement,evidence",
    [
        (
            "Understanding of DevOps concepts",
            "SKILLS: Microsoft Excel",
        ),
        (
            "Interest in modern development practices",
            "SKILLS: Git, Docker, CI/CD",
        ),
        (
            "Experience in collaborative delivery environments",
            "[Analyst @ Example] Prepared individual monthly reports.",
        ),
    ],
)
def test_general_evidence_policy_rejects_insufficient_or_non_action_evidence(
    requirement,
    evidence,
):
    assert main.verify_evidence_policy(requirement, evidence) is None


def test_grounded_atom_selection_can_partially_verify_unknown_general_capability():
    parsed = parsed_resume(
        resume_text="Projects\nBuilt and tested an application using Git and Docker for deployment.",
        projects=[
            {
                "name": "Platform",
                "bullets": ["Built and tested an application using Git and Docker for deployment."],
            }
        ],
    )

    result = main.aggregate_requirement_evidence(
        "Interest in modern development practices",
        parsed,
        parsed["_resume_text"],
        ai_atom_matches={
            main.normalize_phrase("modern development practices"): {
                "evidence_id": "r1a1e1",
                "evidence": "[Project: Platform] Built and tested an application using Git and Docker for deployment.",
                "confidence": "strong",
            }
        },
    )
    atom = result["atomic_breakdown"][0]

    assert result["status"] == "partial"
    assert atom["selected_evidence_id"] == "r1a1e1"
    assert atom["selected_evidence"].startswith("[Project: Platform]")


def test_years_experience_cannot_be_proved_by_one_backend_bullet():
    evidence = "[Engineer @ Example] Developed backend services using Python and MySQL."

    assert main.classify_requirement_evidence_match(
        "3+ years of experience in backend engineering",
        evidence,
    ) is None


def test_bundled_named_tools_are_checked_independently():
    parsed = parsed_resume(
        resume_text="Projects\nBuilt APIs using Python and FastAPI.",
        skills=["Python", "FastAPI"],
        projects=[
            {
                "name": "API Platform",
                "tech_stack": ["Python", "FastAPI"],
                "bullets": ["Built APIs using Python and FastAPI."],
            }
        ],
    )

    result = main.aggregate_requirement_evidence(
        "Design and maintain APIs and microservices using Python, FastAPI, and Postgres",
        parsed,
        parsed["_resume_text"],
    )

    statuses = {
        main.normalize_phrase(item["requirement"]): item["status"]
        for item in result["atomic_breakdown"]
    }
    assert result["status"] == "partial"
    assert statuses["python"] == "present"
    assert statuses["fastapi"] == "present"
    assert statuses["postgres"] == "missing"
    assert any("microservices" in requirement and status == "missing" for requirement, status in statuses.items())


def test_aws_does_not_prove_specific_snowflake_requirement():
    assert not main._evidence_proves_any_core_concept(
        "Specific experience with Snowflake",
        "[Engineer @ Example] Built AWS Lambda workflows for operational data.",
    )


def test_generic_data_atom_does_not_prove_domain_passion():
    assert not main._evidence_proves_any_core_concept(
        "Domain Passion: Use data and intelligence to drive ad campaign efficiency and demonstrate media investment value",
        "[Founder @ Example] Built a data-driven EdTech platform supporting user engagement analysis.",
    )


def test_one_line_must_cover_all_core_atoms_to_be_strong():
    requirement = "Build scalable backend systems for AI and machine learning models in production"
    backend_only = "[Engineer @ Example] Developed backend data structures and automated workflows."
    full_evidence = (
        "[Engineer @ Example] Built backend systems that deployed machine learning models "
        "into production."
    )

    assert not main._evidence_proves_all_core_concepts(requirement, backend_only)
    assert main._evidence_proves_all_core_concepts(requirement, full_evidence)


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


def test_degree_project_circuit_experience_not_proven_by_cs_degree_only():
    parsed_resume = {
        "_resume_text": """
        Education
        BSc Computer Science (Artificial Intelligence), University of Nottingham, 2025
        """,
        "summary": "",
        "skills": ["Python"],
        "tools": [],
        "education": [
            {
                "degree": "BSc Computer Science (Artificial Intelligence)",
                "institution": "University of Nottingham",
                "graduation_year": "2025",
            }
        ],
        "projects": [],
        "work_experience": [],
    }

    degree = main.aggregate_requirement_evidence(
        "Undergraduate degree in electronic engineering, computer science or similar discipline",
        parsed_resume,
        parsed_resume["_resume_text"],
    )
    circuits = main.aggregate_requirement_evidence(
        "Experience analysing, designing, constructing & testing electronic circuits during degree projects",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert degree["status"] == "present"
    assert circuits["status"] == "missing"


def test_postgraduate_qualification_not_proven_by_bsc():
    bsc_resume = {
        "_resume_text": "Education\nBSc Computer Science, University of Nottingham, 2025",
        "summary": "",
        "skills": [],
        "tools": [],
        "education": [
            {
                "degree": "BSc Computer Science",
                "institution": "University of Nottingham",
                "graduation_year": "2025",
            }
        ],
        "projects": [],
        "work_experience": [],
    }
    msc_resume = {
        **bsc_resume,
        "_resume_text": "Education\nMSc Computer Science, University of Nottingham, 2026",
        "education": [
            {
                "degree": "MSc Computer Science",
                "institution": "University of Nottingham",
                "graduation_year": "2026",
            }
        ],
    }

    requirement = "Post graduate qualification (Master/PHD) in electronics engineering, computer science or similar discipline"

    assert main.aggregate_requirement_evidence(requirement, bsc_resume, bsc_resume["_resume_text"])["status"] == "missing"
    assert main.aggregate_requirement_evidence(requirement, msc_resume, msc_resume["_resume_text"])["status"] == "present"


def test_master_data_management_is_not_treated_as_masters_degree():
    parsed_resume = {
        "_resume_text": "Projects\nBuilt a data quality dashboard using SQL.",
        "summary": "",
        "skills": ["SQL", "data quality"],
        "tools": [],
        "education": [
            {
                "degree": "BSc Computer Science",
                "institution": "University of Nottingham",
                "graduation_year": "2025",
            }
        ],
        "projects": [
            {
                "name": "Data Quality Dashboard",
                "tech_stack": ["SQL"],
                "bullets": ["Built a data quality dashboard using SQL."],
            }
        ],
        "work_experience": [],
    }

    policy = main._requirement_policy("Master data management experience")
    result = main.aggregate_requirement_evidence(
        "Master data management experience",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert policy["type"] != "postgraduate_degree"
    assert result["status"] != "present"


def test_final_year_degree_project_does_not_prove_electronics_without_evidence():
    parsed_resume = {
        "_resume_text": """
        Education
        BSc Computer Science, University of Nottingham, 2025

        Projects
        Final year degree project: Built a web analytics dashboard using Python and React.
        """,
        "summary": "",
        "skills": ["Python", "React"],
        "tools": [],
        "education": [
            {
                "degree": "BSc Computer Science",
                "institution": "University of Nottingham",
                "graduation_year": "2025",
            }
        ],
        "projects": [
            {
                "name": "Final Year Degree Project",
                "tech_stack": ["Python", "React"],
                "bullets": ["Built a web analytics dashboard using Python and React."],
            }
        ],
        "work_experience": [],
    }

    result = main.aggregate_requirement_evidence(
        "Experience analysing, designing, constructing & testing electronic circuits during degree projects",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert result["status"] == "missing"


def test_certification_named_project_does_not_prove_certification():
    parsed_resume = {
        "_resume_text": """
        Projects
        AWS Certification Tracker: Built a dashboard for tracking cloud learning progress.
        """,
        "summary": "",
        "skills": ["AWS", "Python"],
        "tools": [],
        "education": [],
        "certifications": [],
        "projects": [
            {
                "name": "AWS Certification Tracker",
                "tech_stack": ["Python"],
                "bullets": ["Built a dashboard for tracking cloud learning progress."],
            }
        ],
        "work_experience": [],
    }

    result = main.aggregate_requirement_evidence(
        "AWS certification",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert result["status"] == "missing"


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


def test_lead_fullstack_jd_extracts_owned_responsibilities_when_gemini_is_sparse(monkeypatch):
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient('{"requirements":[{"text":"Excellent communication skills","category":"essential"}]}'))
    jd = """
    How You'll Spend Your Time
    You will design, develop, test, deploy, and improve digital products with a focus on full-stack development.
    You will be responsible for the technical approach to problems, and getting the team aligned on a technical vision.
    You will be accountable for the technical delivery of the project by the team.

    What We're Looking For
    Experience deploying, securing, scaling, and monitoring in the cloud with deep experience of AWS, Azure, or GCP.
    Experience with automated testing and creating CI/CD pipelines.
    """

    requirements = main.extract_job_responsibilities(jd)
    texts = [req["text"].lower() for req in requirements]

    assert len(requirements) >= 6
    assert any("technical vision" in text for text in texts)
    assert any("technical delivery" in text for text in texts)
    assert any("automated testing" in text or "pipelines" in text for text in texts)


def test_application_positioning_uses_evidence_score():
    positioning = main.build_application_positioning(35, 45)

    assert positioning == {
        "headline": "Developing fit",
        "cover_letter_tone": "balanced and evidence-led",
        "cover_letter_guidance": "Focus on transferable evidence and avoid unsupported claims.",
        "technical_relevance_score": 45,
    }


def test_at_least_one_language_examples_are_not_all_required():
    parsed_resume = {
        "_resume_text": "Skills\nPython, SQL, Git",
        "summary": "",
        "skills": ["Python", "SQL", "Git"],
        "tools": [],
        "education": [],
        "projects": [],
        "work_experience": [],
    }

    result = main.aggregate_requirement_evidence(
        "Strong programming skills in at least one relevant language such as Python, Java, C++, C#, or JavaScript",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert result["status"] == "present"
    assert result["matched_count"] == 1
    assert result["total_count"] == 1
    assert any(
        atom["requirement"].lower() == "python" and atom["status"] == "present"
        for atom in result["atomic_breakdown"]
    )
    assert any(
        atom["requirement"].lower() == "c++" and atom["status"] == "missing"
        for atom in result["atomic_breakdown"]
    )


def test_such_as_framework_examples_are_alternatives():
    parsed_resume = {
        "_resume_text": "Skills\nMachine learning, PyTorch, TensorFlow",
        "summary": "",
        "skills": ["Machine learning", "PyTorch", "TensorFlow"],
        "tools": [],
        "education": [],
        "projects": [],
        "work_experience": [],
    }

    result = main.aggregate_requirement_evidence(
        "Exposure to machine learning frameworks or libraries such as PyTorch, TensorFlow, or Scikit-learn",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert result["status"] == "present"
    assert result["matched_count"] == 1
    assert result["total_count"] == 1
    assert any(
        atom["requirement"].lower() == "scikit-learn" and atom["status"] == "missing"
        for atom in result["atomic_breakdown"]
    )


def test_missing_skills_from_satisfied_alternative_list_are_not_penalized():
    items = [
        {"skill": "Python", "status": "present", "present": True},
        {"skill": "C++", "status": "missing", "present": False},
        {"skill": "C#", "status": "missing", "present": False},
        {"skill": "JavaScript", "status": "present", "present": True},
    ]
    jd = "Strong programming skills in at least one relevant language such as Python, Java, C++, C#, or JavaScript."

    result = main.filter_satisfied_alternative_missing_skills(items, jd)
    skills = [item["skill"] for item in result]

    assert "Python" in skills
    assert "JavaScript" in skills
    assert "C++" not in skills
    assert "C#" not in skills


def test_plain_or_skill_alternative_suppresses_only_the_missing_option():
    items = [
        {"skill": "Power BI", "status": "present", "present": True},
        {"skill": "dashboarding experience", "status": "missing", "present": False},
        {"skill": "dashboarding", "status": "missing", "present": False},
        {"skill": "Git familiarity", "status": "missing", "present": False},
    ]
    jd = "Preferred Skills\nPower BI or dashboarding experience and Git familiarity."

    result = main.filter_satisfied_alternative_missing_skills(items, jd)
    skills = [item["skill"] for item in result]

    assert "Power BI" in skills
    assert "dashboarding experience" not in skills
    assert "dashboarding" not in skills
    assert "Git familiarity" in skills


def test_early_career_project_experience_satisfies_zero_to_one_year_requirement():
    parsed_resume = {
        "_resume_text": """
        Education
        BSc Computer Science with Artificial Intelligence, University of Nottingham, 2025

        Projects
        Built a CV matching platform using Python and FastAPI.
        """,
        "summary": "",
        "skills": ["Python"],
        "tools": [],
        "education": [
            {
                "degree": "BSc Computer Science with Artificial Intelligence",
                "institution": "University of Nottingham",
                "graduation_year": "2025",
            }
        ],
        "projects": [
            {
                "name": "CV Matching Platform",
                "tech_stack": ["Python", "FastAPI"],
                "bullets": ["Built a CV matching platform using Python and FastAPI."],
            }
        ],
        "work_experience": [],
    }

    result = main.aggregate_requirement_evidence(
        "0-1 years of relevant experience through academic projects, internships, placements, or early professional experience",
        parsed_resume,
        parsed_resume["_resume_text"],
    )

    assert result["status"] == "present"
    assert result["section"] in {"education", "projects"}


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


def test_gemini_lite_audit_removes_flagged_rewrite_skill(monkeypatch):
    resume_text = "Skills\nPython, Excel\nExperience\nAutomated Excel reporting."
    rewrite = {
        "skills_section": [
            {"category": "Skills", "items": ["Python", "Excel", "Regulatory Reporting"]}
        ],
        "additional_keywords_to_include": [],
        "missing_information": [],
    }
    audit_json = """
    {
      "unsupported_claims": [
        {
          "claim": "Regulatory Reporting",
          "source_section": "skills_section",
          "reason": "Original CV does not mention regulatory reporting.",
          "severity": "remove"
        }
      ],
      "safe_claims": ["Python", "Excel"]
    }
    """
    monkeypatch.setattr(main, "GENAI_CLIENT", FakeClient(audit_json))

    result = main.audit_and_validate_rewrite(rewrite, resume_text, "Role requires regulatory reporting.")
    skills = result["skills_section"][0]["items"]

    assert "Python" in skills
    assert "Excel" in skills
    assert "Regulatory Reporting" not in skills
    assert any("Regulatory Reporting" in item for item in result["additional_keywords_to_include"])
    assert result["rewrite_audit"]["unsupported_count"] == 1


def test_rewrite_audit_fallback_keeps_deterministic_validation(monkeypatch):
    resume_text = "Skills\nPython\nProjects\nBuilt APIs with Python."
    rewrite = {
        "skills_section": [
            {"category": "Skills", "items": ["Python", "Kubernetes"]}
        ],
        "additional_keywords_to_include": [],
        "missing_information": [],
    }
    monkeypatch.setattr(main, "GENAI_CLIENT", RaisingClient())

    result = main.audit_and_validate_rewrite(rewrite, resume_text, "Role requires Kubernetes.")
    skills = result["skills_section"][0]["items"]

    assert "Python" in skills
    assert "Kubernetes" not in skills
    assert result["rewrite_audit"]["unsupported_count"] == 0


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
        "present",
        "skills",
        id="communication can use skills or work evidence",
    ),
    pytest.param(
        "written_verbal_presentation_strong_from_reports_and_presenting",
        {
            "_resume_text": "Experience\nPresented weekly reports to stakeholders and explained performance trends clearly.",
            "summary": "",
            "skills": [],
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
        "Excellent written and verbal communication and presentation skills",
        "present",
        "experience",
        id="reports plus presentations prove communication modes",
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


def parsed_resume(
    *,
    resume_text="",
    summary="",
    skills=None,
    tools=None,
    education=None,
    certifications=None,
    projects=None,
    work_experience=None,
):
    return {
        "_resume_text": resume_text,
        "summary": summary,
        "skills": skills or [],
        "tools": tools or [],
        "education": education or [],
        "certifications": certifications or [],
        "projects": projects or [],
        "work_experience": work_experience or [],
    }


FALSE_POSITIVE_REQUIREMENT_FIXTURES = [
    pytest.param(
        "aws_dashboard_not_certification",
        parsed_resume(
            resume_text="Projects\nAWS Cost Dashboard: Built a dashboard showing monthly cloud spend.",
            skills=["AWS", "Python"],
            projects=[{"name": "AWS Cost Dashboard", "tech_stack": ["AWS", "Python"], "bullets": ["Built a dashboard showing monthly cloud spend."]}],
        ),
        "AWS Solutions Architect certification",
        id="AWS project does not prove AWS certification",
    ),
    pytest.param(
        "acca_tracker_not_qualification",
        parsed_resume(
            resume_text="Projects\nACCA Study Tracker: Built a revision planner for accounting exams.",
            skills=["accounting basics", "Python"],
            projects=[{"name": "ACCA Study Tracker", "tech_stack": ["Python"], "bullets": ["Built a revision planner for accounting exams."]}],
        ),
        "ACCA qualification",
        id="ACCA study project does not prove ACCA qualification",
    ),
    pytest.param(
        "cpa_calculator_not_certification",
        parsed_resume(
            resume_text="Projects\nBuilt a tax calculator for CPA exam practice questions.",
            projects=[{"name": "Tax Calculator", "tech_stack": ["JavaScript"], "bullets": ["Built a tax calculator for CPA exam practice questions."]}],
        ),
        "CPA certification",
        id="CPA practice project does not prove CPA certification",
    ),
    pytest.param(
        "business_project_not_mba",
        parsed_resume(
            resume_text="Projects\nBuilt a business strategy case-study recommender.",
            education=[{"degree": "BSc Computer Science", "institution": "Nottingham"}],
            projects=[{"name": "Strategy Recommender", "tech_stack": ["Python"], "bullets": ["Built a business strategy case-study recommender."]}],
        ),
        "MBA degree",
        id="business project does not prove MBA",
    ),
    pytest.param(
        "healthcare_app_not_nursing_degree",
        parsed_resume(
            resume_text="Projects\nBuilt a healthcare appointment scheduling app.",
            education=[{"degree": "BSc Computer Science", "institution": "Nottingham"}],
            projects=[{"name": "Healthcare Scheduler", "tech_stack": ["React"], "bullets": ["Built a healthcare appointment scheduling app."]}],
        ),
        "Nursing degree",
        id="healthcare app does not prove nursing degree",
    ),
    pytest.param(
        "project_template_not_pmp",
        parsed_resume(
            resume_text="Projects\nBuilt a PMP-style project plan template generator.",
            projects=[{"name": "Project Planner", "tech_stack": ["Python"], "bullets": ["Built a PMP-style project plan template generator."]}],
        ),
        "PMP certification",
        id="PMP template does not prove PMP certification",
    ),
    pytest.param(
        "stock_dashboard_not_financial_statements",
        degree_resume(),
        "financial statement preparation",
        id="market project does not prove financial statement preparation",
    ),
    pytest.param(
        "report_automation_not_regulatory_reporting",
        parsed_resume(
            resume_text="Projects\nAutomated weekly operational report exports using Excel macros.",
            skills=["Excel", "report automation"],
            projects=[{"name": "Report Automation", "tech_stack": ["Excel"], "bullets": ["Automated weekly operational report exports using Excel macros."]}],
        ),
        "regulatory reporting",
        id="generic report automation does not prove regulatory reporting",
    ),
    pytest.param(
        "react_web_not_react_native",
        parsed_resume(
            resume_text="Projects\nBuilt a React web dashboard.",
            skills=["React", "JavaScript"],
            projects=[{"name": "Web Dashboard", "tech_stack": ["React"], "bullets": ["Built a React web dashboard."]}],
        ),
        "React Native",
        id="React web does not prove React Native",
    ),
    pytest.param(
        "javascript_not_typescript",
        parsed_resume(
            resume_text="Skills\nJavaScript, React",
            skills=["JavaScript", "React"],
        ),
        "TypeScript",
        id="JavaScript does not prove TypeScript",
    ),
    pytest.param(
        "docker_compose_not_terraform",
        parsed_resume(
            resume_text="Projects\nContainerised a Flask API with Docker Compose.",
            skills=["Docker", "Python"],
            projects=[{"name": "API Containerisation", "tech_stack": ["Docker"], "bullets": ["Containerised a Flask API with Docker Compose."]}],
        ),
        "Terraform",
        id="Docker Compose does not prove Terraform",
    ),
    pytest.param(
        "content_calendar_not_cms",
        parsed_resume(
            resume_text="Experience\nMaintained a shared content calendar in Microsoft Excel.",
            skills=["content calendars", "Microsoft Excel"],
            work_experience=[
                {
                    "title": "Communications Intern",
                    "company": "Charity",
                    "bullets": ["Maintained a shared content calendar in Microsoft Excel."],
                }
            ],
        ),
        "Content management system experience is essential",
        id="content calendar does not prove CMS",
    ),
    pytest.param(
        "email_engagement_not_google_analytics",
        parsed_resume(
            resume_text="Experience\nSummarised reader feedback and email engagement notes.",
            skills=["reader feedback", "email engagement"],
            work_experience=[
                {
                    "title": "Communications Intern",
                    "company": "Charity",
                    "bullets": ["Summarised reader feedback and email engagement notes."],
                }
            ],
        ),
        "Google Analytics or similar reporting tools experience",
        id="email notes do not prove Google Analytics/reporting tool",
    ),
    pytest.param(
        "style_consistency_not_indesign",
        parsed_resume(
            resume_text="Experience\nManaged weekly deadlines, style consistency, and image captions.",
            skills=["style guides", "copyediting"],
            work_experience=[
                {
                    "title": "Editor",
                    "company": "Student Newspaper",
                    "bullets": ["Managed weekly deadlines, style consistency, and image captions."],
                }
            ],
        ),
        "Adobe InDesign experience is desirable",
        id="style editing does not prove Adobe InDesign",
    ),
    pytest.param(
        "case_studies_not_confidential_student_data",
        parsed_resume(
            resume_text="Experience\nInterviewed volunteers and converted notes into concise case studies.",
            skills=["interviewing", "case studies"],
            work_experience=[
                {
                    "title": "Communications Intern",
                    "company": "Charity",
                    "bullets": ["Interviewed volunteers and converted notes into concise case studies."],
                }
            ],
        ),
        "Handle confidential student and partner information responsibly",
        id="case studies do not prove confidential information handling",
    ),
    pytest.param(
        "validation_checks_not_incident_investigation",
        parsed_resume(
            resume_text="Experience\nImplemented validation checks across operational data, improving reliability by 40%.",
            skills=["data validation"],
            work_experience=[{"title": "Data Engineer", "company": "DataCo", "bullets": ["Implemented validation checks across operational data, improving reliability by 40%."]}],
        ),
        "Support incident investigations by analysing historical operational data and presenting root-cause findings",
        id="validation checks do not prove incident investigation",
    ),
    pytest.param(
        "mentoring_bot_not_line_management",
        parsed_resume(
            resume_text="Projects\nBuilt a mentoring chatbot for onboarding FAQs.",
            skills=["mentoring content", "Python"],
            projects=[{"name": "Mentoring Bot", "tech_stack": ["Python"], "bullets": ["Built a mentoring chatbot for onboarding FAQs."]}],
        ),
        "line management experience",
        id="mentoring chatbot does not prove line management",
    ),
    pytest.param(
        "language_app_not_french_fluency",
        parsed_resume(
            resume_text="Projects\nBuilt a French restaurant booking vocabulary app.",
            skills=["React"],
            projects=[{"name": "French Vocabulary App", "tech_stack": ["React"], "bullets": ["Built a French restaurant booking vocabulary app."]}],
        ),
        "French fluency",
        id="French app does not prove French fluency",
    ),
    pytest.param(
        "security_dashboard_not_iso27001",
        parsed_resume(
            resume_text="Projects\nBuilt a dashboard for vulnerability trend analysis.",
            skills=["security dashboards"],
            projects=[{"name": "Security Dashboard", "tech_stack": ["Python"], "bullets": ["Built a dashboard for vulnerability trend analysis."]}],
        ),
        "ISO 27001 certification",
        id="security dashboard does not prove ISO 27001 certification",
    ),
]


@pytest.mark.parametrize("name,parsed_resume,requirement", FALSE_POSITIVE_REQUIREMENT_FIXTURES)
def test_false_positive_requirement_fixtures_stay_missing(name, parsed_resume, requirement):
    result = main.aggregate_requirement_evidence(
        requirement,
        parsed_resume,
        parsed_resume.get("_resume_text", ""),
    )

    assert result["status"] == "missing", name


FALSE_NEGATIVE_REQUIREMENT_FIXTURES = [
    pytest.param(
        "sql_skill_present",
        parsed_resume(resume_text="Skills\nSQL, Python", skills=["SQL", "Python"]),
        "SQL",
        "skills",
        id="SQL skill is detected",
    ),
    pytest.param(
        "react_typescript_present",
        parsed_resume(resume_text="Skills\nReact, TypeScript", skills=["React", "TypeScript"]),
        "React and TypeScript",
        "skills",
        id="React and TypeScript are both detected",
    ),
    pytest.param(
        "aws_lambda_present",
        parsed_resume(
            resume_text="Projects\nBuilt a serverless parser with AWS Lambda and S3.",
            skills=["AWS Lambda", "S3"],
            projects=[{"name": "Serverless Parser", "tech_stack": ["AWS Lambda", "S3"], "bullets": ["Built a serverless parser with AWS Lambda and S3."]}],
        ),
        "AWS Lambda",
        "skills",
        id="AWS Lambda exact tool is detected",
    ),
    pytest.param(
        "strong_python_programming_present",
        parsed_resume(resume_text="Skills\nPython, SQL", skills=["Python", "SQL"]),
        "Strong Python programming skills",
        "skills",
        id="Python is detected inside programming skills phrase",
    ),
    pytest.param(
        "github_actions_present",
        parsed_resume(
            resume_text="Skills\nGitHub Actions, Docker\nExperience\nUsed GitHub Actions for deployment workflows.",
            skills=["GitHub Actions", "Docker"],
            work_experience=[{"title": "Developer", "company": "DevCo", "bullets": ["Used GitHub Actions for deployment workflows."]}],
        ),
        "Experience using GitHub Actions",
        "skills",
        id="GitHub Actions is detected inside experience using phrase",
    ),
    pytest.param(
        "cicd_present",
        parsed_resume(
            resume_text="Experience\nCreated CI/CD pipelines with GitHub Actions for deployment.",
            tools=["GitHub Actions"],
            work_experience=[{"title": "Developer", "company": "DevCo", "bullets": ["Created CI/CD pipelines with GitHub Actions for deployment."]}],
        ),
        "CI/CD pipelines",
        "experience",
        id="CI/CD pipeline evidence is detected",
    ),
    pytest.param(
        "line_management_present",
        parsed_resume(
            resume_text="Experience\nLine managed 3 analysts and ran quarterly performance reviews.",
            skills=["line management"],
            work_experience=[{"title": "Analytics Lead", "company": "DataCo", "bullets": ["Line managed 3 analysts and ran quarterly performance reviews."]}],
        ),
        "line management experience",
        "experience",
        id="line management is detected",
    ),
    pytest.param(
        "pmp_cert_present",
        parsed_resume(
            resume_text="Certifications\nProject Management Professional (PMP)",
            certifications=["Project Management Professional (PMP)"],
        ),
        "PMP certification",
        "certifications",
        id="PMP certification is detected",
    ),
    pytest.param(
        "msc_present",
        parsed_resume(
            resume_text="Education\nMSc Computer Science, University of Nottingham, 2026",
            education=[{"degree": "MSc Computer Science", "institution": "University of Nottingham", "graduation_year": "2026"}],
        ),
        "Postgraduate degree in Computer Science",
        "education",
        id="MSc Computer Science is detected",
    ),
    pytest.param(
        "regulatory_reporting_present",
        parsed_resume(
            resume_text="Experience\nPrepared regulatory reporting packs for FCA submissions.",
            skills=["regulatory reporting"],
            work_experience=[{"title": "Reporting Analyst", "company": "BankCo", "bullets": ["Prepared regulatory reporting packs for FCA submissions."]}],
        ),
        "regulatory reporting",
        "skills",
        id="regulatory reporting is detected",
    ),
    pytest.param(
        "kubernetes_present",
        parsed_resume(resume_text="Skills\nKubernetes, Docker", skills=["Kubernetes", "Docker"]),
        "Kubernetes",
        "skills",
        id="Kubernetes is detected",
    ),
    pytest.param(
        "figma_present",
        parsed_resume(resume_text="Skills\nFigma, user research", skills=["Figma", "user research"]),
        "Figma",
        "skills",
        id="Figma is detected",
    ),
    pytest.param(
        "spanish_fluency_present",
        parsed_resume(resume_text="Skills\nFluent Spanish and English", skills=["Fluent Spanish", "English"]),
        "Spanish fluency",
        "skills",
        id="Spanish fluency is detected",
    ),
    pytest.param(
        "written_english_present",
        parsed_resume(
            resume_text="Profile\nStrong written English, proofreading, copyediting, and research synthesis.",
            summary="Strong written English, proofreading, copyediting, and research synthesis.",
            skills=["written English", "proofreading", "copyediting"],
        ),
        "Excellent written English",
        "skills",
        id="written English is detected",
    ),
    pytest.param(
        "cms_present",
        parsed_resume(resume_text="Skills\nWordPress CMS, copyediting", skills=["WordPress CMS", "copyediting"]),
        "Content management system experience",
        "skills",
        id="CMS is detected as content management system",
    ),
    pytest.param(
        "gdpr_work_present",
        parsed_resume(
            resume_text="Experience\nMaintained GDPR compliance documentation for data subject request workflows.",
            skills=["GDPR compliance"],
            work_experience=[{"title": "Data Governance Analyst", "company": "DataCo", "bullets": ["Maintained GDPR compliance documentation for data subject request workflows."]}],
        ),
        "GDPR compliance",
        "skills",
        id="GDPR compliance is detected",
    ),
]


@pytest.mark.parametrize("name,parsed_resume,requirement,expected_section", FALSE_NEGATIVE_REQUIREMENT_FIXTURES)
def test_false_negative_requirement_fixtures_are_found(name, parsed_resume, requirement, expected_section):
    result = main.aggregate_requirement_evidence(
        requirement,
        parsed_resume,
        parsed_resume.get("_resume_text", ""),
    )

    assert result["status"] == "present", name
    assert result["section"] == expected_section, name
