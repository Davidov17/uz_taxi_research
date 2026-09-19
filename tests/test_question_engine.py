import pytest

from telegram_bot.application.question_engine import (
    Position,
    ValidationError,
    current_question,
    next_position,
    parse_currency,
    parse_number,
    parse_percentage,
    prev_position,
    start_position,
)
from telegram_bot.domain.questionnaire import (
    PLATFORM_LOOP_SECTIONS,
    QUESTION_COUNT,
    QUESTIONS_BY_DISPLAY_NUMBER,
    SCREENSHOTS_SECTION_INDEX,
)

EXPECTED_CODES_IN_ORDER = [QUESTIONS_BY_DISPLAY_NUMBER[n].code for n in range(1, QUESTION_COUNT + 1)]


def make_view(survey_answers: dict | None = None):
    """Build an answers_view_fn like SurveySession would. The flat 15-question
    flow has no Condition on any section, so the view's content is irrelevant
    to navigation — every respondent walks the same section sequence — but
    next_position/current_question still need a callable of this shape."""
    survey_answers = survey_answers or {}

    def view(position: Position) -> dict:
        return dict(survey_answers)

    return view


def walk_forward(position, view, n):
    for _ in range(n):
        position = next_position(position, view, platform_count=0)
        assert position is not None
    return position


def test_no_platform_loop_configured():
    assert PLATFORM_LOOP_SECTIONS == ()


def test_start_position_is_section_one_question_zero():
    pos = start_position()
    assert pos.section_index == 1
    assert pos.question_index == 0
    assert pos.platform_pos is None


def test_section_one_has_two_questions_in_order():
    view = make_view()
    pos = start_position()
    q0 = current_question(pos, view)
    assert q0.code == "city"
    pos = next_position(pos, view, platform_count=0)
    q1 = current_question(pos, view)
    assert q1.code == "target_platform"


def test_fifteen_questions_in_order_regardless_of_answers():
    """The core requirement of the redesign: every respondent sees exactly
    Q1..Q15 in the same order, with no conditional branching and no
    per-platform repeats — the view's content must not change the path."""
    for survey_answers in ({}, {"platforms_used_includes_other": True}, {"receives_bonuses": False}):
        view = make_view(survey_answers)
        pos = start_position()
        pos = next_position(pos, view, platform_count=0)  # target_platform
        visited = []
        for _ in range(QUESTION_COUNT):
            pos = next_position(pos, view, platform_count=0)
            assert pos is not None
            assert pos.platform_pos is None
            visited.append(current_question(pos, view).code)
        assert visited == EXPECTED_CODES_IN_ORDER


def test_question_count_is_exactly_fifteen():
    assert QUESTION_COUNT == 15
    assert len(QUESTIONS_BY_DISPLAY_NUMBER) == 15
    assert list(QUESTIONS_BY_DISPLAY_NUMBER.keys()) == list(range(1, 16))


def test_all_fifteen_questions_have_display_numbers_matching_their_key():
    for n, question in QUESTIONS_BY_DISPLAY_NUMBER.items():
        assert question.display_number == n


def test_after_last_question_comes_screenshots_then_review():
    view = make_view()
    pos = start_position()
    pos = next_position(pos, view, platform_count=0)  # target_platform
    pos = walk_forward(pos, view, QUESTION_COUNT)  # Q1..Q15
    assert current_question(pos, view).code == "driver_motivation"  # Q15, last question
    pos = next_position(pos, view, platform_count=0)
    assert pos.section_index == SCREENSHOTS_SECTION_INDEX
    assert current_question(pos, view).code == "has_screenshots"
    pos = next_position(pos, view, platform_count=0)
    assert pos.section_index == SCREENSHOTS_SECTION_INDEX + 1  # review
    assert current_question(pos, view) is None


def test_forward_then_back_returns_to_start():
    view = make_view()
    pos = start_position()
    forward = walk_forward(pos, view, n=5)
    back = forward
    for _ in range(5):
        back = prev_position(back, view, platform_count=0)
    assert back == pos


def test_prev_at_very_first_question_returns_none():
    view = make_view()
    pos = start_position()
    assert prev_position(pos, view, platform_count=0) is None


def test_forward_through_entire_questionnaire_then_all_the_way_back():
    view = make_view()
    pos = start_position()
    total_steps = 1 + QUESTION_COUNT + 1  # target_platform + Q1..Q15 + has_screenshots
    forward = walk_forward(pos, view, n=total_steps)
    back = forward
    for _ in range(total_steps):
        back = prev_position(back, view, platform_count=0)
    assert back == pos


# ---- Validation ---------------------------------------------------------


def test_parse_number_accepts_plain_integer_and_decimal():
    assert parse_number("42") == 42.0
    assert parse_number("3.5") == 3.5


def test_parse_number_accepts_thousands_separators():
    assert parse_number("1,500,000") == 1_500_000.0


def test_parse_number_rejects_garbage():
    with pytest.raises(ValidationError):
        parse_number("not a number")


def test_parse_number_enforces_bounds():
    with pytest.raises(ValidationError):
        parse_number("10", min_value=0, max_value=7)
    with pytest.raises(ValidationError):
        parse_number("-1", min_value=0)


def test_parse_percentage_range():
    assert parse_percentage("50") == 50.0
    with pytest.raises(ValidationError):
        parse_percentage("101")
    with pytest.raises(ValidationError):
        parse_percentage("-5")


def test_parse_currency_rejects_negative():
    assert parse_currency("1000") == 1000.0
    with pytest.raises(ValidationError):
        parse_currency("-1")


def test_validation_error_carries_stable_code_for_localization():
    try:
        parse_number("nope")
    except ValidationError as exc:
        assert exc.code == "not_a_number"
    else:
        pytest.fail("expected ValidationError")

    try:
        parse_number("10", max_value=7)
    except ValidationError as exc:
        assert exc.code == "too_high"
        assert exc.params == {"max_value": 7}
    else:
        pytest.fail("expected ValidationError")
