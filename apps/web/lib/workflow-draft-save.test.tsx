import { act, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createWorkflowDraftSaver,
  type WorkflowDraftSaver,
  type WorkflowDraftSnapshot,
  type WorkflowSaveStatus,
} from "./workflow-draft-save";

type Graph = { label: string };
type Saved = { id: string; version: number; graph: Graph };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function createHarness() {
  const persisted: WorkflowDraftSnapshot<Graph>[] = [];
  let graph: Graph = { label: "A" };
  let version = 1;
  const statuses: WorkflowSaveStatus[] = [];
  let persistImpl: (snapshot: WorkflowDraftSnapshot<Graph>) => Promise<Saved> = async (snapshot) => {
    persisted.push(snapshot);
    version += 1;
    return { id: snapshot.workflowId, version, graph: snapshot.graph };
  };

  const saver = createWorkflowDraftSaver<Graph, Saved>({
    debounceMs: 800,
    getSnapshot: () => ({ workflowId: "wf-1", version, graph }),
    persist: (snapshot) => persistImpl(snapshot),
    onStatusChange: (status) => {
      statuses.push(status);
    },
  });

  return {
    saver,
    statuses,
    persisted,
    edit(label: string) {
      graph = { label };
      saver.markDirty();
    },
    setPersist(next: typeof persistImpl) {
      persistImpl = next;
    },
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("工作流草稿保存队列", () => {
  it("防抖期间继续编辑只持久化最新图", async () => {
    vi.useFakeTimers();
    const harness = createHarness();
    harness.edit("A");
    harness.saver.schedule();
    await vi.advanceTimersByTimeAsync(400);
    harness.edit("B");
    harness.saver.schedule();
    await vi.advanceTimersByTimeAsync(800);
    await Promise.resolve();

    expect(harness.persisted).toEqual([
      { workflowId: "wf-1", version: 1, graph: { label: "B" } },
    ]);
    expect(harness.statuses.at(-1)).toBe("已保存");
    expect(harness.saver.isDirty()).toBe(false);
  });

  it("慢保存期间二次编辑会补交，成功前不显示已保存", async () => {
    vi.useFakeTimers();
    const first = deferred<Saved>();
    const second = deferred<Saved>();
    const persistCalls: Graph[] = [];
    const harness = createHarness();
    let call = 0;
    harness.setPersist((snapshot) => {
      persistCalls.push(snapshot.graph);
      call += 1;
      return call === 1 ? first.promise : second.promise;
    });

    harness.edit("A");
    const firstSave = harness.saver.saveNow();
    await Promise.resolve();
    expect(persistCalls).toEqual([{ label: "A" }]);
    expect(harness.statuses.at(-1)).toBe("保存中");

    harness.edit("B");
    expect(harness.saver.isDirty()).toBe(true);

    first.resolve({ id: "wf-1", version: 2, graph: { label: "A" } });
    await Promise.resolve();
    await Promise.resolve();
    expect(persistCalls).toEqual([{ label: "A" }, { label: "B" }]);
    expect(harness.statuses).not.toContain("已保存");
    expect(harness.statuses.at(-1)).toBe("保存中");

    second.resolve({ id: "wf-1", version: 3, graph: { label: "B" } });
    await expect(firstSave).resolves.toBe(true);
    expect(harness.statuses.at(-1)).toBe("已保存");
    expect(harness.saver.isDirty()).toBe(false);
  });

  it("发布等待在途保存和补交完成后再继续", async () => {
    const first = deferred<Saved>();
    const second = deferred<Saved>();
    const harness = createHarness();
    const graphs: Graph[] = [];
    let call = 0;
    harness.setPersist((snapshot) => {
      graphs.push(snapshot.graph);
      call += 1;
      return call === 1 ? first.promise : second.promise;
    });

    harness.edit("A");
    void harness.saver.saveNow();
    await Promise.resolve();
    harness.edit("B");

    let published = false;
    const publish = harness.saver.saveNow().then((ok) => {
      if (!ok) throw new Error("保存失败，未发布");
      published = true;
      return graphs.at(-1);
    });

    expect(published).toBe(false);
    first.resolve({ id: "wf-1", version: 2, graph: { label: "A" } });
    await Promise.resolve();
    await Promise.resolve();
    expect(published).toBe(false);

    second.resolve({ id: "wf-1", version: 3, graph: { label: "B" } });
    await expect(publish).resolves.toEqual({ label: "B" });
    expect(published).toBe(true);
  });

  it("保存失败时不清除 dirty，发布不得继续", async () => {
    const harness = createHarness();
    harness.setPersist(async () => {
      throw new Error("网络中断");
    });
    harness.edit("A");
    const ok = await harness.saver.saveNow();
    expect(ok).toBe(false);
    expect(harness.saver.isDirty()).toBe(true);
    expect(harness.statuses.at(-1)).toBe("保存失败");

    let published = false;
    const saved = await harness.saver.saveNow();
    if (saved) published = true;
    expect(saved).toBe(false);
    expect(published).toBe(false);
  });
});

function SaveStatusPanel({
  persist,
}: {
  persist: (snapshot: WorkflowDraftSnapshot<Graph>) => Promise<Saved>;
}) {
  const graphRef = useRef<Graph>({ label: "empty" });
  const versionRef = useRef(1);
  const saverRef = useRef<WorkflowDraftSaver | null>(null);
  const [status, setStatus] = useState<WorkflowSaveStatus>("已保存");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const saver = createWorkflowDraftSaver<Graph, Saved>({
      debounceMs: 800,
      getSnapshot: () => ({
        workflowId: "wf-ui",
        version: versionRef.current,
        graph: graphRef.current,
      }),
      persist,
      onStatusChange: setStatus,
      onPersisted: (result) => {
        versionRef.current = result.version;
        setNotice(`已持久化:${result.graph.label}`);
      },
      onError: (error) => {
        setNotice(error instanceof Error ? error.message : "保存失败");
      },
    });
    saverRef.current = saver;
    return () => {
      saver.dispose();
      if (saverRef.current === saver) saverRef.current = null;
    };
  }, [persist]);

  return (
    <div>
      <div data-testid="save-status">{status}</div>
      <div data-testid="notice">{notice}</div>
      <button
        type="button"
        onClick={() => {
          graphRef.current = { label: "first" };
          saverRef.current?.markDirty();
          void saverRef.current?.saveNow();
        }}
      >
        编辑并保存 A
      </button>
      <button
        type="button"
        onClick={() => {
          graphRef.current = { label: "latest" };
          saverRef.current?.markDirty();
        }}
      >
        编辑 B
      </button>
      <button
        type="button"
        onClick={() => {
          void saverRef.current?.saveNow().then((ok) => {
            setNotice(ok ? `发布:${graphRef.current.label}` : "保存失败，未发布");
          });
        }}
      >
        发布
      </button>
    </div>
  );
}

