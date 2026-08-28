// Regenerates the screenshots embedded in README.md / docs/zh-TW/README.md.
//
// Prerequisites (see README "Local development"):
//   1. A running deployment to photograph — the compose stack from the
//      Quickstart works: `docker compose up -d` with a configured .env
//      (JWT_SECRET, BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD).
//   2. `npx playwright install chromium` once after installing devDeps.
//
// Run from frontend/:  npm run screenshots
//
// Environment:
//   BASE_URL          target front door            (default http://localhost:8080)
//   ADMIN_EMAIL       bootstrap admin login        (default: BOOTSTRAP_ADMIN_EMAIL)
//   ADMIN_PASSWORD    bootstrap admin password     (default: BOOTSTRAP_ADMIN_PASSWORD)
//   ADMIN_NEW_PASSWORD  set when the admin still has must_change_password
//                       (default docs-password-12345)
//
// The script is idempotent: it reuses an existing "demo-corpus" project,
// uploads only missing files, and ignores an already-created demo user.
// Query/Explore tabs are NOT captured — they need a finished index and a
// real LLM key, and fake graph data is worse than no screenshot.

import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:8080";
const ADMIN_EMAIL = process.env.ADMIN_EMAIL ?? process.env.BOOTSTRAP_ADMIN_EMAIL;
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD ?? process.env.BOOTSTRAP_ADMIN_PASSWORD;
const ADMIN_NEW_PASSWORD = process.env.ADMIN_NEW_PASSWORD ?? "docs-password-12345";
const PROJECT_NAME = "demo-corpus";
const ANALYST = {
  email: "analyst@example.com", display_name: "王分析", password: "analyst-docs-pass-1",
};
const OUT_ROOT = fileURLToPath(new URL("../../docs/assets/screenshots/", import.meta.url));
// Two captures per run: zh/ (the product's primary interface) feeds
// docs/zh-TW/README.md, en/ feeds README.md. All UI text comes from the
// locale dictionaries — keep these labels in sync with src/i18n/locales/.
const LOCALES = [
  {
    locale: "zh-TW", outDir: `${OUT_ROOT}zh/`,
    labels: {
      email: "電子郵件", password: "密碼", signIn: "登入系統",
      changeTitle: "首次登入請修改密碼", currentPassword: "目前密碼",
      newPassword: "新密碼", submit: "送出", filesTab: "檔案",
      settingsTab: "設定", adminUsers: "管理者 — 使用者",
    },
  },
  {
    locale: "en-US", outDir: `${OUT_ROOT}en/`,
    labels: {
      email: "Email", password: "Password", signIn: "Sign in",
      changeTitle: "Change your password before continuing",
      currentPassword: "Current password", newPassword: "New password",
      submit: "Submit", filesTab: "Files", settingsTab: "Settings",
      adminUsers: "Admin — Users",
    },
  },
];

const CORPUS = [
  {
    name: "q3-report.txt",
    body: `Q3 Platform Report\n\nRevenue grew 18% quarter over quarter, driven mainly by the\nknowledge-management product line. Customer interviews repeatedly\nmention two wins: faster onboarding of new analysts, and the citation\ntrails that make every answer auditable.\n\nOperations closed the two oldest security findings and moved the\nremaining batch ingest to the new pipeline. Churn stayed flat at 2.1%.\n`,
  },
  {
    name: "meeting-notes-1014.txt",
    body: `Meeting notes — 2026-10-14\n\nAttendees: 王分析, Dana Kim, R. Alvarez\n\nDecisions:\n1. Ship the shared graph view to all teams next sprint.\n2. Move corpus uploads behind the per-project quota check.\n3. Re-run the drift query benchmark after the indexer upgrade.\n\nAction items: Dana drafts the rollout note; R. Alvarez books the\nload-test window.\n`,
  },
];

function fail(msg) {
  console.error(`capture-screens: ${msg}`);
  process.exit(1);
}

if (!ADMIN_EMAIL || !ADMIN_PASSWORD) {
  fail("ADMIN_EMAIL / ADMIN_PASSWORD (or BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD) must be set");
}

async function api(path, { method = "GET", token, body, form } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) {
    payload = form; // FormData: fetch sets multipart boundary itself
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const r = await fetch(`${BASE_URL}/api${path}`, { method, headers, body: payload });
  const text = await r.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch { /* non-JSON error body */ }
  return { status: r.status, json };
}

