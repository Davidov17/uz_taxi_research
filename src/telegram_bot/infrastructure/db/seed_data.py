"""Reference/lookup data for a fresh database.

Defined as plain Python data (not hardcoded only inside a migration) so the
same source of truth can be used by the Alembic data migration, by test
fixtures, and by any future admin/reseed script. Each entry is a dict of
column values — apply with an idempotent upsert (see `upsert_all` below).
"""

CITIES = [
    {"code": "tashkent", "name": "Tashkent"},
    {"code": "samarkand", "name": "Samarkand"},
    {"code": "namangan", "name": "Namangan"},
    {"code": "andijan", "name": "Andijan"},
]

PLATFORMS = [
    {"code": "yandex_go", "name": "Yandex Go", "is_other": False},
    {"code": "uklon", "name": "Uklon", "is_other": False},
    {"code": "other", "name": "Other", "is_other": True},
]

RIDE_CATEGORIES = [
    {"code": "economy", "name": "Economy"},
    {"code": "comfort", "name": "Comfort"},
    {"code": "comfort_plus", "name": "Comfort+"},
    {"code": "business", "name": "Business"},
    {"code": "minivan", "name": "Minivan"},
    {"code": "delivery", "name": "Delivery"},
    {"code": "other", "name": "Other"},
]

SWITCH_FREQUENCIES = [
    # Legacy options from the previous questionnaire — kept (is_active=false,
    # set by migration da9dcac30c59) so surveys answered before the
    # questionnaire replacement keep valid foreign keys; never offered to
    # new surveys.
    {"code": "never", "name": "Never switches", "sort_order": 0},
    {"code": "rarely", "name": "Rarely (a few times a month)", "sort_order": 1},
    {"code": "weekly", "name": "Weekly", "sort_order": 2},
    {"code": "daily", "name": "Daily / every shift", "sort_order": 3},
    # Current questionnaire's exact wording ("How often do you switch between apps?").
    {"code": "never_same_app", "name": "Never, I always use the same app", "sort_order": 10},
    {"code": "sometimes_pick_hours", "name": "Sometimes (e.g. pick hours)", "sort_order": 11},
    {"code": "once_a_day", "name": "Once a day", "sort_order": 12},
    {"code": "few_times_a_day", "name": "A few times a day", "sort_order": 13},
]

EARNINGS_BASIS = [
    {"code": "gross", "name": "Gross (before commission/fees)"},
    {"code": "net", "name": "Net (after commission/fees)"},
    {"code": "unknown", "name": "Unknown / driver unsure"},
]

DRIVER_TYPES = [
    {"code": "independent", "name": "Independent driver"},
    {"code": "fleet", "name": "Fleet driver"},
    {"code": "other", "name": "Other"},
]

PAYOUT_METHODS = [
    {"code": "bank_card", "name": "Bank card"},
    {"code": "cash_office", "name": "Cash at office/partner point"},
    {"code": "e_wallet", "name": "E-wallet"},
    {"code": "bank_transfer", "name": "Bank transfer"},
    {"code": "other", "name": "Other"},
]

REFERENCE_DATA = {
    "cities": CITIES,
    "platforms": PLATFORMS,
    "ride_categories": RIDE_CATEGORIES,
    "switch_frequencies": SWITCH_FREQUENCIES,
    "earnings_basis": EARNINGS_BASIS,
    "driver_types": DRIVER_TYPES,
    "payout_methods": PAYOUT_METHODS,
}
