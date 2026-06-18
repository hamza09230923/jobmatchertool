from fastapi.testclient import TestClient

import main


def test_analyze_empty_job_description_returns_controlled_400(monkeypatch):
    monkeypatch.setattr(
        main.auth_utils,
        "decode_jwt",
        lambda token: {"sub": "123", "email": "test@example.com"} if token else None,
    )
    monkeypatch.setattr(
        main.db,
        "get_user_by_id",
        lambda user_id: {
            "id": int(user_id),
            "email": "test@example.com",
            "tier": "paid",
            "email_verified": True,
            "lifetime_scans": 0,
        },
    )
    monkeypatch.setattr(main, "extract_pdf_text", lambda file_bytes: "Resume text")

    response = TestClient(main.app).post(
        "/analyze",
        files={"resume": ("resume.pdf", b"placeholder pdf bytes", "application/pdf")},
        data={"job_description": ""},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Job description is empty."
