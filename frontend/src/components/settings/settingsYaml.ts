import { dump as yamlDump, load as yamlLoad } from "js-yaml";

// Pure YAML helpers behind the settings pane: parsing for form mode, the
// form-edit merge, and PyYAML error parsing for inline save errors.

// Form mode edits these paths in the parsed document (spec §6.5: input.* is
// locked at creation and shown read-only).
export const COMPLETION_PATHS = ["model", "model_provider", "auth_method"] as const;
export const MODEL_SECTIONS = [
  "completion_models.default_completion_model",
  "embedding_models.default_embedding_model",
] as const;
const FORM_FIELDS: Array<[string, readonly string[]]> = [
  ...MODEL_SECTIONS.map((s): [string, readonly string[]] => [s, COMPLETION_PATHS]),
  ["chunking", ["size", "overlap"]],
];

export type FormValue = string | number;
export type FormDraft = Record<string, FormValue>;

// A 400 from a save, kept under the editor until the next save (R4-35).
export interface SaveError {
  text: string;
  line?: number;
  column?: number;
}

// Form mode parses the current YAML. Empty content parses to undefined and
// "~" to null (no throw), and scalars/arrays parse fine but are not maps —
// treat every non-plain-object base as broken: degrade the display instead
// of crashing the render.
export function plainObject(text: string): Record<string, unknown> | null {
  try {
    const v = yamlLoad(text);
    return v !== null && typeof v === "object" && !Array.isArray(v)
      ? (v as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

// Walks a dotted path ("completion_models.default_completion_model.model")
// through nested maps; anything missing or not a map on the way is undefined.
function atPath(doc: Record<string, unknown>, path: string): unknown {
  let node: unknown = doc;
  for (const p of path.split(".")) {
    if (node === null || typeof node !== "object" || Array.isArray(node)) return undefined;
    node = (node as Record<string, unknown>)[p];
  }
  return node;
}

// The value a form field shows for a path: numbers stay numbers, anything
// else (missing included) is its string form.
export function formValueAt(doc: Record<string, unknown>, path: string): FormValue {
  const v = atPath(doc, path);
  return typeof v === "number" ? v : String(v ?? "");
}

// Applies a form draft onto the YAML. A form save must never silently
// rebuild the doc from a broken base: null when the base is not a map.
export function mergeFormEdits(content: string, draft: FormDraft): string | null {
  const doc = plainObject(content);
  if (doc === null) return null;
  for (const [section, leaves] of FORM_FIELDS) {
    // create intermediate objects on demand so a missing section can be added
    let node: Record<string, unknown> = doc;
    for (const p of section.split(".")) {
      node[p] = (node[p] as Record<string, unknown> | undefined) ?? {};
      node = node[p] as Record<string, unknown>;
    }
    leaves.forEach((leaf) => {
      const v = draft[`${section}.${leaf}`];
      if (v !== undefined) node[leaf] = v;
    });
  }
  return yamlDump(doc);
}

// PyYAML's message is context/problem lines interleaved with
// `in "<unicode string>", line N, column M` marks: keep the prose, report
// the last mark (the problem, not the context) as the location.
export function yamlProblem(reason: string): Omit<SaveError, "text"> & { prose: string } {
  const marks = [...reason.matchAll(/line (\d+), column (\d+)/g)];
  const last = marks.at(-1);
  const prose = reason.split("\n").map((l) => l.trim())
    .filter((l) => l && !l.startsWith("in \"")).join("; ");
  return { prose, line: last ? Number(last[1]) : undefined, column: last ? Number(last[2]) : undefined };
}
