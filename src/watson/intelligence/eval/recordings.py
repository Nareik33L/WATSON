"""Record/replay language+embedding runtime. FakeRuntime is never implicit.

Replay plays back committed JSON responses with no truth.yaml lexicon,
no name-list enrichment, and no description hashing. Recordings are
hand-authored plausible model output (see recordings.json ``source``).

New ``complete`` rows are keyed by SHA-256 of roles + contents + model +
temperature + num_predict + schema. Only IDs and timestamps this run
issued are replaced with prompt-order ordinals (fixture UUIDs/dates stay).
Stored responses use those same ordinals; replay maps them back to the
current prompt's IDs so ``[c:N]`` cites the same claim. New-format files
(``format: prompt-hash-v1``) hash with the recorded model and reject a
configured model/params mismatch. Legacy files (no format marker) still
match window titles and unique phrases, filtered by call type.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from watson.intelligence.runtime.types import Message, RuntimeUnavailable
from watson.memory.ids import issued_ids, issued_stamps

DEFAULT_RECORDINGS = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "eval" / "recordings.json"
)

FORMAT_V1 = "prompt-hash-v1"
_WINDOW = re.compile(r"^Window:\s*(.+)$", re.MULTILINE)
_PROMPT_HASH = re.compile(r"^[0-9a-f]{64}$")
_CITE = re.compile(r"\[c:([^\]]+)\]")
_ISO_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_RUN_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_RUN_STAMP = re.compile(r"\b20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b")
# Same order the bake-off recordings were written (run_eval V1_DAY_FIXTURES).
_LEGACY_DAY_ORDER = (
    "day_acme_q4",
    "day_fragmented_multi",
    "day_meeting_heavy",
    "day_sensitive_traps",
    "day_near_empty",
)
_DAY_DATES = {
    "day_acme_q4": "2026-10-31",
    "day_fragmented_multi": "2026-11-03",
    "day_meeting_heavy": "2026-11-04",
    "day_sensitive_traps": "2026-11-05",
    "day_near_empty": "2026-11-06",
}
# Unique window-title fragments → fixture day. Shared titles (Acme Q4 draft
# appears on two days) are omitted so attribution stays unambiguous.
_WINDOW_DAY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "day_acme_q4",
        (
            "re: acme q4 proposal",
            "acme rate card",
            "proposals/acme_q4",
            "compose — revised pricing",
        ),
    ),
    (
        "day_fragmented_multi",
        (
            "northwind",
            "harbor safety",
            "harbor findings",
            "cedar onboarding",
        ),
    ),
    (
        "day_meeting_heavy",
        (
            "lumen room",
            "standup notes",
            "harbor planning — notes",
            "compose — demo recap",
        ),
    ),
    ("day_sensitive_traps", ("north pier",)),
    ("day_near_empty", ("inbox — lumen desk",)),
)
log = logging.getLogger("watson.intelligence.eval.recordings")

# Generic understanding when no recording matches. Lenient mode only.
_DEFAULT_UNDERSTAND: dict[str, Any] = {
    "activity_type": "knowledge_work",
    "description": "Working in the frontmost window. No completion evidence is visible.",
    "confidence": 0.7,
    "claims": [
        {
            "kind": "activity",
            "text": "Editing a document",
            "assertion": "observed",
            "evidence": [
                {"snippet": "Still working in the current window.", "line_start": 1, "line_end": 1}
            ],
        }
    ],
    "project_candidates": [],
    "people_candidates": [],
}


class ReplayMiss(RuntimeError):
    """No recording for this call. Message names kind and key hash only."""


class ReplayParamsError(RuntimeError):
    """Configured model/params do not match a new-format recordings file."""


class RecordingRuntime:
    """Replay committed responses, or record a wrapped runtime.

    ``name`` is ``replay`` unless an inner runtime is recording. FakeRuntime
    is not constructed here. Replay does not rewrite responses. ``strict``
    defaults to True in replay mode (CI / ``watson eval``); set False to
    opt into a warned default on miss.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        inner: Any | None = None,
        mode: str = "replay",
        strict: bool | None = None,
        expected_model: str = "",
    ) -> None:
        if mode not in {"replay", "record"}:
            raise ValueError('eval mode must be "replay" or "record"')
        if mode == "record" and inner is None:
            raise RuntimeUnavailable(
                "record mode needs an explicit runtime (ollama). "
                "FakeRuntime is opt-in only via --runtime fake."
            )
        self.mode = mode
        self.path = path or DEFAULT_RECORDINGS
        self.inner = inner
        self.expected_model = str(expected_model or "")
        self.name = "replay" if inner is None else str(getattr(inner, "name", "record"))
        self.strict = mode == "replay" if strict is None else bool(strict)
        self._recordings = _load(self.path) if self.path.is_file() else _empty()
        self._dirty = False
        self.complete_calls = 0
        self.miss_count = 0
        self.last_miss: str | None = None
        self._legacy_warned = False
        self._day: str | None = None
        self._date: str | None = None
        if mode == "replay":
            self._reject_param_mismatch()

    def set_day(self, name: str | None, date: str | None = None) -> None:
        """Bind subsequent complete() calls to a fixture day (eval runner)."""
        self._day = name or None
        self._date = date or None

    def probe(self) -> None:
        probe = getattr(self.inner, "probe", None)
        if callable(probe):
            probe()

    def end_phase(self) -> None:
        end = getattr(self.inner, "end_phase", None)
        if callable(end):
            end()

    def complete(
        self,
        messages: Sequence[Message],
        schema: Mapping[str, Any],
        *,
        num_predict: int | None = None,
    ) -> dict[str, Any]:
        self.complete_calls += 1
        model = self._hash_model()
        temperature = self._hash_temperature()
        digest = prompt_hash(
            messages,
            schema=schema,
            model=model,
            temperature=temperature,
            num_predict=num_predict,
        )
        if self.mode == "replay":
            return self._replay(messages, digest=digest)
        assert self.inner is not None
        try:
            result = self.inner.complete(messages, schema, num_predict=num_predict)
        except TypeError as exc:
            if "num_predict" not in str(exc):
                raise
            result = self.inner.complete(messages, schema)
        if not isinstance(result, dict):
            raise TypeError("recorded complete() must return an object")
        stored = _ordinalize_response(messages, dict(result))
        self._recordings.setdefault("complete", []).append(
            {
                "match": digest,
                "day": self._day,
                "date": self._date,
                "model": model,
                "temperature": temperature,
                "num_predict": num_predict,
                "schema": _canonical(schema),
                "response": stored,
            }
        )
        self._dirty = True
        return dict(result)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.mode == "record":
            assert self.inner is not None
            vectors = self.inner.embed(texts)
            stored = self._recordings.setdefault("embeddings", {})
            recorded: list[list[float]] = []
            for text, vector in zip(texts, vectors, strict=False):
                row = [float(x) for x in vector]
                stored[text] = row
                recorded.append(row)
            self._dirty = True
            return recorded
        stored = self._recordings.get("embeddings")
        out: list[list[float]] = []
        for text in texts:
            raw = stored.get(text) if isinstance(stored, dict) else None
            if isinstance(raw, list):
                out.append([float(x) for x in raw])
            else:
                # Signed hash dims so distinct names are not cosine-collapsed
                # into the first entity of that type (plan D8 resolver).
                digest = hashlib.sha256(text.encode()).digest()
                out.append([(byte - 128.0) / 128.0 for byte in digest[:16]])
        return out

    def save(self) -> None:
        if not self._dirty:
            return
        if self.mode == "record":
            self._recordings["format"] = FORMAT_V1
            self._recordings["model"] = self._model_name()
            self._recordings["temperature"] = self._temperature()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._recordings, indent=2) + "\n", encoding="utf-8")
        self._dirty = False

    def _replay(self, messages: Sequence[Message], digest: str) -> dict[str, Any]:
        rows = self._recordings.get("complete", [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("match") or "") != digest:
                continue
            response = row.get("response")
            if isinstance(response, dict):
                return _restore_response(messages, dict(response))

        if not _is_new_format(self._recordings):
            blob = "\n".join(str(item.get("content") or "") for item in messages)
            found = self._legacy_lookup(blob, rows)
            if found is not None:
                return found
        return self._miss(messages, digest)

    def _legacy_lookup(self, blob: str, rows: Any) -> dict[str, Any] | None:
        window = _WINDOW.search(blob)
        haystack = window.group(1).strip() if window else blob
        prompt_day, prompt_date = _prompt_day_date(self._day, self._date, blob)
        hits: list[tuple[int, dict[str, Any], str]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            needle = str(row.get("match") or "")
            if not needle or _PROMPT_HASH.fullmatch(needle):
                continue
            response = row.get("response")
            if needle.casefold() not in haystack.casefold() or not isinstance(response, dict):
                continue
            hits.append((index, row, needle))
        if not hits:
            return None
        kind = call_kind(blob)
        hits = [hit for hit in hits if _key_matches_kind(hit[2], kind)]
        if not hits:
            return None
        self._warn_legacy()
        if window:
            # Understand: first window-title hit. Never consume (revisits).
            return dict(hits[0][1]["response"])

        attributed = [
            hit
            for hit in hits
            if (prompt_day and _row_day(hit[1]) == prompt_day)
            or (prompt_date and _row_date(hit[1]) == prompt_date)
        ]
        if attributed:
            header = blob[:500].casefold()
            header_same = [hit for hit in attributed if hit[2].casefold() in header]
            # Same day's row may be reused (second consolidate / repair).
            chosen = header_same or attributed
            return dict(chosen[0][1]["response"])

        # Observation / claim prefixes identify the day by appearing in this
        # prompt. Do not require a unique count (two consolidate passes).
        content = [
            hit
            for hit in hits
            if not _is_window_key(hit[2])
            and not _is_shared_prefix_key(hit[2])
            and not _row_day(hit[1])
            and not _row_date(hit[1])
            and _key_matches_kind(hit[2], kind)
        ]
        if content:
            return dict(content[0][1]["response"])

        other_days = [hit for hit in hits if _row_day(hit[1]) and _row_day(hit[1]) != prompt_day]
        if (prompt_day or prompt_date) and other_days:
            # This day has no row. Never hand out another day's last extract.
            return None

        # Unique phrase keys (CI "Extract cross-segment") apply to every day.
        unique = [
            hit
            for hit in hits
            if _legacy_key_count(rows, hit[2]) == 1
            and not _row_day(hit[1])
            and not _row_date(hit[1])
        ]
        if unique:
            return dict(unique[0][1]["response"])
        return None

    def _miss(self, messages: Sequence[Message], digest: str) -> dict[str, Any]:
        kind = call_kind(messages)
        detail = f"replay miss for {kind} key={digest}"
        self.miss_count += 1
        self.last_miss = f"ReplayMiss: {detail}"
        log.error("%s", detail)
        if self.strict:
            raise ReplayMiss(detail)
        log.warning("%s; lenient mode returning default", detail)
        return dict(_DEFAULT_UNDERSTAND)

    def _warn_legacy(self) -> None:
        if self._legacy_warned:
            return
        self._legacy_warned = True
        log.warning(
            "legacy recording key used; exact prompt-hash lookup missed. "
            "Per-day rows match by day/date, not call order"
        )

    @property
    def recorded_model(self) -> str:
        """Model name stored on a new-format file, else the inner/expected model."""
        return self._recorded_model() or self._configured_model()

    def _is_new_format(self) -> bool:
        return _is_new_format(self._recordings)

    def _configured_model(self) -> str:
        if self.inner is not None:
            return self._model_name()
        return self.expected_model

    def _hash_model(self) -> str:
        return self._configured_model() or self._recorded_model()

    def _hash_temperature(self) -> float:
        if self.inner is not None:
            return self._temperature()
        recorded = self._recorded_temperature()
        return recorded if recorded is not None else 0.0

    def _recorded_model(self) -> str:
        value = self._recordings.get("model")
        if value:
            return str(value)
        for row in self._recordings.get("complete") or []:
            if isinstance(row, dict) and _PROMPT_HASH.fullmatch(str(row.get("match") or "")):
                model = str(row.get("model") or "")
                if model:
                    return model
        return ""

    def _recorded_temperature(self) -> float | None:
        if "temperature" in self._recordings:
            try:
                return float(self._recordings["temperature"])
            except (TypeError, ValueError):
                return None
        for row in self._recordings.get("complete") or []:
            if isinstance(row, dict) and _PROMPT_HASH.fullmatch(str(row.get("match") or "")):
                _, temperature = _row_model_temp(row)
                return temperature
        return None

    def _reject_param_mismatch(self) -> None:
        if not self._is_new_format():
            return
        recorded = self._recorded_model()
        configured = self._configured_model()
        if configured and recorded and configured != recorded:
            raise ReplayParamsError(f"recordings are for {recorded}, not {configured}")
        if self.inner is not None:
            rec_temp = self._recorded_temperature()
            if rec_temp is not None and self._temperature() != rec_temp:
                raise ReplayParamsError(
                    f"recordings temperature is {rec_temp}, not {self._temperature()}"
                )

    def _model_name(self) -> str:
        inner = self.inner
        if inner is None:
            return ""
        return str(getattr(inner, "llm_model", "") or "")

    def _temperature(self) -> float:
        inner = self.inner
        if inner is None:
            return 0.0
        settings = getattr(inner, "_settings", None)
        raw = getattr(settings, "temperature", None) if settings is not None else None
        if raw is None:
            raw = getattr(inner, "temperature", None)
        try:
            return float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return 0.0


def prompt_hash(
    messages: Sequence[Message] | str,
    *,
    schema: Mapping[str, Any] | None = None,
    model: str = "",
    temperature: float = 0.0,
    num_predict: int | None = None,
    run_ids: Sequence[str] | None = None,
    run_stamps: Sequence[str] | None = None,
) -> str:
    """SHA-256 of roles, contents with run-issued IDs ordinalized, model, params."""
    payload = {
        "messages": _normalised_turns(messages, run_ids=run_ids, run_stamps=run_stamps),
        "model": model or "",
        "num_predict": num_predict,
        "schema": _canonical(schema),
        "temperature": float(temperature),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def call_kind(messages: Sequence[Message] | str) -> str:
    """Name the complete() call for miss errors. No prompt text is returned."""
    if isinstance(messages, str):
        blob = messages
    else:
        blob = "\n".join(str(item.get("content") or "") for item in messages)
    folded = blob.casefold()
    if "extract cross-segment" in folded or "day extract" in folded:
        return "day_extract"
    if "daily narrative" in folded or "daily summary" in folded:
        return "daily_summary"
    if "cluster these observations" in folded:
        return "clustering"
    if _WINDOW.search(blob) or folded.lstrip().startswith("# watson understanding"):
        return "understanding"
    return "complete"


def _turns(messages: Sequence[Message] | str) -> list[dict[str, str]]:
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    return [
        {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
        for item in messages
    ]


def _run_ids(run_ids: Sequence[str] | None) -> tuple[str, ...]:
    if run_ids is not None:
        return tuple(run_ids)
    return issued_ids()


def _run_stamps(run_stamps: Sequence[str] | None) -> tuple[str, ...]:
    if run_stamps is not None:
        return tuple(run_stamps)
    return issued_stamps()


def _id_ordinals(blob: str, run_ids: Sequence[str]) -> dict[str, str]:
    """Map run-issued UUIDs to 1-based ordinals in prompt appearance order."""
    known = {item.casefold() for item in run_ids}
    mapping: dict[str, str] = {}
    for match in _RUN_UUID.finditer(blob):
        token = match.group(0)
        if token.casefold() not in known or token in mapping:
            continue
        mapping[token] = str(len(mapping) + 1)
    return mapping


def _apply_run_ids(text: str, mapping: Mapping[str, str], stamps: Sequence[str]) -> str:
    known_stamps = set(stamps)

    def replace_id(match: re.Match[str]) -> str:
        token = match.group(0)
        return mapping.get(token, token)

    def replace_stamp(match: re.Match[str]) -> str:
        token = match.group(0)
        return "<ts>" if token in known_stamps else token

    text = _RUN_UUID.sub(replace_id, text)
    if known_stamps:
        text = _RUN_STAMP.sub(replace_stamp, text)
    return text


def _normalised_turns(
    messages: Sequence[Message] | str,
    *,
    run_ids: Sequence[str] | None = None,
    run_stamps: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """Roles + contents; run-issued IDs become prompt-order ordinals. No sorting."""
    turns = _turns(messages)
    blob = "\n".join(item["content"] for item in turns)
    mapping = _id_ordinals(blob, _run_ids(run_ids))
    stamps = _run_stamps(run_stamps)
    return [
        {"role": item["role"], "content": _apply_run_ids(item["content"], mapping, stamps)}
        for item in turns
    ]


def _rewrite_value(value: Any, mapping: Mapping[str, str], stamps: Sequence[str]) -> Any:
    if isinstance(value, str):
        return _apply_run_ids(value, mapping, stamps)
    if isinstance(value, list):
        return [_rewrite_value(item, mapping, stamps) for item in value]
    if isinstance(value, dict):
        return {str(key): _rewrite_value(item, mapping, stamps) for key, item in value.items()}
    return value


def _ordinalize_response(
    messages: Sequence[Message] | str, response: dict[str, Any]
) -> dict[str, Any]:
    """Rewrite prompt run-IDs in the stored response to prompt-order ordinals."""
    blob = "\n".join(item["content"] for item in _turns(messages))
    mapping = _id_ordinals(blob, _run_ids(None))
    rewritten = _rewrite_value(response, mapping, _run_stamps(None))
    return rewritten if isinstance(rewritten, dict) else dict(response)


def _restore_response(
    messages: Sequence[Message] | str, response: dict[str, Any]
) -> dict[str, Any]:
    """Map stored prompt-order ordinals back to this run's IDs."""
    blob = "\n".join(item["content"] for item in _turns(messages))
    mapping = _id_ordinals(blob, _run_ids(None))
    inverse = {ordinal: token for token, ordinal in mapping.items()}
    restored = _restore_value(response, inverse)
    return restored if isinstance(restored, dict) else dict(response)


def _restore_value(value: Any, inverse: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        if value in inverse:
            return inverse[value]

        def replace_cite(match: re.Match[str]) -> str:
            token = match.group(1)
            if token in inverse:
                return f"[c:{inverse[token]}]"
            return match.group(0)

        return _CITE.sub(replace_cite, value)
    if isinstance(value, list):
        return [_restore_value(item, inverse) for item in value]
    if isinstance(value, dict):
        return {str(key): _restore_value(item, inverse) for key, item in value.items()}
    return value


def _row_model_temp(row: Mapping[str, Any]) -> tuple[str, float]:
    model = str(row.get("model") or "")
    raw = row.get("temperature")
    try:
        temperature = float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        temperature = 0.0
    return model, temperature


def _is_new_format(data: Mapping[str, Any]) -> bool:
    return str(data.get("format") or "") == FORMAT_V1


def _canonical(schema: Mapping[str, Any] | None) -> Any:
    if not schema:
        return {}
    try:
        return json.loads(json.dumps(schema, sort_keys=True, default=str))
    except TypeError:
        return {}


def _date_from_prompt(blob: str) -> str | None:
    match = _ISO_DATE.search(blob)
    return match.group(1) if match else None


def _row_day(row: Mapping[str, Any]) -> str | None:
    value = row.get("day")
    return str(value) if value else None


def _row_date(row: Mapping[str, Any]) -> str | None:
    value = row.get("date")
    return str(value) if value else None


def _legacy_key_count(rows: Any, needle: str) -> int:
    return sum(1 for row in rows if isinstance(row, dict) and str(row.get("match") or "") == needle)


def _is_shared_prefix_key(needle: str) -> bool:
    return "\n" in needle or needle.startswith("#")


def _is_window_key(needle: str) -> bool:
    """True for understand window-title keys, not extract/cluster/summary."""
    if not needle or _PROMPT_HASH.fullmatch(needle) or _is_shared_prefix_key(needle):
        return False
    folded = needle.casefold()
    if folded.startswith("[") or folded.startswith("#"):
        return False
    markers = (
        "extract cross-segment",
        "cluster these observations",
        "write the daily narrative",
        "from the day's numbered",
        "you produce",
    )
    if any(marker in folded for marker in markers):
        return False
    if " is " in folded or folded.rstrip().endswith("."):
        return False
    return True


def _prompt_day_date(
    bound_day: str | None,
    bound_date: str | None,
    blob: str,
) -> tuple[str | None, str | None]:
    """Day/date from the eval runner, else ISO date / fixture date in the prompt."""
    day = bound_day or None
    date = bound_date or _date_from_prompt(blob)
    if day and not date:
        date = _DAY_DATES.get(day)
    if date and not day:
        matches = [name for name, known in _DAY_DATES.items() if known == date]
        if len(matches) == 1:
            day = matches[0]
    return day, date


def _day_from_window_needle(needle: str) -> str | None:
    folded = needle.casefold()
    hits = [name for name, hints in _WINDOW_DAY_HINTS if any(hint in folded for hint in hints)]
    return hits[0] if len(hits) == 1 else None


def _key_matches_kind(needle: str, kind: str) -> bool:
    """Never serve extract/summary from an understand row, or vice versa."""
    folded = needle.casefold()
    if kind == "day_extract":
        return (
            "day extract" in folded
            or "extract cross-segment" in folded
            or (folded.startswith("[") and not folded.startswith("[c:"))
        )
    if kind == "daily_summary":
        return "daily summary" in folded or "daily narrative" in folded or folded.startswith("[c:")
    if kind == "clustering":
        return (
            "cluster these" in folded
            or "watson clustering" in folded
            or (
                not folded.startswith("[")
                and not _is_shared_prefix_key(needle)
                and not _is_window_key(needle)
            )
        )
    if kind == "understanding":
        return _is_window_key(needle)
    return True


def _bind_row_day(row: dict[str, Any], day: str) -> None:
    row["day"] = day
    row.setdefault("date", _DAY_DATES.get(day))


def _attribute_legacy_days(rows: list[Any]) -> None:
    """Tag unattributed per-day rows. Never assign by call order.

    Shared-prefix duplicates (``# WATSON day extract``) do not name a day.
    Bake-off files are attributed from neighboring unique window titles.
    A group with exactly two rows per V1 fixture is tagged in fixture order.
    Anything still ambiguous stays untagged and is a miss at lookup.
    Window titles and unique phrase keys are left alone.
    """
    current: str | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        needle = str(row.get("match") or "")
        if not needle or _PROMPT_HASH.fullmatch(needle):
            continue
        if _is_window_key(needle):
            found = _day_from_window_needle(needle)
            if found:
                current = found
            continue
        if _row_day(row) or _row_date(row) or current is None:
            continue
        if _legacy_key_count(rows, needle) == 1 and not _is_shared_prefix_key(needle):
            continue
        _bind_row_day(row, current)

    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if _row_day(row) or _row_date(row):
            continue
        needle = str(row.get("match") or "")
        if not needle or _PROMPT_HASH.fullmatch(needle) or not _is_shared_prefix_key(needle):
            continue
        groups[needle].append(index)
    expected = 2 * len(_LEGACY_DAY_ORDER)
    for indices in groups.values():
        if len(indices) != expected:
            # Ambiguous count: do not invent a 1-per-day slot order.
            continue
        for slot, index in enumerate(indices):
            row = rows[index]
            if isinstance(row, dict):
                _bind_row_day(row, _LEGACY_DAY_ORDER[slot // 2])


def _load(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: recordings must be a JSON object")
    complete = loaded.get("complete")
    if isinstance(complete, list) and not _is_new_format(loaded):
        _attribute_legacy_days(complete)
    return loaded


def _empty() -> dict[str, Any]:
    return {"complete": [], "embeddings": {}, "source": "empty"}
