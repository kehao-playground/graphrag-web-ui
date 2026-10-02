import type { FormInstance } from "antd";

// A Modal's onOk that submits only a valid form. validateFields rejects
// when a field fails its rules; the form already shows those errors, so
// the rejection is swallowed here instead of escaping as an unhandled
// promise rejection on every invalid submit (F35-01).
export function onValid<T>(form: FormInstance<T>, submit: (values: T) => void): () => void {
  return () => {
    form.validateFields().then(submit, () => undefined);
  };
}
