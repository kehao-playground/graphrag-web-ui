import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button, Popconfirm, Progress, Space, Table, Typography, Upload, message,
} from "antd";
import type { TableProps, UploadProps } from "antd";
import { api } from "../api/client";
import type { FileEntry, FilesOut, Project } from "../api/types";

// Mirrors the backend whitelist (services/files.py ALLOWED_EXTENSIONS):
// text → txt/md, csv → csv, json → json.
const ACCEPT: Record<"text" | "csv" | "json", string> = {
  text: ".txt,.md",
  csv: ".csv",
  json: ".json",
};

// Backend errors are always {"detail": "..."}; 400/413 upload messages come through here
async function detailOf(r: Response, fallback: string): Promise<string> {
  try {
    const body = await r.json() as { detail?: string };
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

const kib = (bytes: number) => `${(bytes / 1024).toFixed(1)} KiB`;

export default function FilesPanel({ projectId, inputFileType, canEdit }: {
  projectId: string;
  // ProjectOut.input_file_type is a plain string; the backend Literal on the
  // create body keeps the runtime values inside ACCEPT's keys.
  inputFileType: Project["input_file_type"];
  canEdit: boolean;
}) {
  const accept = ACCEPT[inputFileType as keyof typeof ACCEPT];
  const qc = useQueryClient();

  const files = useQuery({
    queryKey: ["projects", projectId, "files"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/files`);
      if (!r.ok) throw new Error(await detailOf(r, `載入檔案失敗(${r.status})`));
      return (await r.json()) as FilesOut;
    },
    retry: false,
  });

  useEffect(() => {
    if (files.error) message.error(files.error.message);
  }, [files.error]);

  const deleteFile = useMutation({
    mutationFn: async (name: string) => {
      const r = await api(`/api/projects/${projectId}/files/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await detailOf(r, `刪除檔案失敗(${r.status})`));
    },
    onSuccess: () => {
      message.success("檔案已刪除");
      qc.invalidateQueries({ queryKey: ["projects", projectId, "files"] });
    },
    onError: (e) => message.error(e.message),
  });

  // customRequest keeps the multipart POST inside api() so the auth header
  // and 401-retry apply; the browser sets the multipart boundary itself.
  const customRequest: UploadProps["customRequest"] = async ({ file, onSuccess, onError }) => {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await api(`/api/projects/${projectId}/files`, { method: "POST", body: fd });
      if (r.ok) {
        onSuccess?.(await r.json(), file);
        message.success(`已上傳 ${(file as File).name}`);
        qc.invalidateQueries({ queryKey: ["projects", projectId, "files"] });
      } else {
        const error = new Error(await detailOf(r, `上傳失敗(${r.status})`));
        onError?.(error);
        message.error(error.message);
      }
    } catch (e) {
      // api() rethrows network-level failures; surface them like HTTP errors
      const error = e instanceof Error ? e : new Error("上傳失敗");
      onError?.(error);
      message.error(error.message);
    }
  };

  const columns: TableProps<FileEntry>["columns"] = [
    { title: "名稱", dataIndex: "name" },
    { title: "大小", dataIndex: "size", width: 110, render: (_, f) => kib(f.size) },
    { title: "修改時間", dataIndex: "modified_at", width: 190, render: (_, f) => new Date(f.modified_at).toLocaleString() },
    ...(canEdit
      ? [{
          title: "操作",
          width: 90,
          render: (_: unknown, f: FileEntry) => (
            <Popconfirm
              title={`刪除 ${f.name}?`}
              okText="刪除"
              okButtonProps={{ danger: true }}
              onConfirm={() => deleteFile.mutate(f.name)}
            >
              <Button danger size="small">刪除</Button>
            </Popconfirm>
          ),
        }]
      : []),
  ];

  const usage = files.data?.usage_bytes ?? 0;
  const quota = files.data?.quota_bytes ?? 0;
  const percent = quota > 0 ? Math.round((usage / quota) * 100) : 0;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {canEdit && (
        <Upload.Dragger accept={accept} customRequest={customRequest} showUploadList={false} multiple>
          <p className="ant-upload-text">點擊或拖曳檔案上傳</p>
          <p className="ant-upload-hint">僅接受 {accept}</p>
        </Upload.Dragger>
      )}
      <div>
        <Typography.Text type="secondary">已使用 {kib(usage)} / {kib(quota)}</Typography.Text>
        {/* explicit format keeps the percent visible in exception status (antd
            swaps the text for an icon when only status is set) */}
        <Progress percent={percent} status={percent > 90 ? "exception" : "normal"} format={(p) => `${p}%`} />
      </div>
      <Table
        rowKey="name"
        size="small"
        loading={files.isFetching}
        dataSource={files.data?.files ?? []}
        columns={columns}
        pagination={false}
      />
    </Space>
  );
}
