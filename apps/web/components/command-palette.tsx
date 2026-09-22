"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { WorkflowDialog } from "./workflow-dialog";
import styles from "./workflow-studio.module.css";

export type StudioCommand = { id: string; label: string; section: string; run: () => void; disabled?: boolean };
export const COLLECT_COMMANDS = "mangaflow:collect-commands";
export const OPEN_COMMANDS = "mangaflow:open-commands";

export function CommandPalette() {
  const router = useRouter();
  const [commands, setCommands] = useState<StudioCommand[] | null>(null);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    let generation = 0;
    const open = () => {
      const request = ++generation;
      const entries: StudioCommand[] = [
        { id: "home", label: "项目首页", section: "页面", run: () => router.push("/") },
        { id: "settings", label: "设置 · 模型与供应商", section: "页面", run: () => router.push("/settings") },
        { id: "help", label: "帮助与快捷键", section: "页面", run: () => router.push("/help") },
      ];
      const projectId = window.location.pathname.match(/\/projects\/([^/]+)/)?.[1]
        ?? new URLSearchParams(window.location.search).get("projectId");
      if (projectId) {
        for (const [path, label] of [["source", "原作"], ["storyboard", "分镜"], ["generate", "生成与修复"], ["workflow", "流程编排"]]) {
          entries.push({ id: path, label, section: "项目页面", run: () => router.push(`/projects/${projectId}/${path}`) });
        }
      }
      window.dispatchEvent(new CustomEvent(COLLECT_COMMANDS, { detail: entries }));
      setCommands(entries); setQuery(""); setIndex(0); setError("");
      if (projectId && !entries.some((entry) => entry.section === "工作流")) {
        void api.workflows(projectId).then((workflows) => {
          if (request !== generation) return;
          setCommands((current) => current && [...current, ...workflows.map((workflow) => ({
            id: workflow.id, label: workflow.name, section: "工作流",
            run: () => router.push(`/projects/${projectId}/workflow?workflow=${workflow.id}`),
          }))]);
        }).catch(() => { if (request === generation) setError("工作流搜索暂不可用，请重新打开重试。"); });
      }
    };
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); open(); }
    };
    window.addEventListener("keydown", key);
    window.addEventListener(OPEN_COMMANDS, open);
    return () => { generation++; window.removeEventListener("keydown", key); window.removeEventListener(OPEN_COMMANDS, open); };
  }, [router]);
  const results = (commands ?? []).filter((command) => `${command.label} ${command.section}`.toLowerCase().includes(query.toLowerCase()));
  const choose = (command: StudioCommand) => { if (!command.disabled) { setCommands(null); command.run(); } };
  return commands && <WorkflowDialog title="命令中心" onClose={() => setCommands(null)}>
    <input autoFocus className={styles.searchInput} aria-label="搜索页面、节点、工作流和操作" placeholder="搜索页面、节点、工作流，或输入操作…" value={query}
      onChange={(event) => { setQuery(event.target.value); setIndex(0); }}
      onKeyDown={(event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setIndex((value) => (value + (event.key === "ArrowDown" ? 1 : -1) + results.length) % Math.max(1, results.length)); }
        if (event.key === "Enter" && results[index]) { event.preventDefault(); choose(results[index]); }
      }} aria-activedescendant={results[index] ? `command-${results[index].id}` : undefined} role="combobox" aria-expanded="true" aria-controls="command-results" />
    <div className={styles.searchResults} id="command-results" role="listbox" aria-label="命令搜索结果">
      {results.map((command, position) => <button id={`command-${command.id}`} key={command.id} role="option" aria-selected={position === index} disabled={command.disabled} onClick={() => choose(command)}
        ref={(element) => { if (position === index) element?.scrollIntoView?.({ block: "nearest" }); }}>
        <span>{command.label}</span><small>{command.section}</small>
      </button>)}
      {!results.length && <p>没有匹配结果，试试节点名称或“运行”。</p>}
    </div>
    {error && <p role="alert">{error}</p>}
    <footer>↑ ↓ 选择 · Enter 执行 · Esc 关闭</footer>
  </WorkflowDialog>;
}
