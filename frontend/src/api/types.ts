export interface User {
  id: string; email: string; display_name: string;
  role: "admin" | "user"; is_active: boolean; must_change_password: boolean;
}
// GET /api/users 的窄清單(所有已登入者可用;刻意無 role 等管理欄位)
export interface UserBrief {
  id: string; email: string; display_name: string; is_active: boolean;
}
export interface Project {
  id: string; name: string; slug: string; description: string | null;
  input_file_type: "text" | "csv" | "json"; owner_id: string; created_at: string;
}
export interface Member { user_id: string; email: string; display_name: string; role: string }
// GET /api/projects/{id}/files (Task 2): files are sorted by name.
export interface FileEntry { name: string; size: number; modified_at: string }
export interface FilesOut { files: FileEntry[]; usage_bytes: number; quota_bytes: number }
