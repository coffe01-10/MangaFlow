"use client";

import {
  AlignCenter,
  Link2,
  LockKeyhole,
  Maximize2,
  MoreHorizontal,
  MousePointer2,
  Trash2,
  Unplug,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import type { Dispatch, ReactNode, RefObject, SetStateAction } from "react";
import type {
  DragEvent as ReactDragEvent,
  PointerEvent as ReactPointerEvent,
  WheelEvent as ReactWheelEvent,
} from "react";
import type { Project } from "@/lib/api";

import { getPortPoint, nodeTypeClass, pathBetween, portTypeClass } from "./geometry";
import {
  kindIcon,
  statusLabel,
  WORLD_HEIGHT,
  WORLD_WIDTH,
} from "./graph-model";
import styles from "../workflow-editor.module.css";
import type { ConnectionAnchor, FlowEdge, FlowNode } from "./types";

export function FlowCanvas({
  viewportRef,
  nodes,
  edges,
  nodeMap,
  selectedNodeId,
  selectedEdgeId,
  setSelectedNodeId,
  setSelectedEdgeId,
  activeProject,
  projectDataError,
  projectsLoading,
  assetCount,
  chapterCount,
  assetsLoading,
  chaptersLoading,
  pan,
  zoom,
  draftEnd,
  connectionAnchor,
  beginPan,
  handleWheel,
  handleDrop,
  beginNodeDrag,
  beginOutputConnection,
  beginInputConnection,
  deleteSelection,
  onCenterSelectedNode,
  zoomBy,
  fitToView,
  runMonitor,
}: {
  viewportRef: RefObject<HTMLDivElement | null>;
  nodes: FlowNode[];
  edges: FlowEdge[];
  nodeMap: Map<string, FlowNode>;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  setSelectedNodeId: Dispatch<SetStateAction<string | null>>;
  setSelectedEdgeId: Dispatch<SetStateAction<string | null>>;
  activeProject: Project | null;
  projectDataError: boolean;
  projectsLoading: boolean;
  assetCount: number;
  chapterCount: number;
  assetsLoading: boolean;
  chaptersLoading: boolean;
  pan: { x: number; y: number };
  zoom: number;
  draftEnd: { x: number; y: number } | null;
  connectionAnchor: ConnectionAnchor | null;
  beginPan: (event: ReactPointerEvent<HTMLDivElement>) => void;
  handleWheel: (event: ReactWheelEvent<HTMLDivElement>) => void;
  handleDrop: (event: ReactDragEvent<HTMLDivElement>) => void;
  beginNodeDrag: (event: ReactPointerEvent, node: FlowNode) => void;
  beginOutputConnection: (event: ReactPointerEvent, nodeId: string, portId: string) => void;
  beginInputConnection: (event: ReactPointerEvent, nodeId: string, portId: string) => void;
  deleteSelection: () => void;
  onCenterSelectedNode: () => void;
  zoomBy: (factor: number) => void;
  fitToView: () => void;
  runMonitor: ReactNode;
}) {
  return (
    <main className={styles.canvasShell}>
      <div className={styles.canvasToolbar}>
        <div className={styles.toolGroup}>
          <button className={`${styles.canvasTool} ${styles.active}`} title="选择"><MousePointer2 size={15} /></button>
          <button className={styles.canvasTool} title="居中选中节点" onClick={onCenterSelectedNode}><AlignCenter size={15} /></button>
          <i />
          <button className={styles.canvasTool} title="断开选中连线" disabled={!selectedEdgeId} onClick={deleteSelection}><Unplug size={15} /></button>
          <button className={styles.canvasTool} title="删除选中项" disabled={!selectedNodeId && !selectedEdgeId} onClick={deleteSelection}><Trash2 size={15} /></button>
        </div>
        <div className={styles.canvasMeta} title={activeProject ? `已连接 ${activeProject.name}：${chapterCount} 章，${assetCount} 项资产` : "尚未连接项目"}>
          <span className={`${styles.liveDot} ${projectDataError || !activeProject ? styles.projectErrorDot : ""}`} />
          <span className={styles.projectMetaText}>{activeProject ? `${activeProject.name} · ${assetCount} 资产` : projectsLoading ? "连接项目…" : "未连接项目"}</span>
          <i />{nodes.length} 节点<i />{edges.length} 连线
        </div>
      </div>

      <div
        ref={viewportRef}
        className={`${styles.viewport} ${connectionAnchor?.side === "output" ? styles.connectingFromOutput : ""} ${connectionAnchor?.side === "input" ? styles.connectingFromInput : ""}`}
        style={{
          backgroundPosition: `${pan.x}px ${pan.y}px`,
          backgroundSize: `${24 * zoom}px ${24 * zoom}px`,
        }}
        onPointerDown={beginPan}
        onWheel={handleWheel}
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
      >
        <div className={styles.world} style={{ width: WORLD_WIDTH, height: WORLD_HEIGHT, transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}>
          <svg className={styles.edges} width={WORLD_WIDTH} height={WORLD_HEIGHT} aria-hidden="true">
            {edges.map((item) => {
              const source = nodeMap.get(item.source);
              const target = nodeMap.get(item.target);
              if (!source || !target) return null;
              const start = getPortPoint(source, item.sourcePort, "output");
              const end = getPortPoint(target, item.targetPort, "input");
              const selected = selectedEdgeId === item.id;
              return (
                <g
                  key={item.id}
                  data-flow-edge="true"
                  data-edge-id={item.id}
                  data-source-node={item.source}
                  data-source-port={item.sourcePort}
                  data-target-node={item.target}
                  data-target-port={item.targetPort}
                  onPointerDown={(event) => {
                  event.stopPropagation();
                  setSelectedEdgeId(item.id);
                  setSelectedNodeId(null);
                }}
                >
                  <path className={styles.edgeHit} d={pathBetween(start, end)} />
                  <path className={selected ? styles.edgeSelected : styles.edgeLine} d={pathBetween(start, end)} />
                </g>
              );
            })}
            {draftEnd && connectionAnchor && (() => {
              const anchorNode = nodeMap.get(connectionAnchor.nodeId);
              if (!anchorNode) return null;
              const anchorPoint = getPortPoint(anchorNode, connectionAnchor.portId, connectionAnchor.side);
              return <path className={styles.edgeDraft} d={connectionAnchor.side === "output" ? pathBetween(anchorPoint, draftEnd) : pathBetween(draftEnd, anchorPoint)} />;
            })()}
          </svg>

          {nodes.map((node) => {
            const Icon = kindIcon[node.kind];
            const selected = node.id === selectedNodeId;
            const isAssetSource = node.outputs.some((item) => item.id === "assets" && item.dataType === "asset");
            const isChapterSource = node.outputs.some((item) => item.id === "source" && item.dataType === "text");
            return (
              <article
                key={node.id}
                data-flow-node="true"
                className={`${nodeTypeClass(node.kind)} ${selected ? styles.selectedNode : ""}`}
                style={{ transform: `translate(${node.x}px, ${node.y}px)` }}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  setSelectedNodeId(node.id);
                  setSelectedEdgeId(null);
                }}
              >
                <header className={styles.nodeHeader} onPointerDown={(event) => beginNodeDrag(event, node)}>
                  <span className={styles.nodeIcon}><Icon size={16} /></span>
                  <div><small>{node.eyebrow}</small><strong>{node.title}</strong></div>
                  <button title="更多操作" onPointerDown={(event) => event.stopPropagation()}><MoreHorizontal size={15} /></button>
                </header>
                <p className={styles.nodeDescription}>{node.description}</p>
                <div className={styles.nodeStatus}><span className={styles[`status_${node.status}`]} />{statusLabel[node.status]}</div>

                <div className={styles.inputPorts}>
                  {node.inputs.map((item) => (
                    <div className={styles.portRow} key={item.id}>
                      <button
                        aria-label={`${node.title} 输入 ${item.label}`}
                        className={portTypeClass(item.dataType)}
                        data-node-id={node.id}
                        data-input-port={item.id}
                        data-connected={edges.some((edgeItem) => edgeItem.target === node.id && edgeItem.targetPort === item.id) ? "true" : "false"}
                        onPointerDown={(event) => beginInputConnection(event, node.id, item.id)}
                        title={edges.some((edgeItem) => edgeItem.target === node.id && edgeItem.targetPort === item.id) ? "拖动以更换来源；释放到空白处断开" : "拖到任意输出端口建立连接"}
                      />
                      <span>{item.label}</span>
                    </div>
                  ))}
                </div>
                <div className={styles.outputPorts}>
                  {node.outputs.map((item) => (
                    <div className={styles.portRow} key={item.id}>
                      <span>{isAssetSource && item.id === "assets"
                        ? `${item.label} · ${assetsLoading ? "…" : assetCount}`
                        : isChapterSource && item.id === "source"
                          ? `${item.label} · ${chaptersLoading ? "…" : chapterCount}章`
                          : item.label}</span>
                      <button
                        aria-label={`${node.title} 输出 ${item.label}`}
                        className={portTypeClass(item.dataType)}
                        data-node-id={node.id}
                        data-output-port={item.id}
                        onPointerDown={(event) => beginOutputConnection(event, node.id, item.id)}
                        title={`拖到任意输入端口连接 ${item.label}`}
                      />
                    </div>
                  ))}
                </div>
                <footer className={styles.nodeFooter}>
                  <span className={styles.nodeModel}>{node.settings.model}</span>
                  <span className={`${styles.nodeProjectBadge} ${activeProject && !projectDataError ? "" : styles.nodeProjectBadgeOff}`} title={activeProject ? `已绑定项目：${activeProject.name}；数据仍需通过端口传递` : "未绑定项目"}>
                    <Link2 size={9} />{activeProject ? "项目" : "未绑定"}
                  </span>
                  {node.settings.locked && <LockKeyhole size={11} />}
                  <small>#{node.id.split("-").at(-1)}</small>
                </footer>
              </article>
            );
          })}
        </div>

        <div className={connectionAnchor ? `${styles.canvasHint} ${styles.canvasHintActive}` : styles.canvasHint}>
          <MousePointer2 size={13} />
          {connectionAnchor?.side === "output" && "正在连线：释放到任意输入端口"}
          {connectionAnchor?.side === "input" && "正在换源：释放到任意输出端口；放到空白处断开"}
          {!connectionAnchor && "自由连线：拖动任意端口 · 选中连线后按 Delete 删除"}
        </div>
        <div className={styles.zoomControls}>
          <button onClick={() => zoomBy(1.15)} title="放大"><ZoomIn size={15} /></button>
          <span>{Math.round(zoom * 100)}%</span>
          <button onClick={() => zoomBy(0.87)} title="缩小"><ZoomOut size={15} /></button>
          <i />
          <button onClick={fitToView} title="适应画布"><Maximize2 size={15} /></button>
        </div>

        <div className={styles.minimap} aria-label="工作流缩略图">
          <div className={styles.minimapViewport} />
          {nodes.map((node) => (
            <i key={node.id} className={styles[`mini_${node.kind}`]} style={{ left: `${node.x / WORLD_WIDTH * 100}%`, top: `${node.y / WORLD_HEIGHT * 100}%` }} />
          ))}
        </div>

        {runMonitor}
      </div>
    </main>
  );
}
