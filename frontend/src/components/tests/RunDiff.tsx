import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Alert, Modal, Skeleton, Space, Tag, Tooltip, Typography } from "antd";
import type { TestResult, TestRun } from "../../api/types";
import { runResults } from "../../api/queries";
import { runAnchor, runLabel } from "./methods";
import { sentenceDiff } from "./sentenceDiff";
import type { DiffSegment } from "./sentenceDiff";

// One side of the comparison: which run the result came from. The side is
// labelled the way the matrix column is (R4-27) — method and start time —
// with the run's index anchor (spec §5.3) as the label's tooltip.
export interface DiffSide {
  run: TestRun;
  resultId: string;
}

// antd red-1 / green-1: a tint, not a strike — prose context stays readable
// around the differing sentence (spec §9.2).
const LEFT_ONLY = { background: "#fff1f0" };
const RIGHT_ONLY = { background: "#f6ffed" };

function Pane({ run, result, segments, side }: {
  run: TestRun;
  result: TestResult;
  segments: DiffSegment[];
  side: "left" | "right";
}) {
  const { t, i18n } = useTranslation();
  // Classic side-by-side alignment: each pane keeps the shared sentences and
  // its own; the other side's sentences simply do not appear here.
  const other = side === "left" ? "right" : "left";
  const shown = segments.filter((s) => s.side !== other);
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <Tooltip title={runAnchor(run, t)}>
        <Typography.Text strong>{runLabel(run, t, i18n.language)}</Typography.Text>
      </Tooltip>
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
// sentence granularity. Fetches both runs' result lists through the query
// the drawer uses, so opening a diff right after rating through the drawer
// costs no extra request. Errors render in the modal, not as a toast. The
// modal is open exactly while a pair is set.
export default function RunDiff({ pair, onClose }: {
  pair: { left: DiffSide; right: DiffSide } | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const open = pair !== null;
  const left = pair?.left ?? null;
  const right = pair?.right ?? null;
  const useSideResults = (side: DiffSide | null) =>
    useQuery({
      ...runResults(side?.run.id ?? ""),
      enabled: open && !!side,
      meta: { silent: true },
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
        <Space align="baseline" size="middle">
          <Typography.Title level={4} style={{ marginTop: 0 }}>
            {t("workbench.compareTitle")}
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontWeight: "normal" }}>
            {t("common.escToClose")}
          </Typography.Text>
        </Space>
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
            <Pane run={left!.run} result={lr} segments={segments} side="left" />
            <Pane run={right!.run} result={rr} segments={segments} side="right" />
          </div>
        </>
      )}
    </Modal>
  );
}
