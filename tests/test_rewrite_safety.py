import main


def test_rewrite_skill_validator_removes_unevidenced_certification():
    rewrite = {
        "skills_section": [{"category": "Certifications", "items": ["AWS Solutions Architect", "Python"]}],
        "additional_keywords_to_include": [],
        "missing_information": [],
    }

    result = main.validate_rewrite_skills(rewrite, "Skills\nPython\nProjects\nBuilt Python APIs.")
    skills = result["skills_section"][0]["items"]

    assert "Python" in skills
    assert "AWS Solutions Architect" not in skills
    assert any("AWS Solutions Architect" in item for item in result["additional_keywords_to_include"])


def test_rewrite_skill_validator_keeps_evidenced_exact_tool():
    rewrite = {
        "skills_section": [{"category": "Tools", "items": ["Kubernetes", "Docker"]}],
        "additional_keywords_to_include": [],
        "missing_information": [],
    }

    result = main.validate_rewrite_skills(rewrite, "Skills\nKubernetes, Docker\nExperience\nDeployed services to Kubernetes.")
    skills = result["skills_section"][0]["items"]

    assert "Kubernetes" in skills
    assert "Docker" in skills


def test_rewrite_audit_removes_invented_experience_bullet():
    rewrite = {
        "experience_section": [
            {
                "heading": "Developer | DevCo | 2024",
                "bullets": [
                    "Built Python APIs for internal tools.",
                    "Managed a team of 6 engineers across two squads.",
                ],
            }
        ],
        "additional_keywords_to_include": [],
    }
    audit = {
        "unsupported_claims": [
            {
                "claim": "Managed a team of 6 engineers across two squads.",
                "source_section": "experience_section",
                "severity": "remove",
                "reason": "Original CV has no people management evidence.",
            }
        ]
    }

    result = main.apply_rewrite_audit(rewrite, audit)
    bullets = result["experience_section"][0]["bullets"]

    assert "Built Python APIs for internal tools." in bullets
    assert "Managed a team of 6 engineers across two squads." not in bullets


def test_rewrite_audit_removes_invented_project_bullet():
    rewrite = {
        "projects_section": [
            {
                "heading": "Analytics Dashboard",
                "bullets": [
                    "Built a dashboard with React and Python.",
                    "Delivered a 35% revenue uplift for enterprise customers.",
                ],
            }
        ],
        "additional_keywords_to_include": [],
    }
    audit = {
        "unsupported_claims": [
            {
                "claim": "Delivered a 35% revenue uplift for enterprise customers.",
                "source_section": "projects_section",
                "severity": "remove",
            }
        ]
    }

    result = main.apply_rewrite_audit(rewrite, audit)
    bullets = result["projects_section"][0]["bullets"]

    assert "Built a dashboard with React and Python." in bullets
    assert "Delivered a 35% revenue uplift for enterprise customers." not in bullets


def test_rewrite_audit_clears_fully_unsupported_summary():
    rewrite = {
        "rewritten_summary": "Certified Kubernetes administrator with 10 years of platform leadership.",
        "additional_keywords_to_include": [],
    }
    audit = {
        "unsupported_claims": [
            {
                "claim": "Certified Kubernetes administrator with 10 years of platform leadership.",
                "source_section": "summary",
                "severity": "remove",
            }
        ]
    }

    result = main.apply_rewrite_audit(rewrite, audit)

    assert result["rewritten_summary"] == ""


def test_rewrite_audit_keeps_unflagged_bullets():
    rewrite = {
        "experience_section": [
            {
                "heading": "Analyst | DataCo | 2024",
                "bullets": ["Automated SQL quality checks.", "Presented weekly data-quality reports."],
            }
        ],
        "additional_keywords_to_include": [],
    }

    result = main.apply_rewrite_audit(rewrite, {"unsupported_claims": []})

    assert result["experience_section"][0]["bullets"] == [
        "Automated SQL quality checks.",
        "Presented weekly data-quality reports.",
    ]


