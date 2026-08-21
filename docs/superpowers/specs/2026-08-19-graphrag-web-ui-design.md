# GraphRAG Web UI 管理平台 — 設計文件

日期:2026-08-19(同日依設計審查修訂)
狀態:已與需求方確認;§13 列出仍需實測確認的項目

## 1. 目標

為 [Microsoft GraphRAG](https://microsoft.github.io/graphrag/) 提供團隊共用的 Web 管理介面,取代日常 CLI 操作(`graphrag init/index/update/query`)。

**功能範圍(四大區域,全部要):**

1. **Indexing 任務管理** — UI 啟動 index/update/dry-run、即時日誌、進度、取消、歷史
2. **設定檔編輯器** — 視覺化編輯 `settings.yaml` 與 `.env`
3. **查詢測試介面** — local/global/drift/basic 四種查詢模式,結構化結果與引用
4. **索引結果視覺化** — 瀏覽 `output/*.parquet`,知識圖譜視覺化

**非目標(Non-goals):**

- 不做 prompt-tune 的 UI 化(保留 CLI;未來可加)
- 不做多租戶 SaaS(單一團隊內部使用)
- 不做計費/用量分析儀表板(GraphRAG 本身不提供 API 成本彙總)
- 不自行維護 GraphRAG fork;以官方 PyPI 套件為準
- 不做 API 服務水平擴展:runner 與 workspace PVC 綁定,api 固定單副本(見 §8.2)

## 2. 需求摘要

| 面向 | 決定 |
|---|---|
| 使用情境 | 團隊共用服務(部署於伺服器) |
| 權限 | 個別帳號體系;區分擁有者與任務操作者 |
| 專案模型 | 多專案;一個專案 = 一個 graphrag root;成員共用 |
| 資料進入 | UI 上傳文件到專案 `input/`(txt/md/csv/json,每專案鎖定單一格式,見 §6.5) |
| 規模 | 10–50 使用者、同時 1–3 個 indexing 任務 |
| 部署 | docker-compose 與 Helm chart(K8s)皆須支援 |
| 技術棧 | FastAPI(Python)+ React(TS, Vite) |
| 應用資料庫 | PostgreSQL |

## 3. 核心架構決策

**後端如何執行 GraphRAG(方案 A,已確認):混合模式**

| 工作 | 整合方式 | 理由 |
|---|---|---|
| Indexing | spawn `graphrag` CLI 子程序 | 程序隔離(崩潰不影響 API)、版本相容風險最低(只依賴 CLI 介面)、貼近官方支援路徑 |
| 查詢 | import `graphrag.api` in-process(async) | 取得結構化結果(answer + context DataFrame),CLI 純文字輸出無法支撐查詢 UX |
| 產物瀏覽 | DuckDB 直接讀 `output/*.parquet` | 不經 GraphRAG,快速且無副作用 |

其他關鍵決策:

- **專案 = graphrag root 原封不動**:建立專案時後端執行 `graphrag init`(產生 settings.yaml、.env、prompts/ 與 `input/`——原始碼確認 init 會建立 input 目錄)。任何時候可退回 CLI 直接操作同一個目錄,零綁定。**代價**:檔案可能被 UI 以外的途徑改動,因此設定檔並行控制以「檔案內容 hash」為準,不能只信 DB 時間戳(見 §6.1)。
- **任務隊列用 Postgres**(`SELECT … FOR UPDATE SKIP LOCKED`),不引入 Redis/Celery:併發量不需要,少一個基礎設施組件。
- **同一專案的索引任務全域互斥**:index / update 同時作用於同一個 root 會互相覆寫 `output/`、`cache/`、`stats.json`,且是靜默損毀。以 DB 層 partial unique index 強制互斥(見 §5),不倚賴應用邏輯。
- **任務生死以 heartbeat 判定,不用 PID**:PID 只在同一個 PID namespace 內有意義,容器重啟後會被重用,靠 PID 存活檢查會誤判。runner 定期更新 `jobs.heartbeat_at`,reconciler 以逾時判定 `failed(interrupted)`;PID 僅供**同一個 runner 內部**送訊號。
- **取消走 DB 旗標,不走行程訊號直送**:cancel 只寫 `jobs.cancel_requested_at`,由持有該子程序的 runner 輪詢後執行 SIGTERM → 30s → SIGKILL。這樣在 rolling update 期間有兩個 pod、或 API 重啟後,取消語意仍然正確。
- **日誌串流走檔案 tail**:GraphRAG 原生寫 `logs/*.log`,stdout 同步捕獲至檔案。API 重啟後以 heartbeat 收斂 + 檔案 tail 重新掛回,狀態自動收斂,不依賴 process 記憶體。
- **Indexing 子程序一律跑在 backend pod 內**(不用 K8s Job API):兩種部署(docker-compose / K8s)行為完全一致。**代價有兩層**:(a) pod 重啟會中斷任務 → reconciler 標記 `failed(interrupted)`,GraphRAG 有 LLM cache,重跑成本部分吸收;(b) 子程序與 API 共用容器記憶體 limit,indexing 觸發 cgroup OOM 時可能連帶砍掉整個容器(含 API 與其他人的任務)。緩解措施見 §8.2 / §10。

## 4. 系統架構

```
┌──────────────┐        ┌───────────────────────────────────────┐
│  React SPA   │  HTTP  │  FastAPI backend (single pod)         │
│  Vite + TS   │──────▶ │  ├ Auth      : JWT + user mgmt        │
│  Ant Design  │  SSE   │  ├ Projects  : CRUD/members/files     │
└──────────────┘        │  ├ Jobs      : PG queue + subprocess  │
                        │  ├ Query     : graphrag.api (async)   │
                        │  ├ Artifacts : DuckDB over parquet    │
                        │  └ Runner    : asyncio loop+heartbeat │
                        └────────┬────────────────────┬─────────┘
                                 │                    │
                    ┌────────────┴─────┐   ┌──────────┴──────────────┐
                    │   PostgreSQL     │   │  Filesystem (PVC/volume)│
                    │   users          │   │  /data/workspaces/<id>/ │
                    │   projects       │   │    settings.yaml  .env  │
                    │   project_members│   │    prompts/  input/     │
                    │   jobs           │   │    output/  cache/      │
                    │   settings_ver.  │   │    logs/                │
                    └──────────────────┘   └─────────────────────────┘
```

## 5. 資料模型(PostgreSQL)

| 表 | 重點欄位 |
|---|---|
| `users` | email(唯一)、password_hash(argon2)、display_name、role(admin/user)、is_active、created_at |
| `projects` | name、slug(唯一)、description、owner_id、input_file_type(text/csv/json,建立後鎖定)、created_at |
| `project_members` | (project_id, user_id) 唯一、role(owner/editor/viewer) |
| `jobs` | project_id、type(index/update/dry_run)、method(standard/fast)、argv(實際執行的完整命令)、status、queued_by、**worker_id**、pid、**heartbeat_at**、**cancel_requested_at**、exit_code、error、stats(jsonb)、queued_at/started_at/finished_at |
| `settings_versions` | project_id、content(yaml 文本)、**content_hash**、saved_by、created_at(設定檔版本備份,供回復) |
| `audit_log` | actor_id、action、target_type、target_id、payload(jsonb)、created_at(專案/成員/使用者的建立刪除與權限異動) |

**必要約束:**

```sql
-- 同一專案同時只能有一個索引類任務(queued 或 running)
CREATE UNIQUE INDEX jobs_one_active_per_project
  ON jobs (project_id)
  WHERE status IN ('queued', 'running');
```

`dry_run` 不進隊列(見 §6.3),因此不受此約束影響。

**Job 狀態機:** `queued → running → succeeded | failed | failed(interrupted) | cancelled`

- `cancel_requested_at` 已設但仍 running → UI 顯示 `cancelling`
- exit_code 137 → error 標註「疑似記憶體不足(OOM)」

**權限矩陣:**

| 操作 | 系統 admin | 專案 owner | editor | viewer |
|---|---|---|---|---|
| 使用者管理 | ✓ | | | |
| 建立/刪除專案 | ✓ | ✓(自己的) | | |
| 成員管理 | ✓ | ✓ | | |
| 改設定/上傳檔案 | ✓ | ✓ | ✓ | |
| 啟動任務/取消 | ✓ | ✓ | ✓ | |
| 查詢/看結果與日誌 | ✓ | ✓ | ✓ | ✓ |

- 擁有者為單一且固定(建立者);成員角色僅可授予 editor/viewer(2026-08-20 需求方裁定)

**成本護欄**(查詢與索引都會實際花錢,權限之外另設):

- 查詢:per-user + per-project 速率限制(可設定,預設 30 次/小時/人)
- 啟動 index/update:前端二次確認,並顯示上一次執行的 runtime 與文件數作為參考(**2026-08-21 實測**:3.1.0 的 `stats.json` 只有 runtime/記憶體/文件數,**沒有 token 用量欄位**,原設計不可行)

## 6. 後端設計

### 6.1 API 面

- `POST /api/auth/login|refresh|logout`;admin 使用者 CRUD(`/api/admin/users`)
- `/api/projects`:CRUD、成員管理
- `/api/projects/{id}/files`:上傳/列表/刪除 → `input/`(格式須符合專案 `input_file_type`;單檔與總量上限、專案配額可設定)
- `/api/projects/{id}/settings`:
  - `GET` 回傳 `{content, content_hash}`(hash 由磁碟上的實際檔案內容計算)
  - `PUT` 帶 `expected_hash`;與磁碟現況不符 → 409 + 回傳目前內容供前端 diff。**不使用 DB `updated_at` 做樂觀鎖**,因為檔案可能被 CLI 從旁改動(§3 的零綁定保證)
  - 寫入成功時自動存一份 `settings_versions`
- `/api/projects/{id}/env`:**per-key 操作**,不做整份覆寫
  - `GET` 回傳 key 清單與遮罩值(`sk-****`),永不回明文
  - `PATCH {key: value}` 設定/更新單一 key;`DELETE /env/{key}` 移除
  - 這樣避免「前端拿遮罩值整份 PUT 回來,把真 key 覆寫成 `sk-****`」
- `/api/projects/{id}/jobs`:POST 啟動(index/update + method)、歷史列表
- `POST /api/projects/{id}/dry-run`:同步執行 `graphrag index --dry-run`,不進隊列,直接回傳驗證結果
- `GET /api/jobs/{id}/logs`:SSE 即時日誌(支援 `Last-Event-ID` 以位元組 offset 續傳);`POST /api/jobs/{id}/cancel`:寫入 `cancel_requested_at`,立即回 202
- `/api/projects/{id}/query`:method(local/global/drift/basic)+ query + 參數
  - `POST .../query` 一次性回覆(短查詢)
  - `GET .../query/stream` SSE 串流(預設路徑;global search 常見 30–90s,見 §6.4)
- `/api/projects/{id}/artifacts/{table}`:entities/relationships/communities/community_reports/text_units/documents;分頁 + 篩選(社群、類型、關鍵字)。專案有 running 索引任務時,回應標記 `stale: true`,前端顯示「索引進行中,結果可能不完整」
- `GET /api/health`:輕量 liveness(僅檢查行程與 DB);`GET /api/ready`:readiness,含 graphrag 版本與 workspace 掛載檢查。**graphrag 版本在啟動時偵測一次後快取**,不在每次 probe fork 子程序

### 6.2 設定檔編輯器(雙模式)

- **表單模式**:常用區塊(LLM、embedding、chunking、storage、vector_store)結構化表單
- **YAML 模式**:原始碼編輯;寫入前做 YAML schema 驗證
- 兩種模式皆可觸發驗證,一律走 `graphrag index --dry-run`(`graphrag update` **沒有** `--dry-run` 選項)
- 每次保存留版本,可回復;回復也是一次帶 `expected_hash` 的寫入

### 6.3 Runner(任務執行器)

- asyncio 背景迴圈:搶佇列(`FOR UPDATE SKIP LOCKED`)→ 檢查全域同時上限(預設 2,可設定)→ `create_subprocess_exec` 執行 graphrag CLI
- **CLI 參數映射**(`--method` enum 雖含 `standard|fast|standard-update|fast-update`,但 **update 指令必須傳 `standard|fast`**:CLI 內部 `_get_method()` 會自動附加 `-update` 後綴,直接傳 `standard-update` 會組出無效的 `standard-update-update` pipeline——原始碼確認):

  | job.type | job.method | 實際命令 |
  |---|---|---|
  | index | standard | `graphrag index --root <ws> --method standard` |
  | index | fast | `graphrag index --root <ws> --method fast` |
  | update | standard | `graphrag update --root <ws> --method standard`(內部執行 standard-update pipeline) |
  | update | fast | `graphrag update --root <ws> --method fast`(內部執行 fast-update pipeline) |
  | dry_run | — | `graphrag index --root <ws> --dry-run`(同步,不進隊列) |
- stdout/stderr 即時寫入該 job 的 log 檔;結束時記錄 exit_code、掃描 stats 檔進 stats 欄位。**stats 檔路徑依 job.type**(2026-08-21 實測定案,見 §13 實測表):index → `output/stats.json`;update → `update_output/<timestamp>/delta/stats.json`(**merge 後 `output/stats.json` 不回寫**)。**stats.json 在每個 workflow 完成後增量寫入**,Indexing 階段可據此做真實進度(已完成 workflow 數 / 總數),不必只靠日誌行數
- **heartbeat**:running 期間每 10s 更新 `jobs.heartbeat_at` 與 `worker_id`
- **啟動時 reconcile**:DB 為 running 但 `heartbeat_at` 逾時(預設 60s)→ `failed(interrupted)`
- **取消**:迴圈每 5s 檢查自己持有的 job 是否被設定 `cancel_requested_at` → SIGTERM → 30s 寬限 → SIGKILL → 標記 `cancelled`

### 6.4 查詢服務

`graphrag.api` 的查詢全部是 async 介面,**不需要 threadpool**:非串流的四個(`local_search` 等)是 `async def` 直接 `await`;`*_streaming` 四個是同步函式回傳 `AsyncGenerator`,以 `async for chunk in ...` 消費。本設計預設走串流端點,主要使用後者。簽章形如:

```python
async def local_search(config, entities, communities, community_reports,
                       text_units, relationships, covariates,
                       community_level, response_type, query,
                       callbacks=None) -> tuple[response, context_data]
```

重點:**沒有「以 settings 建構 search 物件」這種 API**——呼叫端必須自己 `pd.read_parquet()` 載入 DataFrame 傳進去。各模式所需資料:

| 模式 | 需要的 parquet |
|---|---|
| basic | text_units |
| local | entities, communities, community_reports, text_units, relationships, (covariates) |
| drift | entities, communities, community_reports, text_units, relationships |
| global | entities, communities, community_reports |

因此設計上必須有:

- **per-project DataFrame 快取層**:以 `output/` 各檔案的 (mtime, size) 為失效鍵;LRU 淘汰,總記憶體上限可設定(預設 2 GB)。沒有這層則每次查詢重讀數百 MB parquet,不可用
- 記憶體預算需與 §8.2 的容器 limit 一併規劃(indexing 子程序 + 查詢快取共用同一個 limit)
- **引用(citations)需要自行解析**:API 只回 `(response, context_data)`,答案內文是 `[Data: Entities (12, 34); Reports (5)]` 這類行內標記,沒有現成的 citation 物件。實作需 parse 標記 → 對 `context_data` 的 DataFrame join 回實體/關係/報告的實際內容 → 組成 `citations`。**這是 Phase 4 最大的一塊工作量**
- **串流**:預設走對應的 streaming 端點,經 SSE 推給前端,避免 ingress read timeout
- LLM key 從專案 `.env` 載入,不進 DB
- **vector store 隔離**:預設 LanceDB 落在專案 `output/` 下沒有問題;若團隊改用 Azure AI Search / CosmosDB,container name 會跨專案相撞 → 設定編輯器對 vector store container name 做 per-project 唯一性校驗
- 回應統一結構:`{answer, context, citations, timings}`;錯誤帶日誌摘錄

### 6.5 檔案輸入格式

GraphRAG 的 `input.type` 是單一型別 + `input.file_pattern`(regex),一個 root 的 `input/` 不能任意混放格式。完整 enum 為 text/csv/json/jsonl/markitdown/parquet(原始碼 `graphrag_input/input_config.py`;本產品鎖定 text/csv/json)。注意 `InputConfig` 為 `extra="allow"`,**寫錯鍵名會被靜默忽略**,寫入後必須解析驗證。因此:

- 專案建立時選定 `input_file_type`(text / csv / json),寫入 `projects` 並同步 settings.yaml
- 上傳白名單依專案設定收斂(text → txt/md;csv → csv;json → json)
- 變更格式需在設定編輯器明確操作,並提示既有 `input/` 內容需清理

## 7. 前端設計

- **技術棧**:React 19 + Vite + TypeScript、Ant Design 6(管理台表格/表單密集)、TanStack Query v5、React Router v7、Zustand(auth state)。*(2026-08-19 修訂:原定 React 18 + AntD 5 + Router 6,實作時 npm 已上 stable 最新 majors 且 build/tsc/test 全綠,經需求方確認保留新版)*
- **頁面**:登入、專案列表、專案詳情(tab:Overview / Files / Settings / Jobs / Query / Explore)、Admin 使用者管理
- **日誌 viewer**:虛擬捲動 + 自動跟隨 + 暫停;斷線以 `Last-Event-ID` 續傳
- **查詢介面**:SSE 串流逐字顯示,答案下方以可展開卡片呈現 citations(對應 §6.4 解析結果)
- **設定編輯器**:409 衝突時顯示 diff 與「重新載入 / 覆寫」兩個明確選項
- **圖譜視覺化(Phase 5)**:react-sigma + graphology(WebGL,萬級節點),依 community 著色,節點搜尋/過濾;表格用 Ant Table 伺服器端分頁
- **目錄結構**:feature 導向(`features/projects`、`features/jobs`…),共用元件放 `shared/`

## 8. 部署

### 8.1 docker-compose(開發/小規模)

服務:`postgres`、`api`(含 runner,掛 workspace volume)、`web`(nginx serve SPA + 反代 `/api`,`proxy_buffering off`)

### 8.2 Helm chart(K8s)

- `api` Deployment(+ PVC 掛 `/data/workspaces`)+ Service
  - **`replicas: 1` 且 `strategy: Recreate`**:runner 在 pod 內、PVC 為 RWO,rolling update 期間出現兩個 pod 會搶同一份 workspace
  - `terminationGracePeriodSeconds` 拉長(預設 120)+ preStop hook,讓進行中的任務有機會收尾或乾淨地標記中斷
  - **資源**:indexing 子程序、API、查詢 DataFrame 快取共用同一個容器 limit。limits 須依 indexing 峰值抓(values 提供建議值與調整說明);OOM 時 exit code 137 會被特判並回報
- `web` Deployment + Service
- Ingress:**SSE 需要 buffering 關閉**(nginx annotation `nginx.ingress.kubernetes.io/proxy-buffering: "off"`、長 read timeout)
- Postgres:values 可切換 — 內建(dependency chart)或外部 DB(連線字串)
- 健康:`/api/health` 作 liveness、`/api/ready` 作 readiness

### 8.3 設定

所有環境差異走環境變數(DATABASE_URL、WORKSPACES_DIR、MAX_CONCURRENT_JOBS、JWT secret、上傳上限(UPLOAD_MAX_FILE_MB)、保留天數、專案配額(PROJECT_QUOTA_MB)、查詢快取上限等),兩種部署共用同一組變數名。

首任 admin 由 `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` 於首次啟動建立,建立後強制改密碼。

### 8.4 Auth 細節

- access token 有效期 15 分鐘;refresh token 7 天,**輪替式**(每次 refresh 換發並作廢舊的)
- refresh token 存 DB(hash),支援登出與 admin 停用帳號時即刻撤銷
- 密碼重設:MVP 由 admin 重設(不做郵件流程,與非目標一致)

## 9. 代碼組織(Clean Architecture 精神)

原則:**依賴方向由外向內;graphrag 與基礎設施細節隔離在 adapter 後面**。不追求嚴格分層儀式,取其精神:

```
backend/
  src/graphrag_ui/
    domain/          # 純邏輯:Job 狀態機、權限規則、設定驗證(無 IO)
    services/        # 使用案例:ProjectService、JobService…(介面注入)
    adapters/        # 實作:Postgres repos、FS workspace、GraphragCLI、
                     #   GraphragQuery、DuckDB artifacts
    api/             # FastAPI routes、schemas、auth(JWT)
    runner/          # asyncio 任務執行器
    migrations/      # alembic
```

- domain/services 不 import FastAPI、SQLAlchemy、graphrag;透過 interface 由 adapters 實作
- Graphrag 整合(CLI 參數映射、api 呼叫、citation 解析)全部收在 `adapters/graphrag/`,版本升級的影響範圍被隔離
- DB schema 一律經 alembic migration,不手動改

### 每輪迭代的 code smell 檢查(流程要求)

每個實作階段收尾時,執行 smell 清單審視並決定是否重構(寫進各階段計畫的驗收項目):

- 過長函式/類別、重複代碼、feature envy
- 跨層洩漏(graphrag 型別出現在 domain/services、route 內寫業務邏輯)
- 單一檔案過大(>400 行視為訊號)、死代碼、過度抽象(只有一個實作且無第二個實作預期的 interface)

## 10. 錯誤處理與資源治理

- 任務失敗:exit code + stderr 進 `jobs.error`;exit 137 標註疑似 OOM
- API/pod 重啟:reconciler 以 heartbeat 逾時收斂孤兒任務(見 6.3)
- 設定並行編輯衝突:409 + 前端 diff 提示(以檔案 hash 判定,涵蓋 CLI 從旁改動)
- 上傳:格式白名單、大小上限、專案配額、path traversal 全程防護(所有檔案 API 以 project root 為基準做規範化檢查)
- `.env` 秘密永不回明文;per-key 更新避免誤覆寫
- 查詢逾時/LLM 錯誤:結構化錯誤 + 日誌摘錄
- graphrag CLI 缺失/過舊:readiness 反映,前端於啟動任務時前置檢查並提示

**保留與配額**(PVC 會被無限成長的日誌與 cache 撐爆,爆掉時通常是索引寫到一半失敗):

| 對象 | 政策 |
|---|---|
| job 日誌 | 保留 N 天(預設 30),超過清除;失敗任務的日誌延長保留 |
| `cache/` | 每專案上限(可設定),超過時提示手動清理 |
| `update_output/` | update 會留下 `<timestamp>/{delta,previous}` 目錄(原始碼確認);成功 merge 後依保留政策清除舊 timestamp 目錄 |
| `input/` + `output/` | 每專案儲存配額,上傳與啟動任務前預檢 |
| 磁碟水位 | readiness 檢查,低於門檻時拒絕新任務並告警 |

**備份**:PostgreSQL 定期 dump;workspace PVC 依部署環境的快照機制(Helm values 文件說明,不自行實作)。

## 11. 測試

- **單元**:Job 狀態機、權限矩陣、設定驗證、path 防護、CLI 參數映射、citation 解析
- **整合**:pytest + httpx AsyncClient + 臨時 workspace;真實 graphrag 小語料 dry-run 與完整 index(標記 slow,夜跑)
- **前端**:vitest + RTL 關鍵組件(設定表單、任務狀態、日誌 viewer、查詢串流)
- **Smoke(每階段交付前)**:docker-compose 起全套,UI 手動/腳本走過該階段主流程

## 12. 階段劃分(各自獨立實作計畫)

1. **Foundation-A** — scaffold(前後端 + compose + Helm)、alembic、auth(含 token 輪替與 bootstrap admin)、使用者管理、專案 CRUD + 成員 + 權限矩陣
2. **Foundation-B** — 檔案上傳(含配額與 path 防護)、設定檔編輯器(雙模式 + hash 樂觀鎖 + 版本)、`.env` per-key API、dry-run 驗證
3. **Indexing** — 任務啟動/隊列/per-project 互斥/heartbeat/即時日誌/取消/歷史/reconcile/保留政策
4. **Query** — 四種查詢模式 + DataFrame 快取層 + 串流 + citation 解析與呈現
5. **Explore** — parquet 表格瀏覽 + 知識圖譜視覺化

每階段含:實作 → smoke → smell 檢查/重構 →(必要時)文件更新。Helm chart 於 Phase 1 建立(與 compose 同步維護),避免最後補課。

## 13. 待確認事項(實作前需實測)

| # | 項目 | 為何重要 | 何時確認 |
|---|---|---|---|
| 1 | `graphrag update` 的輸出落點 | **已由原始碼確認**:`DEFAULT_UPDATE_OUTPUT_BASE_DIR="update_output"`,`run_pipeline` 建立 `update_output/<timestamp>/{delta,previous}`(previous 為舊索引備份)後 merge 回 `output/`。仍需以真實語料確認 merge 後 `output/` 完整性與失敗中途的恢復行為,否則 Phase 4/5 會讀到過期資料 | **Phase 3(Indexing)開工前**,以真實小語料實測 |
| 2 | stats 檔位置與增量寫入節奏 | `jobs.stats` 與進度條依賴;index 的 `output/stats.json` 已由原始碼確認,update 的落點與 merge 後回寫行為待實測 | 同上 |
| 3 | 目標 graphrag 版本鎖定 | CLI 介面與 `graphrag.api` 簽章皆隨版本變動;需在 pyproject 鎖定並記錄於此 | **已鎖定 `graphrag==3.1.0`**(Phase 1,2026-08-19):`backend/pyproject.toml` pin `==3.1.0`。最新版 3.1.1 因 `graphrag-vectors` 硬依賴 `lancedb~=0.34.0`(無 macOS x86_64 wheel、無 sdist)無法在 Intel Mac 開發機安裝,故取 3.1.x 線中可跨平台安裝的最新版(lancedb 0.24.1 有 mac x86_64/arm64 + linux wheel) |
| 4 | indexing 記憶體峰值(以團隊實際語料量測) | 決定容器 limits 與查詢快取上限的分配 | Phase 3 |

### 2026-08-21 Phase 3 開工前實測(真實語料,graphrag 3.1.0,gpt-4o-mini)

| # | 項目 | 實測結果 |
|---|---|---|
| 1 | `graphrag update` 輸出落點 | `update_output/<ts>/{delta,previous}` 確認:delta 含 6 parquet + context.json + stats.json;previous 含 6 parquet。merge 回 `output/` 正確(documents 3→4,含新增檔) |
| 2 | stats 位置與節奏 | index → `output/stats.json`、update → `update_output/<ts>/delta/stats.json`,**每個 workflow 完成後增量寫入**(實測 349b→798b→1870b→3647b→4102b)。**merge 後 `output/stats.json` 不回寫**(仍為上次 index 內容)→ `jobs.stats` 依 job.type 取路徑;Phase 4/5 不得以 `output/stats.json` 判斷新舊 |
| 3 | 記憶體峰值初值 | 560B 語料:standard 572MB、fast 644MB(RSS)→ api pod limit 建議 ≥ 2GB;真實語料量測留 Phase 3 Task 內執行 |
| 4 | fast method 邊角 | `extract_graph_nlp` 在微語料失敗:「Graph Pruning failed. No entities remain.」EXIT 1(錯誤路徑可依賴);fast 首跑會**即時下載 NLTK 資料** → 容器 image 需預載 `nltk_data` |
