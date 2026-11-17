# 訂單狀態管理：取消 / 退貨 — 設計規格

**日期：** 2026-06-04
**目標範圍：** 為訂單加入「已取消」「已退貨」兩個終態，並提供用戶端取消（未付款）、後台取消（未交付）、後台退貨（已交付）三條操作路徑。
**不在範圍內：** QRCode 生成、掃碼核銷網頁、退款金額記錄、取消後復原（管理員修正）。

---

## 1. 問題定義

### 1.1 背景
目前訂單狀態是散落的欄位（`payment_method` / `is_notified` / `is_picked_up`），沒有統一的「訂單是否仍有效」標記。實務上會遇到：
- 用戶下錯單、還沒付款 → 想自己取消
- 假單 / 顧客反悔（電話通知）→ 後台需要取消
- 已交付後顧客退貨 → 後台需要註記

這些都需要一個明確的訂單狀態機。

### 1.2 範圍
本設計是「訂單 QRCode + 掃碼核銷」大計畫的**子項 1+2+3**（狀態機地基 + 取消 + 退貨）。後續子項（QR 生成、核銷網頁）建立在此狀態機之上，不在本輪。

---

## 2. 資料庫變更

在 `init_db()` 的 `ALTER TABLE` try/except 區塊新增（idempotent）：

```sql
ALTER TABLE `Order` ADD COLUMN order_status VARCHAR(20) DEFAULT 'active'
ALTER TABLE `Order` ADD COLUMN cancelled_at DATETIME DEFAULT NULL
ALTER TABLE `Order` ADD COLUMN cancelled_by VARCHAR(20) DEFAULT NULL   -- 'customer' / 'admin'
ALTER TABLE `Order` ADD COLUMN refunded_at  DATETIME DEFAULT NULL
ALTER TABLE `Order` ADD COLUMN status_note  VARCHAR(200) DEFAULT NULL  -- 取消/退貨原因（選填）
```

### 2.1 設計取捨
- **`order_status` 與 `payment_method` 正交**：正常單 `order_status='active'`，付款狀態照舊跑。取消/退貨是「訂單是否作廢」維度，跟付款維度互不干擾。
- **軟刪除**：取消/退貨不刪資料，只改 `order_status` → 保留完整記錄可追查。
- **退貨先不記退款金額**：退款走人工銀行轉帳，金額已在訂單 `grand_total`。之後要再加 `refund_amount` 很容易（YAGNI）。

### 2.2 值域
| 欄位 | 值 |
|------|----|
| `order_status` | `active`（預設）/ `cancelled` / `refunded` |
| `cancelled_by` | `customer` / `admin` / NULL |

---

## 3. 狀態機規則

| 動作 | 誰 | 前提狀態 | 結果 |
|------|----|---------|------|
| 取消訂單 | 用戶 | `order_status='active'` AND `payment_method='unpaid'` AND 是自己的單 | `cancelled` |
| 取消訂單 | 後台 | `order_status='active'` AND `is_picked_up=FALSE` | `cancelled` |
| 標記退貨 | 後台 | `order_status='active'` AND `is_picked_up=TRUE` | `refunded` |

### 3.1 規則細節
- **用戶取消**：只能取消「自己的」（`line_id == session.line_id`）+ 狀態 `active` + `payment_method='unpaid'`。已付款/已對帳取消不了 → UI 顯示「請聯繫客服」。
- **後台取消**：可取消任何 `active` 且**未交付**的單。已交付的不能取消（要走退貨）。
- **後台退貨**：只能對 `active` 且**已交付**的單標記。
- **終態不可逆**：`cancelled` / `refunded` 都是終點，不能轉回 `active`。誤操作復原屬「管理員修正」功能，不在本輪。
- **冪等保護**：已是 `cancelled`/`refunded` 的單重複操作 → 回 409，不二次觸發。

---

## 4. 後端 API

### 4.1 `POST /api/orders/<order_id>/cancel`（用戶取消）
- **公開端點**，但嚴格驗證。
- 限速：沿用 `_check_order_rate`（30/小時/IP）。
- order_id 格式驗證（純數字 10–25 碼）。
- **核心 SQL（四重條件一次擋掉越權 + 越狀態）**：
  ```sql
  UPDATE `Order`
  SET order_status='cancelled', cancelled_at=NOW(), cancelled_by='customer'
  WHERE order_id=%s
    AND line_id=%s                      -- 只能取消自己的
    AND order_status='active'
    AND payment_method='unpaid'
  ```
- 前置條件：`session.line_id` 必須存在，否則 401。
- rowcount=0 → 回 409 `{"ok": false, "error": "此訂單無法取消"}`（涵蓋：非自己的 / 已付款 / 已取消）。
- 成功回 `{"ok": true}`。

### 4.2 `POST /api/orders/<order_id>/admin-cancel`（後台取消）
- `@login_required` + `@csrf_required`。
- body：`{ "note": "原因（選填）" }`，長度上限 200。
- SQL：
  ```sql
  UPDATE `Order`
  SET order_status='cancelled', cancelled_at=NOW(), cancelled_by='admin', status_note=%s
  WHERE order_id=%s AND order_status='active' AND is_picked_up=FALSE
  ```
- rowcount=0 → 409「此訂單無法取消（已交付或已是終態）」。
- `audit('admin_cancel', order_id=..., note=...)`。

### 4.3 `POST /api/orders/<order_id>/refund`（後台退貨）
- `@login_required` + `@csrf_required`。
- body：`{ "note": "退貨原因（選填）" }`。
- SQL：
  ```sql
  UPDATE `Order`
  SET order_status='refunded', refunded_at=NOW(), status_note=%s
  WHERE order_id=%s AND order_status='active' AND is_picked_up=TRUE
  ```
