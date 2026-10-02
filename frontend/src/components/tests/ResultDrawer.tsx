import { useCallback, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Drawer, Input, Segmented, Skeleton, Space, Typography,
} from "antd";
import { sendOk } from "../../api/client";
import { runResults } from "../../api/queries";
import type { TestResult, TestRun } from "../../api/types";
import AnswerView from "./AnswerView";
import { runLabel } from "./methods";
import { RATING_META, SCORES } from "./ratings";
import type { Score } from "./ratings";

// One cell's full result (spec §9.2 "Cell → drawer"): the question AS ASKED
// (denormalized at run time, spec §5.3), the answer with citations and
// timings through the shared AnswerView — the same rendering the ad-hoc mode
// uses, so users compare answers, not layouts — plus the rating and note.
//
// Rating must be fast: with the drawer open, 1/2/3 rate good/fair/poor and
// advance to the next result of the run. The keydown handler is bound to the
// drawer panel itself (antd's onKeyDown lands on the dialog element), never
// to document, and skips keystrokes whose target is a text field — typing a
// note can never rate. Rating the LAST result closes the drawer: the pass
// is done. One rating is in flight at a time (R1-111): a key pressed before
// the previous PUT lands is dropped, so nothing is rated twice or skipped;
// a key pressed while the run's results still load is held and applied once
// they arrive (R4-41).
export default function ResultDrawer({ projectId, run, peers = [], resultId, onClose, onRated }: {
  // run beyond the plan's { resultId, onClose, onRated }: results are only
  // readable per run (GET /test-runs/{rid}/results), and that same ordered
  // list is what "advance to the next result" walks. The title names it.
  // projectId feeds AnswerView's citation links (spec §7.4): the preview
  // locator binds to the project's files.
  projectId: string;
  run: TestRun | null;
  // The matrix's runs, so the title reads exactly as the column (V-08).
  peers?: readonly TestRun[];
  resultId: string | null;
  onClose: () => void;
  onRated: () => void;
}) {
  const qc = useQueryClient();
  const { t, i18n } = useTranslation();
  const runId = run?.id ?? null;
  // Keys rate AND advance, so the current result is internal state — no
  // prop-sync effect needed: the parent keys this component by the picked
  // cell, so a new pick remounts with fresh state while advance walks the
  // run's list internally.
  const [currentId, setCurrentId] = useState<string | null>(resultId);
  const open = runId !== null && currentId !== null;

  const results = useQuery({ ...runResults(runId ?? ""), enabled: open });

  // Focus the drawer body as soon as it mounts: rc-drawer's focus lock can
  // lose the race with the panel motion, and keys 1/2/3 only reach the
  // handler while focus is inside the drawer. The panel mounts a beat after
  // `open` flips, so a callback ref — not an effect on `open` — is what
  // catches it, also while the results still load (R4-41). It never steals
  // focus back from the note field.
  const bodyRef = useCallback((el: HTMLDivElement | null) => {
    if (!el) return;
    const dialog = el.closest('[role="dialog"]');
    if (!dialog?.contains(document.activeElement)) el.focus();
  }, []);

  const list = results.data?.results ?? [];
  const index = list.findIndex((r) => r.id === currentId);
  const current = index >= 0 ? list[index] : null;

  // The note is per-result, seeded from an existing rating. Derived during
  // render instead of via an effect: when currentId changes (advance), the
  // derived value flips to the next result's own note, and typing claims
  // the draft for the result being viewed.
  const [noteDraft, setNoteDraft] = useState<{ for: string | null; value: string }>({
    for: null,
    value: "",
  });
  const note = noteDraft.for === currentId ? noteDraft.value : (current?.rating?.note ?? "");

  const rate = useMutation({
    mutationFn: (score: Score) => sendOk(
      `/api/test-results/${currentId}/rating`, "workbench.rateFailed",
      { method: "PUT", body: JSON.stringify({ score, note }) },
    ),
    onSuccess: () => {
      onRated();
      void qc.invalidateQueries({ queryKey: runResults(runId ?? "").queryKey });
    },
  });

  // Advance only after the rating landed; on the last result the drawer
  // closes instead — the rating pass is complete. `rows` is the run's list
  // the rating was aimed at. A ref, not rate.isPending, guards the flight:
  // the mutation's state reaches the next render only after a notify tick,
  // and two keydowns can land inside it. It is taken while a key waits for
  // the results too, so a second early key is dropped as well.
  const inFlight = useRef(false);
  const rateAndAdvance = (score: Score, rows: TestResult[]) => {
    const at = rows.findIndex((r) => r.id === currentId);
    if (at < 0) {
      inFlight.current = false;
      return;
    }
    inFlight.current = true;
    rate.mutate(score, {
      onSuccess: () => {
        const next = at + 1 < rows.length ? rows[at + 1] : null;
        if (next) setCurrentId(next.id);
        else onClose();
      },
      onSettled: () => {
        inFlight.current = false;
      },
    });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.repeat) return; // key auto-repeat would mass-rate the run
    const target = e.target as HTMLElement;
    // Typing a note must never rate (spec §9.2).
    if (target.closest("input, textarea, select, [contenteditable]")) return;
    const score = /^[1-9]$/.test(e.key) ? SCORES[Number(e.key) - 1] : undefined;
    if (!score || !currentId || !runId) return;
    e.preventDefault();
    if (inFlight.current) return;
    if (current) {
      rateAndAdvance(score, list);
    } else if (results.isPending) {
      // Pressed before the results arrived: wait on the same query (no
      // second request) and rate once it lands.
      inFlight.current = true;
      qc.ensureQueryData(runResults(runId)).then(
        (data) => rateAndAdvance(score, data.results),
        () => { inFlight.current = false; },
      );
    }
  };

  return (
    <Drawer
      open={open}
      width={560}
      onClose={onClose}
      onKeyDown={onKeyDown}
      title={run
        ? `${t("workbench.resultTitle")} · ${runLabel(run, t, i18n.language, peers)}`
        : t("workbench.resultTitle")}
      extra={<Typography.Text type="secondary">{t("common.escToClose")}</Typography.Text>}
    >
      <div ref={bodyRef} tabIndex={-1} style={{ outline: "none" }}>
      {!current ? (
        results.isPending ? (
          <Skeleton active paragraph={{ rows: 6 }} />
        ) : results.error ? (
          <Alert type="error" showIcon message={results.error.message} />
        ) : (
          <Alert type="warning" showIcon message={t("workbench.resultMissing")} />
        )
      ) : (
        <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
          <div>
            <Typography.Title level={5} style={{ marginTop: 0 }}>
              {current.question_text}
            </Typography.Title>
            <Typography.Text type="secondary">
              {t("workbench.resultPosition", { current: index + 1, total: list.length })}
            </Typography.Text>
          </div>
          {/* The row's error is a fixed server message (R2-07): say it in
              the reader's language rather than echo it. */}
          {current.error && <Alert type="error" showIcon message={t("workbench.questionFailed")} />}
          <AnswerView
            projectId={projectId}
            answer={current.answer ?? ""}
            citations={current.citations ?? []}
            timings={current.timings}
            // A stored run's citations pin to the artifacts that produced
            // them: {resultId, entryId} lets the server re-read the stored
            // passage instead of trusting current artifacts (spec §7.4).
            origin={currentId !== null ? { resultId: currentId } : null}
          />
          <div>
            <Segmented
              value={current.rating?.score ?? undefined}
              options={SCORES.map((value) => ({ label: t(RATING_META[value].labelKey), value }))}
              disabled={rate.isPending}
              onChange={(v) => rate.mutate(v as Score)}
            />
            <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 4 }}>
              {t("workbench.ratingHint")}
            </Typography.Paragraph>
            <Input.TextArea
              aria-label={t("workbench.noteLabel")}
              placeholder={t("workbench.notePlaceholder")}
              rows={2}
              value={note}
              onChange={(e) => setNoteDraft({ for: currentId, value: e.target.value })}
            />
          </div>
        </Space>
      )}
      </div>
    </Drawer>
  );
}