def test_no_invention_validator_removes_invented_tool_and_metric():
    rewrite = {
        "experience_section": [
            {
                "heading": "Developer | DevCo | 2024",
                "bullets": [
                    "Built Python APIs for internal reporting.",
                    "Deployed Kubernetes services and improved uptime by 35%.",
                ],
            }
        ],
        "additional_keywords_to_include": [],
        "missing_information": [],
    }
    resume_text = "Experience\nDeveloper, DevCo, 2024\nBuilt Python APIs for internal reporting."

    result = main.validate_rewrite_no_inventions(rewrite, resume_text)
    bullets = result["experience_section"][0]["bullets"]

    assert "Built Python APIs for internal reporting." in bullets
    assert "Deployed Kubernetes services and improved uptime by 35%." not in bullets
    assert any("Kubernetes" in item or "35%" in item for item in result["additional_keywords_to_include"])
    assert result["rewrite_audit"]["deterministic_removed_count"] == 1


def test_no_invention_validator_removes_editorial_tools_and_certifications():
    rewrite = {
        "skills_section": [
            {
                "category": "Editorial tools",
                "items": ["Copyediting", "CMS", "SEO", "Google Analytics", "Adobe InDesign", "QTS"],
            }
        ],
        "experience_section": [
            {
                "heading": "Communications Intern | Charity | 2025",
                "bullets": [
                    "Drafted and proofread weekly newsletters.",
                    "Improved engagement by 45% using Google Analytics and SEO.",
                ],
            }
        ],
        "additional_keywords_to_include": [],
        "missing_information": [],
    }
    resume_text = "Skills\nCopyediting, proofreading\nExperience\nDrafted and proofread weekly newsletters."

    result = main.validate_rewrite_no_inventions(rewrite, resume_text)
    skills = result["skills_section"][0]["items"]
    bullets = result["experience_section"][0]["bullets"]

    assert "Copyediting" in skills
    assert "CMS" not in skills
    assert "SEO" not in skills
    assert "Google Analytics" not in skills
    assert "Adobe InDesign" not in skills
    assert "QTS" not in skills
    assert "Drafted and proofread weekly newsletters." in bullets
    assert "Improved engagement by 45% using Google Analytics and SEO." not in bullets


def test_no_invention_validator_allows_metric_prompt_not_in_source():
    rewrite = {
        "experience_section": [
            {
                "heading": "Developer | DevCo | 2024",
                "bullets": ["Built Python APIs for internal reporting [METRIC: how many users or reports supported?]."],
            }
        ],
        "additional_keywords_to_include": [],
        "missing_information": [],
    }
    resume_text = "Experience\nDeveloper, DevCo, 2024\nBuilt Python APIs for internal reporting."

    result = main.validate_rewrite_no_inventions(rewrite, resume_text)

    assert result["experience_section"][0]["bullets"] == [
        "Built Python APIs for internal reporting [METRIC: how many users or reports supported?]."
    ]


def test_rewrite_packets_keep_jd_only_keywords_as_gap_suggestions():
    resume_text = "Skills\nPython\nExperience\nBuilt Python APIs."
    job_description = "Required: Python, Kubernetes, ACA qualification, stakeholder communication."

    packets = main.build_rewrite_evidence_packets(resume_text, job_description, {})

    allowed_norms = {main.normalize_phrase(item) for item in packets["shared"]["allowed_cv_terms"]}
    assert "python" in allowed_norms
    assert "kubernetes" not in allowed_norms
    gap_norms = {main.normalize_phrase(item) for item in packets["shared"]["jd_keywords_for_gap_list_only"]}
    assert "kubernetes" in gap_norms or "aca" in gap_norms


