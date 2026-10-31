"""Collect OCR lines from marked DOM nodes after a template render."""

from __future__ import annotations

from typing import Any

from watson_fixturegen.sidecar import OcrLine

OCR_JS = """() => {
  const nodes = document.querySelectorAll("[data-ocr]");
  const lines = [];
  for (const el of nodes) {
    const raw = (el.innerText || "").replace(/\\s+/g, " ").trim();
    if (!raw) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    lines.push({
      text: raw,
      bbox: [
        Math.round(r.x),
        Math.round(r.y),
        Math.round(r.width),
        Math.round(r.height),
      ],
      confidence: 1.0,
    });
  }
  return lines;
}"""


def normalize_ocr_lines(raw: Any) -> list[OcrLine]:
    if not isinstance(raw, list):
        raise TypeError("OCR evaluate() must return a list")
    lines: list[OcrLine] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("OCR line must be an object")
        text = str(item["text"]).strip()
        if not text:
            continue
        bbox_raw = item["bbox"]
        if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
            raise ValueError("OCR bbox must have four numbers")
        bbox = [int(value) for value in bbox_raw]
        lines.append({"text": text, "bbox": bbox, "confidence": 1.0})
    return lines
