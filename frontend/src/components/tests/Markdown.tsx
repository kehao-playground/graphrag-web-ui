import type { ReactNode } from "react";
import { Typography } from "antd";

// The subset of Markdown graphrag answers use (R4-09): ATX headings,
// paragraphs, `-`/`1.` lists, rules, **bold**, *italic* and `code`. Every
// node is a React element built from text — no HTML passthrough, so an
// answer quoting markup shows it as text. `[Data: …]` markers go to
// renderMarker, which turns them into citation anchors. Unclosed syntax
// (a half-streamed `**bo`) stays literal until its closer arrives.

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "rule" }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "para"; text: string };

const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { kind: "ul" | "ol"; items: string[] } | null = null;
  const flush = () => {
    if (para.length) blocks.push({ kind: "para", text: para.join("\n") });
    if (list) blocks.push(list);
    para = [];
    list = null;
  };
  for (const line of text.split(/\r?\n/)) {
    let m: RegExpExecArray | null;
    if (!line.trim()) {
      flush();
    } else if ((m = HEADING.exec(line))) {
      flush();
      blocks.push({ kind: "heading", level: m[1].length, text: m[2] });
    } else if (RULE.test(line)) {
      flush();
      blocks.push({ kind: "rule" });
    } else if ((m = BULLET.exec(line)) || (m = NUMBERED.exec(line))) {
      const kind = BULLET.test(line) ? "ul" : "ol";
      if (para.length || (list && list.kind !== kind)) flush();
      list ??= { kind, items: [] };
      list.items.push(m[1]);
    } else if (list && /^\s/.test(line)) {
      // An indented line continues the last item.
      list.items[list.items.length - 1] += ` ${line.trim()}`;
    } else {
      if (list) flush();
      para.push(line);
    }
  }
  flush();
  return blocks;
}

const INLINE = /(\[Data:[^\]]*\]|\*\*[^*\n]+?\*\*|`[^`\n]+`|\*[^*\s][^*\n]*?\*)/g;

function inline(
  text: string,
  renderMarker: (raw: string, key: string) => ReactNode,
  prefix: string,
): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const key = `${prefix}.${i}`;
    if (i % 2 === 0) return part;
    if (part.startsWith("[Data:")) return renderMarker(part, key);
    if (part.startsWith("**")) return <strong key={key}>{inline(part.slice(2, -2), renderMarker, key)}</strong>;
    if (part.startsWith("`")) return <code key={key}>{part.slice(1, -1)}</code>;
    return <em key={key}>{inline(part.slice(1, -1), renderMarker, key)}</em>;
  });
}

export default function Markdown({ text, renderMarker }: {
  text: string;
  renderMarker: (raw: string, key: string) => ReactNode;
}) {
  return (
    <>
      {parseBlocks(text).map((b, i) => {
        const key = String(i);
        switch (b.kind) {
          case "heading":
            // Answer headings sit inside a panel: # → h3 … ### and below → h5.
            return (
              <Typography.Title key={key} level={Math.min(5, b.level + 2) as 3 | 4 | 5}>
                {inline(b.text, renderMarker, key)}
              </Typography.Title>
            );
          case "rule":
            return <hr key={key} style={{ border: 0, borderTop: "1px solid rgba(5,5,5,0.06)" }} />;
          case "ul":
          case "ol": {
            const List = b.kind;
            return (
              <List key={key} style={{ paddingInlineStart: 24 }}>
                {b.items.map((it, j) => <li key={j}>{inline(it, renderMarker, `${key}.${j}`)}</li>)}
              </List>
            );
          }
          default:
            return (
              <Typography.Paragraph key={key} style={{ whiteSpace: "pre-wrap" }}>
                {inline(b.text, renderMarker, key)}
              </Typography.Paragraph>
            );
        }
      })}
    </>
  );
}
