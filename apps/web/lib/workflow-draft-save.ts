export type WorkflowSaveStatus = "已保存" | "待保存" | "保存中" | "保存失败";

export interface WorkflowDraftSnapshot<TGraph> {
  workflowId: string;
  version: number;
  graph: TGraph;
}

export interface WorkflowDraftPersistResult {
  id: string;
  version: number;
}

export interface WorkflowDraftSaver {
  markDirty: () => void;
  schedule: () => void;
  saveNow: () => Promise<boolean>;
  reset: () => void;
  isDirty: () => boolean;
  dispose: () => void;
}

interface CreateWorkflowDraftSaverOptions<TGraph, TResult extends WorkflowDraftPersistResult> {
  getSnapshot: () => WorkflowDraftSnapshot<TGraph> | null;
  persist: (snapshot: WorkflowDraftSnapshot<TGraph>) => Promise<TResult>;
  onStatusChange: (status: WorkflowSaveStatus) => void;
  onPersisted?: (result: TResult, snapshot: WorkflowDraftSnapshot<TGraph>) => void;
  onError?: (error: unknown) => void;
  debounceMs?: number;
}

/**
 * Serializes workflow draft saves and resubmits edits that happen while a
 * request is in flight. Dirty is cleared only when the latest snapshot has
 * actually been persisted.
 */
export function createWorkflowDraftSaver<TGraph, TResult extends WorkflowDraftPersistResult>(
  options: CreateWorkflowDraftSaverOptions<TGraph, TResult>,
): WorkflowDraftSaver {
  const debounceMs = options.debounceMs ?? 800;
  let generation = 0;
  let savedGeneration = 0;
  let epoch = 0;
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let inflight: Promise<boolean> | null = null;

  function clearTimer() {
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
  }

  function isCurrent(runEpoch: number) {
    return !disposed && epoch === runEpoch;
  }

  async function drain(runEpoch: number): Promise<boolean> {
    while (isCurrent(runEpoch) && generation !== savedGeneration) {
      const target = generation;
      const snapshot = options.getSnapshot();
      if (!snapshot) {
        savedGeneration = target;
        options.onStatusChange("已保存");
        continue;
      }
      options.onStatusChange("保存中");
      try {
        const result = await options.persist(snapshot);
        if (!isCurrent(runEpoch)) return false;
        options.onPersisted?.(result, snapshot);
        if (generation === target) savedGeneration = target;
      } catch (error) {
        if (!isCurrent(runEpoch)) return false;
        options.onStatusChange("保存失败");
        options.onError?.(error);
        return false;
      }
    }
    if (!isCurrent(runEpoch)) return false;
    if (generation === savedGeneration) {
      options.onStatusChange("已保存");
      return true;
    }
    return drain(runEpoch);
  }

  function saveNow(): Promise<boolean> {
    if (disposed) return Promise.resolve(false);
    clearTimer();
    const runEpoch = epoch;
    if (inflight) {
      const queued = inflight.then((ok) => {
        if (!ok || !isCurrent(runEpoch)) return false;
        if (generation === savedGeneration) return true;
        return drain(runEpoch);
      });
      const tracked = queued.finally(() => {
        if (inflight === tracked) inflight = null;
      });
      inflight = tracked;
      return tracked;
    }
    const run = drain(runEpoch);
    const tracked = run.finally(() => {
      if (inflight === tracked) inflight = null;
    });
    inflight = tracked;
    return tracked;
  }

  return {
    markDirty() {
      if (disposed) return;
      generation += 1;
      options.onStatusChange("待保存");
    },
    schedule() {
      if (disposed || generation === savedGeneration) return;
      clearTimer();
      timer = setTimeout(() => {
        timer = null;
        void saveNow();
      }, debounceMs);
    },
    saveNow,
    reset() {
      clearTimer();
      epoch += 1;
      generation = 0;
      savedGeneration = 0;
      inflight = null;
      if (!disposed) options.onStatusChange("已保存");
    },
    isDirty() {
      return generation !== savedGeneration;
    },
    dispose() {
      disposed = true;
      clearTimer();
      inflight = null;
    },
  };
}
