from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_render_blueprint_targets_live_frontend_backend():
    render_yaml = (ROOT / "render.yaml").read_text(encoding="utf-8")
    api_config = (ROOT / "web" / "src" / "apiConfig.js").read_text(encoding="utf-8")
    index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert "name: jobmatchertool" in render_yaml
    assert "https://jobmatchertool.onrender.com" in api_config
    assert "https://jobmatchertool.onrender.com/status" in index_html


def test_render_blueprint_persists_user_and_usage_state_on_disk():
    render_yaml = (ROOT / "render.yaml").read_text(encoding="utf-8")
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    db_py = (ROOT / "db.py").read_text(encoding="utf-8")

    assert "mountPath: /var/data" in render_yaml
    assert "USERS_DB" in render_yaml
    assert "value: /var/data/users.db" in render_yaml
    assert 'render_disk = Path("/var/data")' in db_py
    assert '"users_db": db.status_metadata()' in main_py
    assert "SCAN_COUNTS_FILE" in render_yaml
    assert "value: /var/data/scan_counts.json" in render_yaml
    assert "FEEDBACK_FILE" in render_yaml
    assert "value: /var/data/feedback.json" in render_yaml
