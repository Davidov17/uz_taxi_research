"""Closed, stable value sets modeled as native PostgreSQL enums.

Anything an interviewer might need to *extend* later (cities, platforms,
ride categories, payout methods, ...) lives in a reference/lookup table
instead — see infrastructure/db/models/lookups.py — so adding a new value
never requires a schema migration. These enums are reserved for small,
genuinely fixed sets that are unlikely to change.
"""

import enum


class SurveyStatus(str, enum.Enum):
    DRAFT = "draft"  # started but not yet completed
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class Language(str, enum.Enum):
    """The interviewer's chosen language for one survey. Chosen before
    Question 1 and persisted on Survey.language so a resumed draft always
    renders in the language it was started in — see presentation/i18n.py
    for the actual translated strings."""

    EN = "en"
    RU = "ru"
    UZ = "uz"


class HoursPerDayBucket(str, enum.Enum):
    """The fixed hours-per-day buckets the questionnaire asks for (§3,
    "How many hours on average do you drive a day?") — kept as a closed
    enum, not a lookup table, since the bucket boundaries are part of the
    canonical question wording itself, not admin-editable reference data.
    """

    H1_2 = "1_2"
    H3_4 = "3_4"
    H5_6 = "5_6"
    H7_8 = "7_8"
    H9_10 = "9_10"
    H11_12 = "11_12"
    H12_PLUS = "12_plus"


class SeasonalPattern(str, enum.Enum):
    """Structured answer to "Are your driving habits different during the
    winter and summer season?" — replaces the old free-text
    seasonal_behavior_notes for new surveys (that column is kept for
    historical rows; see DriverExperience)."""

    SAME_YEAR_ROUND = "same_year_round"
    MORE_IN_SUMMER = "more_in_summer"
    MORE_IN_WINTER = "more_in_winter"


class BestExperienceNote(str, enum.Enum):
    """The two non-platform answers to "Which app gives you the best
    experience as a driver?" (Q3) — a real platform answer uses
    Survey.best_experience_platform_id instead; exactly one of the two is
    ever set."""

    SAME = "same"
    DONT_KNOW = "dont_know"


class InternetReliability(str, enum.Enum):
    POOR = "poor"
    FAIR = "fair"
    GOOD = "good"
    EXCELLENT = "excellent"
    UNKNOWN = "unknown"


class BonusPeriod(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    PER_TRIP = "per_trip"
    OTHER = "other"


class AttachmentFileType(str, enum.Enum):
    SCREENSHOT = "screenshot"
    PHOTO = "photo"
    DOCUMENT = "document"
    OTHER = "other"


class AnswerSource(str, enum.Enum):
    """Where a metric value came from. The research brief requires this to
    be preserved per-answer for the quantitative "main metrics" (trips,
    online hours, earnings, ASP, tips, bonus/supply spend, commission,
    distance) since a driver's verbal estimate and what their app screen
    actually shows are two different, both-worth-keeping data points —
    see MetricObservation.
    """

    VERBAL = "verbal"
    SCREENSHOT = "screenshot"
    INTERVIEWER_OBSERVATION = "interviewer_observation"


class MetricCode(str, enum.Enum):
    """The closed set of quantitative research metrics that can be
    reported at more than one cadence and/or sourced more than one way.
    Scoped exactly to the brief's "main research metrics" list — everything
    else in the questionnaire has exactly one natural source (an interview
    answer) and lives directly on its own typed column instead.
    """

    TRIPS_PER_WEEK = "trips_per_week"
    TRIPS_PER_MONTH = "trips_per_month"
    ONLINE_HOURS_PER_WEEK = "online_hours_per_week"
    ONLINE_HOURS_PER_MONTH = "online_hours_per_month"
    WEEKLY_EARNINGS = "weekly_earnings"
    MONTHLY_EARNINGS = "monthly_earnings"
    ASP = "asp"
    TIPS = "tips"
    BONUS_SPEND = "bonus_spend"
    COMMISSION_PCT = "commission_pct"
    DISTANCE_KM = "distance_km"
