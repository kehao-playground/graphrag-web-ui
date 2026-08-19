export interface User {
  id: string; email: string; display_name: string;
  role: "admin" | "user"; is_active: boolean; must_change_password: boolean;
}
export interface Project {
  id: string; name: string; slug: string; description: string | null;
  input_file_type: "text" | "csv" | "json"; owner_id: string; created_at: string;
}
export interface Member { user_id: string; email: string; display_name: string; role: string }
