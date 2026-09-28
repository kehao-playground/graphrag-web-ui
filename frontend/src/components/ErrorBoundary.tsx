import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button, Result } from "antd";

// Catches a render error below it — a crashed pane, or a lazy chunk whose
// fetch failed (stale deploy, offline) — and shows it in place, so the
// rest of the app keeps rendering instead of unmounting to a blank page.
// Retry clears the error and re-renders; a changed resetKey (e.g. the
// route) clears it too. Reload is the way out of a stale deploy, whose old
// chunk names no longer exist on the server.
export default class ErrorBoundary extends Component<
  { children: ReactNode; resetKey?: unknown; onReset?: () => void },
  { error: Error | null; resetKey?: unknown }
> {
  state: { error: Error | null; resetKey?: unknown } = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  static getDerivedStateFromProps(
    props: { resetKey?: unknown },
    state: { error: Error | null; resetKey?: unknown },
  ) {
    return Object.is(props.resetKey, state.resetKey)
      ? null
      : { error: null, resetKey: props.resetKey };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }

  private reset = () => {
    this.props.onReset?.();
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    return error ? <Fallback error={error} onRetry={this.reset} /> : this.props.children;
  }
}

function Fallback({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <Result
      status="error"
      title={t("errorBoundary.title")}
      subTitle={error.message}
      extra={[
        <Button key="retry" type="primary" onClick={onRetry}>{t("errorBoundary.retry")}</Button>,
        <Button key="reload" onClick={() => window.location.reload()}>{t("errorBoundary.reload")}</Button>,
      ]}
    />
  );
}
