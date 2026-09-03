import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const stylesheet = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");

function rule(selector: string) {
  const start = stylesheet.indexOf(selector + " {");
  expect(start).toBeGreaterThanOrEqual(0);
  const bodyStart = stylesheet.indexOf("{", start) + 1;
  const bodyEnd = stylesheet.indexOf("}", bodyStart);
  return stylesheet.slice(bodyStart, bodyEnd);
}

function luminance(hex: string) {
  const channels = [0, 2, 4].map(
    (offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255,
  );
  const linear = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(first: string, second: string) {
  const [lighter, darker] = [luminance(first), luminance(second)].sort(
    (left, right) => right - left,
  );
  return (lighter + 0.05) / (darker + 0.05);
}

describe("project settings switch styles", () => {
  it("keeps closed, enabled, and keyboard-focus states distinguishable", () => {
    const track = rule(".switch-setting i");
    const closedThumb = rule(".switch-setting i::after");
    const enabledThumb = rule(".switch-setting.on i::after");

    expect(track).toContain("background: #ddd7cb");
    expect(closedThumb).toContain("background: #66604f");
    expect(contrastRatio("66604f", "ddd7cb")).toBeGreaterThanOrEqual(3);
    expect(enabledThumb).toContain("background: var(--green)");
    expect(enabledThumb).toContain("transform: translateX(16px)");
    expect(stylesheet).toContain(
      ":focus-visible { outline: 3px solid var(--vermillion); outline-offset: 3px; }",
    );
  });
});

describe("director workspace styles (V02-41B)", () => {
  it("D15 预览卡在 900px 以下升级为 modal（fixed 定位覆盖）", () => {
    const inlinePreview = rule(".director-preview");
    expect(inlinePreview).not.toContain("position: fixed");
    const mediaBlocks = stylesheet.split(/@media\s*/).slice(1);
    const modalBlock = mediaBlocks.find((block) => block.startsWith("(max-width: 900px)")
      && /\.director-preview\s*\{[^}]*position:\s*fixed/.test(block));
    expect(modalBlock).toBeTruthy();
  });

  it("导演模式开关与作用域芯片保持对比契约：active 使用 vermillion 描边", () => {
    const activeChip = rule(".director-chip.active");
    expect(activeChip).toContain("border-color: var(--vermillion)");
    const switchActive = rule(".director-mode-switch button.active");
    expect(switchActive).toContain("background: var(--ink)");
  });
});
