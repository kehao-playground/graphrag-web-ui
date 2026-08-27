import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Form, Input, Modal } from "antd";
import { useNavigate } from "react-router-dom";
import { redirectToProxyLogin, useAuth } from "../stores/auth";

export default function Login() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const login = useAuth((s) => s.login);
  const setUser = useAuth.setState;
  const [error, setError] = useState(false);
  const [mustChange, setMustChange] = useState(false);
  const [changeForm] = Form.useForm();
  const [changing, setChanging] = useState(false);

  const authMode = useAuth((s) => s.authMode);
  useEffect(() => {
    if (authMode === "proxy") redirectToProxyLogin();
  }, [authMode]);
  // The proxy IdP owns sign-in; the local form never shows (spec §6.3).
  if (authMode === "proxy") return null;

  const onFinish = async (values: { email: string; password: string }) => {
    setError(false);
    const ok = await login(values.email, values.password);
    if (!ok) { setError(true); return; }
    const user = useAuth.getState().user;
    if (user?.must_change_password) setMustChange(true);
    else navigate("/");
  };

  const onChangePassword = async (values: { current_password: string; new_password: string }) => {
    setChanging(true);
    const token = useAuth.getState().accessToken;
    const r = await fetch("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(values),
    });
    setChanging(false);
    if (!r.ok) {
      // 400 = wrong current password; 422 = new password failed backend
      // validation (min_length=8) — the two are surfaced separately
      if (r.status === 400) {
        changeForm.setFields([{ name: "current_password", errors: [t("login.wrongCurrent")] }]);
      } else {
        changeForm.setFields([{ name: "new_password", errors: [t("login.newPasswordInvalid")] }]);
      }
      return;
    }
    setUser({ user: { ...useAuth.getState().user!, must_change_password: false } });
    setMustChange(false);
    navigate("/");
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
      <Modal
        title={t("login.changeTitle")}
        open={mustChange}
        closable={false}
        footer={null}
      >
        <Form form={changeForm} layout="vertical" onFinish={onChangePassword}>
          <Form.Item label={t("login.currentPassword")} name="current_password" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item label={t("login.newPassword")} name="new_password" rules={[
            { required: true, message: t("login.newPasswordRequired") },
            { min: 8, message: t("login.newPasswordMin") },
          ]}>
            <Input.Password />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={changing} block>{t("common.submit")}</Button>
        </Form>
      </Modal>
    </div>
  );
}
