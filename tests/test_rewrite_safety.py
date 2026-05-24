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
