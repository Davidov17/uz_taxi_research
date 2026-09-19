"""Individual qualitative-response feeds for the admin dashboard.

Distinct from statistics_service.py: that layer aggregates ("42% receive
bonuses"); this one lists individual driver responses verbatim, for a
researcher to actually read. Reuses ExportService's eager-loaded survey
graph (one query) rather than re-implementing the same joins.

`perceived_market_leader` is included here even though it's a structured
single-choice answer, not free text — the dashboard treats "what did
drivers say" as one qualitative feed regardless of answer shape. Per the
brief, this is a driver's *opinion* about the market, not a verified fact —
callers (the dashboard UI) must label it as such, not present it as market
data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_bot.application.export_service import ExportFilters, ExportService, _platform_label


@dataclass(frozen=True)
class QualitativeResponse:
    survey_id: str
    city: str
    survey_date: datetime
    text: str
    platform: str | None = None


@dataclass(frozen=True)
class QualitativeFeeds:
    market_leader_responses: list[QualitativeResponse]
    """Driver opinions about which platform is biggest — NOT verified market
    data. Includes both the current questionnaire's open-text answer
    (market_leader_text) and older surveys' platform-pick answer."""
    reasons_to_join_new_platform: list[QualitativeResponse]
    improvement_suggestions: list[QualitativeResponse]
    category_requirements: list[QualitativeResponse]
    """Requirements to drive in a given category. Includes both the
    current questionnaire's survey-level open-text answer
    (main_category_text / category_requirements_text) and older surveys'
    per-platform structured answer (ride_category_usages.category_requirements)."""
    employment_responses: list[QualitativeResponse]
    """Free-text answer to "fleet company or independent driver?" — see
    statistics_service.classify_driver_type for a best-effort categorical
    reading of the same text."""
    bonus_descriptions: list[QualitativeResponse]
    payout_notes: list[QualitativeResponse]


class QualitativeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_feeds(self, filters: ExportFilters | None = None) -> QualitativeFeeds:
        surveys = await ExportService(self.db)._load_surveys(filters or ExportFilters())

        market_leader: list[QualitativeResponse] = []
        reasons: list[QualitativeResponse] = []
        improvements: list[QualitativeResponse] = []
        requirements: list[QualitativeResponse] = []
        employment: list[QualitativeResponse] = []
        bonus_descriptions: list[QualitativeResponse] = []
        payout_notes: list[QualitativeResponse] = []

        for s in surveys:
            if s.market_leader_text:
                market_leader.append(QualitativeResponse(s.human_code, s.city.name, s.survey_datetime, s.market_leader_text))
            elif s.perceived_market_leader_platform:
                market_leader.append(
                    QualitativeResponse(s.human_code, s.city.name, s.survey_datetime, s.perceived_market_leader_platform.name)
                )
            de = s.driver_experience
            if de and de.reason_to_join_new_platform:
                reasons.append(QualitativeResponse(s.human_code, s.city.name, s.survey_datetime, de.reason_to_join_new_platform))
            if de and de.improvement_suggestions:
                improvements.append(
                    QualitativeResponse(s.human_code, s.city.name, s.survey_datetime, de.improvement_suggestions)
                )
            if de and de.main_category_text:
                requirements.append(
                    QualitativeResponse(
                        s.human_code, s.city.name, s.survey_datetime,
                        f"{de.main_category_text}: {de.category_requirements_text or ''}".strip(": "),
                    )
                )
            for sp in s.survey_platforms:
                for rcu in sp.ride_category_usages:
                    if rcu.category_requirements:
                        requirements.append(
                            QualitativeResponse(
                                s.human_code, s.city.name, s.survey_datetime, rcu.category_requirements,
                                platform=_platform_label(sp),
                            )
                        )
                if sp.bonus and sp.bonus.description:
                    bonus_descriptions.append(
                        QualitativeResponse(
                            s.human_code, s.city.name, s.survey_datetime, sp.bonus.description, platform=_platform_label(sp)
                        )
                    )
                if sp.payment and sp.payment.payout_notes:
                    payout_notes.append(
                        QualitativeResponse(
                            s.human_code, s.city.name, s.survey_datetime, sp.payment.payout_notes, platform=_platform_label(sp)
                        )
                    )
            employment_text = s.driver_employment.employment_text if s.driver_employment else None
            if employment_text:
                employment.append(QualitativeResponse(s.human_code, s.city.name, s.survey_datetime, employment_text))

        return QualitativeFeeds(
            market_leader_responses=market_leader,
            reasons_to_join_new_platform=reasons,
            improvement_suggestions=improvements,
            category_requirements=requirements,
            employment_responses=employment,
            bonus_descriptions=bonus_descriptions,
            payout_notes=payout_notes,
        )
