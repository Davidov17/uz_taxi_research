"""JSON API for the admin dashboard.

Every route is a thin translator: parse query params into the existing
application-layer filter dataclasses, call the existing service
(StatisticsService / ExportService / QualitativeService), and hand back
whatever it returns — FastAPI's jsonable_encoder serializes our dataclasses,
Enums, Decimals and datetimes correctly on its own, so there's no manual
JSON-shaping layer duplicating what the services already computed.

No business logic lives here. If a number looks wrong, the bug is in
application/, not in this file.
"""

from __future__ import annotations

import os
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from telegram_bot.application.export_service import ExportFilters, ExportService, workbook_to_bytes
from telegram_bot.application.qualitative_service import QualitativeService
from telegram_bot.application.statistics_service import StatisticsFilters, StatisticsService
from telegram_bot.domain.enums import Language, SurveyStatus
from telegram_bot.infrastructure.db.models import (
    Attachment,
    City,
    DriverEmployment,
    DriverType,
    Platform,
    Survey,
    SurveyPlatform,
)
from telegram_bot.web.deps import get_db

router = APIRouter(prefix="/api")


# ---- shared filter parsing --------------------------------------------------


class DashboardFilters:
    """Parsed from query params once per request, then converted to
    whichever application-layer filter dataclass a given service expects."""

    def __init__(
        self,
        city_id: int | None = Query(None, description="Restrict to one city"),
        platform_id: int | None = Query(None, description="Restrict to one platform"),
        driver_type_id: int | None = Query(None, description="Restrict to one driver type"),
        date_from: date | None = Query(None, description="Survey date, inclusive lower bound"),
        date_to: date | None = Query(None, description="Survey date, inclusive upper bound"),
        language: str | None = Query(None, description="Restrict to one interview language (en/ru/uz)"),
    ):
        self.city_id = city_id
        self.platform_id = platform_id
        self.driver_type_id = driver_type_id
        self.date_from = date_from
        self.date_to = date_to
        self.language = language

    def to_statistics_filters(self) -> StatisticsFilters:
        return StatisticsFilters(
            city_id=self.city_id,
            platform_id=self.platform_id,
            driver_type_id=self.driver_type_id,
            date_from=self.date_from,
            date_to=self.date_to,
            language=self.language,
        )

    def to_export_filters(self, *, include_incomplete: bool = False) -> ExportFilters:
        return ExportFilters(
            city_id=self.city_id,
            platform_id=self.platform_id,
            date_from=self.date_from,
            date_to=self.date_to,
            language=self.language,
            include_incomplete=include_incomplete,
        )


# ---- reference data (for filter dropdowns) -----------------------------------


@router.get("/cities")
async def list_cities(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(City).where(City.is_active).order_by(City.id))).scalars().all()
    return [{"id": c.id, "code": c.code, "name": c.name} for c in rows]


@router.get("/platforms")
async def list_platforms(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Platform).where(Platform.is_active).order_by(Platform.id))).scalars().all()
    return [{"id": p.id, "code": p.code, "name": p.name} for p in rows]


@router.get("/driver-types")
async def list_driver_types(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(DriverType).where(DriverType.is_active).order_by(DriverType.id))).scalars().all()
    return [{"id": d.id, "code": d.code, "name": d.name} for d in rows]


@router.get("/languages")
async def list_languages():
    """Static, not DB-backed — the three languages are fixed by the
    questionnaire's translation content (domain/i18n.py), not admin-editable
    reference data like cities/platforms."""
    return [{"code": "en", "name": "English"}, {"code": "ru", "name": "Русский"}, {"code": "uz", "name": "O'zbekcha"}]


# ---- OVERVIEW -----------------------------------------------------------------


@router.get("/overview")
async def overview(filters: DashboardFilters = Depends(), db: AsyncSession = Depends(get_db)):
    report = await StatisticsService(db).generate_report(filters.to_statistics_filters())
    return report.general


