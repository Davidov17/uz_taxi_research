# Implementation Plan — Driver Market Research Telegram Bot

Status: **Proposal — not yet implemented.** This document is for review before any code is written.

## 0. Context and inputs

- No existing project was found: the repository directory was empty and not yet a git repo.
- No separate "research brief" file was located on disk. This plan is built directly from the requirements listed in the request. Where the brief would normally pin down a detail (exact wording of questions, answer scales, category taxonomy, etc.), this doc calls it out as an **open question** rather than guessing silently.
- Nothing is being overwritten — this is a greenfield build.

---

## 1. Goals and constraints (as understood)

- Telegram bot used **by interviewers**, in real time, during a ride, to interview drivers.
- Covers 4 cities: Tashkent, Samarkand, Namangan, Andijan.
- Covers platforms: Yandex Go, Uklon, and an open-ended "Other" (free text).
- One interviewer can run many surveys (one per driver/ride).
- Some questions are asked **once per survey** (driver/market-level), others are **repeated per platform** the driver uses (platform-level, e.g. commission %, earnings, bonuses on Yandex Go *and* on Uklon).
- Conditional / branching logic (skip logic) is required.
- Answers must land in **structured fields**, not just free text blobs — so we can compute stats by city/platform/etc. later.
- Raw answers must remain available for audit (what was literally asked/answered, including any later-superseded question versions).
- Screenshots (driver stats screens) must attach to the correct survey.
- Every survey gets a unique ID.
- Excel/CSV export needed later (not now).
- Clean architecture: Telegram-handling code must not contain business logic.

---

## 2. Proposed architecture

### 2.1 Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Best fit for a small ops team, huge Telegram + data-export ecosystem |
| Telegram framework | **aiogram 3.x** | Native async, router-based handlers, built-in FSM (finite state machine) with pluggable storage — a good match for a long multi-step, branching conversation. (Alternative considered: python-telegram-bot — also viable, slightly less ergonomic FSM story. Flag if you have a preference.) |
| ORM / DB access | SQLAlchemy 2.0 (async) + Alembic migrations | Typed models, mature migration tooling |
| Database | **PostgreSQL** | Multi-city/multi-interviewer concurrent writes, good JSON support for flexible answer payloads, easy path to hosted DB (RDS/Supabase/Timeweb/etc.). SQLite acceptable for local dev only. |
| Validation layer | Pydantic v2 | Validate answer values against question type before persisting |
| File storage | Local disk (`/data/attachments`) behind a storage interface, Telegram `file_id` also kept | Start simple; interface allows swapping to S3-compatible storage later without touching business logic |
| Export | pandas + openpyxl, triggered by admin command or CLI script | Deferred build, but schema is designed for it now |
| Config | `pydantic-settings` + `.env` | Bot token, DB URL, admin IDs, storage path |
| Deployment | Docker + docker-compose (bot + postgres) | Reproducible, easy to hand off to a VPS |
| Testing | pytest, pytest-asyncio, aiogram's test utilities / fake bot | Unit-test the survey engine independent of Telegram |

### 2.2 Layers (clean architecture)

```
telegram_bot/
├── presentation/              # Telegram-only concerns
│   ├── handlers/              # routers: start, survey_flow, admin, export
│   ├── keyboards/              # inline/reply keyboard builders
│   └── formatters/             # turns domain objects into Telegram messages
│
├── application/                 # use cases — orchestration, no Telegram/DB details
│   ├── survey_session.py        # SurveySessionService: start/answer/skip/finish survey
│   ├── question_engine.py       # walks the questionnaire graph, applies conditions,
│   │                             # expands repeatable platform blocks
│   ├── export_service.py        # (phase 2) builds Excel/CSV from repository data
│   └── dto.py                   # request/response objects between presentation <-> app
│
├── domain/                      # pure business objects, no framework deps
│   ├── entities.py               # Survey, Answer, Interviewer, Platform, City...
│   ├── questionnaire.py          # Question, QuestionGroup, Condition — declarative model
│   └── value_objects.py          # AnswerType enum, Money, PlatformCode, etc.
│
├── infrastructure/
│   ├── db/
│   │   ├── models.py             # SQLAlchemy ORM models
│   │   ├── repositories/          # SurveyRepository, InterviewerRepository, ...
│   │   └── migrations/            # Alembic
│   ├── storage/                   # FileStorage interface + local-disk implementation
│   └── config.py
│
├── questionnaire_def/            # THE QUESTIONNAIRE AS DATA, not code
│   └── v1.yaml                    # ordered sections, question codes, types, conditions,
│                                   # repeat-for-platform markers — edit this to change the survey
│                                   # without touching handler code
│
└── main.py                       # composition root: wires bot, dispatcher, DB session, DI
```

