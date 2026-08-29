"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, type Job } from "@/lib/api";
import { activePollInterval, isActiveTaskStatus } from "@/lib/task-status";

import { queueStatsOf } from "./display";
import type { WorkspaceSection } from "./types";

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

  const cancelJob = useMutation({
    mutationFn: (jobId: string) => api.cancelJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs", id] }),
  });
  const retryJob = useMutation({
    mutationFn: (jobId: string) => api.retryJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs", id] }),
  });
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
