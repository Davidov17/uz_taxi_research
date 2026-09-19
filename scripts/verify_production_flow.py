"""End-to-end smoke test against a real, running stack: a real Postgres
connection (not the test suite's rolled-back savepoint), real bytes written
to SCREENSHOT_STORAGE_DIR, and (optionally) the live web API over HTTP.

Meant to be run once after a fresh deploy to prove the whole thing actually
works, not as part of the pytest suite (that already covers this logic in
isolation — see tests/). Usage:

    uv run python scripts/verify_production_flow.py [--api-base http://localhost:8000]

Creates one real completed survey with one screenshot in whatever database
DATABASE_URL points at — safe to run against a throwaway/staging database,
not recommended against a database you care about keeping pristine.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import urllib.request
from pathlib import Path

from sqlalchemy import select

from telegram_bot.application.export_service import ExportFilters, ExportService, workbook_to_bytes
from telegram_bot.application.statistics_service import StatisticsFilters, StatisticsService
from telegram_bot.application.survey_session import CursorState, SurveySession, get_or_create_interviewer
from telegram_bot.domain.enums import SurveyStatus
from telegram_bot.domain.questionnaire import QuestionType
from telegram_bot.infrastructure.config import settings
from telegram_bot.infrastructure.db.models import Platform
from telegram_bot.infrastructure.db.models.lookups import City
from telegram_bot.infrastructure.db.session import make_engine, make_session_factory
from telegram_bot.infrastructure.storage import download_attachment


class _FakeBotDownloads:
    """Stands in for aiogram's Bot.download() — writes real bytes to disk
    without needing a live Telegram token/network, same fake used in
    tests/test_storage.py."""

    async def download(self, file_id: str, destination) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes-for-smoke-test")


async def drive_full_survey(session, *, city_id: int, platform_id: int) -> None:
    for _ in range(400):
        q = session.current_question()
        if q is None:
            break
        if q.code == "city":
            await session.answer(q, str(city_id))
        elif q.code == "platforms_used":
            await session.answer(q, [str(platform_id)])
        elif q.qtype == QuestionType.SINGLE_CHOICE:
            options = await session.resolve_options(q)
            await session.answer(q, options[0].value)
        elif q.qtype == QuestionType.MULTI_CHOICE:
            options = await session.resolve_options(q)
            await session.answer(q, [options[0].value])
        elif q.qtype == QuestionType.YES_NO:
            if q.code == "has_screenshots":
                await session.answer(q, True)
                break  # drop into the screenshot loop below
            await session.answer(q, False)
        elif not q.required:
            await session.skip(q)
        else:
            value = 1.0
            if q.min_value is not None:
                value = max(value, q.min_value)
            if q.max_value is not None:
                value = min(value, q.max_value)
            await session.answer(q, str(value))


async def main(api_base: str | None) -> int:
    print(f"Connecting to {settings.database_url.split('@')[-1]} ...")
    engine = make_engine()
    session_factory = make_session_factory(engine)

    async with session_factory() as db:
        city = (await db.execute(select(City).limit(1))).scalars().first()
        platform = (await db.execute(select(Platform).where(Platform.code == "yandex_go"))).scalars().first()
        assert city is not None, "no seeded city found — run migrations first"
        assert platform is not None, "no seeded 'yandex_go' platform found — run migrations first"

        interviewer = await get_or_create_interviewer(db, 999_000_111, "Smoke Test Interviewer")
        session = await SurveySession.resume(db, CursorState.initial(language="ru"), interviewer)
        await drive_full_survey(session, city_id=city.id, platform_id=platform.id)
        assert session.awaiting_screenshot_upload, "expected to land in the screenshot upload step"

        attachment = await session.record_screenshot(
            telegram_file_id="smoke-test-file-id",
            telegram_file_unique_id="smoke-test-file-unique-id",
            mime_type="image/jpeg",
            platform_id=platform.id,
        )
        local_path = await download_attachment(
            _FakeBotDownloads(),
            storage_dir=settings.screenshot_storage_dir,
            survey_id=session.survey.id,
            attachment_id=attachment.id,
            telegram_file_id="smoke-test-file-id",
            mime_type="image/jpeg",
        )
        assert local_path and Path(local_path).is_file(), "screenshot was not written to disk"
        attachment.local_path = local_path
        await db.flush()

        await session.finish_screenshot_upload()
        assert session.is_review(), "expected to land on the review screen before confirming"
        assert session.survey.status == SurveyStatus.DRAFT, "must not auto-submit before confirm()"
        await session.confirm()
        await db.commit()

        survey = session.survey
        assert survey.status == SurveyStatus.COMPLETED, f"expected COMPLETED, got {survey.status}"
        assert survey.human_code, "expected a human-readable survey code"
        assert survey.language.value == "ru", f"expected language 'ru', got {survey.language}"
        human_code = survey.human_code
        print(f"Survey completed: {human_code} (language={survey.language.value})  screenshot -> {local_path}")

    async with session_factory() as db:
        report = await StatisticsService(db).generate_report(StatisticsFilters())
        assert report.general.total_surveys >= 1
        print(f"Statistics OK: {report.general.total_surveys} completed survey(s) in report")

        export_service = ExportService(db)
        sheets = await export_service.build_sheets(ExportFilters())
        summary_sheet = next(s for s in sheets if s.key == "survey_summary")
        assert any(row[0] == human_code for row in summary_sheet.rows), (
            "new survey not found in Survey Summary export rows"
        )

        workbook = await export_service.build_workbook(ExportFilters())
        workbook_bytes = workbook_to_bytes(workbook)
        out_path = Path(tempfile.gettempdir()) / "mrb_smoke_test_export.xlsx"
        out_path.write_bytes(workbook_bytes)
        print(f"Excel export OK: {len(workbook_bytes)} bytes -> {out_path}")

    await engine.dispose()

    if api_base:
        print(f"Checking live API at {api_base} ...")
        with urllib.request.urlopen(f"{api_base}/health", timeout=5) as resp:
            assert resp.status == 200
        with urllib.request.urlopen(f"{api_base}/api/surveys/{human_code}", timeout=5) as resp:
            assert resp.status == 200
        with urllib.request.urlopen(f"{api_base}/api/surveys/{human_code}/screenshots", timeout=5) as resp:
            assert resp.status == 200
            import json

            shots = json.loads(resp.read())
            assert len(shots) == 1
            assert shots[0]["cached_locally"] == "yes"
            attachment_id = shots[0]["attachment_id"]
        with urllib.request.urlopen(f"{api_base}/api/screenshots/{attachment_id}/image", timeout=5) as resp:
            assert resp.status == 200
            assert len(resp.read()) > 0
        with urllib.request.urlopen(f"{api_base}/api/export/excel", timeout=10) as resp:
            assert resp.status == 200
            assert len(resp.read()) > 1000
        print("Live API OK: survey detail, screenshot metadata, screenshot image, and Excel export all served correctly.")

    print("\nAll production-flow checks passed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default=None, help="e.g. http://localhost:8000 to also check the live web API")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.api_base)))
