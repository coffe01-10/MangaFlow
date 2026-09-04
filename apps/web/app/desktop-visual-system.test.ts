import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// V02-51B (Issue #108) visual-system contracts from
// docs/v02-desktop-workspace-ux-audit.md §4/§8/§11/§13. These assertions read
// the stylesheet source, matching the repo's existing style-contract tests.
const stylesheet = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");
const projectWorkspaceSource = readFileSync(resolve(process.cwd(), "components/project-workspace.tsx"), "utf8");

function mediaBlocks(query: string) {
  const blocks: string[] = [];
  let searchFrom = 0;
  for (;;) {
    const start = stylesheet.indexOf(`@media ${query}`, searchFrom);
    if (start < 0) break;
    const bodyStart = stylesheet.indexOf("{", start) + 1;
    let depth = 1;
    let cursor = bodyStart;
    while (depth > 0 && cursor < stylesheet.length) {
      if (stylesheet[cursor] === "{") depth += 1;
      if (stylesheet[cursor] === "}") depth -= 1;
      cursor += 1;
    }
    blocks.push(stylesheet.slice(bodyStart, cursor - 1));
    searchFrom = cursor;
  }
  expect(blocks.length, `missing media block @media ${query}`).toBeGreaterThan(0);
  return blocks;
}

function lastMediaBlock(query: string) {
  const blocks = mediaBlocks(query);
  return blocks[blocks.length - 1];
}

function rule(selector: string) {
  const start = stylesheet.indexOf(selector + " {");
  expect(start, `missing rule ${selector}`).toBeGreaterThanOrEqual(0);
  const bodyStart = stylesheet.indexOf("{", start) + 1;
  const bodyEnd = stylesheet.indexOf("}", bodyStart);
  return stylesheet.slice(bodyStart, bodyEnd);
}

describe("V02-51B design tokens (audit §11)", () => {
  it(":root declares color, font, type-scale, space, z, radius, shadow and motion tokens", () => {
    const root = stylesheet.slice(stylesheet.indexOf(":root {"), stylesheet.indexOf("}", stylesheet.indexOf(":root {")));
    for (const token of [
      "--danger: #b23c25",
      "--muted: #66604f",
      "--success-bg: #edf3e9",
      "--warning-bg: #fff3df",
      "--danger-bg: #f8eae6",
      "--focus: var(--vermillion)",
      "--mono: ui-monospace",
      "--text-display: clamp(32px",
      "--text-title: 22px",
      "--text-body: 14px",
      "--text-ui: 13px",
      "--text-meta: 12px",
      "--text-micro: 11px",
      "--space-1: 4px",
      "--space-7: 48px",
      "--z-sticky: 20",
      "--z-dock: 22",
      "--z-dialog: 60",
      "--z-lightbox: 70",
      "--radius: 3px",
      "--shadow-card: 5px 5px 0 rgba(21,21,18,.055)",
      "--shadow-pop: -10px 0 28px rgba(21,21,18,.22)",
      "--ease-out: cubic-bezier(.2,.75,.2,1)",
      "--duration-fast: 120ms",
      "--duration-base: 180ms",
    ]) {
      expect(root).toContain(token);
    }
  });

  it("U10 未加载 Noto 时回退栈仍以系统字体收尾（sans-serif/serif/monospace）", () => {
    const root = stylesheet.slice(stylesheet.indexOf(":root {"), stylesheet.indexOf("}", stylesheet.indexOf(":root {")));
    expect(root).toContain('--sans: "Noto Sans SC", "Microsoft YaHei UI", sans-serif');
    expect(root).toContain('--serif: "Noto Serif SC", "Songti SC", "STSong", serif');
    expect(root).toMatch(/--mono: [^;]*monospace/);
  });

  it("stabilization floor 引用字号 token 而不是魔法数字（控件 ≥12px，U3）", () => {
    expect(stylesheet).toContain("body { font-size: var(--text-body); }");
    expect(stylesheet).toContain("button, input, select, textarea { min-height: 40px !important; font-size: var(--text-meta) !important; }");
    expect(stylesheet).toContain(".button, .button.compact, .generate-one { min-height: 44px !important; font-size: var(--text-body) !important; }");
  });
});

describe("U1 ≥1280 工作台：左导航停靠且底栏不遮挡主栏", () => {
  it("workspace canvas 保留 70px 底部内边距，queue dock 保持 48px 高", () => {
    expect(rule(".workspace-canvas")).toContain("padding: 28px 30px 70px");
    expect(rule(".queue-dock")).toContain("height: 48px");
    expect(rule(".queue-dock")).toContain("z-index: var(--z-dock)");
  });

  it("左导航可折叠成 48px 图标轨且持久化到 localStorage", () => {
    const railBlock = lastMediaBlock("(min-width: 761px)");
    expect(railBlock).toContain(".workspace-layout.rail-left { grid-template-columns: 48px minmax(0, 1fr); }");
    expect(railBlock).toContain(".workspace-left.rail .workspace-project-title");
    expect(projectWorkspaceSource).toContain('window.localStorage.getItem("mangaflow.project-sidebar-collapsed")');
    expect(projectWorkspaceSource).toContain('window.localStorage.setItem("mangaflow.project-sidebar-collapsed"');
  });
});

