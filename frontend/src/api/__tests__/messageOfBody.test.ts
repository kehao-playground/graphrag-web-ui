import { expect, test } from "vitest";
import { i18n } from "../../i18n";
import { messageOfBody } from "../client";

test("code maps through the errors catalog with params, per locale", async () => {
  const body = { detail: "extension '.exe' not allowed for input_file_type 'text'",
                 code: "file_ext_not_allowed",
                 params: { ext: ".exe", input_file_type: "text" } };
  expect(messageOfBody(body, "client.loadTableFailed"))
    .toBe("不允許的副檔名「.exe」（輸入格式 text）");
  await i18n.changeLanguage("en-US");
  expect(messageOfBody(body, "client.loadTableFailed"))
    .toBe("Extension '.exe' is not allowed for the text input format");
  await i18n.changeLanguage("zh-TW");
});

test("a job type param is named in the reader's language, not as its wire id (R3-33)", async () => {
  const body = { detail: "project is being indexed by job j1", code: "project_indexing",
                 params: { job_type: "update" } };
  expect(messageOfBody(body, "client.loadTableFailed"))
    .toBe("更新任務正在進行，文件與設定異動已暫停");
  await i18n.changeLanguage("en-US");
  expect(messageOfBody(body, "client.loadTableFailed"))
    .toBe("An update job is running; document and settings changes are paused");
  await i18n.changeLanguage("zh-TW");
});

test("counted messages pick the English singular and plural (F12-01)", async () => {
  await i18n.changeLanguage("en-US");
  expect(i18n.t("files.bulkDeleteDone", { count: 1 })).toBe("Deleted 1 document");
  expect(i18n.t("files.bulkDeleteDone", { count: 3 })).toBe("Deleted 3 documents");
  await i18n.changeLanguage("zh-TW");
  expect(i18n.t("files.bulkDeleteDone", { count: 1 })).toBe("已刪除 1 份文件");
});

test("unknown code falls back to verbatim detail", () => {
  expect(messageOfBody({ detail: "brand new error", code: "future_code" }, "files.loadFailed"))
    .toBe("brand new error");
});

test("no code, no detail → fallback key with vars", () => {
  i18n.addResourceBundle("zh-TW", "translation",
    { client: { loadTableFailed: "載入資料表失敗({{status}})" } }, true, true);
  expect(messageOfBody({}, "client.loadTableFailed", { status: 502 }))
    .toBe("載入資料表失敗(502)");
});
