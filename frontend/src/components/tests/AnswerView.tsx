import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button, Collapse, Skeleton, Tooltip, Typography } from "antd";
import type { Citation, FilesOut, QueryTimings } from "../../api/types";
import { api, detailOf } from "../../api/client";
import FilePreviewDrawer, { type Locator } from "../files/FilePreviewDrawer";

// One rendering for every answer (spec §9.2): the ad-hoc stream and the
// result drawer render identically, so users compare answers, not
// layouts. Lifted verbatim from QueryPanel's answer area.
//
// Slice ③ closes the citation loop here (spec §7.4/§9.3): a Sources entry
// whose source_name arrived WITH the answer links to that document's
// preview, pinned to the locator for the answer's origin — {resultId,
// entryId} for a stored run, {passage} for an ad-hoc query. The name is
// never looked up later: a citation id is only meaningful against the
// artifacts that produced it, and no endpoint resolves one after the fact.
export default function AnswerView({ projectId, answer, citations, timings, streaming = false, origin = null }: {
  projectId: string;
  answer: string;
  citations: Citation[];
  timings: QueryTimings | null;
  streaming?: boolean;
  origin?: { resultId: string } | null;
}) {
  const { t } = useTranslation();
  const answerRef = useRef<HTMLDivElement>(null);
  const [preview, setPreview] = useState<{ name: string; locator: Locator } | null>(null);

  // Keep the newest line visible as the answer grows.
  useEffect(() => {
    const el = answerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [answer]);

  const linkable = useMemo(
    () => citations.some((c) => c.label === "Sources" && c.entries.some((en) => en.source_name !== null)),
    [citations],
  );

  // The live file listing is how the loop learns a cited document no
  // longer exists. Same key as FilesPanel (read-only share; it may
  // refetch), and only fetched at all when something could link.
  const files = useQuery({
    queryKey: ["projects", projectId, "files"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/files`);
      if (!r.ok) throw new Error(await detailOf(r, "files.loadFailed"));
      return (await r.json()) as FilesOut;
    },
    enabled: linkable,
    retry: false,
  });

  // The listing is filesystem-authoritative for existence (spec §5.1): a
  // name absent from it — or present only as a `removed` row, which has no
  // file behind it — is a document the preview endpoint cannot serve.
  // Until the listing lands, entries stay unlinked rather than gambling on
  // a dead link; a failed listing costs the links, not the answer.
  const alive = useMemo(() => {
    const names = new Set<string>();
    for (const f of files.data?.files ?? []) if (f.index_state !== "removed") names.add(f.name);
    return { known: files.data !== undefined, names };
  }, [files.data]);

  const entryNode = (label: string, en: Citation["entries"][number]) => {
    // Only Sources resolves (spec §7.4): other labels summarize many
    // documents, and a null source_name (guard withheld, unmapped title)
    // renders unlinked — resolution is best-effort throughout.
    const name = label === "Sources" ? en.source_name : null;
    const removed = name !== null && alive.known && !alive.names.has(name);
    return (
      <Typography.Paragraph
        key={en.id}
        type="secondary"
        style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
      >
        {en.text ?? "—"}
        {name && alive.known && !removed && (
          <Button
            type="link"
            style={{ padding: 0, height: "auto", whiteSpace: "normal" }}
            onClick={() => setPreview({
              name,
              locator: origin
                ? { resultId: origin.resultId, entryId: en.id }
                : { passage: en.text ?? "" },
            })}
          >
            {name}
          </Button>
        )}
        {removed && (
          // The span carries the hover: native disabled buttons swallow
          // pointer events in real browsers, and the tooltip must still open.
          <Tooltip title={t("query.sourceRemoved")}>
            <span>
              <Button type="link" disabled style={{ padding: 0, height: "auto" }}>
                {name}
              </Button>
            </span>
          </Tooltip>
        )}
      </Typography.Paragraph>
    );
  };

  return (
    <>
      {(streaming || answer.length > 0) && (
        <div ref={answerRef} style={{ maxHeight: "40vh", overflow: "auto" }}>
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {answer}
          </Typography.Paragraph>
        </div>
      )}

      {streaming && citations.length === 0 ? (
        <Skeleton active paragraph={{ rows: 2 }} title={false} />
      ) : citations.length > 0 ? (
        <Collapse
          items={[{
            key: "citations",
            label: t("query.citations", { count: citations.length }),
            children: citations.map((c, i) => (
              <div key={`${c.label}-${i}`} style={{ marginBottom: 8 }}>
                <Typography.Text strong>
                  {c.label} #{c.ids.join(", ")}
                </Typography.Text>
                {c.entries.map((en) => entryNode(c.label, en))}
              </div>
            )),
          }]}
        />
      ) : null}

      {timings && (
        <Typography.Text type="secondary">
          {t("query.timings", {
            frames: Math.round(timings.frames_ms),
            search: Math.round(timings.search_ms),
            citations: Math.round(timings.citations_ms),
            total: Math.round(timings.total_ms),
          })}
        </Typography.Text>
      )}

      <FilePreviewDrawer
        projectId={projectId}
        name={preview?.name ?? null}
        locator={preview?.locator}
        onClose={() => setPreview(null)}
      />
    </>
  );
}
