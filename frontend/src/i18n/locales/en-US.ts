// en-US catalog (i18n spec §5.1). `satisfies` pins this tree to the
// zh-TW shape — key drift is a compile error.
import type zhTW from "./zh-TW";

type Shape<T> = {
  readonly [K in keyof T]: T[K] extends string ? string : Shape<T[K]>;
};

const enUS = {
  errors: {
    not_indexed: "No index yet — run an indexing job first",
    user_not_found: "User not found",
    forbidden: "Forbidden",
    auth_too_many_attempts: "Too many attempts",
    auth_invalid_credentials: "Invalid email or password",
    auth_invalid_refresh_token: "Invalid refresh token",
    auth_wrong_current_password: "Incorrect current password",
    auth_not_authenticated: "Not authenticated",
    auth_invalid_token: "Invalid or expired token",
    auth_must_change_password: "Password change required",
    admin_only: "Admin only",
    explore_unknown_table: "Unknown table",
    explore_unsupported_filter: "This table does not support that filter",
    explore_read_failed: "Failed to read index output",
    explore_row_not_found: "Row not found",
    file_name_empty: "Filename must not be empty",
    file_name_too_long: "Filename exceeds 255 characters",
    file_name_unsafe: "Filename must not contain path separators or '..'",
    file_name_leading_dot: "Filename must not start with '.'",
    file_ext_not_allowed:
      "Extension '{{ext}}' is not allowed for input_file_type '{{input_file_type}}'",
    file_too_large: "File exceeds the {{max_mb}} MiB upload limit",
    quota_exceeded: "Project storage quota of {{quota_mb}} MiB exceeded",
    file_not_found: "File not found",
    job_not_found: "Job not found",
    job_conflict: "An indexing job is already active for this project",
    disk_watermark: "Insufficient free disk space",
    job_already_finished: "Job already finished",
    job_invalid_last_event_id: "Invalid Last-Event-ID",
    project_not_found: "Project not found",
    init_failed: "graphrag init failed",
    member_owner_protected: "Cannot demote or remove the project owner",
    member_not_found: "Member not found",
    query_rate_limited: "Query rate limit exceeded — try again later",
    query_config_failed: "Failed to load settings",
    query_failed: "Query failed",
    query_interrupted: "Query interrupted",
    settings_conflict: "Settings modified by someone else",
    settings_too_large: "Settings content too large",
    settings_invalid_yaml: "Invalid YAML: {{reason}}",
    settings_invalid_placeholder: "Invalid $ placeholder in settings",
    version_not_found: "Version not found",
    env_invalid_body: "Invalid body",
    env_key_value_required: "key and value are required",
    env_value_too_large: "value too large",
    env_invalid_key: "Invalid key: {{key}}",
    env_value_single_line: "value must be a single line",
    env_key_not_found: "key not found",
    email_registered: "email already registered",
    user_self_change_forbidden: "cannot change your own role or active status",
    user_last_admin_protected: "cannot demote or deactivate the last active admin",
    dry_run_failed: "graphrag dry-run failed",
  },
} satisfies Shape<typeof zhTW>;

export default enUS;