def test_normalize_rewrite_response_preserves_change_evidence_fields():
    payload = {
        "section_changes": [
            {
                "section": "experience",
                "label": "Developer bullet",
                "type": "optimised",
                "change": "Reframed the bullet around API delivery.",
                "original_text": "Built APIs.",
                "rewritten_text": "Built Python APIs for internal reporting.",
                "evidence_source": "Experience packet",
            }
        ]
    }

    result = main.normalize_rewrite_response(payload)
    change = result["section_changes"][0]

    assert change["original_text"] == "Built APIs."
    assert change["rewritten_text"] == "Built Python APIs for internal reporting."
    assert change["evidence_source"] == "Experience packet"


def test_sectional_rewrite_combines_sections_and_runs_validator(monkeypatch):
    def fake_gemini_json(prompt, max_output_tokens):
        if "ONLY the OVERVIEW section" in prompt:
            return {
                "name": "Hamza Abdi",
                "contact": {"email": "", "phone": "", "linkedin": "", "location": ""},
                "role_target": "Developer at ExampleCo",
                "diagnosis": {
                    "current_positioning": "Python API experience is present but not prominent.",
                    "target_positioning": "API delivery is foregrounded for the role.",
                    "key_gaps": [],
                },
                "rewritten_summary": "Python developer with experience building APIs. Eager to bring this expertise to the Developer role at ExampleCo.",
            }
        if "ONLY the SKILLS section" in prompt:
            return {
                "skills_section": [{"category": "Technical Skills", "items": ["Python", "Kubernetes"]}],
                "additional_keywords_to_include": ["Kubernetes (add only if accurate)"],
                "missing_information": [],
                "section_changes": [
                    {
                        "section": "skills",
                        "label": "Skills",
                        "type": "optimised",
                        "change": "Grouped evidenced technical skills.",
                        "original_text": "Python",
                        "rewritten_text": "Python",
                        "evidence_source": "skills packet",
                    }
                ],
            }
        if "ONLY the EXPERIENCE section" in prompt:
            return {
                "experience_section": [
                    {
                        "heading": "Developer | DevCo | 2024",
                        "bullets": [
                            "Built Python APIs for internal reporting.",
                            "Deployed Kubernetes services for 50 enterprise users.",
                        ],
                    }
                ],
                "missing_information": [],
                "section_changes": [],
            }
        if "ONLY the PROJECTS section" in prompt:
            return {"projects_section": [], "missing_information": [], "section_changes": []}
        return {}

    monkeypatch.setattr(main, "rewrite_json_with_gemini", fake_gemini_json)
    monkeypatch.setattr(main, "GENAI_CLIENT", None)
    resume_text = "Hamza Abdi\nSkills\nPython\nExperience\nDeveloper | DevCo | 2024\nBuilt Python APIs for internal reporting.\nEducation\nBSc Computer Science"
    job_description = "ExampleCo is hiring a Developer. Required: Python, Kubernetes."

    result = main.generate_sectional_cv_rewrite(resume_text, job_description, {}, provider="gemini")

    assert result["rewrite_pipeline"]["mode"] == "sectional"
    assert result["skills_section"][0]["items"] == ["Python"]
    assert result["experience_section"][0]["bullets"] == ["Built Python APIs for internal reporting."]
    assert any("Kubernetes" in item for item in result["additional_keywords_to_include"])
    assert result["section_changes"][0]["original_text"] == "Python"


def test_no_invention_validator_removes_terraform_and_ecs_claim():
    rewrite = {
        "experience_section": [
            {
                "heading": "Developer | DevCo | 2024",
                "bullets": ["Used Terraform to manage ECS services."],
            }
        ],
        "skills_section": [{"category": "Technical Skills", "items": ["Python", "Terraform"]}],
        "additional_keywords_to_include": [],
        "missing_information": [],
    }
    resume_text = "Skills\nPython\nExperience\nBuilt Python APIs."

    result = main.validate_rewrite_no_inventions(main.validate_rewrite_skills(rewrite, resume_text), resume_text)

    assert result["experience_section"][0]["bullets"] == []
    assert result["skills_section"][0]["items"] == ["Python"]
    assert any("Terraform" in item for item in result["additional_keywords_to_include"])
