# 轉帳改現金（取貨付現）— 設計規格

**日期：** 2026-06-04
**目標範圍：** 因轉帳有三方金流風險，付款方式改為「取貨付現金」。用戶端移除轉帳流程、改成下單即成立；後台新增「確認訂單」動作（標記現金 + 推下單成功通知 + 生 QR）。
**不在範圍內：** 把 QR 透過 LINE 圖片訊息推給用戶（子項 5）、掃碼核銷網頁（子項 5）。

---

## 1. 問題定義

轉帳到個人帳戶有三方金流風險，停用線上轉帳，改成顧客**取貨時付現金**。這讓整套「下單→待付款→轉帳→回報→對帳」流程不再需要，要簡化成「下單即成立→備貨→取貨付現」。

---

## 2. 新流程總覽

```
顧客下單（payment_method=unpaid, order_status=active）
  ↓ 用戶端：成立確認 modal（取貨付現，無轉帳元素）
① 店家按「確認訂單」（接單）
     → payment_method = 'cash'
     → 推 LINE「下單成功，帶此 QR 來取貨付款」（含 QR — 推圖先不做）
     → 生成 QR 存本地「訂單」資料夾
  ↓ 農場備貨
② 店家按「推播」（貨備好）
     → 推 LINE「可以來取貨了」（order_arrived，原本就有）
  ↓
顧客帶 QR 來 → 取貨付現金 → 店家按「取貨」結案
```

---

## 3. 用戶端改動（pos.html / pos.js / pos.css）

### 3.1 下單成功 modal：轉帳 modal → 成立確認 modal
**移除**（轉帳專用）：
- 轉帳 QR 圖 + 放大 modal + 下載按鈕
- 銀行帳號 + 複製按鈕
- 轉帳備註末 5 碼 + 複製
- 「稍後再付」/「我已轉帳完成」/「取消已付款回報」/「我知道了⏳」按鈕
- 3 秒倒數通知 modal（`paid-confirm-modal`）
- 相關 JS：`markOrderPendingCheck` / `cancelOrderPendingCheck` / `fillPayModal` 的轉帳分支 / `showPaidConfirmModal` / QR 放大 / 銀行複製

**新 modal 內容**：
```
標題：訂單已成立 ✓
訂單編號  260604xxx...
取件人    王小明
應付金額  NT$ 300（取貨付現）

我們會在確認庫存後以 LINE 通知您取貨。
取貨時請帶現金 NT$ 300。

       [ 我知道了 ]
```
- 一個「我知道了」按鈕關閉
- 仍保留「下單需登入 LINE」modal（為了通知 + 追蹤）

### 3.2「我的訂單」抽屜：3 tab → 2 tab
```
原本：[待付款] [待出貨] [已完成]
改成：[準備中] [已完成]
```
- **準備中**：`active` 且未取貨。狀態文案：
  - `is_notified=false` → 「訂單成立 · 備貨中」
  - `is_notified=true` → 「✅ 可取貨，請帶現金來取」
- **已完成**：已取貨 ✓ / 已取消 ✗ / 已退貨 ↺（標籤區分，沿用現有）
- 分桶 `categorizeOrder`：
  ```
  if order_status in (cancelled, refunded) → 'done'
  if is_picked_up → 'done'
  else → 'preparing'   （取代原本 pending/shipping 兩桶）
  ```
- **移除**：localStorage `veg_pending_orders` 待付款清單整套、「查看付款碼」「移除」「取消訂單」按鈕中與付款相關的；保留「取消訂單」（未取貨可取消）
- **紅色徽章**：顯示「準備中」訂單數

> 註：用戶取消功能保留，**沿用現有 `/api/orders/<id>/cancel` 後端不改**（條件 `active + payment_method='unpaid'`）。語意：店家「確認訂單」前（還是 unpaid）用戶可自己取消；一旦店家確認訂單（變 cash + 生 QR + 備貨），用戶不能自己取消，要聯繫客服。前端取消按鈕顯示條件：preparing bucket 且 `payment_method='unpaid'`。

### 3.3 移除 localStorage 購物車外的待付款殘留
- `PENDING_KEY` (`veg_pending_orders`) 相關函式整批移除（loadLocalPending 等）
- 購物車 localStorage（`CART_KEY`）保留不動

---

## 4. 後台改動（admin.html / admin.js / admin.css）

