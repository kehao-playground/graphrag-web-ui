# GraphRAG Web UI 管理平台 — 設計文件

日期:2026-08-19
狀態:已與需求方確認

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

## 2. 需求摘要

| 面向 | 決定 |
|---|---|
| 使用情境 | 團隊共用服務(部署於伺服器) |
| 權限 | 個別帳號體系;區分擁有者與任務操作者 |
| 專案模型 | 多專案;一個專案 = 一個 graphrag root;成員共用 |
| 資料進入 | UI 上傳文件到專案 `input/`(txt/md/csv/json) |
| 規模 | 10–50 使用者、同時 1–3 個 indexing 任務 |
| 部署 | docker-compose 與 Helm chart(K8s)皆須支援 |
| 技術栈 | FastAPI(Python)+ React(TS, Vite) |
| 應用資料庫 | PostgreSQL |

## 3. 核心架構決策

**後端如何執行 GraphRAG(方案 A,已確認):混合模式**

| 工作 | 整合方式 | 理由 |
|---|---|---|
| Indexing | spawn `graphrag` CLI 子程序 | 程序隔離(崩潰不影響 API)、版本相容風險最低(只依賴 CLI 介面)、貼近官方支援路徑 |
| 查詢 | import graphrag 套件 in-process | 取得結構化結果(answer + context + citations),CLI 純文字輸出無法支撐查詢 UX |
| 產物瀏覽 | DuckDB 直接讀 `output/*.parquet` | 不經 GraphRAG,快速且無副作用 |

其他關鍵決策:

- **專案 = graphrag root 原封不動**:建立專案時後端執行 `graphrag init`。任何時候可退回 CLI 直接操作同一個目錄,零綁定。
- **任務隊列用 Postgres**(`SELECT … FOR UPDATE SKIP LOCKED`),不引入 Redis/Celery:併發量不需要,少一個基礎設施組件。
- **日誌串流走檔案 tail**:GraphRAG 原生寫 `logs/*.log`,stdout 同步捕獲至檔案。API 重啟後以 PID 存活檢查 + 檔案 tail 重新掛回,狀態自動收斂,不依賴 process 記憶體。
- **Indexing 子程序一律跑在 backend pod 內**(不用 K8s Job API):兩種部署(docker-compose / K8s)行為完全一致。代價:pod 重啟會中斷任務 → reconciler 標記 `failed(interrupted)`;GraphRAG 有 LLM cache,重跑成本部分吸收。

## 4. 系統架構

```
┌──────────────┐        ┌─────────────────────────────────────┐
│  React SPA   │  HTTP  │  FastAPI backend(單一服務)           │
│  Vite + TS   │──────▶ │  ├ Auth:JWT + 使用者管理              │
│  Ant Design  │  SSE   │  ├ Projects:CRUD/成員/檔案/設定       │
└──────────────┘        │  ├ Jobs:Postgres 隊列 + 子程序管理     │
                        │  ├ Query:graphrag 函式庫 in-process   │
                        │  ├ Artifacts:DuckDB 讀 parquet        │
                        │  └ Runner:asyncio 背景迴圈            │
                        └───────┬──────────────┬───────────────┘
                          PostgreSQL │     檔案系統(PVC / volume)
                        (users/projects/   /data/workspaces/<project_id>/
                         jobs 中繼資料)     settings.yaml .env prompts/
                                            input/ output/ logs/
```

## 5. 資料模型(PostgreSQL)

| 表 | 重點欄位 |
|---|---|
| `users` | email(唯一)、password_hash(argon2)、display_name、role(admin/user)、created_at |
| `projects` | name、slug(唯一)、description、owner_id、created_at |
| `project_members` | (project_id, user_id) 唯一、role(owner/editor/viewer) |
| `jobs` | project_id、type(index/update/dry_run)、method(standard/fast)、status、queued_by、pid、exit_code、error、stats(jsonb)、queued_at/started_at/finished_at |
| `settings_versions` | project_id、content(yaml 文本)、saved_by、created_at(設定檔版本備份,供回復) |

**Job 狀態機:** `queued → running → succeeded | failed | failed(interrupted) | cancelled`

**權限矩陣:**

| 操作 | 系統 admin | 專案 owner | editor | viewer |
|---|---|---|---|---|
| 使用者管理 | ✓ | | | |
| 建立/刪除專案 | ✓ | ✓(自己的) | | |
| 成員管理 | ✓ | ✓ | | |
| 改設定/上傳檔案 | ✓ | ✓ | ✓ | |
| 啟動任務/取消 | ✓ | ✓ | ✓ | |
| 查詢/看結果與日誌 | ✓ | ✓ | ✓ | ✓ |

## 6. 後端設計

### 6.1 API 面

- `POST /api/auth/login|refresh`;admin 使用者 CRUD(`/api/admin/users`)
- `/api/projects`:CRUD、成員管理
- `/api/projects/{id}/files`:上傳/列表/刪除 → `input/`(格式白名單 txt/md/csv/json;單檔與總量上限可設定)
- `/api/projects/{id}/settings`:GET/PUT settings.yaml(樂觀並行:updated_at 比對;寫入時自動存版本);`.env` 寫入 only,回傳一律遮罩(`sk-****`)
- `/api/projects/{id}/jobs`:POST 啟動(index/update + method;dry-run 為獨立 type)、歷史列表
- `GET /api/jobs/{id}/logs`:SSE 即時日誌;`POST /api/jobs/{id}/cancel`:SIGTERM → 寬限 → SIGKILL
- `/api/projects/{id}/query`:method(local/global/drift/basic)+ query + 參數 → 結構化回覆
- `/api/projects/{id}/artifacts/{table}`:entities/relationships/communities/reports/text_units/documents;分頁 + 篩選(社群、類型、關鍵字)
- `GET /api/health`:含 graphrag CLI 版本檢查

