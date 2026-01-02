#!/usr/bin/env python3
# -*- coding: utf-8 -*- 
"""
weirdhost-auto - main.py
改动：优先使用 cookie 登录（REMEMBER_WEB_COOKIE），cookie 失效再使用邮箱+密码登录。
保留：Telegram 通知、异常截图上传、超时延长、按索引填写输入框、勾选 checkbox、点击韩文 로그인 登录按钮、点击 시간추가 续期。
新增：续期后查询到期时间（基于页面文本匹配 "유통기한"），并在通知中包含到期时间。
环境变量：
  - REMEMBER_WEB_COOKIE (可选) : cookie 的 value
  - REMEMBER_WEB_COOKIE_NAME (可选) : cookie 名称，默认 'remember_web'
  - PTERODACTYL_EMAIL, PTERODACTYL_PASSWORD (当 cookie 不可用时回退使用)
  - SERVER_URL (可选，默认 https://hub.weirdhost.xyz/server/d341874c)
  - TG_BOT_TOKEN, TG_CHAT_ID (可选，用于通知)
"""
import os
import asyncio
import aiohttp
import re
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

DEFAULT_SERVER_URL = "https://hub.weirdhost.xyz/server/d341874c"
DEFAULT_COOKIE_NAME = "remember_web"

# ------------------ Telegram 通知函数 ------------------
async def tg_notify(message: str):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        print("⚠️ TG_BOT_TOKEN 或 TG_CHAT_ID 未设置，跳过 Telegram 消息")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, data={"chat_id": chat_id, "text": message})
        except Exception as e:
            print("⚠️ 发送 Telegram 消息失败:", e)

async def tg_notify_photo(photo_path: str, caption: str = ""):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        print("⚠️ TG_BOT_TOKEN 或 TG_CHAT_ID 未设置，跳过 Telegram 图片通知")
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with aiohttp.ClientSession() as session:
        try:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                if caption:
                    data.add_field("caption", caption)
                await session.post(url, data=data)
        except Exception as e:
            print("⚠️ 发送 Telegram 图片失败:", e)

# ------------------ 帮助函数 ------------------
async def try_cookie_login(context, page, server_url) -> bool:
    """
    使用已经添加到 context 的 cookie 尝试访问 server_url 来判断是否登录成功。
    返回 True 表示 cookie 有效且已经登录；否则返回 False。
    """
    try:
        await page.goto(server_url, timeout=90000)
        # 等待页面稳定
        await page.wait_for_load_state("networkidle", timeout=30000)
        # 判断是否被重定向到登录页或页面内存在登录框
        current_url = page.url or ""
        if "/auth/login" in current_url or "/login" in current_url:
            return False
        # 进一步检查页面是否包含明显的登录表单（保险）
        try:
            login_input = await page.query_selector('input')
            if login_input:
                # 如果页面包含输入框并且 URL 仍然在 server 页面，可能仍然登录（某些页面隐含输入框），
                # 我们认为当前页面 URL 没有跳回 login 就是登录成功
                return True
        except Exception:
            pass
        return True
    except Exception as e:
        print("⚠️ 使用 cookie 验证登录状态时出错:", e)
        return False

