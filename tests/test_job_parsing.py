import main


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
