// The block and inline grammar of Markdown.tsx's subset, as pure string
// code so the run diff can compare the text an answer renders as (V-09)
// rather than its source. Markdown.tsx builds React nodes from the same
// parse; plainText flattens it.

export type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "rule" }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "para"; text: string };

const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

export function parseBlocks(text: string): Block[] {
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

export const INLINE = /(\[Data:[^\]]*\]|\*\*[^*\n]+?\*\*|`[^`\n]+`|\*[^*\s][^*\n]*?\*)/g;

// What a reader sees of an inline span: emphasis and code markers dropped,
// citation markers (and the space before them) removed.
function inlineText(text: string): string {
  return text.split(INLINE).map((part, i) => {
    if (i % 2 === 0) return part;
    if (part.startsWith("[Data:")) return "";
    if (part.startsWith("**")) return inlineText(part.slice(2, -2));
    return part.slice(1, -1);
  }).join("").replace(/[ \t]+([.,;:!?。，；：！？])/g, "$1").replace(/[ \t]+$/gm, "");
}

// An answer as the drawer renders it, one line per heading, paragraph line
// or list item, blocks separated by a blank line; rules carry no text.
export function plainText(text: string): string {
  return parseBlocks(text).flatMap((b) => {
    switch (b.kind) {
      case "rule": return [];
      case "heading": return [inlineText(b.text)];
      case "ul":
      case "ol": return [b.items.map(inlineText).join("\n")];
      default: return [inlineText(b.text)];
    }
  }).join("\n\n");
}
