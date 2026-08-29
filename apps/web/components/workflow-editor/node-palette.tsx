"use client";

import { ChevronDown, MousePointer2, Plus, Search } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";

import { paletteGroups } from "./graph-model";
import styles from "../workflow-editor.module.css";

export function NodePalette({
  search,
  setSearch,
  collapsedGroups,
  togglePaletteGroup,
  addNode,
}: {
  search: string;
  setSearch: Dispatch<SetStateAction<string>>;
  collapsedGroups: string[];
  togglePaletteGroup: (label: string) => void;
  addNode: (templateKey: string, point?: { x: number; y: number }) => void;
}) {
  const visibleGroups = paletteGroups.map((group) => ({
    ...group,
    items: group.items.filter((item) => `${item.title}${item.description}`.toLowerCase().includes(search.toLowerCase())),
  })).filter((group) => group.items.length > 0);

  return (
    <aside className={styles.palette}>
      <div className={styles.paletteHeader}>
        <div><span>NODE LIBRARY</span><strong>节点库</strong></div>
        <button className={styles.iconButton} onClick={() => addNode("parser")} title="添加节点"><Plus size={16} /></button>
      </div>
      <label className={styles.searchBox}>
        <Search size={14} />
        <input aria-label="搜索节点" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索节点…" />
        <kbd>/</kbd>
      </label>
      <div className={styles.paletteScroll}>
        {visibleGroups.map((group) => {
          const isCollapsed = collapsedGroups.includes(group.label) && !search.trim();
          return (
            <section className={styles.paletteGroup} key={group.label}>
              <button className={styles.paletteGroupHeader} aria-expanded={!isCollapsed} onClick={() => togglePaletteGroup(group.label)}>
                <span>{group.label}</span><ChevronDown className={isCollapsed ? styles.chevronCollapsed : ""} size={13} />
              </button>
              {!isCollapsed && <div className={styles.paletteItems}>
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.key}
                    draggable
                    className={`${styles.paletteItem} ${styles[`palette_${item.kind}`]}`}
                    onClick={() => addNode(item.key)}
                    onDragStart={(event) => {
                      event.dataTransfer.setData("application/x-mangaflow-node", item.key);
                      event.dataTransfer.effectAllowed = "copy";
                    }}
                  >
                    <span><Icon size={15} /></span>
                    <div><strong>{item.title}</strong><small>{item.description}</small></div>
                    <Plus size={13} />
                  </button>
                );
              })}
              </div>}
            </section>
          );
        })}
      </div>
      <div className={styles.paletteHint}><MousePointer2 size={14} /><span>拖到画布添加节点<br /><small>或单击快速添加</small></span></div>
    </aside>
  );
}
