import { Select } from "antd";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";

// The one language switch, shared by the sidebar and the login card (R4-36):
// option values are the i18n language codes themselves, and the choice is
// cached by the detector, so a pick on /login carries past sign-in.
export default function LanguageSelect({ style }: { style?: CSSProperties }) {
  const { t, i18n } = useTranslation();
  return (
    <Select
      aria-label={t("layout.language")}
      value={i18n.language}
      onChange={(v) => { void i18n.changeLanguage(v); }}
      options={[
        { value: "zh-TW", label: "中文" },
        { value: "en-US", label: "English" },
      ]}
      popupMatchSelectWidth={false}
      style={style}
    />
  );
}
