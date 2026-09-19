"""Excel/CSV export layer over completed surveys.

Read-only, like statistics_service.py — one query loads the full survey
graph (eager-loaded, no N+1s), and each sheet is built by walking it in
memory. The Excel workbook and every CSV export are built from the exact
same per-sheet row-builder functions, so there is one definition of "what
a row of the Earnings sheet looks like," not two.

Design rules, applied consistently across every sheet:
  - One row per observation (one Earnings row per survey+platform, one
    RideCategoryUsage row per survey+platform+category, ...) — never one
    row per survey with platform columns crammed sideways.
  - No merged cells, anywhere.
  - Every per-platform/per-observation sheet repeats survey_id, city,
    survey date, and platform as leading columns, so each sheet is
    independently filterable/pivotable in Excel without a VLOOKUP back to
    Survey Summary.
  - Missing values are written as blank cells (None), never "N/A", never 0.
  - Percentages and currency amounts are written as plain numbers (int/
    float), never as strings with a "%" or currency symbol baked in — the
    column header carries the unit instead, so the values stay usable for
    SUM/AVERAGE/charts directly in Excel.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from telegram_bot.application.statistics_service import StatisticsFilters, StatisticsService
from telegram_bot.domain.enums import Language, SurveyStatus
from telegram_bot.infrastructure.db.models import (
    Attachment,
    DriverEmployment,
    Earnings,
    Payment,
    RideCategoryUsage,
    Survey,
    SurveyPlatform,
)

Row = list[Any]


@dataclass(frozen=True)
class SheetData:
    key: str
    title: str
    headers: list[str]
    rows: list[Row]


@dataclass(frozen=True)
class ExportFilters:
    city_id: int | None = None
    platform_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    language: str | None = None
    include_incomplete: bool = False
    """Completed surveys only by default — "incomplete surveys are
    excluded unless explicitly requested"."""


# ---- value coercion helpers -------------------------------------------------


def _num(value: Any) -> int | float | None:
    """Decimal -> float so openpyxl (and CSV readers) see a plain number,
    never a string. None stays None -> a blank cell, never 0."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _yes_no(value: bool | None) -> str | None:
    if value is None:
        return None
    return "yes" if value else "no"


def _enum_label(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


def _dt(value):
    """openpyxl (and, by extension, a real .xlsx file) rejects
    timezone-aware datetimes outright — strip tzinfo so the cell keeps its
    native Excel date/time type (sortable, filterable) instead of falling
    back to a plain string. All our timestamps are stored in UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=None)


def _platform_label(sp: SurveyPlatform) -> str:
    return sp.platform_other_name or sp.platform.name


# ---- service ------------------------------------------------------------------


class ExportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_sheets(self, filters: ExportFilters | None = None) -> list[SheetData]:
        filters = filters or ExportFilters()
        surveys = await self._load_surveys(filters)
        stats_filters = StatisticsFilters(
            city_id=filters.city_id,
            platform_id=filters.platform_id,
            date_from=filters.date_from,
            date_to=filters.date_to,
        )
        report = await StatisticsService(self.db).generate_report(stats_filters)

        return [
            _survey_summary_sheet(surveys),
            _driver_platforms_sheet(surveys),
            _working_statistics_sheet(surveys),
            _earnings_sheet(surveys),
            _bonuses_sheet(surveys),
            _payments_sheet(surveys),
            _categories_sheet(surveys),
            _market_intelligence_sheet(surveys),
            _driver_experience_sheet(surveys),
            _screenshots_sheet(surveys),
            _raw_answers_sheet(surveys),
            _statistics_sheet(report),
        ]

    async def build_workbook(self, filters: ExportFilters | None = None) -> Workbook:
        sheets = await self.build_sheets(filters)
        workbook = Workbook()
        workbook.remove(workbook.active)  # default blank sheet
        for sheet in sheets:
            _write_worksheet(workbook.create_sheet(sheet.title), sheet)
        return workbook

    async def build_csv(self, sheet_key: str, filters: ExportFilters | None = None) -> str:
        sheets = await self.build_sheets(filters)
        by_key = {sheet.key: sheet for sheet in sheets}
        if sheet_key not in by_key:
            raise ValueError(f"Unknown sheet key: {sheet_key!r}. Valid keys: {sorted(by_key)}")
        return _sheet_to_csv(by_key[sheet_key])

    async def get_survey_detail(self, human_code: str) -> dict[str, Any] | None:
        """The "Raw Data" single-survey view: every structured sheet's row
        for this one survey, reusing the exact same row-builder functions
        as the workbook export so the two never drift apart.
        """
        survey = await self._load_survey_by_human_code(human_code)
        if survey is None:
            return None
        one = [survey]
        return {
            "summary": _first_row_dict(_survey_summary_sheet(one)),
            "platforms": _rows_as_dicts(_driver_platforms_sheet(one)),
            "working_statistics": _first_row_dict(_working_statistics_sheet(one)),
            "earnings": _rows_as_dicts(_earnings_sheet(one)),
            "bonuses": _rows_as_dicts(_bonuses_sheet(one)),
            "payments": _rows_as_dicts(_payments_sheet(one)),
            "categories": _rows_as_dicts(_categories_sheet(one)),
            "market_intelligence": _rows_as_dicts(_market_intelligence_sheet(one)),
            "driver_experience": _first_row_dict(_driver_experience_sheet(one)),
            "screenshots": _rows_as_dicts(_screenshots_sheet(one)),
            "raw_answers": _rows_as_dicts(_raw_answers_sheet(one)),
        }

    # ---- data loading -----------------------------------------------------

    @staticmethod
    def _eager_load_options() -> list:
        """The full survey-graph eager-load chain, shared by every loader
        below so the "what does a fully-loaded Survey look like" answer
        lives in exactly one place."""
        return [
            selectinload(Survey.interviewer),
            selectinload(Survey.city),
            selectinload(Survey.target_platform),
            selectinload(Survey.perceived_market_leader_platform),
            selectinload(Survey.best_experience_platform),
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.platform),
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.switch_frequency),
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.earnings).selectinload(
                Earnings.earnings_basis
            ),
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.bonus),
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.payment).selectinload(
                Payment.payout_method
            ),
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.market_intelligence),
            selectinload(Survey.survey_platforms)
            .selectinload(SurveyPlatform.ride_category_usages)
            .selectinload(RideCategoryUsage.category),
            selectinload(Survey.working_stats),
            selectinload(Survey.driver_employment).selectinload(DriverEmployment.driver_type),
            selectinload(Survey.driver_experience),
            selectinload(Survey.attachments).selectinload(Attachment.platform),
        ]

    async def _load_survey_by_human_code(self, human_code: str) -> Survey | None:
        """For the "Raw Data: view an individual completed survey"
        dashboard feature — only ever returns a COMPLETED survey; a
        draft/abandoned one (or an unknown code) is treated as not found.
        """
        stmt = (
            select(Survey)
            .options(*self._eager_load_options())
            .where(Survey.human_code == human_code, Survey.status == SurveyStatus.COMPLETED)
        )
        result = await self.db.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def _load_surveys(self, filters: ExportFilters) -> list[Survey]:
        stmt = (
            select(Survey)
            .options(*self._eager_load_options())
            .order_by(Survey.survey_datetime)
        )
        if not filters.include_incomplete:
            stmt = stmt.where(Survey.status == SurveyStatus.COMPLETED)
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
                Survey.id.in_(
                    select(SurveyPlatform.survey_id).where(SurveyPlatform.platform_id == filters.platform_id)
                )
            )
        result = await self.db.execute(stmt)
        return list(result.unique().scalars().all())


