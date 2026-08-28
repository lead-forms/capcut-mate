"""Automatic foreign burned-caption detection using sampled OCR frames."""
from __future__ import annotations

import csv
import io
import json
import subprocess
import tempfile
from pathlib import Path


def _duration(path: Path) -> float:
    return float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ], text=True).strip())


def analyze(path: Path, confidence_floor: float = 35) -> dict:
    total = _duration(path)
    sample_times = [x for x in (0.5, 2, 4, 6, 9, 12) if x < total]
    hits = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, timestamp in enumerate(sample_times):
            frame = Path(tmp) / f"frame_{index}.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(timestamp),
                            "-i", str(path), "-frames:v", "1", str(frame)], check=True)
            ocr = subprocess.run(["tesseract", str(frame), "stdout", "-l", "eng",
                                  "--psm", "11", "tsv"], capture_output=True,
                                 text=True, check=False)
            words = []
            for row in csv.DictReader(io.StringIO(ocr.stdout), delimiter="\t"):
                try:
                    confidence, top = float(row.get("conf") or -1), int(row.get("top") or 0)
                except ValueError:
                    continue
                text = (row.get("text") or "").strip()
                if confidence >= confidence_floor and len(text) >= 2 and top < 650:
                    words.append(text)
            if words:
                hits.append({"time": timestamp, "text": " ".join(words)})
    ratio = len(hits) / max(1, len(sample_times))
    detected = ratio >= 0.34
    return {"asset": path.name, "foreign_subtitle_detected": detected,
            "hit_ratio": round(ratio, 3), "hits": hits,
            "strategy": "white_label_cover" if detected else "none"}


def write_report(inputs: list[Path], output: Path) -> None:
    output.write_text(json.dumps([analyze(p) for p in inputs], ensure_ascii=False,
                                 indent=2), encoding="utf-8")
