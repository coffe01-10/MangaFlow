"""Generate MangaFlow's README SVGs with the Python standard library.

Pixel glyphs and the run/stair rendering approach are adapted from the
write-visual-readme skill and keli-wen/agy-staff (MIT). The page emblem,
palette, diagrams and wording are specific to MangaFlow. See
assets/readme/LICENSE.pixel-font.txt. No network requests are made.
"""

import argparse
import re
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "readme"
INK, PAPER, WHITE = "#151512", "#f4f1e9", "#fcfbf7"
LINE, MUTED, RED, GREEN = "#d7d1c4", "#66604f", "#b23c25", "#47745a"
GLYPHS = {
    "0": (".XXX.", "X...X", "X..XX", "X.X.X", "XX..X", "X...X", ".XXX."),
    "1": ("..X..", ".XX..", "..X..", "..X..", "..X..", "..X..", ".XXX."),
    "2": (".XXX.", "X...X", "....X", "...X.", "..X..", ".X...", "XXXXX"),
    "3": ("XXXXX", "....X", "...X.", "..XX.", "....X", "X...X", ".XXX."),
    "4": ("...X.", "..XX.", ".X.X.", "X..X.", "XXXXX", "...X.", "...X."),
    "A": (".XXX.", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"),
    "B": ("XXXX.", "X...X", "X...X", "XXXX.", "X...X", "X...X", "XXXX."),
    "C": (".XXX.", "X...X", "X....", "X....", "X....", "X...X", ".XXX."),
    "D": ("XXXX.", "X...X", "X...X", "X...X", "X...X", "X...X", "XXXX."),
    "E": ("XXXXX", "X....", "X....", "XXXX.", "X....", "X....", "XXXXX"),
    "F": ("XXXXX", "X....", "X....", "XXXX.", "X....", "X....", "X...."),
    "G": (".XXX.", "X...X", "X....", "X.XXX", "X...X", "X...X", ".XXXX"),
    "H": ("X...X", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"),
    "I": ("XXX", ".X.", ".X.", ".X.", ".X.", ".X.", "XXX"),
    "J": (".XXXX", "...X.", "...X.", "...X.", "...X.", "X..X.", ".XX.."),
    "K": ("X...X", "X..X.", "X.X..", "XX...", "X.X..", "X..X.", "X...X"),
    "L": ("X....", "X....", "X....", "X....", "X....", "X....", "XXXXX"),
    "M": ("X...X", "XX.XX", "X.X.X", "X.X.X", "X...X", "X...X", "X...X"),
    "N": ("X...X", "XX..X", "X.X.X", "X..XX", "X...X", "X...X", "X...X"),
    "O": (".XXX.", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."),
    "P": ("XXXX.", "X...X", "X...X", "XXXX.", "X....", "X....", "X...."),
    "R": ("XXXX.", "X...X", "X...X", "XXXX.", "X.X..", "X..X.", "X...X"),
    "S": (".XXXX", "X....", "X....", ".XXX.", "....X", "....X", "XXXX."),
    "T": ("XXXXX", "..X..", "..X..", "..X..", "..X..", "..X..", "..X.."),
    "U": ("X...X", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."),
    "V": ("X...X", "X...X", "X...X", "X...X", "X...X", ".X.X.", "..X.."),
    "W": ("X...X", "X...X", "X...X", "X.X.X", "X.X.X", "XX.XX", "X...X"),
    "Y": ("X...X", "X...X", ".X.X.", "..X..", "..X..", "..X..", "..X.."),
    ".": ("..", "..", "..", "..", "..", "XX", "XX"),
    "/": ("....X", "...X.", "...X.", "..X..", ".X...", ".X...", "X...."),
    " ": ("...", "...", "...", "...", "...", "...", "..."),
    "+": (".....", "..X..", "..X..", "XXXXX", "..X..", "..X..", "....."),
}


def pixel_width(value, unit):
    return sum((len(GLYPHS[c][0]) + 1) * unit for c in value) - unit


def pixel_text(value, x, y, unit, color):
    parts = []
    for char in value:
        for row_index, row in enumerate(GLYPHS[char]):
            for run in re.finditer("X+", row):
                parts.append(
                    rect(
                        x + run.start() * unit,
                        y + row_index * unit,
                        len(run.group()) * unit,
                        unit,
                        color,
                    )
                )
        x += (len(GLYPHS[char][0]) + 1) * unit
    return '<g shape-rendering="crispEdges">' + "".join(parts) + "</g>"


def rect(x, y, width, height, fill, stroke=None):
    outline = f' stroke="{stroke}" stroke-width="1.5"' if stroke else ""
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="{fill}"{outline}/>'


def text(x, y, value, size=18, color=INK, weight=400, anchor="start"):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
        f'font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>'
    )


def path(value, color=INK, width=2, fill="none"):
    return f'<path d="{value}" fill="{fill}" stroke="{color}" stroke-width="{width}"/>'


def document(width, height, title, description, body, *, licensed=False):
    notice = ""
    if licensed:
        license_text = (OUT / "LICENSE.pixel-font.txt").read_text(encoding="utf-8")
        notice = "<!-- Pixel glyph attribution.\n" + license_text + "-->\n"
    return (
        notice + f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(title, quote=True)}">\n<title>{escape(title)}</title>\n'
        f"<desc>{escape(description)}</desc>\n{body}\n</svg>\n"
    )


def page_icon(kind, color=RED):
    base = rect(4, 4, 66, 80, PAPER, INK)
    if kind == 0:
        base += path("M16 22H57 M16 34H57 M16 46H48 M16 58H54", MUTED, 3)
        base += rect(49, 66, 14, 8, color)
    elif kind == 1:
        base += rect(12, 13, 25, 25, color)
        base += rect(43, 13, 19, 25, "#ebe6da", INK)
        base += rect(12, 45, 50, 28, WHITE, INK)
        base += path("M17 66L30 53L39 64L48 57L58 67", color)
    elif kind == 2:
        base += rect(11, 13, 51, 60, "#e8eee5")
        base += path("M14 66L29 45L43 60L54 44L62 64", color, 2.5)
        base += '<circle cx="26" cy="29" r="8" fill="' + color + '"/>'
        base += rect(50, 3, 23, 20, RED)
        base += text(61.5, 18, "1", 15, WHITE, 700, "middle")
    else:
        base += rect(12, 14, 22, 24, "#ebe6da", INK)
        base += rect(40, 14, 22, 24, "#e8eee5", INK)
        base += rect(12, 45, 50, 28, WHITE, INK)
        base += path("M25 59L34 67L53 49", color, 4)
    return base


def logo():
    style = (
        "<style>.logo-ink{color:#151512}.logo-muted{color:#66604f}"
        ".logo-accent{color:#b23c25}@media(prefers-color-scheme:dark){"
        ".logo-ink{color:#f4f1e9}.logo-muted{color:#c0b8a6}"
        ".logo-accent{color:#f07c62}}</style>"
    )
    body = style + '<g transform="translate(12,16)">' + page_icon(1) + "</g>"
    body += '<g class="logo-ink">' + pixel_text("MANGA", 110, 26, 8, "currentColor") + "</g>"
    body += '<g class="logo-accent">' + pixel_text("FLOW", 358, 26, 8, "currentColor") + "</g>"
    body += (
        '<g class="logo-muted">'
        + pixel_text("AI / MANGA WORKBENCH", 112, 94, 2, "currentColor")
        + "</g>"
    )
    return document(
        562, 122, "MangaFlow AI", "漫画页图标与像素字标；墨色随明暗主题变化。", body, licensed=True
    )


def stair(x, y, w, h):
    return (
        f"M{x + 4},{y}H{x + w - 4}V{y + 2}H{x + w - 2}V{y + 4}H{x + w}"
        f"V{y + h - 4}H{x + w - 2}V{y + h - 2}H{x + w - 4}V{y + h}H{x + 4}"
        f"V{y + h - 2}H{x + 2}V{y + h - 4}H{x}V{y + 4}H{x + 2}V{y + 2}H{x + 4}Z"
    )


def badge(label, value, accent, title):
    split = 20 + pixel_width(label, 2)
    width = split + pixel_width(value, 2) + 22
    body = '<g shape-rendering="crispEdges">'
    for x, y, w, h, color in [
        (2, 2, width - 2, 22, INK),
        (0, 0, width - 2, 22, LINE),
        (1, 1, width - 4, 20, INK),
    ]:
        body += f'<path d="{stair(x, y, w, h)}" fill="{color}"/>'
    body += rect(split, 3, width - split - 8, 16, accent)
    body += pixel_text(label, 10, 4, 2, WHITE)
    body += pixel_text(value, split + 7, 4, 2, WHITE)
    return document(
        width, 24, title, "静态项目信息，不是实时 CI 状态。", body + "</g>", licensed=True
    )


STEPS = [
    ("原文与设定", "原作导入与来源追溯", "人物 / 服装 / 风格", "INPUT", INK),
    ("剧本与分镜", "解析 / 分页 / 格位", "确认当前分镜版本", "STORYBOARD", GREEN),
    ("逐页生成", "一次一个页面候选", "选择模型与参考图", "GENERATION", RED),
    ("校对与成品", "人工采用 / 视觉检查", "单页输出与整章导出", "HUMAN REVIEW", GREEN),
]


def overview(mobile=False):
    width, height = (430, 904) if mobile else (920, 620)
    body = (
        '<style>text{font-family:"Microsoft YaHei","Noto Sans CJK SC",'
        'Arial,sans-serif}</style><defs><pattern id="paper-dots" width="16" '
        'height="16" patternUnits="userSpaceOnUse"><circle cx="8" cy="8" '
        'r="0.6" fill="#c9c2b3"/></pattern></defs>'
    )
    body += rect(1, 1, width - 2, height - 2, PAPER, LINE)
    body += rect(2, 2, width - 4, height - 4, "url(#paper-dots)")
    margin = 24 if mobile else 32
    body += text(margin, 36, "MANGAFLOW / PRODUCTION MAP", 12 if mobile else 14, MUTED, 700)
    body += text(
        margin,
        80,
        "一段原文，一页漫画。" if mobile else "从一段原文，到一页漫画。",
        27 if mobile else 36,
        INK,
        700,
    )
    body += text(
        margin,
        112,
        "流程示意 · 非界面截图" if mobile else "AI 处理解析与绘图，你负责设定、校对与采用。",
        16 if mobile else 18,
        MUTED,
    )
    if not mobile:
        body += text(888, 36, "流程示意 · 非界面截图", 14, MUTED, anchor="end")
    for index, (title, line1, line2, label, accent) in enumerate(STEPS):
        x, y, w, h = (
            (24, 142 + index * 150, 382, 128) if mobile else (32 + index * 220, 144, 196, 280)
        )
        body += rect(x + 4, y + 5, w, h, "#ded7c9") + rect(x, y, w, h, WHITE, INK)
        body += rect(x, y, w, 4, accent)
        if mobile:
            body += text(x + 16, y + 34, f"0{index + 1}", 22, accent, 700)
            body += text(x + 68, y + 35, title, 24, INK, 700)
            body += text(x + 68, y + 68, line1, 16, MUTED)
            body += text(x + 68, y + 95, line2, 16, MUTED)
            body += (
                f'<g transform="translate({x + 15},{y + 49}) scale(.5)">'
                + page_icon(index, accent)
                + "</g>"
            )
            if index < 3:
                body += path(f"M215 {y + h + 6}v11m-5-5 5 5 5-5", RED)
        else:
            body += text(x + 16, y + 32, f"0{index + 1}", 20, accent, 700)
            body += (
                f'<g transform="translate({x + 58},{y + 49})">' + page_icon(index, accent) + "</g>"
            )
            body += text(x + 16, y + 176, title, 25, INK, 700)
            body += text(x + 16, y + 206, line1, 16, MUTED)
            body += text(x + 16, y + 231, line2, 16, MUTED)
            body += text(x + 16, y + 261, label, 11, accent, 700)
            if index < 3:
                body += path(f"M{x + w + 6} 282h12m-5-5 5 5-5 5", RED)
    if mobile:
        body += text(24, 766, "本地：SQLite + 文件目录", 18, INK, 600)
        body += text(24, 797, "调用：配置的外部模型服务", 18, INK, 600)
        body += path("M24 820H406", LINE, 1)
        body += text(24, 851, "逐页确认，不自动生成整章。", 18, INK, 700)
        body += text(24, 881, "可靠性加固中，当前限制见路线图。", 15, RED)
    else:
        for x, heading, detail in [
            (32, "本地数据 / SQLite + 文件目录", "项目、版本、任务、素材与导出"),
            (470, "外部模型 / API 与 Worker 调用", "按配置向供应商发送所需文本与图片"),
        ]:
            body += rect(x, 458, 418, 70, WHITE, LINE)
            body += text(x + 18, 486, heading, 19, INK, 600)
            body += text(x + 18, 512, detail, 16, MUTED)
        body += text(32, 569, "每页由你推进。不是无人值守的整章生成器。", 20, INK, 700)
        body += text(32, 600, "可靠性加固中 · 取消、保存与质检边界见路线图", 16, RED)
    description = (
        "原文与人物服装风格设定，进入剧本分页和分镜，再逐页生成一个候选；"
        "人工校对采用和视觉检查后输出。数据保存在本地，AI 调用使用外部供应商。"
        "这是业务流程示意，不是运行截图；任务可靠性、保存与质检仍有待修复问题。"
    )
    return document(width, height, "MangaFlow 漫画创作流程概览", description, body)


def outputs():
    return {
        "logo.svg": logo(),
        "badges/node.svg": badge("NODE", "22+", GREEN, "Node.js 22 及以上"),
        "badges/python.svg": badge("PYTHON", "3.12+", GREEN, "Python 3.12 及以上"),
        "badges/windows.svg": badge("DEV", "WINDOWS", "#665846", "Windows PowerShell 开发入口"),
        "badges/stage.svg": badge("STAGE", "MVP", RED, "MVP 阶段，可靠性加固中"),
        "overview.svg": overview(),
        "overview-mobile.svg": overview(mobile=True),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true", help="regenerate known SVG output files")
    mode.add_argument("--check", action="store_true", help="verify generated SVGs without writing")
    args = parser.parse_args()
    generated = outputs()
    if args.check:
        stale = [
            name
            for name, value in generated.items()
            if not (OUT / name).is_file() or (OUT / name).read_text(encoding="utf-8") != value
        ]
        if stale:
            parser.exit(1, "SVGs out of sync: " + ", ".join(stale) + "\n")
        print(f"PASS: {len(generated)} SVGs match their generator")
        return
    existing = [name for name in generated if (OUT / name).exists()]
    if existing and not args.force:
        parser.exit(2, "Outputs exist; use --force to regenerate: " + ", ".join(existing) + "\n")
    for name, value in generated.items():
        target = OUT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w" if args.force else "x", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
        print(target.relative_to(ROOT))


if __name__ == "__main__":
    main()
