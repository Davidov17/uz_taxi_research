# Statistics & Analytics Layer

`src/telegram_bot/application/statistics_service.py` — read-only, computes
aggregate statistics from completed surveys. No new tables; it queries the
same schema the survey flow writes to.

## Usage

```python
from telegram_bot.application.statistics_service import StatisticsService, StatisticsFilters

service = StatisticsService(db)  # any AsyncSession
report = await service.generate_report(
    StatisticsFilters(city_id=..., platform_id=..., date_from=..., date_to=..., driver_type_id=...)
)
```

`generate_report()` returns one `StatisticsReport` with a section per
metric group (`general`, `working_pattern`, `earnings`, `commission`,
`payments`, `bonuses`, `multi_app`, `driver_type`, `categories`,
`sample_market`, `driver_estimates`). Each section's individual query is
also callable on its own (`service._working_pattern_stats(filters)`, etc.)
if only one group is needed.

All filters are optional and combine with AND. `platform_id` narrows both
which surveys are in scope (only those reporting that platform) *and*, for
platform-scoped tables (earnings, commission, bonuses, payments,
categories), which rows contribute to the numbers — so filtering by
platform gives you "stats for drivers on this platform, using only their
data for this platform," not their combined multi-app figures.

## Two rules baked into every query

**1. Missing values are never treated as zero.** A skipped question is
excluded from that metric's calculation, not averaged in as 0. This falls
out of using plain SQL `AVG`/`PERCENTILE_CONT`/`MIN`/`MAX`/`COUNT(column)`
— all of which ignore NULLs — rather than `COALESCE(column, 0)` anywhere.
Every `MetricSummary.count` tells you exactly how many actual responses a
number is based on, which is frequently smaller than the survey count in
scope.

**2. Sample statistics and driver estimates are structurally separated.**

- `StatisticsReport.sample_market` — facts about the respondents
  themselves (their own internet reliability). Safe to report as-is.
- `StatisticsReport.driver_estimates` — subjective driver opinions about
  the wider market: `perceived_market_leader` (who they *think* is
  biggest), `estimated_driver_count` (their *guess* at platform size), and
  `seasonality_text_classification` (a best-effort keyword read of the
  free-text seasonality answer — there is no structured "same / more in
  summer / more in winter" question, so this is an approximate
  classification of open text, not a tabulated answer).

  **Report these as "surveyed drivers estimate/believe ...", never as
  verified market figures.** Every field/docstring in this section says so
  explicitly, and the two are never merged into one bucket, precisely so a
  consumer of this report can't accidentally present a guess as a fact.

Everything else in the report — earnings, commission, hours, trips,
bonuses, categories, driver type — is self-reported by the driver too, but
describes their *own* situation (a fact about the sample), not a guess
about something external, so it's presented as ordinary sample statistics.

## Result shapes

- `MetricSummary(count, average, median, minimum, maximum)` — every
  numeric metric, uniformly. `MetricSummary.empty()` when there's no data
  in scope (never raises).
- `CategoryBreakdown(counts: dict[str, int], total: int)` — every
  distribution/percentage-style metric (surveys by city, driver type
  split, bonus yes/no, category distribution, platform combinations,
  ...). `.percentages` is a derived property (`share of total`, rounded to
  1 decimal); `total` is the number of responses that had a value, which
  can be less than the survey count in scope.

## What's covered

| Section | Metrics |
|---|---|
| General | total surveys, completed surveys, by city, by platform, by date |
| Working pattern | days/week, hours/day, trips/day, trips/week (avg/median/min/max/count each) |
| Earnings | weekly earnings, by platform, by city, per trip, per working hour |
| Commission | overall, by platform |
| Payments | avg cash %, avg digital % |
| Bonuses | % receiving, avg value, avg trip threshold |
| Multi-app | % one vs. multiple platforms, platform combinations |
| Driver type | independent vs. fleet vs. other |
| Categories | category distribution, main-category distribution |
| Sample market | internet reliability |
| Driver estimates | perceived market leader, estimated driver counts, seasonality (text-classified) |

## Tests

`tests/test_statistics_service.py` — a 7-survey synthetic dataset (5
completed with deliberately varied missing fields, 1 `DRAFT`, 1
`ABANDONED`) with every expected average/median/count hand-computed in the
module docstring, plus filter tests (city, platform, date range, driver
type) and a no-data-in-scope case that must return empty summaries, never
raise.
