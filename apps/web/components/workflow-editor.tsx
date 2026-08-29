"use client";

import {
  Activity,
  Box,
  Check,
  ChevronDown,
  ChevronUp,
  CircleDot,
  Download,
  Link2,
  LockKeyhole,
  Minus,
  PanelRightClose,
  Play,
  Redo2,
  RotateCcw,
  Save,
  Settings2,
  Trash2,
  Undo2,
  Workflow,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent as ReactDragEvent,
} from "react";
import { api } from "@/lib/api";
import styles from "./workflow-editor.module.css";
import {
  ACTIVE_PROJECT_KEY,
  NODE_HEIGHT,
  NODE_WIDTH,
  WORLD_HEIGHT,
  WORLD_WIDTH,
  createNode,
  initialEdges,
  initialNodes,
  kindIcon,
  statusLabel,
  templateMap,
} from "./workflow-editor/graph-model";
import { clamp } from "./workflow-editor/geometry";
import { NodePalette } from "./workflow-editor/node-palette";
import { FlowCanvas } from "./workflow-editor/flow-canvas";
import { useWorkflowPersistence } from "./workflow-editor/use-workflow-persistence";
import { useViewportInteractions } from "./workflow-editor/use-viewport-interactions";
import type { FlowEdge, FlowNode } from "./workflow-editor/types";

