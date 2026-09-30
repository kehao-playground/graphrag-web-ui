import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Drawer, Space, Typography } from "antd";
import { sseUrl } from "../api/client";
import { refreshOnce, useAuth } from "../stores/auth";

// A reader this close to the bottom is following the tail.
const FOLLOW_SLACK_PX = 8;

type StreamState = "live" | "reconnecting" | "lost";

// Live job log viewer: native EventSource over the SSE route (Task 4);
// sseUrl() carries the auth rule. A dropped connection is retried natively
// with Last-Event-ID; a retry the server refuses (the ?token= expired, a
// 401) closes the stream for good, and Reconnect resumes it at the last
// offset with a fresh token (R2-32).
//
// Chunks are buffered and appended to the <pre> as text nodes once per
// animation frame (R1-85): a multi-MB index log never re-renders or
// re-joins as a whole. The <pre> has no React children; the stream owns it.
// The drawer is open exactly while a job id is set (R1-113).
export default function JobLogViewer({ jobId, title, onClose }: {
  jobId: string | null;
  // The caller's identity line for the job; the generic title otherwise.
  title?: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const preRef = useRef<HTMLPreElement>(null);
  const pendingRef = useRef("");
  const frameRef = useRef<number | null>(null);
  const lastIdRef = useRef<string | null>(null);
  const resumeRef = useRef(false);
  const [follow, setFollow] = useState(true);
  const followRef = useRef(true);
  const [stream, setStream] = useState<StreamState>("live");
  // Bumped by Reconnect: re-runs the stream effect in resume mode.
  const [attempt, setAttempt] = useState(0);

  const setFollowing = useCallback((on: boolean) => {
    followRef.current = on;
    setFollow(on);
  }, []);

  const scrollToEnd = () => {
    const pre = preRef.current;
    if (pre) pre.scrollTop = pre.scrollHeight;
  };

  const flush = useCallback(() => {
    frameRef.current = null;
    const pre = preRef.current;
    if (!pre || !pendingRef.current) return;
    pre.append(pendingRef.current);
    pendingRef.current = "";
    if (followRef.current) pre.scrollTop = pre.scrollHeight;
  }, []);

  useEffect(() => {
    if (!jobId) return;
    const resume = resumeRef.current;
    resumeRef.current = false;
    if (!resume) {
      // A fresh open replays the whole log.
      lastIdRef.current = null;
      pendingRef.current = "";
      if (preRef.current) preRef.current.textContent = "";
    }
    const params: Record<string, string> = resume && lastIdRef.current ? { offset: lastIdRef.current } : {};
    const es = new EventSource(sseUrl(`/api/jobs/${jobId}/logs`, params));
    es.addEventListener("open", () => setStream("live"));
    // data is a JSON-encoded string chunk; json.dumps keeps it single-line.
    es.addEventListener("log", (e) => {
      const ev = e as MessageEvent;
      if (ev.lastEventId) lastIdRef.current = ev.lastEventId;
      setStream("live");
      pendingRef.current += JSON.parse(ev.data) as string;
      frameRef.current ??= requestAnimationFrame(flush);
    });
    es.addEventListener("done", () => {
      // Terminal status: the server ends the response; stop listening locally.
      es.close();
    });
    es.addEventListener("error", () => {
      // CONNECTING: the browser is retrying on its own; CLOSED: it gave up.
      setStream(es.readyState === EventSource.CLOSED ? "lost" : "reconnecting");
    });
    return () => {
      es.close();
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      flush();
    };
  }, [jobId, attempt, flush]);

  const reconnect = async () => {
    // The usual cause is an expired access token: get a fresh one first.
    // A failed refresh still retries; the error listener reports the result.
    if (useAuth.getState().authMode !== "proxy") await refreshOnce().catch(() => null);
    resumeRef.current = true;
    setStream("live");
    setAttempt((n) => n + 1);
  };

  // A closed drawer forgets its stream state: the next open starts live
  // and following, like the replay it gets.
  const onOpenChange = (isOpen: boolean) => {
    if (isOpen) return;
    setStream("live");
    setFollowing(true);
  };

  const onScroll = () => {
    const pre = preRef.current;
    if (!pre) return;
    const atEnd = pre.scrollHeight - pre.scrollTop - pre.clientHeight <= FOLLOW_SLACK_PX;
    if (atEnd !== followRef.current) setFollowing(atEnd);
  };

  const followButton = follow
    ? <Button size="small" onClick={() => setFollowing(false)}>{t("jobs.logPause")}</Button>
    : (
      <Button size="small" type="primary" onClick={() => { setFollowing(true); scrollToEnd(); }}>
        {t("jobs.logFollow")}
      </Button>
    );

  return (
    <Drawer title={title ?? t("jobs.logsTitle")} open={jobId !== null} onClose={onClose} size="large" extra={followButton}
      afterOpenChange={onOpenChange}>
      <Space orientation="vertical" style={{ width: "100%" }}>
        {stream === "reconnecting" && <Typography.Text type="secondary">{t("jobs.logReconnecting")}</Typography.Text>}
        {stream === "lost" && (
          <Alert
            type="warning"
            showIcon
            message={t("jobs.logLost")}
            action={<Button size="small" onClick={() => void reconnect()}>{t("jobs.logReconnect")}</Button>}
          />
        )}
        <pre
          ref={preRef}
          onScroll={onScroll}
          style={{
            margin: 0, maxHeight: "70vh", overflow: "auto",
            fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-all",
          }}
        />
      </Space>
    </Drawer>
  );
}
