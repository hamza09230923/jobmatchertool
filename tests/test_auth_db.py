import auth_utils
import db


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