- rowcount=0 → 409「此訂單無法標記退貨（尚未交付或已是終態）」。
- `audit('refund', order_id=...)`。

### 4.4 改 `GET /api/orders/mine`
- SELECT 多回傳 `order_status`、`cancelled_at`、`refunded_at`。
- **WHERE：進行中的全回 + 60 天內的歷史全回**（含已取消/退貨，給「已完成」tab 當歷史）：
  ```sql
  WHERE line_id=%s
    AND (
      is_picked_up = FALSE                                  -- 所有進行中（含待付款/待出貨/未交付的取消）
      OR order_time >= DATE_SUB(NOW(), INTERVAL 60 DAY)     -- 60 天內歷史（已取貨/取消/退貨）
    )
  ```
- 前端依 `order_status` + `is_picked_up` 分桶：`cancelled`/`refunded` 一律歸「已完成」tab，**不會出現在「待付款」**（見 §5.1 分桶邏輯）。所以待付款分類自然乾淨，不需在 SQL 排除。

### 4.5 改 `GET /api/orders`（admin list）
- 每筆回傳 `order_status`、`cancelled_by`、`status_note`。
- 新增 query param `show_voided`（預設 0）：
  - `show_voided=0` → `WHERE order_status='active'`（預設只看正常單）。
  - `show_voided=1` → 不過濾（含 cancelled/refunded）。

---

## 5. 前端 UI

### 5.1 用戶端（pos.js「我的訂單」抽屜）

**前端分桶邏輯 `categorizeOrder` 擴充**：
```
if order_status == 'cancelled'  → bucket 'done'（歸到已完成 tab，灰標籤）
if order_status == 'refunded'   → bucket 'done'（橘標籤）
（其餘照舊：pending / shipping / done）
```

**待付款 tab 卡片**：
- 若 `status='unpaid'`（payment_method=unpaid 且 active）→ 多一個 **「取消訂單」** 按鈕（紅色 ghost，btn-sm）。
- 點 → confirm「確定取消訂單 #xxx？取消後無法復原」→ 呼叫 `/cancel`。
- 成功 → `refreshMyOrdersUI()` 重整（該單轉到「已完成」tab 顯示「已取消」灰標籤）+ toast「訂單已取消」+ 從 localStorage 待付款移除。

**已完成 tab（語意擴大為「已結束的訂單」）**：
| order_status | 標籤文字 | 顏色 |
|--------------|---------|------|
| active + is_picked_up | ✓ 已完成取貨 | 綠/灰 |
| cancelled | ✗ 已取消 | 灰 |
| refunded | ↺ 已退貨 | 橘 |

### 5.2 後台（admin.js 訂單列）

**操作區依狀態加按鈕**：
- `active` + 未交付 → 加 **「取消」**（紅 ghost btn-sm）。
- `active` + 已交付 → 加 **「退貨」**（橘 btn-sm）。
- 已是 cancelled/refunded → 不顯示這些按鈕。

**操作流程**：點按鈕 → confirm modal（可輸入選填原因，沿用現有 `confirm-modal` + 加一個 input）→ 呼叫對應 API → 成功重整列表。

**訂單列狀態呈現**：
- `cancelled` → 整列灰階 + 紅色「已取消」標籤（顯示 cancelled_by：顧客自取消/後台取消）。
- `refunded` → 橘色「已退貨」標籤。

**側邊欄**：新增「顯示已取消/退貨」checkbox（對應 `show_voided`），預設不勾（只看正常單）。

---

## 6. 安全性

- **用戶取消越權防護**：SQL `WHERE` 同時鎖 `line_id=自己` + `order_status='active'` + `payment_method='unpaid'`，四重條件一次擋掉所有越權/越狀態，不靠額外 if 判斷。
- **後台端 CSRF + login**：admin-cancel / refund 都 `@login_required @csrf_required`。
- **冪等**：所有狀態轉移 SQL 都帶 `WHERE order_status='active'`，重複操作 rowcount=0 → 409，不會二次執行。
- **audit log**：admin-cancel / refund 寫入 audit.log。
- **note 長度限制**：200 字，防 DoS。

---

## 7. 測試計畫

| 案例 | 預期 |
|------|------|
| 用戶取消自己的 unpaid 單 | 成功，order_status=cancelled |
| 用戶取消自己的 pending_check 單 | 409（已回報轉帳不可自取消） |
| 用戶取消別人的單（偽造 order_id） | 409（line_id 不符） |
| 用戶未登入呼叫 cancel | 401 |
| 後台取消未交付單 | 成功 |
| 後台取消已交付單 | 409（要走退貨） |
| 後台對已交付單退貨 | 成功，order_status=refunded |
| 後台對未交付單退貨 | 409（尚未交付） |
| 重複取消同一單 | 第二次 409 |
| 用戶端待付款 tab | 已取消的單不出現 |
| 用戶端已完成 tab | 顯示已取貨 / 已取消 / 已退貨（標籤區分） |
| 後台預設列表 | 不顯示 cancelled/refunded |
| 後台勾「顯示已取消/退貨」 | 顯示，含灰/橘標籤 |

---

## 8. 不在此範圍

- ❌ QRCode 生成（子項 4）
- ❌ 掃碼核銷網頁（子項 5）
- ❌ 退款金額記錄（之後加 refund_amount）
- ❌ 取消/退貨後復原（管理員修正功能）
- ❌ 取消/退貨的 LINE 推播通知（可之後加 cancelled/refunded queue type）

---

## 9. 風險與回滾

- **新欄位 idempotent**：ALTER TABLE 包在 try/except，重複執行不報錯；舊資料 order_status 預設 'active' 不影響既有訂單。
- **回滾**：移除新 API + 前端按鈕即可；order_status 欄位留著不影響（全是 active）。
