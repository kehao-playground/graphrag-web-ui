import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Alert, Button, Space } from "antd";
import { apiJson } from "../../api/client";
import type { DryRunOut } from "../../api/types";

// The settings check (graphrag's dry run). A failed run is a result, shown
// inline — not a toast — and a pass says what it does not test (R4-24).
export default function DryRunSection({ projectId, canEdit }: {
  projectId: string;
  canEdit: boolean;
}) {
  const { t } = useTranslation();
  const [dryRun, setDryRun] = useState<{ ok: boolean; output: string } | null>(null);
  const mutation = useMutation({
    meta: { silent: true },
    mutationFn: () => apiJson<DryRunOut>(
      `/api/projects/${projectId}/dry-run`, "settings.dryRunFailed", { method: "POST" },
    ),
    onSuccess: (out) => setDryRun({ ok: out.ok, output: out.output }),
    onError: (e) => setDryRun({ ok: false, output: e.message }),
  });

  return (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <Button disabled={!canEdit} loading={mutation.isPending}
                onClick={() => mutation.mutate()}>{t("settings.dryRunButton")}</Button>
      </Space>
      {dryRun && (
        <Alert
          type={dryRun.ok ? "success" : "error"}
          showIcon
          message={dryRun.ok ? t("settings.dryRunPassed") : t("settings.dryRunFailed")}
          description={
            <>
              {dryRun.ok && <div>{t("settings.dryRunScope")}</div>}
              {dryRun.output && (
                <pre style={{ margin: 0, maxHeight: 240, overflow: "auto" }}>{dryRun.output}</pre>
              )}
            </>
          }
        />
      )}
    </div>
  );
}
