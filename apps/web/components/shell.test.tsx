import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalNav } from "./shell";

const navigation = vi.hoisted(() => ({ pathname: "/" }));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
}));

describe("GlobalNav", () => {
  beforeEach(() => {
    navigation.pathname = "/";
  });

  it("只保留三个真实全局入口，并标记当前页面", () => {
    navigation.pathname = "/settings";
    render(<GlobalNav />);

    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["项目", "帮助", "设置"]);
    expect(screen.getByRole("link", { name: "设置" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "项目" })).not.toHaveAttribute("aria-current");
  });
});
