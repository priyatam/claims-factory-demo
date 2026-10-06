"""Download 100 labeled damage photos and write evals/history.

Source: Car Front and Rear Damage Detection (DrBimmer/comprehensive-car-damage).
License: MIT, as stated on the dataset card.
https://huggingface.co/datasets/DrBimmer/comprehensive-car-damage

The dataset has no make, model, or colour. Those stay null. Damage text comes
from the class label. Dollar amounts are synthetic kept ranges.
"""

from __future__ import annotations

import json
import random
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent / "history"
IMAGES = ROOT / "images"
API = "https://huggingface.co/api/datasets/DrBimmer/comprehensive-car-damage/tree/main/"
RESOLVE = "https://huggingface.co/datasets/DrBimmer/comprehensive-car-damage/resolve/main/"

CLASSES = {
    "F_Breakage": {
        "summary": "front view, broken parts visible",
        "severity": "moderate",
        "parts": ["front"],
    },
    "F_Crushed": {
        "summary": "front view, visibly crushed",
        "severity": "severe",
        "parts": ["front"],
    },
    "R_Breakage": {
        "summary": "rear view, broken parts visible",
        "severity": "moderate",
        "parts": ["rear"],
    },
    "R_Crushed": {
        "summary": "rear view, visibly crushed",
        "severity": "severe",
        "parts": ["rear"],
    },
}

PER_CLASS = 25
BAD_RANGE = {8, 21, 34, 47, 60, 73, 86, 99}


def list_jpegs(folder: str) -> list[str]:
    url = API + folder
    request = urllib.request.Request(url, headers={"User-Agent": "strands-agent-demo-evals"})
    with urllib.request.urlopen(request, timeout=60) as response:
        entries = json.load(response)
    names = [item["path"] for item in entries if item["path"].lower().endswith(".jpg")]

    def number(path: str) -> int:
        return int(Path(path).stem.split("_")[-1])

    names.sort(key=number)
    return names[:PER_CLASS]


def download(source: str, dest: Path) -> None:
    url = RESOLVE + source
    request = urllib.request.Request(url, headers={"User-Agent": "strands-agent-demo-evals"})
    with urllib.request.urlopen(request, timeout=60) as response:
        dest.write_bytes(response.read())


def resize(src: Path, dest: Path) -> None:
    with Image.open(src) as image:
        image = image.convert("RGB")
        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
        image.save(dest, format="JPEG", quality=70, optimize=True)


def dollars(severity: str, rng: random.Random) -> tuple[int, int, int]:
    if severity == "moderate":
        low = rng.randint(400, 1200)
        high = low + rng.randint(400, 1400)
    else:
        low = rng.randint(2200, 5000)
        high = low + rng.randint(1500, 4500)
    kept = rng.randint(low + 1, high - 1)
    return low, high, kept


def build_rows(sources: list[tuple[str, dict]]) -> list[dict]:
    rng = random.Random(404)
    rows = []
    for index, (source, label) in enumerate(sources):
        low, high, kept = dollars(label["severity"], rng)
        failure_mode = None
        decoy_low = None
        decoy_high = None
        claim_label = "accept"
        if index in BAD_RANGE:
            failure_mode = "bad_usable_range"
            claim_label = "correct"
            decoy_low = high + rng.randint(80, 250)
            decoy_high = decoy_low + rng.randint(200, 600)
        filename = Path(source).name
        rows.append(
            {
                "claim_id": f"SYN{index + 1:04d}",
                "plate": None,
                "make": None,
                "model": None,
                "colour": None,
                "damage_summary": label["summary"],
                "severity": label["severity"],
                "parts": label["parts"],
                "estimate_low": low,
                "estimate_high": high,
                "currency": "USD",
                "kept_dollars": kept,
                "label": claim_label,
                "image": filename,
                "dataset_label": source.split("/", 1)[0],
                "source_file": source,
                "expected_status": "ok",
                "false_ok_case": False,
                "failure_mode": failure_mode,
                "decoy_low": decoy_low,
                "decoy_high": decoy_high,
                "agent_make": None,
                "agent_model": None,
            }
        )
    return rows


def main() -> None:
    sources = [(name, CLASSES[folder]) for folder in CLASSES for name in list_jpegs(folder)]
    if len(sources) != 100:
        raise SystemExit(f"expected 100 images, got {len(sources)}")
    scratch = ROOT / "_download"
    if scratch.exists():
        shutil.rmtree(scratch)
    raw = scratch / "raw"
    resized = scratch / "resized"
    raw.mkdir(parents=True)
    resized.mkdir(parents=True)

    def fetch(item: tuple[str, dict]) -> None:
        source, _label = item
        download(source, raw / Path(source).name)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, sources))
    for source, _label in sources:
        name = Path(source).name
        resize(raw / name, resized / name)

    rows = build_rows(sources)
    IMAGES.mkdir(parents=True, exist_ok=True)
    for old in IMAGES.iterdir():
        if old.is_file():
            old.unlink()
    for source, _label in sources:
        name = Path(source).name
        shutil.move(resized / name, IMAGES / name)
    (ROOT / "claims.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    shutil.rmtree(scratch)
    sizes = [(IMAGES / row["image"]).stat().st_size for row in rows]
    print(f"wrote {len(rows)} claims, images {min(sizes)}-{max(sizes)} bytes")


if __name__ == "__main__":
    main()
