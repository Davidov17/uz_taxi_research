"""The questionnaire, expressed as data.

This module has no dependency on aiogram or the database — it just
describes *what* to ask, in what order, and in which languages. The
application layer (survey_session.py) interprets this structure and talks
to the database; the presentation layer (aiogram handlers) only renders
whatever question the application layer says is current, in the
interviewer's chosen language. Neither of those layers hardcodes question
order or wording — it all comes from SECTIONS below, which is what keeps
"same question in three languages" a data change instead of a
handler/questionnaire-duplication rewrite.

IMPORTANT — flat, unconditioned flow: every question in SECTIONS below has
`condition=None` and every section has exactly one question and
`scope=SectionScope.SURVEY`. There is no per-platform repeat
(`PLATFORM_LOOP_SECTIONS` is empty) and no branching — the interviewer
sees every question, in the same order, every time. This is a deliberate
simplification (a prior version of this questionnaire had per-platform
repeats and conditional follow-ups); `Condition`/`SectionScope.PLATFORM`
still exist as engine features `question_engine.py` supports, they're just
unused by the current content.

Every question's canonical wording is English; `LocalizedText` values are
`{"en": ..., "ru": ..., "uz": ...}` dicts, resolved to a plain string at
render time via `localize()`. The underlying stored *value* for an answer
(a platform id, an option code, ...) never depends on language — only
what's displayed does.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

LocalizedText = dict[str, str]

DEFAULT_LANGUAGE = "en"


def localize(text: LocalizedText | str, lang: str) -> str:
    """Resolve a LocalizedText to one language, falling back to English
    and then to whatever's available if even English is missing (should
    never happen for our own content, but keeps this total rather than
    raising on a malformed translation dict)."""
    if isinstance(text, str):
        return text
    if lang in text:
        return text[lang]
    if DEFAULT_LANGUAGE in text:
        return text[DEFAULT_LANGUAGE]
    return next(iter(text.values()), "")


class QuestionType(str, Enum):
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    NUMBER = "number"
    PERCENTAGE = "percentage"
    CURRENCY = "currency"
    TEXT = "text"
    YES_NO = "yes_no"
    PHOTO = "photo"


class SectionScope(str, Enum):
    SURVEY = "survey"  # asked once per survey
    PLATFORM = "platform"  # engine feature, unused by the current (flat) questionnaire content


@dataclass(frozen=True)
class Option:
    """A single render-ready choice: value is the stable, language-independent
    answer identity; label is already resolved to one language. This is
    what keyboards.py / handlers.py work with — see LocalizedOption for how
    static options are *authored* below."""

    value: str
    label: str


@dataclass(frozen=True)
class LocalizedOption:
    """Authoring-time choice for questions with a fixed option set (as
    opposed to a DB-backed lookup, e.g. cities/platforms — see
    SurveySession.resolve_options). `value` is stable across languages;
    `label` carries all three translations."""

    value: str
    label: LocalizedText

    def resolve(self, lang: str) -> Option:
        return Option(self.value, localize(self.label, lang))


@dataclass(frozen=True)
class Condition:
    """A predicate evaluated against the answers collected so far.
    Currently unused by any question in SECTIONS (see this module's
    docstring) — kept because question_engine.py's navigation is written
    in terms of it, but nothing sets one any more."""

    field: str
    op: str  # eq | ne | in | truthy | falsy | is_none | is_not_none | gt
    value: Any = None

    def evaluate(self, answers: dict[str, Any]) -> bool:
        actual = answers.get(self.field)
        if self.op == "eq":
            return actual == self.value
        if self.op == "ne":
            return actual != self.value
        if self.op == "in":
            return actual in (self.value or ())
        if self.op == "truthy":
            return bool(actual)
        if self.op == "falsy":
            return not actual
        if self.op == "is_none":
            return actual is None
        if self.op == "is_not_none":
            return actual is not None
        if self.op == "gt":
            return actual is not None and actual > self.value
        raise ValueError(f"Unknown condition op: {self.op}")


@dataclass(frozen=True)
class Question:
    code: str
    text: LocalizedText
    qtype: QuestionType
    required: bool = True
    condition: Condition | None = None
    options: tuple[LocalizedOption, ...] | None = None
    # Resolved at render time by the application layer against the DB —
    # see SurveySession.resolve_options for the supported source names.
    options_source: str | None = None
    # Extra static options appended after options_source's dynamically
    # resolved ones (e.g. Q3's "All are about the same" / "Don't know"
    # alongside the driver's actually-selected platforms; Q9's earnings-
    # basis choices alongside its DB-backed lookup). Only meaningful
    # together with options_source.
    extra_options: tuple[LocalizedOption, ...] | None = None
    min_value: float | None = None
    max_value: float | None = None
    help_text: LocalizedText | None = None
    platform_name_in_text: bool = False
    # 1..QUESTION_COUNT for the interviewer-facing "Q{n}." numbering (see
    # presentation/formatters.py); None for the unnumbered setup/
    # screenshot steps before Q1 and after the last question.
    display_number: int | None = None


@dataclass(frozen=True)
class Section:
    index: int
    title: LocalizedText
    scope: SectionScope
    questions: tuple[Question, ...]


PLATFORM_LOOP_SECTIONS: tuple[int, ...] = ()
"""Empty: the current questionnaire has no per-platform repeat. Platform
information is collected once, in Q1 (platforms_used); question_engine.py
treats an empty tuple here as "there is no platform loop" — see
_advance_section/_retreat_section."""

QUESTION_COUNT = 13
SCREENSHOTS_SECTION_INDEX = 15

# ---- shared option labels, reused across several questions ------------------

_DONT_KNOW = {"en": "Don't know", "ru": "Не знаю", "uz": "Bilmayman"}
_OTHER = {"en": "Other", "ru": "Другое", "uz": "Boshqa"}


SECTIONS: tuple[Section, ...] = (
    Section(
        index=1,
        title={"en": "Survey setup", "ru": "Настройка опроса", "uz": "So'rovnomani sozlash"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "city",
                {
                    "en": "Which city is this interview in?",
                    "ru": "В каком городе проходит это интервью?",
                    "uz": "Ushbu intervyu qaysi shaharda o'tkazilmoqda?",
                },
                QuestionType.SINGLE_CHOICE,
                options_source="cities",
            ),
        ),
    ),
    Section(
        index=2,
        title={"en": "Q1. Ride-hailing apps", "ru": "В1. Приложения такси", "uz": "S1. Taksi ilovalari"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "platforms_used",
                {
                    "en": "Which ride-hailing apps do you currently drive for? Mention as many as you work for",
                    "ru": "В каких приложениях такси вы сейчас работаете? Укажите все, с которыми сотрудничаете",
                    "uz": "Hozirda qaysi taksi ilovalarida ishlaysiz? Ishlaydigan barcha ilovalaringizni belgilang",
                },
                QuestionType.MULTI_CHOICE,
                options_source="platforms",
                display_number=1,
            ),
        ),
    ),
    Section(
        index=3,
        title={"en": "Q2. Platform switching", "ru": "В2. Переключение платформ", "uz": "S2. Platforma almashtirish"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "switch_frequency",
                {
                    "en": "How often do you switch between apps?",
                    "ru": "Как часто вы переключаетесь между приложениями?",
                    "uz": "Ilovalar orasida qanchalik tez-tez almashasiz?",
                },
                QuestionType.SINGLE_CHOICE,
                options_source="switch_frequencies",
                display_number=2,
            ),
        ),
    ),
    Section(
        index=4,
        title={"en": "Q3. Best driver experience", "ru": "В3. Лучший опыт водителя", "uz": "S3. Eng yaxshi haydovchi tajribasi"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "best_experience",
                {
                    "en": "Which ride-hailing app gives you the best experience as a driver?",
                    "ru": "Какое приложение такси даёт вам лучший опыт как водителю?",
                    "uz": "Qaysi taksi ilovasi haydovchi sifatida sizga eng yaxshi tajriba beradi?",
                },
                QuestionType.SINGLE_CHOICE,
                options_source="survey_platforms",
                extra_options=(
                    LocalizedOption(
                        "same",
                        {
                            "en": "All are about the same",
                            "ru": "Все примерно одинаковые",
                            "uz": "Barchasi taxminan bir xil",
                        },
                    ),
                    LocalizedOption("dont_know", _DONT_KNOW),
                ),
                display_number=3,
            ),
        ),
    ),
    Section(
        index=5,
        title={"en": "Q4. Working pattern", "ru": "В4. Режим работы", "uz": "S4. Ish tartibi"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "days_per_week",
                {
                    "en": "How many days per week do you usually work?",
                    "ru": "Сколько дней в неделю вы обычно работаете?",
                    "uz": "Odatda haftasiga necha kun ishlaysiz?",
                },
                QuestionType.SINGLE_CHOICE,
                min_value=0,
                max_value=7,
                options=tuple(
                    LocalizedOption(
                        str(n),
                        {
                            "en": f"{n} day" if n == 1 else f"{n} days",
                            "ru": f"{n} день" if n == 1 else (f"{n} дня" if 2 <= n <= 4 else f"{n} дней"),
                            "uz": f"{n} kun",
                        },
                    )
                    for n in range(1, 8)
                ),
                display_number=4,
            ),
        ),
    ),
    Section(
        index=6,
        title={"en": "Q5. Driving hours & season", "ru": "В5. Часы вождения и сезон", "uz": "S5. Haydash soatlari va fasl"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "hours_and_season",
                {
                    "en": (
                        "How many hours on average do you drive a day? "
                        "And are your driving habits different during the winter and summer season? "
                        "Pick one hours option and one seasonal option."
                    ),
                    "ru": (
                        "Сколько часов в среднем вы водите в день? "
                        "И отличаются ли ваши привычки вождения зимой и летом? "
                        "Выберите один вариант часов и один сезонный вариант."
                    ),
                    "uz": (
                        "O'rtacha kuniga necha soat haydaysiz? "
                        "Va qish va yoz fasllarida haydash odatlaringiz farq qiladimi? "
                        "Bitta soat variantini va bitta fasl variantini tanlang."
                    ),
                },
                QuestionType.MULTI_CHOICE,
                options=(
                    LocalizedOption("1_2", {"en": "1 hr - 2 hrs", "ru": "1–2 часа", "uz": "1–2 soat"}),
                    LocalizedOption("3_4", {"en": "3 hrs - 4 hrs", "ru": "3–4 часа", "uz": "3–4 soat"}),
                    LocalizedOption("5_6", {"en": "5 hrs - 6 hrs", "ru": "5–6 часов", "uz": "5–6 soat"}),
                    LocalizedOption("7_8", {"en": "7 hrs - 8 hrs", "ru": "7–8 часов", "uz": "7–8 soat"}),
                    LocalizedOption("9_10", {"en": "9 hrs- 10 - hrs", "ru": "9–10 часов", "uz": "9–10 soat"}),
                    LocalizedOption("11_12", {"en": "11 hrs -12 hrs", "ru": "11–12 часов", "uz": "11–12 soat"}),
                    LocalizedOption("12_plus", {"en": "More than 12 hrs", "ru": "Более 12 часов", "uz": "12 soatdan ortiq"}),
                    LocalizedOption(
                        "same_year_round",
                        {
                            "en": "I drive the same on average throughout the year",
                            "ru": "В среднем я вожу одинаково в течение всего года",
                            "uz": "Yil davomida o'rtacha bir xil haydayman",
                        },
                    ),
                    LocalizedOption(
                        "more_in_summer",
                        {"en": "I drive more in summer", "ru": "Летом я вожу больше", "uz": "Yozda ko'proq haydayman"},
                    ),
                    LocalizedOption(
                        "more_in_winter",
                        {
                            "en": "I drive more in the winter season",
                            "ru": "Зимой я вожу больше",
                            "uz": "Qish faslida ko'proq haydayman",
                        },
                    ),
                ),
                display_number=5,
            ),
        ),
    ),
    Section(
        index=7,
        title={"en": "Q6. Driving category", "ru": "В6. Категория вождения", "uz": "S6. Haydash toifasi"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "main_category",
                {
                    "en": "Which category do you drive the most?",
                    "ru": "В какой категории вы ездите чаще всего?",
                    "uz": "Ko'proq qaysi toifada haydaysiz?",
                },
                QuestionType.SINGLE_CHOICE,
                options_source="ride_categories",
                extra_options=(LocalizedOption("dont_know", _DONT_KNOW),),
                display_number=6,
            ),
        ),
    ),
    Section(
        index=8,
        title={"en": "Q7. Trips", "ru": "В7. Поездки", "uz": "S7. Safarlar"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "trips_per_day_range",
                {
                    "en": "How many trips on average do you usually complete daily?",
                    "ru": "Сколько поездок в среднем вы обычно совершаете за день?",
                    "uz": "Odatda kuniga o'rtacha nechta safar bajarasiz?",
                },
                QuestionType.SINGLE_CHOICE,
                options=(
                    LocalizedOption("1_5", {"en": "1–5", "ru": "1–5", "uz": "1–5"}),
                    LocalizedOption("6_10", {"en": "6–10", "ru": "6–10", "uz": "6–10"}),
                    LocalizedOption("11_15", {"en": "11–15", "ru": "11–15", "uz": "11–15"}),
                    LocalizedOption("16_20", {"en": "16–20", "ru": "16–20", "uz": "16–20"}),
                    LocalizedOption("21_30", {"en": "21–30", "ru": "21–30", "uz": "21–30"}),
                    LocalizedOption("31_40", {"en": "31–40", "ru": "31–40", "uz": "31–40"}),
                    LocalizedOption("40_plus", {"en": "More than 40", "ru": "Более 40", "uz": "40 dan ortiq"}),
                    LocalizedOption("dont_know", _DONT_KNOW),
                ),
                display_number=7,
            ),
        ),
    ),
    Section(
        index=9,
        title={"en": "Q8. Commission", "ru": "В8. Комиссия", "uz": "S8. Komissiya"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "commission_range",
                {
                    "en": "What is the commission fee percentage each company takes from the total ride fare?",
                    "ru": "Какой процент комиссии берёт каждая компания от полной стоимости поездки?",
                    "uz": "Har bir kompaniya umumiy yo'l haqidan necha foiz komissiya oladi?",
                },
                QuestionType.SINGLE_CHOICE,
                options=(
                    LocalizedOption("0_5", {"en": "0–5%", "ru": "0–5%", "uz": "0–5%"}),
                    LocalizedOption("6_10", {"en": "6–10%", "ru": "6–10%", "uz": "6–10%"}),
                    LocalizedOption("11_15", {"en": "11–15%", "ru": "11–15%", "uz": "11–15%"}),
                    LocalizedOption("16_20", {"en": "16–20%", "ru": "16–20%", "uz": "16–20%"}),
                    LocalizedOption("21_25", {"en": "21–25%", "ru": "21–25%", "uz": "21–25%"}),
                    LocalizedOption("25_plus", {"en": "More than 25%", "ru": "Более 25%", "uz": "25%dan ortiq"}),
                    LocalizedOption(
                        "varies_by_platform",
                        {"en": "Different by platform", "ru": "Разное по платформам", "uz": "Platformaga qarab har xil"},
                    ),
                    LocalizedOption("dont_know", _DONT_KNOW),
                ),
                display_number=8,
            ),
        ),
    ),
    Section(
        index=10,
        title={"en": "Q9. Earnings", "ru": "В9. Доход", "uz": "S9. Daromad"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "earnings",
                {
                    "en": "On average, how much are you making each week with this app?",
                    "ru": "В среднем, сколько вы зарабатываете в неделю с этим приложением?",
                    "uz": "O'rtacha, ushbu ilova bilan haftasiga qancha ishlaysiz?",
                },
                QuestionType.MULTI_CHOICE,
                options=(
                    LocalizedOption(
                        "lt_500k",
                        {"en": "Less than 500,000 UZS", "ru": "Менее 500 000 сум", "uz": "500 000 so'mdan kam"},
                    ),
                    LocalizedOption(
                        "500k_1m",
                        {"en": "500,000 – 1,000,000 UZS", "ru": "500 000 – 1 000 000 сум", "uz": "500 000 – 1 000 000 so'm"},
                    ),
                    LocalizedOption(
                        "1m_1_5m",
                        {"en": "1,000,001 – 1,500,000 UZS", "ru": "1 000 001 – 1 500 000 сум", "uz": "1 000 001 – 1 500 000 so'm"},
                    ),
                    LocalizedOption(
                        "1_5m_2m",
                        {"en": "1,500,001 – 2,000,000 UZS", "ru": "1 500 001 – 2 000 000 сум", "uz": "1 500 001 – 2 000 000 so'm"},
                    ),
                    LocalizedOption(
                        "2m_3m",
                        {"en": "2,000,001 – 3,000,000 UZS", "ru": "2 000 001 – 3 000 000 сум", "uz": "2 000 001 – 3 000 000 so'm"},
                    ),
                    LocalizedOption(
                        "gt_3m",
                        {"en": "More than 3,000,000 UZS", "ru": "Более 3 000 000 сум", "uz": "3 000 000 so'mdan ortiq"},
                    ),
                    LocalizedOption(
                        "varies",
                        {"en": "Varies significantly", "ru": "Сильно варьируется", "uz": "Sezilarli darajada o'zgaradi"},
                    ),
                    LocalizedOption("dont_know", _DONT_KNOW),
                ),
                options_source="earnings_basis",
                display_number=9,
            ),
        ),
    ),
    Section(
        index=11,
        title={"en": "Q10. Bonuses", "ru": "В10. Бонусы", "uz": "S10. Bonuslar"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "bonus_type",
                {
                    "en": "Are you getting any bonuses from the ride-hailing apps you drive on?",
                    "ru": "Получаете ли вы какие-либо бонусы от приложений такси, в которых работаете?",
                    "uz": "Ishlayotgan taksi ilovalaridan biron-bir bonus olyapsizmi?",
                },
                QuestionType.SINGLE_CHOICE,
                options=(
                    LocalizedOption("none", {"en": "No bonuses", "ru": "Без бонусов", "uz": "Bonuslar yo'q"}),
                    LocalizedOption(
                        "trip_based", {"en": "Yes — trip-based", "ru": "Да — за количество поездок", "uz": "Ha — safarlar soniga qarab"}
                    ),
                    LocalizedOption(
                        "time_based", {"en": "Yes — time-based", "ru": "Да — по времени", "uz": "Ha — vaqtga qarab"}
                    ),
                    LocalizedOption(
                        "performance_based",
                        {"en": "Yes — performance-based", "ru": "Да — за показатели", "uz": "Ha — natijalarga qarab"},
                    ),
                    LocalizedOption(
                        "multiple_types", {"en": "Yes — multiple types", "ru": "Да — несколько видов", "uz": "Ha — bir nechta turi"}
                    ),
                    LocalizedOption("other", _OTHER),
                    LocalizedOption("dont_know", _DONT_KNOW),
                ),
                display_number=10,
            ),
        ),
    ),
    Section(
        index=12,
        title={"en": "Q11. Cash payments", "ru": "В11. Оплата наличными", "uz": "S11. Naqd to'lovlar"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "cash_pct",
                {
                    "en": "Percentage of payments made with cash?",
                    "ru": "Какой процент оплат производится наличными?",
                    "uz": "To'lovlarning necha foizi naqd pulda amalga oshiriladi?",
                },
                QuestionType.SINGLE_CHOICE,
                options=(
                    LocalizedOption("0", {"en": "0%", "ru": "0%", "uz": "0%"}),
                    LocalizedOption("1_10", {"en": "1–10%", "ru": "1–10%", "uz": "1–10%"}),
                    LocalizedOption("11_25", {"en": "11–25%", "ru": "11–25%", "uz": "11–25%"}),
                    LocalizedOption("26_50", {"en": "26–50%", "ru": "26–50%", "uz": "26–50%"}),
                    LocalizedOption("51_75", {"en": "51–75%", "ru": "51–75%", "uz": "51–75%"}),
                    LocalizedOption("76_99", {"en": "76–99%", "ru": "76–99%", "uz": "76–99%"}),
                    LocalizedOption("100", {"en": "100%", "ru": "100%", "uz": "100%"}),
                    LocalizedOption("dont_know", _DONT_KNOW),
                ),
                display_number=11,
            ),
        ),
    ),
    Section(
        index=13,
        title={"en": "Q12. Driver type and loyalty", "ru": "В12. Тип водителя и лояльность", "uz": "S12. Haydovchi turi va sodiqlik"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "driver_type_loyalty",
                {
                    "en": "How do you work with ride-hailing platforms?",
                    "ru": "Как вы работаете с платформами такси?",
                    "uz": "Taksi platformalari bilan qanday ishlaysiz?",
                },
                QuestionType.MULTI_CHOICE,
                options=(
                    LocalizedOption(
                        "independent", {"en": "Independent driver", "ru": "Независимый водитель", "uz": "Mustaqil haydovchi"}
                    ),
                    LocalizedOption("fleet", {"en": "Fleet company", "ru": "Автопарк (таксопарк)", "uz": "Avtopark kompaniyasi"}),
                    LocalizedOption(
                        "has_loyalty_program",
                        {
                            "en": "Royalty/point program",
                            "ru": "Программа лояльности/баллов",
                            "uz": "Sodiqlik/ball dasturi",
                        },
                    ),
                    LocalizedOption(
                        "no_loyalty_program",
                        {
                            "en": "No royalty/point program",
                            "ru": "Нет программы лояльности/баллов",
                            "uz": "Sodiqlik/ball dasturi yo'q",
                        },
                    ),
                    LocalizedOption("dont_know", _DONT_KNOW),
                ),
                display_number=12,
            ),
        ),
    ),
    Section(
        index=14,
        title={"en": "Q13. Driver experience / motivation", "ru": "В13. Опыт водителя / мотивация", "uz": "S13. Haydovchi tajribasi / motivatsiya"},
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "driver_motivation",
                {
                    "en": "What would improve your experience as a ride-hailing driver or be the main reason for you to join a new ride-hailing app?",
                    "ru": "Что улучшило бы ваш опыт как водителя такси, или что стало бы главной причиной присоединиться к новому приложению такси?",
                    "uz": "Nima taksi haydovchisi sifatidagi tajribangizni yaxshilardi yoki yangi taksi ilovasiga qo'shilishingiz uchun asosiy sabab bo'lardi?",
                },
                QuestionType.MULTI_CHOICE,
                options=(
                    LocalizedOption(
                        "higher_earnings", {"en": "Higher earnings", "ru": "Более высокий доход", "uz": "Yuqoriroq daromad"}
                    ),
                    LocalizedOption(
                        "lower_commission", {"en": "Lower commission", "ru": "Более низкая комиссия", "uz": "Pastroq komissiya"}
                    ),
                    LocalizedOption("more_bonuses", {"en": "More bonuses", "ru": "Больше бонусов", "uz": "Ko'proq bonuslar"}),
                    LocalizedOption(
                        "better_rider_demand",
                        {"en": "Better rider demand", "ru": "Больший спрос от пассажиров", "uz": "Yaxshiroq yo'lovchi talabi"},
                    ),
                    LocalizedOption(
                        "more_rider_discounts",
                        {"en": "More rider discounts", "ru": "Больше скидок для пассажиров", "uz": "Ko'proq yo'lovchi chegirmalari"},
                    ),
                    LocalizedOption(
                        "faster_payments", {"en": "Faster payments", "ru": "Более быстрые выплаты", "uz": "Tezroq to'lovlar"}
                    ),
                    LocalizedOption(
                        "better_driver_support",
                        {"en": "Better driver support", "ru": "Лучшая поддержка водителей", "uz": "Yaxshiroq haydovchi qo'llab-quvvatlashi"},
                    ),
                    LocalizedOption(
                        "better_app_technology",
                        {"en": "Better app/technology", "ru": "Лучшее приложение/технологии", "uz": "Yaxshiroq ilova/texnologiya"},
                    ),
                    LocalizedOption(
                        "better_loyalty_program",
                        {
                            "en": "Better loyalty/point program",
                            "ru": "Лучшая программа лояльности/баллов",
                            "uz": "Yaxshiroq sodiqlik/ball dasturi",
                        },
                    ),
                    LocalizedOption(
                        "more_flexible_working",
                        {
                            "en": "More flexible working conditions",
                            "ru": "Более гибкие условия работы",
                            "uz": "Ko'proq moslashuvchan ish sharoitlari",
                        },
                    ),
                    LocalizedOption(
                        "better_vehicle_category",
                        {
                            "en": "Better vehicle/category opportunities",
                            "ru": "Лучшие возможности по автомобилю/категории",
                            "uz": "Avtomobil/toifa bo'yicha yaxshiroq imkoniyatlar",
                        },
                    ),
                    LocalizedOption("other", _OTHER),
                    LocalizedOption(
                        "satisfied",
                        {
                            "en": "Nothing / satisfied with current options",
                            "ru": "Ничего / устраивают текущие условия",
                            "uz": "Hech narsa / hozirgi variantlardan mamnunman",
                        },
                    ),
                    LocalizedOption("dont_know", _DONT_KNOW),
                ),
                display_number=13,
            ),
        ),
    ),
    Section(
        index=15,
        title={
            "en": "Driver statistics / screenshots",
            "ru": "Статистика водителя / скриншоты",
            "uz": "Haydovchi statistikasi / skrinshotlar",
        },
        scope=SectionScope.SURVEY,
        questions=(
            Question(
                "has_screenshots",
                {
                    "en": "Do you have screenshots of your driver statistics that you are willing to share?",
                    "ru": "Есть ли у вас скриншоты вашей статистики водителя, которыми вы готовы поделиться?",
                    "uz": "Ulashishga tayyor bo'lgan haydovchi statistikangiz skrinshotlari bormi?",
                },
                QuestionType.YES_NO,
            ),
        ),
    ),
    Section(
        index=16,
        title={"en": "Review and submission", "ru": "Проверка и отправка", "uz": "Ko'rib chiqish va yuborish"},
        scope=SectionScope.SURVEY,
        questions=(),
    ),
)

SECTIONS_BY_INDEX: dict[int, Section] = {s.index: s for s in SECTIONS}

QUESTIONS_BY_DISPLAY_NUMBER: dict[int, Question] = {
    q.display_number: q for s in SECTIONS for q in s.questions if q.display_number is not None
}
assert len(QUESTIONS_BY_DISPLAY_NUMBER) == QUESTION_COUNT, "QUESTIONS_BY_DISPLAY_NUMBER must match QUESTION_COUNT exactly"


def visible_questions(section: Section, answers_view: dict[str, Any]) -> list[Question]:
    return [q for q in section.questions if q.condition is None or q.condition.evaluate(answers_view)]
