// Sentence-level diff (Task 8, spec §9.2): GraphRAG answers are prose, and
// a character diff buries the real change in noise. Splitting and comparing
// sentences is pure string arithmetic — no DOM, no React — so the run diff
// and its unit tests share one definition.

export interface DiffSegment {
  text: string;
  side: "both" | "left" | "right";
}

// A sentence boundary is an ASCII or CJK terminal punctuation mark; the
// delimiter AND the whitespace that follows it stay with the sentence that
// earned them. Carrying the whitespace matters: `split` on a lookbehind
// would consume it, and English prose would render in the diff pane with
// sentences jammed together ("A one.B two.").
const SENTENCE = /.*?[.!?。！？]+\s*|[^.!?。！？]+$/gsu;

function sentences(text: string): string[] {
  // An empty answer yields no sentences, not one empty sentence — otherwise
  // diffing against "" would report a phantom left-only "" segment.
  return text.match(SENTENCE)?.filter((s) => s.trim().length > 0) ?? [];
}

// Standard LCS over the sentence arrays, emitted as an ordered segment list:
// unchanged runs are "both", everything only a has is "left", everything
// only b has is "right". Consecutive same-side sentences merge into one
// segment so the diff view renders tidy blocks.
export function sentenceDiff(a: string, b: string): DiffSegment[] {
  const left = sentences(a);
  const right = sentences(b);
  // dp[i][j] = LCS length of left[i:] vs right[j:] — the suffix table the
  // greedy walk consults to decide which side to emit first.
  const dp: number[][] = Array.from({ length: left.length + 1 }, () =>
    new Array<number>(right.length + 1).fill(0),
  );
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      dp[i][j] =
        left[i] === right[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffSegment[] = [];
  const push = (side: DiffSegment["side"], text: string) => {
    const last = out[out.length - 1];
    if (last && last.side === side) last.text += text;
    else out.push({ side, text });
  };
  let i = 0;
  let j = 0;
  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) {
      push("both", left[i]);
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push("left", left[i]);
      i += 1;
    } else {
      push("right", right[j]);
      j += 1;
    }
  }
  while (i < left.length) {
    push("left", left[i]);
    i += 1;
  }
  while (j < right.length) {
    push("right", right[j]);
    j += 1;
  }
  return out;
}
