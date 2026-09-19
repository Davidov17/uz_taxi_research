"""Tests for the screenshot-derived metric_observations pipeline:
record_screenshot -> extractor -> metric_observations, all sourced from
screenshots (source=AnswerSource.SCREENSHOT) — see
SurveySession._upsert_metric_observation's docstring for why no verbal
answer mirrors into this table any more (every "main metric" question in
the current 15-question flow is a predefined range/bucket, not a precise
free-text number, so mirroring its midpoint here would overstate how exact
a driver's verbal answer actually was)."""

import pytest
from sqlalchemy import select

from telegram_bot.application.screenshot_extraction import ExtractedMetric, ScreenshotExtractor
from telegram_bot.application.survey_session import CursorState, SurveySession
from telegram_bot.domain.enums import AnswerSource, AttachmentFileType, MetricCode
from telegram_bot.domain.questionnaire import SCREENSHOTS_SECTION_INDEX
from telegram_bot.infrastructure.db.models import MetricObservation

pytestmark = pytest.mark.asyncio


class FakeExtractor(ScreenshotExtractor):
    """Stands in for a future real OCR/AI implementation in tests: returns
    a fixed set of metrics for every screenshot, so the wiring between
    record_screenshot -> extractor -> metric_observations can be tested
    without building actual OCR.
    """

    def __init__(self, metrics: list[ExtractedMetric]):
        self._metrics = metrics

    async def extract(self, attachment):
        return self._metrics


async def observations(db, survey_id):
    result = await db.execute(select(MetricObservation).where(MetricObservation.survey_id == survey_id))
    return result.scalars().all()


async def test_screenshot_extraction_writes_sourced_metric_observations(session, interviewer, survey):
    """The OCR/AI abstraction point: when an extractor DOES find metrics
    on a screenshot (a future real implementation, faked here), they land
    in metric_observations with source=SCREENSHOT, linked to that file."""
    extractor = FakeExtractor([ExtractedMetric(metric_code=MetricCode.WEEKLY_EARNINGS, value=1800000)])
    cursor = CursorState(survey_id=str(survey.id), section_index=SCREENSHOTS_SECTION_INDEX, platform_pos=None, question_index=0)
    s = await SurveySession.resume(session, cursor, interviewer, extractor)

    attachment = await s.record_screenshot(
        telegram_file_id="file_1", telegram_file_unique_id="unique_1", file_type=AttachmentFileType.SCREENSHOT
    )

    obs = await observations(session, survey.id)
    earnings = next(o for o in obs if o.metric_code == MetricCode.WEEKLY_EARNINGS)
    assert earnings.value_numeric == 1800000
    assert earnings.source == AnswerSource.SCREENSHOT
    assert earnings.attachment_id == attachment.id


async def test_metrics_from_different_platforms_are_kept_as_separate_rows(session, interviewer, survey, platform_yandex, platform_uklon):
    """Neither platform's extracted value overwrites the other's — they're
    distinguished by platform_id, not just metric_code."""
    extractor = FakeExtractor(
        [ExtractedMetric(metric_code=MetricCode.WEEKLY_EARNINGS, value=1800000, platform_id=platform_yandex.id)]
    )
    cursor = CursorState(survey_id=str(survey.id), section_index=SCREENSHOTS_SECTION_INDEX, platform_pos=None, question_index=0)
    s = await SurveySession.resume(session, cursor, interviewer, extractor)
    await s.record_screenshot(telegram_file_id="f1", telegram_file_unique_id="u1", platform_id=platform_yandex.id)

    s.extractor = FakeExtractor(
        [ExtractedMetric(metric_code=MetricCode.WEEKLY_EARNINGS, value=2200000, platform_id=platform_uklon.id)]
    )
    await s.record_screenshot(telegram_file_id="f2", telegram_file_unique_id="u2", platform_id=platform_uklon.id)

    obs = await observations(session, survey.id)
    earnings_obs = [o for o in obs if o.metric_code == MetricCode.WEEKLY_EARNINGS]
    by_platform = {o.platform_id: o.value_numeric for o in earnings_obs}
    assert by_platform == {platform_yandex.id: 1800000, platform_uklon.id: 2200000}


async def test_repeated_extraction_of_same_metric_and_platform_updates_not_duplicates(session, interviewer, survey, platform_yandex):
    """A second screenshot with a newer reading for the same metric+platform
    updates the existing observation in place rather than creating a
    second, conflicting row for the same (survey, metric, source, platform)."""
    extractor = FakeExtractor(
        [ExtractedMetric(metric_code=MetricCode.WEEKLY_EARNINGS, value=1800000, platform_id=platform_yandex.id)]
    )
    cursor = CursorState(survey_id=str(survey.id), section_index=SCREENSHOTS_SECTION_INDEX, platform_pos=None, question_index=0)
    s = await SurveySession.resume(session, cursor, interviewer, extractor)
    await s.record_screenshot(telegram_file_id="f1", telegram_file_unique_id="u1", platform_id=platform_yandex.id)

    s.extractor = FakeExtractor(
        [ExtractedMetric(metric_code=MetricCode.WEEKLY_EARNINGS, value=1950000, platform_id=platform_yandex.id)]
    )
    await s.record_screenshot(telegram_file_id="f2", telegram_file_unique_id="u2", platform_id=platform_yandex.id)

    obs = await observations(session, survey.id)
    earnings_obs = [o for o in obs if o.metric_code == MetricCode.WEEKLY_EARNINGS and o.platform_id == platform_yandex.id]
    assert len(earnings_obs) == 1
    assert earnings_obs[0].value_numeric == 1950000