@router.get("/executive-summary")
async def executive_summary(filters: DashboardFilters = Depends(), db: AsyncSession = Depends(get_db)):
    report = await StatisticsService(db).generate_report(filters.to_statistics_filters())
    return report.executive


@router.get("/questionnaire-stats")
async def questionnaire_stats(filters: DashboardFilters = Depends(), db: AsyncSession = Depends(get_db)):
    report = await StatisticsService(db).generate_report(filters.to_statistics_filters())
    return {
        "platform_usage": report.general.surveys_by_platform,
        "platform_count_distribution": report.multi_app.platform_count_distribution,
        "driver_motivation": report.questionnaire.driver_motivation,
        "employment_relationship": report.questionnaire.employment_relationship,
        "loyalty_program": report.questionnaire.loyalty_program,
    }


# ---- DRIVER ECONOMICS -----------------------------------------------------------


@router.get("/driver-economics")
async def driver_economics(filters: DashboardFilters = Depends(), db: AsyncSession = Depends(get_db)):
    report = await StatisticsService(db).generate_report(filters.to_statistics_filters())
    return {
        "weekly_earnings": report.earnings.weekly_earnings,
        "trips_per_day": report.working_pattern.trips_per_day,
        "hours_per_day": report.working_pattern.hours_per_day,
        "earnings_per_hour": report.earnings.earnings_per_hour,
        "earnings_per_trip": report.earnings.earnings_per_trip,
        "commission_pct": report.commission.commission_pct,
        # Driver profile — structured where the current questionnaire
        # offers it, heuristic-from-text where it's only ever been free
        # text (both breakdowns come from StatisticsService; see its
        # module docstring for the "never treat missing as zero" and
        # "estimates vs sample facts" rules that produced these shapes).
        "hours_per_day_bucket": report.working_pattern.hours_per_day_bucket,
        "seasonal_pattern": report.driver_estimates.seasonal_pattern,
        "seasonality_text_classification": report.driver_estimates.seasonality_text_classification,
        "driver_type_distribution": report.driver_type.distribution,
        "driver_type_employment_text": report.driver_type.employment_text_classification,
        "perceived_market_leader": report.driver_estimates.perceived_market_leader,
    }


# ---- PLATFORM -------------------------------------------------------------------


@router.get("/platform-stats")
async def platform_stats(filters: DashboardFilters = Depends(), db: AsyncSession = Depends(get_db)):
    report = await StatisticsService(db).generate_report(filters.to_statistics_filters())
    return {
        "platform_usage": report.general.surveys_by_platform,
        "multi_app": report.multi_app.platform_count_bucket,
        "platform_combinations": report.multi_app.platform_combinations,
        "exclusivity": report.multi_app.exclusivity,
        "commission_by_platform": report.commission.commission_by_platform,
        "receives_bonuses": report.bonuses.receives_bonuses,
        "cash_pct": report.payments.cash_pct,
        "digital_pct": report.payments.digital_pct,
    }


# ---- QUALITATIVE -----------------------------------------------------------------


@router.get("/qualitative")
async def qualitative(filters: DashboardFilters = Depends(), db: AsyncSession = Depends(get_db)):
    return await QualitativeService(db).get_feeds(filters.to_export_filters())


# ---- RAW DATA: survey list + single-survey detail --------------------------------