**Key design decision:** the questionnaire itself (question text, order, type, validation, conditional/skip logic, and which questions repeat per platform) is defined as **data** (YAML) loaded into `domain.questionnaire` objects, not hardcoded as a chain of Telegram handlers. `application.question_engine` is a small interpreter that walks this definition. This is what makes "questions can be conditional" and "platform-specific questions can repeat" a configuration change instead of a code change, and it's what lets a non-engineer (or you, later) tweak wording/order without redeploying handler logic.

A single generic `presentation/handlers/survey_flow.py` router drives the conversation: on every user reply it asks `question_engine` "what's the next question given these answers so far," renders it, waits, validates, stores, repeats — rather than one handler per question.

### 2.3 Conversation state handling

- aiogram FSM stores **only**: `survey_id`, `current_question_code`, `current_platform_context` (which platform iteration we're on, if any). Everything else is read from the DB via the survey_id, so a crash/restart mid-survey can resume instead of losing data.
- Answers are persisted **as each question is answered** (not batched at the end), so partial/abandoned surveys still yield usable data.

---

## 3. Database schema (proposal)

Design principle: a **small set of structured "wide" tables** for the fields we know we'll aggregate on (city, platform, earnings, commission, etc.), plus **one EAV-style raw-answer log** for full audit/traceability and for any question we haven't promoted to a structured column yet.

```sql
-- ===== Reference / lookup data =====

interviewers (
  id                  BIGINT PK,
  telegram_user_id    BIGINT UNIQUE NOT NULL,
  full_name           TEXT NOT NULL,
  phone               TEXT,
  is_active           BOOLEAN DEFAULT TRUE,
  created_at          TIMESTAMPTZ DEFAULT now()
)

cities (
  id     SMALLINT PK,
  code   TEXT UNIQUE,     -- 'tashkent' | 'samarkand' | 'namangan' | 'andijan'
  name   TEXT
)

platforms (
  id         SMALLINT PK,
  code       TEXT UNIQUE,   -- 'yandex_go' | 'uklon' | 'other'
  name       TEXT,
  is_other   BOOLEAN DEFAULT FALSE   -- true only for the generic "Other" row
)

ride_categories (
  id     SMALLINT PK,
  code   TEXT UNIQUE,        -- 'economy' | 'comfort' | 'business' | 'other' ...
  name   TEXT
)
-- OPEN QUESTION: exact category taxonomy per platform — see §5.

questions (                  -- catalog / versioned definition, mirrors questionnaire_def/*.yaml
  id              BIGINT PK,
  code            TEXT NOT NULL,      -- stable machine key, e.g. 'weekly_earnings'
  version         INT NOT NULL,       -- bump when wording/type changes
  section         TEXT,               -- 'platform_usage' | 'earnings' | 'market_perception' ...
  text            TEXT NOT NULL,
  answer_type     TEXT NOT NULL,      -- 'single_choice'|'multi_choice'|'number'|'text'|'photo'|'boolean'|'scale'
  is_repeatable   BOOLEAN DEFAULT FALSE,  -- true = asked once per platform in survey_platforms
  UNIQUE (code, version)
)

-- ===== Core survey =====

surveys (
  id                        UUID PK DEFAULT gen_random_uuid(),   -- the "unique survey ID"
  human_code                TEXT UNIQUE,        -- short display code e.g. 'TAS-000123', generated
  interviewer_id            BIGINT REFERENCES interviewers,
  city_id                   SMALLINT REFERENCES cities,
  status                    TEXT NOT NULL DEFAULT 'in_progress',  -- in_progress|completed|abandoned
  started_at                TIMESTAMPTZ DEFAULT now(),
  completed_at               TIMESTAMPTZ,
  questionnaire_version      INT NOT NULL,        -- which questionnaire_def version was used

  -- driver-level structured fields (asked once per survey)
  driver_experience_months   INT,
  uses_multiple_apps         BOOLEAN,
  is_exclusive_to_platform_id SMALLINT REFERENCES platforms,   -- null if not exclusive
  working_days_per_week      SMALLINT,
  working_hours_per_day      NUMERIC(4,1),
  trips_per_day               SMALLINT,
  trips_per_week               SMALLINT,
  main_ride_category_id       SMALLINT REFERENCES ride_categories,
  category_requirements_notes TEXT,
  total_weekly_earnings_amount NUMERIC(12,2),
  total_weekly_earnings_currency TEXT DEFAULT 'UZS',
  earnings_basis              TEXT,       -- 'gross' | 'net'
  perceived_market_leader_id   SMALLINT REFERENCES platforms,
  cash_ratio_pct                SMALLINT,   -- 0-100, card_ratio = 100 - cash_ratio (or store both, see §5)
  card_ratio_pct                SMALLINT,
  driver_type                   TEXT,        -- 'independent' | 'fleet' | 'both'
  fleet_name                     TEXT,
  internet_reliability           TEXT,        -- enum/scale, see §5
  seasonal_differences_notes     TEXT,
  reasons_to_join_new_platform   TEXT[],      -- multi-select + free text 'other'
  reasons_to_join_other_text     TEXT,
  general_notes                  TEXT,

  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now()
)

-- ===== Platform-specific, repeatable block: one row per (survey, platform) =====

survey_platforms (
  id                        BIGINT PK,
  survey_id                 UUID REFERENCES surveys ON DELETE CASCADE,
  platform_id                SMALLINT REFERENCES platforms,
  platform_other_name         TEXT,     -- filled when platform.is_other = true
  is_primary_platform          BOOLEAN DEFAULT FALSE,

  weekly_earnings_amount       NUMERIC(12,2),
  weekly_earnings_currency     TEXT DEFAULT 'UZS',
  commission_pct                 NUMERIC(5,2),
  has_bonuses                    BOOLEAN,
  bonus_description               TEXT,
  bonus_threshold_trips            INT,
  bonus_threshold_amount            NUMERIC(12,2),
  has_rider_promotions              BOOLEAN,
  rider_promotions_notes             TEXT,
  estimated_drivers_on_platform       INT,     -- driver's own estimate, self-reported
  has_loyalty_program                  BOOLEAN,
  loyalty_program_notes                 TEXT,
  payout_methods                        TEXT[],  -- e.g. {'card','cash_office','wallet'}

  UNIQUE (survey_id, platform_id, platform_other_name)
)

-- ===== Raw answer audit log (every question, every answer, verbatim) =====

survey_answers (
  id                BIGINT PK,
  survey_id          UUID REFERENCES surveys ON DELETE CASCADE,
  question_id         BIGINT REFERENCES questions,
  platform_id          SMALLINT REFERENCES platforms NULL,  -- set when this answer belongs
                                                               -- to a repeated per-platform question
  answer_raw_text       TEXT,          -- exactly what came back (label, typed text, or JSON for multi-choice)
  answer_value_json      JSONB,        -- normalized value actually written into structured columns above
  answered_at             TIMESTAMPTZ DEFAULT now()
)
-- This table is the audit trail: even after we normalize an answer into
-- surveys.* / survey_platforms.*, the original stays here untouched.

-- ===== Attachments (screenshots) =====

attachments (
  id                 BIGINT PK,
  survey_id           UUID REFERENCES surveys ON DELETE CASCADE,
  platform_id          SMALLINT REFERENCES platforms NULL,   -- which platform's stats screen, if applicable
  question_id           BIGINT REFERENCES questions NULL,
  telegram_file_id        TEXT NOT NULL,
  telegram_file_unique_id  TEXT NOT NULL,
  local_path                TEXT,            -- populated after download to disk/object storage
  mime_type                  TEXT,
  uploaded_at                 TIMESTAMPTZ DEFAULT now()
)
```

**Why this hybrid (structured + EAV audit) shape:**
- Aggregation queries ("median commission % for Yandex Go in Tashkent") run against normal indexed columns in `survey_platforms` — fast, simple SQL/pandas, no JSON-parsing needed for the export step.
- `survey_answers` guarantees nothing is ever lost or silently reinterpreted: if a question's wording changes later (`version` bump on `questions`), old raw answers stay attached to the version that was actually asked.
- Repeated per-platform questions naturally become **one row per platform per survey** in `survey_platforms`, which is exactly the "repeat for multiple platforms" requirement, and is trivial to `GROUP BY platform_id`.

---

## 4. Questionnaire definition format (sketch)

```yaml
# questionnaire_def/v1.yaml
version: 1
sections:
  - id: platform_usage
    questions:
      - code: uses_multiple_apps
        type: boolean
        text: "Does the driver use more than one ride-hailing app?"
      - code: is_exclusive
        type: single_choice
        text: "Is the driver exclusive to one platform?"
        condition: "uses_multiple_apps == false"
        options: [platform_ref]   # dynamically populated from platforms table

  - id: platforms_used
    repeat_for: platforms_selected     # expands once per platform the driver reported using
    questions:
      - code: weekly_earnings
        type: number
        text: "Weekly earnings on {platform_name}?"
      - code: commission_pct
        type: number
        text: "Commission % on {platform_name}?"
      - code: has_bonuses
        type: boolean
        text: "Are there bonuses on {platform_name}?"
      - code: bonus_threshold_trips
        type: number
        condition: "has_bonuses == true"
      - code: stats_screenshot
        type: photo
        text: "Please attach a screenshot of the driver's stats on {platform_name}."
        required: false
  ...
```

`application/question_engine.py` interprets `condition` as a small safe expression evaluated against already-collected answers (a restricted evaluator, not raw `eval`), and `repeat_for` against the list of platforms captured earlier in the survey. This is the mechanism satisfying "conditional questions" and "platform-specific repeats" without hardcoding either in Telegram handlers.

---

## 5. Assumptions and open questions (need your input before/while building)

These are genuine unknowns from the requirements list — flagging rather than guessing on things that affect schema/UX:

1. **Exact wording and answer options for each question.** The requirement list names *topics* ("bonuses", "reasons to join a new platform") but not the literal question text, choice lists, or scales. I'll need either the original brief or for you to fill in a first draft questionnaire (I can draft one for review).
2. **Ride category taxonomy** — is this platform-specific (Yandex Go's own tiers vs Uklon's own tiers) or a shared taxonomy across platforms? Affects whether `ride_categories` is global or scoped per platform.
3. **Cash/card ratio** — captured once per survey overall, or per platform? Drivers using multiple apps may have different payment mixes per platform. Current schema puts it on `surveys` (overall); flag if you want it duplicated into `survey_platforms`.
4. **"Estimated number of drivers on platform"** — is this the interviewed driver's personal guess (self-reported, likely noisy), or something interviewers are meant to look up separately? Schema assumes self-report.
5. **Internet reliability** — scale (1–5), categorical (good/ok/poor), or free text? Needs a fixed enum for clean stats.
6. **Payout methods** — fixed list (bank card, cash office, e-wallet, etc.) or open text? Assumed fixed multi-select list per platform.
7. **Currency** — assuming UZS (so'm) throughout; confirm.
8. **Language(s) for the bot UI** — Uzbek, Russian, and/or English? This affects both the Telegram UI strings and whether question text needs to be stored per-locale in `questions`.
9. **Interviewer authentication** — is there a fixed, pre-approved list of interviewers (recommended — register via an admin command / allow-list of Telegram IDs), or open self-registration?
10. **Deployment target** — do you already have a VPS/cloud account in mind, or should I assume Docker + generic Linux VPS for the plan?
11. **Data sensitivity** — any requirement to anonymize driver identity (we're not currently asked to collect driver name/phone — confirming that's intentional and only interviewer identity + driver-reported data is stored)?
12. **Survey abandonment** — if an interviewer starts a survey and never finishes (ride ends, driver declines), should the bot allow explicit "abandon/cancel" with reason, or just time out?

None of these block writing the architecture/schema (defaults are stated above), but #1 (actual question text) is the one that would most change scope before real implementation starts.

---

## 6. Build phases (once this plan is approved)

1. **Scaffolding** — repo structure, `pyproject.toml`, Docker/docker-compose, config loading, git init + initial commit.
2. **Domain + DB layer** — SQLAlchemy models per §3, Alembic migration, repositories, seed data (cities, platforms, initial `questions` catalog).
3. **Questionnaire engine** — YAML loader, condition evaluator, repeat-for-platform expansion, unit tests independent of Telegram.
4. **Telegram presentation layer** — aiogram bot skeleton, generic survey-flow router driven by the question engine, keyboards for choice/photo/number question types, `/start`, `/newsurvey`, `/cancel`, `/status`.
5. **Interviewer management** — allow-list/registration flow, admin command to add interviewers.
6. **Attachments** — photo handler, download to storage, link to `attachments` table.
7. **Manual QA pass** — run a handful of full survey flows end-to-end against a test DB, verify structured + raw data both land correctly.
8. **Export (phase 2, deferred per your instruction)** — CLI/admin command producing Excel/CSV from `survey_platforms` + `surveys`, joined with lookups.

---

## 7. What I need from you to proceed

- Confirm or correct the tech stack (Python/aiogram/PostgreSQL) — say now if you want a different stack.
- Answer or defer on the open questions in §5 (can be answered incrementally, not all up front).
- Approve the schema shape (structured tables + raw audit log) or request changes.
- Say go-ahead to start **Phase 1 (scaffolding)** — per your instruction, no code will be written until you review this plan.
