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

// A job's display_status (JobOut): the runner's closed set, with
// "failed(interrupted)" (a job the API restart found running) spelled as
// its own catalog key.
export function jobStatusLabel(v: string, t: TFunction): string {
  switch (v) {
    case "queued": return t("jobs.status.queued");
    case "running": return t("jobs.status.running");
    case "cancelling": return t("jobs.status.cancelling");
    case "succeeded": return t("jobs.status.succeeded");
    case "failed": return t("jobs.status.failed");
    case "failed(interrupted)": return t("jobs.status.interrupted");
    case "cancelled": return t("jobs.status.cancelled");
    default: return v;
  }
}

// Built-in role names are the backend seed's closed set, so the template
// key stays inside typed-t's key union; custom roles render their raw name.
type BuiltinRoleName = "user_admin" | "ops" | "viewer" | "maintainer" | "editor" | "owner";

export function roleLabel(role: { name: string; is_system: boolean }, t: TFunction): string {
  return role.is_system ? t(`roles.${role.name as BuiltinRoleName}`) : role.name;
}

// A built-in's seed description is English API text; the catalog carries
// the reader's copy. Custom roles keep what their author wrote.
export function roleDescription(role: { name: string; description: string; is_system: boolean }, t: TFunction): string {
  return role.is_system ? t(`roleDescriptions.${role.name as BuiltinRoleName}`) : role.description;
}

// Every action the backend audits today, in the filter's order. The label
// key is the id with "." spelled "_"; an action the backend adds before
// this list learns it renders its raw id.
export const AUDIT_ACTIONS = [
  "project.created", "project.updated", "project.deleted",
  "member.added", "member.role_changed", "member.removed",
  "file.uploaded", "file.deleted", "file.tagged", "file.untagged",
  "settings.updated", "env.key_set", "env.key_deleted",
  "question_set.created", "question_set.renamed", "question_set.archived",
  "question.created", "question.updated", "question.forked", "question.archived",
  "job.enqueued", "job.cancelled",
  "test_run.enqueued", "test.rated",
  "user.created", "user.updated", "user.password_changed", "user.password_reset",
  "user.role_promoted",
  "role.created", "role.updated", "role.deleted",
] as const;

type AuditAction = (typeof AUDIT_ACTIONS)[number];
type Snake<S> = S extends `${infer A}.${infer B}` ? `${A}_${B}` : never;
type AuditActionKey = Snake<AuditAction>;

export function auditActionLabel(action: string, t: TFunction): string {
  return (AUDIT_ACTIONS as readonly string[]).includes(action)
    ? t(`auditActions.${action.replace(".", "_") as AuditActionKey}`)
    : action;
}

// Permission atom labels (spec §7 catalog). The atom set is the backend's
// closed catalog, so the template key stays inside typed-t's key union;
// an unknown atom renders its raw name.
type PermKey =
  | "users_manage" | "projects_view_any" | "projects_act_any" | "projects_create"
  | "project_view" | "project_edit_content" | "project_run_jobs"
  | "project_edit_settings" | "project_manage";

export function permLabel(atom: string, t: TFunction): string {
  return t(`perms.${atom.replace(":", "_") as PermKey}`, atom);
}
