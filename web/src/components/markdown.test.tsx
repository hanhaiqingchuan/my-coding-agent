import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { Markdown } from "./markdown";

afterEach(cleanup);

test("renders the supported block subset of an assistant message", () => {
  render(
    <Markdown
      text={[
        "# 标题一",
        "",
        "段落含 **加粗**、*斜体*、`行内代码` 和 [链接](https://example.com)。",
        "",
        "- 第一项",
        "- 第二项",
        "",
        "1. 步骤一",
        "2. 步骤二",
        "",
        "> 引用一行",
        "",
        "```python",
        "print('hello')",
        "```",
        "",
        "## 小节",
        "",
        "结尾段落。",
      ].join("\n")}
    />,
  );

  // `#` renders as h2 (one level down from the timeline's own headings), `##` as h3.
  expect(screen.getByRole("heading", { level: 2, name: "标题一" })).not.toBeNull();
  expect(screen.getByRole("heading", { level: 3, name: "小节" })).not.toBeNull();
  expect(screen.getByText("加粗").tagName).toBe("STRONG");
  expect(screen.getByText("斜体").tagName).toBe("EM");
  expect(screen.getByText("行内代码").tagName).toBe("CODE");
  const link = screen.getByRole("link", { name: "链接" }) as HTMLAnchorElement;
  expect(link.getAttribute("href")).toBe("https://example.com");
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toContain("noopener");
  expect(screen.getByText("第一项").tagName).toBe("LI");
  expect(screen.getByText("步骤二").tagName).toBe("LI");
  expect(screen.getByText("引用一行").tagName).toBe("BLOCKQUOTE");
  expect(screen.getByText("print('hello')").tagName).toBe("CODE");
});

test("renders an http link and a copy button on fenced code blocks", async () => {
  const user = userEvent.setup();
  // Stub AFTER setup(): userEvent.setup() unconditionally installs its own
  // getter for navigator.clipboard, which would shadow a stub placed before it.
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });

  render(
    <Markdown text={"```\nconst answer = 42;\n```"} />,
  );

  await user.click(screen.getByRole("button", { name: "复制" }));
  expect(writeText).toHaveBeenCalledWith("const answer = 42;");
  await screen.findByText("已复制");
});

test("renders only http and https links; anything else stays literal text", () => {
  const { container } = render(
    <Markdown
      text={
        "[正常](https://example.com) [本站](http://example.com/a?b=1) " +
        "[危险](javascript:alert(1)) [本地](file:///etc/passwd) [协议相对](//example.com)"
      }
    />,
  );

  const links = screen.getAllByRole("link");
  expect(links.map((link) => link.getAttribute("href"))).toEqual([
    "https://example.com",
    "http://example.com/a?b=1",
  ]);
  // The unsafe targets never become anchors: they remain literal text.
  const text = container.textContent ?? "";
  expect(text).toContain("[危险](javascript:alert(1))");
  expect(text).toContain("[本地](file:///etc/passwd)");
  expect(text).toContain("[协议相对](//example.com)");
});

test("raw HTML in model output renders as literal text and never executes", () => {
  render(
    <Markdown
      text={
        '<script>alert("xss")</script>\n\n' +
        '<img src=x onerror="alert(1)">\n\n' +
        "正常文本 <b>不是加粗</b>"
      }
    />,
  );

  // No script/img/b elements are ever created…
  expect(document.querySelector("script")).toBeNull();
  expect(document.querySelector("img")).toBeNull();
  expect(document.querySelector("b")).toBeNull();
  // …and the markup stays visible as escaped literal text.
  expect(screen.getByText('<script>alert("xss")</script>')).not.toBeNull();
  expect(screen.getByText('<img src=x onerror="alert(1)">')).not.toBeNull();
  expect(screen.getByText("正常文本 <b>不是加粗</b>")).not.toBeNull();
});

test("an unterminated fence renders its remaining text as a code block", () => {
  render(<Markdown text={"开头\n```ts\nconst x = 1;"} />);

  expect(screen.getByText("const x = 1;").tagName).toBe("CODE");
});

test("emphasis markers without a pair stay literal text", () => {
  render(<Markdown text={"2 * 3 = 6 和 _下划线 与 *星号"} />);

  expect(screen.getByText("2 * 3 = 6 和 _下划线 与 *星号")).not.toBeNull();
});
