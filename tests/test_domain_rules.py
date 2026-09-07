import pytest

import app.domain.states as states_module
from app.domain.states import ensure_unlocked


def test_states_module_exports_no_unused_transition_tables():
    """Issue #128: the JOB/PAGE transition tables and ``ensure_transition``
    were dead code — zero runtime call sites, so they documented guarantees
    the runtime never enforced. They were removed; actual status-transition
    semantics are guaranteed by each write point's conditional UPDATE (see
    the module docstring in app/domain/states.py). This test pins the
    removal so the stale tables cannot silently return as dead exports.
    """

    assert not hasattr(states_module, "JOB_TRANSITIONS")
    assert not hasattr(states_module, "PAGE_TRANSITIONS")
    assert not hasattr(states_module, "ensure_transition")


def test_states_module_keeps_runtime_used_helpers():
    """``ensure_unlocked`` IS wired (inspection route guards locked fields),
    so it must survive the dead-table removal.
    """

    assert callable(states_module.ensure_unlocked)
    assert hasattr(states_module, "JobStatus")
    assert hasattr(states_module, "PageStatus")


def test_lock_blocks_child_and_parent_paths():
    with pytest.raises(ValueError, match="已锁定"):
        ensure_unlocked(["/layout"], ["/layout/panels/0"])
    with pytest.raises(ValueError, match="已锁定"):
        ensure_unlocked(["/panels/panel-1/dialogues"], ["/panels/panel-1"])


def test_unrelated_field_can_be_repaired():
    ensure_unlocked(["/characters/hero/face"], ["/background"])
