import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

// The workflow editor's beforeunload/visibilitychange flush fires the draft
// save exactly as the page tears down: without keepalive the fetch is not
// guaranteed to survive, and the last debounced edits silently drop. With a
// large graph the body can exceed the 64 KiB keepalive cap, where the fetch
// would reject outright — so keepalive must be conditional on payload size.
describe("updateWorkflow keepalive", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const okResponse = () => ({
    ok: true,
    status: 200,
    json: async () => ({ id: "wf1", version: 2 }),
  });

  it("sends a draft-sized payload with keepalive so the unload flush survives", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);

    await api.updateWorkflow("wf1", 1, { draft_graph: { nodes: [], edges: [] } as never });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.keepalive).toBe(true);
    expect(init.method).toBe("PATCH");
  });

  it("drops keepalive when the serialized body exceeds the 64 KiB cap", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);

    // A large graph: the UTF-8 body must exceed 60,000 bytes.
    const bigGraph = { nodes: [{ id: "n", prompt: "x".repeat(70_000) }], edges: [] } as never;
    await api.updateWorkflow("wf1", 1, { draft_graph: bigGraph });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.body as string).length).toBeGreaterThan(60_000);
    expect(init.keepalive).toBeUndefined();
  });

  it("measures wire bytes, not UTF-16 code units: a CJK-heavy body under 60k chars can still exceed the cap", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);

    // 30,000 汉字 = 30,000 UTF-16 code units (passes a naive length gate)
    // but 90,000 UTF-8 bytes — over the 64 KiB keepalive cap. The #750
    // regression: draft prompts are Chinese-first, so this shape is the
    // ordinary autosave, and a length-gated keepalive hard-failed it.
    const cjkGraph = { nodes: [{ id: "n", prompt: "汉".repeat(30_000) }], edges: [] } as never;
    await api.updateWorkflow("wf1", 1, { draft_graph: cjkGraph });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.body as string).length).toBeLessThanOrEqual(60_000);
    expect(new TextEncoder().encode(init.body as string).byteLength).toBeGreaterThan(60_000);
    expect(init.keepalive).toBeUndefined();
  });

  it("keeps keepalive for a CJK body whose wire bytes genuinely fit", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);

    const cjkGraph = { nodes: [{ id: "n", prompt: "汉".repeat(5_000) }], edges: [] } as never;
    await api.updateWorkflow("wf1", 1, { draft_graph: cjkGraph });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new TextEncoder().encode(init.body as string).byteLength).toBeLessThanOrEqual(60_000);
    expect(init.keepalive).toBe(true);
  });
});
