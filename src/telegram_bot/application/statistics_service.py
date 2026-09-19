"""Statistics/analytics layer over completed surveys.

Read-only: nothing here writes to the database. `StatisticsService` builds
its queries directly against the schema built for the survey flow — no new
tables, no duplicated aggregation logic living somewhere else.

Two rules shape every query in this file:

1. **Missing values are never treated as zero.** A driver who skipped a
   question is excluded from that metric's calculation entirely, not
   averaged in as 0. SQL's own AVG/PERCENTILE_CONT/MIN/MAX/COUNT(column)
   already ignore NULLs, which is exactly the behavior we want — the
   discipline here is to never write `COALESCE(column, 0)` or filter with
   `IS NOT NULL` in a way that would silently change to what population is
   being measured. Every `MetricSummary.count` tells you how many
   responses the number is actually based on.

2. **Sample statistics vs. driver estimates are kept in separate report
   sections, never merged.** `StatisticsReport.sample_market` holds facts
   about the respondents themselves (their own internet reliability);
   `StatisticsReport.driver_estimates` holds their opinions/guesses about
   the wider market (perceived market leader, estimated driver counts, and
   a best-effort classification of free-text seasonality notes) — these
   are subjective and must never be presented as verified market figures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_bot.domain.enums import Language, SurveyStatus
from telegram_bot.infrastructure.db.models import (
    Bonus,
    City,
    DriverEmployment,
    DriverExperience,
    DriverType,
    Earnings,
    MarketIntelligence,
    Payment,
    Platform,
    RideCategory,
    RideCategoryUsage,
    Survey,
    SurveyPlatform,
    WorkingStats,
)

# ---- result shapes ---------------------------------------------------------


@dataclass(frozen=True)
class MetricSummary:
    """count/average/median/minimum/maximum for one numeric metric,
    computed over only the responses that had a value."""

    count: int
    average: float | None
    median: float | None
    minimum: float | None
    maximum: float | None

    @classmethod
    def empty(cls) -> MetricSummary:
        return cls(count=0, average=None, median=None, minimum=None, maximum=None)


@dataclass(frozen=True)
class CategoryBreakdown:
    """Counts per category, plus each category's share of the responses
    that had a value (`total`) — not necessarily the share of every survey
    in scope, since some surveys may not have answered this question."""

    counts: dict[str, int]
    total: int

    @property
    def percentages(self) -> dict[str, float]:
        if self.total == 0:
            return {key: 0.0 for key in self.counts}
        return {key: round(value / self.total * 100, 1) for key, value in self.counts.items()}

    @classmethod
    def empty(cls) -> CategoryBreakdown:
        return cls(counts={}, total=0)


@dataclass(frozen=True)
class StatisticsFilters:
    city_id: int | None = None
    platform_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    driver_type_id: int | None = None
    language: str | None = None
    """"en" / "ru" / "uz" — which language the interview was conducted in.
    Purely a filter over Survey.language; every underlying metric is
    language-independent (see domain/i18n.py's design note), so this never
    changes what a metric *means*, only which surveys contribute to it."""


# ---- per-section report shapes ---------------------------------------------


@dataclass(frozen=True)
class GeneralStats:
    total_surveys: int
    completed_surveys: int
    surveys_by_city: CategoryBreakdown
    surveys_by_platform: CategoryBreakdown
    surveys_by_date: CategoryBreakdown
    surveys_by_language: CategoryBreakdown


@dataclass(frozen=True)
class WorkingPatternStats:
    days_per_week: MetricSummary
    hours_per_day: MetricSummary
    trips_per_day: MetricSummary
    trips_per_week: MetricSummary
    hours_per_day_bucket: CategoryBreakdown
    """The bucket the driver actually picked (current questionnaire's
    fixed options) — hours_per_day above is a derived numeric midpoint of
    this, kept for backward-compatible AVG/MEDIAN statistics."""


@dataclass(frozen=True)
class EarningsStats:
    weekly_earnings: MetricSummary
    earnings_by_platform: dict[str, MetricSummary]
    earnings_by_city: dict[str, MetricSummary]
    earnings_per_trip: MetricSummary
    earnings_per_hour: MetricSummary


@dataclass(frozen=True)
class CommissionStats:
    commission_pct: MetricSummary
    commission_by_platform: dict[str, MetricSummary]


@dataclass(frozen=True)
class PaymentStats:
    cash_pct: MetricSummary
    digital_pct: MetricSummary


@dataclass(frozen=True)
class BonusStats:
    receives_bonuses: CategoryBreakdown
    bonus_value: MetricSummary
    bonus_trip_threshold: MetricSummary


@dataclass(frozen=True)
class MultiAppStats:
    platform_count_bucket: CategoryBreakdown  # "one platform" / "multiple platforms"
    platform_combinations: CategoryBreakdown
    exclusivity: CategoryBreakdown  # "yes" / "no", from survey_platforms.is_exclusive (asked for single-platform drivers)


@dataclass(frozen=True)
class DriverTypeStats:
    distribution: CategoryBreakdown  # independent / fleet / other — legacy choice-based surveys
    employment_text_classification: CategoryBreakdown
    """Best-effort keyword classification of the current questionnaire's
    open-text "fleet company or independent driver?" answer (see
    classify_driver_type below) — not a tabulated choice, so treat as
    illustrative only, same caveat as DriverEstimates.seasonality_text_classification."""


@dataclass(frozen=True)
class CategoryUsageStats:
    category_distribution: CategoryBreakdown
    main_category_distribution: CategoryBreakdown


@dataclass(frozen=True)
class SampleMarketStats:
    """Facts about the respondents themselves — safe to report as sample
    statistics."""

    internet_reliability: CategoryBreakdown


@dataclass(frozen=True)
class DriverEstimates:
    """Subjective driver opinions/guesses about the broader market. Report
    these as "surveyed drivers estimate/believe ...", never as verified
    market figures — they are not audited, and different drivers' guesses
    are not directly comparable the way a self-reported fact (e.g. "my
    commission is 20%") is.
    """

    perceived_market_leader: CategoryBreakdown
    estimated_driver_count: MetricSummary
    seasonality_text_classification: CategoryBreakdown
    """Best-effort keyword classification of the legacy free-text
    seasonal_behavior_notes answer (see classify_seasonality below) — kept
    for older surveys, which had no structured "same/more in summer/more
    in winter" question. Treat as illustrative only.
    """
    seasonal_pattern: CategoryBreakdown
    """The current questionnaire's structured single-choice answer to the
    same question — a direct tabulation, not a heuristic guess, for
    surveys answered after the questionnaire replacement."""


@dataclass(frozen=True)
class StatisticsReport:
    filters: StatisticsFilters
    general: GeneralStats
    working_pattern: WorkingPatternStats
    earnings: EarningsStats
    commission: CommissionStats
    payments: PaymentStats
    bonuses: BonusStats
    multi_app: MultiAppStats
    driver_type: DriverTypeStats
    categories: CategoryUsageStats
    sample_market: SampleMarketStats
    driver_estimates: DriverEstimates


# ---- best-effort free-text classification (seasonality, driver type) -------
#
# Keyword lists are per-concept, not per-language: a survey's free text is
# in whichever of the three questionnaire languages (en/ru/uz) the driver
# used, so each concept's tuple below just carries the equivalent word(s)
# from all three rather than branching on language. Kept short and literal
# on purpose — see classify_driver_type's docstring for why.

_APOSTROPHE_VARIANTS = ("’", "ʻ", "ʼ", "`")


def _normalize(text: str) -> str:
    """Lowercase, and fold the several Unicode characters Uzbek Latin text
    commonly uses for the o'/g' apostrophe (curly quote, modifier letters,
    backtick — depends on the driver's keyboard/input method) down to a
    plain "'", so e.g. "o'z mashinasi" keyword-matches regardless of which
    one the driver actually typed."""
    lowered = text.lower()
    for variant in _APOSTROPHE_VARIANTS:
        lowered = lowered.replace(variant, "'")
    return lowered


_SUMMER_KEYWORDS = (
    "summer", "hot season", "hot months",  # en
    "лето", "летом", "летний",  # ru
    "yoz", "yozda", "yozgi",  # uz
)
_WINTER_KEYWORDS = (
    "winter", "cold season", "cold months", "snow",  # en
    "зима", "зимой", "зимний", "снег",  # ru
    "qish", "qishda", "qishki", "qor",  # uz
)
_SAME_KEYWORDS = (
    "same", "no change", "no difference", "consistent", "year-round", "year round", "steady",
    "doesn't change", "does not change",  # en
    "одинаково", "без изменений", "круглый год", "стабильно", "не меняется",  # ru
    "bir xil", "o'zgarmaydi", "barqaror", "yil davomida bir xil",  # uz
)


def classify_seasonality(text: str | None) -> str | None:
    """Heuristic-only: looks for a handful of English/Russian/Uzbek keywords
    in a free-text answer. Returns None for blank/unanswered text,
    "unclassified" when the text doesn't match any keyword set, or when it
    matches more than one (ambiguous text is never guessed at — conservative
    by design). Not a substitute for a real structured question — legacy
    free-text surveys only; new surveys have a structured
    DriverExperience.seasonal_pattern answer instead, which is always
    authoritative and never overridden by this heuristic — see
    DriverEstimates.seasonality_text_classification vs. .seasonal_pattern.
    """
    if not text or not text.strip():
        return None
    lowered = _normalize(text)
    mentions_summer = any(keyword in lowered for keyword in _SUMMER_KEYWORDS)
    mentions_winter = any(keyword in lowered for keyword in _WINTER_KEYWORDS)
    mentions_same = any(keyword in lowered for keyword in _SAME_KEYWORDS)
    if mentions_summer and not mentions_winter:
        return "more_in_summer"
    if mentions_winter and not mentions_summer:
        return "more_in_winter"
    if mentions_same and not (mentions_summer or mentions_winter):
        return "same_year_round"
    return "unclassified"


# ---- best-effort free-text driver-type classification ----------------------

_FLEET_KEYWORDS = (
    "fleet", "taxi park", "company car", "rented", "rent a car", "agency",  # en
    "парк", "автопарк", "таксопарк", "водитель автопарка",  # ru
    "avtopark", "taksopark", "park",  # uz
)
_INDEPENDENT_KEYWORDS = (
    "independent", "own car", "own vehicle", "my own", "myself", "self-employed", "no fleet", "not fleet",  # en
    "самостоятельно", "независимый", "собственный автомобиль",  # ru
    "mustaqil", "o'z avtomobili", "o'z mashinasi",  # uz
)


def classify_driver_type(text: str | None) -> str | None:
    """Heuristic-only keyword classification of the current questionnaire's
    open-text "fleet company or independent driver?" answer — mirrors
    classify_seasonality's approach and caveats exactly (approximate, not a
    substitute for a structured choice; conservative — anything ambiguous
    or with no clear keyword match comes back "unclassified" rather than a
    guess). Returns None for blank text.
    """
    if not text or not text.strip():
        return None
    lowered = _normalize(text)
    mentions_fleet = any(keyword in lowered for keyword in _FLEET_KEYWORDS)
    mentions_independent = any(keyword in lowered for keyword in _INDEPENDENT_KEYWORDS)
    if mentions_fleet and not mentions_independent:
        return "fleet"
    if mentions_independent and not mentions_fleet:
        return "independent"
    return "unclassified"


# ---- service ----------------------------------------------------------------


class StatisticsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def generate_report(self, filters: StatisticsFilters | None = None) -> StatisticsReport:
        filters = filters or StatisticsFilters()
        return StatisticsReport(
            filters=filters,
            general=await self._general_stats(filters),
            working_pattern=await self._working_pattern_stats(filters),
            earnings=await self._earnings_stats(filters),
            commission=await self._commission_stats(filters),
            payments=await self._payment_stats(filters),
            bonuses=await self._bonus_stats(filters),
            multi_app=await self._multi_app_stats(filters),
            driver_type=await self._driver_type_stats(filters),
            categories=await self._category_stats(filters),
            sample_market=await self._sample_market_stats(filters),
            driver_estimates=await self._driver_estimates(filters),
        )

    # ---- shared filter plumbing --------------------------------------------

    def _eligible_survey_ids(self, filters: StatisticsFilters, *, completed_only: bool = True) -> Select:
        """A `SELECT surveys.id` scoped by every filter except `platform_id`
        for tables that aren't platform-scoped themselves — those still need
        the EXISTS-style platform join below since a survey has zero or more
        platforms, not a column of its own.
        """
        stmt = select(Survey.id)
        if completed_only:
            stmt = stmt.where(Survey.status == SurveyStatus.COMPLETED)
        if filters.city_id is not None:
            stmt = stmt.where(Survey.city_id == filters.city_id)
        if filters.language is not None:
            stmt = stmt.where(Survey.language == Language(filters.language))
        if filters.date_from is not None:
            stmt = stmt.where(Survey.survey_datetime >= filters.date_from)
        if filters.date_to is not None:
            stmt = stmt.where(Survey.survey_datetime <= filters.date_to)
        if filters.driver_type_id is not None:
            stmt = stmt.where(
                Survey.id.in_(
                    select(DriverEmployment.survey_id).where(
                        DriverEmployment.driver_type_id == filters.driver_type_id
                    )
                )
            )
        if filters.platform_id is not None:
            stmt = stmt.where(
                Survey.id.in_(
                    select(SurveyPlatform.survey_id).where(SurveyPlatform.platform_id == filters.platform_id)
                )
            )
        return stmt

    def _platform_scoped_conditions(self, table, filters: StatisticsFilters, eligible: Select) -> list:
        """survey_id/platform_id filter for the tables hanging off
        survey_platforms (Earnings, Bonus, Payment, MarketIntelligence,
        RideCategoryUsage): always scoped to the eligible survey pool, and
        additionally to one platform when that filter is set — otherwise a
        driver's platforms would each contribute independently even when
        the caller asked for just one of them.
        """
        conditions = [table.survey_id.in_(eligible)]
        if filters.platform_id is not None:
            conditions.append(table.platform_id == filters.platform_id)
        return conditions

    @staticmethod
    def _summary_columns(expr):
        return (
            func.count(expr),
            func.avg(expr),
            func.percentile_cont(0.5).within_group(expr),
            func.min(expr),
            func.max(expr),
        )

    @staticmethod
    def _summary_from_row(row) -> MetricSummary:
        count, avg, median, minimum, maximum = row
        return MetricSummary(
            count=count or 0,
            average=float(avg) if avg is not None else None,
            median=float(median) if median is not None else None,
            minimum=float(minimum) if minimum is not None else None,
            maximum=float(maximum) if maximum is not None else None,
        )

    async def _metric_summary(self, expr, *where) -> MetricSummary:
        stmt = select(*self._summary_columns(expr)).where(*where)
        row = (await self.db.execute(stmt)).one()
        return self._summary_from_row(row)

    async def _breakdown(self, label_col, *where, group_by=None) -> CategoryBreakdown:
        stmt = select(label_col, func.count()).where(*where).group_by(group_by if group_by is not None else label_col)
        rows = (await self.db.execute(stmt)).all()
        counts = {label: count for label, count in rows if label is not None}
        return CategoryBreakdown(counts=counts, total=sum(counts.values()))

    async def _metric_summary_by_group(self, expr, label_col, group_by, *where) -> dict[str, MetricSummary]:
        stmt = select(label_col, *self._summary_columns(expr)).where(*where).group_by(group_by)
        rows = (await self.db.execute(stmt)).all()
        return {label: self._summary_from_row(row) for label, *row in rows if label is not None}

    # ---- GENERAL ------------------------------------------------------------

    async def _general_stats(self, filters: StatisticsFilters) -> GeneralStats:
        all_ids = self._eligible_survey_ids(filters, completed_only=False)
        completed_ids = self._eligible_survey_ids(filters, completed_only=True)

        total_surveys = (await self.db.execute(select(func.count()).select_from(all_ids.subquery()))).scalar_one()
        completed_surveys = (
            await self.db.execute(select(func.count()).select_from(completed_ids.subquery()))
        ).scalar_one()

        by_city = await self._breakdown(
            City.name,
            Survey.id.in_(completed_ids),
            Survey.city_id == City.id,
        )
        by_platform = await self._breakdown(
            Platform.name,
            SurveyPlatform.survey_id.in_(completed_ids),
            SurveyPlatform.platform_id == Platform.id,
        )
        by_date = await self._breakdown(
            func.date(Survey.survey_datetime),
            Survey.id.in_(completed_ids),
            group_by=func.date(Survey.survey_datetime),
        )
        # Stringify date keys for a JSON-friendly, uniform breakdown shape.
        by_date = CategoryBreakdown(counts={str(k): v for k, v in by_date.counts.items()}, total=by_date.total)

        by_language = await self._breakdown(Survey.language, Survey.id.in_(completed_ids))
        by_language = CategoryBreakdown(
            counts={str(getattr(k, "value", k)): v for k, v in by_language.counts.items()},
            total=by_language.total,
        )

        return GeneralStats(
            total_surveys=total_surveys,
            completed_surveys=completed_surveys,
            surveys_by_city=by_city,
            surveys_by_platform=by_platform,
            surveys_by_date=by_date,
            surveys_by_language=by_language,
        )

    # ---- WORKING --------------------------------------------------------------

    async def _working_pattern_stats(self, filters: StatisticsFilters) -> WorkingPatternStats:
        eligible = self._eligible_survey_ids(filters)
        where = (WorkingStats.survey_id.in_(eligible),)
        hours_bucket = await self._breakdown(WorkingStats.hours_per_day_bucket, *where)
        hours_bucket = CategoryBreakdown(
            counts={str(getattr(k, "value", k)): v for k, v in hours_bucket.counts.items()},
            total=hours_bucket.total,
        )
        return WorkingPatternStats(
            days_per_week=await self._metric_summary(WorkingStats.days_per_week, *where),
            hours_per_day=await self._metric_summary(WorkingStats.hours_per_day, *where),
            trips_per_day=await self._metric_summary(WorkingStats.trips_per_day, *where),
            trips_per_week=await self._metric_summary(WorkingStats.trips_per_week, *where),
            hours_per_day_bucket=hours_bucket,
        )

    # ---- EARNINGS ---------------------------------------------------------------

    async def _earnings_stats(self, filters: StatisticsFilters) -> EarningsStats:
        eligible = self._eligible_survey_ids(filters)

        weekly_earnings = await self._metric_summary(
            Survey.total_weekly_earnings_amount, Survey.id.in_(eligible)
        )

        earnings_platform_where = self._platform_scoped_conditions(Earnings, filters, eligible) + [
            Earnings.platform_id == SurveyPlatform.platform_id,
            Earnings.survey_id == SurveyPlatform.survey_id,
            SurveyPlatform.platform_id == Platform.id,
        ]
        earnings_by_platform = await self._metric_summary_by_group(
            Earnings.weekly_earnings_amount, Platform.name, Platform.name, *earnings_platform_where
        )

        earnings_by_city = await self._metric_summary_by_group(
            Survey.total_weekly_earnings_amount,
            City.name,
            City.name,
            Survey.id.in_(eligible),
            Survey.city_id == City.id,
        )

        # Per-trip / per-hour are derived ratios, computed per survey (not
        # per platform) against the driver's total weekly earnings, guarding
        # against division by zero and against either side being missing.
        per_trip_expr = Survey.total_weekly_earnings_amount / WorkingStats.trips_per_week
        earnings_per_trip = await self._metric_summary(
            per_trip_expr,
            Survey.id.in_(eligible),
            Survey.id == WorkingStats.survey_id,
            Survey.total_weekly_earnings_amount.is_not(None),
            WorkingStats.trips_per_week.is_not(None),
            WorkingStats.trips_per_week > 0,
        )

        weekly_hours_expr = WorkingStats.hours_per_day * WorkingStats.days_per_week
        per_hour_expr = Survey.total_weekly_earnings_amount / weekly_hours_expr
        earnings_per_hour = await self._metric_summary(
            per_hour_expr,
            Survey.id.in_(eligible),
            Survey.id == WorkingStats.survey_id,
            Survey.total_weekly_earnings_amount.is_not(None),
            WorkingStats.hours_per_day.is_not(None),
            WorkingStats.days_per_week.is_not(None),
            weekly_hours_expr > 0,
        )

        return EarningsStats(
            weekly_earnings=weekly_earnings,
            earnings_by_platform=earnings_by_platform,
            earnings_by_city=earnings_by_city,
            earnings_per_trip=earnings_per_trip,
            earnings_per_hour=earnings_per_hour,
        )

    # ---- COMMISSION ---------------------------------------------------------------

    async def _commission_stats(self, filters: StatisticsFilters) -> CommissionStats:
        eligible = self._eligible_survey_ids(filters)
        commission = await self._metric_summary(
            Earnings.commission_pct, *self._platform_scoped_conditions(Earnings, filters, eligible)
        )
        by_platform_where = self._platform_scoped_conditions(Earnings, filters, eligible) + [
            Earnings.platform_id == Platform.id,
        ]
        commission_by_platform = await self._metric_summary_by_group(
            Earnings.commission_pct, Platform.name, Platform.name, *by_platform_where
        )
        return CommissionStats(commission_pct=commission, commission_by_platform=commission_by_platform)

    # ---- PAYMENTS ---------------------------------------------------------------

    async def _payment_stats(self, filters: StatisticsFilters) -> PaymentStats:
        eligible = self._eligible_survey_ids(filters)
        where = self._platform_scoped_conditions(Payment, filters, eligible)
        return PaymentStats(
            cash_pct=await self._metric_summary(Payment.cash_pct, *where),
            digital_pct=await self._metric_summary(Payment.digital_pct, *where),
        )

    # ---- BONUSES ---------------------------------------------------------------

    async def _bonus_stats(self, filters: StatisticsFilters) -> BonusStats:
        eligible = self._eligible_survey_ids(filters)
        where = self._platform_scoped_conditions(Bonus, filters, eligible)

        stmt = (
            select(Bonus.receives_bonuses, func.count())
            .where(*where, Bonus.receives_bonuses.is_not(None))
            .group_by(Bonus.receives_bonuses)
        )
        rows = (await self.db.execute(stmt)).all()
        counts = {("yes" if flag else "no"): count for flag, count in rows}
        receives_bonuses = CategoryBreakdown(counts=counts, total=sum(counts.values()))

        return BonusStats(
            receives_bonuses=receives_bonuses,
            bonus_value=await self._metric_summary(Bonus.bonus_value_amount, *where),
            bonus_trip_threshold=await self._metric_summary(Bonus.required_trips, *where),
        )

    # ---- MULTI-APP ---------------------------------------------------------------

    async def _multi_app_stats(self, filters: StatisticsFilters) -> MultiAppStats:
        eligible = self._eligible_survey_ids(filters, completed_only=True)
        # One row per (survey, platform_name) — grouped in Python below, since
        # a portable "sorted platform combination" aggregate is fragile across
        # SQL dialects and the row count here is small (survey_platforms).
        rows = (
            await self.db.execute(
                select(SurveyPlatform.survey_id, Platform.name)
                .where(SurveyPlatform.survey_id.in_(eligible), SurveyPlatform.platform_id == Platform.id)
            )
        ).all()

        platforms_by_survey: dict = {}
        for survey_id, platform_name in rows:
            platforms_by_survey.setdefault(survey_id, set()).add(platform_name)

        bucket_counts: dict[str, int] = {"one platform": 0, "multiple platforms": 0}
        combination_counts: dict[str, int] = {}
        for platform_names in platforms_by_survey.values():
            bucket_counts["one platform" if len(platform_names) == 1 else "multiple platforms"] += 1
            combo = ", ".join(sorted(platform_names))
            combination_counts[combo] = combination_counts.get(combo, 0) + 1

        exclusivity_where = self._platform_scoped_conditions(SurveyPlatform, filters, eligible) + [
            SurveyPlatform.is_exclusive.is_not(None)
        ]
        exclusivity_rows = (
            await self.db.execute(
                select(SurveyPlatform.is_exclusive, func.count())
                .where(*exclusivity_where)
                .group_by(SurveyPlatform.is_exclusive)
            )
        ).all()
        exclusivity_counts = {("yes" if flag else "no"): count for flag, count in exclusivity_rows}

        return MultiAppStats(
            platform_count_bucket=CategoryBreakdown(counts=bucket_counts, total=sum(bucket_counts.values())),
            platform_combinations=CategoryBreakdown(
                counts=combination_counts, total=sum(combination_counts.values())
            ),
            exclusivity=CategoryBreakdown(counts=exclusivity_counts, total=sum(exclusivity_counts.values())),
        )

    # ---- DRIVER TYPE ---------------------------------------------------------------

    async def _driver_type_stats(self, filters: StatisticsFilters) -> DriverTypeStats:
        eligible = self._eligible_survey_ids(filters)
        breakdown = await self._breakdown(
            DriverType.name,
            DriverEmployment.survey_id.in_(eligible),
            DriverEmployment.driver_type_id == DriverType.id,
        )

        text_rows = (
            await self.db.execute(
                select(DriverEmployment.employment_text).where(
                    DriverEmployment.survey_id.in_(eligible),
                    DriverEmployment.employment_text.is_not(None),
                )
            )
        ).scalars().all()
        classification_counts: dict[str, int] = {}
        for text in text_rows:
            label = classify_driver_type(text)
            if label is None:
                continue
            classification_counts[label] = classification_counts.get(label, 0) + 1
        employment_text_classification = CategoryBreakdown(
            counts=classification_counts, total=sum(classification_counts.values())
        )

        return DriverTypeStats(distribution=breakdown, employment_text_classification=employment_text_classification)

    # ---- CATEGORIES ---------------------------------------------------------------

    async def _category_stats(self, filters: StatisticsFilters) -> CategoryUsageStats:
        """RideCategoryUsage (per-platform, historical surveys only — the
        current questionnaire's Q6 has no per-platform category loop any
        more) and DriverExperience.main_category (survey-level, what every
        current survey actually writes) both answer the same underlying
        question — "what category does this driver mainly drive?" — so
        their counts are merged into one breakdown rather than shown as two
        disconnected, mostly-empty series.
        """
        eligible = self._eligible_survey_ids(filters)
        base_where = self._platform_scoped_conditions(RideCategoryUsage, filters, eligible) + [
            RideCategoryUsage.category_id == RideCategory.id,
        ]
        category_distribution = await self._breakdown(RideCategory.name, *base_where)
        main_category_distribution = await self._breakdown(
            RideCategory.name, *base_where, RideCategoryUsage.is_main_category.is_(True)
        )

        structured_main_category = await self._breakdown(
            RideCategory.name,
            DriverExperience.survey_id.in_(eligible),
            DriverExperience.main_category_id == RideCategory.id,
        )

        def _merge(a: CategoryBreakdown, b: CategoryBreakdown) -> CategoryBreakdown:
            counts = dict(a.counts)
            for label, count in b.counts.items():
                counts[label] = counts.get(label, 0) + count
            return CategoryBreakdown(counts=counts, total=sum(counts.values()))

        return CategoryUsageStats(
            category_distribution=_merge(category_distribution, structured_main_category),
            main_category_distribution=_merge(main_category_distribution, structured_main_category),
        )

    # ---- MARKET: sample facts vs. driver estimates --------------------------------

    async def _sample_market_stats(self, filters: StatisticsFilters) -> SampleMarketStats:
        eligible = self._eligible_survey_ids(filters)
        internet_reliability = await self._breakdown(
            DriverExperience.internet_reliability,
            DriverExperience.survey_id.in_(eligible),
        )
        # Enum values compare/group fine, but stringify keys for a JSON-friendly shape.
        internet_reliability = CategoryBreakdown(
            counts={str(getattr(k, "value", k)): v for k, v in internet_reliability.counts.items()},
            total=internet_reliability.total,
        )
        return SampleMarketStats(internet_reliability=internet_reliability)

    async def _driver_estimates(self, filters: StatisticsFilters) -> DriverEstimates:
        eligible = self._eligible_survey_ids(filters)

        perceived_market_leader = await self._breakdown(
            Platform.name,
            Survey.id.in_(eligible),
            Survey.perceived_market_leader_platform_id == Platform.id,
        )

        estimated_driver_count = await self._metric_summary(
            MarketIntelligence.estimated_driver_count,
            *self._platform_scoped_conditions(MarketIntelligence, filters, eligible),
        )

        notes_rows = (
            await self.db.execute(
                select(DriverExperience.seasonal_behavior_notes).where(
                    DriverExperience.survey_id.in_(eligible),
                    DriverExperience.seasonal_behavior_notes.is_not(None),
                )
            )
        ).scalars().all()
        classification_counts: dict[str, int] = {}
        for note in notes_rows:
            label = classify_seasonality(note)
            if label is None:
                continue
            classification_counts[label] = classification_counts.get(label, 0) + 1
        seasonality_breakdown = CategoryBreakdown(
            counts=classification_counts, total=sum(classification_counts.values())
        )

        seasonal_pattern = await self._breakdown(
            DriverExperience.seasonal_pattern,
            DriverExperience.survey_id.in_(eligible),
        )
        seasonal_pattern = CategoryBreakdown(
            counts={str(getattr(k, "value", k)): v for k, v in seasonal_pattern.counts.items()},
            total=seasonal_pattern.total,
        )

        return DriverEstimates(
            perceived_market_leader=perceived_market_leader,
            estimated_driver_count=estimated_driver_count,
            seasonality_text_classification=seasonality_breakdown,
            seasonal_pattern=seasonal_pattern,
        )
