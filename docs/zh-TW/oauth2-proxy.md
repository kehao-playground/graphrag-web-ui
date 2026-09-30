# OAuth2-Proxy 驗證（選用）

`AUTH_MODE=proxy` 的操作指南。概觀與請求流程圖見
[README 的 OAuth2-Proxy 段](../../README.md#oauth2-proxy-authentication-optional)；
設計理由見[設計規格](../superpowers/specs/2026-08-27-oauth2-proxy-auth-design.md)。

## docker compose（選用 overlay）

```
docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml up -d
```

overlay 會新增 `auth` 服務（固定版
`quay.io/oauth2-proxy/oauth2-proxy:v7.15.4`）作為 `http://localhost:8080`
上唯一發佈的入口、取消發佈 web 埠（雙保險：沒有任何繞過 proxy 直達 api
的路徑）、在 api 上設定 `AUTH_MODE=proxy`，並讓 `/api/*` 回應 **401**
而非登入轉址，SPA 的 fetch 層才能反應。需要 Compose ≥ 2.24。`.env`
最小新增內容：

```dotenv
PROXY_ADMIN_EMAILS=you@example.com
PROXY_AUTH_SECRET=            # >= 32 字元 —— 產生：openssl rand -hex 32
OAUTH2_PROXY_ISSUER_URL=https://idp.example.com/realms/main
OAUTH2_PROXY_CLIENT_ID=graphrag-ui
OAUTH2_PROXY_CLIENT_SECRET=
OAUTH2_PROXY_COOKIE_SECRET=   # 16/24/32 bytes 的 base64 —— openssl rand -base64 32 | tr -d '\n'
OAUTH2_PROXY_REDIRECT_URL=http://localhost:8080/oauth2/callback
OAUTH2_PROXY_EMAIL_DOMAINS=example.com
# 僅純 http 部署使用（瀏覽器在 http 上會拒收 Secure cookie，登入會無聲失敗）：OAUTH2_PROXY_COOKIE_SECURE=false
```

## helm

設定 `proxyAuth.enabled: true`（需要 `ingress.enabled` 與具體的
`ingress.host`）。chart 會把 ingress 拆成兩個 —— `/api` Ingress 在驗證
失敗時把 **401** 原樣傳給 `fetch`，app Ingress 則將瀏覽器轉址到登入頁。
可選擇讓 chart 自帶 oauth2-proxy：

```yaml
proxyAuth:
  enabled: true
  issuerUrl: https://idp.example.com/realms/main
  clientId: graphrag-ui
  clientSecret: "..."        # 明文，或改用 existingSecret（見下）
  cookieSecret: "..."        # 16/24/32 bytes 的 base64
  authSecret: "..."          # PROXY_AUTH_SECRET — >= 32 字元
  adminEmails: ["you@example.com"]
  emailDomains: ["example.com"]   # 必填 —— 見下方警告
  # existingSecret: my-secret     # 三個明文金鑰的替代方案；內容必須包含
  #   client-secret、cookie-secret、proxy-auth-secret 三個 key
```

……或僅透過 annotations 重用全叢集共用的 oauth2-proxy（chart 不自帶
oauth2-proxy；外部實例必須注入 `X-Forwarded-Email`、
`X-Forwarded-Preferred-Username`，以及等於 `authSecret` 的
`X-Proxy-Secret`）：

```yaml
proxyAuth:
  enabled: true
  external:
    url: https://sso.example.com
  authSecret: "..."          # 必須與外部實例注入的金鑰一致
  adminEmails: ["you@example.com"]
  emailDomains: ["example.com"]
```

## email 網域允許清單是安全控制

> **`OAUTH2_PROXY_EMAIL_DOMAINS` 是安全控制，不是便利選項。**
> oauth2-proxy 以 `--email-domain`（清單，`*` = 任意）或
> `--authenticated-emails-file`（每行一個）決定哪些 email 可以通過。
> 由於 JIT 建立（§5.2）會把「IdP 驗證過這個人」直接變成「一列 `User`
> 資料存在」，公用供應商搭配 `*` 就等於**任何持有 Google 帳號的人都能
> 自行註冊 `user` 帳號**，進而建立專案、消耗 LLM 預算。`.env.example`
> 預設不註解此變數，附上佔位網域與明確警告；helm 以
> `proxyAuth.emailDomains` 對應（§7.2）。這是應用本身威脅模型所倚賴的
> 唯一一個 oauth2-proxy 設定（§8）。

（§ 編號指上方連結的設計規格。）

## 注意事項

- **proxy → local 切換**：JIT 帳號的密碼雜湊不可用 —— 在管理員
  （AdminUsers）重設密碼之前，它們無法使用本機登入。
- **local → proxy 切換**：卡在 `must_change_password` 的使用者不會被
  鎖在外面 —— proxy 模式會略過密碼變更閘門。
- **`PROXY_ADMIN_EMAILS` 只授予、永不撤銷。** 列於其中的 email 每次請求
  都會重新授予 `user_admin` + `ops` 組合；要先從變數中移除，才能在
  AdminUsers 變更角色。
- **IdP 上的 email 變更即是新身分** —— 新地址會建立全新的資料列；舊資料
  列保有原本的專案成員資格。管理員需將新帳號重新加入專案，並停用舊資料列。
- **IdP 發出的特殊用途網域（`.local`、`.internal`）會被拒絕** ——
  email 驗證不接受它們，resolver 回 401，該帳號永遠不會建立
  （與 `BOOTSTRAP_ADMIN_EMAIL` 同一個陷阱）。
- **登出**會落在 oauth2-proxy 自己的登入頁：絕不轉址回應用（運作中的
  IdP 工作階段會默默重新登入），而 IdP 自身工作階段的結束屬於
  oauth2-proxy/供應商設定，不是應用的職責。

## 測試用 IdP（本機驗證）

[`deploy/test-idp/`](../../deploy/test-idp) 讓 overlay 接上只有一位靜態使用者的
[Dex](https://dexidp.io/)，不需要真實的身分提供者，就能在筆電上走完整個 proxy
流程。裡面每個值都是公開的測試資料——切勿用於部署。在 repo 根目錄執行：

```
docker compose -p graphrag-idp \
  --env-file .env --env-file deploy/test-idp/test-idp.env \
  -f docker-compose.yml -f docker-compose.proxy-auth.yml \
  -f deploy/test-idp/docker-compose.test-idp.yml up -d --build
```

開啟 `http://localhost:8080`，選「Sign in with OpenID Connect」，以
`admin@example.com` / `test-idp-password` 登入；`PROXY_ADMIN_EMAILS` 會授予此使用者
`user_admin` + `ops`。`-p graphrag-idp` 讓測試使用自己的 volume，不會碰到本機登入
模式堆疊的資料。移除時執行 `docker compose -p graphrag-idp … down -v`。

issuer 必須是瀏覽器與 oauth2-proxy 都連得到的同一個網址（`http://localhost:5556/dex`，
會與權杖的 `iss` 比對）：Dex 發布在主機 5556 埠，另有一個小型 `socat` 轉送容器擁有
oauth2-proxy 所在的網路命名空間，因此 proxy 內的 `localhost:5556` 也會連到 Dex。主機的
8080 埠被占用時，把轉送容器的埠發布到別處（在另一個 `-f` 檔寫
`dex-loopback: ports: !override ["18080:4180"]`），並設定
`OAUTH2_PROXY_REDIRECT_URL=http://localhost:18080/oauth2/callback`；Dex 接受 8080 與
18080 兩個回呼埠。

在公司 proxy 後方：Docker 會把用戶端 `~/.docker/config.json` 的 `proxies` 注入每個
容器，oauth2-proxy 也不例外。overlay 在 `auth` 服務設定 `NO_PROXY=web`，讓轉往 web
容器的上游請求留在 compose 網路內（少了它，每個頁面都會是公司 proxy 回的 502）；
對真實 IdP 的呼叫仍走 proxy。

## 手動冒煙測試（需要真實 IdP 或測試用 IdP）

在 compose overlay 啟動後執行：

1. 匿名：`curl -i http://localhost:8080/api/auth/me` → **401**（不是
   302 —— `api_routes` 生效）。
2. 瀏覽器開啟 `http://localhost:8080/` → IdP 登入 → 應用啟動；
   `/api/auth/me` 顯示 JIT 建立的使用者。
3. 偽造繞過：`docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml exec web curl -i -H "X-Forwarded-Email: admin@x" http://api:8000/api/auth/me` → **401**（沒有金鑰）。
4. 重複標頭的取代語意：經正門送出重複的 `X-Forwarded-Email` 標頭 →
   回應仍是 200，且只有「一個」一致的身分（oauth2-proxy 是取代，
   不是附加）。
5. SSE：排入一項索引工作並開啟其即時日誌（或索引完成後執行查詢）；
   訊框經 auth → web → api 順暢流動不卡住。
6. UI 登出 → 停在 oauth2-proxy 自己的登入頁（不會自動重新登入），且該頁取自網路，
   而非快取的應用頁面（修正波 F25 於測試用 IdP 驗證：
   `docs/superpowers/reviews/assets/f25-03-*.jpg`）。
