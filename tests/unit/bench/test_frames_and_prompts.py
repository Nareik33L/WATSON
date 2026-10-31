from __future__ import annotations

from watson_bench.frames import load_frame_id_list, load_frames
from watson_bench.paths import samples_dir
from watson_bench.prompts import parse_json_object, understanding_prompt, vlm_prompt


def test_load_committed_sample_frames() -> None:
    frames = load_frames(samples_dir())
    assert len(frames) == 5
    mail = next(frame for frame in frames if frame.frame_id == "f001_mail")
    assert mail.app_name == "Mail"
    assert "Priya" in mail.ocr_text
    assert mail.image_path.suffix == ".png"


def test_load_frames_honours_frame_id_list(tmp_path) -> None:
    listing = tmp_path / "sample50.txt"
    listing.write_text("# header\nf001_mail\nf003_chat\n", encoding="utf-8")
    frames = load_frames(samples_dir(), frame_ids=load_frame_id_list(listing))
    assert [frame.frame_id for frame in frames] == ["f001_mail", "f003_chat"]


def test_prompts_and_json_fence_parsing() -> None:
    assert "schema" in vlm_prompt()
    frames = load_frames(samples_dir())
    text = understanding_prompt(frames[0])
    assert "ocr_text" in text
    parsed = parse_json_object('```json\n{"visible_text": "hi", "extra": 1}\n```')
    assert parsed["visible_text"] == "hi"
