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
    WAIT_TIME_AFTER_LOGIN = 20

    def __init__(self, email, password):
        self.email = email
        self.password = password
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
            chrome_options.add_argument('--headless=new')
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
            
        return "无法提取", -1.0

    def find_and_click_button(self, button_type="continue"):
        """通用按钮查找和点击方法"""
        selectors = [
            "button.cl-formButtonPrimary",
            "button[data-localization-key='formButtonPrimary']",
            "//button[.//span[contains(text(), 'Continue')]]",
            "//button[contains(@class, 'cl-formButtonPrimary')]",
            "button[type='submit']",
            "form button"
        ]
        
        for selector in selectors:
            try:
                if selector.startswith("//"):
                    btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                else:
                    btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                
                self.driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                time.sleep(0.3)
                self.driver.execute_script("arguments[0].click();", btn)
                logger.info(f"✅ 点击按钮成功: {selector}")
                return True
            except:
                continue
        
        return False

    def wait_for_password_field(self, timeout=15):
        """等待密码输入框出现（Clerk SPA 不会改变 URL）"""
        password_selectors = [
            "input[type='password']",
            "input[name='password']",
            "input.cl-formFieldInput[type='password']",
            "#password",
        ]
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            for selector in password_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed():
                        logger.info(f"✅ 密码框已出现: {selector}")
                        return element
                except:
                    pass
            time.sleep(0.5)
        
        return None

    def check_for_error(self):
        """检查页面是否有错误提示"""
        error_selectors = [
            ".cl-formFieldErrorText",
            "[data-localization-key*='error']",
            ".error-message",
            "[class*='error']"
        ]
        
        for selector in error_selectors:
            try:
                error = self.driver.find_element(By.CSS_SELECTOR, selector)
                if error.is_displayed():
                    return error.text
            except:
                pass
        return None

    def login(self):
        logger.info(f"🔑 开始登录流程")
        self.driver.get(self.LOGIN_URL)
        time.sleep(4)
        
        def js_set_value(element, value):
            """使用多种方式确保值被正确输入"""
            element.clear()
            element.click()
            time.sleep(0.2)
            
            # 方法1: 直接输入
            element.send_keys(value)
            time.sleep(0.2)
            
            # 方法2: JS 设置并触发事件
            self.driver.execute_script("""
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                arguments[0].dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
            """, element, value)
        
        # 1. 输入邮箱
        try:
            logger.info("🔍 查找邮箱输入框...")
            email_input = self.wait_for_element_present(By.CSS_SELECTOR, "input[name='identifier']", 15)
            js_set_value(email_input, self.email)
            time.sleep(0.5)
            
            # 验证邮箱是否输入成功
            actual_value = email_input.get_attribute('value')
            if actual_value != self.email:
                logger.warning(f"⚠️ 邮箱值不匹配，重试...")
                email_input.clear()
                email_input.send_keys(self.email)
                
            logger.info("✅ 邮箱输入完成")
        except Exception as e:
            raise Exception(f"❌ 输入邮箱失败: {e}")
            
        # 2. 点击第一个 Continue 并等待密码框
        try:
            logger.info("🔍 点击 Continue 按钮...")
            time.sleep(1)
            
            if not self.find_and_click_button():
                raise Exception("无法点击 Continue 按钮")
            
            # ⚠️ 关键修复：不等待 URL 变化，而是等待密码框出现
            logger.info("⏳ 等待密码输入框出现...")
            password_input = self.wait_for_password_field(timeout=15)
            
            if not password_input:
                # 检查是否有错误信息
                error_msg = self.check_for_error()
                if error_msg:
                    raise Exception(f"登录错误: {error_msg}")
                
                # 保存截图用于调试
                try:
                    self.driver.save_screenshot("/tmp/clerk_debug.png")
                    logger.info("📸 已保存调试截图")
                except:
                    pass
                    
                raise Exception("密码框未出现，可能邮箱无效或页面加载失败")
            
            logger.info("✅ 页面已切换到密码步骤")
            time.sleep(1)

        except Exception as e:
            raise Exception(f"❌ 第一步失败: {e}")

        # 3. 输入密码
        try:
            logger.info("⏳ 输入密码...")
            # 重新获取密码框确保是新的元素
            password_input = self.wait_for_element_present(By.CSS_SELECTOR, "input[type='password']", 10)
            js_set_value(password_input, self.password)
            logger.info("✅ 密码输入完成")
        except Exception as e:
            raise Exception(f"❌ 输入密码失败: {e}")

        # 4. 点击登录按钮
        try:
            logger.info("⏳ 等待 2 秒...")
            time.sleep(2)
            
            logger.info("🔍 点击登录按钮...")
            if not self.find_and_click_button():
                raise Exception("无法点击登录按钮")
            
        except Exception as e:
            raise Exception(f"❌ 点击登录按钮失败: {e}")

        # 5. 等待登录完成
        try:
            logger.info(f"⏳ 等待登录完成...")
            
            for i in range(self.WAIT_TIME_AFTER_LOGIN // 2):
                time.sleep(2)
                current_url = self.driver.current_url
                
                if '/home' in current_url or '/dashboard' in current_url:
                    logger.info(f"✅ 登录成功")
                    return True
                
                # 检查错误
                error_msg = self.check_for_error()
                if error_msg:
                    raise Exception(f"登录失败: {error_msg}")
                
                if '/login' not in current_url and '/sign-in' not in current_url:
                    self.driver.get(self.HOME_URL)
                    time.sleep(2)
                    if '/home' in self.driver.current_url:
                        logger.info(f"✅ 登录成功")
                        return True
            
            # 最后尝试
            self.driver.get(self.HOME_URL)
            time.sleep(3)
            if '/home' in self.driver.current_url:
                logger.info(f"✅ 登录成功")
                return True
            
            raise Exception(f"登录超时，当前URL: {self.driver.current_url}")
            
        except Exception as e:
            raise Exception(f"❌ 登录验证失败: {e}")

    def get_server_url(self):
        logger.info("🔍 查找服务器链接...")
        
        if '/home' not in self.driver.current_url:
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
            renew_selector = "a[href*='/renew/']:not(.opacity-50):not(.pointer-events-none)"
            renewed_count = 0
            original_window = self.driver.current_window_handle
            
            while True:
                renew_buttons = self.driver.find_elements(By.CSS_SELECTOR, renew_selector)
                if not renew_buttons:
                    break

                renew_url = renew_buttons[0].get_attribute('href')
                logger.info(f"🚀 处理第 {renewed_count + 1} 个续期链接")
                
                self.driver.execute_script("window.open(arguments[0]);", renew_url)
                time.sleep(1)
                self.driver.switch_to.window(self.driver.window_handles[-1])
                time.sleep(self.RENEW_WAIT_TIME)
                self.driver.close()
                self.driver.switch_to.window(original_window)
                renewed_count += 1
                
                self.driver.get(self.server_url)
                time.sleep(3)

            if renewed_count == 0:
                disabled = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/renew/'].opacity-50")
                return "⏳ 今日已续期" if disabled else "⏳ 未找到续期按钮"

            self.driver.get(self.server_url)
            time.sleep(5)
            
            final_details, final_value = self.extract_expiry_days(self.driver.page_source)
            logger.info(f"ℹ️ 最终过期时间: {final_details}")
            
            if final_value > self.initial_expiry_value:
                return f"✅ 续期成功! {self.initial_expiry_details} -> {final_details}"
            return f"⚠️ 天数未变化 ({final_details})"

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
            return False, "❌ 登录或获取服务器失败"
                
        except Exception as e:
            logger.error(f"❌ 失败: {str(e)}")
            return False, f"❌ 失败: {str(e)}"
        
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
        logger.info("⏳ 加载账号配置...")
        
        accounts_str = os.getenv('PELLA_ACCOUNTS', os.getenv('LEAFLOW_ACCOUNTS', '')).strip()
        if accounts_str:
            for pair in [p.strip() for p in re.split(r'[;,]', accounts_str) if p.strip()]:
                if ':' in pair:
                    email, password = pair.split(':', 1)
                    if email.strip() and password.strip():
                        accounts.append({'email': email.strip(), 'password': password.strip()})
            if accounts:
                logger.info(f"👉 加载了 {len(accounts)} 个账号")
                return accounts
        
        email = os.getenv('PELLA_EMAIL', os.getenv('LEAFLOW_EMAIL', '')).strip()
        password = os.getenv('PELLA_PASSWORD', os.getenv('LEAFLOW_PASSWORD', '')).strip()
        
        if email and password:
            accounts.append({'email': email, 'password': password})
            logger.info("👉 加载了单个账号")
            return accounts
        
        raise ValueError("⚠️ 未找到有效账号配置")
    
    def send_notification(self, results):
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return
        
        try:
            message = f"🎁 Pella续期通知\n📋 共 {len(results)} 个账号\n\n"
            for email, success, result in results:
                status = "✅" if "成功" in result else ("⏳" if "已续期" in result else "❌")
                masked = email[:3] + "***@" + email.split('@')[1] if '@' in email else email[:3] + "***"
                message += f"{status} {masked}: {result[:60]}\n"
            
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
        manager.run_all()
        exit(0)
    except Exception as e:
        logger.error(f"❌ 错误: {e}")
        exit(1)

if __name__ == "__main__":
    main()
