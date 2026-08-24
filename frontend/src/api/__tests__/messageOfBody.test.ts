import { expect, test } from "vitest";
import { i18n } from "../../i18n";
import { messageOfBody } from "../client";

test("code maps through the errors catalog with params, per locale", async () => {
  const body = { detail: "extension '.exe' not allowed for input_file_type 'text'",
                 code: "file_ext_not_allowed",
                 params: { ext: ".exe", input_file_type: "text" } };
  expect(messageOfBody(body, "client.loadTableFailed"))
    .toBe("不允許的副檔名 '.exe' (輸入格式 text)");
  await i18n.changeLanguage("en-US");
  expect(messageOfBody(body, "client.loadTableFailed"))
    .toBe("Extension '.exe' is not allowed for input_file_type 'text'");
  await i18n.changeLanguage("zh-TW");
});

test("unknown code falls back to verbatim detail", () => {
  expect(messageOfBody({ detail: "brand new error", code: "future_code" }, "k"))
    .toBe("brand new error");
});

test("no code, no detail → fallback key with vars", () => {
  i18n.addResourceBundle("zh-TW", "translation",
    { client: { loadTableFailed: "載入資料表失敗({{status}})" } }, true, true);
  expect(messageOfBody({}, "client.loadTableFailed", { status: 502 }))
    .toBe("載入資料表失敗(502)");
});
