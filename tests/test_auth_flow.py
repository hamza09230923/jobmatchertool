from fastapi.testclient import TestClient

import db
import email_service
import main
import rate_limit


def test_signup_credentials_can_login_again(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(email_service, "send_verification_email", lambda *_args, **_kwargs: True)
    rate_limit.auth_limiter._buckets.clear()
    db.init_db()

    client = TestClient(main.app)
    credentials = {
        "email": "New.User+Auth@Example.com",
        "password": "correct horse battery staple",
    }

    signup = client.post("/auth/signup", json=credentials, headers={"x-forwarded-for": "203.0.113.10"})
    assert signup.status_code == 200
    assert signup.json()["user"]["email"] == "new.user+auth@example.com"

    login = client.post(
        "/auth/login",
        json={"email": "NEW.USER+AUTH@example.com", "password": credentials["password"]},
        headers={"x-forwarded-for": "203.0.113.11"},
    )
    assert login.status_code == 200
    assert login.json()["token"]
    assert login.json()["user"]["email"] == "new.user+auth@example.com"


def test_signup_password_does_not_accept_near_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(email_service, "send_verification_email", lambda *_args, **_kwargs: True)
    rate_limit.auth_limiter._buckets.clear()
    db.init_db()

    client = TestClient(main.app)
    client.post(
        "/auth/signup",
        json={"email": "near-miss@example.com", "password": "correct horse battery staple"},
        headers={"x-forwarded-for": "203.0.113.12"},
    )

    login = client.post(
        "/auth/login",
        json={"email": "near-miss@example.com", "password": "correct horse battery staple "},
        headers={"x-forwarded-for": "203.0.113.13"},
    )
    assert login.status_code == 401
