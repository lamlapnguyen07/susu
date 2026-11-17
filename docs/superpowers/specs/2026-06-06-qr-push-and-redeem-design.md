# 子項 5：QR 推送與核銷（D + E）設計規格

- 日期：2026-06-06
- 狀態：已通過設計，待實作
- 前置：現金模式（取貨付現）已上線；後台「確認訂單」已生成 `qr_token` + QR 圖 + 推 `order_confirmed` 文字訊息

## 目標

1. **D — QR 推送**：把已生成的 QR 圖，透過 LINE Messaging API 以圖片訊息推給顧客（目前只存本機 `訂單/`）。
2. **E — 掃碼核銷**：店員在顧客取貨時，用「核銷網頁」的相機掃顧客 QR，核對訂單摘要後確認核銷（標記已取貨），具備 `qr_token` 防偽與冪等。

## 整體流程

```
後台「確認訂單」(已存在)
   └─ 生成 qr_token + QR圖(不可猜檔名) + 推 LINE
         └─ worker 推【文字 + QR圖片】給顧客            ← D
顧客到場出示 QR
   └─ 店員開 /admin/redeem 掃碼 → 看摘要 → 確認核銷       ← E
         └─ 標記 is_picked_up=TRUE（防偽 + 冪等 + audit）
```

---

## D — 推 QR 圖給顧客

### D1. QR 檔名改不可猜
- `generate_order_qr(order_id, token)` 存檔由 `<order_id>.png` 改為 `<order_id>_<token>.png`。
- 理由：`order_id` 是時間戳衍生、可被列舉；加上 64-bit `qr_token` 後檔名不可猜，避免他人盜看 QR 圖。
- 既有 QR（含 `backfill_qr.py` 與已確認訂單）需依新規則重生成。

### D2. 公開圖片路由
- 新增 `GET /order-qr/<filename>`：以 `send_from_directory(ORDER_QR_DIR, filename)` 提供圖。
- 安全：
  - 嚴格驗證 `filename` 格式（僅允許 `^[0-9]+_[A-Za-z0-9_-]+\.png$`），擋路徑穿越。
  - 不提供目錄列表、不需登入（保護來自不可猜檔名；LINE 伺服器需能匿名抓圖）。
  - 設定合理 `Cache-Control`。

### D3. worker 推圖
- `confirm-order` 的 queue payload 增加 `qr_file`（檔名字串，不含網域）。
- `push_sever.py` 的 `order_confirmed` 分支：改為一次 `push_message(user_id, [TextSendMessage(...), ImageSendMessage(originalContentUrl=url, previewImageUrl=url)])`（LINE 單次最多 5 則）。
- 圖片網址 = worker 新環境變數 `PUBLIC_BASE_URL`（例如 `https://你的網域`）+ `/order-qr/` + `qr_file`。
- 若 `PUBLIC_BASE_URL` 未設定或 `qr_file` 缺：降級為只推文字（不可因推圖失敗而漏掉下單成功通知）。

---

## E — 店員掃碼核銷頁

### E1. 後端路由（沿用 `login_required` + `csrf_required`）
- `GET /admin/redeem` → 渲染核銷頁 `redeem.html`。
- `POST /api/redeem/lookup`，body `{qr: "order_id|qr_token"}`：
  - 解析 `qr`（以 `|` 分割），查訂單，驗 `qr_token` 相符。
  - **只讀不改**，回訂單摘要：`order_id`、`name`、`amount`、品項、`payment_method`、`is_picked_up`、`order_status`。
  - 找不到 / token 不符 → 回對應狀態，前端顯示警示（不洩漏其他訂單資訊）。
- `POST /api/redeem/confirm`，body `{order_id, qr_token}`：
  - `UPDATE \`Order\` SET is_picked_up=TRUE, picked_up_at=NOW() WHERE order_id=%s AND qr_token=%s AND order_status='active' AND is_picked_up=FALSE`。
  - 以 `rowcount` 判冪等：`=1` 成功；`=0` 再查原因（不存在 / token 不符 / 已取貨 / 已取消）回對應訊息。
  - 寫 `audit('redeem_order', ...)`。

