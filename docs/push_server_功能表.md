# push_sever.py 功能表（LINE 通知伺服器 / Bot）

> 角色：獨立的 LINE Messaging API 伺服器。負責「把通知推給顧客」+「LINE Bot 對話」。
> 跑在 port **4999**，並用 **ngrok** 對外（給 LINE webhook 與 QR 圖網址用）。
> 與 `app.py` 分工：`app.py` 只把通知「排隊」進 DB；**真正推播都在這支**。

---

## 1. HTTP 路由

| 路由 | 方法 | 對外? | 說明 | 安全 |
|---|---|---|---|---|
| `/` | GET | 是 | 健康檢查，回 `LINE Bot is running!` | — |
| `/test` | GET | 是 | 測試端點 | — |
| `/callback` | POST | 是 | **接收 LINE webhook 事件**（用戶傳訊息） | ✅ 驗 `X-Line-Signature`（HMAC-SHA256），偽造請求回 400 |
| `/order-qr/<filename>` | GET | 是 | 提供取貨 QR 成品圖給 LINE 抓圖 | ✅ 嚴格檔名格式 `<order_id>_<token>.png`，擋路徑穿越 |

---

## 2. LINE Bot 對話功能（`process_user_message`）

顧客在 LINE 聊天室輸入指令時觸發。重複事件會先被 `is_duplicate_webhook_event` 過濾。

| 顧客輸入 | 動作 | 對應函式 |
|---|---|---|
| `取消` / `cancel` | 取消目前操作狀態 | — |
| `會員資料`/`登記`/`會員登記`/`註冊`/`修改資料` | 顯示現有綁定資料 + 進入「等待輸入姓名電話」狀態 | `check_user_registered` |
| （等待狀態下）`姓名 電話` | 綁定 / 更新會員資料 | `register_customer` |
| `即時訂單`/`訂單狀態`/`進行中訂單` | 查進行中訂單 | `query_active_orders` |
| `歷史訂單`/`歷史紀錄` | 查歷史訂單 | `query_historical_orders` |
| `查詢訂單`/`訂單查詢`/`我的訂單` | 查全部訂單 | `query_user_orders` |
| 其他文字 | 靜默（回 `None`），交給 LINE 官方帳號自動回應 | — |

**輸入防呆**：電話自動濾非數字、驗證 09 開頭 10 碼；姓名含空格時取最後一段當電話。
**查訂單需先綁定**：未綁定者查訂單會被擋，提示先登記。

---

## 3. 通知佇列處理（`process_notification_queue`）

背景執行緒，**每 5 秒**輪詢 `LineNotificationQueue` 資料表（`app.py` 寫入），把 `pending` 的通知推給顧客。

| 通知類型 | 觸發來源（app.py） | 推播內容 | 備註 |
|---|---|---|---|
| `order_confirmed` | 後台「確認訂單」 | **文字 + 取貨 QR 圖**（下單成功，帶 QR 來付現） | 用 `send_text_and_image`；圖失敗自動降級只推文字 |
| `payment_confirmed` | 後台「✓ 收款」 | 文字（收款確認） | `send_line_message` |
| `order_arrived` | 後台「推播」 | 文字（貨到通知，可取貨） | `send_line_message` |
| `order_placed` | 〔目前未使用〕 | 文字（下單通知） | ⚠️ app.py 下單不推播，此分支為**死碼**（保留無害） |

**流程**：撈 `pending` → 用 `phone` 查 `CustomerLineBindings` 取 `LINE_ID` → 推播 → 標 `done`。
**找不到綁定** → 標 `failed`（之後該用戶完成綁定時，`register_customer` 會把 failed 的重新排回 `pending`）。

---

## 4. 會員綁定（`register_customer`）

| 步驟 | 動作 |
|---|---|
| 1 | 檢查電話是否已被**別的** LINE 帳號綁定 → 是則拒絕（防盜綁） |
| 2 | 寫入 / 更新 `CustomerLineBindings`（LINE_ID 為主鍵，電話 UNIQUE） |
| 3 | **回溯**：把該電話過去未綁定的歷史訂單補上 `line_id` |
| 4 | **重排**：把該電話先前 `failed` 的通知改回 `pending`，重新推送 |

---

## 5. 輔助函式

| 函式 | 用途 |
|---|---|
| `send_line_message(user_id, text)` | 推單則文字訊息 |
| `send_text_and_image(user_id, text, image_url)` | 推「文字＋圖片」一則；圖失敗降級只推文字 |
| `check_user_registered(user_id)` | 查該 LINE 用戶是否已綁定，回 (是否, 姓名, 電話) |
| `get_user_profile(user_id)` | 呼叫 LINE API 取用戶暱稱 / 頭像 |
| `query_active_orders` / `query_historical_orders` / `query_user_orders` | 組「進行中 / 歷史 / 全部」訂單查詢回覆文字 |
| `is_duplicate_webhook_event(event)` | 用 `webhookEventId` 去重（記憶體 set，上限 1 萬筆） |
| `init_notification_queue()` | 啟動時建立 `LineNotificationQueue` 資料表 |
| `get_database_connection()` | 連到 MySQL（`susu` 資料庫，每次呼叫開新連線） |

---

## 6. 啟動流程（`__main__`）

1. `init_notification_queue()` 建表
2. 啟動背景執行緒跑 `process_notification_queue`（每 5 秒輪詢）
3. `ngrok.connect(4999)` 建立對外通道
4. **自動更新 LINE Developer 的 Webhook URL**（`{ngrok}/callback`）
5. 若未設 `PUBLIC_BASE_URL` → 自動設成 ngrok 網址（給 QR 圖網址用）
6. Flask 跑在 `0.0.0.0:4999`

---

## 7. 環境變數（`.env`）

| 變數 | 用途 |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | 推播 / 回覆訊息用 |
| `LINE_CHANNEL_SECRET` | **驗 webhook 簽章**（必須正確，否則 webhook 全被 400 擋） |
| `NGROK_AUTH_TOKEN` | ngrok 通道 |
| `PUBLIC_BASE_URL` | （選用）QR 圖網址前綴；未設則用 ngrok 網址 |
| DB 連線 | 目前寫死在 `DB_CONFIG`（host/user/password）；建議改讀 env |

---

## 8. 安全重點

- ✅ **Webhook 簽章驗證**（HMAC-SHA256）：擋偽造事件（防有人偽造綁定盜看訂單）
- ✅ **QR 圖檔名防穿越 / 不可猜**
- ⚠️ DB 帳密目前寫死在原始碼 → 若此檔進版控，建議改讀環境變數
- ℹ️ webhook 出錯一律回 200（避免 LINE 重試風暴）；簽章失敗才回 400
