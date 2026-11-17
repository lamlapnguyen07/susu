# 食在好蔬 — Synology NAS 部署說明

把整個 `NAS_Deploy/` 資料夾丟上 NAS，`docker compose up` 一鍵起三個服務：
**Caddy（對外入口）+ app（網站/後台）+ push_server（LINE 通知）**。
app 與 push_server **共用同一個 `data/` 資料夾**，所以取貨 QR 圖不會「找不到圖源」。

---

## 一、資料夾結構

```
NAS_Deploy/
├── docker-compose.yml      ← 一鍵啟動三個服務
├── Caddyfile               ← 反向代理路由（你給的那份）
├── .env                    ← 你的密鑰 / 設定（已含真實值，勿外流）
├── .env.example            ← 範本參考
├── app/                    ← 網站 + 後台 + API（app.py）
│   ├── Dockerfile
│   ├── app.py / seed_products.py / setup_2fa.py / backfill_qr.py
│   ├── templates/ static/ img/   ← 含 QR 底圖 img/QRCODE_main.png
│   └── requirements.txt
├── push_server/            ← LINE 通知 / Webhook（push_sever.py）
│   ├── Dockerfile
│   ├── push_sever.py / diag_line_queue.py
│   └── requirements.txt
└── data/                   ← ★ 持久資料（兩個容器共用，要備份這個）
    ├── 訂單/                ← 取貨 QR 成品圖（app 產生、push_server 對外提供）
    └── product-images/      ← 後台「商品管理」上傳的圖
```

## 二、路由與 QR 圖怎麼接（為什麼不會掉圖）

Caddy 依你的 `Caddyfile` 分流（服務名要跟 compose 一致）：

| 對外網址 | 導到 | 說明 |
|----------|------|------|
| `/botgoodveg/callback` | **push_server:4999** | LINE Webhook |
| `/order-qr/*` | **push_server:4999** | 取貨 QR 圖 |
| 其餘（`/`、`/pos`、`/lidmin`、API…） | **app:5000** | 網站 / 後台 |

`/order-qr/` 走 push_server，而 QR 圖是 **app 產生**的 → 所以 compose 讓 app 和 push_server 都把同一個 `./data` 掛進 `/app/data`，並設 `ORDER_QR_DIR=/app/data/訂單`。push_server 就讀得到 app 產生的圖，**不會掉**。

> QR 底圖 `img/QRCODE_main.png`、菜葉照 `img/menu/` 已打包進 `app` 映像，不必另外掛。

## 三、部署步驟

### 1. 先改 `.env`（**這兩項一定要改，不然 QR / LINE 會壞**）
- `PUBLIC_BASE_URL` → 你的網域（例 `https://eatgoodveg.com`）。沒設 → 只推文字、不推 QR 圖。
- `LINE_CHANNEL_ACCESS_TOKEN` → ⚠️ 目前這串**中間有一個空格**（`…mcVzrH 9MP5lj…`），LINE token 不可能有空格，請去 LINE Developer Console 複製正確的重貼。
- 順帶確認 `DB_HOST`（連不到區網 IP 就改 `host.docker.internal`）、`FLASK_SECURE_COOKIE=1`。

### 2. 對外網路前置（Caddy 自動申請 HTTPS 憑證的前提）
- 網域 `eatgoodveg.com` 的 DNS A 記錄要指到你對外的固定 IP。
- 路由器要把 **80、443** 轉發到這台 NAS。
- ⚠️ Synology DSM 預設可能佔用 80/443（控制台 → 登入入口 → DSM 連接埠改掉，或把對外那兩個 port 讓給 Caddy）。
- 若你是走 **Cloudflare Tunnel**：那就不需要 Caddy 對外開 80/443，改由 tunnel 指到 `app:5000` / `push_server:4999`（這份 compose 仍可用，把 caddy 服務移除即可）。

### 3. 啟動
```bash
cd /volume1/.../NAS_Deploy          # 換成你放的路徑
sudo docker compose up -d --build
sudo docker compose logs -f         # 看啟動是否正常（Ctrl+C 離開）
```
或用 **Container Manager → 專案 → 新增 → 選這個資料夾**。

啟動後：
- 對外：`https://eatgoodveg.com/`、後台 `https://eatgoodveg.com/lidmin`
- 區網直連測試：`http://NAS_IP:5000/`（compose 有保留 5000/4999 對區網，正式可刪）
- 首次啟動 `app` 會自動建表並塞入現有 12 款商品（idempotent）。

### 4. LINE Developer Console
- Messaging API → Webhook URL 填 `https://eatgoodveg.com/botgoodveg/callback`
- 確認 `PUBLIC_BASE_URL` = `https://eatgoodveg.com`

## 四、維運

- **備份**：定期備份整個 `data/`（QR 圖、商品圖、稽核 log 都在裡面）。憑證在 docker volume `caddy_data`。
- **改商品**：後台「🥬 商品管理」直接改，首頁 / 點餐頁同步。
- **重設 2FA**：`docker compose exec app python setup_2fa.py` 產生新密鑰 → 更新 `.env` 的 `ADMIN_TOTP_SECRET` → 重啟。
- **更新程式**：覆蓋 `app/` 或 `push_server/` 檔案後 `docker compose up -d --build`。
- **看 log**：`docker compose logs -f app` / `... push_server` / `... caddy`。

## 五、常見問題

| 症狀 | 原因 / 解法 |
|------|------------|
| Caddy 一直拿不到憑證 | 80/443 沒對外通（DSM 佔用、router 沒轉發、DNS 沒指對） |
| LINE 收到文字但沒 QR 圖 | `PUBLIC_BASE_URL` 沒設、token 有空格、或 `/order-qr/` 沒導到 push_server |
| 推播完全沒收到 | `LINE_CHANNEL_ACCESS_TOKEN` 錯（檢查空格）、Webhook URL 沒指到 push_server |
| 容器一直重啟 | 多半連不到資料庫：`DB_HOST` 改 `host.docker.internal` 試試、確認帳密 |
| 後台登不進去 | `ADMIN_USER/PASS` 沒設會停用登入；純 http 測試把 `FLASK_SECURE_COOKIE` 設 0 |
| 商品圖/QR 重啟後不見 | 確認資料寫在掛載的 `data/`（compose 已設好），別寫進容器內 |
