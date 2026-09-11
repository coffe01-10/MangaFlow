**Found:** 第三轮只读审查（902c58f..7a3cefd 后半），组长已亲自读代码核实到可触发级（2026-09-11）。

## 机制

`apps/desktop/scripts/assemble-web-resources.py` 的 swap 序列：

```
72    if res.exists():
73        os.rename(res, retired)      # (1) 旧树挪走
74    try:
75        os.rename(staging, res)      # (2) 新树就位
76    except BaseException:
80        if retired.exists() and not res.exists():
81            os.rename(retired, res)  # 回滚
82        raise
83    finally:
86        shutil.rmtree(staging, ignore_errors=True)
87        shutil.rmtree(retired, ignore_errors=True)   # ← 删旧树
```

缺陷：回滚保护只包住第二条 rename。**在 (1) 完成后、(2) 开始前落入异步异常**（Ctrl-C 的 KeyboardInterrupt、SystemExit、MemoryError）会绕过 except 直达 finally：`rmtree(staging)` 删掉新树、`rmtree(retired)` 删掉唯一旧树 → `src-tauri/web` 根整体消失，比该脚本要防的"截断 bundle"更糟，且与模块 docstring 的承诺（"a mid-copy failure leaves the previous tree byte-identical"）相抵触。回滚 rename 自身失败时同构（retired 仍在、res 仍缺，finally 照删）。

现有测试 `tests/test_assemble_web_resources.py` 只钉了"拷贝中途失败"与"成功交换"两条路径，**两条 rename 之间与回滚失败的窗口无覆盖**。

## 修复面

- 把回滚保护扩大到覆盖 (1) 之后的所有失败路径；finally 删 `retired` 前必须确认 `res` 已就位（成功交换或回滚成功），否则保留 retired 并大声报错指引手动恢复。
- 测试：monkeypatch `os.rename` 在两条 rename 之间抛 `KeyboardInterrupt`，断言异常传播后旧树字节级存活；回滚 rename 失败路径同法钉住。

## 边界

P4：窗口窄、仅在打包机 Ctrl-C/致命异常时触发；但后果是唯一资源树被删，值得修。
