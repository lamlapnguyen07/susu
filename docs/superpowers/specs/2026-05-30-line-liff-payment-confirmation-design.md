# LINE LIFF 登入 + 付款確認推播 — 設計規格

**日期：** 2026-05-30
**目標範圍：** 在 POS 訂購頁加入 LINE LIFF 登入功能，並補上後台「確認收款 → 推播 LINE」的閉環。
**不在範圍內：** LIFF 訂單歷史頁、`pickup_completed` / `payment_reminder` 訊息類型、新通知 worker。

---

## 1. 問題定義

### 1.1 目前的痛點
- **顧客端**：POS 採匿名訂購，下單後沒有任何身分。轉帳完選「已完成付款」只是更新 localStorage 的本地狀態為「已回報 · 待對帳」，**永遠停在這個狀態**，因為後端確認收款後沒辦法把訊息送回給顧客（顧客沒登入、沒 LINE 綁定的話沒推送管道）。
- **店家端**：後台確認收款後沒有自動通知機制，顧客只能等下次訪問網站才看到狀態變化，或者顧客打電話來問。
- **新顧客的綁定路徑只在 LINE 內**：目前唯一綁定 LINE_ID ↔ 電話的方式是顧客去 LINE 加官方好友、輸入「登記」、回傳「姓名 電話」 (`push_sever.py:register_customer`)。從 web 上沒辦法綁。

### 1.2 範圍判斷
此設計把「LINE + 付款」拆成 4 個子系統並只取 B + C：

| 子系統 | 是否做 | 原因 |
|--------|--------|------|
| A. LINE 推送 worker | ❌ 已存在（`push_sever.py`），不動 |
| **B. 顧客 LINE 綁定（web 端 LIFF）** | ✅ | 補上 web 綁定路徑 |
| **C. 付款確認通知** | ✅ | 補閉環 |
| D. LIFF 訂單歷史頁 | ❌ | 下一輪 |

---

## 2. 架構

```
┌─────────────────────────────────────────────────────────────┐
│  前端 (POS 頁) ─ 新增 LIFF SDK + UI 重整                       │
├─────────────────────────────────────────────────────────────┤
│  Flask 後端 (app.py) ─ 新增 4 個 API + 改 1 個既有 endpoint    │
├─────────────────────────────────────────────────────────────┤
│  舊 LINE Worker (push_sever.py) ─ 加 1 種訊息類型 elif 分支    │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 各層職責

| 層 | 檔案 | 職責 |
|----|------|------|
| 前端 | `templates/pos.html`、`static/js/pos.js`、`static/css/pos.css` | LIFF init / 顯示登入狀態 / 自動帶入姓名電話 / 抽屜 UI 重整 |
| 前端 | `templates/admin.html`、`static/js/admin.js` | 後台訂單列加「✓收款」按鈕 |
| 後端 | `app.py` | LIFF ID Token 驗證、訂單查詢 by line_id、確認收款流程、下單時的 binding |
| 後端依賴 | `requirements.txt` | 新增 `requests>=2.31` |
| Worker | `push_sever.py`（用戶另外維護） | 新增 `payment_confirmed` 分支 |
| 資料庫 | `Order`、`CustomerLineBindings`、`LineNotificationQueue` | 表結構不動 |

---

## 3. UI 設計

### 3.1 POS 頁首右上角

**狀態 1：未登入 LINE**
```
[LOGO]   ……   [📅 12/30 三]   [⇣ 用 LINE 登入]   [🛒 購物車 3]
```

**狀態 2：已登入 LINE**
```
[LOGO]   ……   [📅 12/30 三]   [👤 王小明 ▾]      [🛒 購物車 3]
                                       ↓ 點下拉
                                   ┌──────────────┐
                                   │ 王小明        │
                                   │ 0912-345-678 │
                                   │ ────────     │
                                   │ 登出 LINE     │
                                   └──────────────┘
