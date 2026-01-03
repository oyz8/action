#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weirdhost-auto - main.py
功能：使用 Cookie 登录 → 续期 → 提取新 Cookie → 更新 GitHub Secrets
环境变量：
  - REMEMBER_WEB_COOKIE : cookie 值（必须）
  - REMEMBER_WEB_COOKIE_NAME : cookie 名称（可选，默认 'remember_web'）
  - SERVER_URL : 服务器地址（可选）
  - TG_BOT_TOKEN, TG_CHAT_ID : Telegram 通知（可选）
  - REPO_TOKEN : 用于自动更新 GitHub Secrets（可选但推荐）
  - GITHUB_REPOSITORY : 自动由 GitHub Actions 提供
"""
import os
import asyncio
import aiohttp
import base64
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False
    print("⚠️ PyNaCl 未安装，无法自动更新 Secrets。pip install pynacl")

DEFAULT_SERVER_URL = "https://hub.weirdhost.xyz/server/d341874c"
DEFAULT_COOKIE_NAME = "remember_web"


# ------------------ GitHub Secrets 更新 ------------------
def encrypt_secret(public_key: str, secret_value: str) -> str:
    if not NACL_AVAILABLE:
        raise RuntimeError("PyNaCl 未安装")
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def update_github_secret(secret_name: str, secret_value: str) -> bool:
    repo_token = os.environ.get("REPO_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()

    if not repo_token or not repository or not NACL_AVAILABLE:
        print(f"⚠️ 跳过更新 {secret_name}（缺少 REPO_TOKEN/GITHUB_REPOSITORY 或 PyNaCl）")
        return False

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {repo_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with aiohttp.ClientSession() as session:
        try:
            # 获取 public key
            pk_url = f"https://api.github.com/repos/{repository}/actions/secrets/public-key"
            async with session.get(pk_url, headers=headers) as resp:
                if resp.status != 200:
                    print(f"❌ 获取 public key 失败: {resp.status}")
                    return False
                pk_data = await resp.json()

            # 加密并更新
            encrypted_value = encrypt_secret(pk_data["key"], secret_value)
            secret_url = f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}"
            payload = {"encrypted_value": encrypted_value, "key_id": pk_data["key_id"]}
            
            async with session.put(secret_url, headers=headers, json=payload) as resp:
                if resp.status in (201, 204):
                    print(f"✅ 已更新 Secret: {secret_name}")
                    return True
                print(f"❌ 更新失败: {resp.status}")
                return False
        except Exception as e:
            print(f"❌ 更新 Secret 出错: {e}")
            return False


# ------------------ Telegram 通知 ------------------
async def tg_notify(message: str):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, data={"chat_id": chat_id, "text": message})
        except Exception as e:
            print(f"⚠️ TG 通知失败: {e}")


async def tg_notify_photo(photo_path: str, caption: str = ""):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with aiohttp.ClientSession() as session:
        try:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                data.add_field("caption", caption)
                await session.post(url, data=data)
        except Exception as e:
            print(f"⚠️ TG 图片通知失败: {e}")


# ------------------ Cookie 提取 ------------------
async def extract_remember_cookie(context) -> tuple:
    """提取 remember_web* cookie，返回 (name, value) 或 (None, None)"""
    try:
        cookies = await context.cookies()
        for cookie in cookies:
            if cookie["name"].startswith("remember_web"):
                print(f"🍪 提取到 Cookie: {cookie['name']} = {cookie['value'][:30]}...")
                return (cookie["name"], cookie["value"])
        return (None, None)
    except Exception as e:
        print(f"⚠️ 提取 Cookie 失败: {e}")
        return (None, None)


# ------------------ 主逻辑 ------------------
async def add_server_time():
    server_url = os.environ.get("SERVER_URL", DEFAULT_SERVER_URL)
    cookie_value = os.environ.get("REMEMBER_WEB_COOKIE", "").strip()
    cookie_name = os.environ.get("REMEMBER_WEB_COOKIE_NAME", DEFAULT_COOKIE_NAME)

    if not cookie_value:
        msg = "❌ REMEMBER_WEB_COOKIE 未设置，无法登录"
        print(msg)
        await tg_notify(msg)
        return

    print("🚀 启动 Playwright...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(120000)
        page.set_default_navigation_timeout(120000)

        try:
            # ========== 1. 注入 Cookie 并登录 ==========
            await context.add_cookies([{
                "name": cookie_name,
                "value": cookie_value,
                "domain": "hub.weirdhost.xyz",
                "path": "/",
            }])
            print(f"🔑 已注入 Cookie: {cookie_name}")

            await page.goto(server_url, timeout=90000)
            await page.wait_for_load_state("networkidle", timeout=30000)

            # 检查是否登录成功
            if "/auth/login" in page.url or "/login" in page.url:
                msg = "❌ Cookie 已失效，请手动更新 REMEMBER_WEB_COOKIE"
                print(msg)
                await page.screenshot(path="cookie_expired.png", full_page=True)
                await tg_notify_photo("cookie_expired.png", msg)
                await tg_notify(msg)
                return

            print("✅ Cookie 登录成功")

            # ========== 2. 点击续期按钮 ==========
            add_button = page.locator('button:has-text("시간추가")')
            if await add_button.count() == 0:
                add_button = page.locator('text=시간추가')
            if await add_button.count() == 0:
                add_button = page.locator('button:has-text("Add Time")')

            if await add_button.count() == 0:
                await page.screenshot(path="no_button.png", full_page=True)
                await tg_notify_photo("no_button.png", "❌ 未找到续期按钮")
                await tg_notify("❌ 未找到 '시간추가' 按钮")
                return

            await add_button.nth(0).click()
            print("🔄 已点击续期按钮")
            await page.wait_for_timeout(3000)

            # ========== 3. 提取并更新 Cookie ==========
            new_name, new_value = await extract_remember_cookie(context)
            if new_name and new_value:
                # 检查是否有变化
                if new_value != cookie_value or new_name != cookie_name:
                    print("🔄 Cookie 已更新，正在同步到 GitHub Secrets...")
                    await update_github_secret("REMEMBER_WEB_COOKIE", new_value)
                    if new_name != DEFAULT_COOKIE_NAME:
                        await update_github_secret("REMEMBER_WEB_COOKIE_NAME", new_name)
                    await tg_notify("🔑 Cookie 已自动更新到 GitHub Secrets")
                else:
                    print("ℹ️ Cookie 未变化，无需更新")
            else:
                print("⚠️ 未提取到新 Cookie")

            # ========== 4. 查询到期时间 ==========
            expiry_time = "Unknown"
            try:
                await page.goto(server_url, timeout=90000)
                await page.wait_for_load_state("networkidle", timeout=30000)
                expiry_time = await page.evaluate("""
                    () => {
                        const text = document.body.innerText;
                        const match = text.match(/유통기한\\s*(\\d{4}-\\d{2}-\\d{2}(?:\\s+\\d{2}:\\d{2}:\\d{2})?)/);
                        return match ? match[1].trim() : 'Unknown';
                    }
                """)
                print(f"📅 到期时间: {expiry_time}")
            except Exception as e:
                print(f"⚠️ 获取到期时间失败: {e}")

            # ========== 5. 发送成功通知 ==========
            msg = f"✅ 续期成功\n📅 到期时间: {expiry_time}\n🔗 {server_url}"
            await tg_notify(msg)
            print(msg)

        except Exception as e:
            msg = f"❌ 脚本异常: {repr(e)}"
            print(msg)
            try:
                await page.screenshot(path="error.png", full_page=True)
                await tg_notify_photo("error.png", msg)
            except:
                pass
            await tg_notify(msg)

        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(add_server_time())
