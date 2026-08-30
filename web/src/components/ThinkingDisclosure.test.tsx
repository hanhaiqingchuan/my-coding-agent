import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { ThinkingDisclosure } from "./ThinkingDisclosure";

afterEach(cleanup);

/** jsdom does no layout, so the scroll geometry is defined per element. */
function fakeScrollGeometry(element: HTMLElement, scrollHeight: number) {
  Object.defineProperty(element, "scrollHeight", {
    value: scrollHeight,
    configurable: true,
  });
  Object.defineProperty(element, "clientHeight", {
    value: 100,
    configurable: true,
  });
}

test("a live open thinking block pins its scroll to the newest line", () => {
  const { rerender } = render(
    <ThinkingDisclosure text="第一行" live closed={false} />,
  );
  const body = document.querySelector<HTMLElement>(".thinking-text");
  expect(body).not.toBeNull();
  const element = body as HTMLElement;
  fakeScrollGeometry(element, 300);
  element.scrollTop = 0;

  rerender(<ThinkingDisclosure text={"第一行\n第二行\n第三行"} live closed={false} />);

  expect(element.scrollTop).toBe(300);
});

test("a reader who scrolled up is not yanked back while more text streams in", () => {
  const { rerender } = render(
    <ThinkingDisclosure text="第一行" live closed={false} />,
  );
  const element = document.querySelector<HTMLElement>(
    ".thinking-text",
  ) as HTMLElement;
  fakeScrollGeometry(element, 300);
  // The reader scrolls up: away from the bottom.
  element.scrollTop = 0;
  element.dispatchEvent(new Event("scroll"));

  rerender(<ThinkingDisclosure text={"第一行\n第二行"} live closed={false} />);

  expect(element.scrollTop).toBe(0);
});