async function seed() {
  const health = await fetch(`${BASE_URL}/api/health`).catch(() => null);
  if (!health?.ok) {
    fail(`no healthy API at ${BASE_URL} — start the stack first (docker compose up -d)`);
  }
  let login = await api("/auth/login", {
    method: "POST", body: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  });
  if (login.status === 401) {
    // Most likely a password change survived an earlier run.
    login = await api("/auth/login", {
      method: "POST", body: { email: ADMIN_EMAIL, password: ADMIN_NEW_PASSWORD },
    });
  }
  if (login.status !== 200) fail(`admin login rejected (${login.status}) — check ADMIN_EMAIL/ADMIN_PASSWORD`);

  let { access_token: token } = login.json;
  // If the first login flagged must_change_password, the browser step must
  // log in with the NEW password afterwards.
  const effectivePassword = login.json.user.must_change_password ? ADMIN_NEW_PASSWORD : ADMIN_PASSWORD;

  if (login.json.user.must_change_password) {
    const r = await api("/auth/change-password", {
      method: "POST", token,
      body: { current_password: ADMIN_PASSWORD, new_password: ADMIN_NEW_PASSWORD },
    });
    if (r.status !== 204) fail(`change-password -> ${r.status}`);
    console.log("seed: bootstrap admin password changed (must_change_password)");
  }


  // Project: reuse "demo-corpus" across runs.
  const list = await api("/projects", { token });
  let project = list.json?.find((p) => p.name === PROJECT_NAME);
  if (!project) {
    const r = await api("/projects", {
      method: "POST", token,
      body: { name: PROJECT_NAME, description: "Screenshots demo project", input_file_type: "text" },
    });
    if (r.status !== 201) fail(`create project -> ${r.status}: ${JSON.stringify(r.json)}`);
    project = r.json;
    console.log(`seed: project ${PROJECT_NAME} created`);
  } else {
    console.log("seed: reusing existing project");
  }

  // Files: upload only what is missing.
  const files = await api(`/projects/${project.id}/files`, { token });
  const existing = new Set((files.json?.files ?? []).map((f) => f.name));
  for (const f of CORPUS) {
    if (existing.has(f.name)) continue;
    const form = new FormData();
    form.append("file", new Blob([f.body], { type: "text/plain" }), f.name);
    const r = await api(`/projects/${project.id}/files`, { method: "POST", token, form });
    if (r.status !== 201) fail(`upload ${f.name} -> ${r.status}: ${JSON.stringify(r.json)}`);
    console.log(`seed: uploaded ${f.name}`);
  }

  // Masked env key for the Settings screenshot.
  const envPut = await api(`/projects/${project.id}/env`, {
    method: "PATCH", token, body: { key: "GRAPHRAG_API_KEY", value: "sk-docs-demo-not-a-real-key" },
  });
  if (envPut.status !== 204) fail(`set GRAPHRAG_API_KEY -> ${envPut.status}`);
  console.log("seed: GRAPHRAG_API_KEY set (masked on read)");

  // A second user row makes the AdminUsers screenshot representative.
  const u = await api("/admin/users", {
    method: "POST", token,
    body: { email: ANALYST.email, display_name: ANALYST.display_name, password: ANALYST.password },
  });
  console.log(u.status === 201 ? "seed: demo analyst created" : "seed: demo analyst already exists");

  return { token, project, effectivePassword };
}

async function capture({ project, effectivePassword }, { locale, outDir, labels }) {
  await mkdir(outDir, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    // i18n follows the browser locale, so pin it per run (plus a local
    // timezone for stable timestamps).
    locale,
    timezoneId: "Asia/Taipei",
  });
  const page = await context.newPage();
  // antd motion (tab ink bar, table fade-in) needs a beat before the shutter,
  // or screenshots catch half-rendered low-opacity tables / mid-slide ink bars.
  const settle = (ms = 700) => page.waitForTimeout(ms);
  const shot = (name, opts = {}) =>
    page.screenshot({ path: `${outDir}${name}.png`, ...opts });

  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await page.getByLabel(labels.email).waitFor({ timeout: 15000 });
  await settle(400);
  await shot("login");
  console.log(`capture: ${locale} login.png`);

  await page.getByLabel(labels.email).fill(ADMIN_EMAIL);
  await page.getByLabel(labels.password).fill(effectivePassword);
  await page.getByRole("button", { name: labels.signIn }).click();

  const modal = page.getByTitle(labels.changeTitle);
  if (await modal.isVisible().catch(() => false)) {
    await modal.getByLabel(labels.currentPassword).fill(effectivePassword);
    await modal.getByLabel(labels.newPassword).fill(ADMIN_NEW_PASSWORD);
    await modal.getByRole("button", { name: labels.submit }).click();
    console.log(`capture: ${locale} bootstrap password changed via UI`);
  }

  await page.waitForURL("**/projects", { timeout: 15000 });
  await page.getByRole("button", { name: PROJECT_NAME }).waitFor({ timeout: 15000 });
  await settle();
  await shot("projects");
  console.log(`capture: ${locale} projects.png`);

  await page.getByRole("button", { name: PROJECT_NAME }).click();
  await page.getByRole("tab", { name: labels.filesTab }).click();
  await page.getByText("q3-report.txt").waitFor({ timeout: 15000 });
  await settle();
  await shot("project-files");
  console.log(`capture: ${locale} project-files.png`);

  await page.getByRole("tab", { name: labels.settingsTab }).click();
  await page.getByRole("cell", { name: "GRAPHRAG_API_KEY" }).waitFor({ timeout: 15000 });
  await settle();
  // The env table sits below the YAML editor: full page so it is not cut.
  await shot("project-settings", { fullPage: true });
  console.log(`capture: ${locale} project-settings.png`);

  await page.getByRole("menuitem", { name: labels.adminUsers }).click();
  await page.getByText(ANALYST.email).waitFor({ timeout: 15000 });
  await settle();
  await shot("admin-users");
  console.log(`capture: ${locale} admin-users.png`);

  await browser.close();
}

const seeded = await seed();
for (const cfg of LOCALES) await capture(seeded, cfg);
console.log(`done → ${OUT_ROOT}{en,zh}/`);
