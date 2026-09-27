# PUB-01B 条漫（WEBTOON）导出契约冻结

状态：已冻结（2026-10-05，基线 `4a51f103`）
关联：roadmap PUB-01A（手机预览，`computeStrip`）、PUB-01C（实现）

本文件冻结条漫导出的输出结构、参数、切片规则与清理语义。PUB-01C 的实现
必须与本文逐条一致；任何偏离先修订本文再改代码。

## 1. 导出类型与产物结构

新增导出类型 `WEBTOON`，复用既有 `POST /chapters/{id}/exports`、
`ExportBundle` 行与 `/exports/{id}/download` 下载链路。产物是一个 ZIP：

```text
slice-0001.jpg        # 或 .png，按 format
slice-0002.jpg
...
manifest.json         # 最后写入，见 §4
```

- 分片文件名 `slice-NNNN.{ext}`：四位零填充序号，从 0001 起按阅读顺序稳定
  编号；同一输入参数与候选集合下文件名集合恒定。
- ZIP 内不出现嵌套目录、不出现非 `slice-*`/`manifest.json` 成员。
- `ExportBundle.export_type = "WEBTOON"`；下载 media type = `application/zip`；
  现有 PNG/PDF/JSON 三类的请求格式、文件名规则与门禁语义保持不变。

## 2. 参数与预设

`ExportRequest` 在 `export_type` 之外新增可选字段；缺省值由预设提供，显式
字段覆盖同名预设值。参数仅对 `WEBTOON` 生效；对 PNG/PDF/JSON 提交这些
字段返回 422。

| 字段 | 范围 | STANDARD 默认 |
| --- | --- | --- |
| `preset` | `STANDARD` / `COMPACT` / `HQ_PNG` | `STANDARD` |
| `width` | 320–4096 px | 1080 |
| `format` | `JPEG` / `PNG` | `JPEG` |
| `quality` | 50–100（仅 JPEG） | 88 |
| `gap_px` | 0–128 | 16 |
| `max_slice_height` | 512–16384 | 4096 |

预设表（实现侧常量，非数据模型；平台限值以后以新增预设名的方式接入，
不把某个平台写死进 schema）：

| 预设 | width | format | quality | gap_px | max_slice_height |
| --- | --- | --- | --- | --- | --- |
| STANDARD | 1080 | JPEG | 88 | 16 | 4096 |
| COMPACT | 800 | JPEG | 85 | 0 | 8192 |
| HQ_PNG | 1440 | PNG | — | 0 | 8192 |

`ExportRequest` 只携带参数，不持久化参数列：本次导出的完整参数快照写进
ZIP 内 `manifest.json`；`ExportBundle` 行维持既有列（不改 schema、不加迁移）。
幂等键由「采用候选集合 + 规范化参数」共同决定：`token = sha256(candidate_ids
| canonical_params)[:12]`，沿用既有 `{token}-{serial}-{suffix}` 命名与
`reuse_existing` 前缀匹配语义。

## 3. 切片规则（与 PUB-01A 预览一致的唯一算法）

输入为全部生产通过页，按 `page_number` 升序；切片在「目标宽度图像像素」
空间进行：

1. 每页源图按 `width` 等比缩放，条带内高度 `h_i = round(src_h × width / src_w)`；
   `src_w/src_h` 缺失或 ≤0 视为数据缺陷，该页导出 409（预览侧的 4:3 占位只
   是显示回退，不进入导出）。
2. 相邻页间插入 `gap_px` 间距；间距归属前一页条带段，切片边界只落在
   「间距末尾 = 下一页顶边」，即切片内不出现半间距，片首/片尾不带间距。
3. 贪心页间优先：顺序累加页面，当下一个页面的底边会使当前片高度超过
   `max_slice_height` 时，在该页顶边的页间缝处收刀。切片内允许任意多页，
   但总高严格 ≤ `max_slice_height`。
4. 超限单页：缩放后 `h_i > max_slice_height` 的页按 `max_slice_height`
   等距**硬切**为 `ceil(h_i / max_slice_height)` 段，每段独立成片、不与
   前后页面合并打包（尾部不足段同样自成一片）；该页前若有未收刀的普
   通片先在其顶边页间缝收刀。硬切点与页间切片共用同一序号空间；
   manifest 对该类分片标注 `hard_cut: true`。