describe("草稿保存状态提示", () => {
  it("已保存只在最新图持久化后出现，发布使用同一份内容", async () => {
    const first = deferred<Saved>();
    const second = deferred<Saved>();
    let call = 0;
    const persist = vi.fn((savedSnapshot: WorkflowDraftSnapshot<Graph>) => {
      call += 1;
      return call === 1 ? first.promise : second.promise.then(() => ({
        id: savedSnapshot.workflowId,
        version: 3,
        graph: savedSnapshot.graph,
      }));
    });

    render(<SaveStatusPanel persist={persist} />);
    await act(async () => {
      screen.getByText("编辑并保存 A").click();
    });
    expect(screen.getByTestId("save-status")).toHaveTextContent("保存中");

    await act(async () => {
      screen.getByText("编辑 B").click();
    });
    expect(screen.getByTestId("save-status")).toHaveTextContent("待保存");

    await act(async () => {
      screen.getByText("发布").click();
    });

    await act(async () => {
      first.resolve({ id: "wf-ui", version: 2, graph: { label: "first" } });
      await first.promise.catch(() => undefined);
    });
    await waitFor(() => expect(persist).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("save-status")).toHaveTextContent("保存中");
    expect(screen.getByTestId("notice")).not.toHaveTextContent("发布:");

    await act(async () => {
      second.resolve({ id: "wf-ui", version: 3, graph: { label: "latest" } });
      await second.promise.catch(() => undefined);
    });
    await waitFor(() => {
      expect(screen.getByTestId("save-status")).toHaveTextContent("已保存");
      expect(screen.getByTestId("notice")).toHaveTextContent("发布:latest");
    });
    expect(persist.mock.calls[1][0].graph).toEqual({ label: "latest" });
  });
});
