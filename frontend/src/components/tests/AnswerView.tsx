import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Button, Collapse, Skeleton, Tooltip, Typography } from "antd";
import type { Citation, QueryTimings } from "../../api/types";
import { projectFiles } from "../../api/queries";
import FilePreviewDrawer, { type Locator } from "../files/FilePreviewDrawer";
import Markdown from "./Markdown";
import { EXPLORE_TABLE, buildCitationModel, parseMarker, type Passage, type SourceDoc } from "./citationModel";

// A cited passage shows one line; the rest is a click away (R4-10).
const EXCERPT_CHARS = 120;

function Excerpt({ text }: { text: string | null }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (text === null) return <>—</>;
  if (text.length <= EXCERPT_CHARS) return <>{text}</>;
  return (
    <>
      <span style={{ whiteSpace: "pre-wrap" }}>{open ? text : `${text.slice(0, EXCERPT_CHARS)}…`}</span>{" "}
      <Button type="link" size="small" style={{ padding: 0, height: "auto" }} onClick={() => setOpen(!open)}>
        {open ? t("query.showLess") : t("query.showMore")}
      </Button>
    </>
  );
}

// One rendering for every answer (spec §9.2): the ad-hoc stream and the
// result drawer render identically, so users compare answers, not
// layouts. The answer renders as Markdown whose [Data: …] markers anchor
// into the citations panel; the panel lists each cited document once.
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
  const [preview, setPreview] = useState<{ name: string; locator: Locator; passage: string } | null>(null);
  const model = useMemo(() => buildCitationModel(citations), [citations]);
  const [citationsOpen, setCitationsOpen] = useState(false);
  // The anchor a clicked [Data] marker asked for; scrolled to once the
  // collapse has mounted its children.
  const [target, setTarget] = useState<string | null>(null);
  const uid = useId();
  const anchorId = (key: string, id: number) => `${uid}cite-${key}-${id}`;

  // Follow the newest line while streaming, unless the user scrolled up to
  // read; the bound goes away once the answer is complete (R4-09/R4-11).
  const follow = useRef(true);
  useEffect(() => {
    if (streaming) follow.current = true;
  }, [streaming]);
  useEffect(() => {
    const el = answerRef.current;
    if (el && streaming && follow.current) el.scrollTop = el.scrollHeight;
  }, [answer, streaming]);

  // The collapse mounts its panel a frame or two after opening, so the
  // anchor is looked up over a few animation frames.
  useEffect(() => {
    if (!citationsOpen || target === null) return;
    let frame = 0;
    const attempt = (left: number) => {
      const el = document.getElementById(target);
      if (el) {
        el.scrollIntoView?.({ block: "center", behavior: "smooth" });
        setTarget(null);
      } else if (left > 0) {
        frame = requestAnimationFrame(() => attempt(left - 1));
      } else {
        setTarget(null);
      }
    };
    attempt(30);
    return () => cancelAnimationFrame(frame);
  }, [citationsOpen, target]);

  const linkable = model.sources.some((d) => d.name !== null);

  // The live file listing is how the loop learns a cited document no
  // longer exists. The documents pane's query (read-only share; fresh for
  // 30 s, so a remount per picked result reuses it), only fetched at all
  // when something could link, and quiet: a failed listing costs the
  // links, not the answer.
  const files = useQuery({ ...projectFiles(projectId), enabled: linkable, meta: { silent: true } });

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

  const openPreview = (name: string, p: Passage) => setPreview({
    name,
    locator: origin ? { resultId: origin.resultId, entryId: p.id } : { passage: p.text ?? "" },
    passage: p.text ?? "",
  });

  // Only Sources resolves to a document (spec §7.4): other labels summarize
  // many documents, and a null source_name (guard withheld, unmapped title)
  // renders unlinked — resolution is best-effort throughout.
  const docName = (doc: SourceDoc) => {
    const { name } = doc;
    if (name === null) return <Typography.Text strong>{t("query.sourceNumber", { id: doc.passages[0].id })}</Typography.Text>;
    if (!alive.known) return <Typography.Text strong>{name}</Typography.Text>;
    if (!alive.names.has(name)) {
      return (
        // The span carries the hover: native disabled buttons swallow
        // pointer events in real browsers, and the tooltip must still open.
        <Tooltip title={t("query.sourceRemoved")}>
          <span>
            <Button type="link" disabled style={{ padding: 0, height: "auto" }}>{name}</Button>
          </span>
        </Tooltip>
      );
    }
    return (
      <Button
        type="link"
        style={{ padding: 0, height: "auto", whiteSpace: "normal" }}
        onClick={() => openPreview(name, doc.passages[0])}
      >
        {name}
      </Button>
    );
  };
  const docLive = (doc: SourceDoc) => doc.name !== null && alive.known && alive.names.has(doc.name);

  // A [Data: …] marker becomes one anchor per cited id; ids the payload
  // does not carry stay plain text.
  const markerNode = (raw: string, key: string) => {
    const groups = parseMarker(raw);
    if (groups.length === 0) return raw;
    return (
      <Typography.Text key={key} type="secondary" style={{ fontSize: 12 }}>
        [
        {groups.map((g, gi) => (
          <span key={gi}>
            {gi > 0 && "; "}
            {g.label}{" "}
            {g.ids.map((id, ii) => (
              <span key={id}>
                {ii > 0 && ", "}
                {model.has(g.key, id) ? (
                  <Button
                    type="link"
                    size="small"
                    style={{ padding: 0, height: "auto", fontSize: 12 }}
                    onClick={() => {
                      setCitationsOpen(true);
                      setTarget(anchorId(g.key, id));
                    }}
                  >
                    {id}
                  </Button>
                ) : id}
              </span>
            ))}
          </span>
        ))}
        ]
      </Typography.Text>
    );
  };

  const citationBody = (
    <>
      {model.sources.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text strong>{t("query.sourcesHeading")}</Typography.Text>
          {model.sources.map((doc, di) => (
            <div key={doc.name ?? `#${doc.passages[0].id}`} style={{ marginTop: di > 0 ? 8 : 4 }}>
              {docName(doc)}
              {doc.passages.map((p, pi) => (
                <div key={p.id} id={anchorId("sources", p.id)} style={{ paddingInlineStart: 12 }}>
                  <Excerpt text={p.text} />
                  {pi > 0 && docLive(doc) && (
                    <>
                      {" "}
                      <Button
                        type="link"
                        size="small"
                        style={{ padding: 0, height: "auto" }}
                        onClick={() => openPreview(doc.name!, p)}
                      >
                        {t("query.openPassage")}
                      </Button>
                    </>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
      {model.groups.map((g) => {
        const table = EXPLORE_TABLE[g.key];
        return (
          <div key={g.key} style={{ marginBottom: 12 }}>
            <Typography.Text strong>{g.label}</Typography.Text>
            <ul style={{ margin: 0, paddingInlineStart: 20 }}>
              {g.items.map((it) => (
                <li key={it.id} id={anchorId(g.key, it.id)}>
                  {table ? (
                    <Link
                      to={`/projects/${projectId}/explore?table=${table}&row=${it.id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={t("query.openInExplore")}
                    >
                      {it.text === null ? `#${it.id}`
                        : it.text.length > EXCERPT_CHARS ? `${it.text.slice(0, EXCERPT_CHARS)}…` : it.text}
                    </Link>
                  ) : (
                    <Excerpt text={it.text ?? `#${it.id}`} />
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </>
  );

  return (
    <>
      {(streaming || answer.length > 0) && (
        <div
          ref={answerRef}
          data-answer
          style={streaming ? { maxHeight: "40vh", overflow: "auto" } : undefined}
          onScroll={(e) => {
            const el = e.currentTarget;
            follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
        >
          <Markdown text={answer} renderMarker={markerNode} />
        </div>
      )}

      {streaming && citations.length === 0 ? (
        <Skeleton active paragraph={{ rows: 2 }} title={false} />
      ) : citations.length > 0 ? (
        <Collapse
          activeKey={citationsOpen ? ["citations"] : []}
          onChange={(keys) => setCitationsOpen(([] as string[]).concat(keys).includes("citations"))}
          items={[{
            key: "citations",
            label: t("query.citations", { count: model.count }),
            children: citationBody,
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
        highlight={preview?.passage}
        onClose={() => setPreview(null)}
      />
    </>
  );
}
