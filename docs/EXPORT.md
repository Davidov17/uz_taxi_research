# Excel / CSV Export

`src/telegram_bot/application/export_service.py` — read-only, builds an
Excel workbook (via `openpyxl`) or a single-sheet CSV from the same survey
graph `statistics_service.py` reads, plus a `Statistics` sheet built directly
from `StatisticsService.generate_report()`.

## Usage

```python
from telegram_bot.application.export_service import ExportService, ExportFilters, workbook_to_bytes

service = ExportService(db)  # any AsyncSession
workbook = await service.build_workbook(
    ExportFilters(city_id=..., platform_id=..., date_from=..., date_to=..., include_incomplete=False)
)
xlsx_bytes = workbook_to_bytes(workbook)  # ready to send as a Telegram document / HTTP response

csv_text = await service.build_csv("earnings", ExportFilters(...))
```

`include_incomplete` defaults to `False` — only `COMPLETED` surveys are
exported unless explicitly requested, per spec. `build_csv` accepts the
`key` of any sheet (`survey_summary`, `driver_platforms`,
`working_statistics`, `earnings`, `bonuses`, `payments`, `categories`,
`market_intelligence`, `driver_experience`, `screenshots`, `raw_answers`,
`statistics`) and raises `ValueError` for an unknown key.

## The 12 sheets

| # | Sheet | Grain |
|---|---|---|
| 1 | Survey Summary | one row per survey |
| 2 | Driver Platforms | one row per survey × platform |
| 3 | Working Statistics | one row per survey |
| 4 | Earnings | one row per survey × platform (with an Earnings row) |
| 5 | Bonuses | one row per survey × platform (with a Bonus row) |
| 6 | Payments | one row per survey × platform (with a Payment row) |
| 7 | Categories | one row per survey × platform × ride category |
| 8 | Market Intelligence | one row per survey × platform |
| 9 | Driver Experience | one row per survey |
| 10 | Screenshots | one row per uploaded file |
| 11 | Raw Answers | one row per (survey, platform-or-none, field) with non-blank free text |
| 12 | Statistics | `StatisticsService` output, flattened to rows |

Every per-platform/per-observation sheet (2, 4–8, 10) repeats
`survey_id`, `city`, `survey_date`, and `platform` as leading columns, so
each sheet is independently filterable/pivotable in Excel without a
VLOOKUP back to Survey Summary. **No merged cells anywhere** — verified by
a test that walks every sheet's `merged_cells.ranges` and asserts it's
empty.

## Value handling

- **Missing stays blank.** A skipped question is written as `None` (an
  empty cell in Excel, an empty string in CSV) — never `"N/A"`, never `0`.
- **Percentages and currency stay numeric.** `commission_pct`, `cash_pct`,
  `digital_pct`, `trip_share_pct`, weekly-earnings and bonus-value amounts
  are written as plain `int`/`float` (Decimal columns are cast to float),
  with a plain numeric `number_format` (`0.0` / `#,##0`) applied — not
  Excel's built-in `%` format, which would silently multiply the value by
  100 on display since our percentages are already stored as e.g. `20.5`,
  not `0.205`. No currency symbols or `%` signs are baked into the value
  itself; the column header carries the unit, so every number stays usable
  directly in `SUM`/`AVERAGE`/a chart.
- **Timestamps lose their timezone, not their type.** Postgres returns
  timezone-aware `datetime`s and openpyxl rejects those outright — they're
  converted to naive UTC datetimes (`_dt()`) so the cell keeps Excel's
  native date/time type instead of falling back to a string.

## Statistics sheet

Two stacked tables (separated by column values, not a merged banner row):
numeric metrics (`section, metric, count, average, median, minimum,
maximum`) and category breakdowns (`section, metric, category, count,
percentage`). The `driver_estimates` rows are labeled
`"driver_estimates (NOT verified market data)"` in the `section` column —
the same sample-vs-estimate separation documented in `STATISTICS.md`
carries through to the export, not just the in-app report.

## Tests

`tests/test_export_service.py` — a 3-survey dataset (one fully populated,
one with several fields deliberately left blank, one `DRAFT`) covering:
sheet structure (all 12 titles, no merged cells, valid `.xlsx` bytes),
completed-vs-incomplete filtering, blank-not-zero handling, numeric-typed
percentages/currency, screenshot metadata, raw-answer consolidation, the
Statistics sheet, CSV parity with the workbook, an unknown-sheet-key error,
and the city filter.