describe("U2 900–1279 工作台：不进 760 汉堡，检查器为抽屉而非消失", () => {
  it("汉堡抽屉只存在于 ≤760px 媒体块内", () => {
    const narrow = mediaBlocks("(max-width: 760px)")[0];
    expect(narrow).toContain(".workspace-left { position: fixed");
    for (const query of ["(min-width: 761px)", "(min-width: 1280px)", "(max-width: 1279.98px) and (min-width: 900px)"]) {
      for (const block of mediaBlocks(query)) {
        expect(block).not.toContain(".workspace-left { position: fixed");
      }
    }
  });

  it("1100–1279 右抽屉、900–1099 底部抽屉：检查器始终以抽屉形式存在（U2）", () => {
    const rightDrawer = mediaBlocks("(max-width: 1279.98px) and (min-width: 1100px)")[0];
    expect(rightDrawer).toContain(".panel-inspector.drawer-open { position: fixed");
    expect(rightDrawer).toContain(".panel-inspector:not(.drawer-open) { display: none; }");
    const bottomDrawer = mediaBlocks("(max-width: 1099.98px) and (min-width: 900px)")[0];
    expect(bottomDrawer).toContain(".panel-inspector.drawer-open { position: fixed");
    expect(bottomDrawer).toContain(".panel-inspector:not(.drawer-open) { display: none; }");
  });

  it("通用右槽在同一断点降级为抽屉/底部抽屉而不是消失", () => {
    const right = mediaBlocks("(max-width: 1279.98px) and (min-width: 900px)")[0];
    expect(right).toContain(".workspace-inspector.drawer-open { position: fixed");
    expect(right).toContain(".workspace-inspector:not(.drawer-open) { display: none; }");
    const below = mediaBlocks("(max-width: 899.98px)")[0];
    expect(below).toContain(".workspace-inspector.drawer-open { position: fixed");
    expect(below).toContain("bottom: 58px");
  });
});

describe("U3/U4 模板 C 设置壳", () => {
  it("≥1280 保持主栏+粘滞诊断侧栏双列", () => {
    expect(rule(".settings-board")).toContain("grid-template-columns: minmax(0, 1.65fr) minmax(280px, .75fr)");
    expect(rule(".settings-secondary")).toContain("position: sticky");
  });

  it("≤1279.98 设置板单列且诊断列不再粘死（U4）", () => {
    const stack = mediaBlocks("(max-width: 1279.98px)")[0];
    expect(stack).toContain(".settings-board { grid-template-columns: 1fr; }");
    expect(stack).toContain(".settings-secondary { position: static; max-height: none; overflow: visible; }");
  });
});

describe("U5 焦点环全局统一为 3px vermillion", () => {
  it("只存在一条全局 focus-visible outline 规则，组件不再改细焦点环", () => {
    const rings = stylesheet.match(/:focus-visible[^{]*\{[^}]*outline:[^}]*\}/g) ?? [];
    expect(rings).toEqual([":focus-visible { outline: 3px solid var(--vermillion); outline-offset: 3px; }"]);
    expect(stylesheet).not.toContain("outline: 1px solid var(--vermillion)");
    expect(stylesheet).not.toContain("outline: 2px solid");
  });

  it("usage 表格行不再用 outline: none 吞掉键盘焦点环", () => {
    expect(stylesheet).not.toMatch(/:focus-visible[^{]*\{[^}]*outline: none/);
  });
});

describe("U6 reduced-motion 合并为一条全局契约", () => {
  it("全表只保留一段 prefers-reduced-motion 块", () => {
    const blocks = stylesheet.match(/@media \(prefers-reduced-motion: reduce\)/g) ?? [];
    expect(blocks).toHaveLength(1);
  });

  it("动画冻结到 .01ms、spinner 可停，hover/按压/拖拽位移一并取消", () => {
    const block = mediaBlocks("(prefers-reduced-motion: reduce)")[0];
    expect(block).toContain("animation-duration: .01ms !important");
    expect(block).toContain("animation-iteration-count: 1 !important");
    expect(block).toContain("transition-duration: .01ms !important");
    expect(block).toContain("scroll-behavior: auto !important");
    expect(block).toContain("button:hover:not(:disabled)");
    expect(block).toContain("button:active:not(:disabled)");
    expect(block).toContain("a.button:hover");
    expect(block).toContain("a.button:active");
    expect(block).toContain(".upload-stage.drag-active");
    expect(block).toContain("transform: none !important");
    expect(stylesheet).not.toContain("rotate(-4deg)");
  });

  it("每一条带 translate 的交互态（hover/active/drag-active）规则都被取消（防再犯）", () => {
    const withoutComments = stylesheet.replace(/\/\*[\s\S]*?\*\//g, "");
    const rulePattern = /([^{}]+)\{([^{}]*)\}/g;
    const interactionTranslateSelectors = new Set<string>();
    for (const match of withoutComments.matchAll(rulePattern)) {
      const selectorGroup = match[1].slice(match[1].lastIndexOf("}") + 1);
      if (!/transform:[^;]*translate/.test(match[2])) continue;
      for (const part of selectorGroup.split(",")) {
        const selector = part.trim();
        if (selector.includes(":hover") || selector.includes(":active") || selector.includes("drag-active")) {
          interactionTranslateSelectors.add(selector);
        }
      }
    }
    expect(interactionTranslateSelectors.size).toBeGreaterThanOrEqual(26);
    const block = mediaBlocks("(prefers-reduced-motion: reduce)")[0];
    for (const selector of interactionTranslateSelectors) {
      expect(block, `reduced-motion 块缺少对交互态位移的取消：${selector}`).toContain(selector);
    }
  });
});
