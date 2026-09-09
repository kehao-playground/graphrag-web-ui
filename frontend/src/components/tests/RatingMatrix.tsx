import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Checkbox, Table, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import type { MatrixCell, MatrixRow, TestRun } from "../../api/types";
import { methodLabel } from "./methods";
import { regressedLineages } from "./ratings";

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
  const label =
    cell.rating === "good" ? t("workbench.ratingGood")
    : cell.rating === "fair" ? t("workbench.ratingFair")
    : cell.rating === "poor" ? t("workbench.ratingPoor")
    : cell.rating;
  const color =
    cell.rating === "good" ? "green" : cell.rating === "fair" ? "gold" : cell.rating === "poor" ? "red" : "default";
  return <Tag color={color}>{label}</Tag>;
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
  const { t } = useTranslation();

  const regressed = useMemo(() => regressedLineages(runs, rows), [runs, rows]);
  const visible = regressionsOnly ? rows.filter((r) => regressed.has(r.lineage_id)) : rows;

  // One row per lineage, one column per run — no virtualization (spec
  // §9.2): hundreds of rows × 5 columns is well within antd's Table.
  // Column label: localized method + the run's start date (a fixed
  // YYYY-MM-DD slice, not Intl, so the label is stable everywhere).
  const columns: TableProps<MatrixRow>["columns"] = [
    {
      title: t("workbench.questionColumn"),
      key: "question",
      render: (_, row) => rowQuestionText(row),
    },
    ...runs.map((run, i) => ({
      title: `${methodLabel(run.method, t)} · ${run.started_at?.slice(5, 10) ?? "—"}`,
      key: run.id,
      render: (_: unknown, row: MatrixRow) => {
        const cell = row.cells[i] ?? null;
        const content = <CellView cell={cell} />;
        // Only a completed cell picks a result (Task 8): a null cell was
        // never asked, an unfilled placeholder was cancelled — neither has
        // an answer for the drawer or the diff to show.
        if (!cell || !cell.completed || !onCell) return content;
        const pick = () => onCell(run, row, cell);
        // Selection aims at question × column, so every pickable cell is
        // labelled with both (spec §9.2). The column names the run's index
        // anchor, exactly what the diff's side labels echo.
        const label = `${rowQuestionText(row)} × #${run.index_job_id ?? run.id}`;
        const selected = cell.result_id === selectedResultId;
        return (
          <span
            role="button"
            tabIndex={0}
            aria-pressed={selected}
            aria-label={label}
            style={{
              cursor: "pointer",
              ...(selected ? { outline: "2px solid #1677ff", outlineOffset: 2 } : null),
            }}
            onClick={pick}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                pick();
              }
            }}
          >
            {content}
          </span>
        );
      },
    })),
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
