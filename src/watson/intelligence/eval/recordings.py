"""Record/replay language+embedding runtime. FakeRuntime is never implicit.

Replay plays back committed JSON responses with no truth.yaml lexicon,
no name-list enrichment, and no description hashing. Recordings are
hand-authored plausible model output (see recordings.json ``source``).

New ``complete`` rows are keyed by SHA-256 of roles + normalised contents
+ model + temperature + num_predict + output schema. Run-scoped claim
UUIDs and ISO timestamps are replaced with stable ordinals before
hashing so a second record of the same fixtures yields the same keys.
Replay builds that hash from the recorded or configured model params
(so a stub runtime still finds rows recorded against Ollama). A miss
hard-fails unless ``strict=False``. Legacy files still load: window
titles and unique phrase keys match as before; shared prompt-prefix
rows are attributed to a fixture day and never handed out in call order.
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

DEFAULT_RECORDINGS = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "eval" / "recordings.json"
)

_WINDOW = re.compile(r"^Window:\s*(.+)$", re.MULTILINE)
_PROMPT_HASH = re.compile(r"^[0-9a-f]{64}$")
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
        self.name = "replay" if inner is None else str(getattr(inner, "name", "record"))
        self.strict = mode == "replay" if strict is None else bool(strict)
        self._recordings = _load(self.path) if self.path.is_file() else _empty()
        self._dirty = False
        self.complete_calls = 0
        self.miss_count = 0
        self._legacy_warned = False
        self._day: str | None = None
        self._date: str | None = None

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
        model = self._model_name()
        temperature = self._temperature()
        digest = prompt_hash(
            messages,
            schema=schema,
            model=model,
            temperature=temperature,
            num_predict=num_predict,
        )
        if self.mode == "replay":
            return self._replay(
                messages,
                schema=schema,
                num_predict=num_predict,
                digest=digest,
            )
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
        return dict(stored)

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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._recordings, indent=2) + "\n", encoding="utf-8")
        self._dirty = False

    def _replay(
        self,
        messages: Sequence[Message],
        *,
        schema: Mapping[str, Any],
        num_predict: int | None,
        digest: str,
    ) -> dict[str, Any]:
        rows = self._recordings.get("complete", [])
        wanted = set(
            self._replay_digests(
                messages,
                schema=schema,
                num_predict=num_predict,
                digest=digest,
            )
        )
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("match") or "") not in wanted:
                continue
            response = row.get("response")
            if isinstance(response, dict):
                return dict(response)

        blob = "\n".join(str(item.get("content") or "") for item in messages)
        found = self._legacy_lookup(blob, rows)
        if found is not None:
            return found
        return self._miss(messages, digest)

    def _replay_digests(
        self,
        messages: Sequence[Message],
        *,
        schema: Mapping[str, Any],
        num_predict: int | None,
        digest: str,
    ) -> list[str]:
        """Hashes from configured params and each hashed row's recorded params."""
        canonical = _canonical(schema)
        specs: list[tuple[str, float, int | None, Any]] = [
            (self._model_name(), self._temperature(), num_predict, canonical)
        ]
        for row in self._recordings.get("complete") or []:
            if not isinstance(row, dict):
                continue
            if not _PROMPT_HASH.fullmatch(str(row.get("match") or "")):
                continue
            specs.append(_row_hash_params(row, num_predict=num_predict, schema=canonical))
        seen: set[tuple[str, float, int | None, str]] = set()
        out: list[str] = [digest]
        for model, temperature, predict, sch in specs:
            fingerprint = (
                model,
                temperature,
                predict,
                json.dumps(sch, sort_keys=True, separators=(",", ":"), default=str),
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            hashed = prompt_hash(
                messages,
                schema=sch,
                model=model,
                temperature=temperature,
                num_predict=predict,
            )
            if hashed not in out:
                out.append(hashed)
        return out

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
        self._warn_legacy()
        if window:
            # Understand: first window-title hit. Never consume (revisits).
            return dict(hits[0][1]["response"])

        kind = call_kind(blob)
        attributed = [
            hit
            for hit in hits
            if (prompt_day and _row_day(hit[1]) == prompt_day)
            or (prompt_date and _row_date(hit[1]) == prompt_date)
        ]
        if attributed:
            pool = [hit for hit in attributed if _key_matches_kind(hit[2], kind)]
            pool = pool or attributed
            header = blob[:500].casefold()
            header_same = [hit for hit in pool if hit[2].casefold() in header]
            # Same day's row may be reused (second consolidate / repair).
            chosen = header_same or pool
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
) -> str:
    """SHA-256 of roles, normalised contents, model, temperature, num_predict, schema."""
    payload = {
        "messages": _normalised_turns(messages),
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


def _id_ordinals(blob: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for match in _RUN_UUID.finditer(blob):
        token = match.group(0)
        if token not in mapping:
            mapping[token] = str(len(mapping) + 1)
    return mapping


def _run_invariant_key(line: str, *, numbered: bool) -> str:
    """Sort key with claim numbers, UUIDs, and run timestamps removed."""
    body = line.strip()
    if numbered and ". " in body:
        body = body.split(". ", 1)[1]
    return _RUN_STAMP.sub("<ts>", _RUN_UUID.sub("<id>", body))


def _renumber_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for index, line in enumerate(lines, start=1):
        newline = "\n" if line.endswith("\n") else ""
        body = line.strip()
        if ". " in body:
            body = body.split(". ", 1)[1]
        out.append(f"{index}. {body}{newline}")
    return out


def _sort_section_lines(text: str, header: str, *, numbered: bool) -> str:
    """Stable-sort run-varying retrieve / entity / claim blocks for the hash."""
    start = text.find(header)
    if start < 0:
        return text
    body_at = start + len(header)
    rest = text[body_at:]
    lines = rest.splitlines(keepends=True)
    taken: list[str] = []
    tail: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            tail = lines[index:]
            break
        if numbered and not re.match(r"\d+\.\s", stripped):
            tail = lines[index:]
            break
        if not numbered and not stripped.startswith("- "):
            tail = lines[index:]
            break
        taken.append(line)
    taken.sort(key=lambda line: _run_invariant_key(line, numbered=numbered))
    if numbered:
        taken = _renumber_lines(taken)
    return text[:body_at] + "".join(taken) + "".join(tail)


def _stabilise_contents(text: str) -> str:
    text = _sort_section_lines(text, "Retrieved context:\n", numbered=False)
    text = _sort_section_lines(text, "Entities:\n", numbered=False)
    text = _sort_section_lines(text, "Claims:\n", numbered=True)
    return text


def _apply_run_ids(text: str, mapping: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return mapping.get(match.group(0), match.group(0))

    return _RUN_STAMP.sub("<ts>", _RUN_UUID.sub(replace, text))


def _normalised_turns(messages: Sequence[Message] | str) -> list[dict[str, str]]:
    """All roles + contents; UUIDs become 1-based ordinals, run stamps collapse."""
    turns = [
        {"role": item["role"], "content": _stabilise_contents(item["content"])}
        for item in _turns(messages)
    ]
    mapping = _id_ordinals("\n".join(item["content"] for item in turns))
    return [
        {"role": item["role"], "content": _apply_run_ids(item["content"], mapping)}
        for item in turns
    ]


def _rewrite_value(value: Any, mapping: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return _apply_run_ids(value, mapping)
    if isinstance(value, list):
        return [_rewrite_value(item, mapping) for item in value]
    if isinstance(value, dict):
        return {str(key): _rewrite_value(item, mapping) for key, item in value.items()}
    return value


def _ordinalize_response(
    messages: Sequence[Message] | str, response: dict[str, Any]
) -> dict[str, Any]:
    """Rewrite prompt UUIDs in the stored response to ordinals so replay cites resolve."""
    mapping = _id_ordinals("\n".join(item["content"] for item in _turns(messages)))
    rewritten = _rewrite_value(response, mapping)
    return rewritten if isinstance(rewritten, dict) else dict(response)


def _row_model_temp(row: Mapping[str, Any]) -> tuple[str, float]:
    model = str(row.get("model") or "")
    raw = row.get("temperature")
    try:
        temperature = float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        temperature = 0.0
    return model, temperature


def _row_num_predict(row: Mapping[str, Any], fallback: int | None) -> int | None:
    if "num_predict" not in row:
        return fallback
    raw = row.get("num_predict")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


def _row_hash_params(
    row: Mapping[str, Any],
    *,
    num_predict: int | None,
    schema: Any,
) -> tuple[str, float, int | None, Any]:
    model, temperature = _row_model_temp(row)
    stored = row.get("schema")
    sch = _canonical(stored) if isinstance(stored, Mapping) else schema
    return model, temperature, _row_num_predict(row, num_predict), sch


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
    """Keep extract from returning a same-day clustering row, and vice versa."""
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
    if isinstance(complete, list):
        _attribute_legacy_days(complete)
    return loaded


def _empty() -> dict[str, Any]:
    return {"complete": [], "embeddings": {}, "source": "empty"}
