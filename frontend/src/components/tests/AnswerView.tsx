import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Collapse, Skeleton, Typography } from "antd";
import type { Citation, QueryTimings } from "../../api/types";

// One rendering for every answer (spec §9.2): the ad-hoc stream and (Task 8)
// the result drawer render identically, so users compare answers, not
// layouts. Lifted verbatim from QueryPanel's answer area.
export default function AnswerView({ answer, citations, timings, streaming = false }: {
  answer: string;
  citations: Citation[];
  timings: QueryTimings | null;
  streaming?: boolean;
}) {
  const { t } = useTranslation();
  const answerRef = useRef<HTMLDivElement>(null);

  // Keep the newest line visible as the answer grows.
  useEffect(() => {
    const el = answerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [answer]);

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
                {c.entries.map((en) => (
                  <Typography.Paragraph
                    key={en.id}
                    type="secondary"
                    style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                  >
                    {en.text ?? "—"}
                  </Typography.Paragraph>
                ))}
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
    </>
  );
}