# ---- sheet builders -----------------------------------------------------------
#
# Each function returns a SheetData for one survey graph. They're free
# functions (not methods) because they do no I/O — they just walk objects
# already loaded by _load_surveys, which keeps them trivially unit-testable
# without a database if needed later.


def _survey_summary_sheet(surveys: list[Survey]) -> SheetData:
    headers = [
        "survey_id", "internal_id", "status", "language", "city", "interviewer",
        "survey_date", "started_at", "completed_at",
        "target_platform", "driver_type", "fleet_name", "employment_text",
        "total_weekly_earnings_amount", "total_weekly_earnings_currency",
        "perceived_market_leader", "market_leader_text", "best_experience_platform",
        "screenshots_offered",
    ]
    rows: list[Row] = []
    for s in surveys:
        de = s.driver_employment
        rows.append(
            [
                s.human_code,
                str(s.id),
                s.status.value,
                _enum_label(s.language),
                s.city.name,
                s.interviewer.full_name,
                _dt(s.survey_datetime),
                _dt(s.started_at),
                _dt(s.completed_at),
                s.target_platform.name if s.target_platform else None,
                de.driver_type.name if de and de.driver_type else None,
                de.fleet_name if de else None,
                de.employment_text if de else None,
                _num(s.total_weekly_earnings_amount),
                s.total_weekly_earnings_currency,
                s.perceived_market_leader_platform.name if s.perceived_market_leader_platform else None,
                s.market_leader_text,
                s.best_experience_platform.name if s.best_experience_platform else None,
                _yes_no(s.screenshots_offered),
            ]
        )
    return SheetData("survey_summary", "Survey Summary", headers, rows)


def _driver_platforms_sheet(surveys: list[Survey]) -> SheetData:
    headers = [
        "survey_id", "city", "survey_date", "platform",
        "works_with_platform", "is_exclusive", "switch_frequency",
        "experience_with_platform_months", "has_loyalty_program", "loyalty_program_notes",
    ]
    rows: list[Row] = []
    for s in surveys:
        for sp in s.survey_platforms:
            rows.append(
                [
                    s.human_code, s.city.name, _dt(s.survey_datetime), _platform_label(sp),
                    _yes_no(sp.works_with_platform), _yes_no(sp.is_exclusive),
                    sp.switch_frequency.name if sp.switch_frequency else None,
                    sp.experience_with_platform_months,
                    _yes_no(sp.has_loyalty_program), sp.loyalty_program_notes,
                ]
            )
    return SheetData("driver_platforms", "Driver Platforms", headers, rows)


def _working_statistics_sheet(surveys: list[Survey]) -> SheetData:
    headers = ["survey_id", "city", "survey_date", "days_per_week", "hours_per_day", "trips_per_day", "trips_per_week"]
    rows: list[Row] = []
    for s in surveys:
        ws = s.working_stats
        rows.append(
            [
                s.human_code, s.city.name, _dt(s.survey_datetime),
                ws.days_per_week if ws else None,
                _num(ws.hours_per_day) if ws else None,
                ws.trips_per_day if ws else None,
                ws.trips_per_week if ws else None,
            ]
        )
    return SheetData("working_statistics", "Working Statistics", headers, rows)


def _earnings_sheet(surveys: list[Survey]) -> SheetData:
    headers = [
        "survey_id", "city", "survey_date", "platform",
        "weekly_earnings_amount", "currency", "earnings_basis", "commission_pct",
    ]
    rows: list[Row] = []
    for s in surveys:
        for sp in s.survey_platforms:
            e = sp.earnings
            if e is None:
                continue
            rows.append(
                [
                    s.human_code, s.city.name, _dt(s.survey_datetime), _platform_label(sp),
                    _num(e.weekly_earnings_amount), e.weekly_earnings_currency,
                    e.earnings_basis.name if e.earnings_basis else None,
                    _num(e.commission_pct),
                ]
            )
    return SheetData("earnings", "Earnings", headers, rows)


