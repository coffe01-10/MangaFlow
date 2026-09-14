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

    // A large graph: the JSON body must exceed 60,000 characters.
    const bigGraph = { nodes: [{ id: "n", prompt: "x".repeat(70_000) }], edges: [] } as never;
    await api.updateWorkflow("wf1", 1, { draft_graph: bigGraph });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.body as string).length).toBeGreaterThan(60_000);
    expect(init.keepalive).toBeUndefined();
  });
});
