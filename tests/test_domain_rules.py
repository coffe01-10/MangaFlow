import pytest

from app.domain.states import (
    PAGE_TRANSITIONS,
    PageStatus,
    ensure_transition,
    ensure_unlocked,
)


def test_page_happy_path_transition_is_allowed():
    ensure_transition(PageStatus.PLANNED, PageStatus.STORYBOARDED, PAGE_TRANSITIONS)


def test_page_cannot_skip_review_states():
    with pytest.raises(ValueError, match="非法状态迁移"):
        ensure_transition(PageStatus.PLANNED, PageStatus.FINAL_READY, PAGE_TRANSITIONS)


def test_lock_blocks_child_and_parent_paths():
    with pytest.raises(ValueError, match="已锁定"):
        ensure_unlocked(["/layout"], ["/layout/panels/0"])
    with pytest.raises(ValueError, match="已锁定"):
        ensure_unlocked(["/panels/panel-1/dialogues"], ["/panels/panel-1"])


def test_unrelated_field_can_be_repaired():
    ensure_unlocked(["/characters/hero/face"], ["/background"])
