import { Fragment, useMemo, useState, type ReactNode } from "react";

/**
 * A hand-rolled, dependency-free Markdown subset for assistant text (spec 13.6).
 *
 * Safety model: the renderer never touches `innerHTML`. Every piece of model output
 * becomes React text nodes, so raw HTML — `<script>`, event handlers, images — is
 * escaped to literal text by construction, and the only generated anchor is an
 * http(s) link that opens with `target="_blank" rel="noopener noreferrer"`.
 *
 * Supported: ATX headings, bold, italic, inline code, fenced code blocks (with a copy
 * button), unordered/ordered lists, blockquotes, links. Everything else is a paragraph.
 */

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "code"; language: string | null; code: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "paragraph"; text: string };

const FENCE = /^```([A-Za-z0-9_+-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const UNORDERED_ITEM = /^[-*]\s+(.*)$/;
const ORDERED_ITEM = /^\d{1,9}[.)]\s+(.*)$/;
const QUOTE_LINE = /^>\s?(.*)$/;

export function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => parseBlocks(text), [text]);
  return (
    <div className="markdown">
      {blocks.map((block, index) => (
        <Fragment key={index}>{renderBlock(block)}</Fragment>
      ))}
    </div>
  );
}

function parseBlocks(text: string): Block[] {
  const lines = text.split("\n");
  const blocks: Block[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.trim() === "") {
      index += 1;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence !== null) {
      const language = fence[1] === "" ? null : fence[1];
      const code: string[] = [];
      index += 1;
      while (index < lines.length && lines[index].trim() !== "```") {
        code.push(lines[index]);
        index += 1;
      }
      // A missing closing fence consumes the rest of the text as code.
      index = index < lines.length ? index + 1 : index;
      blocks.push({ kind: "code", language, code: code.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading !== null) {
      blocks.push({
        kind: "heading",
        level: heading[1].length,
        text: heading[2].trim(),
      });
      index += 1;
      continue;
    }
    const listItems = collectListItems(lines, index);
    if (listItems !== null) {
      const { items, ordered, next } = listItems;
      blocks.push({ kind: "list", ordered, items });
      index = next;
      continue;
    }
    const quote = QUOTE_LINE.exec(line);
    if (quote !== null) {
      const quoted: string[] = [];
      while (index < lines.length) {
        const match = QUOTE_LINE.exec(lines[index]);
        if (match === null) break;
        quoted.push(match[1]);
        index += 1;
      }
      blocks.push({ kind: "quote", text: quoted.join("\n") });
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length) {
      const current = lines[index];
      if (
        current.trim() === "" ||
        FENCE.test(current) ||
        HEADING.test(current) ||
        UNORDERED_ITEM.test(current) ||
        ORDERED_ITEM.test(current) ||
        QUOTE_LINE.test(current)
      ) {
        break;
      }
      paragraph.push(current);
      index += 1;
    }
    blocks.push({ kind: "paragraph", text: paragraph.join("\n") });
  }
  return blocks;
}

function collectListItems(
  lines: string[],
  start: number,
): { items: string[]; ordered: boolean; next: number } | null {
  const unordered = UNORDERED_ITEM.exec(lines[start]);
  const ordered = ORDERED_ITEM.exec(lines[start]);
  if (unordered === null && ordered === null) return null;
  const pattern = unordered !== null ? UNORDERED_ITEM : ORDERED_ITEM;
  const items: string[] = [];
  let index = start;
  while (index < lines.length) {
    const match = pattern.exec(lines[index]);
    if (match === null) break;
    items.push(match[1].trim());
    index += 1;
  }
  return { items, ordered: ordered !== null, next: index };
}

function renderBlock(block: Block): ReactNode {
  switch (block.kind) {
    case "heading": {
      const Tag = `h${Math.min(block.level + 1, 6)}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      // The bubble already styles its own header row, so headings inside a message
      // start one level down to keep the visual hierarchy of the timeline intact.
      return <Tag>{renderInline(block.text)}</Tag>;
    }
    case "code":
      return <CodeBlock code={block.code} language={block.language} />;
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag>
          {block.items.map((item, index) => (
            <li key={index}>{renderInline(item)}</li>
          ))}
        </Tag>
      );
    }
    case "quote":
      return <blockquote>{renderInline(block.text)}</blockquote>;
    case "paragraph":
      return <p>{renderInline(block.text)}</p>;
  }
}

/** One inline match: which pattern won, and the raw matched text.
 *
 * Emphasis uses a flanking-aware body (`\S…\S`), so `2 * 3 = 6` never italicizes:
 * a marker must hug non-space content on its inner side, the way CommonMark's
 * left/right-flanking rules behave for intraword-free cases.
 */
const INLINE_PATTERNS = [
  /`([^`\n]+)`/,
  /\*\*(\S(?:[^*\n]*\S)?)\*\*/,
  /\*(\S(?:[^*\n]*\S)?)\*/,
  /_(\S(?:[^_\n]*\S)?)_/,
  /\[([^\]\n]+)\]\(([^)\s]+)\)/,
] as const;

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let buffer = "";
  let rest = text;
  let key = 0;
  while (rest.length > 0) {
    let earliest: { index: number; length: number; match: RegExpExecArray } | null = null;
    for (const pattern of INLINE_PATTERNS) {
      const match = pattern.exec(rest);
      if (match === null) continue;
      if (earliest === null || match.index < earliest.match.index) {
        earliest = { index: match.index, length: match[0].length, match };
      }
    }
    if (earliest === null) break;
    buffer += rest.slice(0, earliest.match.index);
    const matched = earliest.match;
    if (buffer !== "") {
      nodes.push(<Fragment key={key++}>{buffer}</Fragment>);
      buffer = "";
    }
    if (matched[0].startsWith("`")) {
      nodes.push(<code key={key++}>{matched[1]}</code>);
    } else if (matched[0].startsWith("**")) {
      nodes.push(<strong key={key++}>{matched[1]}</strong>);
    } else if (matched[0].startsWith("*") || matched[0].startsWith("_")) {
      nodes.push(<em key={key++}>{matched[1]}</em>);
    } else {
      nodes.push(renderLink(matched[1], matched[2], key++));
    }
    rest = rest.slice(earliest.match.index + matched[0].length);
  }
  if (buffer !== "" || nodes.length === 0) {
    // The last node reuses the current key slot: every earlier push already
    // consumed its own, so uniqueness holds without a trailing increment.
    nodes.push(<Fragment key={key}>{buffer + rest}</Fragment>);
  }
  return nodes;
}

function renderLink(label: string, url: string, key: number): ReactNode {
  // Only http(s) targets become anchors; anything else (javascript:, file:, //,
  // protocol-relative, mailto:…) stays literal text rather than a clickable hazard.
  if (!/^https?:\/\//i.test(url)) {
    return <Fragment key={key}>{`[${label}](${url})`}</Fragment>;
  }
  return (
    <a key={key} href={url} target="_blank" rel="noopener noreferrer">
      {label}
    </a>
  );
}

export function CodeBlock({ code, language }: { code: string; language: string | null }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_600);
    } catch {
      // Clipboard permission denials leave the button unchanged; the code stays
      // selectable so the user can copy manually.
    }
  };
  return (
    <div className="md-code-block">
      <div className="md-code-head">
        <span className="md-code-lang">{language ?? "text"}</span>
        <button type="button" className="md-code-copy" onClick={() => void copy()}>
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}
