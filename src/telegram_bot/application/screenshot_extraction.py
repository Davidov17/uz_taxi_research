"""Abstraction point for turning an uploaded screenshot into structured
metrics automatically.

Not implemented yet, by design: "Do not attempt OCR yet." This module
exists so that adding OCR/AI extraction later is a matter of writing one
new class and wiring it into SurveySession's constructor — not hunting
through the upload flow for places to bolt extraction on. SurveySession
calls `extract()` on whatever ScreenshotExtractor it's given right after
every screenshot is recorded; the default, NullScreenshotExtractor,
always returns nothing.

A future real implementation (OCR, a vision-model call, ...) reads the
image behind `attachment.telegram_file_id` and returns one ExtractedMetric
per value it found; SurveySession stores each one as a MetricObservation
with source=SCREENSHOT, linked back to that attachment — the same table
the verbal answers in Sections 4 and 7 already mirror into (see
docs/SURVEY_SPECIFICATION.md), so downstream export/analysis code doesn't
need to know whether a value came from OCR or from typing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from telegram_bot.domain.enums import MetricCode
from telegram_bot.infrastructure.db.models import Attachment


@dataclass(frozen=True)
class ExtractedMetric:
    metric_code: MetricCode
    value: float
    platform_id: int | None = None
    notes: str | None = None


class ScreenshotExtractor(ABC):
    """Given a just-uploaded screenshot, return whatever metrics can be
    read off it. Implementations should not raise for the ordinary
    "couldn't read anything useful" case — return an empty list instead;
    exceptions should be reserved for genuine failures (e.g. the extraction
    service being unreachable).
    """

    @abstractmethod
    async def extract(self, attachment: Attachment) -> list[ExtractedMetric]:
        raise NotImplementedError


class NullScreenshotExtractor(ScreenshotExtractor):
    """The current, default extractor: always returns nothing. Screenshots
    are still collected and stored in full (see Attachment) — this class
    only stands in for the not-yet-built step that would read numbers off
    of them automatically.
    """

    async def extract(self, attachment: Attachment) -> list[ExtractedMetric]:
        return []
