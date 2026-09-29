import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Form, Input, Modal, message } from "antd";
import { ApiRequestError, sendOk } from "../api/client";
import { useAuth } from "../stores/auth";

interface Props {
  open: boolean;
  title: string;
  // The forced first-login change cannot be dismissed; the self-service
  // one from the user menu can.
  onCancel?: () => void;
  // Called once the new password is set and the session is signed in again.
  onDone: () => void;
  // Called when the change went through but signing in again did not.
  onSignedOut: () => void;
}

// One password-change form for the forced first-login modal and the user
// menu (R3-09). The backend revokes every refresh token of the user on a
// change, this login's included, so the form signs in again with the new
// password; otherwise the session would end when the access token expires.
export default function ChangePasswordModal({ open, title, onCancel, onDone, onSignedOut }: Props) {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [changing, setChanging] = useState(false);

  const onFinish = async (values: { current_password: string; new_password: string }) => {
    setChanging(true);
    try {
      await sendOk("/api/auth/change-password", "login.changeFailed", {
        method: "POST", body: JSON.stringify(values),
      });
    } catch (err) {
      if (!(err instanceof ApiRequestError)) {
        // Without this the button spun forever on a dropped connection
        message.error(t("login.networkError"));
      } else if (err.status === 400) {
        // 400 = wrong current password; 422 = new password failed backend
        // validation (min_length=8) — the two are surfaced separately
        form.setFields([{ name: "current_password", errors: [t("login.wrongCurrent")] }]);
      } else if (err.status === 422) {
        form.setFields([{ name: "new_password", errors: [t("login.newPasswordInvalid")] }]);
      } else {
        message.error(err.message);
      }
      setChanging(false);
      return;
    }
    const { user, login } = useAuth.getState();
    let signedIn = false;
    try {
      signedIn = !!user && await login(user.email, values.new_password);
    } catch {
      signedIn = false;
    }
    setChanging(false);
    form.resetFields();
    message.success(t("login.passwordChanged"));
    if (signedIn) { onDone(); return; }
    // The old session is revoked server-side; drop it here too.
    await useAuth.getState().logout().catch(() => {});
    onSignedOut();
  };

  return (
    <Modal
      title={title}
      open={open}
      closable={!!onCancel}
      maskClosable={false}
      onCancel={() => { form.resetFields(); onCancel?.(); }}
      footer={null}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" onFinish={onFinish}>
        <Form.Item label={t("login.currentPassword")} name="current_password" rules={[{ required: true }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item label={t("login.newPassword")} name="new_password" rules={[
          { required: true, message: t("login.newPasswordRequired") },
          { min: 8, message: t("login.newPasswordMin") },
        ]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={changing} block>{t("common.submit")}</Button>
      </Form>
    </Modal>
  );
}
