"""Translated UI chrome: everything that isn't a questionnaire question
itself (see questionnaire.py for those) — navigation buttons, prompts,
validation/error messages, the review screen, and completion message.

Framework-free (pure dicts/functions), like the rest of domain/, so both
application/ (which needs lookup-option translations to build fully
localized Option lists) and presentation/ (which needs everything else)
can depend on it without crossing layers the wrong way.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LANGUAGE = "en"

LANGUAGE_NAMES: dict[str, str] = {
    "en": "\U0001f1ec\U0001f1e7 English",
    "ru": "\U0001f1f7\U0001f1fa Русский",
    "uz": "\U0001f1fa\U0001f1ff O'zbekcha",
}


def t(key: str, lang: str, **params: Any) -> str:
    entry = UI_STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANGUAGE) or next(iter(entry.values()), key)
    return text.format(**params) if params else text


def translate_lookup(table: str, code: str, lang: str, fallback: str) -> str:
    """Localized label for a DB-lookup-backed option (switch_frequencies,
    earnings_basis, ...), keyed by the lookup row's stable `code` — never
    its `name`, since `name` is the English display text itself and isn't
    guaranteed to be a valid dict key format. Lookups with no translation
    entry (cities, platforms, ...) fall back to the DB's own `name`
    untouched — this is deliberate: platform/city names are proper nouns
    the brief explicitly says should NOT be translated.
    """
    table_entries = LOOKUP_TRANSLATIONS.get(table)
    if not table_entries:
        return fallback
    code_entry = table_entries.get(code)
    if not code_entry:
        return fallback
    return code_entry.get(lang) or code_entry.get(DEFAULT_LANGUAGE) or fallback


def validation_message(code: str, lang: str, **params: Any) -> str:
    entry = VALIDATION_MESSAGES.get(code, VALIDATION_MESSAGES["generic"])
    text = entry.get(lang) or entry.get(DEFAULT_LANGUAGE) or next(iter(entry.values()))
    return text.format(**params) if params else text


UI_STRINGS: dict[str, dict[str, str]] = {
    "back": {"en": "← Back", "ru": "← Назад", "uz": "← Orqaga"},
    "skip": {"en": "Skip", "ru": "Пропустить", "uz": "O'tkazib yuborish"},
    "done": {"en": "Done ✓", "ru": "Готово ✓", "uz": "Tayyor ✓"},
    "yes": {"en": "Yes", "ru": "Да", "uz": "Ha"},
    "no": {"en": "No", "ru": "Нет", "uz": "Yo'q"},
    "confirm_submit": {"en": "Confirm / Submit", "ru": "Подтвердить / Отправить", "uz": "Tasdiqlash / Yuborish"},
    "edit_back": {"en": "Edit / Back", "ru": "Изменить / Назад", "uz": "Tahrirlash / Orqaga"},
    "start_new_survey": {"en": "Start New Survey", "ru": "Начать новый опрос", "uz": "Yangi so'rovnoma boshlash"},
    "begin_survey": {"en": "Begin survey", "ru": "Начать опрос", "uz": "So'rovnomani boshlash"},
    "finish_uploading": {"en": "Finish uploading", "ru": "Завершить загрузку", "uz": "Yuklashni tugatish"},
    "upload_another_screenshot": {"en": "Upload another screenshot", "ru": "Загрузить ещё один скриншот", "uz": "Yana bitta skrinshot yuklash"},
    "not_platform_specific": {"en": "Not platform-specific", "ru": "Не привязано к платформе", "uz": "Muayyan platformaga bog'liq emas"},

    "section_label": {
        "en": "Section {index} of {total} — {title}",
        "ru": "Раздел {index} из {total} — {title}",
        "uz": "{index}-bo'lim, jami {total} — {title}",
    },
    "question_number_prefix": {
        "en": "Q{n}. {text}",
        "ru": "В{n}. {text}",
        "uz": "S{n}. {text}",
    },
    "platform_progress_suffix": {
        "en": " ({name}, platform {position} of {total})",
        "ru": " ({name}, платформа {position} из {total})",
        "uz": " ({name}, {position}-platforma, jami {total})",
    },

    "select_language_prompt": {
        "en": "Please select your language:",
        "ru": "Пожалуйста, выберите язык:",
        "uz": "Iltimos, tilingizni tanlang:",
    },
    "survey_in_progress": {
        "en": "You have a survey in progress — continuing where you left off. Send /restart to discard it and start over instead.",
        "ru": "У вас есть незавершённый опрос — продолжаем с того места, где вы остановились. Отправьте /restart, чтобы отменить его и начать заново.",
        "uz": "Sizda tugallanmagan so'rovnoma bor — to'xtagan joyingizdan davom etamiz. Uni bekor qilib, qaytadan boshlash uchun /restart yuboring.",
    },
    "welcome_prompt": {
        "en": "Driver market research survey.\n\nPress Begin to start a new interview.",
        "ru": "Опрос для исследования рынка водителей.\n\nНажмите «Начать», чтобы начать новое интервью.",
        "uz": "Haydovchilar bozorini tadqiq qilish so'rovnomasi.\n\nYangi intervyuni boshlash uchun \"Boshlash\" tugmasini bosing.",
    },
    "restart_confirm_prompt": {
        "en": "Restart will discard the current survey's progress and start a brand-new one. Are you sure?",
        "ru": "Перезапуск удалит текущий прогресс опроса и начнёт новый. Вы уверены?",
        "uz": "Qayta boshlash joriy so'rovnoma jarayonini bekor qiladi va yangisini boshlaydi. Ishonchingiz komilmi?",
    },
    "restart_yes": {"en": "Yes, restart", "ru": "Да, перезапустить", "uz": "Ha, qayta boshlash"},
    "restart_no": {"en": "No, keep going", "ru": "Нет, продолжить", "uz": "Yo'q, davom etish"},
    "restart_started": {"en": "Survey restarted.", "ru": "Опрос перезапущен.", "uz": "So'rovnoma qayta boshlandi."},
    "restart_continuing": {"en": "Continuing.", "ru": "Продолжаем.", "uz": "Davom etilmoqda."},
    "no_survey_in_progress": {
        "en": "No survey in progress yet. Send /start to begin one.",
        "ru": "Пока нет активного опроса. Отправьте /start, чтобы начать.",
        "uz": "Hozircha faol so'rovnoma yo'q. Boshlash uchun /start yuboring.",
    },
    "stale_step_notice": {
        "en": "That step has moved on — here's where we are now.",
        "ru": "Этот шаг уже пройден — вот где мы сейчас.",
        "uz": "Bu bosqich allaqachon o'tib ketgan — hozir qayerda ekanimiz shu.",
    },
    "please_use_buttons": {
        "en": "Please use the buttons above to answer the current question.",
        "ru": "Пожалуйста, используйте кнопки выше, чтобы ответить на текущий вопрос.",
        "uz": "Iltimos, joriy savolga javob berish uchun yuqoridagi tugmalardan foydalaning.",
    },
    "please_try_again": {
        "en": "Please try again.",
        "ru": "Попробуйте ещё раз.",
        "uz": "Iltimos, qaytadan urinib ko'ring.",
    },

    "screenshot_platform_prompt": {
        "en": "Which platform is this screenshot for?",
        "ru": "Для какой платформы этот скриншот?",
        "uz": "Bu skrinshot qaysi platforma uchun?",
    },
    "screenshot_description_prompt": {
        "en": "What does this screenshot show? Pick a period below, or type a short description.",
        "ru": "Что показано на этом скриншоте? Выберите период ниже или напишите краткое описание.",
        "uz": "Bu skrinshotda nima ko'rsatilgan? Quyidan davrni tanlang yoki qisqacha tavsif yozing.",
    },
    "screenshot_upload_prompt": {
        "en": "Send a photo, or an image file as a document,{progress}\n\nYou can upload screenshots for the current week, previous week, previous 4 weeks/month, or any other period — send as many as you have, or press Finish uploading if none are available.",
        "ru": "Отправьте фото или изображение файлом-документом,{progress}\n\nВы можете загрузить скриншоты за текущую неделю, прошлую неделю, предыдущие 4 недели/месяц или любой другой период — отправьте столько, сколько есть, или нажмите «Завершить загрузку», если их нет.",
        "uz": "Foto yoki hujjat sifatida rasm faylini yuboring,{progress}\n\nJoriy hafta, o'tgan hafta, oldingi 4 hafta/oy yoki boshqa istalgan davr uchun skrinshotlar yuklashingiz mumkin — nechta bo'lsa shuncha yuboring, yoki agar yo'q bo'lsa \"Yuklashni tugatish\" tugmasini bosing.",
    },
    "screenshot_progress_suffix": {
        "en": " ({count} uploaded so far.)",
        "ru": " (уже загружено: {count}.)",
        "uz": " (hozirgacha {count} ta yuklandi.)",
    },
    "screenshot_finish_previous_step": {
        "en": "Please finish the previous step first.",
        "ru": "Сначала завершите предыдущий шаг.",
        "uz": "Iltimos, avval oldingi bosqichni tugating.",
    },
    "screenshot_wrong_type": {
        "en": "That file type isn't supported — please send a photo, or an image file (JPEG, PNG, etc.) as a document.",
        "ru": "Этот тип файла не поддерживается — отправьте фото или изображение (JPEG, PNG и т.д.) как документ.",
        "uz": "Bu fayl turi qo'llab-quvvatlanmaydi — iltimos, foto yoki rasm faylini (JPEG, PNG va h.k.) hujjat sifatida yuboring.",
    },
    "screenshot_saved": {
        "en": "Saved. {count} screenshot(s) uploaded so far.",
        "ru": "Сохранено. Всего загружено скриншотов: {count}.",
        "uz": "Saqlandi. Hozirgacha {count} ta skrinshot yuklandi.",
    },
    "screenshot_period_current_week": {"en": "Current week", "ru": "Текущая неделя", "uz": "Joriy hafta"},
    "screenshot_period_previous_week": {"en": "Previous week", "ru": "Прошлая неделя", "uz": "O'tgan hafta"},
    "screenshot_period_previous_month": {"en": "Previous 4 weeks / month", "ru": "Предыдущие 4 недели / месяц", "uz": "Oldingi 4 hafta / oy"},
    "screenshot_period_other": {"en": "Other", "ru": "Другое", "uz": "Boshqa"},

    "review_title": {"en": "SURVEY #{code}", "ru": "ОПРОС #{code}", "uz": "SO'ROVNOMA #{code}"},
    "review_city": {"en": "City", "ru": "Город", "uz": "Shahar"},
    "review_platforms": {"en": "Platforms", "ru": "Платформы", "uz": "Platformalar"},
    "review_working": {"en": "Working", "ru": "Работа", "uz": "Ish"},
    "review_market": {"en": "Market", "ru": "Рынок", "uz": "Bozor"},
    "review_driver": {"en": "Driver", "ru": "Водитель", "uz": "Haydovchi"},
    "review_days_per_week": {"en": "days/week", "ru": "дней/нед.", "uz": "kun/hafta"},
    "review_commission": {"en": "Commission", "ru": "Комиссия", "uz": "Komissiya"},
    "review_weekly_earnings": {"en": "Weekly earnings", "ru": "Доход в неделю", "uz": "Haftalik daromad"},
    "review_bonuses": {"en": "Bonuses", "ru": "Бонусы", "uz": "Bonuslar"},
    "review_market_leader": {"en": "Market leader", "ru": "Лидер рынка", "uz": "Bozor yetakchisi"},
    "review_cash_payments": {"en": "Cash payments", "ru": "Оплата наличными", "uz": "Naqd to'lovlar"},
    "review_not_answered": {"en": "not answered", "ru": "нет ответа", "uz": "javob berilmagan"},
    "review_yes": {"en": "yes", "ru": "да", "uz": "ha"},
    "review_no": {"en": "no", "ru": "нет", "uz": "yo'q"},

    "completion_title": {
        "en": "✅ Survey completed successfully!",
        "ru": "✅ Опрос успешно завершён!",
        "uz": "✅ So'rovnoma muvaffaqiyatli yakunlandi!",
    },
    "completion_survey_id": {"en": "Survey ID", "ru": "Номер опроса", "uz": "So'rovnoma ID"},
    "completion_thanks": {
        "en": "Thank you for participating.",
        "ru": "Спасибо за участие.",
        "uz": "Ishtirok etganingiz uchun rahmat.",
    },
}


LOOKUP_TRANSLATIONS: dict[str, dict[str, dict[str, str]]] = {
    "switch_frequencies": {
        "never_same_app": {"ru": "Никогда, всегда использую одно приложение", "uz": "Hech qachon, doim bitta ilovadan foydalanaman"},
        "sometimes_pick_hours": {"ru": "Иногда (например, выбираю часы)", "uz": "Ba'zan (masalan, soatlarni tanlab)"},
        "once_a_day": {"ru": "Один раз в день", "uz": "Kuniga bir marta"},
        "few_times_a_day": {"ru": "Несколько раз в день", "uz": "Kuniga bir necha marta"},
        # Legacy (retired) options — translated too, in case an old draft
        # survey (started before the questionnaire replacement) is ever
        # resumed and re-renders its already-selected value.
        "never": {"ru": "Никогда не переключается", "uz": "Hech qachon almashmaydi"},
        "rarely": {"ru": "Редко (несколько раз в месяц)", "uz": "Kamdan-kam (oyiga bir necha marta)"},
        "weekly": {"ru": "Еженедельно", "uz": "Haftalik"},
        "daily": {"ru": "Ежедневно / каждую смену", "uz": "Har kuni / har smenada"},
    },
    "earnings_basis": {
        "gross": {"ru": "До вычета комиссии", "uz": "Komissiyagacha (yalpi)"},
        "net": {"ru": "После вычета комиссии", "uz": "Komissiyadan keyin (sof)"},
        "unknown": {"ru": "Неизвестно / водитель не уверен", "uz": "Noma'lum / haydovchi aniq bilmaydi"},
    },
    "ride_categories": {
        "economy": {"ru": "Эконом", "uz": "Ekonom"},
        "comfort": {"ru": "Комфорт", "uz": "Komfort"},
        "comfort_plus": {"ru": "Комфорт+", "uz": "Komfort+"},
        "business": {"ru": "Бизнес", "uz": "Biznes"},
        "minivan": {"ru": "Минивэн", "uz": "Minivan"},
        "delivery": {"ru": "Доставка", "uz": "Yetkazib berish"},
        "other": {"ru": "Другое", "uz": "Boshqa"},
    },
}


VALIDATION_MESSAGES: dict[str, dict[str, str]] = {
    "generic": {
        "en": "This question requires an answer.",
        "ru": "На этот вопрос нужен ответ.",
        "uz": "Bu savolga javob berish shart.",
    },
    "required": {
        "en": "This question requires an answer.",
        "ru": "На этот вопрос нужен ответ.",
        "uz": "Bu savolga javob berish shart.",
    },
    "empty": {
        "en": "Please send a number.",
        "ru": "Пожалуйста, отправьте число.",
        "uz": "Iltimos, raqam yuboring.",
    },
    "not_a_number": {
        "en": "That doesn't look like a number — please try again.",
        "ru": "Это не похоже на число — попробуйте ещё раз.",
        "uz": "Bu raqamga o'xshamayapti — iltimos, qaytadan urinib ko'ring.",
    },
    "too_low": {
        "en": "Value must be at least {min_value:g}.",
        "ru": "Значение должно быть не меньше {min_value:g}.",
        "uz": "Qiymat kamida {min_value:g} bo'lishi kerak.",
    },
    "too_high": {
        "en": "Value must be at most {max_value:g}.",
        "ru": "Значение должно быть не больше {max_value:g}.",
        "uz": "Qiymat ko'pi bilan {max_value:g} bo'lishi kerak.",
    },
    "select_at_least_one_platform": {
        "en": "Select at least one platform.",
        "ru": "Выберите хотя бы одну платформу.",
        "uz": "Kamida bitta platformani tanlang.",
    },
    "select_at_least_one_option": {
        "en": "Please select at least one option.",
        "ru": "Пожалуйста, выберите хотя бы один вариант.",
        "uz": "Iltimos, kamida bitta variantni tanlang.",
    },
    "hours_and_season_invalid": {
        "en": "Please select exactly one hours-per-day option and one seasonal option.",
        "ru": "Пожалуйста, выберите ровно один вариант часов в день и один сезонный вариант.",
        "uz": "Iltimos, aynan bitta kuniga soat variantini va bitta fasl variantini tanlang.",
    },
    "earnings_invalid": {
        "en": "Please select exactly one earnings range and one basis option.",
        "ru": "Пожалуйста, выберите ровно один диапазон дохода и один вариант базы расчёта.",
        "uz": "Iltimos, aynan bitta daromad oralig'ini va bitta hisoblash asosini tanlang.",
    },
}
