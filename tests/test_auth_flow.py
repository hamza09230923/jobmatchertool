from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import auth_utils
import db
import email_service
import main
import rate_limit


def auth_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(email_service, "send_verification_email", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(email_service, "send_password_reset_email", lambda *_args, **_kwargs: True)
    rate_limit.auth_limiter._buckets.clear()
    db.init_db()
    return TestClient(main.app)


def test_signup_credentials_can_login_again(tmp_path, monkeypatch):
    client = auth_client(tmp_path, monkeypatch)
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
    client = auth_client(tmp_path, monkeypatch)
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


def test_duplicate_signup_returns_conflict(tmp_path, monkeypatch):
    client = auth_client(tmp_path, monkeypatch)
    credentials = {"email": "dupe@example.com", "password": "correct horse battery staple"}

    first = client.post("/auth/signup", json=credentials, headers={"x-forwarded-for": "203.0.113.14"})
    second = client.post("/auth/signup", json=credentials, headers={"x-forwarded-for": "203.0.113.15"})

    assert first.status_code == 200
    assert second.status_code == 409


def test_auth_me_rejects_missing_and_invalid_tokens(tmp_path, monkeypatch):
    client = auth_client(tmp_path, monkeypatch)

    missing = client.get("/auth/me")
    invalid = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_password_reset_invalidates_old_password_and_token(tmp_path, monkeypatch):
    client = auth_client(tmp_path, monkeypatch)
    email = "reset@example.com"
    old_password = "correct horse battery staple"
    new_password = "new correct horse battery staple"
    signup = client.post(
        "/auth/signup",
        json={"email": email, "password": old_password},
        headers={"x-forwarded-for": "203.0.113.16"},
    )
    assert signup.status_code == 200

    user = db.get_user_by_email(email)
    reset_token = auth_utils.generate_secure_token()
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    db.set_password_reset_token(user["id"], reset_token, expires)

    reset = client.post("/auth/reset-password", json={"token": reset_token, "password": new_password})
    old_login = client.post(
        "/auth/login",
        json={"email": email, "password": old_password},
        headers={"x-forwarded-for": "203.0.113.17"},
    )
    new_login = client.post(
        "/auth/login",
        json={"email": email, "password": new_password},
        headers={"x-forwarded-for": "203.0.113.18"},
    )
    reused = client.post("/auth/reset-password", json={"token": reset_token, "password": "another password"})

    assert reset.status_code == 200
    assert old_login.status_code == 401
    assert new_login.status_code == 200
    assert reused.status_code == 400


def test_delete_account_requires_password_and_prevents_future_login(tmp_path, monkeypatch):
    client = auth_client(tmp_path, monkeypatch)
    email = "delete-me@example.com"
    password = "correct horse battery staple"
    signup = client.post(
        "/auth/signup",
        json={"email": email, "password": password},
        headers={"x-forwarded-for": "203.0.113.19"},
    )
    token = signup.json()["token"]

    wrong = client.post(
        "/auth/delete-account",
        json={"password": "wrong password"},
        headers={"Authorization": f"Bearer {token}"},
    )
    deleted = client.post(
        "/auth/delete-account",
        json={"password": password},
        headers={"Authorization": f"Bearer {token}"},
    )
    login = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers={"x-forwarded-for": "203.0.113.20"},
    )

    assert wrong.status_code == 401
    assert deleted.status_code == 200
    assert login.status_code == 401
