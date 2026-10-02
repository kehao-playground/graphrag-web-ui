import { useMemo } from "react";
import type { TdHTMLAttributes } from "react";
import { useTranslation } from "react-i18next";
import { Checkbox, Table, Tag, Tooltip, Typography } from "antd";
import type { TableProps } from "antd";
import type { MatrixCell, MatrixRow, TestRun } from "../../api/types";
import { runAnchor, runLabel } from "./methods";
import { RATING_META, regressedLineages } from "./ratings";
import type { Score } from "./ratings";

// The lineage's current wording: the newest cell that actually asked it
// (spec §5.3 — each cell carries the text as asked).
function rowQuestionText(row: MatrixRow): string {
  for (let i = row.cells.length - 1; i >= 0; i -= 1) {
    const cell = row.cells[i];
    if (cell) return cell.question_text;
  }
  return row.lineage_id;
}

function CellView({ cell }: { cell: MatrixCell | null }) {
  const { t } = useTranslation();
  // Neither a null cell (the run never asked this lineage) nor an unfilled
  // placeholder (a cancelled run) is an answer: both render as not-run
  // rather than as an empty one (spec §5.3).
  if (!cell || !cell.completed) {
    return <span aria-label={t("workbench.notRun")}>{t("workbench.notRun")}</span>;
  }
  if (cell.error) {
    return <Tag color="volcano" title={cell.error}>{t("workbench.errored")}</Tag>;
  }
  if (!cell.rating) {
    return <Tag>{t("workbench.unrated")}</Tag>;
  }
  // An unknown score (a newer backend) renders raw rather than vanishing.
  const meta = RATING_META[cell.rating as Score] as (typeof RATING_META)[Score] | undefined;
  return <Tag color={meta?.color ?? "default"}>{meta ? t(meta.labelKey) : cell.rating}</Tag>;
}

export default function RatingMatrix({ runs, rows, regressionsOnly, onRegressionsOnly, onCell, selectedResultId }: {
  runs: TestRun[];
  rows: MatrixRow[];
  regressionsOnly: boolean;
  onRegressionsOnly: (v: boolean) => void;
  // Task 8 wires this to the result drawer / two-cell diff selection; the
  // matrix only reports which cell was picked.
  onCell?: (run: TestRun, row: MatrixRow, cell: MatrixCell) => void;
  // The first pick of the compare flow: the cell stays outlined after the
  // drawer closes so the user can see what their next click will diff
  // against.
  selectedResultId?: string | null;
}) {
  const { t, i18n } = useTranslation();

  const regressed = useMemo(() => regressedLineages(runs, rows), [runs, rows]);
  const visible = regressionsOnly ? rows.filter((r) => regressed.has(r.lineage_id)) : rows;

  // One row per lineage, one column per run — no virtualization (spec
  // §9.2): hundreds of rows × 5 columns is well within antd's Table.
  // Column label: the run label (method + start in the active locale, so
  // two runs on one day still differ); the index anchor is its tooltip.
  const columns: TableProps<MatrixRow>["columns"] = [
    {
      title: t("workbench.questionColumn"),
      key: "question",
      render: (_, row) => rowQuestionText(row),
    },
    ...runs.map((run, i) => {
      const label = runLabel(run, t, i18n.language, runs);
      return {
        title: <Tooltip title={runAnchor(run, t)}><span>{label}</span></Tooltip>,
        key: run.id,
        render: (_: unknown, row: MatrixRow) => <CellView cell={row.cells[i] ?? null} />,
        // The whole cell is the click target (R4-28), not just its tag.
        // Only a completed cell picks a result (Task 8): a null cell was
        // never asked, an unfilled placeholder was cancelled — neither has
        // an answer for the drawer or the diff to show.
        onCell: (row: MatrixRow): TdHTMLAttributes<HTMLTableCellElement> => {
          const cell = row.cells[i] ?? null;
          if (!cell || !cell.completed || !onCell) return {};
          const pick = () => onCell(run, row, cell);
          const selected = cell.result_id === selectedResultId;
          return {
            role: "button",
            tabIndex: 0,
            "aria-pressed": selected,
            // Selection aims at question × column, so every pickable cell
            // is labelled with both (spec §9.2).
            "aria-label": `${rowQuestionText(row)} × ${label}`,
            style: {
              cursor: "pointer",
              ...(selected ? { outline: "2px solid #1677ff", outlineOffset: -2 } : null),
            },
            onClick: pick,
            onKeyDown: (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                pick();
              }
            },
          };
        },
      };
    }),
  ];

  if (runs.length === 0) {
    return <Typography.Text type="secondary">{t("workbench.noRuns")}</Typography.Text>;
  }

  return (
    <div>
      <Checkbox
        style={{ marginBottom: 12 }}
        checked={regressionsOnly}
        onChange={(e) => onRegressionsOnly(e.target.checked)}
      >
        {t("workbench.regressionsOnly")}
      </Checkbox>
      <Table
        rowKey="lineage_id"
        size="small"
        dataSource={visible}
        columns={columns}
        pagination={false}
      />
    </div>
  );
}
