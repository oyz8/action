#!/usr/bin/env python3
"""
Pella 自动续期脚本 (增强稳定性 - 使用 JavaScript 强制输入绕过交互问题)
支持单账号和多账号

配置变量说明:
- 单账号变量:
    - PELLA_EMAIL / LEAFLOW_EMAIL=登录邮箱
    - PELLA_PASSWORD / LEAFLOW_PASSWORD=登录密码
- 多账号变量:
    - PELLA_ACCOUNTS / LEAFLOW_ACCOUNTS: 格式：邮箱1:密码1,邮箱2:密码2,邮箱3:密码3
- 通知变量 (可选):
    - TG_BOT_TOKEN=Telegram 机器人 Token
    - TG_CHAT_ID=Telegram 聊天 ID
"""


import os
import time
import logging
import re
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PellaAutoRenew:
    LOGIN_URL = "https://www.pella.app/login"
    HOME_URL = "https://www.pella.app/home"
    RENEW_WAIT_TIME = 8
    WAIT_TIME_AFTER_LOGIN = 15

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.telegram_bot_token = os.getenv('TG_BOT_TOKEN', '')
        self.telegram_chat_id = os.getenv('TG_CHAT_ID', '')
        self.initial_expiry_details = "N/A"
        self.initial_expiry_value = -1.0
        self.server_url = None
        
        if not self.email or not self.password:
            raise ValueError("邮箱和密码不能为空")
        
        self.driver = None
        self.setup_driver()
    
    def setup_driver(self):
        chrome_options = Options()
        
        if os.getenv('GITHUB_ACTIONS'):
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
        
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except WebDriverException as e:
            logger.error(f"❌ 驱动初始化失败: {e}")
            raise

    def wait_for_element_clickable(self, by, value, timeout=10):
        return WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((by, value))
        )
    
    def wait_for_element_present(self, by, value, timeout=10):
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )

    def extract_expiry_days(self, page_source):
        match = re.search(r"Your server expires in\s*(\d+)D\s*(\d+)H\s*(\d+)M", page_source)
        if match:
            days_int = int(match.group(1))
            hours_int = int(match.group(2))
            minutes_int = int(match.group(3))
            detailed_string = f"{days_int} 天 {hours_int} 小时 {minutes_int} 分钟"
            total_days_float = days_int + (hours_int / 24) + (minutes_int / (24 * 60))
            return detailed_string, total_days_float
            
        match_simple = re.search(r"Your server expires in\s*(\d+)D", page_source)
        if match_simple:
            days_int = int(match_simple.group(1))
            return f"{days_int} 天", float(days_int)
            
        logger.warning("⚠️ 未找到有效的过期时间格式")
        return "无法提取", -1.0

    def login(self):
        logger.info(f"🔑 开始登录流程")
        self.driver.get(self.LOGIN_URL)
        
        def js_set_value_and_trigger(element, value):
            self.driver.execute_script(f"arguments[0].value = '{value}';", element)
            self.driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", element)
            self.driver.execute_script("arguments[0].dispatchEvent(new Event('change', { bubbles: true }));", element)
        
        # 1. 输入邮箱
        try:
            logger.info("🔍 查找邮箱输入框...")
            email_input = self.wait_for_element_present(By.CSS_SELECTOR, "input[name='identifier']", 15)
            js_set_value_and_trigger(email_input, self.email)
            logger.info("✅ 邮箱输入完成")
        except Exception as e:
            raise Exception(f"❌ 输入邮箱失败: {e}")
            
        # 2. 点击 Continue
        try:
            logger.info("🔍 查找并点击 Continue 按钮...")
            continue_btn_1 = self.wait_for_element_clickable(By.XPATH, "//button[contains(., 'Continue')]", 10)
            initial_url = self.driver.current_url
            self.driver.execute_script("arguments[0].click();", continue_btn_1)
            logger.info("✅ 已点击 Continue 按钮")
            
            logger.info("⏳ 等待页面 URL 变化...")
            WebDriverWait(self.driver, 10).until(EC.url_changes(initial_url))
            logger.info("✅ 页面已切换")

            # 3. 等待密码输入框
            logger.info("⏳ 等待密码输入框...")
            password_input = self.wait_for_element_present(By.CSS_SELECTOR, "input[type='password']", 15)
            logger.info("✅ 密码输入框已出现")

            # 4. 输入密码
            js_set_value_and_trigger(password_input, self.password)
            logger.info("✅ 密码输入完成")
            
        except Exception as e:
            raise Exception(f"❌ 登录流程失败: {e}")

        # 5. 点击登录按钮 (修复：尝试多种选择器)
        try:
            logger.info("⏳ 等待 2 秒...")
            time.sleep(2)

            logger.info("🔍 查找登录按钮...")
            
            button_selectors = [
                "//button[contains(., 'Continue')]",
                "//button[contains(., 'Sign in')]",
                "//button[contains(., 'Log in')]",
                "//button[@type='submit']",
                "//button[contains(@class, 'cl-formButtonPrimary')]"
            ]
            
            login_btn = None
            for selector in button_selectors:
                try:
                    login_btn = WebDriverWait(self.driver, 3).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    logger.info(f"✅ 找到按钮: {selector}")
                    break
                except:
                    continue
            
            if not login_btn:
                all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
                logger.info(f"🔍 遍历 {len(all_buttons)} 个按钮...")
                for btn in all_buttons:
                    btn_text = btn.text.strip().lower()
                    if btn_text in ['continue', 'sign in', 'log in', 'submit']:
                        login_btn = btn
                        logger.info(f"✅ 找到按钮: '{btn_text}'")
                        break
            
            if not login_btn:
                raise Exception("❌ 无法找到登录按钮")
            
            self.driver.execute_script("arguments[0].click();", login_btn)
            logger.info("✅ 已点击登录按钮")
            
        except Exception as e:
            logger.warning(f"⚠️ 点击失败，尝试提交表单: {e}")
            try:
                self.driver.execute_script("""
                    var forms = document.querySelectorAll('form');
                    if (forms.length > 0) forms[forms.length - 1].submit();
                """)
                logger.info("✅ 表单提交成功")
            except Exception as e_submit:
                raise Exception(f"❌ 表单提交失败: {e_submit}")

        # 6. 等待登录完成
        try:
            WebDriverWait(self.driver, self.WAIT_TIME_AFTER_LOGIN).until(
                EC.url_to_be(self.HOME_URL)
            )
            logger.info(f"✅ 登录成功")
            return True
        except TimeoutException:
            try:
                error_elem = self.driver.find_element(By.CSS_SELECTOR, ".cl-alert-danger, [data-testid*='error']")
                if error_elem.is_displayed():
                    raise Exception(f"❌ 登录失败: {error_elem.text.strip()}")
            except NoSuchElementException:
                pass
            raise Exception("⚠️ 登录超时")

    def get_server_url(self):
        logger.info("🔍 查找服务器链接...")
        
        if not self.driver.current_url.startswith(self.HOME_URL):
            self.driver.get(self.HOME_URL)
            time.sleep(3)
            
        try:
            server_link = self.wait_for_element_clickable(By.CSS_SELECTOR, "a[href*='/server/']", 15)
            server_link.click()
            WebDriverWait(self.driver, 10).until(EC.url_contains("/server/"))
            self.server_url = self.driver.current_url
            logger.info(f"✅ 服务器页面: {self.server_url}")
            return True
        except Exception as e:
            raise Exception(f"❌ 获取服务器URL失败: {e}")
    
    def renew_server(self):
        if not self.server_url:
            raise Exception("❌ 缺少服务器 URL")
            
        logger.info(f"👉 执行续期流程")
        self.driver.get(self.server_url)
        time.sleep(5)

        page_source = self.driver.page_source
        self.initial_expiry_details, self.initial_expiry_value = self.extract_expiry_days(page_source)
        logger.info(f"ℹ️ 初始过期时间: {self.initial_expiry_details}")

        if self.initial_expiry_value == -1.0:
            raise Exception("❌ 无法提取初始过期时间")

        try:
            renew_link_selectors = "a[href*='/renew/']:not(.opacity-50):not(.pointer-events-none)"
            renewed_count = 0
            original_window = self.driver.current_window_handle
            
            while True:
                renew_buttons = self.driver.find_elements(By.CSS_SELECTOR, renew_link_selectors)
                
                if not renew_buttons:
                    break

                button = renew_buttons[0]
                renew_url = button.get_attribute('href')
                logger.info(f"🚀 处理第 {renewed_count + 1} 个续期链接")
                
                self.driver.execute_script("window.open(arguments[0]);", renew_url)
                time.sleep(1)
                self.driver.switch_to.window(self.driver.window_handles[-1])

                try:
                    WebDriverWait(self.driver, 5).until(EC.url_contains("/renew/"))
                except:
                    pass

                time.sleep(self.RENEW_WAIT_TIME)
                self.driver.close()
                self.driver.switch_to.window(original_window)
                renewed_count += 1
                
                self.driver.get(self.server_url)
                time.sleep(3)

            if renewed_count == 0:
                disabled = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/renew/'].opacity-50")
                if disabled:
                    return "⏳ 今日已续期"
                return "⏳ 未找到续期按钮"

            if renewed_count > 0:
                self.driver.get(self.server_url)
                time.sleep(5)
                
                final_details, final_value = self.extract_expiry_days(self.driver.page_source)
                logger.info(f"ℹ️ 最终过期时间: {final_details}")
                
                if final_value > self.initial_expiry_value:
                    return f"✅ 续期成功! {self.initial_expiry_details} -> {final_details}"
                elif final_value == self.initial_expiry_value:
                    return f"⚠️ 天数未变化 ({final_details})"
                else:
                    return f"❌ 天数下降! {self.initial_expiry_details} -> {final_details}"

        except Exception as e:
            raise Exception(f"❌ 续期错误: {e}")
            
    def run(self):
        try:
            logger.info(f"⏳ 处理账号: {self.email}")
            
            if self.login():
                if self.get_server_url():
                    result = self.renew_server()
                    logger.info(f"📋 结果: {result}")
                    return True, result
                else:
                    return False, "❌ 无法获取服务器URL"
            else:
                return False, "❌ 登录失败"
                
        except Exception as e:
            error_msg = f"❌ 失败: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
        
        finally:
            if self.driver:
                self.driver.quit()

