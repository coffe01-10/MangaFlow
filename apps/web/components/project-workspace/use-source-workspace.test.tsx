import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { useSourceWorkspace } from "./use-source-workspace";

const importApi = vi.spyOn(api, "importSource");
const reviseApi = vi.spyOn(api, "reviseSource");
const revisionsApi = vi.spyOn(api, "revisions");

function Probe({ onReady }: { onReady: (hook: ReturnType<typeof useSourceWorkspace>) => void }) {
  const hook = useSourceWorkspace({
    id: "project-1",
    projectPath: (target) => `/projects/project-1/${target}`,
    router: { push: vi.fn() } as never,
    activeChapterId: null,
    setSelectedChapterId: () => undefined,
    setSelectedPageId: () => undefined,
  });
  onReady(hook);
  return (
    <div>
      <textarea aria-label="mirror" value={hook.sourceText} readOnly />
      {hook.importNotice && <p role="status">{hook.importNotice}</p>}
    </div>
  );
}

function renderProbe() {
  let hook!: ReturnType<typeof useSourceWorkspace>;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Probe onReady={(value) => { hook = value; }} />
    </QueryClientProvider>,
  );
  return () => hook;
}

describe("useSourceWorkspace 关键行为", () => {
  beforeEach(() => {
    importApi.mockReset().mockResolvedValue({ chapters: [{ id: "chapter-1" }], total_characters: 100 } as never);
    reviseApi.mockReset().mockResolvedValue(undefined as never);
    revisionsApi.mockReset().mockResolvedValue([{ original_text: "旧章节内容" }] as never);
  });

  it("导入成功后给出下一步指引，而不是静默清空输入框", async () => {
    const getHook = renderProbe();
    await act(async () => {
      getHook().setSourceText("全新的第一章内容");
    });
    await act(async () => {
      getHook().importSource.mutate();
    });
    await waitFor(() => {
      expect(getHook().importNotice).toContain("生成漫画剧本");
    });
    expect(getHook().sourceText).toBe("");
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("输入框有未导入原文时，加载章节修订必须先确认；取消则不覆盖", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const getHook = renderProbe();
    await act(async () => {
      getHook().setSourceText("用户刚粘贴的整章文本");
    });
    await act(async () => {
      await getHook().beginEditChapter("chapter-1", "第一章");
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(revisionsApi).not.toHaveBeenCalled();
    expect(getHook().sourceText).toBe("用户刚粘贴的整章文本");
    confirmSpy.mockRestore();
  });

  it("取消修改前确认；拒绝时保留全部文本", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const getHook = renderProbe();
    await act(async () => {
      getHook().setSourceText("绝不悄悄丢掉的文本");
    });
    await act(async () => {
      getHook().cancelEditChapter();
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(getHook().sourceText).toBe("绝不悄悄丢掉的文本");
    confirmSpy.mockRestore();
  });
});
