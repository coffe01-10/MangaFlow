import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CommandPalette, COLLECT_COMMANDS, type StudioCommand } from "./command-palette";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

describe("global command palette", () => {
  beforeEach(() => {
    push.mockReset();
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  });
  it("opens anywhere with Ctrl+K, searches and executes a page command", () => {
    render(<CommandPalette />);
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const search = screen.getByRole("combobox");
    fireEvent.change(search, { target: { value: "设置" } });
    fireEvent.keyDown(search, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/settings");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("collects current editor commands and skips disabled operations", () => {
    const action = vi.fn();
    const collect = (event: Event) => (event as CustomEvent<StudioCommand[]>).detail.push({ id: "running", label: "运行", section: "操作", run: action, disabled: true });
    window.addEventListener(COLLECT_COMMANDS, collect);
    render(<CommandPalette />);
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "运行" } });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(action).not.toHaveBeenCalled();
    expect(screen.getByRole("option")).toBeDisabled();
    window.removeEventListener(COLLECT_COMMANDS, collect);
  });
});