class MultiAccountManager:
    def __init__(self):
        self.telegram_bot_token = os.getenv('TG_BOT_TOKEN', '')
        self.telegram_chat_id = os.getenv('TG_CHAT_ID', '')
        self.accounts = self.load_accounts()
    
    def load_accounts(self):
        accounts = []
        logger.info("⏳ 开始加载账号配置...")
        
        accounts_str = os.getenv('PELLA_ACCOUNTS', os.getenv('LEAFLOW_ACCOUNTS', '')).strip()
        if accounts_str:
            try:
                account_pairs = [p.strip() for p in re.split(r'[;,]', accounts_str) if p.strip()]
                for pair in account_pairs:
                    if ':' in pair:
                        email, password = pair.split(':', 1)
                        if email.strip() and password.strip():
                            accounts.append({'email': email.strip(), 'password': password.strip()})
                if accounts:
                    logger.info(f"👉 加载了 {len(accounts)} 个账号")
                    return accounts
            except Exception as e:
                logger.error(f"❌ 解析失败: {e}")
        
        email = os.getenv('PELLA_EMAIL', os.getenv('LEAFLOW_EMAIL', '')).strip()
        password = os.getenv('PELLA_PASSWORD', os.getenv('LEAFLOW_PASSWORD', '')).strip()
        
        if email and password:
            accounts.append({'email': email, 'password': password})
            logger.info("👉 加载了单个账号配置")
            return accounts
        
        raise ValueError("⚠️ 未找到有效账号配置")
    
    def send_notification(self, results):
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return
        
        try:
            success_count = sum(1 for _, s, r in results if s and "续期成功" in r)
            message = f"🎁 Pella续期通知\n📋 共 {len(results)} 个账号\n✅ 成功: {success_count}\n\n"
            
            for email, success, result in results:
                status = "✅" if success and "成功" in result else ("⏳" if "已续期" in result else "❌")
                masked = email[:3] + "***@" + email.split('@')[1] if '@' in email else email[:3] + "***"
                message += f"{status} {masked}: {result[:80]}\n"
            
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage",
                data={"chat_id": self.telegram_chat_id, "text": message},
                timeout=10
            )
            logger.info("✅ 通知已发送")
        except Exception as e:
            logger.error(f"❌ 通知失败: {e}")
    
    def run_all(self):
        logger.info(f"👉 执行 {len(self.accounts)} 个账号")
        results = []
        
        for i, account in enumerate(self.accounts, 1):
            logger.info(f"{'='*50}")
            logger.info(f"👉 第 {i}/{len(self.accounts)} 个: {account['email']}")
            
            try:
                auto_renew = PellaAutoRenew(account['email'], account['password'])
                success, result = auto_renew.run()
                if i < len(self.accounts):
                    time.sleep(5)
            except Exception as e:
                success, result = False, f"❌ 异常: {e}"
            
            results.append((account['email'], success, result))
        
        self.send_notification(results)
        return all(s for _, s, _ in results), results

def main():
    try:
        manager = MultiAccountManager()
        success, _ = manager.run_all()
        exit(0 if success else 0)
    except Exception as e:
        logger.error(f"❌ 错误: {e}")
        exit(1)

if __name__ == "__main__":
    main()
