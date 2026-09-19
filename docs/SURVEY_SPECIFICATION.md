# Survey Specification

This is the authoritative reference for what the bot asks, in what
language, in what order, under what conditions, and where each answer
lands in the database. It describes the **current** questionnaire (12
sections, 26 driver-facing questions, three languages) and must be kept in
sync with
[`src/telegram_bot/domain/questionnaire.py`](../src/telegram_bot/domain/questionnaire.py)
(question content/branching),
[`src/telegram_bot/domain/i18n.py`](../src/telegram_bot/domain/i18n.py)
(UI chrome translations), and the model files under
[`src/telegram_bot/infrastructure/db/models/`](../src/telegram_bot/infrastructure/db/models/)
(database fields).

This questionnaire **replaced** an earlier 16-section flow. Nothing from
that flow was deleted — old columns and old survey data are still readable
(see "Legacy fields" at the end of each section's table where relevant) —
but it is no longer asked to new drivers. Do not use an older copy of this
document as a reference.

## How to read this document

- **Type** — one of the 8 reusable input components: `single_choice`,
  `multi_choice`, `number`, `percentage`, `currency`, `text`, `yes_no`,
  `photo`.
- **Required** — if optional, the interviewer sees a Skip button and the
  field is left `NULL` rather than forced.
- **Condition** — when the question is shown at all. `—` means always
  shown (subject only to its section/platform repeat).
- **DB field** — `table.column`. `(platform-scoped)` means one row/value
  per platform the driver reported using; everything else is one row per
  survey.
- **Scope** — `survey` (asked once) or `platform` (repeated once for each
  platform the driver reported working with — Sections 5–7).

## Language / multilingual support

Three languages, selected **before Question 1**:

| Code | Language | Flag shown |
|---|---|---|
| `en` | English | 🇬🇧 |
| `ru` | Русский (Russian) | 🇷🇺 |
| `uz` | O'zbekcha (Uzbek) | 🇺🇿 |

Flow:

```
/start
  ↓
Please select your language:
  🇬🇧 English   🇷🇺 Русский   🇺🇿 O'zbekcha
  ↓
Survey begins (Section 1 — city)
```

- Language selection is **not** a questionnaire section — it has no
  `SurveySession` position of its own. It's a dedicated aiogram FSM state
  (`SurveyStates.language_select`, see `presentation/states.py`), handled
  entirely in `presentation/handlers/survey.py` (`cmd_start` /
  `cb_select_language`) before a `SurveySession` is created.
- The chosen code is held on `CursorState.language` until the `Survey` row
  exists (created on the `city` answer — see Section 1), then written to
  `surveys.language` (`NOT NULL`, native Postgres enum, default `'en'`)
  and read from there for the rest of the survey's life —
  `SurveySession.language` / `.set_language()`.
- **Resuming a draft** (`/start` with an in-progress survey) uses the
  language already stored on that survey — the picker is not shown again.
- **Starting a new survey** (`/restart` → confirm, or "Start New Survey"
  after completion) always shows the language picker again — a fresh
  survey is a fresh language choice.
- **Translated content**: every question's text and every static option's
  label is a `{"en": ..., "ru": ..., "uz": ...}` dict on the `Question`/
  `Option` objects in `questionnaire.py`, resolved at render time via
  `domain.questionnaire.localize()`. UI chrome (Back/Skip/Done/Confirm/
  Edit, validation and error messages, the review screen's labels, the
  completion message, the screenshot-upload prompts) lives in
  `domain/i18n.py` (`UI_STRINGS`, `VALIDATION_MESSAGES`), keyed the same
  way. DB-lookup-backed options that still need translation
  (`switch_frequencies`, `earnings_basis`) are translated by **stable
  lookup `code`**, in `i18n.LOOKUP_TRANSLATIONS` — never by their English
  `name`, and never by re-keying the row itself.
- **What is deliberately NOT translated**: city and platform names
  (`cities.name`, `platforms.name`) are shown as-is in every language —
  they're proper nouns, not questionnaire content. The *stored* answer
  value for every question (a platform id, a percentage, an enum code,
  the interviewer's language selection itself) never depends on language;
  only what's *displayed* does.
- **Validation/error messages** are localized too:
  `application/question_engine.ValidationError` carries a stable `code`
  (`"empty"`, `"not_a_number"`, `"too_low"`, `"too_high"`, `"required"`,
  `"select_at_least_one_platform"`) plus format params (e.g.
  `min_value`/`max_value`); the presentation layer resolves the actual
  message via `i18n.validation_message(code, lang, **params)`. English
  wording is unchanged from before this was localized.

## Reusable question components

| Component | Telegram rendering | Validation |
|---|---|---|
| `single_choice` | One inline button per option | Value must be one of the rendered options |
| `multi_choice` | Toggleable inline buttons + localized "Done" | At least one selection required to press Done |
| `number` | Free-text reply | Must parse as a number; `min_value`/`max_value` enforced when set |
| `percentage` | Free-text reply | Must parse as a number in [0, 100] |
| `currency` | Free-text reply | Must parse as a number ≥ `min_value` (default 0) |
| `text` | Free-text reply | Stored verbatim (raw qualitative answer, not normalized) |
| `yes_no` | Localized "Yes" / "No" inline buttons | — |
| `photo` / `document` | Telegram photo or image-file upload | Section 11's screenshot upload loop only (see below) — not the generic question engine |

Every question screen also offers a localized **Back** button (unless
it's the very first question) and **Skip** (when `required=False`). The
screenshot upload loop is the one exception to Back.

## Section 1 — Survey setup

*Unchanged from the previous questionnaire — kept as-is per the
replacement's scope.*

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `city` | Which city is this interview in? | single_choice | yes | — | `surveys.city_id` |
| `target_platform` | Is there a specific platform this interview is targeting? (optional) | single_choice | no | — | `surveys.target_platform_id` |

`surveys.human_code` (e.g. `TAS-000123`), `surveys.id` (UUID),
`surveys.language`, `interviewer_id`, `survey_datetime`, `started_at` are
all set automatically the moment `city` is answered (this is also the
first point a `Survey` row exists at all — see `SurveySession._dispatch_answer`).

## Section 2 — Driver platform usage

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `platforms_used` | Which ride-hailing apps do you currently drive for? Mention as many as you work for | multi_choice | yes | — | `survey_platforms` (one row per selection) |
| `platform_other_name` | You selected "Other" — what is the platform called? | text | yes | an "Other" platform was selected | `survey_platforms.platform_other_name` |

The selected platform **ids** (not names) become the ordered list every
per-platform section (5–7) repeats over. Changing this selection later
(via Back) deletes the `survey_platforms` row — and everything hanging off
it via cascade (`earnings`, `bonuses`, `payments`, `market_intelligence`)
— for any platform that's no longer selected; nothing stale is left
behind (`SurveySession._answer_platforms_used`).

## Section 3 — Multi-app behavior

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `switch_frequency` | How often do you switch between apps? | single_choice | yes | more than one platform selected | `survey_platforms.switch_frequency_id` (same value applied to every platform row) |
| `best_experience_platform` | If you are working with more than one app, which of these apps gives you the best experience as a driver? | single_choice | no | more than one platform selected | `surveys.best_experience_platform_id` |
| `is_exclusive` | If you are working with just one app, are you working with that app exclusively? | yes_no | yes | exactly one platform selected | `survey_platforms.is_exclusive` |

`switch_frequency`'s four current options ("Never, I always use the same
app" / "Sometimes (e.g. pick hours)" / "Once a day" / "A few times a
day") are new `switch_frequencies` lookup rows added by migration
`da9dcac30c59`; the previous questionnaire's four options (never/rarely/
weekly/daily) are kept in the same table with `is_active=false` so old
surveys' foreign keys stay valid, but are never offered to new surveys.

`best_experience_platform`'s options are the *selected* platforms only
(`options_source="survey_platforms"`) — never an unselected one.

Exactly one of `best_experience_platform`/`is_exclusive` is ever shown,
determined purely by how many platforms were selected in Section 2 — this
is the questionnaire's canonical example of the platform-count
conditional (`Condition("_platform_count", "gt"/"eq", ...)`).

## Section 4 — General working pattern

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `days_per_week` | How many days per week do you usually work? | single_choice (1–7, labeled "1 day"/"2 days"/…/"7 days") | yes | — | `working_stats.days_per_week` |
| `hours_per_day` | How many hours on average do you drive a day? | single_choice (fixed buckets, see below) | yes | — | `working_stats.hours_per_day_bucket` (+ `hours_per_day`, derived) |
| `main_category_text` | Which category do you drive the most? | text | no | — | `driver_experience.main_category_text` |
| `category_requirements_text` | Which ones are the requirements to drive in each category? | text | no | — | `driver_experience.category_requirements_text` |
| `trips_summary_text` | How many trips on average do you usually complete daily? Or weekly? | text | no | — | `working_stats.trips_summary_text` |

`hours_per_day`'s options, verbatim: **"1 hr - 2 hrs"**, **"3 hrs - 4
hrs"**, **"5 hrs - 6 hrs"**, **"7 hrs - 8 hrs"**, **"9 hrs- 10 - hrs"**,
**"11 hrs -12 hrs"**, **"More than 12 hrs"** — the canonical wording is
preserved exactly, including its irregular spacing/hyphenation. The
selected bucket is stored structurally on `hours_per_day_bucket` (a
native enum: `1_2`/`3_4`/`5_6`/`7_8`/`9_10`/`11_12`/`12_plus`); its
representative numeric midpoint (1.5, 3.5, ..., 13.0) is also written to
the legacy `hours_per_day` numeric column so existing AVG/MEDIAN
statistics keep working without a query rewrite — see
`application/survey_session._HOURS_BUCKET_MIDPOINT`.

`days_per_week` was already a fixed 1–7 single-choice before the
questionnaire replacement; unchanged here.

**Legacy fields, no longer asked**: `working_stats.trips_per_day` /
`.trips_per_week` (structured daily/weekly trip counts) — the current
questionnaire deliberately asks trips as one open-text question instead
(`trips_summary_text`), per "do not force the interviewer into
daily/weekly predefined fields."

## Sections 5–7 — repeated once per platform selected in Section 2

These three sections run through in full for platform 1, then again for
platform 2, etc. (`Section 5 of 12 (Yandex Go, platform 1 of 2)`). The
current platform's name is always shown in the progress label, and is
substituted for the literal **"x"**/**"X"** placeholder in two questions'
canonical wording (Q13, Q18 below) via `{platform}` in the question's
`LocalizedText` and `Question.platform_name_in_text=True` —
`presentation/formatters.question_message` does the substitution at
render time; the underlying stored answer never depends on which
platform it was, only the *displayed prompt* does.

### Section 5 — Commission & earnings

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `commission_pct` | How much does each company take from the total ride fare? in other words, what is the commission fee percentage? | percentage | no | — | `earnings.commission_pct` |
| `weekly_earnings_amount` | On average, how much are you making each week with this "{platform}" app"? How much are you making in total each week? | currency | no | — | `earnings.weekly_earnings_amount` |
| `earnings_basis` | In the case of digital trips, specify if those earnings are before or after the commission is taken by the ride-hailing company. | single_choice (gross/net/unknown) | yes | `weekly_earnings_amount` was given | `earnings.earnings_basis_id` |

**Legacy field, no longer asked**: `surveys.total_weekly_earnings_amount`
("across all platforms combined" — asked once, on the last platform) is
not part of the current 26-question flow.

### Section 6 — Bonuses

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `receives_bonuses` | Are you getting any bonuses from the ride-hailing apps you drive on? | yes_no | yes | — | `bonuses.receives_bonuses` |
| `required_trips` | How many trips do you have to complete to get the bonus? | number | no | `receives_bonuses = true` | `bonuses.required_trips` |
| `bonus_period` | Specify frequency (If the named trips are daily, weekly, monthly, on a specific timeframe | single_choice (Daily/Weekly/Monthly/Specific timeframe/Other) | yes | `receives_bonuses = true` | `bonuses.bonus_period` |
| `bonus_description` | How much are they offering you (What is the bonus value)? or What are they offering+ | text | yes | `receives_bonuses = true` | `bonuses.description` |

If `receives_bonuses` is No, none of the three detail questions are
shown. The canonical wording for `bonus_period`/`bonus_description` above
is preserved exactly as given, including the unclosed parenthesis in
`bonus_period`'s text and the trailing "+" in `bonus_description`'s.

**Legacy field, no longer asked**: `bonuses.bonus_value_amount` (a
numeric currency question) — the current questionnaire asks the bonus
value as open text instead (`bonus_description`, reusing the `description`
column that the previous flow used for an optional qualitative note).

### Section 7 — Competitor & market awareness

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `competitor_rider_discounts_notes` | Do you know if the competitor offers any rider discounts at the moment? | text | no | — | `market_intelligence.competitor_rider_discounts_notes` |
| `estimated_driver_count_text` | Are you aware of how many drivers work with the "{platform}" platform? | text | no | — | `market_intelligence.estimated_driver_count_text` |

**Legacy field, no longer asked**: `market_intelligence.estimated_driver_count`
(a numeric question) — the current questionnaire asks this as open text
instead, since a driver's answer here ("a few hundred, not sure") rarely
comes as a clean number.

## Section 8 — Market & payment

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `market_leader_text` | Who is the market leader? | text | no | — | `surveys.market_leader_text` |
| `cash_pct` | Percentage of payments made with cash? | percentage | no | — | `payments.cash_pct` (broadcast to every selected platform) |
| `payout_notes` | How does the ride-hailing platform make commission payouts to drivers (and, if cash payments are available, collect money from drivers)? | text | no | — | `payments.payout_notes` (broadcast to every selected platform) |

Survey-scoped (asked once, not per platform) even though `cash_pct`/
`payout_notes` land on the platform-scoped `payments` table — the single
answer is written to every selected platform's row, the same broadcast
pattern `switch_frequency` (Section 3) already used.

**Legacy fields, no longer asked**: `payments.digital_pct`,
`payout_method_id`, `early_cashout_available`, `cashout_fee_amount`,
`cashout_fee_is_percentage`; `surveys.perceived_market_leader_platform_id`
(the old single-choice "which platform is the leader" — replaced by the
open-text `market_leader_text`).

## Section 9 — Driver type

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `employment_text` | Are you working with a fleet company or are you an independent driver? | text | yes | — | `driver_employment.employment_text` |

**Legacy fields, no longer asked**: `driver_employment.driver_type_id`
(a single-choice independent/fleet/other lookup) and `.fleet_name` — the
current questionnaire asks this as open text instead. See
`application/statistics_service.classify_driver_type` for how the
dashboard still derives a fleet-vs-independent breakdown from the open
text (English/Russian/Uzbek keywords, conservative — see "Classification
heuristics" below).

## Section 10 — Complementary questions

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `loyalty_program_notes` | Are you part of a royalty/point program? Could you describe it? | text | no | — | `survey_platforms.loyalty_program_notes` (broadcast to every selected platform) |
| `improvement_suggestions` | How would you improve your experience as a ride-hailing driver? | text | no | — | `driver_experience.improvement_suggestions` |
| `reason_to_join_new_platform` | Which one would be the main reason for you to join a new ride-hailing app? | text | no | — | `driver_experience.reason_to_join_new_platform` |
| `seasonal_pattern` | Are your driving habits different during the winter and summer season? | single_choice ("I drive the same on average throughout the year" / "I drive more in summer" / "I drive more in the winter season") | no | — | `driver_experience.seasonal_pattern` |

`seasonal_pattern` is a **structured** answer (a native enum:
`same_year_round`/`more_in_summer`/`more_in_winter`) — it is the
authoritative value for every survey answered under the current
questionnaire and is never overridden or recomputed by any heuristic; see
"Classification heuristics" below for how it relates to the legacy
free-text field.

**Legacy field, no longer asked as its own question**:
`driver_experience.seasonal_behavior_notes` (free text) — older surveys
only have this; `classify_seasonality()` derives an illustrative
same/summer/winter reading from it for those surveys only.
`survey_platforms.has_loyalty_program` (yes/no) is likewise no longer
written by new surveys — only `loyalty_program_notes` is.

## Section 11 — Driver statistics / screenshots

*Unchanged from the previous questionnaire — preserved as-is, just with
localized prompts.*

| Code | Text (en) | Type | Required | Condition | DB field |
|---|---|---|---|---|---|
| `has_screenshots` | Do you have screenshots of your driver statistics that you are willing to share? | yes_no | yes | — | `surveys.screenshots_offered` |

- **No** → straight to Section 12 (review).
- **Yes** → the bot repeatedly accepts a photo or an image-file document,
  then (only when the survey has more than one platform) asks which
  platform it's for, then asks what it shows — four localized quick-pick
  buttons (Current week / Previous week / Previous 4 weeks / month /
  Other) or free text — and shows localized "Upload another screenshot" /
  "Finish uploading". Saying Yes and finishing without uploading anything
  is valid.
- Each uploaded file becomes one `attachments` row (`survey_id`,
  `platform_id` if known, `telegram_file_id`/`telegram_file_unique_id`,
  `file_type`, `uploaded_at`, `sequence_number`, `description`), and the
  bot downloads it to `settings.screenshot_storage_dir`
  (`Attachment.local_path`) best-effort, non-blocking — see
  `docs/DASHBOARD.md`'s Screenshots section and `docs/DEPLOYMENT.md`.

## Section 12 — Review and confirmation

Unlike the previous questionnaire (which auto-submitted the instant the
last question was answered), the current flow shows a structured review
screen and waits for an explicit tap before anything is finalized.

The moment the cursor walks off the end of the questionnaire
(`SurveySession.is_review()` becomes true — landing on Section 12, which
has no questions of its own), `presentation/handlers/survey._send_current`
renders (`presentation/formatters.format_review`, localized):

```
SURVEY #TAS-000123

City:
Tashkent

Platforms:
• Yandex Go
• Uklon

Working:
• 6 days/week
• 7 hrs - 8 hrs

Yandex Go:
• Commission: 20%
• Weekly earnings: 1500000
• Bonuses: yes

Uklon:
• Commission: 25%
• Weekly earnings: 900000
• Bonuses: no

Market:
• Market leader: Yandex Go
• Cash payments: 30%

Driver:
• Independent, my own car
```

with two buttons: **Confirm / Submit** (`review_confirm`) and **Edit /
Back** (`review_edit`). The survey stays `DRAFT` until Confirm/Submit is
tapped.

- **Confirm / Submit** → `SurveySession.confirm()` → `finish()`:
  `surveys.status` `DRAFT` → `COMPLETED`, `completed_at` set, and the
  driver sees the localized completion message (survey ID, thank-you) with
  a **"Start New Survey"** button. Idempotent — a duplicate tap (double
  tap, Telegram retry) re-shows the same completed survey's ID rather than
  erroring or resubmitting.
- **Edit / Back** → ordinary `SurveySession.go_back()` — lands back on
  `has_screenshots` (the last real question), from which the interviewer
  can walk further back through the whole questionnaire to correct any
  answer, then forward again (every `answer()` call still auto-advances)
  back to a freshly rebuilt review screen.

**Survey ID format**: `<CITY_PREFIX>-<6-digit sequence>`, e.g.
`TAS-000123` — generated when `city` is answered (Section 1), not at
confirmation.

**Start New Survey** (`new_survey` callback) shows the language picker
again, then starts a fresh session from Section 1 — the just-completed
survey is left completely untouched (`SurveySession.restart()` only ever
abandons a `DRAFT` survey).

## Classification heuristics (dashboard-only, never authoritative over a structured answer)

Two free-text fields have no fully-reliable structured equivalent across
*all* surveys (old and new), so the dashboard derives an illustrative
category from the text — conservative by design: ambiguous or
no-keyword-match text is always `"unclassified"`, never guessed.
`application/statistics_service.py`:

- `classify_seasonality(text)` — legacy `seasonal_behavior_notes` only.
  **Never applied to, and never overrides,** the current questionnaire's
  structured `seasonal_pattern` answer (Section 10) — the two are
  reported as separate `StatisticsReport.driver_estimates` fields
  (`seasonality_text_classification` vs. `seasonal_pattern`) and never
  merged into one number.
- `classify_driver_type(text)` — the current questionnaire's
  `employment_text` (Section 9), since that question has no structured
  equivalent at all (old or new).

Both recognize English, Russian, and Uzbek keywords for the same concept
in one flat tuple (e.g. `_FLEET_KEYWORDS` holds "fleet"/"автопарк"/
"avtopark" together) — see the functions' docstrings for the exact lists.
Uzbek Latin apostrophe variants (`'`/`ʻ`/`ʼ`/backtick — different keyboards
produce different Unicode characters for `o'`/`g'`) are normalized before
matching. A response mentioning both categories (e.g. "used to be
independent, now with a fleet") is `"unclassified"`, not guessed at.

## Brief traceability

| Canonical question (§7 of the questionnaire brief) | Question code(s) |
|---|---|
| Which ride-hailing apps do you currently drive for? Mention as many as you work for | `platforms_used` |
| How often do you switch between apps? | `switch_frequency` |
| If you are working with more than one app, which of these apps gives you the best experience as a driver? | `best_experience_platform` |
| If you are working with just one app, are you working with that app exclusively? | `is_exclusive` |
| How many days per week do you usually work? | `days_per_week` |
| How many hours on average do you drive a day? | `hours_per_day` |
| Which category do you drive the most? | `main_category_text` |
| Which ones are the requirements to drive in each category? | `category_requirements_text` |
| How many trips on average do you usually complete daily? Or weekly? | `trips_summary_text` |
| How much does each company take from the total ride fare? ... commission fee percentage? | `commission_pct` |
| On average, how much are you making each week with this "x" app"? ... | `weekly_earnings_amount` |
| ... before or after the commission is taken by the ride-hailing company | `earnings_basis` |
| Are you getting any bonuses from the ride-hailing apps you drive on? | `receives_bonuses` |
| How many trips do you have to complete to get the bonus? / Specify frequency | `required_trips`, `bonus_period` |
| How much are they offering you (What is the bonus value)? ... | `bonus_description` |
| Do you know if the competitor offers any rider discounts at the moment? | `competitor_rider_discounts_notes` |
| Are you aware of how many drivers work with the "X" platform? | `estimated_driver_count_text` |
| Who is the market leader? | `market_leader_text` |
| Percentage of payments made with cash? | `cash_pct` |
| How does the ride-hailing platform make commission payouts to drivers ... | `payout_notes` |
| Are you working with a fleet company or are you an independent driver? | `employment_text` |
| Are you part of a royalty/point program? Could you describe it? | `loyalty_program_notes` |
| How would you improve your experience as a ride-hailing driver? | `improvement_suggestions` |
| Which one would be the main reason for you to join a new ride-hailing app? | `reason_to_join_new_platform` |
| Are your driving habits different during the winter and summer season? | `seasonal_pattern` |
