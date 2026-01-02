# scripts/renew.py
import os
import requests
from playwright.sync_api import sync_playwright

SERVER_ID = os.environ.get("SERVER_ID")
URL = f"https://hub.weirdhost.xyz/server/{SERVER_ID}/"
REMEMBER_COOKIE = os.environ.get("REMEMBER_COOKIE")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

def send_tg(msg):
    if TG_BOT_TOKEN and TG_CHAT_ID:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg}
        )

def main():
    if not REMEMBER_COOKIE or not SERVER_ID:
        send_tg("❌ 续期失败: REMEMBER_COOKIE 或 SERVER_ID 未设置")
        exit(1)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        
        context.add_cookies([{
            "name": "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d",
            "value": REMEMBER_COOKIE,
            "domain": "hub.weirdhost.xyz",
            "path": "/"
        }])
        
        page = context.new_page()
        
        try:
            page.goto(URL, wait_until="networkidle", timeout=30000)
            
            if "login" in page.url.lower():
                send_tg("❌ 续期失败: Cookie 已过期")
                exit(1)
            
            btn = page.locator("span:has-text('시간추가')").first
            btn.wait_for(timeout=10000)
            btn.click()
            page.wait_for_timeout(3000)
            
            send_tg(f"✅ 服务器 {SERVER_ID} 续期成功!")
            
        except Exception as e:
            send_tg(f"❌ 续期失败: {str(e)}")
            exit(1)
        finally:
            browser.close()

if __name__ == "__main__":
    main()
