**Found:** 第三轮只读审查发现"无回归网"；组长亲自实跑后升级为**管道在 HEAD 已断裂**（2026-09-11）。

## 实证

`bash apps/desktop/scripts/build-frontend-static.sh` 在硬链接克隆一步即失败（exit 1）：

```
cp: cannot create hard link '/d/自媒体/mangaflow-desktop-web-XXXX/node_modules/@mangaflow/web'
  to '/d/自媒体/漫画工作流/node_modules/@mangaflow/web': Permission denied
```

- `node_modules/@mangaflow/web` 是 npm workspaces 生成的**目录 Junction**（→ apps/web，自 2026-08-27 存在）；git bash 的 `cp -al` 对 junction 条目尝试建硬链接，必然失败（已在 scratch 目录确定性复现，与本次失败同因）。
- `apps/desktop/dist/` 下没有任何 `static-build.log`——tee 日志从未在本机成功产出过；`dist/frontend` 现存产物时间戳 2026-09-06 17:21，早于今天两轮改动，**当前 HEAD 的静态导出管道无人验证成功过**。
- 同时：今天 08:49 新增的 `apps/web/proxy.ts`（#300 nonce CSP，2abd7a5）与 `output: "export"` 的组合从未被构建验证（proxy.ts 在静态导出形态下无服务器可运行；Next 16 对 proxy+export 是否硬失败未知——管道修通前无法观察）。
- 无 CI 覆盖该脚本。

## 修复面

1. 让硬链接克隆感知 junction：跳过 junction 条目并在克隆目标里以 `cmd /c mklink /J` 重建（或对该条目降级为拷贝穿越），保证 worktree 依赖树完整。
2. 管道修通后实跑一次 `next build`（MANGAFLOW_STATIC_EXPORT=1），记录 proxy.ts × export 的真实行为；若构建硬失败，在抛弃型 worktree 内按 MANGAFLOW_STATIC_EXPORT 摘除 proxy.ts（业务树零改动）；若仅被忽略，记录现状即可（CSP 债务由 #378 决策钉住）。
3. 补冒烟门禁：脚本尾部对 `dist/frontend` 产物做最小断言（index.html 与关键路由 HTML 存在），失败即 exit 非 0。

## 边界

实跑验证只在本机 git bash + Windows node 环境成立；其他 shell 环境行为 NOT RUN。
