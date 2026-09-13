import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type SourceRevision } from "@/lib/api";

import { useSourceWorkspace } from "./use-source-workspace";

const importApi = vi.spyOn(api, "importSource");
const reviseApi = vi.spyOn(api, "reviseSource");
const uploadApi = vi.spyOn(api, "uploadSource");
const revisionsApi = vi.spyOn(api, "revisions");

function Probe({
  onReady,
  onSelected,
}: {
  onReady: (hook: ReturnType<typeof useSourceWorkspace>) => void;
  onSelected?: (chapterId: string | null) => void;
}) {
  const hook = useSourceWorkspace({
    id: "project-1",
    projectPath: (target) => `/projects/project-1/${target}`,
    router: { push: vi.fn() } as never,
    activeChapterId: null,
    setSelectedChapterId: onSelected ?? (() => undefined),
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

function renderProbe(onSelected?: (chapterId: string | null) => void) {
  let hook!: ReturnType<typeof useSourceWorkspace>;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Probe onReady={(value) => { hook = value; }} onSelected={onSelected} />
    </QueryClientProvider>,
  );
  return () => hook;
}

describe("useSourceWorkspace 关键行为", () => {
  beforeEach(() => {
    importApi.mockReset().mockResolvedValue({ chapters: [{ id: "chapter-1" }], total_characters: 100 } as never);
    reviseApi.mockReset().mockResolvedValue(undefined as never);
    uploadApi.mockReset().mockResolvedValue({ chapters: [{ id: "chapter-uploaded" }], total_characters: 100 } as never);
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

  it("迟到的保存成功不得清掉用户已切去编辑的另一章文本", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    let resolveRevise!: (value: SourceRevision) => void;
    reviseApi.mockReset().mockImplementation(() => new Promise<SourceRevision>((resolve) => {
      resolveRevise = resolve;
    }));
    const getHook = renderProbe();
    await act(async () => {
      getHook().setEditingChapterId("chapter-a");
      getHook().setSourceText("章节 A 的修订文本");
    });
    await act(async () => {
      getHook().importSource.mutate();
    });
    expect(reviseApi).toHaveBeenCalledWith("chapter-a", "第一章", "章节 A 的修订文本");
    // 保存 A 仍在途：用户此时切到章节 B 的修订编辑。
    await act(async () => {
      await getHook().beginEditChapter("chapter-b", "第一章");
    });
    expect(getHook().editingChapterId).toBe("chapter-b");
    expect(getHook().sourceText).toBe("旧章节内容");
    // A 的保存此刻才成功：表单已归属 B，不得被清空或改写。
    await act(async () => {
      resolveRevise({} as SourceRevision);
    });
    await waitFor(() => expect(getHook().importSource.isSuccess).toBe(true));
    expect(getHook().editingChapterId).toBe("chapter-b");
    expect(getHook().sourceText).toBe("旧章节内容");
    expect(getHook().importNotice).toBe("");
    confirmSpy.mockRestore();
  });

  it("文件上传在途切去修订编辑后，迟到的成功不得清空表单或切走选中章节", async () => {
    let resolveUpload!: (value: { chapters: { id: string }[]; total_characters: number }) => void;
    uploadApi.mockReset().mockImplementation(() => new Promise<{ chapters: { id: string }[]; total_characters: number }>((resolve) => {
      resolveUpload = resolve;
    }));
    const selected: (string | null)[] = [];
    const getHook = renderProbe((chapterId) => selected.push(chapterId));
    await act(async () => {
      getHook().importSourceFile.mutate(new File(["第一章内容"], "chapter-1.txt", { type: "text/plain" }));
    });
    expect(uploadApi).toHaveBeenCalledTimes(1);
    // 上传仍在途：用户点“修改原文”切到章节 B 的修订编辑。
    await act(async () => {
      await getHook().beginEditChapter("chapter-b", "第一章");
    });
    expect(getHook().editingChapterId).toBe("chapter-b");
    expect(getHook().sourceText).toBe("旧章节内容");
    // 上传此刻才成功：表单已归属 B，不得被清空、改写或切走选中。
    await act(async () => {
      resolveUpload({ chapters: [{ id: "chapter-uploaded" }], total_characters: 100 });
    });
    await waitFor(() => expect(getHook().importSourceFile.isSuccess).toBe(true));
    expect(getHook().editingChapterId).toBe("chapter-b");
    expect(getHook().sourceText).toBe("旧章节内容");
    expect(getHook().importNotice).toBe("");
    // beginEditChapter 自己选中了 chapter-b；上传成功不得再把选中切到上传章。
    expect(selected).toEqual(["chapter-b"]);
  });

  it("文件上传在途敲入新文本（未进编辑态）后，迟到的成功不得清空输入框", async () => {
    let resolveUpload!: (value: { chapters: { id: string }[]; total_characters: number }) => void;
    uploadApi.mockReset().mockImplementation(() => new Promise<{ chapters: { id: string }[]; total_characters: number }>((resolve) => {
      resolveUpload = resolve;
    }));
    const getHook = renderProbe();
    await act(async () => {
      getHook().importSourceFile.mutate(new File(["第一章内容"], "chapter-1.txt", { type: "text/plain" }));
    });
    // 上传在途：用户在空输入框敲入新章文本（尚未进入修订编辑态）。
    await act(async () => {
      getHook().setSourceText("在途敲入的全新章节");
    });
    await act(async () => {
      resolveUpload({ chapters: [{ id: "chapter-uploaded" }], total_characters: 100 });
    });
    await waitFor(() => expect(getHook().importSourceFile.isSuccess).toBe(true));
    expect(getHook().sourceText).toBe("在途敲入的全新章节");
    expect(getHook().editingChapterId).toBeNull();
  });

  it("文件上传成功且无在途变化时，仍清空输入框并切换到上传章", async () => {
    const selected: (string | null)[] = [];
    const getHook = renderProbe((chapterId) => selected.push(chapterId));
    await act(async () => {
      getHook().importSourceFile.mutate(new File(["第一章内容"], "chapter-1.txt", { type: "text/plain" }));
    });
    await waitFor(() => expect(getHook().importSourceFile.isSuccess).toBe(true));
    expect(getHook().sourceText).toBe("");
    expect(getHook().editingChapterId).toBeNull();
    expect(getHook().importNotice).toContain("已导入 100 字");
    expect(selected).toEqual(["chapter-uploaded"]);
    expect(screen.getByRole("status")).toBeTruthy();
  });
});
