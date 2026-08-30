import { isValidElement, useState, type ReactElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Assistant text renders through react-markdown + remark-gfm (spec 13.6): the full
 * CommonMark block grammar plus GFM tables, struck text and task lists.
 *
 * Safety model: react-markdown never touches `innerHTML` — every piece of model
 * output becomes React text nodes, so raw HTML (`<script>`, event handlers,
 * images) is escaped to literal text by construction. On top of that, the link
 * gate below turns only http(s) targets into anchors; anything else renders as
 * the literal source text. The fenced-code card keeps its copy button.
 */

/* The timeline's own heading row sits one level above message content, so every
   heading in model output shifts one level down (h1 renders as h2, and so on). */

export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: (props) => <h2 {...props} />,
          h2: (props) => <h3 {...props} />,
          h3: (props) => <h4 {...props} />,
          h4: (props) => <h5 {...props} />,
          h5: (props) => <h6 {...props} />,
          h6: (props) => <h6 {...props} />,
          a: ({ node, href, children }) => {
            if (href !== undefined && /^https?:\/\//i.test(href)) {
              return (
                <a href={href} target="_blank" rel="noopener noreferrer">
                  {children}
                </a>
              );
            }
            // Unsafe targets stay the literal source text rather than anchors.
            const position = node?.position;
            const raw =
              position?.start.offset !== undefined &&
              position?.end.offset !== undefined
                ? text.slice(position.start.offset, position.end.offset)
                : children;
            return <>{raw}</>;
          },
          pre: ({ children }) => {
            // A fenced block's pre wraps exactly one <code> element; remark keeps
            // one trailing newline inside the fence that is not the author's code.
            const code = isValidElement(children)
              ? (children as ReactElement<{ className?: string; children?: ReactNode }>)
              : null;
            const language =
              /language-([A-Za-z0-9_+-]+)/.exec(code?.props.className ?? "")?.[1] ??
              null;
            const text = flattenText(code?.props.children).replace(/\n$/, "");
            return <CodeBlock code={text} language={language} />;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

/** Code text arrives as a string or an array of strings; flatten either. */
function flattenText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenText).join("");
  if (isValidElement(node)) {
    return flattenText((node.props as { children?: ReactNode }).children);
  }
  return "";
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
