import type { ReactNode } from "react";
import { Typography } from "antd";
import { INLINE, parseBlocks } from "./markdownText";

// The subset of Markdown graphrag answers use (R4-09): ATX headings,
// paragraphs, `-`/`1.` lists, rules, **bold**, *italic* and `code`. Every
// node is a React element built from text — no HTML passthrough, so an
// answer quoting markup shows it as text. `[Data: …]` markers go to
// renderMarker, which turns them into citation anchors. Unclosed syntax
// (a half-streamed `**bo`) stays literal until its closer arrives.

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
