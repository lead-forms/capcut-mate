import importlib
import os


def test_server_defaults_are_localhost(monkeypatch):
    monkeypatch.delenv("SERVER_HOST", raising=False)
    monkeypatch.delenv("SERVER_PORT", raising=False)

    import config

    reloaded = importlib.reload(config)
    assert reloaded.SERVER_HOST == "127.0.0.1"
    assert reloaded.SERVER_PORT == 30000


def test_default_draft_path_uses_local_app_data(monkeypatch, tmp_path):
    monkeypatch.delenv("DRAFT_SAVE_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(tmp_path))

    import config

    reloaded = importlib.reload(config)
    assert reloaded.DRAFT_SAVE_PATH == os.path.join(
        os.fspath(tmp_path),
        "JianyingPro",
        "User Data",
        "Projects",
        "com.lveditor.draft",
    )
