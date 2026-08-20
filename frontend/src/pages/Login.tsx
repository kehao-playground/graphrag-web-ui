import { useState } from "react";
import { Alert, Button, Form, Input, Modal } from "antd";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../stores/auth";

export default function Login() {
  const navigate = useNavigate();
  const login = useAuth((s) => s.login);
  const setUser = useAuth.setState;
  const [error, setError] = useState(false);
  const [mustChange, setMustChange] = useState(false);
  const [changeForm] = Form.useForm();
  const [changing, setChanging] = useState(false);

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
      // 400 = 原密碼錯誤;422 = 新密碼未過後端驗證(min_length=8) — 兩者分開提示
      if (r.status === 400) {
        changeForm.setFields([{ name: "current_password", errors: ["原密碼錯誤"] }]);
      } else {
        changeForm.setFields([{ name: "new_password", errors: ["新密碼不符合規定(至少 8 個字元)"] }]);
      }
      return;
    }
    setUser({ user: { ...useAuth.getState().user!, must_change_password: false } });
    setMustChange(false);
    navigate("/");
  };

  return (
    <div style={{ maxWidth: 360, margin: "12vh auto" }}>
      <h2>GraphRAG Web UI 登入</h2>
      {error && <Alert type="error" message="登入失敗,請檢查帳號密碼" style={{ marginBottom: 16 }} showIcon />}
      <Form layout="vertical" onFinish={onFinish}>
        <Form.Item label="電子郵件" name="email" rules={[{ required: true, message: "請輸入電子郵件" }]}>
          <Input type="email" />
        </Form.Item>
        <Form.Item label="密碼" name="password" rules={[{ required: true, message: "請輸入密碼" }]}>
          <Input.Password />
        </Form.Item>
        <Button type="primary" htmlType="submit" block>登入系統</Button>
      </Form>
      <Modal
        title="首次登入請修改密碼"
        open={mustChange}
        closable={false}
        footer={null}
      >
        <Form form={changeForm} layout="vertical" onFinish={onChangePassword}>
          <Form.Item label="目前密碼" name="current_password" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item label="新密碼" name="new_password" rules={[
            { required: true, message: "請輸入新密碼" },
            { min: 8, message: "新密碼至少 8 個字元" },
          ]}>
            <Input.Password />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={changing} block>送出</Button>
        </Form>
      </Modal>
    </div>
  );
}
