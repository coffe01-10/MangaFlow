"use client";

import { Activity, ChevronUp, Minus } from "lucide-react";
import type { CSSProperties, Dispatch, SetStateAction } from "react";

import styles from "../workflow-editor.module.css";
import type { FlowNode } from "./types";

export function RunMonitor({
  nodes,
  isRunning,
  logOpen,
  setLogOpen,
}: {
  nodes: FlowNode[];
  isRunning: boolean;
  logOpen: boolean;
  setLogOpen: Dispatch<SetStateAction<boolean>>;
}) {
  const completedCount = nodes.filter((node) => node.status === "done").length;
  const runningNode = nodes.find((node) => node.status === "running");

  return (
    <section className={logOpen ? styles.runLog : `${styles.runLog} ${styles.runLogCollapsed}`}>
      <header>
        <div><Activity size={14} /><strong>运行监视器</strong><span>{isRunning ? "LIVE" : "IDLE"}</span></div>
        <button aria-expanded={logOpen} onClick={() => setLogOpen((current) => !current)} title={logOpen ? "收起运行监视器" : "展开运行监视器"}>
          {logOpen ? <Minus size={14} /> : <ChevronUp size={14} />}
        </button>
      </header>
      {logOpen && <div className={styles.logBody}>
        <div className={styles.progressRing} style={{ "--progress": `${Math.round(completedCount / Math.max(nodes.length, 1) * 100) * 3.6}deg` } as CSSProperties}><span>{completedCount}<small>/{nodes.length}</small></span></div>
        <div className={styles.logCopy}>
          <span>{isRunning ? "正在执行" : completedCount === nodes.length ? "流程已完成" : "等待运行"}</span>
          <strong>{runningNode?.title ?? (completedCount === nodes.length ? "全部节点通过" : "从原作章节开始")}</strong>
          <small>{isRunning ? "输出将自动传递至下一个节点" : "运行只演示节点状态，不会消耗模型额度"}</small>
        </div>
        <div className={styles.logStats}><span><i className={styles.green} />成功 {completedCount}</span><span><i className={styles.amber} />警告 {nodes.filter((node) => node.status === "warning").length}</span><span><i />等待 {nodes.filter((node) => ["idle", "ready"].includes(node.status)).length}</span></div>
      </div>}
    </section>
  );
}
