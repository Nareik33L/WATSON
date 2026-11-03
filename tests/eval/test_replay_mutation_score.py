"""Operator-mutation score for eval replay. Target ≥ 26 of 30 mutants.

No-ops (replacement missing or source unchanged) and collection errors
count as SURVIVED, not killed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TARGET_MUTANTS = 30
KILL_FLOOR = 26
TEST_PATHS = (
    "tests/unit/test_eval_recordings.py::test_record_stores_full_prompt_hash_and_params",
    "tests/unit/test_eval_recordings.py::test_prompt_hash_includes_roles_and_params",
    "tests/unit/test_eval_recordings.py::test_legacy_exhausted_rows_are_a_miss",
    "tests/unit/test_eval_recordings.py::test_legacy_window_is_not_consumed",
    "tests/unit/test_eval_recordings.py::test_legacy_window_match_is_casefold",
    "tests/unit/test_eval_recordings.py::test_legacy_content_key_reuse_does_not_require_unique_count",
    "tests/unit/test_eval_recordings.py::test_prompt_hash_uses_every_message_not_just_the_first",
    "tests/unit/test_eval_recordings.py::test_prompt_hash_run_id_ordinals_follow_appearance_not_sort",
    "tests/unit/test_eval_recordings.py::test_prompt_hash_keeps_claim_prompt_order",
    "tests/unit/test_eval_recordings.py::test_prompt_hash_repeated_ids_keep_distinct_ordinals",
    "tests/unit/test_consolidation.py::test_load_claims_tied_created_at_orders_kind_assertion_text",
    "tests/unit/test_eval_recordings.py::test_prompt_hash_leaves_fixture_uuid",
    "tests/unit/test_eval_recordings.py::test_prompt_hash_leaves_fixture_timestamp",
    "tests/unit/test_eval_recordings.py::test_replay_maps_ordinal_cites_to_prompt_order_not_sorted",
    "tests/unit/test_eval_recordings.py::test_replay_uses_recorded_model_when_inner_is_missing",
    "tests/unit/test_eval_recordings.py::test_legacy_matches_by_prompt_date_without_set_day",
    "tests/unit/test_eval_recordings.py::test_new_format_rejects_different_model",
    "tests/unit/test_eval_recordings.py::test_miss_increments_count_logs_and_sets_last_miss",
    "tests/unit/test_eval_recordings.py::test_lenient_miss_count_fails_pipeline_without_exception",
    "tests/unit/test_eval_recordings.py::test_prompt_hash_leaves_unregistered_iso_date",
    "tests/unit/test_eval_recordings.py::test_prompt_hash_strips_registered_run_stamps",
    "tests/unit/test_eval_recordings.py::test_extract_does_not_consume_window_row",
    "tests/unit/test_eval_recordings.py::test_consolidation_structured_reraises_replay_miss",
    "tests/unit/test_eval_recordings.py::test_understanding_does_not_consume_extract_row",
    "tests/unit/test_eval_recordings.py::test_new_format_does_not_use_legacy_lookup",
    "tests/unit/test_eval_recordings.py::test_new_format_rejects_temperature_mismatch_without_inner",
    "tests/unit/test_eval_recordings.py::test_build_eval_runtime_passes_expected_temperature",
    "tests/unit/test_eval_recordings.py::test_legacy_recorded_model_is_unverified_not_config",
    "tests/unit/test_eval_recordings.py::test_restore_does_not_rewrite_rank_string_ordinal",
    "tests/unit/test_eval_recordings.py::test_cite_ordinals_ignore_preamble_run_id",
    "tests/unit/test_understanding.py::test_understand_segment_reraises_replay_miss",
    "tests/unit/test_cli.py::test_eval_cli_exits_1_on_temperature_mismatch",
)
_COLLECTED = re.compile(r"(\d+) tests? collected")
_RAN = re.compile(r"(\d+) (passed|failed)")

RECORDINGS = "src/watson/intelligence/eval/recordings.py"
RUN = "src/watson/intelligence/eval/run.py"
UNDERSTANDING = "src/watson/pipeline/understanding.py"
CONSOLIDATION = "src/watson/pipeline/consolidation.py"
DB = "src/watson/memory/db.py"

# One named replacement each. First two are the QA @37f4931 survivors.
MUTANTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "load-claims-id-only",
        CONSOLIDATION,
        "        ORDER BY created_at, kind, assertion, text, id\n",
        "        ORDER BY created_at, id\n",
    ),
    (
        "ordinals-const",
        RECORDINGS,
        '        mapping[token] = str(len(mapping) + 1)\n    return mapping\n',
        '        mapping[token] = "1"\n    return mapping\n',
    ),
    (
        "understand-swallow",
        UNDERSTANDING,
        '        if exc.__class__.__name__ == "ReplayMiss":\n            raise\n',
        "        if False:\n            raise\n",
    ),
    (
        "miss-count-inc",
        RECORDINGS,
        "        self.miss_count += 1\n",
        "        pass\n",
    ),
    (
        "miss-count-gate",
        RUN,
        "        if recorder is not None and recorder.miss_count and not failure_reason:\n",
        "        if False:\n",
    ),
    (
        "no-miss-log",
        RECORDINGS,
        '        log.error("%s", detail)\n',
        "        pass\n",
    ),
    (
        "no-last-miss",
        RECORDINGS,
        '        self.last_miss = f"ReplayMiss: {detail}"\n',
        "        self.last_miss = None\n",
    ),
    (
        "all-uuids",
        RECORDINGS,
        (
            "        if token.casefold() not in known or token in mapping:\n"
            "            continue\n"
            "        mapping[token] = str(len(mapping) + 1)\n"
            "    return mapping\n"
        ),
        (
            "        if token in mapping:\n"
            "            continue\n"
            "        mapping[token] = str(len(mapping) + 1)\n"
            "    return mapping\n"
        ),
    ),
    (
        "all-stamps",
        RECORDINGS,
        (
            '        return "<ts>" if token in known_stamps else token\n'
            "\n"
            "    text = _RUN_UUID.sub(replace_id, text)\n"
            "    if known_stamps:\n"
            "        text = _RUN_STAMP.sub(replace_stamp, text)\n"
            "    return text\n"
        ),
        (
            '        return "<ts>"\n'
            "\n"
            "    text = _RUN_UUID.sub(replace_id, text)\n"
            "    text = _RUN_STAMP.sub(replace_stamp, text)\n"
            "    return text\n"
        ),
    ),
    (
        "overbroad-dates",
        RECORDINGS,
        (
            "    text = _RUN_UUID.sub(replace_id, text)\n"
            "    if known_stamps:\n"
            "        text = _RUN_STAMP.sub(replace_stamp, text)\n"
            "    return text\n"
        ),
        (
            "    text = _RUN_UUID.sub(replace_id, text)\n"
            '    text = _ISO_DATE.sub("<d>", text)\n'
            "    if known_stamps:\n"
            "        text = _RUN_STAMP.sub(replace_stamp, text)\n"
            "    return text\n"
        ),
    ),
    (
        "stamps-unregistered",
        DB,
        "    note_stamp(stamp)\n",
        "    pass\n",
    ),
    (
        "understand-kind-any",
        RECORDINGS,
        '    if kind == "understanding":\n        return _is_window_key(needle)\n',
        '    if kind == "understanding":\n        return True\n',
    ),
    (
        "legacy-on-new",
        RECORDINGS,
        "        if not _is_new_format(self._recordings):\n",
        "        if True:\n",
    ),
    (
        "no-temp-reject",
        RECORDINGS,
        (
            "        if rec_temp is not None and configured_temp is not None"
            " and configured_temp != rec_temp:\n"
        ),
        (
            "        if False and rec_temp is not None and configured_temp is not None"
            " and configured_temp != rec_temp:\n"
        ),
    ),
    (
        "recorded-model-config",
        RECORDINGS,
        (
            "        if recorded:\n"
            "            return recorded\n"
            "        if not self._is_new_format():\n"
            '            return "legacy (unverified)"\n'
            "        return self._configured_model()\n"
        ),
        (
            "        if recorded:\n"
            "            return recorded\n"
            "        return self._configured_model()\n"
        ),
    ),
    (
        "restore-cites-only",
        RECORDINGS,
        "        if as_id and value in inverse:\n            return inverse[value]\n",
        "        if False and as_id and value in inverse:\n            return inverse[value]\n",
    ),
    (
        "match-first-80",
        RECORDINGS,
        '                "match": digest,\n',
        '                "match": next(iter(messages))["content"][:80] if messages else digest,\n',
    ),
    (
        "exhausted-reuse",
        RECORDINGS,
        (
            "        if (prompt_day or prompt_date) and other_days:\n"
            "            # This day has no row. Never hand out another day's last extract.\n"
            "            return None\n"
        ),
        (
            "        if (prompt_day or prompt_date) and other_days:\n"
            '            return dict(other_days[-1][1]["response"])\n'
        ),
    ),
    (
        "window-consume",
        RECORDINGS,
        (
            "        if window:\n"
            "            # Understand: first window-title hit. Never consume (revisits).\n"
            '            return dict(hits[0][1]["response"])\n'
        ),
        (
            "        if window:\n"
            "            row = hits[0][1]\n"
            "            if row in rows:\n"
            "                rows.remove(row)\n"
            '            return dict(row["response"])\n'
        ),
    ),
    (
        "no-casefold",
        RECORDINGS,
        (
            "            if needle.casefold() not in haystack.casefold()"
            " or not isinstance(response, dict):\n"
        ),
        "            if needle not in haystack or not isinstance(response, dict):\n",
    ),
    (
        "unique-count-cluster",
        RECORDINGS,
        "            and not _row_date(hit[1])\n            and _key_matches_kind(hit[2], kind)\n",
        (
            "            and not _row_date(hit[1])\n"
            "            and _legacy_key_count(rows, hit[2]) == 1\n"
            "            and _key_matches_kind(hit[2], kind)\n"
        ),
    ),
    (
        "hash-first-message",
        RECORDINGS,
        '    turns = _turns(messages)\n    blob = "\\n".join(item["content"] for item in turns)\n',
        (
            "    turns = _turns(messages)[:1]\n"
            '    blob = "\\n".join(item["content"] for item in turns)\n'
        ),
    ),
    (
        "sort-then-cite",
        RECORDINGS,
        "    for match in _RUN_UUID.finditer(blob):\n        token = match.group(0)\n",
        (
            "    for match in sorted(_RUN_UUID.finditer(blob),"
            " key=lambda item: item.group(0)):\n"
            "        token = match.group(0)\n"
        ),
    ),
    (
        "model-const",
        RECORDINGS,
        "        return self._configured_model() or self._recorded_model()\n",
        '        return ""\n',
    ),
    (
        "run-no-set-day",
        RECORDINGS,
        "        self._day = name or None\n        self._date = date or None\n",
        "        return\n",
    ),
    (
        "consolidate-swallow",
        CONSOLIDATION,
        '        if exc.__class__.__name__ == "ReplayMiss":\n            raise\n',
        "        if False:\n            raise\n",
    ),
    (
        "extract-kind-any",
        RECORDINGS,
        '    if kind == "day_extract":\n        return (\n',
        '    if kind == "day_extract":\n        return True or (\n',
    ),
    (
        "hash-skip-temp",
        RECORDINGS,
        '        "temperature": float(temperature),\n',
        '        "temperature": 0.0,\n',
    ),
    (
        "hash-skip-model",
        RECORDINGS,
        '        "model": model or "",\n',
        '        "model": "",\n',
    ),
    (
        "hash-skip-schema",
        RECORDINGS,
        (
            '        "num_predict": num_predict,\n'
            '        "schema": _canonical(schema),\n'
            '        "temperature": float(temperature),\n'
        ),
        (
            '        "num_predict": num_predict,\n'
            '        "schema": {},\n'
            '        "temperature": float(temperature),\n'
        ),
    ),
)


def _apply(src_root: Path, relative: str, old: str, new: str) -> bool:
    path = src_root / relative.removeprefix("src/")
    original = path.read_text(encoding="utf-8")
    if old not in original:
        return False
    updated = original.replace(old, new, 1)
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def _run_pytest(src_root: Path, extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("WATSON_MUTATION", None)
    env["PYTHONPATH"] = os.pathsep.join((str(src_root), str(REPO)))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=line",
            "-o",
            f"pythonpath={src_root} {REPO}",
            *TEST_PATHS,
            *(extra or []),
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _collected_count(proc: subprocess.CompletedProcess[str]) -> int:
    blob = f"{proc.stdout}\n{proc.stderr}"
    match = _COLLECTED.search(blob)
    if match:
        return int(match.group(1))
    return 0


def _classify(src_root: Path, *, changed: bool) -> str:
    """Return KILLED or SURVIVED. Collection errors and no-ops survive."""
    run = _run_pytest(src_root)
    blob = f"{run.stdout}\n{run.stderr}"
    ran = bool(_RAN.search(blob))
    if (not changed) or "ERROR collecting" in blob or run.returncode in {2, 3, 4, 5} or not ran:
        return "SURVIVED"
    if run.returncode == 1:
        return "KILLED"
    return "SURVIVED"


@pytest.mark.timeout(300)
def test_replay_kills_at_least_26_of_30(tmp_path: Path) -> None:
    assert len(MUTANTS) == TARGET_MUTANTS
    src_root = tmp_path / "src"
    shutil.copytree(REPO / "src" / "watson", src_root / "watson")
    originals = {
        relative: (src_root / relative.removeprefix("src/")).read_text(encoding="utf-8")
        for _, relative, _, _ in MUTANTS
    }

    collect = _run_pytest(src_root, ["--collect-only"])
    n_collect = _collected_count(collect)
    if collect.returncode != 0 or n_collect == 0:
        pytest.fail(
            "mutation harness could not collect tests "
            f"(exit {collect.returncode}, collected {n_collect}):\n"
            f"{collect.stdout}\n{collect.stderr}"
        )

    noop_run = _run_pytest(src_root)
    noop_blob = f"{noop_run.stdout}\n{noop_run.stderr}"
    if noop_run.returncode != 0 or not _RAN.search(noop_blob):
        pytest.fail(
            "unmutated copy must pass pytest "
            f"(exit {noop_run.returncode}):\n{noop_run.stdout}\n{noop_run.stderr}"
        )

    killed = 0
    survived: list[str] = []
    for name, relative, old, new in MUTANTS:
        path = src_root / relative.removeprefix("src/")
        path.write_text(originals[relative], encoding="utf-8")
        changed = _apply(src_root, relative, old, new)
        if _classify(src_root, changed=changed) == "KILLED":
            killed += 1
        else:
            survived.append(name if changed else f"{name} (noop)")
        path.write_text(originals[relative], encoding="utf-8")

    assert killed >= KILL_FLOOR, (
        f"killed {killed}/{TARGET_MUTANTS}; need ≥ {KILL_FLOOR}. survived: {', '.join(survived)}"
    )
    print(f"replay mutants killed {killed}/{TARGET_MUTANTS}; survived={survived}")
