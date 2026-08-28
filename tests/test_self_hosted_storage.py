from pathlib import Path
from unittest.mock import patch

import config
from src.utils.upload_file import upload_file


def test_local_storage_returns_self_hosted_url(tmp_path: Path):
    output = tmp_path / "output"
    source = tmp_path / "render.mp4"
    source.write_bytes(b"capcut-render")
    with (
        patch.object(config, "STORAGE_BACKEND", "local"),
        patch.object(config, "OUTPUT_DIR", str(output)),
        patch.object(config, "LOCAL_STORAGE_DIR", str(output / "rendered")),
        patch.object(config, "SELF_HOST_BASE_URL", "https://render.internal"),
    ):
        url = upload_file(str(source))
    assert url.startswith("https://render.internal/files/rendered/")
    copied = output / url.split("/files/", 1)[1]
    assert copied.read_bytes() == b"capcut-render"


def test_self_host_defaults_do_not_use_jcaigc():
    assert "jcaigc.cn" not in config.DRAFT_URL
    assert "jcaigc.cn" not in config.DOWNLOAD_URL
    assert config.ENABLE_APIKEY is False
