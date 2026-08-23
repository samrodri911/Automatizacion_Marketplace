import pytest

from app.automation.states import (
    RESUMABLE_STATES,
    AutomationState,
    AutomationStateMachine,
    StateTransitionError,
)


def test_initial_state_is_idle():
    machine = AutomationStateMachine()
    assert machine.state == AutomationState.IDLE


def test_transition_updates_state_and_history():
    machine = AutomationStateMachine()
    machine.transition_to(AutomationState.STARTING_BROWSER)
    machine.transition_to(AutomationState.CHECKING_SESSION)

    assert machine.state == AutomationState.CHECKING_SESSION
    assert machine.history == [
        AutomationState.IDLE,
        AutomationState.STARTING_BROWSER,
        AutomationState.CHECKING_SESSION,
    ]


def test_intervention_and_resume_returns_to_previous_state():
    machine = AutomationStateMachine()
    machine.transition_to(AutomationState.FILLING_DATA)

    machine.request_intervention(reason="CAPTCHA")
    assert machine.state == AutomationState.WAITING_USER

    resumed = machine.resume()
    assert resumed == AutomationState.FILLING_DATA
    assert machine.state == AutomationState.FILLING_DATA


def test_resume_without_intervention_raises():
    machine = AutomationStateMachine()
    with pytest.raises(StateTransitionError):
        machine.resume()


def test_terminal_states_detected():
    machine = AutomationStateMachine()
    assert machine.is_terminal() is False
    machine.transition_to(AutomationState.SUCCESS)
    assert machine.is_terminal() is True


def test_reset_returns_to_idle():
    machine = AutomationStateMachine()
    machine.transition_to(AutomationState.ERROR)
    machine.reset()
    assert machine.state == AutomationState.IDLE
    assert machine.history == [AutomationState.IDLE]


def test_open_your_listings_is_resumable():
    # La navegación a "Tus publicaciones" es un paso pausable: si Facebook
    # pide una acción manual, debe poder reanudarse exactamente ahí.
    machine = AutomationStateMachine()
    machine.transition_to(AutomationState.OPENING_YOUR_LISTINGS)

    machine.request_intervention(reason="verificación manual")
    assert machine.state == AutomationState.WAITING_USER

    resumed = machine.resume()
    assert resumed == AutomationState.OPENING_YOUR_LISTINGS


def test_open_your_listings_in_resumable_set():
    assert AutomationState.OPENING_YOUR_LISTINGS in RESUMABLE_STATES


# --------------------------------------------------------------------------
# Nueva FSM del flujo de republicación (Iteración 5)
# --------------------------------------------------------------------------
def test_republish_flow_states_exist():
    for name in (
        "MATCHED",
        "EDITING_PRODUCT",
        "AWAITING_REPUBLISH_CONFIRM",
        "FILLING_LISTING",
        "REPUBLISHED",
        "REPUBLISH_BLOCKED",
    ):
        assert hasattr(AutomationState, name)


def test_republish_end_to_end_transitions():
    machine = AutomationStateMachine()
    for state in (
        AutomationState.SCANNING_LISTINGS,
        AutomationState.LISTINGS_SCANNED,
        AutomationState.MATCHED,
        AutomationState.EDITING_PRODUCT,
        AutomationState.AWAITING_REPUBLISH_CONFIRM,
        AutomationState.VERIFYING_DELETE,
        AutomationState.AWAITING_DELETE_CONFIRM,
        AutomationState.DELETING_LISTING,
        AutomationState.VERIFYING_DELETION,
        AutomationState.LISTING_DELETED,
        AutomationState.CREATING_LISTING,
        AutomationState.FILLING_LISTING,
        AutomationState.UPLOADING_IMAGES,
        AutomationState.PUBLISHING,
        AutomationState.VERIFYING_PUBLICATION,
        AutomationState.REPUBLISHED,
    ):
        machine.transition_to(state)
    assert machine.state == AutomationState.REPUBLISHED
    assert machine.is_terminal()
    assert len(machine.history) == 17  # IDLE + 16 transiciones


def test_republish_blocked_is_terminal():
    machine = AutomationStateMachine()
    machine.transition_to(AutomationState.REPUBLISH_BLOCKED)
    assert machine.is_terminal() is True


def test_filling_listing_and_verifying_publication_are_resumable():
    assert AutomationState.FILLING_LISTING in RESUMABLE_STATES
    assert AutomationState.VERIFYING_PUBLICATION in RESUMABLE_STATES


def test_resume_after_intervention_in_filling_returns_where_it_was():
    machine = AutomationStateMachine()
    machine.transition_to(AutomationState.FILLING_LISTING)
    machine.request_intervention(reason="CAPTCHA")
    assert machine.state == AutomationState.WAITING_USER
    resumed = machine.resume()
    assert resumed == AutomationState.FILLING_LISTING
