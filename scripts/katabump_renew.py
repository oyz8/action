#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KataBump 自动续订脚本 (支持 Turnstile 验证码)
cron: 0 9,21 * * *
new Env('KataBump续订');
"""

import os
import sys
import re
import asyncio
import requests
import time
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright

# 配置
DASHBOARD_URL = 'https://dashboard.katabump.com'
SERVER_ID = os.environ.get('KATA_SERVER_ID') or ''
KATA_EMAIL = os.environ.get('KATA_EMAIL') or ''
KATA_PASSWORD = os.environ.get('KATA_PASSWORD') or ''
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ''
TG_CHAT_ID = os.environ.get('TG_USER_ID') or ''

# Capsolver API Key (用于解决 Turnstile 验证码)
CAPSOLVER_KEY = os.environ.get('CAPSOLVER_KEY') or ''

SCREENSHOT_DIR = os.environ.get('SCREENSHOT_DIR') or '/tmp'

# Turnstile 配置
TURNSTILE_SITEKEY = '0x4AAAAAAA1IssKDXD0TRMjP'


def log(msg):
    tz = timezone(timedelta(hours=8))
    t = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}')


def tg_notify(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    try:
        requests.post(
            f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            timeout=30
        )
        log('✅ Telegram 通知已发送')
        return True
    except Exception as e:
        log(f'❌ Telegram 错误: {e}')
    return False


def tg_notify_photo(photo_path, caption=''):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    try:
        with open(photo_path, 'rb') as f:
            requests.post(
                f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto',
                data={'chat_id': TG_CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'},
                files={'photo': f},
                timeout=60
            )
        log('✅ Telegram 截图已发送')
        return True
    except Exception as e:
        log(f'❌ Telegram 图片发送错误: {e}')
    return False


def solve_turnstile_capsolver(page_url, sitekey):
    """使用 Capsolver 解决 Turnstile 验证码"""
    if not CAPSOLVER_KEY:
        log('⚠️ 未配置 CAPSOLVER_KEY，无法自动解决验证码')
        return None
    
    log('🔄 正在使用 Capsolver 解决 Turnstile...')
    
    try:
        # 创建任务
        create_task_url = 'https://api.capsolver.com/createTask'
        task_payload = {
            'clientKey': CAPSOLVER_KEY,
            'task': {
                'type': 'AntiTurnstileTaskProxyLess',
                'websiteURL': page_url,
                'websiteKey': sitekey,
            }
        }
        
        resp = requests.post(create_task_url, json=task_payload, timeout=30)
        result = resp.json()
        
        if result.get('errorId') != 0:
            log(f'❌ Capsolver 创建任务失败: {result.get("errorDescription")}')
            return None
        
        task_id = result.get('taskId')
        log(f'📋 Capsolver 任务创建成功: {task_id}')
        
        # 轮询获取结果
        get_result_url = 'https://api.capsolver.com/getTaskResult'
        for i in range(60):  # 最多等待 60 秒
            time.sleep(1)
            
            resp = requests.post(get_result_url, json={
                'clientKey': CAPSOLVER_KEY,
                'taskId': task_id
            }, timeout=30)
            result = resp.json()
            
            status = result.get('status')
            if status == 'ready':
                token = result.get('solution', {}).get('token')
                log('✅ Turnstile 验证码已解决')
                return token
            elif status == 'failed':
                log(f'❌ Capsolver 解决失败: {result.get("errorDescription")}')
                return None
            
            if i % 10 == 0:
                log(f'⏳ 等待验证码解决... ({i}s)')
        
        log('❌ Capsolver 超时')
        return None
        
    except Exception as e:
        log(f'❌ Capsolver 错误: {e}')
        return None


def get_expiry_from_text(text):
    match = re.search(r'Expiry[\s\S]*?(\d{4}-\d{2}-\d{2})', text, re.IGNORECASE)
    return match.group(1) if match else None


def days_until(date_str):
    try:
        exp = datetime.strptime(date_str, '%Y-%m-%d')
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (exp - today).days
    except:
        return None


async def run():
    log('🚀 KataBump 自动续订 (支持 Turnstile)')
    log(f'🖥 服务器 ID: {SERVER_ID}')
    
    if not SERVER_ID:
        raise Exception('未设置 KATA_SERVER_ID 环境变量')
    
    server_url = f'{DASHBOARD_URL}/servers/edit?id={SERVER_ID}'
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-blink-features=AutomationControlled',
            ]
        )
        
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        page = await context.new_page()
        
        # 隐藏自动化特征
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        
        try:
            # ========== 登录 ==========
            log('🔐 正在登录...')
            
            await page.goto(f'{DASHBOARD_URL}/auth/login', timeout=60000)
            await page.wait_for_load_state('networkidle', timeout=30000)
            
            email_input = page.locator('input[name="email"], input[type="email"]')
            await email_input.wait_for(timeout=10000)
            await email_input.fill(KATA_EMAIL)
            
            password_input = page.locator('input[name="password"], input[type="password"]')
            await password_input.fill(KATA_PASSWORD)
            
            login_btn = page.locator('button[type="submit"], input[type="submit"]')
            await login_btn.first.click()
            
            await page.wait_for_timeout(3000)
            await page.wait_for_load_state('networkidle', timeout=30000)
            
            if '/auth/login' in page.url:
                screenshot_path = os.path.join(SCREENSHOT_DIR, 'login_failed.png')
                await page.screenshot(path=screenshot_path, full_page=True)
                tg_notify_photo(screenshot_path, '❌ 登录失败，请检查账号密码')
                raise Exception('登录失败')
            
            log('✅ 登录成功')
            
            # ========== 打开服务器页面 ==========
            log(f'📄 打开服务器页面...')
            
            await page.goto(server_url, timeout=90000)
            await page.wait_for_load_state('networkidle', timeout=30000)
            await page.wait_for_timeout(2000)
            
            # 获取当前到期时间
            page_content = await page.content()
            old_expiry = get_expiry_from_text(page_content) or '未知'
            days = days_until(old_expiry)
            log(f'📅 当前到期: {old_expiry} (剩余 {days} 天)')
            
            # ========== 点击主页面 Renew 按钮 ==========
            log('🔍 查找 Renew 按钮...')
            
            main_renew_btn = page.locator('button[data-bs-target="#renew-modal"]')
            
            if await main_renew_btn.count() == 0:
                main_renew_btn = page.locator('button.btn-outline-primary:has-text("Renew")')
            
            if await main_renew_btn.count() == 0:
                screenshot_path = os.path.join(SCREENSHOT_DIR, 'no_renew.png')
                await page.screenshot(path=screenshot_path, full_page=True)
                tg_notify_photo(screenshot_path, f'❌ 未找到 Renew 按钮\n服务器: {SERVER_ID}')
                raise Exception('未找到 Renew 按钮')
            
            log('🖱 点击 Renew 按钮打开模态框...')
            await main_renew_btn.first.click()
            await page.wait_for_timeout(1500)
            
            # ========== 等待模态框 ==========
            modal = page.locator('#renew-modal')
            try:
                await modal.wait_for(state='visible', timeout=5000)
                log('✅ 模态框已打开')
            except:
                raise Exception('模态框未打开')
            
            # ========== 处理 Turnstile 验证码 ==========
            log('🔍 检查 Turnstile 验证码...')
            
            turnstile = page.locator('.cf-turnstile, [data-sitekey]')
            turnstile_token = None
            
            if await turnstile.count() > 0:
                log('🛡 检测到 Turnstile 验证码')
                
                # 等待 Turnstile 自动完成（有时候会自动通过）
                log('⏳ 等待 Turnstile 自动验证...')
                await page.wait_for_timeout(5000)
                
                # 检查是否有 cf-turnstile-response
                response_input = page.locator('input[name="cf-turnstile-response"]')
                if await response_input.count() > 0:
                    current_value = await response_input.get_attribute('value')
                    if current_value and len(current_value) > 10:
                        log('✅ Turnstile 自动验证成功')
                        turnstile_token = current_value
                
                # 如果没有自动通过，使用 Capsolver
                if not turnstile_token and CAPSOLVER_KEY:
                    turnstile_token = solve_turnstile_capsolver(server_url, TURNSTILE_SITEKEY)
                    
                    if turnstile_token:
                        # 注入 token
                        await page.evaluate(f'''
                            (token) => {{
                                const input = document.querySelector('input[name="cf-turnstile-response"]');
                                if (input) {{
                                    input.value = token;
                                }}
                                // 尝试调用 turnstile 回调
                                if (window.turnstile && window.turnstile.getResponse) {{
                                    // 已有实现
                                }}
                            }}
                        ''', turnstile_token)
                        log('✅ Turnstile token 已注入')
                
                if not turnstile_token:
                    screenshot_path = os.path.join(SCREENSHOT_DIR, 'captcha_required.png')
                    await page.screenshot(path=screenshot_path, full_page=True)
                    
                    if days is not None and days <= 3:
                        tg_notify_photo(
                            screenshot_path,
                            f'⚠️ KataBump 需要手动续订\n\n'
                            f'🖥 服务器: <code>{SERVER_ID}</code>\n'
                            f'📅 到期: {old_expiry}\n'
                            f'⏰ 剩余: {days} 天\n'
                            f'❗ 需要验证码，请配置 CAPSOLVER_KEY 或手动续订\n\n'
                            f'👉 <a href="{server_url}">手动续订</a>'
                        )
                    else:
                        log(f'⏳ 剩余 {days} 天，暂不需要续订')
                    return
            
            # ========== 提交续订 ==========
            log('🖱 点击确认 Renew...')
            
            submit_btn = page.locator('#renew-modal button[type="submit"]')
            if await submit_btn.count() == 0:
                submit_btn = page.locator('#renew-modal .modal-footer button.btn-primary')
            
            await submit_btn.first.click()
            
            await page.wait_for_timeout(3000)
            await page.wait_for_load_state('networkidle', timeout=30000)
            
            # ========== 检查结果 ==========
            log('🔍 检查续订结果...')
            
            current_url = page.url
            page_content = await page.content()
            
            if 'renew=success' in current_url or 'success' in page_content.lower():
                new_expiry = get_expiry_from_text(page_content) or '未知'
                log(f'🎉 续订成功！新到期: {new_expiry}')
                
                screenshot_path = os.path.join(SCREENSHOT_DIR, 'success.png')
                await page.screenshot(path=screenshot_path, full_page=True)
                tg_notify_photo(
                    screenshot_path,
                    f'✅ KataBump 续订成功\n\n'
                    f'🖥 服务器: <code>{SERVER_ID}</code>\n'
                    f'📅 原到期: {old_expiry}\n'
                    f'📅 新到期: {new_expiry}'
                )
            elif 'error' in current_url.lower():
                error_match = re.search(r'error=([^&]+)', current_url)
                error_msg = error_match.group(1) if error_match else '未知错误'
                log(f'❌ 续订失败: {error_msg}')
                
                screenshot_path = os.path.join(SCREENSHOT_DIR, 'error.png')
                await page.screenshot(path=screenshot_path, full_page=True)
                tg_notify_photo(screenshot_path, f'❌ 续订失败: {error_msg}')
            else:
                # 重新获取到期时间检查
                await page.goto(server_url, timeout=60000)
                await page.wait_for_load_state('networkidle')
                page_content = await page.content()
                new_expiry = get_expiry_from_text(page_content) or '未知'
                
                if new_expiry != old_expiry and new_expiry > old_expiry:
                    log(f'🎉 续订成功！新到期: {new_expiry}')
                    screenshot_path = os.path.join(SCREENSHOT_DIR, 'success.png')
                    await page.screenshot(path=screenshot_path, full_page=True)
                    tg_notify_photo(
                        screenshot_path,
                        f'✅ KataBump 续订成功\n\n'
                        f'🖥 服务器: <code>{SERVER_ID}</code>\n'
                        f'📅 原到期: {old_expiry}\n'
                        f'📅 新到期: {new_expiry}'
                    )
                else:
                    log(f'⚠️ 续订状态未知')
                    screenshot_path = os.path.join(SCREENSHOT_DIR, 'unknown.png')
                    await page.screenshot(path=screenshot_path, full_page=True)
                    
                    if days is not None and days <= 2:
                        tg_notify_photo(screenshot_path, f'⚠️ 请检查续订状态\n到期: {new_expiry}')
        
        except Exception as e:
            log(f'❌ 错误: {e}')
            try:
                screenshot_path = os.path.join(SCREENSHOT_DIR, 'error.png')
                await page.screenshot(path=screenshot_path, full_page=True)
                tg_notify_photo(screenshot_path, f'❌ 出错: {e}')
            except:
                pass
            tg_notify(f'❌ KataBump 出错\n🖥 {SERVER_ID}\n❗ {e}')
            raise
        
        finally:
            await browser.close()


def main():
    log('=' * 50)
    log('   KataBump 自动续订')
    log('=' * 50)
    
    if not KATA_EMAIL or not KATA_PASSWORD:
        log('❌ 请设置 KATA_EMAIL 和 KATA_PASSWORD')
        sys.exit(1)
    
    if not SERVER_ID:
        log('❌ 请设置 KATA_SERVER_ID')
        sys.exit(1)
    
    log(f'📧 邮箱: {KATA_EMAIL[:3]}***')
    log(f'🖥 服务器: {SERVER_ID}')
    log(f'🔑 Capsolver: {"已配置" if CAPSOLVER_KEY else "未配置"}')
    
    asyncio.run(run())
    log('🏁 完成')


if __name__ == '__main__':
    main()
