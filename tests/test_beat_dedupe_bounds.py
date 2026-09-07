"""Regression: beat dedupe must not drop distinct dialogue.

The merge-time dedupe keyed duplicates on (source_segment_ids, action) only,
so two legitimate beats sharing a segment and a generic action line
("两人交谈") but carrying different dialogue collapsed into one — scripted
dialogue silently deleted from the persisted source with no error. The key
now includes the beat content; only verbatim repeats are dropped.
"""

from app.services.ai_schemas import BeatDraft
from app.services.worker_handlers.story_parse import _resequence_beats


def _beat(ordinal: int, action: str, dialogue: str, speaker: str = "林澈") -> BeatDraft:
    return BeatDraft(
        ordinal=ordinal,
        action=action,
        speaker_name=speaker,
        dialogue=dialogue,
        source_segment_ids=["seg-1"],
    )


def test_distinct_dialogue_survives_shared_action_and_segment():
    beats = [
        _beat(1, "两人交谈", "你要去哪？"),
        _beat(2, "两人交谈", "去车站。"),
    ]
    result = _resequence_beats(beats)
    assert [beat.dialogue for beat in result] == ["你要去哪？", "去车站。"]
    assert [beat.ordinal for beat in result] == [1, 2]


def test_verbatim_repeat_is_still_dropped():
    beats = [
        _beat(1, "两人交谈", "你要去哪？"),
        _beat(2, "两人交谈", "你要去哪？"),
    ]
    result = _resequence_beats(beats)
    assert len(result) == 1


def test_different_speaker_same_action_is_kept():
    beats = [
        _beat(1, "两人交谈", "你要去哪？", speaker="林澈"),
        _beat(2, "两人交谈", "我要去车站。", speaker="苏黎"),
    ]
    result = _resequence_beats(beats)
    assert len(result) == 2


def test_register_unmerged_tokens_pins_renamed_character():
    """A lost version claim skips the alias merge; the character's committed
    (possibly renamed) tokens must still register so later drafts under the
    same parse report alias conflicts against the renamed row."""

    from app.services.worker_handlers.story_parse import (
        register_unmerged_tokens,
    )

    all_aliases: dict[str, str] = {"顾川": "顾川", "小川": "顾川"}
    register_unmerged_tokens(all_aliases, "顾队长", ["队长", "老顾"])

    assert all_aliases["顾队长"] == "顾队长"
    assert all_aliases["队长"] == "顾队长"
    assert all_aliases["老顾"] == "顾队长"
    # Pre-existing tokens are untouched.
    assert all_aliases["顾川"] == "顾川"
    # Re-registration never overwrites an established owner.
    register_unmerged_tokens(all_aliases, "顾队长", ["顾川"])
    assert all_aliases["顾川"] == "顾川"
