import json

from integrations.ai_dropshipping_gem.render_client import CapCutMateRenderClient


def test_client_uses_capcut_mate_v1_path():
    client = CapCutMateRenderClient("http://render-node:30000/")
    assert client.base == "http://render-node:30000/openapi/capcut-mate/v1"


def test_recipe_video_infos_are_json_serializable():
    recipe = {"video_infos": [{"video_url": "https://example.test/a.mp4",
                               "start": 0, "end": 1_000_000}]}
    assert json.loads(json.dumps(recipe))["video_infos"][0]["end"] == 1_000_000
