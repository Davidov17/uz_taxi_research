"""Import every model module here so they all register on the shared
`Base.metadata` before anything calls `configure_mappers()`, runs Alembic
autogenerate, or creates tables in tests. Import this package (not
individual model modules) whenever you need the full metadata."""

from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.attachment import Attachment
from telegram_bot.infrastructure.db.models.bonus import Bonus
from telegram_bot.infrastructure.db.models.driver_employment import DriverEmployment
from telegram_bot.infrastructure.db.models.driver_experience import DriverExperience
from telegram_bot.infrastructure.db.models.earnings import Earnings
from telegram_bot.infrastructure.db.models.fsm_state import FsmState
from telegram_bot.infrastructure.db.models.interviewer import Interviewer
from telegram_bot.infrastructure.db.models.lookups import (
    City,
    DriverType,
    EarningsBasis,
    PayoutMethod,
    Platform,
    RideCategory,
    SwitchFrequency,
)
from telegram_bot.infrastructure.db.models.market_intelligence import MarketIntelligence
from telegram_bot.infrastructure.db.models.metric_observation import MetricObservation
from telegram_bot.infrastructure.db.models.payment import Payment
from telegram_bot.infrastructure.db.models.ride_category_usage import RideCategoryUsage
from telegram_bot.infrastructure.db.models.survey import Survey
from telegram_bot.infrastructure.db.models.survey_answer_option import SurveyAnswerOption
from telegram_bot.infrastructure.db.models.survey_platform import SurveyPlatform
from telegram_bot.infrastructure.db.models.working_stats import WorkingStats

__all__ = [
    "Base",
    "Attachment",
    "Bonus",
    "DriverEmployment",
    "DriverExperience",
    "Earnings",
    "FsmState",
    "Interviewer",
    "City",
    "DriverType",
    "EarningsBasis",
    "PayoutMethod",
    "Platform",
    "RideCategory",
    "SwitchFrequency",
    "MarketIntelligence",
    "MetricObservation",
    "Payment",
    "RideCategoryUsage",
    "Survey",
    "SurveyAnswerOption",
    "SurveyPlatform",
    "WorkingStats",
]
