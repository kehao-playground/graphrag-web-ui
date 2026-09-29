import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Form, Input, message } from "antd";
import { useNavigate } from "react-router-dom";
import ChangePasswordModal from "../components/ChangePasswordModal";
import { redirectToProxyLogin, useAuth } from "../stores/auth";

export default function Login() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const login = useAuth((s) => s.login);
  const [error, setError] = useState(false);
  const [mustChange, setMustChange] = useState(false);

  const authMode = useAuth((s) => s.authMode);
  useEffect(() => {
    // Never rd back to /login: with a live proxy session /oauth2/start 302s
    // straight to rd and the loop never ends (found in live smoke test).
    if (authMode === "proxy") redirectToProxyLogin("/");
  }, [authMode]);
  // The proxy IdP owns sign-in; the local form never shows (spec §6.3).
  if (authMode === "proxy") return null;

  const onFinish = async (values: { email: string; password: string }) => {
    setError(false);
    let ok: boolean;
    try {
      ok = await login(values.email, values.password);
    } catch {
      message.error(t("login.networkError"));
      return;
    }
    if (!ok) { setError(true); return; }
    const user = useAuth.getState().user;
    if (user?.must_change_password) setMustChange(true);
    else navigate("/");
  };

  return (
    <div style={{ maxWidth: 360, margin: "12vh auto" }}>
      <h2>{t("login.pageTitle")}</h2>
      {error && <Alert type="error" message={t("login.failed")} style={{ marginBottom: 16 }} showIcon />}
      <Form layout="vertical" onFinish={onFinish}>
        <Form.Item label={t("common.email")} name="email" rules={[{ required: true, message: t("login.emailRequired") }]}>
          <Input type="email" />
        </Form.Item>
        <Form.Item label={t("login.passwordLabel")} name="password" rules={[{ required: true, message: t("login.passwordRequired") }]}>
          <Input.Password />
        </Form.Item>
        <Button type="primary" htmlType="submit" block>{t("login.submit")}</Button>
      </Form>
      <ChangePasswordModal
        open={mustChange}
        title={t("login.changeTitle")}
        onDone={() => { setMustChange(false); navigate("/"); }}
        onSignedOut={() => setMustChange(false)}
      />
    </div>
  );
}
