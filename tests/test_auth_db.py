import auth_utils
import db


def test_default_db_path_uses_render_disk_when_available(monkeypatch, tmp_path):
    fake_var_data = tmp_path / "var" / "data"
    fake_var_data.mkdir(parents=True)
    pathlib_path = db.Path
    monkeypatch.delenv("USERS_DB", raising=False)
    monkeypatch.setattr(
        db,
        "Path",
        lambda value: fake_var_data if value == "/var/data" else pathlib_path(value),
    )

    assert db.resolve_db_path() == fake_var_data / "users.db"


def test_configured_users_db_overrides_render_disk(monkeypatch, tmp_path):
    configured = tmp_path / "configured" / "users.db"
    monkeypatch.setenv("USERS_DB", str(configured))

    assert db.resolve_db_path() == configured


def test_user_password_hash_persists_in_configured_db_path(tmp_path, monkeypatch):
    db_path = tmp_path / "nested" / "users.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    db.init_db()
    password_hash = auth_utils.hash_password("correct horse battery staple")
    created = db.create_user("person@example.com", password_hash)
    loaded = db.get_user_by_email("PERSON@example.com")

    assert db_path.exists()
    assert loaded["id"] == created["id"]
    assert auth_utils.verify_password("correct horse battery staple", loaded["password_hash"])