### 6.2 設定檔編輯器(雙模式)

- **表單模式**:常用區塊(LLM、embedding、chunking、storage)結構化表單
- **YAML 模式**:原始碼編輯;寫入前做 YAML schema 驗證
- 兩種模式皆可觸發 `--dry-run` 驗證;每次保存留版本,可回復

### 6.3 Runner(任務執行器)

- asyncio 背景迴圈:搶佇列 → 檢查同時上限(預設 2,可設定)→ `create_subprocess_exec` 執行 `graphrag index --root <ws> …`
- stdout/stderr 即時寫入該 job 的 log 檔;結束時記錄 exit_code、掃描 `output/stats.json` 進 stats 欄位
- 啟動時 reconcile:DB 為 running 但 PID 已死 → `failed(interrupted)`
- 取消:SIGTERM → 30s 寬限 → SIGKILL

### 6.4 查詢服務

- 以專案 settings 建構 search 物件(graphrag 庫),在 threadpool 執行(sync 函式庫),逾時保護
- LLM key 從專案 `.env` 載入,不進 DB
- 回應統一結構:`{answer, context, citations, timings}`;錯誤帶日誌摘錄

## 7. 前端設計

- **栈**:React 18 + Vite + TypeScript、Ant Design 5(管理台表格/表單密集,最穩)、TanStack Query、React Router、Zustand(auth state)
- **頁面**:登入、專案列表、專案詳情(tab:Overview / Files / Settings / Jobs / Query / Explore)、Admin 使用者管理
- **日誌 viewer**:虛擬捲動 + 自動跟隨 + 暫停
- **圖譜視覺化(Phase 4)**:react-sigma + graphology(WebGL,萬級節點),依 community 著色,節點搜尋/過濾;表格用 Ant Table 伺服器端分頁
- **目錄結構**:feature 導向(`features/projects`、`features/jobs`…),共用元件放 `shared/`

## 8. 部署

### 8.1 docker-compose(開發/小規模)

服務:`postgres`、`api`(含 runner,掛 workspace volume)、`web`(nginx serve SPA + 反代 `/api`)

### 8.2 Helm chart(K8s)

- `api` Deployment(+ PVC 掛 `/data/workspaces`)+ Service
- `web` Deployment + Service
- Ingress:**SSE 需要 buffering 關閉**(nginx annotation `nginx.ingress.kubernetes.io/proxy-buffering: "off"`、長 read timeout)
- Postgres:values 可切換 — 內建(dependency chart)或外部 DB(連線字串)
- 資源建議:indexing 在 pod 內執行,api 容器需預留 CPU/記憶體(requests/limits 於 values)
- 健康:`/api/health` 作 liveness/readiness probe

### 8.3 設定

所有環境差異走環境變數(DATABASE_URL、WORKSPACES_DIR、MAX_CONCURRENT_JOBS、JWT secret、上傳上限等),兩種部署共用同一組變數名。

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
```

- domain/services 不 import FastAPI、SQLAlchemy、graphrag;透過 interface 由 adapters 實作
- Graphrag 整合(CLI 參數、函式庫呼叫)全部收在 `adapters/graphrag/`,版本升級的影響範圍被隔離

### 每輪迭代的 code smell 檢查(流程要求)

每個實作階段收尾時,執行 smell 清單審視並決定是否重構(寫進各階段計畫的驗收項目):

- 過長函式/類別、重複代碼、feature envy
- 跨層洩漏(graphrag 型別出現在 domain/services、route 內寫業務邏輯)
- 單一檔案過大(>400 行視為訊號)、死代碼、過度抽象(只有一個實作且無第二個實作預期的 interface)

## 10. 錯誤處理

- 任務失敗:exit code + stderr 進 `jobs.error`,日誌檔永久保留於 workspace
- API/pod 重啟:reconciler 收斂孤兒任務(見 6.3)
- 設定並行編輯衝突:409 + 前端提示重新載入
- 上傳:格式白名單、大小上限、path traversal 全程防護(所有檔案 API 以 project root 為基準做規範化檢查)
- `.env` 秘密永不回明文
- 查詢逾時/LLM 錯誤:結構化錯誤 + 日誌摘錄
- graphrag CLI 缺失/過舊:health 端點反映,前端於啟動任務時前置檢查並提示

## 11. 測試

- **單元**:Job 狀態機、權限矩陣、設定驗證、path 防護
- **整合**:pytest + httpx AsyncClient + 臨時 workspace;真實 graphrag 小語料 dry-run(標記 slow,夜跑)
- **前端**:vitest + RTL 關鍵組件(設定表單、任務狀態、日誌 viewer)
- **Smoke(每階段交付前)**:docker-compose 起全套,UI 手動/腳本走過該階段主流程

## 12. 階段劃分(各自獨立實作計畫)

1. **Foundation** — scaffold(前後端 + compose)、auth + 使用者管理、專案 CRUD + 成員、檔案上傳、設定檔編輯器(雙模式 + 版本)
2. **Indexing** — 任務啟動/隊列/即時日誌/取消/歷史/reconcile
3. **Query** — 四種查詢模式 + 結果與引用呈現
4. **Explore** — parquet 表格瀏覽 + 知識圖譜視覺化

每階段含:實作 → smoke → smell 檢查/重構 →(必要時)文件更新。Helm chart 於 Phase 1 建立(與 compose 同步維護),避免最後补課。
