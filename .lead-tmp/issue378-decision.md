**决策（lead，2026-09-11）：维持钉住债务，不做 hash 注入，不迁移；理由记录后关闭。**

依据：

1. **hash 注入不可行已由钉住测试给出论证**：`apps/desktop/shell-core/tests/delivery_contract.rs:70-94`（`script_src_unsafe_inline_is_pinned_debt_not_drift`）写明——静态导出内联脚本包含 Next 的 flight 数据（`self.__next_f.push`，随每构建、每页变化）加 shell-tools.html 自有内联脚本；hash 方案必须对**每次构建的全部页面全部内联脚本**重算并重新生成 tauri.conf CSP，漏一个 hash 即静默白屏，属高脆弱性换低收益。该测试冻结精确指令串，任何放宽/收紧都必须是 lead 评审过的故意契约变更。
2. **nonce 加固的桌面路径是 plan-B standalone 形态**：`apps/desktop/scripts/assemble-web-resources.py` 组装的 `src-tauri/web`（node.exe + standalone server）有真实服务器在跑，PR #377 的 `proxy.ts` 每请求 nonce CSP 在该形态生效并有 delivery_contract/vitest 双钉住。静态导出形态无服务器，nonce 结构性不可用。
3. **静态导出形态的暴露面已被另外两条契约钉住**：网络面 loopback-only（`webview_network_surface_stays_loopback_only`），`object-src 'none'`/`frame-src 'none'` 同测冻结；残余的 `'unsafe-inline'` 是局部、已文档化的债。

后续若有需求推动静态形态去 inline-script（例如 Next 导出器改为外链 bootstrap），再按契约变更流程重开；那属于新证据驱动的新决策，不是本 issue 的悬置项。

关联：#300（nonce CSP 主线）、#377（plan-B/web 半边落地）。静态导出管道本身的可运行性问题另见 #385。