def _bonuses_sheet(surveys: list[Survey]) -> SheetData:
    headers = [
        "survey_id", "city", "survey_date", "platform",
        "receives_bonuses", "bonus_period", "required_trips",
        "bonus_value_amount", "currency", "description",
    ]
    rows: list[Row] = []
    for s in surveys:
        for sp in s.survey_platforms:
            b = sp.bonus
            if b is None:
                continue
            rows.append(
                [
                    s.human_code, s.city.name, _dt(s.survey_datetime), _platform_label(sp),
                    _yes_no(b.receives_bonuses), _enum_label(b.bonus_period), b.required_trips,
                    _num(b.bonus_value_amount), b.bonus_value_currency, b.description,
                ]
            )
    return SheetData("bonuses", "Bonuses", headers, rows)


def _payments_sheet(surveys: list[Survey]) -> SheetData:
    headers = [
        "survey_id", "city", "survey_date", "platform",
        "cash_pct", "digital_pct", "payout_method",
        "early_cashout_available", "cashout_fee_amount", "cashout_fee_is_percentage",
    ]
    rows: list[Row] = []
    for s in surveys:
        for sp in s.survey_platforms:
            p = sp.payment
            if p is None:
                continue
            rows.append(
                [
                    s.human_code, s.city.name, _dt(s.survey_datetime), _platform_label(sp),
                    p.cash_pct, p.digital_pct,
                    p.payout_method.name if p.payout_method else None,
                    _yes_no(p.early_cashout_available), _num(p.cashout_fee_amount),
                    _yes_no(p.cashout_fee_is_percentage),
                ]
            )
    return SheetData("payments", "Payments", headers, rows)


def _categories_sheet(surveys: list[Survey]) -> SheetData:
    headers = [
        "survey_id", "city", "survey_date", "platform",
        "category", "trip_share_pct", "is_main_category", "category_requirements",
    ]
    rows: list[Row] = []
    for s in surveys:
        for sp in s.survey_platforms:
            for rcu in sp.ride_category_usages:
                rows.append(
                    [
                        s.human_code, s.city.name, _dt(s.survey_datetime), _platform_label(sp),
                        rcu.category.name, rcu.trip_share_pct, _yes_no(rcu.is_main_category),
                        rcu.category_requirements,
                    ]
                )
    return SheetData("categories", "Categories", headers, rows)


def _market_intelligence_sheet(surveys: list[Survey]) -> SheetData:
    headers = [
        "survey_id", "city", "survey_date", "platform",
        "estimated_driver_count", "competitor_rider_discounts_notes", "demand_promotion_spending_notes",
    ]
    rows: list[Row] = []
    for s in surveys:
        for sp in s.survey_platforms:
            mi = sp.market_intelligence
            if mi is None:
                continue
            rows.append(
                [
                    s.human_code, s.city.name, _dt(s.survey_datetime), _platform_label(sp),
                    mi.estimated_driver_count, mi.competitor_rider_discounts_notes,
                    mi.demand_promotion_spending_notes,
                ]
            )
    return SheetData("market_intelligence", "Market Intelligence", headers, rows)


def _driver_experience_sheet(surveys: list[Survey]) -> SheetData:
    headers = [
        "survey_id", "city", "survey_date", "internet_reliability",
        "improvement_suggestions", "reason_to_join_new_platform", "seasonal_behavior_notes",
    ]
    rows: list[Row] = []
    for s in surveys:
        de = s.driver_experience
        rows.append(
            [
                s.human_code, s.city.name, _dt(s.survey_datetime),
                _enum_label(de.internet_reliability) if de else None,
                de.improvement_suggestions if de else None,
                de.reason_to_join_new_platform if de else None,
                de.seasonal_behavior_notes if de else None,
            ]
        )
    return SheetData("driver_experience", "Driver Experience", headers, rows)