### E2. 前端
- 新增 `templates/redeem.html`、`static/js/redeem.js`、樣式（可併入 `admin.css` 或新 `redeem.css`）。
- 掃碼庫：**本地化 jsQR**（純函式、單檔、MIT、不開 web worker → 不需放寬 CSP `script-src`/`worker-src`）+ 約 40 行 `getUserMedia` 取影格 → canvas → `jsQR(imageData)`。
  - 不選 html5-qrcode：它會以 blob 建 web worker，需放寬 CSP。
- 流程：
  1. 「開始掃描」按鈕（相機需使用者手勢觸發）。
  2. 掃到 QR → `POST /api/redeem/lookup` → 顯示訂單摘要卡。
  3. 店員核對金額收現金 → 「確認核銷」→ `POST /api/redeem/confirm`。
  4. 成功 → 顯示成功 → 「繼續掃下一筆」（重置相機）。
  5. 相機被拒：顯示提示說明（本版不做手動輸入後備）。

---

## DB / 設定 / 安全

- **ALTER**（冪等）：`Order` 加 `picked_up_at TIMESTAMP NULL`。
- **改 Permissions-Policy**：`camera=()` → `camera=(self)`（目前 `camera=()` 會擋掉核銷頁相機；改為僅允許同源頁面使用相機）。
- **防偽 + 冪等**：核銷一律驗 `qr_token`；`WHERE … is_picked_up=FALSE` + `rowcount` 確保不能重複核銷。
- **稽核**：核銷寫 `audit.log`。
- **Rate limit**：`/api/redeem/lookup`、`/api/redeem/confirm` 沿用既有限流防爆破（64-bit token 本就無法暴力猜出）。
- **裝置限制（未來增強）**：使用者構想「只有特定裝置可連後台/核銷」。MAC 位址在 Web 不可行（伺服器只看得到公網 IP，看不到 MAC）。等效可行做法：① IP 白名單（綁固定對外 IP）；② 裝置綁定簽章 Cookie（核准裝置設一次長期 token）。本版不做，記為未來增強。

---

## 動到的檔案

- `app.py`：`generate_order_qr` 檔名、`confirm-order` payload 加 `qr_file`、新增 `/order-qr/<filename>`、`/admin/redeem`、`/api/redeem/lookup`、`/api/redeem/confirm`、`ALTER picked_up_at`、`Permissions-Policy` 改 `camera=(self)`。
- `push_sever.py`：`order_confirmed` 分支推【文字 + 圖片】、新增 `PUBLIC_BASE_URL` 環境變數、降級邏輯。
- 新增：`templates/redeem.html`、`static/js/redeem.js`、樣式、本地 `static/js/vendor/jsQR.js`（vendored）。
- 既有 QR 圖：依新檔名規則重生成（更新 `backfill_qr.py` 或一次性重跑）。

## YAGNI（本版不做）

- 獨立店員帳號（沿用後台 admin 登入）。
- 裝置綁定 / IP 白名單。
- 核銷頁手動輸入後備（先靠相機）。
- 圖片預覽縮圖（`previewImageUrl` 直接用原圖）。

## 驗收

1. 後台「確認訂單」後，顧客 LINE 收到【文字 + QR 圖片】。
2. QR 圖網址不可被列舉猜出；非法檔名被擋。
3. 店員開 `/admin/redeem`（需登入）能用相機掃碼 → 看到正確摘要 → 確認核銷 → 訂單 `is_picked_up=TRUE` 且 `picked_up_at` 有值。
4. 重複核銷同一單 → 第二次被擋並提示「已取貨」。
5. 偽造 / 不符 `qr_token` → 被擋。
6. 取消單掃碼 → 提示「已取消」，不可核銷。
