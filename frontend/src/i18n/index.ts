// Self-initializing i18next module (spec §5.1). Importing any part of the
// app pulls this in; tests import it too and pin the language after.
import i18next from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import enUS from "./locales/en-US";
import zhTW from "./locales/zh-TW";

// Decision 3 (spec §1): zh* → zh-TW, everything else en-US. An explicit
// pure function instead of i18next negotiation (no
// nonExplicitSupportedLngs, no zh-TW fallback) so the mapping is testable.
export function resolveDetectedLanguage(l: string): "zh-TW" | "en-US" {
  return l.toLowerCase().startsWith("zh") ? "zh-TW" : "en-US";
}

export type ErrorCode = keyof (typeof zhTW)["errors"];

void i18next
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    supportedLngs: ["zh-TW", "en-US"],
    fallbackLng: "en-US", // missing-key backstop only
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
      convertDetectedLanguage: resolveDetectedLanguage,
    },
    // React escapes interpolated values already (spec G4).
    interpolation: { escapeValue: false },
    resources: {
      "zh-TW": { translation: zhTW },
      "en-US": { translation: enUS },
    },
  });

declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "translation";
    resources: { translation: typeof zhTW };
  }
}

export { i18next as i18n };
