"use client";

import { Activity, ChevronDown, Download, Link2, Play, Redo2, Save, Undo2, Workflow } from "lucide-react";

import type { Project } from "@/lib/api";
import styles from "../workflow-editor.module.css";

export function EditorTopbar({
  projects,
  projectsLoading,
  resolvedProjectId,
  chooseProject,
  projectDataError,
  projectDataLoading,
  chapterCount,
  assetCount,
  saved,
  isRunning,
  exportFlow,
  saveFlow,
  runWorkflow,
}: {
  projects: Project[];
  projectsLoading: boolean;
  resolvedProjectId: string;
  chooseProject: (projectId: string) => void;
  projectDataError: boolean;
  projectDataLoading: boolean;
  chapterCount: number;
  assetCount: number;
  saved: boolean;
  isRunning: boolean;
  exportFlow: () => void;
  saveFlow: () => void;
  runWorkflow: () => void;
}) {
  return (
    <header className={styles.topbar}>
      <div className={styles.breadcrumb}>
        <span className={styles.workspaceMark}><Workflow size={15} /> FLOW / 01</span>
        <i />
        <div>
          <label className={styles.projectPicker} title="选择这张画布绑定的项目">
            <Link2 size={12} />
            <select aria-label="当前工作流项目" value={resolvedProjectId} onChange={(event) => chooseProject(event.target.value)} disabled={!projects.length}>
              <option value="" disabled>{projectsLoading ? "正在读取项目…" : "选择项目"}</option>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
            <ChevronDown size={12} />
          </label>
          <span>{projectDataError ? "项目服务未连接" : projectDataLoading ? "正在同步项目上下文…" : `${chapterCount} 章 · ${assetCount} 项资产`}</span>
        </div>
      </div>
      <div className={styles.topActions}>
        <div className={saved ? styles.saveState : `${styles.saveState} ${styles.unsaved}`}><span />{saved ? "所有更改已保存" : "有未保存更改"}</div>
        <button className={styles.iconButton} title="撤销（即将支持）" disabled><Undo2 size={16} /></button>
        <button className={styles.iconButton} title="重做（即将支持）" disabled><Redo2 size={16} /></button>
        <button className={styles.secondaryButton} onClick={exportFlow}><Download size={15} />导出</button>
        <button className={styles.secondaryButton} onClick={saveFlow}><Save size={15} />保存</button>
        <button className={styles.runButton} onClick={runWorkflow} disabled={isRunning}>
          {isRunning ? <Activity className={styles.pulseIcon} size={15} /> : <Play size={15} fill="currentColor" />}
          {isRunning ? "运行中" : "运行工作流"}
        </button>
      </div>
    </header>
  );
}
