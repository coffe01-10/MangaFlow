"use client";

import { Box, ChevronDown, CircleDot, Link2, LockKeyhole, PanelRightClose, RotateCcw, Settings2, Trash2 } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";

import type { Project } from "@/lib/api";
import { kindIcon, statusLabel } from "./graph-model";
import styles from "../workflow-editor.module.css";
import type { FlowNode } from "./types";

export function NodeInspector({
  selectedNode,
  activeProject,
  projectsLoading,
  projectDataError,
  assetCount,
  chapterCount,
  inspectorOpen,
  setInspectorOpen,
  updateNode,
  updateSettings,
  deleteSelection,
}: {
  selectedNode: FlowNode | null;
  activeProject: Project | null;
  projectsLoading: boolean;
  projectDataError: boolean;
  assetCount: number;
  chapterCount: number;
  inspectorOpen: boolean;
  setInspectorOpen: Dispatch<SetStateAction<boolean>>;
  updateNode: (patch: Partial<FlowNode>) => void;
  updateSettings: (patch: Partial<FlowNode["settings"]>) => void;
  deleteSelection: () => void;
}) {
  if (!inspectorOpen) {
    return <button className={styles.openInspector} onClick={() => setInspectorOpen(true)} title="打开属性面板"><Settings2 size={16} /></button>;
  }

  const selectedIsAssetSource = selectedNode?.outputs.some((item) => item.id === "assets" && item.dataType === "asset") ?? false;
  const selectedIsChapterSource = selectedNode?.outputs.some((item) => item.id === "source" && item.dataType === "text") ?? false;
  const selectedBindingCopy = !activeProject
    ? "尚未连接项目；该节点只能保留本地流程配置。"
    : selectedIsAssetSource
      ? `${assetCount} 项项目资产可从“资产包”端口传给下游节点。`
      : selectedIsChapterSource
        ? `${chapterCount} 章项目原作可从“原始文本”端口传给下游节点。`
        : "继承当前项目范围，但只处理端口实际连入的数据，不会自动读取全部资产。";

  return (
    <aside className={styles.inspector}>
      <header className={styles.inspectorHeader}>
        <div><span>INSPECTOR</span><strong>属性面板</strong></div>
        <button className={styles.iconButton} onClick={() => setInspectorOpen(false)} title="收起属性面板"><PanelRightClose size={16} /></button>
      </header>
      {selectedNode ? (
        <div className={styles.inspectorContent}>
          <div className={styles.selectedSummary}>
            <span className={`${styles.summaryIcon} ${styles[`summary_${selectedNode.kind}`]}`}>{(() => { const Icon = kindIcon[selectedNode.kind]; return <Icon size={18} />; })()}</span>
            <div><small>{selectedNode.eyebrow} / {selectedNode.id.toUpperCase()}</small><strong>{selectedNode.title}</strong><span><i className={styles[`status_${selectedNode.status}`]} />{statusLabel[selectedNode.status]}</span></div>
          </div>

          <section className={styles.inspectorSection}>
            <h2>项目绑定 <span>CONTEXT</span></h2>
            <div className={`${styles.projectBindingCard} ${activeProject && !projectDataError ? "" : styles.projectBindingCardOff}`}>
              <span><Link2 size={14} /></span>
              <div>
                <strong>{activeProject?.name ?? (projectsLoading ? "正在连接项目…" : "未绑定项目")}</strong>
                <p>{selectedBindingCopy}</p>
              </div>
              <small>{activeProject && !projectDataError ? "已连接" : "未连接"}</small>
            </div>
          </section>

          <section className={styles.inspectorSection}>
            <h2>基本信息 <span>BASIC</span></h2>
            <label className={styles.fieldLabel} htmlFor={`flow-title-${selectedNode.id}`}>节点名称</label>
            <input id={`flow-title-${selectedNode.id}`} className={styles.textInput} value={selectedNode.title} onChange={(event) => updateNode({ title: event.target.value })} />
            <label className={styles.fieldLabel} htmlFor={`flow-description-${selectedNode.id}`}>说明</label>
            <textarea id={`flow-description-${selectedNode.id}`} className={styles.textArea} value={selectedNode.description} onChange={(event) => updateNode({ description: event.target.value })} />
          </section>

          <section className={styles.inspectorSection}>
            <h2>执行设置 <span>RUNTIME</span></h2>
            <label className={styles.fieldLabel} htmlFor={`flow-model-${selectedNode.id}`}>使用模型</label>
            <label className={styles.selectBox}>
              <select id={`flow-model-${selectedNode.id}`} value={selectedNode.settings.model} onChange={(event) => updateSettings({ model: event.target.value })}>
                <option>Gemini 3.5 Flash</option>
                <option>Nano Banana 2</option>
                <option>Nano Banana Pro</option>
              </select>
              <ChevronDown size={14} />
            </label>
            <div className={styles.twoFields}>
              <div><label className={styles.fieldLabel} htmlFor={`flow-resolution-${selectedNode.id}`}>清晰度</label><label className={styles.selectBox}><select id={`flow-resolution-${selectedNode.id}`} value={selectedNode.settings.resolution} onChange={(event) => updateSettings({ resolution: event.target.value })}><option>1K 草稿</option><option>2K 标准</option><option>4K 高清</option></select><ChevronDown size={14} /></label></div>
              <div><label className={styles.fieldLabel} htmlFor={`flow-concurrency-${selectedNode.id}`}>并发数</label><input id={`flow-concurrency-${selectedNode.id}`} className={styles.numberInput} min={1} max={8} type="number" value={selectedNode.settings.concurrency} onChange={(event) => updateSettings({ concurrency: Number(event.target.value) })} /></div>
            </div>
            <button aria-pressed={selectedNode.settings.locked} className={selectedNode.settings.locked ? `${styles.toggleRow} ${styles.toggleOn}` : styles.toggleRow} onClick={() => updateSettings({ locked: !selectedNode.settings.locked })}>
              <span><LockKeyhole size={14} /><span><strong>锁定节点设定</strong><small>运行时禁止自动改写</small></span></span><i />
            </button>
          </section>

          <section className={styles.inspectorSection}>
            <h2>端口 <span>PORTS</span></h2>
            <div className={styles.portList}>
              {[...selectedNode.inputs.map((item) => ({ ...item, direction: "输入" })), ...selectedNode.outputs.map((item) => ({ ...item, direction: "输出" }))].map((item) => (
                <div key={`${item.direction}-${item.id}`}><i className={styles[`port_${item.dataType}`]} /><span><strong>{item.label}</strong><small>{item.direction} · {item.dataType}</small></span><CircleDot size={13} /></div>
              ))}
            </div>
          </section>

          <section className={styles.inspectorSection}>
            <h2>备注 <span>NOTES</span></h2>
            <textarea aria-label="备注" className={`${styles.textArea} ${styles.notesArea}`} value={selectedNode.settings.notes} onChange={(event) => updateSettings({ notes: event.target.value })} placeholder="记录需要人工确认的规则…" />
          </section>

          <div className={styles.inspectorActions}>
            <button onClick={() => updateNode({ status: "ready" })}><RotateCcw size={14} />重置状态</button>
            <button className={styles.dangerAction} onClick={deleteSelection}><Trash2 size={14} />删除节点</button>
          </div>
        </div>
      ) : (
        <div className={styles.emptyInspector}><Box size={26} /><strong>未选择节点</strong><p>单击画布中的节点以编辑名称、模型、端口和运行参数。</p></div>
      )}
    </aside>
  );
}
