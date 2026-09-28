import type { TFunction } from "i18next";

// Display vocabularies more than one component renders (R1-49). A pure
// module rather than exports from a .tsx file, which would break the
// react/only-export-components fast-refresh ratchet. Unknown values render
// raw, like methodLabel: the backend may grow a type before the catalog.

// Sentence form ("An indexing job"): notices and cards that name the job
// holding a project.
export function jobTypeLabel(v: string, t: TFunction): string {
  return v === "index" ? t("workbench.typeIndex")
    : v === "update" ? t("workbench.typeUpdate")
    : v === "test_run" ? t("workbench.typeTestRun")
    : v;
}

// Noun form ("Index"): the jobs table column and the launch picker.
export function jobTypeShortLabel(v: string, t: TFunction): string {
  return v === "index" ? t("jobs.typeIndex")
    : v === "update" ? t("jobs.typeUpdate")
    : v === "test_run" ? t("jobs.typeTestRun")
    : v;
}

// Built-in role names are the backend seed's closed set, so the template
// key stays inside typed-t's key union; custom roles render their raw name.
type BuiltinRoleName = "user_admin" | "ops" | "viewer" | "maintainer" | "editor" | "owner";

export function roleLabel(role: { name: string; is_system: boolean }, t: TFunction): string {
  return role.is_system ? t(`roles.${role.name as BuiltinRoleName}`) : role.name;
}
