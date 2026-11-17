# 上線指南：eatgoodveg.com → 你的 NAS（Cloudflare Tunnel）

目標：用**免費** Cloudflare Tunnel 讓網域連到 NAS 上的網站。
不用固定 IP、不用開 port、附免費 HTTPS。換網路（SIM↔家用）都不用改設定。

前提：① 已買 eatgoodveg.com　② NAS 開著、連著網路　③ app.py / push_sever.py 能在 NAS 上跑（資料庫在遠端，NAS 不用裝 DB）。

---

## A. 網域接上 Cloudflare（一次性，約 10 分鐘）
1. 註冊免費帳號 → dash.cloudflare.com
2. **Add a site** → 輸入 `eatgoodveg.com` → 選 **Free** 方案
3. Cloudflare 給你兩個 nameserver（像 `xxx.ns.cloudflare.com`）
4. 到**你買網域的網站後台** → 把 nameserver 改成 Cloudflare 給的那兩個
5. 等到 Cloudflare 顯示 **Active**（幾分鐘～幾小時）

## B. 在 NAS 上把程式跑起來
1. NAS 裝 **Python 3**（Synology：套件中心 / 或用 Container Manager 跑 Docker）
2. 把整個專案資料夾 + `.env` 放上 NAS（`.env` 別外流）
3. 裝套件：`pip install -r requirements-lock.txt`
4. 開兩支程式：
   - `python app.py` → 聽 **:5000**（網站本體）
   - `python push_sever.py` → 聽 **:4999**（LINE 通知 + webhook）
5. 先在 NAS 本機開 `http://localhost:5000` 確認有反應

## C. 裝 Cloudflare Tunnel
1. Cloudflare 後台 → **Zero Trust → Networks → Tunnels → Create a tunnel**
2. 選 **Cloudflared** → 命名（如 `goodveg`）→ 它給你一段安裝指令/token
3. 在 NAS 裝 `cloudflared` 並執行（Synology 最簡單：Container Manager 跑官方 `cloudflare/cloudflared` 容器，貼上 token）
4. 設定 **Public Hostname（路由）**：

   | 網址 | 指到 |
   |---|---|
   | `eatgoodveg.com` | `http://localhost:5000` |
   | `bot.eatgoodveg.com` | `http://localhost:4999` |

   （第二條 `bot.` 是給 LINE webhook 用）
5. 存檔，Cloudflare 自動配 HTTPS。開 `https://eatgoodveg.com` 看到首頁 = 成功 🎉

## D. 改設定（從 ngrok 換成固定網域）
1. `.env` 加/改：
   ```
   PUBLIC_BASE_URL=https://eatgoodveg.com
   FLASK_SECURE_COOKIE=1
   ```
2. **LINE 後台**（developers.line.biz）：
   - LIFF 端點 URL → `https://eatgoodveg.com/pos`
   - Messaging API Webhook → `https://bot.eatgoodveg.com/callback` → 按 **Verify**
3. ⚠️ `push_sever.py` 裡的 **ngrok 自動建通道**那段要關掉——否則它一啟動就會把 webhook 蓋回 ngrok 網址，跟 Cloudflare 打架。**這個小改動跟我說一聲，我幫你改成吃固定網域。**
4. 重啟 `app.py` 和 `push_sever.py`

## E. 上線前檢查
- [ ] `https://eatgoodveg.com` 的首頁、/pos、/admin 都開得起來
- [ ] 後台**開 2FA**（`python setup_2fa.py`）、**改掉預設帳密**
- [ ] 手機 LINE 登入下單 → 收到 LINE + QR 圖 → 核銷 → 後台即時更新
- [ ] 做一次資料庫備份（見 `docs/伺服器維運安全清單.md`）

---

## 常見問題
- **網站打不開**：先確認 NAS 本機 `localhost:5000` 有反應；再看 Cloudflare Tunnel 狀態是不是 **Healthy**。
- **LINE 收不到 QR 圖**：確認 `.env` 的 `PUBLIC_BASE_URL=https://eatgoodveg.com` 且 app.py 已重啟。
- **換網路（SIM→家用寬頻）**：Cloudflare Tunnel 不受影響，**不用改任何設定**。
- **正式營運**：建議網路用家用/店面寬頻或便宜 VPS，別用學校網路（營利違規）。SIM 適合先測試。