def _screenshots_sheet(surveys: list[Survey]) -> SheetData:
    headers = [
        "survey_id", "city", "survey_date", "platform", "sequence_number",
        "file_type", "telegram_file_id", "telegram_file_unique_id",
        "description", "uploaded_at", "attachment_id", "cached_locally",
    ]
    rows: list[Row] = []
    for s in surveys:
        for att in s.attachments:
            rows.append(
                [
                    s.human_code, s.city.name, _dt(s.survey_datetime),
                    att.platform.name if att.platform else None, att.sequence_number,
                    _enum_label(att.file_type), att.telegram_file_id, att.telegram_file_unique_id,
                    att.description, _dt(att.uploaded_at),
                    att.id, _yes_no(att.local_path is not None),
                ]
            )
    return SheetData("screenshots", "Screenshots", headers, rows)


def _raw_answers_sheet(surveys: list[Survey]) -> SheetData:
    """Every free-text (qualitative) answer in the schema, consolidated
    into one long/tidy table for audit — verbatim, never summarized or
    truncated. Complements (doesn't replace) the same text appearing
    inline on its originating sheet above.
    """
    headers = ["survey_id", "city", "survey_date", "platform", "field", "raw_text"]
    rows: list[Row] = []

    def emit(s: Survey, platform: str | None, field: str, text: str | None) -> None:
        if text:
            rows.append([s.human_code, s.city.name, _dt(s.survey_datetime), platform, field, text])

    for s in surveys:
        de = s.driver_experience
        if de:
            emit(s, None, "improvement_suggestions", de.improvement_suggestions)
            emit(s, None, "reason_to_join_new_platform", de.reason_to_join_new_platform)
            emit(s, None, "seasonal_behavior_notes", de.seasonal_behavior_notes)
        if s.driver_employment:
            emit(s, None, "fleet_name", s.driver_employment.fleet_name)
        for sp in s.survey_platforms:
            label = _platform_label(sp)
            emit(s, label, "platform_other_name", sp.platform_other_name)
            emit(s, label, "loyalty_program_notes", sp.loyalty_program_notes)
            if sp.bonus:
                emit(s, label, "bonus_description", sp.bonus.description)
            if sp.market_intelligence:
                emit(s, label, "competitor_rider_discounts_notes", sp.market_intelligence.competitor_rider_discounts_notes)
                emit(s, label, "demand_promotion_spending_notes", sp.market_intelligence.demand_promotion_spending_notes)
            for rcu in sp.ride_category_usages:
                emit(s, label, "category_requirements", rcu.category_requirements)
        for att in s.attachments:
            emit(s, att.platform.name if att.platform else None, "screenshot_description", att.description)

    return SheetData("raw_answers", "Raw Answers", headers, rows)