```

- 日期欄位手機板隱藏
- 已登入時顯示 LINE pictureUrl + displayName，點下拉顯示已綁電話 + 登出選項
- 「登出 LINE」會 call `/api/line/logout`（清 server session）+ `liff.logout()`（清前端）

### 3.2 購物車雙入口

- **右上角按鈕**：`[🛒 購物車 N]`，N 為包數
- **右下角浮動 FAB**：原本左下移到右下，內容不變
- 兩個按鈕都打開同一個右側抽屜（不變）

### 3.3 購物車抽屜頂部「待付款」高亮條

**有待付款訂單時：**
```
┌──────────────────────────────────────┐
│ 購物車                            [×] │
├──────────────────────────────────────┤
│ ⚠ 你有 2 筆待付款訂單                  │ ← 高亮條（ochre 米橙底）
│   #20251230001  NT$ 300   [付款碼→]   │
│   #20251229005  NT$ 450   [付款碼→]   │
├──────────────────────────────────────┤
│ ── 目前購物車（2 包） ──                │
│ 01 組合包 ×2          NT$ 300          │
│ ...                                   │
└──────────────────────────────────────┘
```

**沒待付款時：** 高亮條整段隱藏，直接從購物車列表開始（跟現在一樣）。

**資料來源**：
- 登入時：`GET /api/orders/mine`（server 端為真實來源）
- 未登入時：localStorage `veg_pending_orders`（沿用現邏輯）

### 3.4 結帳第 2 步「填寫取件資訊」自動帶入

- **未登入**：兩欄空白
- **登入 + 未綁過 (CustomerLineBindings 沒這個 LINE_ID 的 row)**：姓名帶入 displayName，電話空白
- **登入 + 已綁**：姓名跟電話都帶入並打 ✓ icon。使用者仍可改

### 3.5 後台訂單列「✓收款」按鈕

訂單列操作區增加：

```
時間/編號 | 顧客 | ... | 收款       | 狀態  | 操作
12/30   | 王小明 | ... | [待對帳▾] | 待取貨 | [推播] [✓收款] [取貨]
                                              ↑ 新增
```

- 點 [✓收款]：跳 confirm modal「確認 #20251230001 已收到款項？」（沿用現有 `confirm-modal`）
- 確認 → POST `/api/admin/orders/<id>/confirm-payment`
- 已是 `cash` / `transfer` 的訂單按鈕隱藏（沒必要重複確認）

---

## 4. 後端 API 規格

### 4.1 `POST /api/line/verify`

驗證 LIFF 拿到的 ID Token，並把身分寫進 Flask session。

**Request body**
```json
{ "idToken": "<JWT from liff.getIDToken()>" }
```

**Server 流程**
1. POST `https://api.line.me/oauth2/v2.1/verify` with `id_token=<idToken>&client_id=<LIFF Channel ID>`
2. LINE 回 `{ sub, name, picture, iss, aud, exp, ... }`
3. 驗 `aud == 你的 Channel ID`、`exp > now`、`iss == "https://access.line.me"`
4. SELECT CustomerLineBindings WHERE LINE_ID = sub → 抓 bound name / phone（可能 None）
5. 寫 session：
   - `session['line_id'] = sub`
   - `session['line_name'] = name`
   - `session['line_picture'] = picture`
   - `session['line_phone'] = bound_phone or None`

**Response**
```json
{
  "line_id": "Uxxx...",
  "display_name": "王小明",
  "picture_url": "https://...",
  "bound_phone": "0912345678",
  "bound_name": "王小明"
}
```
未綁過時 `bound_phone` 和 `bound_name` 為 null。

**錯誤**
- 400：缺 idToken
- 401：ID Token 驗證失敗（過期/簽發者錯/aud 不符）

**速率限制**：套現有 rate limit 中介，每 IP 每分鐘 30 次。

### 4.2 `GET /api/orders/mine`

抓登入用戶的「待付款 + 待取貨」訂單，供購物車抽屜頂部使用。

**前置條件**：`session['line_id']` 必須存在，否則 401。

**SQL**
```sql
SELECT order_id, customer_name, customer_phone, grand_total,
       payment_method, is_picked_up, expected_ship_date, order_time
FROM `Order`
WHERE line_id = %s
  AND is_picked_up = FALSE
  AND payment_method IN ('unpaid', 'pending_check')
ORDER BY order_time DESC
LIMIT 20
```

**Response**：`{ orders: [...] }`，欄位同上。

### 4.3 `POST /api/line/logout`

清掉 session 裡所有 `line_*` 欄位。前端再 call `liff.logout()` 清前端。

**Response**：`{ ok: true }`

### 4.4 `POST /api/admin/orders/<order_id>/confirm-payment`

後台收款確認 + 推 LINE。

**前置條件**：`@login_required`（admin session）。

**Server 流程**
1. SELECT Order WHERE order_id = ? → 拿 phone, grand_total, expected_ship_date, line_id
2. 若 payment_method 已是 `cash` 或 `transfer` → 回 409「此訂單已確認收款」
3. UPDATE Order SET payment_method = 'transfer' WHERE order_id = ?
4. `_enqueue_line_in_cursor(cur, 'payment_confirmed', phone, payload)`：
   ```json
   {
     "order_id": "20251230001",
     "amount": 500,
     "ship_date": "2026-01-02"
   }
   ```
