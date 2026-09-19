"""aiogram FSM states, one per logical phase of the flat 15-question flow.

`questionnaire` covers all 15 numbered questions (sections 2-16) — there's
no more per-platform loop to give its own state, and which exact question
is current is tracked by SurveySession.cursor (persisted via FSMContext
data), not by which aiogram State we're in. The State only needs to be
coarse enough to route an incoming update to the survey handlers and back
out again; SurveySession is the source of truth for exact position.

`language_select` is not a questionnaire section (it has no SurveySession
position) — it's a standalone step handled entirely in
handlers/survey.py, before a SurveySession is even created/resumed.
"""

from aiogram.fsm.state import State, StatesGroup

from telegram_bot.domain.questionnaire import QUESTION_COUNT, SCREENSHOTS_SECTION_INDEX


class SurveyStates(StatesGroup):
    language_select = State()
    setup = State()  # section 1: city, target_platform
    questionnaire = State()  # sections 2..16: Q1-Q15
    screenshots = State()  # section 17
    review = State()  # section 18


SECTION_TO_STATE = {1: SurveyStates.setup}
SECTION_TO_STATE.update({i: SurveyStates.questionnaire for i in range(2, 2 + QUESTION_COUNT)})
SECTION_TO_STATE[SCREENSHOTS_SECTION_INDEX] = SurveyStates.screenshots
SECTION_TO_STATE[SCREENSHOTS_SECTION_INDEX + 1] = SurveyStates.review

ALL_SURVEY_STATES = [s for s in SurveyStates.__all_states__ if s != SurveyStates.language_select]
