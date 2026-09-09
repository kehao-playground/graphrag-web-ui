import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Alert, Modal, Skeleton, Space, Tag, Typography } from "antd";
import type { TestResult, TestRun } from "../../api/types";
import { api, detailOf } from "../../api/client";
import { methodLabel } from "./methods";
import { sentenceDiff } from "./sentenceDiff";
import type { DiffSegment } from "./sentenceDiff";

// One side of the comparison: which run the result came from. The label
// names the run the way the matrix columns do — index anchor, method, date —
// because that anchor (spec §5.3) is what makes two runs comparable.
export interface DiffSide {
  run: TestRun;
  resultId: string;
}

function runLabel(run: TestRun, t: TFunction): string {
  return `#${run.index_job_id ?? run.id} · ${methodLabel(run.method, t)} · ${run.started_at?.slice(5, 10) ?? "—"}`;
}

// antd red-1 / green-1: a tint, not a strike — prose context stays readable
// around the differing sentence (spec §9.2).
const LEFT_ONLY = { background: "#fff1f0" };
const RIGHT_ONLY = { background: "#f6ffed" };

function Pane({ label, result, segments, side }: {
  label: string;
  result: TestResult;
  segments: DiffSegment[];
  side: "left" | "right";
}) {
  const { t } = useTranslation();
  // Classic side-by-side alignment: each pane keeps the shared sentences and
  // its own; the other side's sentences simply do not appear here.
  const other = side === "left" ? "right" : "left";
  const shown = segments.filter((s) => s.side !== other);
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <Typography.Text strong>{label}</Typography.Text>
      <Typography.Paragraph type="secondary" style={{ whiteSpace: "pre-wrap" }}>
        {result.question_text}
      </Typography.Paragraph>
      {result.error ? (
        <Tag color="volcano" title={result.error}>{t("workbench.errored")}</Tag>
      ) : (
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
          {shown.map((s, i) => (
            <span
              key={i}
              style={s.side === "both" ? undefined : side === "left" ? LEFT_ONLY : RIGHT_ONLY}
            >
              {s.text}
            </span>
          ))}
        </Typography.Paragraph>
      )}
    </div>
  );
}

// The side-by-side diff (spec §9.2): two selected cells, answers compared at
// sentence granularity. Fetches both runs' result lists under the same cache
// keys the drawer uses, so opening a diff right after rating through the
// drawer costs no extra request.
export default function RunDiff({ open, left, right, onClose }: {
  open: boolean;
  onClose: () => void;
  left: DiffSide | null;
  right: DiffSide | null;
}) {
  const { t } = useTranslation();
  const useSideResults = (side: DiffSide | null) =>
    useQuery({
      queryKey: ["test-runs", side?.run.id, "results"],
      queryFn: async () => {
        const r = await api(`/api/test-runs/${side!.run.id}/results`);
        if (!r.ok) throw new Error(await detailOf(r, "workbench.loadResultsFailed"));
        return (await r.json()) as { results: TestResult[] };
      },
      enabled: open && !!side,
      retry: false,
    });
  const lq = useSideResults(left);
  const rq = useSideResults(right);
  const lr = left ? (lq.data?.results.find((r) => r.id === left.resultId) ?? null) : null;
  const rr = right ? (rq.data?.results.find((r) => r.id === right.resultId) ?? null) : null;

  const segments = useMemo(
    () => (lr && rr ? sentenceDiff(lr.answer ?? "", rr.answer ?? "") : []),
    [lr, rr],
  );

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      width={920}
      title={
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          {t("workbench.compareTitle")}
        </Typography.Title>
      }
    >
      {lq.error || rq.error ? (
        <Alert type="error" showIcon message={(lq.error ?? rq.error)!.message} />
      ) : !lr || !rr ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : (
        <>
          <Space style={{ marginBottom: 8 }}>
            <Tag color="red">{t("workbench.diffLeftOnly")}</Tag>
            <Tag color="green">{t("workbench.diffRightOnly")}</Tag>
          </Space>
          <div style={{ display: "flex", gap: 16 }}>
            <Pane label={runLabel(left!.run, t)} result={lr} segments={segments} side="left" />
            <Pane label={runLabel(right!.run, t)} result={rr} segments={segments} side="right" />
          </div>
        </>
      )}
    </Modal>
  );
}
