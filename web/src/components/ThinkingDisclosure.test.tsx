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
  // The reader scrolls up: an upward wheel gesture away from the bottom.
  element.scrollTop = 0;
  element.dispatchEvent(
    new WheelEvent("wheel", { deltaY: -3, bubbles: true }),
  );

  rerender(<ThinkingDisclosure text={"第一行\n第二行"} live closed={false} />);

  expect(element.scrollTop).toBe(0);
});

test("stream growth outpacing the scroll event never strands the follow", () => {
  // Regression shape: the follow jumps to the current bottom, but the scroll
  // event for that jump lands after even more text grew, so the stale position
  // reads far from the bottom. A scroll event must never unpin the follow.
  const { rerender } = render(
    <ThinkingDisclosure text="第一行" live closed={false} />,
  );
  const element = document.querySelector<HTMLElement>(
    ".thinking-text",
  ) as HTMLElement;
  fakeScrollGeometry(element, 300);

  rerender(<ThinkingDisclosure text={"第一行\n第二行"} live closed={false} />);
  expect(element.scrollTop).toBe(300);

  // More text arrives before the jump's scroll event fires.
  fakeScrollGeometry(element, 3000);
  element.dispatchEvent(new Event("scroll"));

  rerender(
    <ThinkingDisclosure text={"第一行\n第二行\n第三行"} live closed={false} />,
  );

  expect(element.scrollTop).toBe(3000);
});

test("a reader who returns to the bottom re-pins the follow", () => {
  const { rerender } = render(
    <ThinkingDisclosure text="第一行" live closed={false} />,
  );
  const element = document.querySelector<HTMLElement>(
    ".thinking-text",
  ) as HTMLElement;
  fakeScrollGeometry(element, 300);
  element.scrollTop = 0;
  element.dispatchEvent(
    new WheelEvent("wheel", { deltaY: -3, bubbles: true }),
  );

  // Scrolling back to the bottom is the reader asking to resume following.
  element.scrollTop = 200;
  element.dispatchEvent(new Event("scroll"));

  rerender(<ThinkingDisclosure text={"第一行\n第二行"} live closed={false} />);

  expect(element.scrollTop).toBe(300);
});

test("a fresh block re-pins the follow even if the reader left the tail", () => {
  const { rerender } = render(
    <ThinkingDisclosure text="第一块" live closed={false} />,
  );
  const element = document.querySelector<HTMLElement>(
    ".thinking-text",
  ) as HTMLElement;
  fakeScrollGeometry(element, 300);
  element.scrollTop = 0;
  element.dispatchEvent(
    new WheelEvent("wheel", { deltaY: -3, bubbles: true }),
  );

  rerender(<ThinkingDisclosure text="第一块" live closed />);
  rerender(<ThinkingDisclosure text="第二块" live closed={false} />);

  expect(element.scrollTop).toBe(300);
});