# ------------------ 主逻辑 ------------------
async def add_server_time():
    server_url = os.environ.get("SERVER_URL", DEFAULT_SERVER_URL)
    email = os.environ.get("PTERODACTYL_EMAIL")
    password = os.environ.get("PTERODACTYL_PASSWORD")
    remember_cookie_value = os.environ.get("REMEMBER_WEB_COOKIE", "").strip()
    remember_cookie_name = os.environ.get("REMEMBER_WEB_COOKIE_NAME", DEFAULT_COOKIE_NAME)

    print("🚀 启动 Playwright（Chromium）...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # 设置全局 timeout
        page.set_default_timeout(120000)
        page.set_default_navigation_timeout(120000)

        used_cookie = False
        try:
            # ------------------ 如果提供了 cookie，优先尝试 cookie 登录 ------------------
            if remember_cookie_value:
                try:
                    # 若 cookie 名称可能带 hash 后缀（remember_web_xxx），尝试直接用环境变量的 name， 
                    # 如果没设置特定 name，使用默认 remember_web
                    cookie_to_add = {
                        "name": remember_cookie_name,
                        "value": remember_cookie_value,
                        "domain": "hub.weirdhost.xyz",
                        "path": "/",
                        # 可根据需要设置 expires / httpOnly / secure，这里不强制设置
                    }
                    await context.add_cookies([cookie_to_add])
                    print("🔑 已向 context 注入 cookie，尝试使用 cookie 登录...")
                    # 打开一个页面验证 cookie 是否有效
                    page = await context.new_page()
                    cookie_ok = await try_cookie_login(context, page, server_url)
                    if cookie_ok:
                        used_cookie = True
                        print("✅ Cookie 有效，已使用 Cookie 登录。")
                        await tg_notify(f"✅ Cookie 登录成功，正在续期：{server_url}")
                    else:
                        print("⚠️ Cookie 无效或已过期，将使用邮箱/密码登录。")
                        # 关闭当前带 cookie 的 page，后面会走密码登录流程
                        await page.close()
                        page = await context.new_page()
                        # 清掉 cookies in context to avoid confusion if needed
                        try:
                            await context.clear_cookies()
                        except Exception:
                            pass
                except Exception as e:
                    print("⚠️ 注入 cookie 或验证 cookie 时出错，回退密码登录:", e)
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = await context.new_page()

            # ------------------ 如果没有使用 cookie，或 cookie 无效 -> 使用邮箱/密码登录 ------------------
            if not used_cookie:
                if not email or not password:
                    msg = "❌ 未配置有效的 REMEMBER_WEB_COOKIE 且缺少 PTERODACTYL_EMAIL / PTERODACTYL_PASSWORD，请配置后重试。"
                    print(msg)
                    await tg_notify(msg)
                    return

                # 打开登录页
                login_url = "https://hub.weirdhost.xyz/auth/login"
                await page.goto(login_url, timeout=120000)

                # 等待至少有输入框出现
                try:
                    await page.wait_for_selector('input', timeout=60000)
                except PlaywrightTimeoutError:
                    # 截图并通知
                    screenshot_path = "login_page_not_loaded.png"
                    try:
                        await page.screenshot(path=screenshot_path, full_page=True)
                        await tg_notify_photo(screenshot_path, caption="⚠️ 登录页加载超时或未找到输入框")
                    except Exception:
                        pass
                    await tg_notify("❌ 登录页加载超时或未找到输入框")
                    return

                inputs = await page.query_selector_all('input')
                if len(inputs) < 2:
                    screenshot_path = "login_inputs_not_found.png"
                    try:
                        await page.screenshot(path=screenshot_path, full_page=True)
                        await tg_notify_photo(screenshot_path, caption="❌ 登录页面输入框不足两个，无法填写邮箱和密码")
                    except Exception:
                        pass
                    await tg_notify("❌ 登录页面输入框不足两个，无法填写邮箱和密码")
                    return

                # 填写邮箱和密码（第一个 input 填邮箱，第二个 input 填密码）
                try:
                    await inputs[0].fill(email, timeout=120000)
                    await inputs[1].fill(password, timeout=120000)
                except Exception as e:
                    screenshot_path = "fill_inputs_failed.png"
                    try:
                        await page.screenshot(path=screenshot_path, full_page=True)
                        await tg_notify_photo(screenshot_path, caption=f"❌ 填写输入框失败: {e}")
                    except Exception:
                        pass
                    await tg_notify(f"❌ 填写输入框失败: {e}")
                    return

                # 勾选协议 checkbox（若存在）
                try:
                    checkbox = await page.query_selector('input[type="checkbox"]')
                    if checkbox:
                        await checkbox.check()
                except Exception:
                    # 不影响登录流程
                    print("⚠️ 协议勾选框未找到或无法勾选，继续登录")

                # 点击登录按钮 —— 优先使用韩文登录按钮文本 "로그인"
                try:
                    login_button = page.locator('button:has-text("로그인")')
                    if await login_button.count() == 0:
                        # 退回到常见的其他选择器
                        login_button = page.locator('button:has-text("Login")')
                    if await login_button.count() == 0:
                        login_button = page.locator('button[type="submit"]')
                    if await login_button.count() == 0:
                        # 找不到登录按钮
                        screenshot_path = "login_button_not_found.png"
                        try:
                            await page.screenshot(path=screenshot_path, full_page=True)
                            await tg_notify_photo(screenshot_path, caption="❌ 未找到登录按钮")
                        except Exception:
                            pass
                        await tg_notify("❌ 未找到登录按钮（登录失败）")
                        return

                    await login_button.nth(0).click()
                    # 等短时间让页面处理提交
                    await page.wait_for_timeout(3000)
                except Exception as e:
                    screenshot_path = "click_login_failed.png"
                    try:
                        await page.screenshot(path=screenshot_path, full_page=True)
                        await tg_notify_photo(screenshot_path, caption=f"❌ 点击登录按钮失败: {e}")
                    except Exception:
                        pass
                    await tg_notify(f"❌ 点击登录按钮失败: {e}")
                    return

                # 等待登录成功（跳转到 /server/ 或 networkidle）
                try:
                    await page.wait_for_url("**/server/**", timeout=60000)
                except PlaywrightTimeoutError:
                    # 尝试等待页面稳定后判断是否登录（可能页面没有跳转）
                    try:
                        await page.wait_for_load_state("networkidle", timeout=30000)
                    except Exception:
                        pass
                    # 最后通过 URL 判断
                    if "/auth/login" in (page.url or "") or "/login" in (page.url or ""):
                        screenshot_path = "login_failed.png"
                        try:
                            await page.screenshot(path=screenshot_path, full_page=True)
                            await tg_notify_photo(screenshot_path, caption="❌ 登录似乎失败，请手动检查（页面仍在登录页）")
                        except Exception:
                            pass
                        await tg_notify("❌ 登录似乎失败（页面仍在登录页）")
                        return

            # ------------------ 到这里已经登录（cookie 或 密码登录成功） ------------------
            # 打开服务器页面并点击续期按钮
            try:
                await page.goto(server_url, timeout=90000)
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception as e:
                screenshot_path = "open_server_failed.png"
                try:
                    await page.screenshot(path=screenshot_path, full_page=True)
                    await tg_notify_photo(screenshot_path, caption=f"❌ 打开服务器页面失败: {e}")
                except Exception:
                    pass
                await tg_notify(f"❌ 打开服务器页面失败: {e}")
                return

            # 查找并点击 '시간추가' 按钮
            add_button = page.locator('button:has-text("시간추가")')
            if await add_button.count() == 0:
                add_button = page.locator('text=시간추가')
            if await add_button.count() == 0:
                add_button = page.locator('button:has-text("Add Time")')

            if await add_button.count() == 0:
                screenshot_path = "no_button_found.png"
                try:
                    await page.screenshot(path=screenshot_path, full_page=True)
                    await tg_notify_photo(screenshot_path, caption="❠ 未找到 '시간추가' 按钮")
                except Exception:
                    pass
                await tg_notify("❌ 未找到 '시간추가' 按钮，续期失败")
                return

            # 点击
            try:
                await add_button.nth(0).click()
                await page.wait_for_timeout(3000)
            except Exception as e:
                screenshot_path = "click_add_time_failed.png"
                try:
                    await page.screenshot(path=screenshot_path, full_page=True)
                    await tg_notify_photo(screenshot_path, caption=f"❌ 点击续期按钮失败: {e}")
                except Exception:
                    pass
                await tg_notify(f"❌ 点击续期按钮失败: {e}")
                return

            # ------------------ 查询到期时间 ------------------
            expiry_time = "Unknown"
            try:
                print("🔄 续期后重新加载服务器页面以查询最新到期时间...")
                await page.goto(server_url, timeout=90000)
                await page.wait_for_load_state("networkidle", timeout=30000)
                # 从页面提取“유통기한”后跟的日期
                expiry_time = await page.evaluate("""
                    () => {
                        const text = document.body.innerText;
                        const match = text.match(/유통기한\\s*(\\d{4}-\\d{2}-\\d{2}(?:\\s+\\d{2}:\\d{2}:\\d{2})?)/);
                        return match ? match[1].trim() : 'Not found';
                    }
                """)
                if expiry_time == "Not found":
                    expiry_time = "Unknown"
                print(f"📅 最新到期时间: {expiry_time}")
            except Exception as e:
                print(f"⚠️ 重新访问服务器或解析到期时间失败: {e}")


            # ------------------ 输出续费成功通知 ------------------
            success_msg = f"✅ 续期操作已完成，到期时间：{expiry_time}，服务器：{server_url}"
            await tg_notify(success_msg)
            print(success_msg)

        except Exception as e:
            # 捕获整个流程中未处理的异常，截图并通知
            msg = f"❌ 脚本异常: {repr(e)}"
            print(msg)
            screenshot_path = "error_screenshot.png"
            try:
                await page.screenshot(path=screenshot_path, full_page=True)
                print(f"📸 已保存错误截图：{screenshot_path}")
                await tg_notify_photo(screenshot_path, caption=msg)
            except Exception as se:
                print("⚠️ 无法保存或发送截图:", se)
            await tg_notify(msg)

        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(add_server_time())
