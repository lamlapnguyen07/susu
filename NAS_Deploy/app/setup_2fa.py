"""
後台 2FA（TOTP）一次性設定工具。

流程：
  跑這支 → 用 Google Authenticator 掃 QR（或手動輸入密鑰）
  → 把印出的 ADMIN_TOTP_SECRET 貼進 .env → 重啟 app.py
  → 刪掉 2fa_setup_qr.png（內含密鑰，別外流）

掉手機 / 換手機：重跑本程式產生新密鑰、更新 .env 即可，不會被鎖死。

用法：python setup_2fa.py
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ADMIN_USER = os.environ.get('ADMIN_USER', 'iothouse')
ISSUER = os.environ.get('TOTP_ISSUER', '食在好蔬')
QR_PATH = '2fa_setup_qr.png'


def main():
    try:
        import pyotp
    except ImportError:
        print('✗ 缺少 pyotp，請先執行： pip install pyotp')
        return

    secret = pyotp.random_base32()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=ADMIN_USER, issuer_name=ISSUER)

    # 產生 QR 圖（用已安裝的 qrcode 套件）
    qr_ok = False
    try:
        import qrcode
        qrcode.make(uri).save(QR_PATH)
        qr_ok = True
    except Exception as e:
        print(f'(QR 圖產生失敗，可改用手動輸入密鑰) {e}')

    line = '=' * 56
    print('\n' + line)
    print('  後台 2FA（二步驗證）設定')
    print(line)
    if qr_ok:
        print(f'  1) 用 Google Authenticator 掃描圖片：{QR_PATH}')
        print('     （開啟該圖檔，App 內按「+」→ 掃描 QR code）')
        print('     或手動新增帳戶，輸入：')
    else:
        print('  1) 在 Google Authenticator 手動新增帳戶，輸入：')
    print(f'       帳戶名稱：{ISSUER} ({ADMIN_USER})')
    print(f'       金鑰    ：{secret}')
    print('       類型    ：採時間 (Time-based)')
    print()
    print('  2) 把這一行貼進專案的 .env：')
    print()
    print(f'       ADMIN_TOTP_SECRET={secret}')
    print()
    print('  3) 重新啟動 app.py。下次登入輸入帳密後，會再要求 6 位數。')
    if qr_ok:
        print(f'  4) 設定完成後，請刪除 {QR_PATH}（內含密鑰）。')
    print(line)
    print('  ⚠ 密鑰等同第二把鑰匙：別給別人、別 commit 進 git。')
    print('  ⚠ 掉手機 → 重跑本程式換新密鑰、更新 .env 即可，不會被鎖死。')
    print(line + '\n')


if __name__ == '__main__':
    main()
