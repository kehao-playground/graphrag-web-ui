import type { TFunction } from "i18next";
import type { QueryMethod } from "../../api/types";

// The four query methods, shared by the workbench's launch dialog, the
// ad-hoc box and the matrix's run columns. drift keeps the endonym "DRIFT"
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
