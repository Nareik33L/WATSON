"""Choose a runtime from config and hold understanding jobs when it is missing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlcipher3 import dbapi2 as sqlcipher

from watson.config.schema import IntelligenceConfig, WatsonConfig
from watson.intelligence.runtime.fake import FakeRuntime
from watson.intelligence.runtime.ollama import OllamaRuntime
from watson.intelligence.runtime.types import (
    DisabledVisionRuntime,
    EmbeddingRuntime,
    JobFailure,
    LanguageRuntime,
    RuntimeUnavailable,
    VisionRuntime,
)
from watson.memory.db import iso_now
from watson.pipeline.jobs import JobState

UNDERSTAND_JOB_KIND = "understand"


@dataclass(frozen=True)
class RuntimeBundle:
    name: str
    language: LanguageRuntime
    embedding: EmbeddingRuntime
    vision: VisionRuntime
    settings: IntelligenceConfig

    def probe(self) -> None:
        language = self.language
        probe = getattr(language, "probe", None)
        if callable(probe):
            probe()


@dataclass(frozen=True)
class RuntimeHold:
    held: bool
    reason: str | None
    jobs_updated: int


def intelligence_settings(config: WatsonConfig | IntelligenceConfig) -> IntelligenceConfig:
    if isinstance(config, IntelligenceConfig):
        return config
    return config.intelligence


def build_runtime(config: WatsonConfig | IntelligenceConfig) -> RuntimeBundle:
    """Construct adapters. Probe is separate so a missing model cannot crash import."""
    settings = intelligence_settings(config)
    vision: VisionRuntime = DisabledVisionRuntime()
    if settings.runtime == "fake":
        fake = FakeRuntime()
        return RuntimeBundle(
            name=fake.name,
            language=fake,
            embedding=fake,
            vision=vision,
            settings=settings,
        )
    if settings.runtime == "ollama":
        ollama = OllamaRuntime(settings)
        return RuntimeBundle(
            name=ollama.name,
            language=ollama,
            embedding=ollama,
            vision=vision,
            settings=settings,
        )
    raise RuntimeUnavailable(
        f"Unknown intelligence.runtime {settings.runtime!r}. "
        'Use "ollama" or "fake".'
    )


def hold_understanding_jobs(
    conn: sqlcipher.Connection,
    reason: str,
    *,
    now: datetime | None = None,
) -> int:
    """Keep pending understand jobs queued and record the missing-runtime reason."""
    stamp = iso_now(now)
    cursor = conn.execute(
        """
        UPDATE jobs
        SET last_error = ?, updated_at = ?
        WHERE kind = ? AND state = ?
        """,
        (reason, stamp, UNDERSTAND_JOB_KIND, JobState.PENDING.value),
    )
    conn.commit()
    return int(cursor.rowcount)


def hold_if_unavailable(
    conn: sqlcipher.Connection,
    config: WatsonConfig | IntelligenceConfig,
    *,
    now: datetime | None = None,
) -> RuntimeHold:
    """§7.4: missing runtime/model → jobs stay pending with a clear reason.

    Never raises. Unexpected probe errors are treated as unavailable.
    """
    try:
        bundle = build_runtime(config)
        bundle.probe()
    except JobFailure as exc:
        count = hold_understanding_jobs(conn, str(exc), now=now)
        return RuntimeHold(held=True, reason=str(exc), jobs_updated=count)
    except Exception as exc:
        reason = f"intelligence runtime probe failed: {exc}"
        count = hold_understanding_jobs(conn, reason, now=now)
        return RuntimeHold(held=True, reason=reason, jobs_updated=count)
    return RuntimeHold(held=False, reason=None, jobs_updated=0)