5. 由此每一片高度恒 ≤ `max_slice_height`，且每片要么是若干整页加内部
   间距，要么是单个页面的一个连续纵向段。

**一致性约束**：PUB-01A 预览的 `apps/web/lib/mobile-preview.ts`
`computeStrip` 与本节为同一算法的双语实现——页间优先、硬切超限页、
边界序号一致；改任一侧必须同步另一侧，并共享第 §5 的金样例断言。

## 4. manifest.json 结构

```json
{
  "schema_version": "1.0",
  "generator": "mangaflow-webtoon-export",
  "export_type": "WEBTOON",
  "project": {"id": "...", "name": "..."},
  "chapter": {"id": "...", "title": "..."},
  "params": {"preset": "STANDARD", "width": 1080, "format": "JPEG",
             "quality": 88, "gap_px": 16, "max_slice_height": 4096},
  "page_count": 12,
  "total_height": 18352,
  "slices": [
    {
      "file": "slice-0001.jpg",
      "index": 1,
      "width": 1080,
      "height": 2000,
      "sha256": "<slice bytes>",
      "hard_cut": false,
      "pages": [
        {"page_id": "...", "page_number": 1,
         "src_rect": [0, 0, 1440, 2160],
         "dst_rect": [0, 0, 1080, 1620]}
      ]
    }
  ]
}
```

- `slices[*].pages` 列出该片覆盖的页面及其源/目标矩形；跨片硬切页会在
  相邻两片各出现一次（`src_rect`/`dst_rect` 记录各自段）。
- `sha256` 逐片记录，供「导出后逐片检查」验收与下载端校验。
- 分片内顶部为最早页码，`dst_rect` y 值随片内顺序递增且间距等于 `gap_px`。

## 5. 内存上限与流式生成

- 峰值内存 ≤ `max(源图解码位图, width × max_slice_height × 4) + 单页缩放位图`。
  源图像素上限沿用 `settings.max_image_pixels` 摄入门禁。
- 实现必须逐页流式：解码当前页 → 缩放到 `width` → 追加/硬切写入当前片
  缓冲 → 片满即落盘到临时目录并释放缓冲；不得把整章位图同时驻留内存。
- 防御上限：分片数 > 999 或解压缩后总字节 > 2 GiB 时拒绝导出并返回
  422（参数越界、素材异常之外的第三条确定性失败路径）。

## 6. 导出记录与中断清理

- 沿用 `_write_export_atomically`：唯一 `.tmp` 落盘 + `os.replace` 换入，
  异常时删除 `.tmp`；分片先写入同目录 `.{serial}.slices/` 临时目录，
  ZIP 完成后整目录删除，异常时尽力清理。
- 进程崩溃遗留的 `.{serial}.slices/` 与 `*.tmp` 属于无害孤儿文件；下一次
  同章节导出开始前先扫描清理该目录内的孤儿（有界、同目录、同名前缀，
  不碰活文件）。
- `ExportBundle` 行只在文件就位后提交，沿用每章每类保留 20 条的
  `_prune_superseded_exports`；下载路由补 `WEBTOON → application/zip`。

## 7. 门禁与错误

- 沿用 `_selected_pages`：任一页面未达生产通过 → 409 + 页码 + 阻塞明细；
  采用素材缺失 → 409。条漫导出不放行部分就绪章节。
- 参数非法 → 422（Pydantic 边界）；尺寸缺失页 → 409；片数/体量超限 → 422。
- 不调用任何图片模型，不修改候选、分镜或既有导出文件。

## 8. 验收锚点（PUB-01C 实现的对照面）

- 金样例切片断言（前后端同一数据）：3×1000px 页、gap 16、max 1100 →
  边界 [0,1016) / [1016,2032) / [2032,3032)；单页 5000px、max 1000 →
  5 段等长硬切片。
- ZIP 内成员集合、manifest 字段、逐片 sha256 与解码尺寸一致；
  预览片界像素位置 = 导出片界 / `width` × 视口宽。
- 长章节以构造页数验证峰值内存不随页数线性增长（逐页释放）。
