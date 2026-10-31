"""Write a handful of tiny synthetic frames + OCR sidecars for harness smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

FRAMES = (
    {
        "frame_id": "f001_mail",
        "app_name": "Mail",
        "bundle_id": "com.example.mail",
        "window_title": "Re: Acme Q4 Proposal — Priya Natarajan",
        "bg": (232, 236, 244),
        "accent": (47, 92, 168),
        "lines": [
            "From: Priya Natarajan <priya@acme.example>",
            "Subject: Re: Acme Q4 Proposal",
            "Can we move the demo to 14 Nov?",
            "Please send revised pricing by Friday.",
        ],
    },
    {
        "frame_id": "f002_doc",
        "app_name": "Pages",
        "bundle_id": "com.example.docs",
        "window_title": "Internal Onboarding Guide — Section 3",
        "bg": (250, 248, 240),
        "accent": (120, 80, 40),
        "lines": [
            "Internal Onboarding Guide",
            "Section 3: Access and tools",
            "Status: published this morning",
            "Still editing: Acme proposal appendix",
        ],
    },
    {
        "frame_id": "f003_chat",
        "app_name": "Chat",
        "bundle_id": "com.example.chat",
        "window_title": "Delivery — Tom Ashworth",
        "bg": (236, 244, 236),
        "accent": (36, 120, 72),
        "lines": [
            "Tom Ashworth: agreed, 14 Nov works",
            "You: I will send the revised deck",
            "Tom Ashworth: thanks — flag risks in Q4",
        ],
    },
    {
        "frame_id": "f004_ide",
        "app_name": "IDE",
        "bundle_id": "com.example.ide",
        "window_title": "watson/pipeline/extraction.py",
        "bg": (28, 30, 36),
        "accent": (200, 200, 210),
        "lines": [
            "def extract_frame(path):",
            "    sidecar = load_ocr(path)",
            "    return Frame(text=sidecar.text)",
        ],
    },
    {
        "frame_id": "f005_slides",
        "app_name": "Keynote",
        "bundle_id": "com.example.slides",
        "window_title": "Acme Q4 Demo outline",
        "bg": (244, 232, 232),
        "accent": (160, 40, 40),
        "lines": [
            "Slide 4 — Risks",
            "Timeline: demo 14 Nov",
            "Owner: Delivery",
            "(visually dense placeholder)",
        ],
    },
)


def generate(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for spec in FRAMES:
        png = directory / f"{spec['frame_id']}.png"
        sidecar = directory / f"{spec['frame_id']}.json"
        _draw(png, spec)
        sidecar.write_text(json.dumps(_sidecar(spec), indent=2) + "\n", encoding="utf-8")
        written.extend([png, sidecar])
    return written


def _draw(path: Path, spec: dict) -> None:
    image = Image.new("RGB", (320, 180), spec["bg"])
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 319, 28), fill=spec["accent"])
    title = str(spec["window_title"])[:42]
    ink = (250, 250, 250)
    body = (20, 20, 20) if sum(spec["bg"]) > 300 else (230, 230, 230)
    draw.text((8, 8), f"{spec['app_name']}  {title}", fill=ink)
    y = 44
    for line in spec["lines"]:
        draw.text((12, y), str(line), fill=body)
        y += 22
    image.save(path, format="PNG", optimize=True)


def _sidecar(spec: dict) -> dict:
    lines = [
        {
            "text": line,
            "bbox": [12, 44 + index * 22, 300, 62 + index * 22],
            "confidence": 0.98,
        }
        for index, line in enumerate(spec["lines"])
    ]
    return {
        "frame_id": spec["frame_id"],
        "captured_at": "2026-10-31T09:15:00+00:00",
        "bundle_id": spec["bundle_id"],
        "app_name": spec["app_name"],
        "window_title": spec["window_title"],
        "url": None,
        "ocr": {"lines": lines},
        "width": 320,
        "height": 180,
        "capture_mode": "frontmost",
    }


def main() -> int:
    target = Path(__file__).resolve().parent / "frames"
    paths = generate(target)
    print(f"wrote {len(paths)} files under {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
