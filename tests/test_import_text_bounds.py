"""Regression: hostile import payloads persist cleanly or fail with 4xx/201.

An oversized multipart ``title`` or a chapter-header line longer than the
200-character column overflowed ``chapters.title`` on PostgreSQL (raw
driver 500), and NUL bytes inside otherwise valid UTF-8 text made the
``original_text`` insert fail on PostgreSQL. Titles and decoded text are
now normalized once at the import boundary.
"""

from app.services.content_workflow import (
    normalize_chapter_title,
    normalize_source_text,
    split_chapters,
)


def test_normalize_source_text_strips_controls_keeps_content():
    assert normalize_source_text("a\x00b\x0bc\n\td") == "abc\n\td"
    assert "\x00" not in normalize_source_text("a\x00b\nc")
    assert normalize_source_text("正文\n第二行") == "正文\n第二行"


def test_chapter_title_is_clamped_to_column_bound():
    title = normalize_chapter_title("第一章 " + "长" * 300)
    assert len(title) == 200


def test_split_chapters_long_header_yields_bounded_title():
    chapters = split_chapters("正文", "第一章 " + "长" * 300 + "\n正文内容")
    assert len(chapters) == 1
    assert len(chapters[0][0]) == 200
    assert "\x00" not in normalize_source_text("第一章\x00长\n内容")


def test_split_chapters_empty_title_falls_back():
    chapters = split_chapters("\x00 \x00", "没有章节标记的正文")
    assert chapters[0][0] == "正文"