5. commit

**Response**：`{ ok: true, queued: bool }`，`queued` 表示是否有成功推進佇列（沒綁 LINE 的訂單仍會 INSERT 但 worker 之後會標 failed）。

### 4.5 改寫 `POST /api/orders/place`

原本就會用 phone lookup CustomerLineBindings 拿 line_id 寫進 Order。新增邏輯：

```python
session_line_id = session.get('line_id')

if session_line_id:
    # === LINE 已登入路徑：做 lazy binding（含衝突檢查、回溯、queue 重排）===

    # 1a. 檢查 phone 是否被別的 LINE_ID 佔用（CustomerLineBindings.Phone_Number 是 UNIQUE）
    cur.execute("SELECT LINE_ID FROM CustomerLineBindings WHERE Phone_Number = %s", (phone,))
    existing = cur.fetchone()
    if existing and existing[0] != session_line_id:
        return jsonify({'error': '此手機號碼已被其他 LINE 帳號綁定'}), 409

    # 1b. UPSERT 綁定（這顆 LINE_ID 是新的就 INSERT，已存在就 UPDATE 姓名/電話）
    cur.execute("""
        INSERT INTO CustomerLineBindings (LINE_ID, Customer_Name, Phone_Number, Register_Time)
        VALUES (%s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            Customer_Name = VALUES(Customer_Name),
            Phone_Number = VALUES(Phone_Number)
    """, (session_line_id, name, phone))

    # 1c. 回溯：把該電話過去未綁 line_id 的訂單補上
    cur.execute("""
        UPDATE `Order` SET line_id = %s
        WHERE customer_phone = %s AND (line_id IS NULL OR line_id = '')
    """, (session_line_id, phone))

    # 1d. 重排：該電話 failed 的通知重新入 queue
    cur.execute("""
        UPDATE LineNotificationQueue SET status='pending'
        WHERE phone=%s AND status='failed'
    """, (phone,))

    line_id = session_line_id

else:
    # === 匿名訂購路徑：沿用舊邏輯，用 phone lookup 既有綁定 ===
    cur.execute("SELECT LINE_ID FROM CustomerLineBindings WHERE Phone_Number = %s LIMIT 1", (phone,))
    row = cur.fetchone()
    line_id = row[0] if row else None

# 2. INSERT Order（line_id 已決定）+ 入 queue 'order_placed'（沿用既有邏輯）
```

> 這套邏輯**完全照搬** `push_sever.py:register_customer` 的回溯流程，只是觸發點從「LINE OA chat」改成「web 下單」。

---

## 5. Worker 變動（`push_sever.py`）

在 `process_notification_queue` 的 type 分支加：

```python
elif row['type'] == 'payment_confirmed':
    payload = json.loads(row['payload']) if row['payload'] else {}
    oid = payload.get('order_id', '?')
    amt = payload.get('amount', 0)
    ship = payload.get('ship_date', '')
    msg = (
        f"✅ 【智慧農場 - 收款確認】\n\n"
        f"您好 {name}，\n"
        f"您的訂單 #{oid} 已確認收到款項 ✓\n\n"
        f"💰 金額：NT$ {amt}\n"
        f"📦 預計出貨日：{ship}\n\n"
        f"我們會在準備完成後通知您取貨！"
    )
```

---

## 6. LIFF 前端流程

### 6.1 初始化

POS 頁載入時：

```javascript
// 從 server 注入的 LIFF_ID
await liff.init({ liffId: window.__LIFF_ID__ });

if (liff.isLoggedIn()) {
  const idToken = liff.getIDToken();
  const res = await fetch('/api/line/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.__CSRF__ },
    body: JSON.stringify({ idToken }),
  });
  const profile = await res.json();
  // 更新 UI 右上角 + 帶入結帳欄位 + 拉 /api/orders/mine
  setLineState(profile);
}
```

### 6.2 進站行為差異

- **從 LINE App 內開啟**：LIFF 自動完成登入，靜默拿到 profile → 直接顯示已登入狀態，使用者無感
- **從一般瀏覽器開啟 + 未登入**：不自動跳 LINE 登入頁。顯示「⇣ 用 LINE 登入」按鈕，使用者主動按才呼叫 `liff.login()`（會跳 LINE OAuth）

### 6.3 登出流程

點頭像下拉「登出 LINE」：

```javascript
await fetch('/api/line/logout', { method: 'POST', headers: { 'X-CSRF-Token': window.__CSRF__ } });
liff.logout();
location.reload();
```

