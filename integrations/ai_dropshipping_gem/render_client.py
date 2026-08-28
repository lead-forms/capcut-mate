"""Controller for a Windows CapCut Mate/Jianying rendering node."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path


class RenderBlocked(RuntimeError):
    """The draft is valid but the Windows renderer or API auth is unavailable."""


class CapCutMateRenderClient:
    """Run create/edit/gen_video/status/video_url without an FFmpeg export path."""

    def __init__(self, base_url: str, api_key: str | None = None,
                 timeout: int = 180):
        self.base = base_url.rstrip("/") + "/openapi/capcut-mate/v1"
        self.api_key = api_key
        self.timeout = timeout

    def _post(self, endpoint: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base}/{endpoint}",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode())
        if result.get("code") not in (None, 0):
            raise RenderBlocked(f"{endpoint}: {result.get('message', result)}")
        return result

    def build_draft(self, recipe: dict) -> dict:
        """Create a vertical draft and apply clips, captions, and zoom keyframes."""
        draft_url = self._post("create_draft", {"width": 1080, "height": 1920})["draft_url"]
        videos = self._post("add_videos", {
            "draft_url": draft_url,
            "video_infos": json.dumps(recipe["video_infos"], ensure_ascii=False),
            "scene_timelines": recipe.get("scene_timelines"),
            "alpha": 1.0, "scale_x": 1.0, "scale_y": 1.0,
            "transform_x": 0, "transform_y": 0,
        })
        segment_ids = videos.get("segment_ids", [])
        if recipe.get("captions"):
            self._post("add_captions", {
                "draft_url": draft_url,
                "captions": json.dumps(recipe["captions"], ensure_ascii=False),
                "text_color": recipe.get("text_color", "#ffffff"),
                "border_color": recipe.get("border_color", "#000000"),
                "alignment": 1, "font": recipe.get("font"),
                "font_size": recipe.get("font_size", 15),
                "transform_y": recipe.get("transform_y", -560),
                "bold": True,
            })
        keyframes = []
        for index, segment_id in enumerate(segment_ids):
            values = recipe.get("zoom", {}).get(str(index), [1.02, 1.08])
            clip = recipe["video_infos"][index]
            length = clip["end"] - clip["start"]
            keyframes.extend([
                {"segment_id": segment_id, "property": "KFTypeScaleX",
                 "offset": 0, "value": values[0]},
                {"segment_id": segment_id, "property": "KFTypeScaleX",
                 "offset": max(1, length - 1), "value": values[1]},
            ])
        if keyframes:
            self._post("add_keyframes", {"draft_url": draft_url,
                                         "keyframes": json.dumps(keyframes)})
        self._post("save_draft", {"draft_url": draft_url})
        return {"draft_url": draft_url, "segment_ids": segment_ids,
                "status": "draft_ready"}

    def render(self, draft_url: str, output: Path, poll_seconds: int = 5,
               max_polls: int = 240) -> dict:
        """Call gen_video, poll gen_video_status, and download video_url."""
        payload = {"draft_url": draft_url}
        if self.api_key:
            payload["apiKey"] = self.api_key
        self._post("gen_video", payload)
        for _ in range(max_polls):
            status = self._post("gen_video_status", {"draft_url": draft_url})
            if status.get("status") == "completed" and status.get("video_url"):
                output.parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(status["video_url"], output)
                status["local_output"] = str(output)
                return status
            if status.get("status") == "failed":
                raise RenderBlocked(status.get("error_message") or str(status))
            time.sleep(poll_seconds)
        raise RenderBlocked("Windows render node timed out")