### 4.1 新增「確認訂單」按鈕（取代「✓收款」）
- 顯示時機：`order_status='active'` 且 `payment_method='unpaid'` 且未取貨
- 按下 → confirm modal → 呼叫新 endpoint `POST /api/orders/<id>/confirm-order`
- 行為（見 §5.1）：標記 cash + 推「下單成功」通知 + 生 QR

### 4.2 「推播」按鈕保留不動
- 原本的 `notify`（推 order_arrived 到貨通知）完全保留
- 顯示時機微調：`active` 且未取貨且 `payment_method != 'unpaid'`（已確認訂單後才推「可取貨」較合理；unpaid 的先確認訂單）

### 4.3 收款下拉、取貨、取消、退貨
- 全部保留不動（收款下拉仍有 現金/轉帳，供手動微調）

---

## 5. 後端改動（app.py）

### 5.1 新 endpoint `POST /api/orders/<id>/confirm-order`
- `@login_required @csrf_required`
- 限 `order_status='active'` 且 `payment_method='unpaid'`
- 一個 transaction 內：
  ```sql
  UPDATE `Order`
  SET payment_method='cash', qr_token=%s
  WHERE order_id=%s AND order_status='active' AND payment_method='unpaid'
  ```
  + `_enqueue_line_in_cursor(cur, 'order_confirmed', phone, payload)`
  （payload: order_id, amount, qr 提示文字）
- commit 後 `generate_order_qr(order_id, qr_token)`（存本地，不推圖）
- rowcount=0 → 409
- `audit('confirm_order', order_id=...)`

### 5.2 QR 生成從「收款」移走
- `confirm-payment` 和 `update_payment` 裡的 `generate_order_qr` **移除**（QR 改在 confirm-order 生成）
- `qr_token` 寫入也從那兩處移除（移到 confirm-order）

### 5.3 LINE 通知類型
- 新增 `order_confirmed`：「下單成功，帶此 QR 來取貨付款」（worker 端要加 template；推圖之後做）
- `order_arrived`（可取貨）保留不動
- `payment_confirmed`（轉帳收款確認）→ 現金模式用不到，但保留 code 不刪（後台手動改轉帳時仍會觸發）

### 5.4 `/api/orders/mine`
- 不變（已回傳 order_status / is_picked_up / payment_method，前端重新分桶即可）

---

## 6. Worker（push_sever.py，用戶另外維護）
新增 `order_confirmed` 分支（這次先存本地不推圖，但通知文字可先加）：
```
✅ 下單成功！
您的訂單 #{order_id} 已確認，金額 NT$ {amount}。
請帶著我們提供的 QR 條碼前來取貨並付現金。
（採收完成後會再通知您可取貨時間）
```

---

## 7. 安全性
- `confirm-order`：login + csrf + 冪等（WHERE active+unpaid）+ audit
- QR 生成失敗不影響確認訂單流程（try/except）
- 其餘沿用既有防護

---

## 8. 測試計畫
| 案例 | 預期 |
|------|------|
| 下單成功 | 跳「成立確認」modal（無轉帳元素），顯示金額 + 取貨付現提示 |
| 用戶「我的訂單」 | 兩個 tab：準備中 / 已完成 |
| 後台 active+unpaid 單 | 顯示「確認訂單」按鈕 |
| 按「確認訂單」 | payment_method=cash + 推 order_confirmed + 生 QR 存本地 |
| 確認訂單後 | 「推播」按鈕出現（可推到貨通知） |
| 重複確認訂單 | 409 |
| QR 圖 | 存到「訂單/<order_id>.png」，內容 order_id|token |
| 後台收款下拉 | 仍可手動選現金/轉帳 |
| 用戶取消未通知訂單 | 成功 |
| 用戶取消已通知（可取貨）訂單 | 擋下（聯繫客服） |

---

## 9. 不在範圍
- ❌ QR 透過 LINE 圖片訊息推給用戶（子項 5）
- ❌ 掃碼核銷網頁（子項 5）
- ❌ 移除 payment_confirmed / 轉帳相關後端 code（保留，後台手動改轉帳仍可用）

---

## 10. 風險與回滾
- 轉帳 modal 的 HTML/CSS/JS 移除較多，但都是獨立區塊，回滾 = git revert
- `confirm-order` 是新 endpoint，不影響既有；QR 生成移位是搬動非重寫
- 既有「已轉帳」訂單（pending_check/transfer）不受影響，後台仍可處理