export function WorkflowEditor() {
  const viewportRef = useRef<HTMLDivElement>(null);
  const runTokenRef = useRef(0);
  const [activeProjectId, setActiveProjectId] = useState(() => typeof window === "undefined"
    ? ""
    : window.localStorage.getItem(ACTIVE_PROJECT_KEY) ?? "");
  const [nodes, setNodes] = useState<FlowNode[]>(initialNodes);
  const [edges, setEdges] = useState<FlowEdge[]>(initialEdges);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>("adapter-1");
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [logOpen, setLogOpen] = useState(true);
  const [collapsedGroups, setCollapsedGroups] = useState<string[]>([]);
  const [search, setSearch] = useState("");

  const projectsQuery = useQuery({ queryKey: ["workflow-projects"], queryFn: api.projects });
  const resolvedProjectId = projectsQuery.data?.some((project) => project.id === activeProjectId)
    ? activeProjectId
    : projectsQuery.data?.[0]?.id ?? "";
  const activeProject = projectsQuery.data?.find((project) => project.id === resolvedProjectId) ?? null;
  const assetsQuery = useQuery({
    queryKey: ["workflow-assets", resolvedProjectId],
    queryFn: () => api.assets(resolvedProjectId),
    enabled: Boolean(resolvedProjectId),
  });
  const chaptersQuery = useQuery({
    queryKey: ["workflow-chapters", resolvedProjectId],
    queryFn: () => api.chapters(resolvedProjectId),
    enabled: Boolean(resolvedProjectId),
  });

  const { saved, setSaved, toast, showToast, saveFlow } = useWorkflowPersistence({
    resolvedProjectId,
    nodes,
    edges,
    setNodes,
    setEdges,
  });

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? null;
  const nodeMap = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);

  const deleteSelection = useCallback(() => {
    if (selectedNodeId) {
      setNodes((current) => current.filter((node) => node.id !== selectedNodeId));
      setEdges((current) => current.filter((item) => item.source !== selectedNodeId && item.target !== selectedNodeId));
      setSelectedNodeId(null);
      setSaved(false);
      return;
    }
    if (selectedEdgeId) {
      setEdges((current) => current.filter((item) => item.id !== selectedEdgeId));
      setSelectedEdgeId(null);
      setSaved(false);
    }
  }, [selectedEdgeId, selectedNodeId, setSaved]);

  const {
    pan,
    setPan,
    zoom,
    draftEnd,
    connectionAnchor,
    worldPoint,
    beginNodeDrag,
    beginOutputConnection,
    beginInputConnection,
    beginPan,
    handleWheel,
    zoomBy,
    fitToView,
  } = useViewportInteractions({
    viewportRef,
    nodes,
    nodeMap,
    edges,
    setNodes,
    setEdges,
    setSaved,
    setSelectedNodeId,
    setSelectedEdgeId,
    saveFlow,
    deleteSelection,
  });

  function addNode(templateKey: string, point?: { x: number; y: number }) {
    const rect = viewportRef.current?.getBoundingClientRect();
    const fallback = {
      x: ((rect?.width ?? 900) / 2 - pan.x) / zoom - NODE_WIDTH / 2,
      y: ((rect?.height ?? 620) / 2 - pan.y) / zoom - NODE_HEIGHT / 2,
    };
    const offsets = [
      { x: 0, y: 0 },
      { x: -310, y: -260 },
      { x: 310, y: -260 },
      { x: 0, y: 260 },
      { x: 310, y: 260 },
      { x: -310, y: 260 },
      { x: 310, y: 0 },
      { x: -310, y: 0 },
      { x: 0, y: -260 },
      { x: 0, y: 520 },
      { x: 620, y: 260 },
      { x: -620, y: 260 },
      { x: 620, y: 0 },
      { x: -620, y: 0 },
    ];
    const target = point ?? offsets
      .map((offset) => ({
        x: clamp(fallback.x + offset.x, 0, WORLD_WIDTH - NODE_WIDTH),
        y: clamp(fallback.y + offset.y, 0, WORLD_HEIGHT - NODE_HEIGHT),
      }))
      .find((candidate) => nodes.every((node) => !(
        candidate.x < node.x + NODE_WIDTH + 22
        && candidate.x + NODE_WIDTH + 22 > node.x
        && candidate.y < node.y + NODE_HEIGHT + 22
        && candidate.y + NODE_HEIGHT + 22 > node.y
      ))) ?? fallback;
    const id = `${templateKey}-${Date.now()}`;
    const next = createNode(templateKey, id, clamp(target.x, 0, WORLD_WIDTH - NODE_WIDTH), clamp(target.y, 0, WORLD_HEIGHT - NODE_HEIGHT));
    setNodes((current) => [...current, next]);
    setSelectedNodeId(id);
    setSelectedEdgeId(null);
    setSaved(false);
  }

  function handleDrop(event: ReactDragEvent<HTMLDivElement>) {
    event.preventDefault();
    const templateKey = event.dataTransfer.getData("application/x-mangaflow-node");
    if (!templateMap.has(templateKey)) return;
    const point = worldPoint(event.clientX, event.clientY);
    addNode(templateKey, { x: point.x - NODE_WIDTH / 2, y: point.y - 30 });
  }

  function updateNode(patch: Partial<FlowNode>) {
    if (!selectedNodeId) return;
    setNodes((current) => current.map((node) => node.id === selectedNodeId ? { ...node, ...patch } : node));
    setSaved(false);
  }

  function updateSettings(patch: Partial<FlowNode["settings"]>) {
    if (!selectedNodeId) return;
    setNodes((current) => current.map((node) => node.id === selectedNodeId ? {
      ...node,
      settings: { ...node.settings, ...patch },
    } : node));
    setSaved(false);
  }

  function chooseProject(projectId: string) {
    setActiveProjectId(projectId);
    window.localStorage.setItem(ACTIVE_PROJECT_KEY, projectId);
    const nextProject = projectsQuery.data?.find((project) => project.id === projectId);
    showToast(nextProject ? `已连接项目：${nextProject.name}` : "项目连接已更新");
  }

  function togglePaletteGroup(label: string) {
    setCollapsedGroups((current) => current.includes(label)
      ? current.filter((item) => item !== label)
      : [...current, label]);
  }

  async function runWorkflow() {
    if (isRunning) return;
    const token = ++runTokenRef.current;
    setIsRunning(true);
    setLogOpen(true);
    const order = ["source-1", "parser-1", "adapter-1", "director-1", "assets-1", "generator-1", "quality-1", "export-1"];
    setNodes((current) => current.map((node) => ({ ...node, status: order.includes(node.id) ? "idle" : node.status })));
    for (const id of order) {
      if (token !== runTokenRef.current) return;
      setNodes((current) => current.map((node) => node.id === id ? { ...node, status: "running" } : node));
      await new Promise((resolve) => window.setTimeout(resolve, 430));
      setNodes((current) => current.map((node) => node.id === id ? { ...node, status: "done" } : node));
    }
    setIsRunning(false);
    showToast("整条工作流运行完成");
  }

  function exportFlow() {
    const blob = new Blob([JSON.stringify({ version: 1, projectId: resolvedProjectId || null, nodes, edges }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "mangaflow-workflow.json";
    anchor.click();
    URL.revokeObjectURL(url);
    showToast("工作流 JSON 已导出");
  }

  const completedCount = nodes.filter((node) => node.status === "done").length;
  const runningNode = nodes.find((node) => node.status === "running");
  const assetCount = assetsQuery.data?.length ?? 0;
  const chapterCount = chaptersQuery.data?.length ?? 0;
  const projectDataLoading = Boolean(resolvedProjectId) && (assetsQuery.isLoading || chaptersQuery.isLoading);
  const projectDataError = projectsQuery.isError || assetsQuery.isError || chaptersQuery.isError;
  const selectedIsAssetSource = selectedNode?.outputs.some((item) => item.id === "assets" && item.dataType === "asset") ?? false;
  const selectedIsChapterSource = selectedNode?.outputs.some((item) => item.id === "source" && item.dataType === "text") ?? false;
  const selectedBindingCopy = !activeProject
    ? "尚未连接项目；该节点只能保留本地流程配置。"
    : selectedIsAssetSource
      ? `${assetCount} 项项目资产可从“资产包”端口传给下游节点。`
      : selectedIsChapterSource
        ? `${chapterCount} 章项目原作可从“原始文本”端口传给下游节点。`
        : "继承当前项目范围，但只处理端口实际连入的数据，不会自动读取全部资产。";

  const centerSelectedNode = useCallback(() => {
    if (selectedNode) setPan({ x: 360 - selectedNode.x * zoom, y: 260 - selectedNode.y * zoom });
  }, [selectedNode, setPan, zoom]);

  return (
    <div className={styles.editor}>
      <header className={styles.topbar}>
        <div className={styles.breadcrumb}>
          <span className={styles.workspaceMark}><Workflow size={15} /> FLOW / 01</span>
          <i />
          <div>
            <label className={styles.projectPicker} title="选择这张画布绑定的项目">
              <Link2 size={12} />
              <select aria-label="当前工作流项目" value={resolvedProjectId} onChange={(event) => chooseProject(event.target.value)} disabled={!projectsQuery.data?.length}>
                <option value="" disabled>{projectsQuery.isLoading ? "正在读取项目…" : "选择项目"}</option>
                {projectsQuery.data?.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
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

      <div className={`${styles.body} ${inspectorOpen ? "" : styles.inspectorClosed}`}>
        <NodePalette
          search={search}
          setSearch={setSearch}
          collapsedGroups={collapsedGroups}
          togglePaletteGroup={togglePaletteGroup}
          addNode={addNode}
        />

        <FlowCanvas
          viewportRef={viewportRef}
          nodes={nodes}
          edges={edges}
          nodeMap={nodeMap}
          selectedNodeId={selectedNodeId}
          selectedEdgeId={selectedEdgeId}
          setSelectedNodeId={setSelectedNodeId}
          setSelectedEdgeId={setSelectedEdgeId}
          activeProject={activeProject}
          projectDataError={projectDataError}
          projectsLoading={projectsQuery.isLoading}
          assetCount={assetCount}
          chapterCount={chapterCount}
          assetsLoading={assetsQuery.isLoading}
          chaptersLoading={chaptersQuery.isLoading}
          pan={pan}
          zoom={zoom}
          draftEnd={draftEnd}
          connectionAnchor={connectionAnchor}
          beginPan={beginPan}
          handleWheel={handleWheel}
          handleDrop={handleDrop}
          beginNodeDrag={beginNodeDrag}
          beginOutputConnection={beginOutputConnection}
          beginInputConnection={beginInputConnection}
          deleteSelection={deleteSelection}
          onCenterSelectedNode={centerSelectedNode}
          zoomBy={zoomBy}
          fitToView={fitToView}
          runMonitor={(
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
          )}
        />

        {inspectorOpen ? (
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
                      <strong>{activeProject?.name ?? (projectsQuery.isLoading ? "正在连接项目…" : "未绑定项目")}</strong>
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
        ) : (
          <button className={styles.openInspector} onClick={() => setInspectorOpen(true)} title="打开属性面板"><Settings2 size={16} /></button>
        )}
      </div>

      {toast && <div className={styles.toast}><Check size={15} />{toast}</div>}
    </div>
  );
}
