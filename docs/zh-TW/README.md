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
- 在 macOS 上,`docker compose build web` 可能卡在鑰匙圈提示。請先解鎖
  鑰匙圈,或只建置 API(`docker compose build api`),並在本機跑前端
   來做 UI 工作:`API_PROXY_TARGET=http://localhost:8080 npm run preview`。

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

## 貢獻與文件

- [CONTRIBUTING.md](../../CONTRIBUTING.md)
- 正體中文(zh-TW)鏡像:[`README.md`](README.md)
- 設計規格:[`docs/superpowers/specs/`](../../docs/superpowers/specs/)
