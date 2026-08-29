"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";

import { api, type Job } from "@/lib/api";
import { activePollInterval, isActiveTaskStatus } from "@/lib/task-status";

import { queueStatsOf } from "./display";
import type { WorkspaceSection } from "./types";

function usePerJobMutation(mutationFn: (jobId: string) => Promise<Job>, onSuccess: () => void) {
  const inFlight = useRef(new Set<string>());
  const [pendingIds, setPendingIds] = useState<string[]>([]);
  const mutation = useMutation({
    mutationFn,
    onSuccess,
    onSettled: (_data, _error, jobId) => {
      inFlight.current.delete(jobId);
      setPendingIds((ids) => ids.filter((id) => id !== jobId));
    },
  });
  const request = (jobId: string) => {
    if (inFlight.current.has(jobId)) return;
    inFlight.current.add(jobId);
    setPendingIds((ids) => (ids.includes(jobId) ? ids : [...ids, jobId]));
    mutation.mutate(jobId);
  };
  const isPending = (jobId: string) => pendingIds.includes(jobId);
  return { mutation, request, isPending };
}

/**
 * Jobs domain: archive filter, selection and notices, the jobs query with its
 * active-task polling, and every job lifecycle mutation (cancel, retry,
 * archive, restore, bulk archive, delete).
 */
export function useJobsWorkspace({
  id,
  section,
}: {
  id: string;
  section: WorkspaceSection;
}) {
  const queryClient = useQueryClient();
  const [showArchivedJobs, setShowArchivedJobs] = useState(false);
  const [jobNotice, setJobNotice] = useState("");
  const [selectedJobIds, setSelectedJobIds] = useState<string[]>([]);

  const jobs = useQuery({
    queryKey: ["jobs", id, showArchivedJobs],
    queryFn: () => api.jobs(id, showArchivedJobs),
    enabled: ["assets", "jobs", "generate"].includes(section),
    refetchInterval: (query) => activePollInterval(query.state.data, 3000),
  });

  const invalidateJobs = () => queryClient.invalidateQueries({ queryKey: ["jobs", id] });
  const cancelAction = usePerJobMutation((jobId) => api.cancelJob(jobId), invalidateJobs);
  const retryAction = usePerJobMutation((jobId) => api.retryJob(jobId), invalidateJobs);
  const cancelJob = cancelAction.mutation;
  const retryJob = retryAction.mutation;
  const archiveJob = useMutation({
    mutationFn: (jobId: string) => api.archiveJob(jobId),
    onSuccess: () => {
      setJobNotice("任务已移入历史记录");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "归档失败"),
  });
  const restoreJob = useMutation({
    mutationFn: (jobId: string) => api.restoreJob(jobId),
    onSuccess: () => {
      setJobNotice("任务已恢复到近期记录");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "恢复失败"),
  });
  const archiveCompletedJobs = useMutation({
    mutationFn: () => api.archiveCompletedJobs(id),
    onSuccess: (result) => {
      setJobNotice(result.archived_count ? `已归档 ${result.archived_count} 条已结束任务` : "没有可归档的已结束任务");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "清空失败"),
  });
  const bulkArchiveJobs = useMutation({
    mutationFn: () => api.bulkArchiveJobs(id, selectedJobIds),
    onSuccess: (result) => {
      setJobNotice(`已批量归档 ${result.archived_count} 条任务`);
      setSelectedJobIds([]);
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "批量归档失败"),
  });
  const deleteJob = useMutation({
    mutationFn: (jobId: string) => api.deleteJob(jobId),
    onSuccess: () => {
      setJobNotice("无引用任务已彻底删除");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "删除失败"),
  });

  const queueStats = useMemo(() => queueStatsOf(jobs.data ?? []), [jobs.data]);
  const activeJobs = (jobs.data ?? []).filter((job) => isActiveTaskStatus(job.status));
  const failedJobs = (jobs.data ?? []).filter((job) => job.status === "FAILED");
  const completedJobGroups = Object.entries(
    (jobs.data ?? []).filter((job) => !isActiveTaskStatus(job.status) && job.status !== "FAILED").reduce<Record<string, Job[]>>((groups, job) => {
      const date = new Date(job.created_at).toLocaleDateString("zh-CN");
      groups[date] = [...(groups[date] ?? []), job];
      return groups;
    }, {}),
  );

  return {
    jobs,
    showArchivedJobs,
    setShowArchivedJobs,
    jobNotice,
    setJobNotice,
    selectedJobIds,
    setSelectedJobIds,
    cancelJob,
    retryJob,
    requestCancel: cancelAction.request,
    requestRetry: retryAction.request,
    isCancelPending: cancelAction.isPending,
    isRetryPending: retryAction.isPending,
    archiveJob,
    restoreJob,
    archiveCompletedJobs,
    bulkArchiveJobs,
    deleteJob,
    queueStats,
    activeJobs,
    failedJobs,
    completedJobGroups,
  };
}

export type JobsWorkspace = ReturnType<typeof useJobsWorkspace>;