def _completed_surveys_filtered(filters: DashboardFilters):
    stmt = select(Survey).where(Survey.status == SurveyStatus.COMPLETED)
    if filters.city_id is not None:
        stmt = stmt.where(Survey.city_id == filters.city_id)
    if filters.language is not None:
        stmt = stmt.where(Survey.language == Language(filters.language))
    if filters.date_from is not None:
        stmt = stmt.where(Survey.survey_datetime >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(Survey.survey_datetime <= filters.date_to)
    if filters.platform_id is not None:
        stmt = stmt.where(
            Survey.id.in_(select(SurveyPlatform.survey_id).where(SurveyPlatform.platform_id == filters.platform_id))
        )
    if filters.driver_type_id is not None:
        stmt = stmt.where(
            Survey.id.in_(
                select(DriverEmployment.survey_id).where(DriverEmployment.driver_type_id == filters.driver_type_id)
            )
        )
    return stmt


@router.get("/surveys")
async def list_surveys(
    filters: DashboardFilters = Depends(),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    base = _completed_surveys_filtered(filters)
    total_count = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    page_stmt = (
        base.options(selectinload(Survey.city))
        .order_by(Survey.survey_datetime.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(page_stmt)).unique().scalars().all()

    return {
        "total": total_count,
        "items": [
            {
                "survey_id": s.human_code,
                "city": s.city.name,
                "survey_date": s.survey_datetime.replace(tzinfo=None),
                "status": s.status.value,
            }
            for s in rows
        ],
    }


@router.get("/surveys/{human_code}")
async def survey_detail(human_code: str, db: AsyncSession = Depends(get_db)):
    detail = await ExportService(db).get_survey_detail(human_code)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No completed survey with id {human_code!r}")
    return detail


@router.get("/surveys/{human_code}/screenshots")
async def survey_screenshots(human_code: str, db: AsyncSession = Depends(get_db)):
    detail = await ExportService(db).get_survey_detail(human_code)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No completed survey with id {human_code!r}")
    return detail["screenshots"]


def _attachments_filtered(filters: DashboardFilters):
    """Screenshots across *all* surveys (unlike /surveys/{id}/screenshots,
    which is scoped to one survey) — same filter semantics as
    _completed_surveys_filtered (platform_id scopes to surveys that report
    working with that platform, not just attachments tagged with it, so
    an untagged screenshot from a multi-platform survey still counts)."""
    stmt = (
        select(Attachment)
        .join(Survey, Attachment.survey_id == Survey.id)
        .where(Survey.status == SurveyStatus.COMPLETED)
    )
    if filters.city_id is not None:
        stmt = stmt.where(Survey.city_id == filters.city_id)
    if filters.language is not None:
        stmt = stmt.where(Survey.language == Language(filters.language))
    if filters.date_from is not None:
        stmt = stmt.where(Survey.survey_datetime >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(Survey.survey_datetime <= filters.date_to)
    if filters.platform_id is not None:
        stmt = stmt.where(
            Survey.id.in_(select(SurveyPlatform.survey_id).where(SurveyPlatform.platform_id == filters.platform_id))
        )
    if filters.driver_type_id is not None:
        stmt = stmt.where(
            Survey.id.in_(
                select(DriverEmployment.survey_id).where(DriverEmployment.driver_type_id == filters.driver_type_id)
            )
        )
    return stmt


def _as_breakdown(rows) -> dict:
    counts = {str(label): count for label, count in rows if label is not None}
    return {"counts": counts, "total": sum(counts.values())}


@router.get("/screenshots")
async def list_screenshots(
    filters: DashboardFilters = Depends(),
    page: int = Query(0, ge=0),
    page_size: int = Query(24, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Evidence gallery: every uploaded screenshot across all (filtered)
    surveys, paginated, plus breakdowns for the Screenshots tab's summary
    charts. "submitted" in the summary counts respondents with at least
    one actually-uploaded attachment, not just a "yes" answer to
    has_screenshots — same definition StatisticsService uses for the
    executive KPI.
    """
    eligible_attachment_ids = _attachments_filtered(filters).with_only_columns(Attachment.id).subquery()

    total = (await db.execute(select(func.count()).select_from(eligible_attachment_ids))).scalar_one()

    respondents_total = (
        await db.execute(select(func.count()).select_from(_completed_surveys_filtered(filters).subquery()))
    ).scalar_one()
    respondents_with_screenshots = (
        await db.execute(
            select(func.count(func.distinct(Attachment.survey_id))).where(
                Attachment.id.in_(select(eligible_attachment_ids.c.id))
            )
        )
    ).scalar_one()

    page_stmt = (
        _attachments_filtered(filters)
        .options(selectinload(Attachment.survey).selectinload(Survey.city), selectinload(Attachment.platform))
        .order_by(Attachment.uploaded_at.desc())
        .limit(page_size)
        .offset(page * page_size)
    )
    rows = (await db.execute(page_stmt)).unique().scalars().all()

    by_platform_rows = (
        await db.execute(
            select(Platform.name, func.count())
            .where(Attachment.id.in_(select(eligible_attachment_ids.c.id)), Attachment.platform_id == Platform.id)
            .group_by(Platform.name)
        )
    ).all()
    by_city_rows = (
        await db.execute(
            select(City.name, func.count())
            .where(
                Attachment.id.in_(select(eligible_attachment_ids.c.id)),
                Attachment.survey_id == Survey.id,
                Survey.city_id == City.id,
            )
            .group_by(City.name)
        )
    ).all()
    by_date_rows = (
        await db.execute(
            select(func.date(Attachment.uploaded_at), func.count())
            .where(Attachment.id.in_(select(eligible_attachment_ids.c.id)))
            .group_by(func.date(Attachment.uploaded_at))
        )
    ).all()

    return {
        "summary": {
            "submitted": respondents_with_screenshots,
            "total_respondents": respondents_total,
            "pct": round(respondents_with_screenshots / respondents_total * 100, 1) if respondents_total else 0.0,
        },
        "by_platform": _as_breakdown(by_platform_rows),
        "by_city": _as_breakdown(by_city_rows),
        "by_date": _as_breakdown([(str(d), c) for d, c in by_date_rows]),
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [
            {
                "attachment_id": att.id,
                "survey_id": att.survey.human_code,
                "city": att.survey.city.name,
                "platform": att.platform.name if att.platform else None,
                "sequence_number": att.sequence_number,
                "description": att.description,
                "uploaded_at": att.uploaded_at,
                "cached_locally": att.local_path is not None,
            }
            for att in rows
        ],
    }


@router.get("/screenshots/{attachment_id}/image")
async def screenshot_image(attachment_id: int, db: AsyncSession = Depends(get_db)):
    """Serves the cached image file if one exists. The bot downloads every
    uploaded screenshot to `settings.screenshot_storage_dir` as it's
    received (see infrastructure/storage.py) and records the resulting
    path on `Attachment.local_path`; this endpoint just serves that file.
    A missing/failed download (network hiccup, or a screenshot uploaded
    before this existed) is reported as a plain 404 rather than pretending
    an image exists.
    """
    attachment = await db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="No such attachment")
    if not attachment.local_path or not os.path.isfile(attachment.local_path):
        raise HTTPException(
            status_code=404,
            detail=(
                "This screenshot is not cached locally yet (only its metadata is available). "
                f"Telegram file_id: {attachment.telegram_file_id}"
            ),
        )
    return FileResponse(attachment.local_path, media_type=attachment.mime_type or "application/octet-stream")


# ---- EXPORT -----------------------------------------------------------------------


@router.get("/export/excel")
async def export_excel(
    filters: DashboardFilters = Depends(),
    include_incomplete: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    workbook = await ExportService(db).build_workbook(filters.to_export_filters(include_incomplete=include_incomplete))
    data = workbook_to_bytes(workbook)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="survey_export.xlsx"'},
    )


@router.get("/export/csv/{sheet_key}")
async def export_csv(
    sheet_key: str,
    filters: DashboardFilters = Depends(),
    include_incomplete: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    try:
        csv_text = await ExportService(db).build_csv(
            sheet_key, filters.to_export_filters(include_incomplete=include_incomplete)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{sheet_key}.csv"'},
    )