---

## 7. 安全性

- **ID Token 一律由 server 端驗** — 走 LINE 官方 `https://api.line.me/oauth2/v2.1/verify` endpoint，不信任前端傳的任何 line_id。
- **CustomerLineBindings.Phone_Number 衝突檢查** — 一支電話只能綁一個 LINE_ID，下單時 binding 衝突直接 409 拒絕。
- **`/api/orders/mine` 必須 session 驗證** — 沒有 `session['line_id']` 直接 401，不可被前端用任意 line_id 查別人訂單。
- **CSRF 保護** — 所有 POST API 沿用現有 X-CSRF-Token 機制。
- **Rate limit** — `/api/line/verify` 限速避免被打爆 LINE API。

---

## 8. 前置設定（使用者操作）

進入 [LINE Developers Console](https://developers.line.biz/console/)：

1. 在現有 Provider 下，**新增一個 LINE Login channel**（或在既有 Messaging API channel 內加 LIFF app）
2. 在這個 channel 建 LIFF app：
   - **Endpoint URL**：POS 頁網址（例如 `https://你的域名/pos`）
   - **Scope**：`profile openid`（不要 `email`，本設計用不到）
   - **Bot link feature**：可選，啟用後 LIFF 內能把人加好友
3. 抓 **LIFF ID** 跟 **Channel ID** 兩個值
4. 把 LIFF ID 跟 Channel ID 設成環境變數：
   - `LIFF_ID=1234567890-AbCdEfGh`
   - `LINE_LOGIN_CHANNEL_ID=1234567890`

不需要「認證帳號（藍盾）」。

---

## 9. 環境變數新增

`app.py` 啟動時讀：

| 變數 | 用途 |
|------|------|
| `LIFF_ID` | 注入到前端 HTML，供 `liff.init` |
| `LINE_LOGIN_CHANNEL_ID` | 後端驗 ID Token 時當 `client_id` |

未設定時：兩個都缺 → LINE 功能整段降級停用，POS 仍能匿名訂購（不會 break 既有流程）。

---

## 10. 測試計畫

### 10.1 手動測試案例

| 案例 | 預期 |
|------|------|
| 全新顧客從一般瀏覽器進站 | 看到「用 LINE 登入」按鈕，點下去跳 LINE OAuth |
| 從 LINE App 內開連結進站 | 自動完成 LIFF 登入，右上角顯示頭像 |
| 已綁過的顧客再進站 | 自動帶入姓名 + 電話到結帳欄位 |
| 未綁過的顧客登入後下單 | 下單成功後 CustomerLineBindings 多一筆，且 Order.line_id 已填 |
| 同一電話被兩個 LINE 嘗試綁定 | 第二個下單 409 拒絕 |
| 後台對 unpaid 訂單按「✓收款」 | payment_method 變 transfer + worker 5 秒內推 LINE |
| 對沒 LINE 綁的訂單按「✓收款」 | API 仍成功（payment_method 更新），但 worker 標佇列 failed（不影響） |
| 對已是 transfer 的訂單按「✓收款」 | 按鈕應隱藏；若強打 API → 409 |
| 登入後登出 | session line_* 清空，右上回到「用 LINE 登入」狀態 |
| 直接打 `/api/orders/mine` 不帶 session | 401 |
| 傳假的 idToken 給 `/api/line/verify` | 401（LINE API 那邊就拒了） |

### 10.2 壓力測試
沿用 `loadtest.py`，多加一個「LINE-bound 下單 + 確認收款」場景，確認佇列入庫量級可承受 100 並發。

---

## 11. 不在此範圍

明確排除，避免範圍漂移：

- ❌ LIFF 訂單歷史頁（D 子系統）
- ❌ `pickup_completed`、`payment_reminder` 訊息類型
- ❌ 改寫 worker 主架構（沿用現有的 polling 模式）
- ❌ 改變既有 `_enqueue_line_in_cursor` 簽名
- ❌ Order 加新欄位（user 確認不做 `payment_confirmed_at`）
- ❌ admin 的 payment_method dropdown 改造（保留現有的下拉切換，「✓收款」只是快捷鍵）

---

## 12. 風險與回滾

- **LIFF Channel 未建** → `LIFF_ID` env 為空時整段降級，POS 仍能匿名運作
- **LINE API 短暫 down** → `/api/line/verify` 回 502，前端 fallback 到「未登入」狀態，不 block 訂購流程
- **回滾** → 把 `LIFF_ID` env 移除即可關掉前端 LINE 區塊；新增的 API 不會被舊客戶端呼叫所以可保留不影響
