# W2I mutation round 3: restore the #449-2 error mapping, keep ONLY the Lightbox swallow
# (catch (Exception) { }) so the #486-3 lightbox visibility check must fail.
import io, sys

ui = r"D:\自媒体\漫画工作流-wt-c" + r"\apps\desktop\native\Controls\Ui.cs"
with io.open(ui, encoding="utf-8") as handle:
    text = handle.read()
old = "            response.EnsureSuccessStatusCode();\n"
new = """            if (!response.IsSuccessStatusCode)
            {
                // #449-2：原 EnsureSuccessStatusCode 只会抛生成的英文行，后端 404/409
                // 的 detail（如素材归档冲突）到不了任何图片界面；改走 MediaErrors 映射。
                var text = await response.Content.ReadAsStringAsync(cancellation).ConfigureAwait(false);
                throw new InvalidOperationException(MediaErrors.DescribeResponse(response, text));
            }
"""
assert text.count(old) == 1, "anchor not found"
with io.open(ui, "w", encoding="utf-8", newline="") as handle:
    handle.write(text.replace(old, new))
print("restored: MediaErrors mapping (Lightbox swallow kept)")
