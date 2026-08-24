// zh-TW catalog (i18n spec §5.1). Values are today's exact copy.
// Interpolation uses i18next {{name}} syntax; escapeValue is false.
export default {
  errors: {
    // shared
    not_indexed: "尚未建立索引,請先執行索引任務",
    user_not_found: "使用者不存在",
    forbidden: "無權限",
    // auth
    auth_too_many_attempts: "嘗試次數過多",
    auth_invalid_credentials: "帳號或密碼錯誤",
    auth_invalid_refresh_token: "refresh token 無效",
    auth_wrong_current_password: "原密碼錯誤",
    auth_not_authenticated: "未登入",
    auth_invalid_token: "token 無效或已過期",
    auth_must_change_password: "需先更改密碼",
    admin_only: "僅管理者可用",
    // explore
    explore_unknown_table: "未知的資料表",
    explore_unsupported_filter: "此資料表不支援該篩選條件",
    explore_read_failed: "讀取索引輸出失敗",
    explore_row_not_found: "找不到該筆資料",
    // files
    file_name_empty: "檔名不可為空",
    file_name_too_long: "檔名超過 255 字元",
    file_name_unsafe: "檔名不可包含路徑分隔符或 '..'",
    file_name_leading_dot: "檔名不可以 '.' 開頭",
    file_ext_not_allowed: "不允許的副檔名 '{{ext}}' (輸入格式 {{input_file_type}})",
    file_too_large: "檔案超過 {{max_mb}} MiB 上傳上限",
    quota_exceeded: "超過專案儲存配額 {{quota_mb}} MiB",
    file_not_found: "找不到檔案",
    // jobs
    job_not_found: "找不到任務",
    job_conflict: "此專案已有進行中的索引任務",
    disk_watermark: "磁碟剩餘空間不足",
    job_already_finished: "任務已結束",
    job_invalid_last_event_id: "無效的 Last-Event-ID",
    // projects
    project_not_found: "專案不存在",
    init_failed: "graphrag init 失敗",
    member_owner_protected: "無法降級或移除專案擁有者",
    member_not_found: "成員不存在",
    // query
    query_rate_limited: "查詢過於頻繁,請稍後再試",
    query_config_failed: "設定載入失敗",
    query_failed: "查詢失敗",
    query_interrupted: "查詢中斷",
    // settings
    settings_conflict: "設定已被他人修改",
    settings_too_large: "設定內容過大",
    settings_invalid_yaml: "YAML 無法解析:{{reason}}",
    settings_invalid_placeholder: "設定內含無效的 $ 佔位符",
    version_not_found: "版本不存在",
    // env
    env_invalid_body: "無效的請求內容",
    env_key_value_required: "需要提供 key 與 value",
    env_value_too_large: "value 過大",
    env_invalid_key: "無效的 key:{{key}}",
    env_value_single_line: "value 必須是單行",
    env_key_not_found: "找不到 key",
    // users
    email_registered: "email 已被註冊",
    user_self_change_forbidden: "無法變更自己的角色或啟用狀態",
    user_last_admin_protected: "無法降級或停用最後一位啟用中的管理者",
    // dry run
    dry_run_failed: "graphrag dry-run 失敗",
  },
  common: {
    appName: "GraphRAG Web UI",
  },
  layout: {
    projects: "專案",
    adminUsers: "管理者 — 使用者",
    logout: "登出",
    title: "GraphRAG Web UI",
  },
};
