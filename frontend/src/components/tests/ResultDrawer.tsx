import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Drawer, Input, Segmented, Skeleton, Space, Typography, message,
} from "antd";
import type { Citation, QueryTimings, TestResult } from "../../api/types";
import { api, detailOf } from "../../api/client";
import AnswerView from "./AnswerView";

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
// is done.
export default function ResultDrawer({ projectId, runId, resultId, onClose, onRated }: {
  // runId beyond the plan's { resultId, onClose, onRated }: results are only
  // readable per run (GET /test-runs/{rid}/results), and that same ordered
  // list is what "advance to the next result" walks.
  // projectId feeds AnswerView's citation links (spec §7.4): the preview
  // locator binds to the project's files.
  projectId: string;
  runId: string | null;
  resultId: string | null;
  onClose: () => void;
  onRated: () => void;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  // Keys rate AND advance, so the current result is internal state — no
  // prop-sync effect needed: the parent keys this component by the picked
  // cell, so a new pick remounts with fresh state while advance walks the
  // run's list internally.
  const [currentId, setCurrentId] = useState<string | null>(resultId);
  const open = runId !== null && currentId !== null;

  const bodyRef = useRef<HTMLDivElement>(null);
  const results = useQuery({
    queryKey: ["test-runs", runId, "results"],
    queryFn: async () => {
      const r = await api(`/api/test-runs/${runId}/results`);
      if (!r.ok) throw new Error(await detailOf(r, "workbench.loadResultsFailed"));
      return (await r.json()) as { results: TestResult[] };
    },
    enabled: open,
    retry: false,
  });

  // Focus the drawer body when it opens: rc-drawer's focus lock can lose
  // the race with the panel motion, and keys 1/2/3 only reach the handler
  // while focus is inside the drawer. The panel mounts a beat after `open`
  // flips, so the effect also re-runs when the results arrive — and never
  // steals focus back from the note field mid-typing.
  useEffect(() => {
    if (!open || !bodyRef.current) return;
    const dialog = bodyRef.current.closest('[role="dialog"]');
    if (!dialog?.contains(document.activeElement)) bodyRef.current.focus();
  }, [open, results.data]);
  useEffect(() => {
    if (results.error) message.error(results.error.message);
  }, [results.error]);

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
    mutationFn: async (score: "good" | "fair" | "poor") => {
      const r = await api(`/api/test-results/${currentId}/rating`, {
        method: "PUT",
        body: JSON.stringify({ score, note }),
      });
      if (!r.ok) throw new Error(await detailOf(r, "workbench.rateFailed"));
    },
    onSuccess: () => {
      onRated();
      void qc.invalidateQueries({ queryKey: ["test-runs", runId, "results"] });
    },
    onError: (e) => message.error(e.message),
  });

  // Advance only after the rating landed; on the last result the drawer
  // closes instead — the rating pass is complete.
  const rateAndAdvance = (score: "good" | "fair" | "poor") => {
    rate.mutate(score, {
      onSuccess: () => {
        const next = index >= 0 && index + 1 < list.length ? list[index + 1] : null;
        if (next) setCurrentId(next.id);
        else onClose();
      },
    });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.repeat) return; // key auto-repeat would mass-rate the run
    const target = e.target as HTMLElement;
    // Typing a note must never rate (spec §9.2).
    if (target.closest("input, textarea, select, [contenteditable]")) return;
    const scores = { "1": "good", "2": "fair", "3": "poor" } as const;
    const score = scores[e.key as keyof typeof scores];
    if (score && currentId) {
      e.preventDefault();
      rateAndAdvance(score);
    }
  };

  return (
    <Drawer
      open={open}
      width={560}
      onClose={onClose}
      onKeyDown={onKeyDown}
      title={t("workbench.resultTitle")}
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
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <div>
            <Typography.Title level={5} style={{ marginTop: 0 }}>
              {current.question_text}
            </Typography.Title>
            <Typography.Text type="secondary">
              {t("workbench.resultPosition", { current: index + 1, total: list.length })}
            </Typography.Text>
          </div>
          {current.error && <Alert type="error" showIcon message={current.error} />}
          <AnswerView
            projectId={projectId}
            answer={current.answer ?? ""}
            citations={(current.citations as Citation[] | null) ?? []}
            timings={(current.timings as QueryTimings | null) ?? null}
            // A stored run's citations pin to the artifacts that produced
            // them: {resultId, entryId} lets the server re-read the stored
            // passage instead of trusting current artifacts (spec §7.4).
            origin={currentId !== null ? { resultId: currentId } : null}
          />
          <div>
            <Segmented
              value={current.rating?.score ?? undefined}
              options={[
                { label: t("workbench.ratingGood"), value: "good" },
                { label: t("workbench.ratingFair"), value: "fair" },
                { label: t("workbench.ratingPoor"), value: "poor" },
              ]}
              onChange={(v) => rate.mutate(v as "good" | "fair" | "poor")}
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
