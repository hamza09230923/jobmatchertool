from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_root_has_no_live_tmp_artifacts():
    tmp_artifacts = sorted(
        path.name
        for path in REPO_ROOT.glob("tmp_*")
        if path.is_file()
    )

    assert tmp_artifacts == []


def test_gitignore_blocks_live_tmp_artifacts():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "tmp_live_*.json" in gitignore
    assert "tmp_*" in gitignore