def _statistics_sheet(report) -> SheetData:
    """Renders the StatisticsService report as two stacked tables on one
    sheet: numeric metrics (count/avg/median/min/max) and category
    breakdowns (count/percentage) — separated by a blank row, not merged
    cells. Driver estimates are labeled as such, never mixed into the
    sample-statistics rows.
    """
    headers = ["section", "metric", "category", "count", "average", "median", "minimum", "maximum", "percentage"]
    rows: list[Row] = []

    def numeric_row(section: str, metric: str, summary) -> None:
        rows.append([section, metric, None, summary.count, summary.average, summary.median, summary.minimum, summary.maximum, None])

    def breakdown_rows(section: str, metric: str, breakdown) -> None:
        pct = breakdown.percentages
        for category, count in breakdown.counts.items():
            rows.append([section, metric, category, count, None, None, None, None, pct.get(category)])

    rows.append(["general", "total_surveys", None, report.general.total_surveys, None, None, None, None, None])
    rows.append(["general", "completed_surveys", None, report.general.completed_surveys, None, None, None, None, None])
    breakdown_rows("general", "surveys_by_city", report.general.surveys_by_city)
    breakdown_rows("general", "surveys_by_platform", report.general.surveys_by_platform)
    breakdown_rows("general", "surveys_by_date", report.general.surveys_by_date)

    for field in ("days_per_week", "hours_per_day", "trips_per_day", "trips_per_week"):
        numeric_row("working_pattern", field, getattr(report.working_pattern, field))

    numeric_row("earnings", "weekly_earnings", report.earnings.weekly_earnings)
    for platform, summary in report.earnings.earnings_by_platform.items():
        numeric_row("earnings", f"earnings_by_platform[{platform}]", summary)
    for city_name, summary in report.earnings.earnings_by_city.items():
        numeric_row("earnings", f"earnings_by_city[{city_name}]", summary)
    numeric_row("earnings", "earnings_per_trip", report.earnings.earnings_per_trip)
    numeric_row("earnings", "earnings_per_hour", report.earnings.earnings_per_hour)

    numeric_row("commission", "commission_pct", report.commission.commission_pct)
    for platform, summary in report.commission.commission_by_platform.items():
        numeric_row("commission", f"commission_by_platform[{platform}]", summary)

    numeric_row("payments", "cash_pct", report.payments.cash_pct)
    numeric_row("payments", "digital_pct", report.payments.digital_pct)

    breakdown_rows("bonuses", "receives_bonuses", report.bonuses.receives_bonuses)
    numeric_row("bonuses", "bonus_value", report.bonuses.bonus_value)
    numeric_row("bonuses", "bonus_trip_threshold", report.bonuses.bonus_trip_threshold)

    breakdown_rows("multi_app", "platform_count_bucket", report.multi_app.platform_count_bucket)
    breakdown_rows("multi_app", "platform_combinations", report.multi_app.platform_combinations)

    breakdown_rows("driver_type", "distribution", report.driver_type.distribution)

    breakdown_rows("categories", "category_distribution", report.categories.category_distribution)
    breakdown_rows("categories", "main_category_distribution", report.categories.main_category_distribution)

    breakdown_rows("sample_market", "internet_reliability", report.sample_market.internet_reliability)

    breakdown_rows("driver_estimates (NOT verified market data)", "perceived_market_leader", report.driver_estimates.perceived_market_leader)
    numeric_row("driver_estimates (NOT verified market data)", "estimated_driver_count", report.driver_estimates.estimated_driver_count)
    breakdown_rows(
        "driver_estimates (NOT verified market data)",
        "seasonality_text_classification",
        report.driver_estimates.seasonality_text_classification,
    )

    return SheetData("statistics", "Statistics", headers, rows)


# ---- rendering ------------------------------------------------------------------

_PERCENT_COLUMNS = {"commission_pct", "cash_pct", "digital_pct", "trip_share_pct", "percentage"}
_CURRENCY_COLUMNS = {
    "weekly_earnings_amount", "bonus_value_amount", "cashout_fee_amount",
    "total_weekly_earnings_amount",
}


def _write_worksheet(ws: Worksheet, sheet: SheetData) -> None:
    ws.append(sheet.headers)
    for row in sheet.rows:
        ws.append(row)
    for col_idx, header in enumerate(sheet.headers, start=1):
        letter = ws.cell(row=1, column=col_idx).column_letter
        if header in _PERCENT_COLUMNS:
            fmt = "0.0"
        elif header in _CURRENCY_COLUMNS:
            fmt = "#,##0"
        else:
            continue
        for cell in ws[letter][1:]:
            if isinstance(cell.value, (int, float)):
                cell.number_format = fmt


def workbook_to_bytes(workbook: Workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _sheet_to_csv(sheet: SheetData) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(sheet.headers)
    for row in sheet.rows:
        writer.writerow(["" if value is None else value for value in row])
    return buffer.getvalue()


def _rows_as_dicts(sheet: SheetData) -> list[dict[str, Any]]:
    return [dict(zip(sheet.headers, row)) for row in sheet.rows]


def _first_row_dict(sheet: SheetData) -> dict[str, Any] | None:
    rows = _rows_as_dicts(sheet)
    return rows[0] if rows else None
