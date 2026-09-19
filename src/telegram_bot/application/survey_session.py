"""The application-layer service that drives one survey conversation.

SurveySession is the only thing that knows how a Question's answer maps to
database writes. It is constructed fresh for every incoming Telegram
update (see presentation/handlers/survey.py) from a small, JSON-serializable
`CursorState` — the actual answers are never cached across updates, they're
always re-read from the database, which is what makes "save progressively"
and "never lose answers on Back" true by construction rather than by
convention.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from telegram_bot.application.question_engine import (
    REVIEW_SECTION_INDEX,
    Position,
    ValidationError,
    current_question,
    next_position,
    parse_number,
    prev_position,
    start_position,
)
from telegram_bot.application.screenshot_extraction import NullScreenshotExtractor, ScreenshotExtractor
from telegram_bot.domain.enums import (
    AnswerSource,
    AttachmentFileType,
    BestExperienceNote,
    HoursPerDayBucket,
    Language,
    SeasonalPattern,
    SurveyStatus,
)
from telegram_bot.domain.i18n import DEFAULT_LANGUAGE, translate_lookup
from telegram_bot.domain.questionnaire import SCREENSHOTS_SECTION_INDEX, LocalizedOption, Option, Question
from telegram_bot.infrastructure.db.models import (
    Attachment,
    Bonus,
    City,
    DriverEmployment,
    DriverExperience,
    DriverType,
    Earnings,
    EarningsBasis,
    Interviewer,
    Payment,
    Platform,
    RideCategory,
    Survey,
    SurveyAnswerOption,
    SurveyPlatform,
    SwitchFrequency,
    WorkingStats,
)

# Representative midpoint for each hours-per-day bucket, so the legacy
# numeric WorkingStats.hours_per_day column (and every AVG/MEDIAN
# statistic built on it) keeps working without a query rewrite, even
# though the questionnaire itself only offers fixed buckets.
_HOURS_BUCKET_MIDPOINT: dict[HoursPerDayBucket, float] = {
    HoursPerDayBucket.H1_2: 1.5,
    HoursPerDayBucket.H3_4: 3.5,
    HoursPerDayBucket.H5_6: 5.5,
    HoursPerDayBucket.H7_8: 7.5,
    HoursPerDayBucket.H9_10: 9.5,
    HoursPerDayBucket.H11_12: 11.5,
    HoursPerDayBucket.H12_PLUS: 13.0,
}
_HOURS_BUCKET_CODES = {b.value for b in HoursPerDayBucket}
_SEASONAL_PATTERN_CODES = {p.value for p in SeasonalPattern}

# Representative midpoints for the other range-style single-choice
# questions (Q7/Q8/Q9/Q12) — same rationale as hours-per-day: the
# questionnaire only offers buckets, but a numeric approximation keeps
# existing AVG/MEDIAN statistics working. Buckets with no sensible single
# number ("Don't know", "Varies", "Different by platform") map to None,
# correctly excluded from those statistics rather than guessed at.
_TRIPS_PER_DAY_MIDPOINT: dict[str, float] = {
    "1_5": 3, "6_10": 8, "11_15": 13, "16_20": 18, "21_30": 26, "31_40": 36, "40_plus": 45,
}
_COMMISSION_PCT_MIDPOINT: dict[str, float] = {
    "0_5": 2.5, "6_10": 8, "11_15": 13, "16_20": 18, "21_25": 23, "25_plus": 30,
}
_EARNINGS_RANGE_MIDPOINT: dict[str, float] = {
    "lt_500k": 250_000, "500k_1m": 750_000, "1m_1_5m": 1_250_000,
    "1_5m_2m": 1_750_000, "2m_3m": 2_500_000, "gt_3m": 3_500_000,
}
_EARNINGS_NON_RANGE_CODES = {"varies", "dont_know"}
_CASH_PCT_MIDPOINT: dict[str, float] = {
    "0": 0, "1_10": 5, "11_25": 18, "26_50": 38, "51_75": 63, "76_99": 87, "100": 100,
}
_BONUS_TYPE_TO_RECEIVES: dict[str, bool | None] = {
    "none": False, "trip_based": True, "time_based": True, "performance_based": True,
    "multiple_types": True, "other": True, "dont_know": None,
}


async def get_or_create_interviewer(db: AsyncSession, telegram_user_id: int, full_name: str) -> Interviewer:
    result = await db.execute(select(Interviewer).where(Interviewer.telegram_user_id == telegram_user_id))
    interviewer = result.scalar_one_or_none()
    if interviewer is None:
        interviewer = Interviewer(telegram_user_id=telegram_user_id, full_name=full_name)
        db.add(interviewer)
        await db.flush()
    return interviewer


@dataclass
class CursorState:
    survey_id: str | None
    section_index: int
    platform_pos: int | None
    question_index: int
    language: str | None = None
    """Chosen before Question 1, while no Survey row exists yet to hold it
    — applied to Survey.language the moment the survey is created (see
    _dispatch_answer's "city" branch). Once the survey exists,
    SurveySession.language reads from the row instead, which is what
    makes a resumed draft always render in the language it was started
    in, per the multilingual requirement."""

    @classmethod
    def initial(cls, language: str | None = None) -> CursorState:
        p = start_position()
        return cls(
            survey_id=None,
            section_index=p.section_index,
            platform_pos=p.platform_pos,
            question_index=p.question_index,
            language=language,
        )

    @property
    def position(self) -> Position:
        return Position(self.section_index, self.platform_pos, self.question_index)

    @position.setter
    def position(self, value: Position) -> None:
        self.section_index = value.section_index
        self.platform_pos = value.platform_pos
        self.question_index = value.question_index

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CursorState:
        return cls(**data)


class SurveySession:
    def __init__(
        self,
        db: AsyncSession,
        cursor: CursorState,
        interviewer: Interviewer,
        extractor: ScreenshotExtractor | None = None,
    ):
        self.db = db
        self.cursor = cursor
        self.interviewer = interviewer
        self.extractor = extractor or NullScreenshotExtractor()
        self.survey: Survey | None = None
        self.survey_platforms: list[SurveyPlatform] = []
        self.working_stats: WorkingStats | None = None
        self.driver_employment: DriverEmployment | None = None
        self.driver_experience: DriverExperience | None = None
        self.attachments: list[Attachment] = []
        self.answer_options: dict[str, list[str]] = {}
        self._survey_ctx: dict[str, Any] = {}

    @classmethod
    async def resume(
        cls,
        db: AsyncSession,
        cursor: CursorState,
        interviewer: Interviewer,
        extractor: ScreenshotExtractor | None = None,
    ) -> SurveySession:
        session = cls(db, cursor, interviewer, extractor)
        if cursor.survey_id:
            await session._load_from_db(uuid.UUID(cursor.survey_id))
        await session._build_context()
        return session

    async def _query_survey_platforms(self, survey_id: uuid.UUID) -> list[SurveyPlatform]:
        """Fetch this survey's SurveyPlatform rows with every relationship
        this module touches eagerly loaded. Used both on initial load and
        right after creating new rows — a freshly-flushed ORM object's
        unloaded relationships try to lazy-load on next access, which
        fails under the async driver, so anything we're about to read
        must come from a query like this one rather than from an object
        we just built in memory.
        """
        result = await self.db.execute(
            select(SurveyPlatform)
            .where(SurveyPlatform.survey_id == survey_id)
            .options(
                selectinload(SurveyPlatform.platform),
                selectinload(SurveyPlatform.switch_frequency),
                selectinload(SurveyPlatform.earnings).selectinload(Earnings.earnings_basis),
                selectinload(SurveyPlatform.bonus),
                selectinload(SurveyPlatform.payment),
            )
            .order_by(SurveyPlatform.platform_id)
        )
        return list(result.scalars().all())

    async def _load_from_db(self, survey_id: uuid.UUID) -> None:
        # Eagerly load every Survey relationship the review screen /
        # question rendering reads directly off `self.survey` (city, in
        # particular) — a plain db.get() leaves them unloaded, and this
        # module's session is fresh per Telegram update (see
        # presentation.middlewares.DbSessionMiddleware), so a later lazy
        # access has no identity-map hit to fall back on and raises under
        # the async driver. This was the root cause of a real bug: the
        # review screen crashing (silently, from the interviewer's point
        # of view — see presentation/handlers/survey.py's docstring on
        # cb_screenshot_finish) the first time format_review() touched
        # survey.city on a freshly-loaded session.
        result = await self.db.execute(
            select(Survey)
            .where(Survey.id == survey_id)
            .options(
                selectinload(Survey.city),
                selectinload(Survey.target_platform),
                selectinload(Survey.best_experience_platform),
                selectinload(Survey.perceived_market_leader_platform),
            )
        )
        self.survey = result.scalar_one_or_none()
        self.survey_platforms = await self._query_survey_platforms(survey_id)
        self.working_stats = await self._get_singleton(WorkingStats, survey_id)
        self.driver_employment = await self._get_singleton(
            DriverEmployment, survey_id, extra_options=(selectinload(DriverEmployment.driver_type),)
        )
        self.driver_experience = await self._get_singleton(
            DriverExperience, survey_id, extra_options=(selectinload(DriverExperience.main_category),)
        )
        self.attachments = await self._query_attachments(survey_id)
        self.answer_options = await self._query_answer_options(survey_id)

    async def _query_attachments(self, survey_id: uuid.UUID) -> list[Attachment]:
        result = await self.db.execute(
            select(Attachment).where(Attachment.survey_id == survey_id).order_by(Attachment.sequence_number)
        )
        return list(result.scalars().all())

    async def _query_answer_options(self, survey_id: uuid.UUID) -> dict[str, list[str]]:
        result = await self.db.execute(select(SurveyAnswerOption).where(SurveyAnswerOption.survey_id == survey_id))
        by_question: dict[str, list[str]] = {}
        for row in result.scalars().all():
            by_question.setdefault(row.question_code, []).append(row.option_code)
        return by_question

    async def _get_singleton(self, model, survey_id: uuid.UUID, extra_options: tuple = ()):
        stmt = select(model).where(model.survey_id == survey_id)
        for opt in extra_options:
            stmt = stmt.options(opt)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    @property
    def language(self) -> str:
        """The survey's persisted language once it exists; the
        not-yet-created draft's chosen language before that (set by
        set_language()); "en" if neither is set yet."""
        if self.survey is not None:
            return self.survey.language.value if isinstance(self.survey.language, Language) else self.survey.language
        return self.cursor.language or DEFAULT_LANGUAGE

    def set_language(self, lang: str) -> None:
        """Called by the language-selection step, before Question 1. Once
        the Survey row exists (from then on), language lives on the row
        instead and this has no further effect."""
        self.cursor.language = lang
        if self.survey is not None:
            self.survey.language = Language(lang)

    @property
    def platform_count(self) -> int:
        return len(self.survey_platforms)

    @property
    def attachment_count(self) -> int:
        return len(self.attachments)

    @property
    def awaiting_screenshot_upload(self) -> bool:
        """True once the driver has said they have screenshots to share,
        until the interviewer presses "Finish uploading". Distinguishes
        this from an ordinary re-render of the has_screenshots question
        (e.g. on resume after a restart)."""
        pos = self.cursor.position
        return (
            self.survey is not None
            and bool(self.survey.screenshots_offered)
            and pos.section_index == SCREENSHOTS_SECTION_INDEX
            and pos.question_index == 0
        )

    async def _next_human_code(self, city_code: str) -> str:
        """A unique, human-readable survey id like "TAS-000123" — city
        prefix plus a zero-padded sequential number within that prefix.
        """
        prefix = city_code[:3].upper()
        result = await self.db.execute(
            select(func.count()).select_from(Survey).where(Survey.human_code.like(f"{prefix}-%"))
        )
        next_number = result.scalar_one() + 1
        return f"{prefix}-{next_number:06d}"

    async def _build_context(self) -> None:
        """No question in the current (flat, unconditioned) questionnaire
        has a Condition — see questionnaire.py's module docstring — so
        there is nothing to compute here. Kept as a hook matching
        question_engine's answers_view_fn signature, and in case a future
        question ever needs one again."""
        self._survey_ctx = {}

    def _answers_view(self, position: Position) -> dict[str, Any]:
        return self._survey_ctx

    def current_question(self) -> Question | None:
        return current_question(self.cursor.position, self._answers_view)

    def is_review(self) -> bool:
        """True once the cursor has walked off the end of the
        questionnaire (past the last real section) — the presentation
        layer renders the review/confirmation screen the moment this
        becomes true, and only actually finishes the survey once the
        interviewer taps Confirm/Submit (see confirm())."""
        return self.cursor.position.section_index >= REVIEW_SECTION_INDEX

    async def resolve_options(self, question: Question, lang: str | None = None) -> list[Option]:
        """Static options (question.options) first, then a DB-resolved
        source (question.options_source) if any, then extra static
        options (question.extra_options) if any — e.g. Q3's selected
        platforms followed by "All are about the same"/"Don't know", or
        Q9's fixed earnings ranges followed by the DB-backed earnings-
        basis choices. Any combination not used by the current
        questionnaire is simply an empty contribution."""
        lang = lang or self.language
        resolved: list[Option] = []
        if question.options is not None:
            resolved.extend(opt.resolve(lang) for opt in question.options)
        if question.options_source is not None:
            resolved.extend(await self._resolve_dynamic_options(question.options_source, lang))
        if question.extra_options is not None:
            resolved.extend(opt.resolve(lang) for opt in question.extra_options)
        return resolved

    async def _resolve_dynamic_options(self, source: str, lang: str) -> list[Option]:
        async def _from_lookup(model, order_by=None) -> list[Option]:
            stmt = select(model).where(model.is_active == True)  # noqa: E712
            stmt = stmt.order_by(order_by) if order_by is not None else stmt.order_by(model.id)
            rows = (await self.db.execute(stmt)).scalars().all()
            table_name = model.__tablename__
            return [Option(str(r.id), translate_lookup(table_name, r.code, lang, r.name)) for r in rows]

        if source == "cities":
            return await _from_lookup(City)
        if source == "platforms":
            return await _from_lookup(Platform)
        if source == "switch_frequencies":
            return await _from_lookup(SwitchFrequency, order_by=SwitchFrequency.sort_order)
        if source == "earnings_basis":
            return await _from_lookup(EarningsBasis)
        if source == "ride_categories":
            return await _from_lookup(RideCategory)
        if source == "survey_platforms":
            return [
                Option(str(sp.platform_id), sp.platform_other_name or sp.platform.name)
                for sp in self.survey_platforms
            ]
        raise ValueError(f"Unknown options_source: {source}")

    # ---- get-or-create helpers for the one-row-per-survey(/-platform) children ----

    async def _get_or_create_working_stats(self) -> WorkingStats:
        if self.working_stats is None:
            self.working_stats = WorkingStats(survey_id=self.survey.id)
            self.db.add(self.working_stats)
            await self.db.flush()
        return self.working_stats

    async def _get_or_create_driver_employment(self) -> DriverEmployment:
        if self.driver_employment is None:
            self.driver_employment = DriverEmployment(survey_id=self.survey.id, driver_type=None)
            self.db.add(self.driver_employment)
            await self.db.flush()
        return self.driver_employment

    async def _get_or_create_driver_experience(self) -> DriverExperience:
        if self.driver_experience is None:
            self.driver_experience = DriverExperience(survey_id=self.survey.id)
            self.db.add(self.driver_experience)
            await self.db.flush()
        return self.driver_experience

    async def _get_or_create_earnings(self, sp: SurveyPlatform) -> Earnings:
        if sp.earnings is None:
            sp.earnings = Earnings(survey_platform=sp)
            self.db.add(sp.earnings)
            await self.db.flush()
        return sp.earnings

    async def _get_or_create_bonus(self, sp: SurveyPlatform) -> Bonus:
        if sp.bonus is None:
            sp.bonus = Bonus(survey_platform=sp)
            self.db.add(sp.bonus)
            await self.db.flush()
        return sp.bonus

    async def _get_or_create_payment(self, sp: SurveyPlatform) -> Payment:
        if sp.payment is None:
            sp.payment = Payment(survey_platform=sp)
            self.db.add(sp.payment)
            await self.db.flush()
        return sp.payment

    # ---- SurveyAnswerOption persistence -----------------------------------
    #
    # Backing store for every closed-option question that has no other
    # natural single-column home — see infrastructure/db/models/
    # survey_answer_option.py for why this is one small relational table
    # rather than several near-identical ones or a JSON column.

    async def _set_answer_options(self, question_code: str, option_codes: list[str]) -> None:
        """Replace every previously stored option for this question with
        exactly the given set — used for both single-choice (always one
        code) and multi-choice questions, so re-answering after Back never
        leaves stale rows behind."""
        existing = (
            await self.db.execute(
                select(SurveyAnswerOption).where(
                    SurveyAnswerOption.survey_id == self.survey.id,
                    SurveyAnswerOption.question_code == question_code,
                )
            )
        ).scalars().all()
        for row in existing:
            await self.db.delete(row)
        for option_code in option_codes:
            self.db.add(SurveyAnswerOption(survey_id=self.survey.id, question_code=question_code, option_code=option_code))
        await self.db.flush()
        self.answer_options[question_code] = list(option_codes)

    async def _set_answer_option(self, question_code: str, option_code: str) -> None:
        await self._set_answer_options(question_code, [option_code])

    # ---- answering -----------------------------------------------------

    async def answer(self, question: Question, raw_value: Any) -> None:
        """Validate (for free-form input) and persist `raw_value` for
        `question`, then advance the cursor to the next visible question.
        Raises ValidationError without writing anything if raw_value fails
        validation, so an invalid submission never partially applies.

        Special case: answering "has_screenshots" with True does NOT
        advance — it hands control to the screenshot upload loop instead
        (see record_screenshot/finish_screenshot_upload). The cursor stays
        on this question so awaiting_screenshot_upload can detect the
        state on resume (e.g. after a bot restart mid-upload).
        """
        await self._dispatch_answer(question, raw_value)
        await self.db.flush()
        await self._build_context()
        if question.code == "has_screenshots" and raw_value is True:
            return
        nxt = next_position(self.cursor.position, self._answers_view, self.platform_count)
        if nxt is not None:
            self.cursor.position = nxt

    async def skip(self, question: Question) -> None:
        if question.required:
            raise ValidationError("This question requires an answer.", code="required")
        nxt = next_position(self.cursor.position, self._answers_view, self.platform_count)
        if nxt is not None:
            self.cursor.position = nxt

    def go_back(self) -> bool:
        prev = prev_position(self.cursor.position, self._answers_view, self.platform_count)
        if prev is None:
            return False
        self.cursor.position = prev
        return True

    def can_go_back(self) -> bool:
        return prev_position(self.cursor.position, self._answers_view, self.platform_count) is not None

    def previous_multi_selection(self, question: Question) -> set[str]:
        """Values already selected for a multi-choice question, so Back
        navigation can re-show prior checkmarks instead of an empty form."""
        code = question.code
        if code == "platforms_used":
            return {str(sp.platform_id) for sp in self.survey_platforms}
        if code == "hours_and_season":
            selected: set[str] = set()
            if self.working_stats and self.working_stats.hours_per_day_bucket:
                selected.add(self.working_stats.hours_per_day_bucket.value)
            if self.driver_experience and self.driver_experience.seasonal_pattern:
                selected.add(self.driver_experience.seasonal_pattern.value)
            return selected
        if code == "earnings":
            selected = set(self.answer_options.get("earnings", []))
            sp = self.survey_platforms[0] if self.survey_platforms else None
            if sp and sp.earnings and sp.earnings.earnings_basis_id is not None:
                selected.add(str(sp.earnings.earnings_basis_id))
            return selected
        if code in ("market_awareness", "payout_methods", "driver_type_loyalty", "driver_motivation"):
            return set(self.answer_options.get(code, []))
        return set()

    async def record_screenshot(
        self,
        *,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        file_type: AttachmentFileType = AttachmentFileType.SCREENSHOT,
        mime_type: str | None = None,
        platform_id: int | None = None,
        description: str | None = None,
    ) -> Attachment:
        """Store one uploaded file and immediately run it through
        self.extractor (a no-op today — see screenshot_extraction.py — but
        this is the one place a future OCR/AI implementation plugs in).
        sequence_number is this survey's upload order, computed from
        self.attachments as loaded fresh at the start of this request, not
        a global counter.
        """
        attachment = Attachment(
            survey_id=self.survey.id,
            platform_id=platform_id,
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            file_type=file_type,
            mime_type=mime_type,
            sequence_number=self.attachment_count + 1,
            description=description,
        )
        self.db.add(attachment)
        await self.db.flush()
        self.attachments.append(attachment)

        for metric in await self.extractor.extract(attachment):
            await self._upsert_metric_observation(metric.metric_code, metric.value, platform_id=metric.platform_id, attachment_id=attachment.id)
        return attachment

    async def _upsert_metric_observation(self, metric_code, value, *, platform_id=None, attachment_id=None) -> None:
        """Screenshot-sourced only — see MetricObservation's docstring.
        Nothing in the current questionnaire's verbal answers mirrors into
        this table any more: every "main metric" question is now a
        predefined range/bucket, and mirroring a bucket's *midpoint* into
        a table meant for precise dual-source values would overstate how
        exact a driver's verbal answer actually was."""
        from telegram_bot.infrastructure.db.models import MetricObservation

        result = await self.db.execute(
            select(MetricObservation).where(
                MetricObservation.survey_id == self.survey.id,
                MetricObservation.metric_code == metric_code,
                MetricObservation.source == AnswerSource.SCREENSHOT,
                MetricObservation.platform_id == platform_id if platform_id is not None else MetricObservation.platform_id.is_(None),
            )
        )
        obs = result.scalar_one_or_none()
        if obs is None:
            obs = MetricObservation(
                survey_id=self.survey.id, platform_id=platform_id, metric_code=metric_code,
                source=AnswerSource.SCREENSHOT, value_numeric=value, attachment_id=attachment_id,
            )
            self.db.add(obs)
        else:
            obs.value_numeric = value
            if attachment_id is not None:
                obs.attachment_id = attachment_id
        await self.db.flush()

    async def finish_screenshot_upload(self) -> None:
        nxt = next_position(self.cursor.position, self._answers_view, self.platform_count)
        if nxt is not None:
            self.cursor.position = nxt

    async def restart(self) -> SurveySession:
        """Used both by the mid-survey "discard and start over" flow and by
        "Start New Survey" after a completed submission. Only a DRAFT
        survey gets abandoned — a COMPLETED one is left untouched either
        way, so calling this after submission is always safe.
        """
        if self.survey is not None and self.survey.status == SurveyStatus.DRAFT:
            self.survey.status = SurveyStatus.ABANDONED
            await self.db.flush()
        return await SurveySession.resume(self.db, CursorState.initial(), self.interviewer)

    async def finish(self) -> None:
        """DRAFT -> COMPLETED. Idempotent: a survey that's already
        COMPLETED is left exactly as it is — no re-stamped completed_at,
        no re-run of anything — so a duplicate submission trigger (double
        tap, Telegram retry, ...) never mutates a completed survey."""
        if self.survey.status == SurveyStatus.COMPLETED:
            return
        self.survey.status = SurveyStatus.COMPLETED
        self.survey.completed_at = datetime.now(timezone.utc)
        await self.db.flush()

    async def confirm(self) -> None:
        """The interviewer tapped Confirm/Submit on the review screen —
        the only thing that actually finalizes a survey."""
        await self.finish()

    async def _dispatch_answer(self, question: Question, raw_value: Any) -> None:
        code = question.code

        if code == "city":
            city = await self.db.get(City, int(raw_value))
            if self.survey is None:
                now = datetime.now(timezone.utc)
                self.survey = Survey(
                    human_code=await self._next_human_code(city.code),
                    interviewer_id=self.interviewer.id,
                    city_id=city.id,
                    status=SurveyStatus.DRAFT,
                    survey_datetime=now,
                    started_at=now,
                    language=Language(self.cursor.language or DEFAULT_LANGUAGE),
                )
                self.db.add(self.survey)
                await self.db.flush()
                self.cursor.survey_id = str(self.survey.id)
            else:
                self.survey.city_id = city.id
            return

        if code == "target_platform":
            self.survey.target_platform_id = int(raw_value)
            return

        if code == "platforms_used":
            await self._answer_platforms_used(raw_value)
            return

        if code == "switch_frequency":
            # Load the SwitchFrequency row and assign the relationship
            # (not just switch_frequency_id) on every platform — the same
            # loaded row can be shared across all of them, and it keeps a
            # later same-request read (e.g. the review screen) from
            # needing its own lazy-load, which fails under the async driver.
            freq = await self.db.get(SwitchFrequency, int(raw_value))
            for sp in self.survey_platforms:
                sp.switch_frequency = freq
            return

        if code == "best_experience":
            if raw_value in ("same", "dont_know"):
                self.survey.best_experience_platform = None
                self.survey.best_experience_note = BestExperienceNote(raw_value)
            else:
                self.survey.best_experience_platform = await self.db.get(Platform, int(raw_value))
                self.survey.best_experience_note = None
            return

        if code == "days_per_week":
            value = int(parse_number(raw_value, min_value=question.min_value, max_value=question.max_value))
            ws = await self._get_or_create_working_stats()
            ws.days_per_week = value
            return

        if code == "hours_and_season":
            await self._answer_hours_and_season(raw_value)
            return

        if code == "main_category":
            de = await self._get_or_create_driver_experience()
            if raw_value == "dont_know":
                de.main_category = None
                await self._set_answer_option(code, "dont_know")
            else:
                # Set the relationship object, not just main_category_id:
                # this session may go on to read de.main_category later in
                # the same request (e.g. the review screen, if this were
                # ever the last question before it) — assigning the loaded
                # object keeps that read from needing its own lazy-load,
                # which fails under the async driver.
                de.main_category = await self.db.get(RideCategory, int(raw_value))
                await self._set_answer_options(code, [])  # clear any earlier "dont_know" marker
            return

        if code == "trips_per_day_range":
            ws = await self._get_or_create_working_stats()
            ws.trips_per_day = _TRIPS_PER_DAY_MIDPOINT.get(raw_value)
            # Q4 (days_per_week) always comes before Q7 in the flat, fixed
            # order, so it's already on working_stats here — derive the
            # weekly figure from it rather than leaving it null, since nothing
            # else in the 15-question flow asks for it directly and the
            # dashboard's earnings-per-trip ratio depends on it.
            if ws.trips_per_day is not None and ws.days_per_week is not None:
                ws.trips_per_week = round(ws.trips_per_day * ws.days_per_week)
            await self._set_answer_option(code, raw_value)
            return

        if code == "commission_range":
            midpoint = _COMMISSION_PCT_MIDPOINT.get(raw_value)
            for sp in self.survey_platforms:
                earnings = await self._get_or_create_earnings(sp)
                earnings.commission_pct = midpoint
            await self._set_answer_option(code, raw_value)
            return

        if code == "earnings":
            await self._answer_earnings(raw_value)
            return

        if code == "bonus_type":
            receives = _BONUS_TYPE_TO_RECEIVES.get(raw_value)
            for sp in self.survey_platforms:
                bonus = await self._get_or_create_bonus(sp)
                bonus.receives_bonuses = receives
            await self._set_answer_option(code, raw_value)
            return

        if code == "market_awareness":
            values = _require_at_least_one(raw_value)
            await self._set_answer_options(code, values)
            return

        if code == "cash_pct":
            midpoint = _CASH_PCT_MIDPOINT.get(raw_value)
            for sp in self.survey_platforms:
                payment = await self._get_or_create_payment(sp)
                payment.cash_pct = midpoint
            await self._set_answer_option(code, raw_value)
            return

        if code == "payout_methods":
            values = _require_at_least_one(raw_value)
            await self._set_answer_options(code, values)
            return

        if code == "driver_type_loyalty":
            await self._answer_driver_type_loyalty(raw_value)
            return

        if code == "driver_motivation":
            values = _require_at_least_one(raw_value)
            await self._set_answer_options(code, values)
            return

        if code == "has_screenshots":
            self.survey.screenshots_offered = bool(raw_value)
            return

        raise ValueError(f"No persistence handler for question code: {code}")

    async def _answer_platforms_used(self, raw_values: list[str]) -> None:
        selected_ids = {int(v) for v in raw_values}
        if not selected_ids:
            raise ValidationError("Select at least one platform.", code="select_at_least_one_platform")

        existing_ids = {sp.platform_id for sp in self.survey_platforms}
        for sp in list(self.survey_platforms):
            if sp.platform_id not in selected_ids:
                await self.db.delete(sp)
                self.survey_platforms.remove(sp)

        new_ids = selected_ids - existing_ids
        if new_ids:
            platforms = (await self.db.execute(select(Platform).where(Platform.id.in_(new_ids)))).scalars().all()
            for platform in platforms:
                sp = SurveyPlatform(survey_id=self.survey.id, platform_id=platform.id, works_with_platform=True)
                self.db.add(sp)
            await self.db.flush()
            # Re-query rather than appending the objects we just built: a
            # freshly-flushed row's relationships (platform, earnings, ...)
            # aren't "known empty" the way a query's eager-loaded ones are,
            # and touching them later would try a lazy-load that fails
            # under the async driver.
            self.survey_platforms = await self._query_survey_platforms(self.survey.id)
        await self.db.flush()

    async def _answer_hours_and_season(self, raw_values: list[str]) -> None:
        values = list(raw_values)
        hour_values = [v for v in values if v in _HOURS_BUCKET_CODES]
        season_values = [v for v in values if v in _SEASONAL_PATTERN_CODES]
        if len(hour_values) != 1 or len(season_values) != 1:
            raise ValidationError(
                "Please select exactly one hours-per-day option and one seasonal option.",
                code="hours_and_season_invalid",
            )
        bucket = HoursPerDayBucket(hour_values[0])
        pattern = SeasonalPattern(season_values[0])
        ws = await self._get_or_create_working_stats()
        ws.hours_per_day_bucket = bucket
        ws.hours_per_day = _HOURS_BUCKET_MIDPOINT[bucket]
        de = await self._get_or_create_driver_experience()
        de.seasonal_pattern = pattern

    async def _answer_earnings(self, raw_values: list[str]) -> None:
        values = list(raw_values)
        range_values = [v for v in values if v not in _EARNINGS_NON_RANGE_CODES and not v.isdigit()]
        varying_values = [v for v in values if v in _EARNINGS_NON_RANGE_CODES]
        basis_values = [v for v in values if v.isdigit()]
        range_or_varying = range_values + varying_values
        if len(range_or_varying) != 1 or len(basis_values) != 1:
            raise ValidationError(
                "Please select exactly one earnings range and one basis option.", code="earnings_invalid"
            )
        range_code = range_or_varying[0]
        basis_id = int(basis_values[0])
        midpoint = _EARNINGS_RANGE_MIDPOINT.get(range_code)
        # Assign the relationship object (not just the FK id) so a freshly
        # created Earnings row already has .earnings_basis loaded for the
        # review screen — same pattern as the main_category/switch_frequency
        # fixes below; setting only the id leaves an unpopulated relationship
        # that raises MissingGreenlet on read under a fresh async session.
        basis = await self.db.get(EarningsBasis, basis_id)
        for sp in self.survey_platforms:
            earnings = await self._get_or_create_earnings(sp)
            earnings.weekly_earnings_amount = midpoint
            earnings.earnings_basis = basis
        # Q9 asks for combined weekly income across every app in one answer
        # (not a separate per-platform question), so the same midpoint is
        # both each platform's figure above and the survey-level total the
        # dashboard's overall earnings/earnings-per-trip/earnings-per-hour
        # stats read from.
        self.survey.total_weekly_earnings_amount = midpoint
        await self._set_answer_option("earnings", range_code)

    async def _answer_driver_type_loyalty(self, raw_values: list[str]) -> None:
        values = _require_at_least_one(raw_values)
        await self._set_answer_options("driver_type_loyalty", values)

        de = await self._get_or_create_driver_employment()
        has_independent = "independent" in values
        has_fleet = "fleet" in values
        if has_independent and not has_fleet:
            de.driver_type_id = await self._driver_type_id("independent")
        elif has_fleet and not has_independent:
            de.driver_type_id = await self._driver_type_id("fleet")
        else:
            de.driver_type_id = None

    async def _driver_type_id(self, code: str) -> int | None:
        row = (await self.db.execute(select(DriverType).where(DriverType.code == code))).scalar_one_or_none()
        return row.id if row else None


def _require_at_least_one(raw_values: Any) -> list[str]:
    values = list(raw_values)
    if not values:
        raise ValidationError("Please select at least one option.", code="select_at_least_one_option")
    return values
