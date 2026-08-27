# GraphRAG Web UI

> 本文件為 [README.md](../../README.md) 的正體中文鏡像,英文版為權威版本。

[Microsoft GraphRAG](https://github.com/microsoft/graphrag) 的團隊網頁控制台:管理
專案、上傳語料、執行索引工作、查詢知識圖譜 —— 支援 local / global /
drift / basic 四種搜尋模式,全部透過 SSE 串流並附行內引用。可瀏覽 parquet
產物(entities、relationships、communities、documents、community reports、text units),
並在互動式 WebGL 檢視(圖譜)中探索圖形。它取代了 GraphRAG CLI
操作,讓非技術成員也能上手:從 `graphrag init` 到查詢執行,一切都藏在
登入、角色與每專案配額之後。

## 架構

簡要概覽(完整細節見[設計規格](../../docs/superpowers/specs/)):

- **前端** — React 19 SPA(Ant Design,介面文字為 zh-TW),以 Vite 建置並由
  nginx 提供服務;nginx 同時將 `/api` 反向代理至後端(SSE 友善:緩衝已關閉)。
- **後端** — FastAPI,分層為 `api` / `services` / `domain` / `adapters`。
  **graphrag 僅有兩個接觸點,且皆侷限於 `adapters/`:**
  - 索引以子程序執行 graphrag CLI —— 專案建立時執行 `graphrag init`,
    之後執行 `graphrag index` / `graphrag update` 工作(`adapters/index_runner.py`、
    `adapters/workspace.py`)。
  - 查詢/搜尋在隔離保護的模組內以 in-process 方式呼叫
    `graphrag.api`(`adapters/graphrag_search.py` —— 之所以隔離,是因為
    graphrag 的相依鏈會在 import 時將 `.env`/dotenv 載入 `os.environ`;
    adapter 會在該 import 前後快照並還原環境)。
- **資料庫** — PostgreSQL 16(SQLAlchemy async + asyncpg);Alembic 遷移會在
  API 啟動時自動執行。
- **工作區** — 每個專案在 `WORKSPACES_DIR` 下各有一個 `graphrag init` 工作區:
  上傳檔案落入 `input/`,索引輸出在 `output/`,每專案金鑰(例如
  `GRAPHRAG_API_KEY`)存於工作區 `.env`。

## 快速開始(15 分鐘)

1. **必要條件** — Docker + Docker Compose。Node **24** 與 Python 3.12 + uv
   僅在本機開發時需要(前端測試堆疊 jsdom/undici 需要 Node ≥ 22;
   CI 固定使用 24)。
2. **設定** — `cp .env.example .env`,接著設定三個 compose 強制變數:

   - `JWT_SECRET` — 一段長的隨機字串(JWT 簽署金鑰;請勿沿用開發預設值)
   - `BOOTSTRAP_ADMIN_EMAIL` — 必須使用可路由的網域,**不可**用 `.local`:
     登入驗證會拒絕特殊用途網域
   - `BOOTSTRAP_ADMIN_PASSWORD`

   全部 15 個變數與其預設值皆記錄於 [`.env.example`](../../.env.example)。
3. **啟動** — `docker compose up --build -d`。UI 位於 `http://localhost:8080`。
   Postgres 會先啟動;API 等待 PG 健康檢查通過後,自動執行 Alembic 遷移。
4. **首次登入** — 以 bootstrap 管理員身分登入;UI 會強制先變更密碼,
   之後才能進行其他操作。
5. **建立專案** — 選擇 `input_file_type`(`text` / `csv` / `json`)。此值
   在建立時即固定,並決定上傳接受的副檔名。
6. **上傳語料** — 檔案會進入專案工作區的 `input/`。單檔上限
   `UPLOAD_MAX_FILE_MB`,每專案配額 `PROJECT_QUOTA_MB`;超過任一上限 → 413。
7. **設定 LLM 金鑰** — 專案設定 → 環境:設定 `GRAPHRAG_API_KEY`(每專案
   各自持有,存於工作區 `.env`,回讀時遮罩顯示)。缺少此金鑰,索引工作會失敗。
8. **索引** — 工作 → 執行一項索引工作(method 為 `fast` 或 `standard`)。
   來自真實語料測試的提醒:在極小語料上,`fast` 方法可能會失敗
   ("Graph Pruning failed. No entities remain.")—— 小型測試語料的首次
   執行請改用 `standard`。可在即時日誌檢視器中追蹤進度。
9. **查詢** — 四種模式(`local`、`global`、`drift`、`basic`)全部以 SSE
   串流回應,並附行內引用。
10. **探索** — 產物資料表(entities / relationships / communities / documents /
    community_reports / text_units)與圖譜 WebGL 圖形檢視。

## 已知注意事項

- graphrag 固定在 `==3.1.0`:較新的 3.1.x 版本會拉入沒有 macOS x86_64
  wheel 的 `lancedb` 版本(見設計規格 §13)。
- 在 macOS 上,非互動式工作階段(SSH、agent 終端機)中 `osxkeychain`
  憑證輔助程式會擋住 Docker:`error getting credentials … keychain cannot
  be accessed` —— 連公開映像檔的 pull/build 都會失敗。請先解鎖鑰匙圈
  (`security -v unlock-keychain ~/Library/Keychains/login.keychain-db`),
  或在只需要公開映像檔時,暫時從 `~/.docker/config.json` 移除
  `"credsStore"`,結束後再還原。使用 OrbStack 時,`docker compose build`
  也受影響:daemon 會經由主機的 docker 設定解析 registry 憑證。

## 疑難排解

- **重新執行 `docker compose up` 後立刻出現「invalid email or password」** ——
  bootstrap 管理員**只在資料庫為空的首次啟動時建立**。若 Postgres volume
  裡已有管理員(例如兩次執行之間改過 `.env` 的 `BOOTSTRAP_ADMIN_PASSWORD`),
  生效的仍是*舊*密碼;API 啟動時會在日誌中指出被忽略的變數。想要乾淨的
  試用狀態:`docker compose down -v`(⚠ 會刪除所有資料)後再 `up`。
- **web 映像檔建置時 `npm ci` 出現 `ERESOLVE`** —— 前端以
  `frontend/.npmrc`(`legacy-peer-deps=true`)容忍 typescript 6 ↔
  openapi-typescript 的 peer 衝突,Dockerfile 必須在 `npm ci` 前把它複製進
  build stage。若你調整複製步驟,請讓 `.npmrc` 跟著 `package.json` 一起。
- **每份 index 工作日誌開頭的「LiteLLM:WARNING … could not pre-load
  bedrock/sagemaker response stream shape」** —— 無害:graphrag 的 LLM 層
  (litellm)會在 import 時探測選用的 AWS(botocore)整合。兩個 graphrag
  接觸點都預設 `LITELLM_LOG=ERROR`,讓這些雜訊不再進入工作日誌;需要
  除錯 LLM 呼叫時,自行 export `LITELLM_LOG`(例如 `DEBUG`)即可覆蓋。

## 本機開發

後端(需要 Docker —— 測試套件使用 testcontainers):

```
cd backend
uv sync
uv run pytest -m "not slow"
```

前端(Node 24):

```
cd frontend
npm ci
npm test
```

Vite 開發伺服器預設將 `/api` 代理至 `http://localhost:8000`;可用
`API_PROXY_TARGET` 將它指向其他前門(見 `frontend/vite.config.ts`)。

## 部署

- [`deploy/helm/graphrag-ui`](../../deploy/helm/graphrag-ui) — Helm chart;
  [`values.yaml`](../../deploy/helm/graphrag-ui/values.yaml) 記錄了每個環境變數,
  且 `NOTES.txt` 會在安裝時印出快速開始指引(zh-TW)。
- [`docker-compose.yml`](../../docker-compose.yml) — 單機部署;同樣的 15 個變數。

## OAuth2-Proxy 驗證(選用)

已經在運作 OIDC 供應商(Google、GitHub、Azure Entra、Keycloak……)的團隊,
可以在應用前部署 [oauth2-proxy](https://oauth2-proxy.github.io/),不必再
維護第二組憑證。此模式以部署為單位選用 —— 預設部署的行為與現況逐 byte
相同(設計:[規格](../../docs/superpowers/specs/2026-08-27-oauth2-proxy-auth-design.md))。

`AUTH_MODE=proxy` 改變三件事:

- **本機登入完全停用** —— `login` / `refresh` / `logout` /
  `change-password` 不註冊(404),也不存在應用程式 JWT。身分來自
  oauth2-proxy 在每個請求注入的 `X-Forwarded-Email` 標頭(顯示名稱:
  `X-Forwarded-Preferred-Username`);SPA 透過 `GET /api/auth/config`
  偵測模式。
- **標頭信任錨定於共享密鑰** —— `PROXY_AUTH_SECRET`(必填,≥ 32 字元;
  否則 API 會在啟動時直接退出)以 `X-Proxy-Secret` 標頭傳遞。缺少恰好
  一個相符值的請求一律拒絕,因此直接對 nginx 或 api 偽造
  `X-Forwarded-*` 標頭毫無用處。
- **使用者即時(JIT)建立** —— 首次出現的 email 會成為一列 `user`
  資料列,密碼雜湊不可用(本機登入對它永遠失效)。列在
  `PROXY_ADMIN_EMAILS`(逗號分隔)中的 email 在每個請求都維持
  `role=admin`。

### docker compose(選用 overlay)

```
docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml up -d
```

overlay 會新增 `auth` 服務(固定版
`quay.io/oauth2-proxy/oauth2-proxy:v7.15.4`)作為 `http://localhost:8080`
上唯一發佈的入口、取消發佈 web 埠(雙保險:沒有任何繞過 proxy 直達 api
的路徑)、在 api 上設定 `AUTH_MODE=proxy`,並讓 `/api/*` 回應 **401**
而非登入轉址,SPA 的 fetch 層才能反應。需要 Compose ≥ 2.24。`.env`
最小新增內容:

```dotenv
PROXY_ADMIN_EMAILS=you@example.com
PROXY_AUTH_SECRET=            # >= 32 字元 — 產生:openssl rand -hex 32
OAUTH2_PROXY_ISSUER_URL=https://idp.example.com/realms/main
OAUTH2_PROXY_CLIENT_ID=graphrag-ui
OAUTH2_PROXY_CLIENT_SECRET=
OAUTH2_PROXY_COOKIE_SECRET=   # 16/24/32 bytes 的 base64 — openssl rand -base64 32 | tr -d '\n'
OAUTH2_PROXY_REDIRECT_URL=http://localhost:8080/oauth2/callback
OAUTH2_PROXY_EMAIL_DOMAINS=example.com
```

### helm

設定 `proxyAuth.enabled: true`(需要 `ingress.enabled` 與具體的
`ingress.host`)。chart 會把 ingress 拆成兩個 —— `/api` Ingress 在驗證
失敗時把 **401** 原樣傳給 `fetch`,app Ingress 則將瀏覽器轉址到登入頁。
可選擇讓 chart 自帶 oauth2-proxy:

```yaml
proxyAuth:
  enabled: true
  issuerUrl: https://idp.example.com/realms/main
  clientId: graphrag-ui
  clientSecret: "..."        # 明文,或改用 existingSecret(見下)
  cookieSecret: "..."        # 16/24/32 bytes 的 base64
  authSecret: "..."          # PROXY_AUTH_SECRET — >= 32 字元
  adminEmails: ["you@example.com"]
  emailDomains: ["example.com"]   # 必填 — 見下方警告
  # existingSecret: my-secret     # 三個明文密鑰的替代方案;內容必須包含
  #   client-secret、cookie-secret、proxy-auth-secret 三個 key
```

……或僅透過 annotations 重用全叢集共用的 oauth2-proxy(chart 不自帶
oauth2-proxy;外部實例必須注入 `X-Forwarded-Email`、
`X-Forwarded-Preferred-Username`,以及等於 `authSecret` 的
`X-Proxy-Secret`):

```yaml
proxyAuth:
  enabled: true
  external:
    url: https://sso.example.com
  authSecret: "..."          # 必須與外部實例注入的密鑰一致
  adminEmails: ["you@example.com"]
  emailDomains: ["example.com"]
```

### email 網域允許清單是安全控制

> **`OAUTH2_PROXY_EMAIL_DOMAINS` is a security control, not a
> convenience.** oauth2-proxy authorizes emails via `--email-domain`
> (list, `*` = any) or `--authenticated-emails-file` (one per line).
> Because JIT provisioning (§5.2) turns "the IdP authenticated them" into
> "a `User` row exists", a public provider plus `*` means **anyone with a
> Google account self-provisions a `user` account** and can create
> projects and spend LLM budget. `.env.example` ships it uncommented with
> a placeholder domain and an explicit warning; helm mirrors it as
> `proxyAuth.emailDomains` (§7.2). This is the one oauth2-proxy setting
> the app's own threat model depends on (§8).

中文摘要:`OAUTH2_PROXY_EMAIL_DOMAINS` 是必要的安全控制,不是便利選項 ——
JIT 建立會把「IdP 通過了這個人」直接變成「一列 `User` 資料存在」;公用
供應商搭配 `*` 等於開放任何人自行註冊 `user` 帳號並消耗 LLM 預算
(§ 編號指上方連結的設計規格)。

### 注意事項

- **proxy → local 切換**:JIT 帳號的密碼雜湊不可用 —— 在管理員
  (AdminUsers)重設密碼之前,它們無法使用本機登入。
- **local → proxy 切換**:卡在 `must_change_password` 的使用者不會被
  鎖在外面 —— proxy 模式會略過密碼變更閘門。
- **`PROXY_ADMIN_EMAILS` 只升級、永不降級。** 列於其中的 email 在每個
  請求都會重新升為 admin;要先從變數中移除,才能在 AdminUsers 降級。
- **IdP 上的 email 變更即是新身分** —— 新地址會建立全新的資料列;舊資料
  列保有原本的專案成員資格。管理員需將新帳號重新加入專案,並停用舊資料列。
- **IdP 發出的特殊用途網域(`.local`、`.internal`)會被拒絕** ——
  email 驗證不接受它們,resolver 回 401,該帳號永遠不會建立
  (與 `BOOTSTRAP_ADMIN_EMAIL` 同一個陷阱)。
- **登出**會落在 oauth2-proxy 自己的登入頁:絕不轉址回應用(運作中的
  IdP 工作階段會默默重新登入),而 IdP 自身工作階段的結束屬於
  oauth2-proxy/供應商設定,不是應用的職責。

### 手動煙霧測試(需要真實 IdP)

在 compose overlay 啟動後執行:

1. 匿名:`curl -i http://localhost:8080/api/auth/me` → **401**(不是
   302 —— `api_routes` 生效)。
2. 瀏覽器開啟 `http://localhost:8080/` → IdP 登入 → 應用啟動;
   `/api/auth/me` 顯示 JIT 建立的使用者。
3. 偽造繞過:`docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml exec web curl -i -H "X-Forwarded-Email: admin@x" http://api:8000/api/auth/me` → **401**(沒有密鑰)。
4. 重複標頭的取代語意:經正門送出重複的 `X-Forwarded-Email` 標頭 →
   回應仍是 200,且只有「一個」一致的身分(oauth2-proxy 是取代,
   不是附加)。
5. SSE:執行一次查詢串流;訊框經 auth → web → api 順暢流動不卡住。
6. UI 登出 → 停在 oauth2-proxy 自己的登入頁(不會自動重新登入)。

## 貢獻與文件

- [CONTRIBUTING.md](../../CONTRIBUTING.md)
- 正體中文(zh-TW)鏡像:[`README.md`](README.md)
- 設計規格:[`docs/superpowers/specs/`](../../docs/superpowers/specs/)
