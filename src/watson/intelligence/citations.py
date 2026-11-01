"""Citation post-processor for daily narratives (plan §7.2 / §11.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CITATION_RE = re.compile(r"\[c:([^\]]*)\]")


@dataclass(frozen=True)
class VerifiedNarrative:
    """Body text whose every `[c:id]` resolves to a claim on this day."""

    narrative_md: str
    paragraphs: tuple[str, ...]
    claim_ids: tuple[str, ...]
    unverified: tuple[dict[str, str], ...]

    def sections_json(self) -> dict[str, object]:
        return {
            "paragraphs": list(self.paragraphs),
            "unverified": [dict(row) for row in self.unverified],
            "claim_ids": list(self.claim_ids),
        }


def expand_index_citations(
    paragraphs: list[dict[str, Any]], claims: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rewrite 1-based `[c:N]` tokens so FakeRuntime recordings can cite claims."""
    out: list[dict[str, Any]] = []
    for para in paragraphs:
        text = str(para.get("text") or "")
        carried = [str(item) for item in list(para.get("claim_ids") or [])]
        rewritten = CITATION_RE.sub(lambda match: _rewrite_token(match, claims), text)
        mapped: list[str] = []
        for raw in carried:
            resolved = _as_claim_id(raw, claims)
            if resolved is not None:
                mapped.append(resolved)
        out.append({"text": rewritten, "claim_ids": mapped})
    return out


def verify_narrative(
    paragraphs: list[dict[str, Any]], day_claims: list[dict[str, Any]]
) -> VerifiedNarrative:
    """Keep sentences whose citations exist on this day; uncited claims → Unverified."""
    day_ids = {str(row["id"]) for row in day_claims}
    kept_paragraphs: list[str] = []
    cited: set[str] = set()
    for para in paragraphs:
        text = str(para.get("text") or "")
        kept_sentences: list[str] = []
        for sentence in _split_sentences(text):
            ids = CITATION_RE.findall(sentence)
            if ids and not all(_resolvable(claim_id, day_ids) for claim_id in ids):
                continue
            kept_sentences.append(sentence)
            cited.update(claim_id for claim_id in ids if _resolvable(claim_id, day_ids))
        kept = " ".join(kept_sentences).strip()
        if kept:
            kept_paragraphs.append(kept)
    unverified = tuple(
        {
            "id": str(row["id"]),
            "kind": str(row["kind"]),
            "text": str(row["text"]),
        }
        for row in day_claims
        if str(row["id"]) not in cited
    )
    return VerifiedNarrative(
        narrative_md="\n\n".join(kept_paragraphs),
        paragraphs=tuple(kept_paragraphs),
        claim_ids=tuple(sorted(cited)),
        unverified=unverified,
    )


def citations_resolvable(narrative_md: str, day_claim_ids: set[str]) -> bool:
    """§11.2 structural: every rendered `[c:id]` belongs to this day."""
    found = CITATION_RE.findall(narrative_md)
    return all(_resolvable(claim_id, day_claim_ids) for claim_id in found)


def _resolvable(claim_id: str, day_ids: set[str]) -> bool:
    return bool(claim_id) and claim_id in day_ids


def _as_claim_id(token: str, claims: list[dict[str, Any]]) -> str | None:
    if token.isdigit():
        index = int(token)
        if 1 <= index <= len(claims):
            return str(claims[index - 1]["id"])
        return None
    ids = {str(row["id"]) for row in claims}
    return token if token in ids else None


def _rewrite_token(match: re.Match[str], claims: list[dict[str, Any]]) -> str:
    resolved = _as_claim_id(match.group(1), claims)
    if resolved is None:
        return match.group(0)
    return f"[c:{resolved}]"


def _split_sentences(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", stripped) if part.strip()]
