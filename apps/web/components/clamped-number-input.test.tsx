import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ClampedNumberInput } from "./clamped-number-input";

// jsdom 下 React 的 onBlur 通过原生 focus/blur 事件触发;原生事件不在
// fireEvent 的 act 包裹里,必须手动包一层让状态更新同步刷新。
function blur(element: HTMLElement) {
  act(() => {
    element.focus();
    element.blur();
  });
}

describe("ClampedNumberInput", () => {
  it("输入期间不钳制,失焦时才夹回区间", () => {
    const onCommit = vi.fn();
    render(<ClampedNumberInput value={1800} min={30} max={3600} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton");

    // 低于 min 的首键 "2" 不被立即改写成 "30"(旧按 keystroke 钳制会让
    // 键盘永远输入不出低于当前值的数)。
    fireEvent.change(input, { target: { value: "2" } });
    expect(input).toHaveValue(2);
    expect(onCommit).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "25" } });
    blur(input);
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith(30);

    // 高于 max 同理:失焦钳到上限。
    fireEvent.change(input, { target: { value: "99999" } });
    blur(input);
    expect(onCommit).toHaveBeenLastCalledWith(3600);
  });

  it("清空后失焦回退到上一个有效值,不提交", () => {
    const onCommit = vi.fn();
    render(<ClampedNumberInput value={4} min={1} max={8} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton");

    fireEvent.change(input, { target: { value: "" } });
    blur(input);
    expect(onCommit).not.toHaveBeenCalled();
    // 空/非法输入放弃修改,回显已提交的 prop 值。
    expect(input).toHaveValue(4);
  });

  it("区间内的输入原样提交", () => {
    const onCommit = vi.fn();
    render(<ClampedNumberInput value={4} min={1} max={8} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton");

    fireEvent.change(input, { target: { value: "6" } });
    blur(input);
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith(6);
  });
});
