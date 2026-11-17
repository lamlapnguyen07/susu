# 後台登入 TOTP 2FA 設計規格

- 日期：2026-06-09
- 狀態：已通過設計，待實作
- 目標：用「簡單但安全」的方式強化後台登入——帳密外洩也進不來

## 背景與期望校正

「絕對不會被攻擊」無法達成（沒有系統 100% 安全）。務實目標：把攻擊成本墊高、出事能止血、不要自己開門。經威脅排序，唯一值得寫程式做的是 **#1 防帳號被冒用**；做好它同時補強 #2 個資外洩；#3 伺服器中毒屬維運習慣（無檔案上傳，攻擊面小）。

選定方案：**TOTP 2FA（Google Authenticator），純 2FA、不做記住裝置**。

## 現況

- 單一 admin：`ADMIN_USER` / `ADMIN_PASS` 存 `.env`，`_verify_credentials` 用 `hmac.compare_digest` timing-safe 比對
- `api_login`：限流（`_check_login_allowed`，3次/900秒）→ 驗帳密 → `session.clear()` 重發 session（防 fixation）→ 設 `session['user']` + csrf
- 登入頁 `templates/login.html`：單步、inline JS，POST `/api/login`
- 已裝 `qrcode`；未裝 `pyotp`

## 設計

### 1. 密鑰儲存
- `.env` 新增 `ADMIN_TOTP_SECRET`（base32）。單一 admin 一把鑰匙。
- **未設 → 2FA 關閉**（登入照舊，不會把自己卡死；啟動時印提醒）。**有設 → 強制**。漸進啟用、零風險。

### 2. 一次性設定腳本 `setup_2fa.py`（離線執行）
- `secret = pyotp.random_base32()`
- `uri = pyotp.totp.TOTP(secret).provisioning_uri(name=ADMIN_USER, issuer_name='食在好蔬')`
- 用 `qrcode` 產生 `2fa_setup_qr.png`
- 終端印出：密鑰文字（手動輸入備援）+ 操作步驟（掃 QR → 貼 `ADMIN_TOTP_SECRET` 進 `.env` → 重啟 → 刪 QR 圖）
- 不開任何對外設定路由（避免敏感端點）。

### 3. 後端：登入拆兩步
- `POST /api/login` {username, password}（沿用限流）：
  - 驗帳密成功 **且** `ADMIN_TOTP_SECRET` 已設 → **先不授權**。設 `session['pending_2fa_user']=user`、`session['pending_2fa_ts']=now`。回 `{ok:True, need_2fa:True}`。
  - 驗帳密成功 **且** 未設密鑰 → 維持現行行為（直接授權 + 回 csrf）。
  - 驗帳密失敗 → 現行失敗路徑（`_record_login_fail` + sleep 0.4 + 401）。
- `POST /api/login/2fa` {code}：
  - 需有未過期的 `pending_2fa_user`（逾時 5 分鐘作廢）。沿用 IP 限流。
  - `pyotp.TOTP(secret).verify(code, valid_window=1)`（±30 秒時鐘容忍）。
  - 成功 → `session.clear()` → 設 `session['user']`、`session.permanent=True`、新 csrf、`_touch_session()`、清限流、`audit('login_2fa_ok')`、回 `{ok:True, csrf_token}`。
  - 失敗 → `_record_login_fail(ip)`、`audit('login_2fa_fail')`、sleep 0.4、回 401。
- 輔助：`_verify_totp(code)` 包 pyotp（密鑰未設時回 False）。

### 4. 前端：登入頁加第二步
- 第一步回 `need_2fa:true` → 隱藏帳密欄、顯示 6 位數欄位（`inputmode=numeric`、`autocomplete=one-time-code`、`maxlength=6`、autofocus），送 `/api/login/2fa`。
- 成功 → 轉 `/admin`。失敗 → 顯示錯誤、可重輸。
- 提供「返回」回到帳密步（清掉 pending 由 server 逾時處理即可）。

### 5. 掉手機備援（止血後路）
- 管理員握有 `.env` → 改/清 `ADMIN_TOTP_SECRET`（重跑 `setup_2fa.py`）即可重設，**永不被鎖死**。
- 因此**不做備用碼**（YAGNI）。

### 6. 啟動提醒
- 沿用現有 default-password 警告機制；新增：若 `ADMIN_TOTP_SECRET` 未設 → 印「⚠️ 2FA 未啟用，建議執行 setup_2fa.py」。

## 安全性質
- 帳密外洩也進不來（第二因素）
- 密鑰不寫死在程式（`.env`，已 gitignore）
- 不開對外設定端點（離線腳本）
- 6 碼爆破：1,000,000 組合 × valid_window=1 × 既有 3次/900秒鎖 → 不可行
- session fixation 防護沿用（`session.clear` + 重發）
- 永不被鎖死（掉手機 → 改 `.env`）

## 動到的檔案
- `requirements.txt` / `requirements-lock.txt`：加 `pyotp`
- `app.py`：讀 `ADMIN_TOTP_SECRET`；`api_login` 加 2FA 分支；新增 `/api/login/2fa`；`_verify_totp`；啟動提醒
- `templates/login.html`：加第二步 UI + 兩步 JS
- 新增 `setup_2fa.py`
- `.gitignore`：加 `2fa_setup_qr.png`

## YAGNI（不做）
備用碼、多管理員各自密鑰、記住裝置、簡訊/Email OTP。

## 驗收
1. 未設密鑰：登入行為與現在完全相同 + 啟動印 2FA 未啟用提醒。
2. 設密鑰後：帳密對 → 要求 6 碼 → 正確碼才進後台。
3. 錯誤 6 碼 → 擋下；連錯觸發既有限流鎖。
4. 過期碼 / 別的 App 的碼 → 擋下。
5. pending 逾時 5 分鐘後再送碼 → 要求重新登入。
6. 模擬掉手機：改 `.env` 密鑰 → 用新 Authenticator 條目可正常登入。
