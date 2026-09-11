**已修复（round 3 集成批次）**：三步全部落地——

1. **junction 感知克隆**（42f5e5e）：`build-frontend-static.sh` 新增 `clone_hardlink_tree`（逐条目 cp -al、跳过 junction 条目并按树根相对路径上报、对含链子树递归）+ `recreate_junction`（源目标经 repo 根前缀换算后 `cmd //c mklink //J` 重建，指向抛弃型 worktree 自己的 apps/web；目标出仓/缺失大声失败）。
2. **proxy.ts × export 实测**：Next 16.3.3 下构建全绿（`✓ Compiled successfully`、10/10 静态页生成、三条 SSG 路由产出），仅警告 middleware/API routes 在静态导出形态不可用——不是硬失败，proxy.ts 保持不动（与 #378 决策一致：静态形态 CSP 债务钉住）。
3. **冒烟门禁**：脚本尾部断言 `dist/frontend/index.html` + 三条补丁路由 HTML（按实测 `trailingSlash:false` 扁平布局钉死），缺失即 exit 非 0。

- 提交：42f5e5e；另 2e5615d 刷新被跟踪的导出产物 `dist/frontend/index.html`（集成 HEAD 上重跑产出）。
- 验证：组长在主仓集成 HEAD 实跑 `bash apps/desktop/scripts/build-frontend-static.sh` exit 0（junction 重建日志行 `recreated junction node_modules/@mangaflow/web -> .../apps/web`、10 页生成、冒烟过）；代理在 wt-c 实跑 4 次（最终两次 exit 0）；无 mangaflow-desktop-web-* 残留。
- NOT RUN：tauri build/NSIS 打包、真实安装运行、MSYS2/PowerShell 环境行为、CI。
