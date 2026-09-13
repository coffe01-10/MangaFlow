import { describe, expect, it } from "vitest";

import { localizeValidationMessage } from "./api";

// #545-9：FastAPI 422 的 pydantic v2 英文消息必须映射为中文，未识别文本原样回退。
describe("localizeValidationMessage（#545-9）", () => {
  it("常见 pydantic v2 消息映射为中文", () => {
    expect(localizeValidationMessage("Field required")).toBe("该字段为必填项");
    expect(localizeValidationMessage("Extra inputs are not permitted")).toBe("存在不允许的额外字段");
    expect(localizeValidationMessage("Input should be a valid integer")).toBe("输入应为整数");
    expect(localizeValidationMessage("Input should be a valid string")).toBe("输入应为文本");
    expect(localizeValidationMessage("Input should be a valid number")).toBe("输入应为数字");
    expect(localizeValidationMessage("Input should be a valid boolean")).toBe("输入应为布尔值");
    expect(localizeValidationMessage("Input should be a valid list")).toBe("输入应为列表");
    expect(localizeValidationMessage("Input should be a valid dictionary")).toBe("输入应为对象");
    expect(localizeValidationMessage("Input should be a valid email")).toBe("输入应为有效的邮箱地址");
  });

  it("带解析细节后缀的整数消息同样映射", () => {
    expect(localizeValidationMessage(
      "Input should be a valid integer, unable to parse string as an integer",
    )).toBe("输入应为整数");
  });

  it("带参数的长度与大小比较消息保留参数", () => {
    expect(localizeValidationMessage("String should have at most 40 characters"))
      .toBe("文本长度不能超过 40 个字符");
    expect(localizeValidationMessage("String should have at least 1 character"))
      .toBe("文本至少需要 1 个字符");
    expect(localizeValidationMessage("Input should be less than or equal to 10"))
      .toBe("输入不能大于 10");
    expect(localizeValidationMessage("Input should be greater than or equal to 1"))
      .toBe("输入不能小于 1");
    expect(localizeValidationMessage("Input should be less than 5")).toBe("输入必须小于 5");
    expect(localizeValidationMessage("Input should be greater than 0")).toBe("输入必须大于 0");
  });

  it("识别不了的消息原样返回（含业务 Value error）", () => {
    expect(localizeValidationMessage("Value error, 章节归属不允许跨项目")).toBe(
      "Value error, 章节归属不允许跨项目",
    );
    expect(localizeValidationMessage("some custom validator message")).toBe(
      "some custom validator message",
    );
  });
});
