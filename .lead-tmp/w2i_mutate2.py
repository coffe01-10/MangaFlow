# W2I mutation round 2: keep Ui.cs mutations (EnsureSuccessStatusCode + swallow), restore
# the dead-batch fix so the media checks (#486-3 / #449-2) get reached and must fail.
import io, sys

root = r"D:\自媒体\漫画工作流-wt-c"
card = root + r"\apps\desktop\native\Views\StyleProductionCard.cs"
with io.open(card, encoding="utf-8") as handle:
    text = handle.read()
anchor = "        if (!Profile.Flag(\"palette_confirmed\") || paletteDirty) throw new InvalidOperationException(\"请先确认并保存彩色色板。\");\n"
assert text.count(anchor) == 1
with io.open(card, "w", encoding="utf-8", newline="") as handle:
    handle.write(text.replace(anchor, anchor + "        await VerifyPendingBatchAsync();\n"))
print("restored: VerifyPendingBatchAsync call")
