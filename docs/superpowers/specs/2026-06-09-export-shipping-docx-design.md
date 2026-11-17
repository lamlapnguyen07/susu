# 後台匯出當日出貨單（Word / .docx）設計規格

- 日期：2026-06-09
- 狀態：已通過設計，待實作

## 目標
後台工具列加一顆按鈕，把「當前所選出貨日」的訂單匯出成一份 Word（.docx）出貨單（一張總表），供列印撿貨/裝袋/出貨。

## 觸發與日期
- 後台加按鈕「📄 匯出出貨單 (Word)」。
- 匯出日期 = 後台當前 `state.selectedDate`（`'all'` | `'none'` | `'YYYY-MM-DD'`）。
  - 真實日期或 `'none'`（未指定出貨日）→ 直接匯出。
  - `'all'`（全部）→ 不匯出，前端提示「請先選一個出貨日再匯出」。

## 抓哪些訂單
- `expected_ship_date = <date>`（`'none'` 時為 `expected_ship_date IS NULL`）
- **AND `order_status = 'active'`**（排除已取消 / 已退貨，那些不出貨）
- 排序：`order_time ASC`

## 版面（一張總表，每筆一列）
欄位：
1. 訂單編號
2. 姓名
3. 電話
4. 品項（packs 壓成一格：`[包1×3] 甜脆萵苣、皺葉萵苣 ｜ pack_remarks`；隨機搭配顯示「農場隨機搭配」）
5. 份數（total_quantity）
6. 金額（grand_total）
7. 付款（unpaid→未確認 / cash→取貨付現 / transfer→轉帳 / pending_check→待對帳）
8. **備註**（order 層 `customer_info.remarks`，如「免單」「外送地址」）

- 標題：「食在好蔬 — 出貨單　YYYY/MM/DD（週X）」（`'none'` 時顯示「未指定出貨日」）
- 表格下方統計：共 N 筆 · 總份數 X · 總金額 NT$ Y
- 中文字型：設 CJK 字型（如「微軟正黑體」），確保 Word 顯示正常。

## 後端
- 新端點 `GET /api/orders/export-shipping?date=<YYYY-MM-DD|none>`，`@login_required`（GET 唯讀，免 CSRF）。
- 驗證 `date` 格式（`^\d{4}-\d{2}-\d{2}$` 或 `none`）；非法 → 400。
- 用 **python-docx**（lazy import；加進 requirements）產生 .docx 到記憶體（`io.BytesIO`），`send_file` 回傳：
  - mimetype `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
  - `as_attachment=True`，檔名 `出貨單_YYYYMMDD.docx`（`none` → `出貨單_未指定.docx`）
- order_details 雙層巢狀：取 `inner = raw.get('order_details', raw)`、`cust = raw.get('customer_info', {})`（沿用先前修正）。
- 找不到任何訂單 → 仍回一份只有標題 + 「（當日無出貨訂單）」的文件，或回 204/JSON 由前端提示；採**回 JSON 404 `{ok:false,error:'當日無訂單'}`**，前端顯示提示（不下載空檔）。

## 前端
- `admin.html`：工具列加按鈕 `#export-shipping-btn`。
- `admin.js`：點擊 → 依 `state.selectedDate` 組 URL → `authFetch`（GET, credentials same-origin）→
  - 200 → 取 `blob` → 建 object URL → 觸發 `<a download>` 下載。
  - 404/JSON → 顯示提示（未選日期 / 當日無訂單）。
- `admin.css`：按鈕樣式（與既有工具列一致）。

## 動到的檔
- `requirements.txt` / `requirements-lock.txt`：加 `python-docx`
- `app.py`：新端點 + docx 產生 + 品項/備註抽取
- `templates/admin.html`：按鈕
- `static/js/admin.js`：下載邏輯
- `static/css/admin.css`：按鈕樣式

## YAGNI（不做）
每單明細頁、PDF、自訂欄位、多日合併、Email 寄送、頁尾簽名欄。

## 驗收
1. 選某出貨日 → 按鈕 → 下載 `出貨單_YYYYMMDD.docx`，內含該日 active 訂單總表 + 統計。
2. 已取消/退貨訂單不出現。
3. 品項、備註（免單）正確顯示，中文不亂碼。
4. 選「全部」→ 提示先選日期，不下載。
5. 當日無訂單 → 提示，不下載空檔。
