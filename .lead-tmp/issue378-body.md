PR #377 完成了 #300 的 plan-B/web 半边（proxy.ts 每请求 nonce + 动态渲染 + delivery_contract/vitest 双钉住）。静态导出形态（tauri.conf 的 app.security.csp）**保留** unsafe-inline，本轮实证依据：
