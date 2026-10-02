import type { TFunction } from "i18next";
import type { QueryMethod, TestRun } from "../../api/types";
import { formatShortDateTime } from "../../i18n/format";

// The four query methods, shared by the workbench's launch dialog, the
// ad-hoc box and the matrix's run columns, plus the run label built on them. drift keeps the endonym "DRIFT"
// in every locale (identifier, not copy).
export const QUERY_METHODS = ["local", "global", "drift", "basic"] as const;

// Known values localize; an unknown method shows raw (JobOut types method
// as a plain string, so the fallback is reachable).
export function methodLabel(v: string, t: TFunction): string {
  return v === "local" ? t("query.methodLocal")
    : v === "global" ? t("query.methodGlobal")
    : v === "drift" ? t("query.methodDrift")
    : v === "basic" ? t("query.methodBasic")
    : v;
}

export function methodOptions(t: TFunction) {
  return QUERY_METHODS.map((v) => ({ label: methodLabel(v, t), value: v as QueryMethod }));
}

// A run's identity (R4-27): method and start time, the same string in the
// matrix column, the diff side and the drawer title. The index job the run
// was anchored to is not identity — two runs can share it — so it is shown
// only as runAnchor's tooltip. `peers` are the runs shown beside it: when
// one of them would get the same minute-precision label, the start time
// carries seconds so the two stay distinct (V-08).
export function runLabel(
  run: TestRun, t: TFunction, lang: string, peers: readonly TestRun[] = [],
): string {
  const at = (r: TestRun, seconds: boolean) =>
    `${methodLabel(r.method, t)} · ${r.started_at ? formatShortDateTime(r.started_at, lang, seconds) : "—"}`;
  const label = at(run, false);
  const clash = peers.some((p) => p.id !== run.id && at(p, false) === label);
  return clash ? at(run, true) : label;
}

export function runAnchor(run: TestRun, t: TFunction): string | null {
  return run.index_job_id ? t("workbench.runAnchor", { id: run.index_job_id.slice(0, 8) }) : null;
}
