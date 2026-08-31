# OAuth2-Proxy 驗證(選用)

`AUTH_MODE=proxy` 的操作指南。概觀與請求流程圖見
[README 的 OAuth2-Proxy 段](../../README.md#oauth2-proxy-authentication-optional);
設計理由見[設計規格](../superpowers/specs/2026-08-27-oauth2-proxy-auth-design.md)。

## docker compose(選用 overlay)

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
# 僅純 http 部署使用（瀏覽器在 http 上會拒收 Secure cookie，登入會無聲失敗）：OAUTH2_PROXY_COOKIE_SECURE=false
```

## helm

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

## email 網域允許清單是安全控制

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

## 注意事項

- **proxy → local 切換**:JIT 帳號的密碼雜湊不可用 —— 在管理員
  (AdminUsers)重設密碼之前,它們無法使用本機登入。
- **local → proxy 切換**:卡在 `must_change_password` 的使用者不會被
  鎖在外面 —— proxy 模式會略過密碼變更閘門。
- **`PROXY_ADMIN_EMAILS` 只授予、永不撤銷。** 列於其中的 email 每次請求
  都會重新授予 `user_admin` + `ops` 組合;要先從變數中移除,才能在
  AdminUsers 變更角色。
- **IdP 上的 email 變更即是新身分** —— 新地址會建立全新的資料列;舊資料
  列保有原本的專案成員資格。管理員需將新帳號重新加入專案,並停用舊資料列。
- **IdP 發出的特殊用途網域(`.local`、`.internal`)會被拒絕** ——
  email 驗證不接受它們,resolver 回 401,該帳號永遠不會建立
  (與 `BOOTSTRAP_ADMIN_EMAIL` 同一個陷阱)。
- **登出**會落在 oauth2-proxy 自己的登入頁:絕不轉址回應用(運作中的
  IdP 工作階段會默默重新登入),而 IdP 自身工作階段的結束屬於
  oauth2-proxy/供應商設定,不是應用的職責。

## 手動煙霧測試(需要真實 IdP)

在 compose overlay 啟動後執行:

1. 匿名:`curl -i http://localhost:8080/api/auth/me` → **401**(不是
   302 —— `api_routes` 生效)。
2. 瀏覽器開啟 `http://localhost:8080/` → IdP 登入 → 應用啟動;
   `/api/auth/me` 顯示 JIT 建立的使用者。
3. 偽造繞過:`docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml exec web curl -i -H "X-Forwarded-Email: admin@x" http://api:8000/api/auth/me` → **401**(沒有密鑰)。
4. 重複標頭的取代語意:經正門送出重複的 `X-Forwarded-Email` 標頭 →
   回應仍是 200,且只有「一個」一致的身分(oauth2-proxy 是取代,
   不是附加)。
5. SSE:排入一項索引工作並開啟其即時日誌(或索引完成後執行查詢);
   訊框經 auth → web → api 順暢流動不卡住。
6. UI 登出 → 停在 oauth2-proxy 自己的登入頁(不會自動重新登入)。
