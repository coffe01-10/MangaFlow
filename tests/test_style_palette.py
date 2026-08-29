from app.services.worker_handlers.style_analyze import (
    _build_color_palette,
    _build_style_prompt_summary,
)


def test_color_palette_falls_back_to_structured_editable_fields() -> None:
    palette = _build_color_palette(
        {
            "color_rules": ["冷灰蓝主色", "黑色短发", "偏冷自然肤色"],
            "lighting": "柔和冷色散射光",
        }
    )

    assert palette == {
        "主色": "冷灰蓝主色",
        "辅助色": "低明度卡其灰与雾紫，只用于小面积识别和层次",
        "肤色": "偏冷的自然肤色，保留血色但避免过度红润",
        "发色": "深黑与低明度识别色，保留发丝层次和角色辨识度",
        "环境色": "潮湿京都的蓝灰、纸门米灰与深木色",
        "光影色": "柔和冷色散射光",
    }


def test_style_prompt_summary_does_not_copy_reference_subjects() -> None:
    summary = _build_style_prompt_summary(
        {
            "line_art": "细腻线稿",
            "screentone": "柔和色块",
            "contrast": "中低对比",
            "panel_language": "右至左分格",
            "lighting": "冷色散射光",
            "prompt_summary": "图书馆里的男子和紫发少女",
        },
        "color",
    )

    assert summary.startswith("彩色日式漫画")
    assert "图书馆" not in summary
    assert "紫发少女" not in summary
