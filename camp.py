import time
import requests
import os
import calendar
from datetime import datetime
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, InvalidSessionIdException
import subprocess
import platform
import re
import random
from profiles import USER_PROFILES
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()

# Telegram settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Select profile (change this ID to switch users)
SELECTED_PROFILE_ID = 2
current_profile = next((p for p in USER_PROFILES if p["id"] == SELECTED_PROFILE_ID), None)
if current_profile is None:
    raise RuntimeError(f"No USER_PROFILES entry found for SELECTED_PROFILE_ID={SELECTED_PROFILE_ID}")

# Monitoring interval (seconds)
MONITORING_INTERVAL = 22 # no. of seconds to wait between checks
EARLIEST_DATE_RESPONSE_WAIT_SECONDS = 6
SLOT_TABLE_WAIT_SECONDS = 10
SLOT_SELECTION_SETTLE_SECONDS = 2
MAX_SLOT_RECHECKS = 12
TELEGRAM_TIMEOUT_SECONDS = 8
TELEGRAM_PHOTO_TIMEOUT_SECONDS = 15

SCREENSHOT_PATH = f"available_slots_{current_profile['id']}.png"
CART_URL = "https://www.ssdcl.com.sg/User/Payment/ReviewItems"

# Pause/Resume state
is_paused = False

# Keep-awake process
caffeinate_process = None

def profile_label():
    return current_profile.get("name") or current_profile.get("telegram_name") or f"profile {current_profile['id']}"

def validate_config():
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")

    for key in ("username", "password"):
        if not current_profile.get(key):
            missing.append(f"current_profile.{key}")

    if missing:
        raise RuntimeError(f"Missing required config: {', '.join(missing)}")

def short_error_message(error):
    """Return a concise, Telegram-friendly error summary."""
    text = str(error).strip()
    lower_text = text.lower()

    if "no such window" in lower_text or "web view not found" in lower_text:
        return "Browser window was closed. Restart the script."
    if "invalid session id" in lower_text:
        return "Browser session ended. Restart the script."
    if "target window already closed" in lower_text:
        return "Browser window was closed. Restart the script."

    first_line = text.splitlines()[0].strip() if text else error.__class__.__name__
    if len(first_line) > 180:
        first_line = first_line[:177] + "..."
    return first_line

validate_config()

# --- Keep-awake ---

def start_keep_awake():
    """Prevent computer from sleeping while script runs"""
    global caffeinate_process
    system = platform.system()
    
    try:
        if system == "Darwin":  # macOS
            # Use caffeinate to prevent sleep
            caffeinate_process = subprocess.Popen(['caffeinate', '-d'])
            print("☕ Keep-awake enabled")
        elif system == "Windows":
            # Use PowerShell to prevent sleep on Windows
            caffeinate_process = subprocess.Popen([
                'powershell', '-Command',
                'while($true) { $null = [System.Threading.Thread]::CurrentThread; Start-Sleep -Seconds 60 }'
            ], creationflags=subprocess.CREATE_NO_WINDOW)
            print("☕ Keep-awake enabled")
        elif system == "Linux":
            # Use systemd-inhibit or caffeine on Linux
            try:
                caffeinate_process = subprocess.Popen(['systemd-inhibit', '--what=sleep', '--who=ssdc_camper', '--why=Booking automation', 'sleep', 'infinity'])
                print("☕ Keep-awake enabled")
            except FileNotFoundError:
                print("⚠️ Sleep prevention is not available on this system")
        else:
            print("⚠️ Sleep prevention is not available on this system")
    except Exception as e:
        print(f"⚠️ Could not start keep-awake: {e}")

def stop_keep_awake():
    """Stop preventing sleep"""
    global caffeinate_process
    if caffeinate_process:
        try:
            caffeinate_process.terminate()
            caffeinate_process.wait(timeout=5)
            print("☕ Keep-awake deactivated")
        except Exception:
            try:
                caffeinate_process.kill()
            except Exception:
                pass
        caffeinate_process = None

# --- Browser setup ---
def build_chrome_options():
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--incognito")
    chrome_options.add_argument("--disable-background-mode")
    return chrome_options

def get_chrome_major_version():
    """Return the installed Chrome major version, e.g. 148 from 148.0.x."""
    system = platform.system()
    commands = []

    if system == "Darwin":
        commands = [
            ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version"],
            ["/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta", "--version"],
        ]
    elif system == "Windows":
        commands = [
            ["reg", "query", r"HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon", "/v", "version"],
            ["reg", "query", r"HKEY_LOCAL_MACHINE\Software\Google\Chrome\BLBeacon", "/v", "version"],
        ]
    else:
        commands = [
            ["google-chrome", "--version"],
            ["google-chrome-stable", "--version"],
            ["chromium-browser", "--version"],
            ["chromium", "--version"],
        ]

    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            version_text = f"{result.stdout} {result.stderr}"
            match = re.search(r"(\d+)\.\d+\.\d+\.\d+", version_text)
            if match:
                return int(match.group(1))
        except (FileNotFoundError, subprocess.SubprocessError):
            continue

    return None

chrome_major_version = get_chrome_major_version()

try:
    if chrome_major_version:
        print(f"🌐 Starting ChromeDriver for Chrome {chrome_major_version}...")
        driver = uc.Chrome(options=build_chrome_options(), version_main=chrome_major_version, use_subprocess=True)
    else:
        print("⚠️ Chrome version not detected; using ChromeDriver auto-detection...")
        driver = uc.Chrome(options=build_chrome_options(), use_subprocess=True)
    print("✅ Automation started")
except Exception as e:
    print(f"❌ Failed to start ChromeDriver: {e}")
    print("Tip: update dependencies with: python3 -m pip install -U undetected-chromedriver selenium")
    raise

# --- Telegram ---

def get_latest_update_id():
    """Return the latest Telegram update ID so old commands are ignored on startup."""
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            timeout=TELEGRAM_TIMEOUT_SECONDS
        )
        if response.status_code == 200:
            updates = response.json().get('result', [])
            return updates[-1].get('update_id', 0) if updates else 0
    except requests.RequestException as e:
        print(f"⚠️ Telegram update check failed: {e}")
        return 0

last_update_id = get_latest_update_id()

def check_telegram_messages():
    """Process Telegram commands and return 'kill' when a stop command is received."""
    global last_update_id, is_paused
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            timeout=TELEGRAM_TIMEOUT_SECONDS
        )
        if response.status_code == 200:
            updates = response.json().get('result', [])
            for update in updates:
                update_id = update.get('update_id', 0)
                if update_id <= last_update_id:
                    continue
                
                message = update.get('message', {})
                text = message.get('text', '').strip().lower()
                last_update_id = update_id
                
                if text in ['/kill', '/stop', '/quit']:
                    print("🛑 Stop command received")
                    send_telegram_alert("🛑 Automation stopped.")
                    return 'kill'
                
                elif text == '/pause':
                    if not is_paused:
                        is_paused = True
                        print("⏸️ Pause command received")
                        send_telegram_alert("⏸️ Automation paused. Send /resume to continue.")
                    else:
                        send_telegram_alert("⏸️ Automation is already paused. Send /resume to continue.")
                
                elif text == '/resume':
                    if is_paused:
                        is_paused = False
                        print("▶️ Resume command received")
                        send_telegram_alert("▶️ Automation resumed.")
                    else:
                        send_telegram_alert("▶️ Automation is already running. Send /pause to pause.")
                
                elif text == '/status':
                    status = "⏸️ PAUSED" if is_paused else "▶️ RUNNING"
                    send_telegram_alert(f"Status: {status}\nProfile: {profile_label()}\nInterval: {MONITORING_INTERVAL}s")
                    
    except Exception as e:
        print(f"⚠️ Error checking Telegram messages: {e}")
    
    return None

def send_telegram_alert(message):
    """Send a text message via Telegram"""
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={'chat_id': TELEGRAM_CHAT_ID, 'text': message},
            timeout=TELEGRAM_TIMEOUT_SECONDS
        )
        if response.status_code != 200:
            print(f"❌ Telegram failed: {response.status_code} - {response.text}")
    except Exception as e:
        print("❌ Telegram send error:", e)

def send_telegram_screenshot(photo_path, caption=None):
    """Send a screenshot via Telegram"""
    try:
        with open(photo_path, 'rb') as photo:
            response = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                data={'chat_id': TELEGRAM_CHAT_ID, 'caption': caption or ''},
                files={'photo': photo},
                timeout=TELEGRAM_PHOTO_TIMEOUT_SECONDS
            )
        if response.status_code == 200:
            print("📸 Screenshot sent to Telegram.")
        else:
            print(f"❌ Telegram screenshot failed: {response.status_code} - {response.text}")
    except Exception as e:
        print("❌ Failed to send screenshot:", e)

# --- CAPTCHA handling ---

def is_normal_page_ready():
    """Return True when a usable login or booking form is already visible."""
    try:
        username_fields = driver.find_elements(By.ID, "UserName")
        password_fields = driver.find_elements(By.ID, "Password")
        login_form_ready = (
            any(field.is_displayed() and field.is_enabled() for field in username_fields)
            and any(field.is_displayed() and field.is_enabled() for field in password_fields)
        )

        selected_date_fields = driver.find_elements(By.ID, "SelectedDate")
        booking_form_ready = any(
            field.is_displayed() and field.is_enabled() for field in selected_date_fields
        )

        return login_form_ready or booking_form_ready
    except Exception:
        return False

def is_captcha_present():
    """Return True when a manual CAPTCHA is active."""
    if is_normal_page_ready():
        return False

    # Check for multiple indicators of the Cloudflare challenge page
    indicators = [
        "//h2[contains(text(), 'Performing security verification')]",
        "//h2[contains(text(), 'security verification')]",
        "//p[contains(text(), 'Verify you are human')]",
        "//p[contains(text(), 'security service to protect')]",
        "//p[contains(text(), 'verifies you are not a bot')]",
    ]
    for xpath in indicators:
        try:
            element = driver.find_element(By.XPATH, xpath)
            if element.is_displayed():
                print("🔍 CAPTCHA detected")
                return True
        except NoSuchElementException:
            continue
    
    try:
        turnstile_input = driver.find_element(By.CSS_SELECTOR, "input[name='cf-turnstile-response']")
        turnstile_response = (turnstile_input.get_attribute("value") or "").strip()

        if not turnstile_response:
            return True
    except NoSuchElementException:
        pass
    
    return False

def wait_for_captcha_to_clear():
    """Wait for the user to resolve CAPTCHA."""
    print("🛑 CAPTCHA detected. Waiting for manual completion...")
    send_telegram_alert("🛑 CAPTCHA detected, resolve manually.")

    last_alert_time = time.time()

    while True:
        time.sleep(3)

        # Keep Telegram controls responsive while waiting on the challenge.
        result = check_telegram_messages()
        if result == 'kill':
            print("🛑 Stopping automation...")
            driver.quit()
            exit()

        # A pause suppresses CAPTCHA reminders and browser interaction.
        if is_paused:
            last_alert_time = time.time()
            continue

        if not is_captcha_present():
            break

        # Send repeated alerts every 15 seconds
        if time.time() - last_alert_time > 15:
            send_telegram_alert("🛑 CAPTCHA detected, resolve manually.")
            last_alert_time = time.time()

    print("✅ CAPTCHA resolved")

# --- Modal handling ---

def close_modal_if_exists():
    """Close modal when it appears."""
    try:
        # Wait for modal to appear
        WebDriverWait(driver, 3).until(
            EC.visibility_of_element_located((By.ID, "modalMsg"))
        )
        time.sleep(0.5)  # Let modal settle

        # Try multiple strategies to close the modal
        max_attempts = 5
        for attempt in range(max_attempts):
            # Strategy 1: Click footer Close button
            try:
                close_btn = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "#modalMsg .modal-footer button[data-dismiss='modal']"))
                )
                close_btn.click()
                
                WebDriverWait(driver, 2).until(EC.invisibility_of_element_located((By.ID, "modalMsg")))
                return True
            except (TimeoutException, Exception):
                pass
            
            # Strategy 2: Click X button
            try:
                close_x = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "#modalMsg button.close[data-dismiss='modal']"))
                )
                close_x.click()
                WebDriverWait(driver, 2).until(EC.invisibility_of_element_located((By.ID, "modalMsg")))
                return True
            except (TimeoutException, Exception):
                pass
            
            # Strategy 3: JavaScript hide
            try:
                driver.execute_script("$('#modalMsg').modal('hide');")
                time.sleep(1)
                
                WebDriverWait(driver, 2).until(EC.invisibility_of_element_located((By.ID, "modalMsg")))
                return True
            except (TimeoutException, Exception):
                pass
            
            # Strategy 4: Click backdrop
            try:
                backdrop = driver.find_element(By.CSS_SELECTOR, ".modal-backdrop")
                backdrop.click()
                time.sleep(1)
                
                WebDriverWait(driver, 2).until(EC.invisibility_of_element_located((By.ID, "modalMsg")))
                return True
            except (TimeoutException, Exception):
                pass
            
            # Strategy 5: Press ESC
            try:
                driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
                time.sleep(1)
                
                WebDriverWait(driver, 2).until(EC.invisibility_of_element_located((By.ID, "modalMsg")))
                return True
            except (TimeoutException, Exception):
                pass
            
            time.sleep(1)  # Wait before next attempt
        
        # All attempts failed
        print(f"❌ Failed to close modal after {max_attempts} attempts.")
        send_telegram_alert(f"❌ Failed to close modal after {max_attempts} attempts.")
        return False

    except TimeoutException:
        # Modal never appeared
        return False

def ensure_modal_is_closed():
    """Verify modal is fully closed before proceeding"""
    max_checks = 10
    for check in range(max_checks):
        try:
            modal = driver.find_element(By.ID, "modalMsg")
            if modal.is_displayed():
                print(f"🔍 Modal still present (check {check + 1}/{max_checks}), closing...")
                close_modal_if_exists()
                time.sleep(1)
            else:
                return True
        except NoSuchElementException:
            return True
    
    print(f"❌ Modal still present after {max_checks} checks.")
    send_telegram_alert(f"❌ Modal persistently present after {max_checks} checks.")
    return False

def is_modal_present():
    """Quick check if modal is currently displayed"""
    try:
        modal = driver.find_element(By.CLASS_NAME, 'modal-dialog')
        return modal.is_displayed()
    except NoSuchElementException:
        return False

def close_visible_modal_if_exists():
    """Close any visible Bootstrap-style modal."""
    close_selectors = [
        "#modalMsg .modal-footer button[data-dismiss='modal']",
        "#modalMsg button.close[data-dismiss='modal']",
        ".modal.in .modal-footer button[data-dismiss='modal']",
        ".modal.show .modal-footer button[data-dismiss='modal']",
        ".modal.in button.close[data-dismiss='modal']",
        ".modal.show button.close[data-dismiss='modal']",
        ".modal-footer button[data-dismiss='modal']",
        "button.close[data-dismiss='modal']",
    ]

    for selector in close_selectors:
        try:
            close_btn = WebDriverWait(driver, 1).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            close_btn.click()
            WebDriverWait(driver, 3).until(
                lambda d: not any(m.is_displayed() for m in d.find_elements(By.CLASS_NAME, 'modal-dialog'))
            )
            return True
        except Exception:
            pass

    try:
        driver.execute_script("if (window.jQuery) { $('.modal').modal('hide'); }")
        WebDriverWait(driver, 3).until(
            lambda d: not any(m.is_displayed() for m in d.find_elements(By.CLASS_NAME, 'modal-dialog'))
        )
        return True
    except Exception:
        pass

    try:
        driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
        WebDriverWait(driver, 3).until(
            lambda d: not any(m.is_displayed() for m in d.find_elements(By.CLASS_NAME, 'modal-dialog'))
        )
        return True
    except Exception:
        return False


def normalize_slot_timing(slot_text):
    """Normalize slot text so the same timing can be skipped on later checks."""
    return re.sub(r"\s+", " ", slot_text or "").strip().lower()

def get_available_slot_links():
    return driver.find_elements(
        By.XPATH,
        "//table[contains(@class, 'main-table') and not(contains(@class, 'clone'))]//td[not(contains(., 'n/a')) and not(contains(@class, 'c-gray'))]//a"
    )

def choose_next_slot(slot_links, attempted_slot_timings):
    candidates = []
    for index, slot in enumerate(slot_links, start=1):
        slot_text = slot.text.strip() or f"slot {index}"
        slot_key = normalize_slot_timing(slot_text)
        candidates.append((slot, slot_text, slot_key))

    for candidate in reversed(candidates):
        if candidate[2] not in attempted_slot_timings:
            return candidate

    if len(candidates) == 1:
        print("ℹ️ Only an already-tried slot remains; trying it again.")
        return candidates[-1]

    return None

def click_check_for_availability():
    check_btn = WebDriverWait(driver, 5).until(
        EC.element_to_be_clickable((By.ID, 'btn_checkforava'))
    )
    driver.execute_script("arguments[0].click();", check_btn)
    print("👆 Clicked Check for availability")


def open_payment_review_tab():
    current_window = driver.current_window_handle
    driver.execute_script("window.open(arguments[0], '_blank');", CART_URL)
    driver.switch_to.window(current_window)
    print("💳 Opened payment review in a new tab")

# --- Login and navigation ---

def login_and_continue():
    """Log into the SSDC system"""
    print("✅ Automation started")
    driver.get("https://www.ssdcl.com.sg/User/Login")
    print("🌐 Opened login page")

    try:
        # Wait for login form or CAPTCHA
        WebDriverWait(driver, 10).until(
            lambda d: is_normal_page_ready() or is_captcha_present()
        )
    except TimeoutException:
        print("⚠️ Login page elements not detected in time.")
        send_telegram_alert("⚠️ Login page elements not detected – check manually.")
        return

    # Handle CAPTCHA if present
    if is_captcha_present():
        wait_for_captcha_to_clear()

        try:
            WebDriverWait(driver, 60).until(
                lambda d: "/User/Information" in d.current_url or is_normal_page_ready()
            )
        except TimeoutException:
            print("⚠️ Timed out waiting for login page after CAPTCHA.")
            send_telegram_alert("⚠️ Timed out waiting for login page after CAPTCHA.")
            return

        if "/User/Information" in driver.current_url:
            print("✅ Already logged in after CAPTCHA")
            return

    # Fill in login credentials
    try:
        username_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "UserName"))
        )
        password_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "Password"))
        )
        username_field.clear()
        username_field.send_keys(current_profile["username"])
        password_field.clear()
        password_field.send_keys(current_profile["password"])
    except TimeoutException:
        if "/User/Information" in driver.current_url:
            print("✅ Already logged in")
            return
        print("⚠️ Login fields did not appear in time.")
        send_telegram_alert("⚠️ Login fields did not appear in time — check manually.")
        return
    except NoSuchElementException:
        if "/User/Information" in driver.current_url:
            print("✅ Already logged in")
            return
        print("⚠️ Login fields not found.")
        send_telegram_alert("⚠️ Login fields not found — check manually.")
        return

    # Click login button
    try:
        login_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[type="submit"]'))
        )
        login_btn.click()
        print("🔐 Clicked Login")
    except TimeoutException:
        print("⚠️ Login button not clickable")
        send_telegram_alert("⚠️ Login button not clickable — check manually.")
        return

    # Wait for successful login
    try:
        WebDriverWait(driver, 15).until(lambda d: "/User/Information" in d.current_url)
        print("✅ Logged in successfully")
    except TimeoutException:
        print("⚠️ Login redirect timeout")
        send_telegram_alert("⚠️ Login redirect timeout — check manually.")

def go_to_booking_page():
    """Navigate to the booking page"""
    driver.get("https://www.ssdcl.com.sg/User/Booking/AddBooking?bookingType=PL")
    print("➡️ Opened booking page")

def wait_interval():
    time.sleep(random.uniform(MONITORING_INTERVAL * 0.8, MONITORING_INTERVAL * 1.3))

# --- Monitoring loop ---

def monitor_loop():
    """Main monitoring loop - checks for available slots continuously"""
    redirect_failures = 0
    max_redirect_attempts = 3
    no_popup_streak = 0  # Track consecutive checks without a response message
    
    while True:
        # Check for Telegram commands
        result = check_telegram_messages()
        if result == 'kill':
            print("🛑 Stopping automation...")
            driver.quit()
            exit()
        
        # Handle paused state
        if is_paused:
            print("⏸️ Paused - waiting for /resume command...")
            time.sleep(5)
            continue
            
        try:
            # STEP 1: Set lesson date to last day of 3 months from now
            try:
                now = datetime.now()
                future_month = now.month + 3
                future_year = now.year
                if future_month > 12:
                    future_month -= 12
                    future_year += 1
                last_day = calendar.monthrange(future_year, future_month)[1]
                future_date = datetime(future_year, future_month, last_day)
                future_date_str = future_date.strftime("%d %b %Y")  # e.g. "31 May 2026"
                
                date_input = driver.find_element(By.ID, 'SelectedDate')
                driver.execute_script(
                    "arguments[0].value = arguments[1];", date_input, future_date_str
                )
            except NoSuchElementException:
                print("⚠️ Could not find lesson date input field")
            
            # STEP 2: Store the date as baseline for change detection
            original_date = None
            try:
                date_input = driver.find_element(By.ID, 'SelectedDate')
                original_date = date_input.get_attribute('value')
            except NoSuchElementException:
                print("⚠️ Could not read lesson date")
            
            # Click Get Earliest Date
            try:
                search_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.ID, 'button-searchDate'))
                )
                # Use JavaScript click for instant execution (faster than regular click)
                driver.execute_script("arguments[0].click();", search_btn)
                print(f"🔎 Checking earliest date from {future_date_str}")
                redirect_failures = 0
            except TimeoutException:
                redirect_failures += 1
                print(f"🔄 Button not found, redirecting... (attempt {redirect_failures})")
                
                if redirect_failures >= max_redirect_attempts:
                    # Check for CAPTCHA
                    if is_captcha_present():
                        print("🔍 CAPTCHA detected during page recovery")
                        wait_for_captcha_to_clear()
                        redirect_failures = 0
                        continue
                    
                    send_telegram_alert(f"❌ Redirect failed {max_redirect_attempts} times. Manual intervention needed.")
                    time.sleep(MONITORING_INTERVAL * 3)
                    redirect_failures = 0
                
                driver.get("https://www.ssdcl.com.sg/User/Booking/AddBooking?bookingType=PL")
                time.sleep(3)
                continue

            # STEP 3: Watch for the asynchronous response. Check the date first on
            # every pass so an available slot wins the race against modal handling.
            date_changed = False
            check_for_availability_clicked = False
            modal_appeared = False

            def click_check_for_availability_once(check_btn, fast_path=False):
                """Issue at most one Check for availability click per earliest-date attempt."""
                nonlocal check_for_availability_clicked
                if check_for_availability_clicked:
                    return False

                # Reserve the click before sending it. If the browser response is
                # uncertain, do not risk a duplicate request in this cycle.
                check_for_availability_clicked = True
                driver.execute_script("arguments[0].click();", check_btn)
                if fast_path:
                    print("⚡️ Date changed; clicked Check for availability")
                else:
                    print("👆 Clicked Check for availability")
                return True

            if original_date:
                try:
                    response_deadline = time.monotonic() + EARLIEST_DATE_RESPONSE_WAIT_SECONDS
                    while time.monotonic() < response_deadline:
                        try:
                            date_input = driver.find_element(By.ID, 'SelectedDate')
                            new_date = date_input.get_attribute('value')

                            if new_date != original_date:
                                date_changed = True
                                print(f"🎉 Earliest date changed: '{original_date}' -> '{new_date}'")

                                check_btn = driver.find_element(By.ID, 'btn_checkforava')
                                click_check_for_availability_once(check_btn, fast_path=True)
                                break

                            if is_modal_present():
                                modal_appeared = True
                                no_popup_streak = 0
                                break
                        except NoSuchElementException:
                            pass

                        time.sleep(0.05)

                except Exception as e:
                    print(f"⚠️ Error checking date: {e}")

            # STEP 4: Fallback modal check when the date baseline could not be read.
            if not original_date:
                try:
                    WebDriverWait(driver, EARLIEST_DATE_RESPONSE_WAIT_SECONDS).until(
                        EC.visibility_of_element_located((By.CLASS_NAME, 'modal-dialog'))
                    )
                    modal_appeared = True
                    no_popup_streak = 0
                except TimeoutException:
                    pass

            # STEP 5: Handle modal if it appeared
            if modal_appeared:
                close_modal_if_exists()
                
                if not ensure_modal_is_closed():
                    print(f"❌ Could not close modal. Waiting {MONITORING_INTERVAL}s...")
                    wait_interval()
                    continue
                
                print(f"ℹ️ No slots available. Waiting {MONITORING_INTERVAL}s before retry...")
                wait_interval()
                continue

            # If there is no response message and no date change, retry once before checking availability
            if date_changed:
                no_popup_streak = 0
            else:
                if not ensure_modal_is_closed():
                    print(f"⚠️ Unexpected modal found. Waiting {MONITORING_INTERVAL}s...")
                    wait_interval()
                    continue
                
                no_popup_streak += 1
                if no_popup_streak < 2:
                    print(f"ℹ️ No response shown ({no_popup_streak}/2). Retrying in 5s...")
                    time.sleep(5)
                    continue
                else:
                    print("ℹ️ No response shown twice; checking availability")
                    no_popup_streak = 0

            # Check for CAPTCHA unless the date changed
            if not date_changed and is_captcha_present():
                wait_for_captcha_to_clear()
                wait_interval()
                continue

            # Click Check for availability unless it was already clicked after a date change.
            try:
                if not check_for_availability_clicked:
                    check_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.ID, 'btn_checkforava'))
                    )
                    click_check_for_availability_once(check_btn)
                # Check for CAPTCHA after clicking
                if is_captcha_present():
                    wait_for_captcha_to_clear()
                    wait_interval()
                    continue

            except Exception as e:
                print(f"❌ Failed to click 'Check for availability': {e}")
                
                if is_modal_present():
                    print("ℹ️  Modal detected; closing it")
                    close_modal_if_exists()
                    ensure_modal_is_closed()
                    wait_interval()
                    continue
                
                send_telegram_alert(f"❌ Failed to click 'Check for availability'.")
                wait_interval()
                continue

            # Load and process the availability table
            attempted_slot_timings = set()
            slot_rechecks = 0

            while True:
                slot_rechecks += 1
                if slot_rechecks > MAX_SLOT_RECHECKS:
                    print("⚠️ Reached slot recheck limit for this availability cycle.")
                    break

                try:
                    slot_table = WebDriverWait(driver, SLOT_TABLE_WAIT_SECONDS).until(
                        EC.presence_of_element_located(
                            (By.XPATH, "//table[contains(@class, 'main-table') and not(contains(@class, 'clone'))]")
                        )
                    )
                    all_slots = get_available_slot_links()
                    if not all_slots:
                        print("⚠️ No selectable slots found.")
                        break

                    print(f"🎯 Found {len(all_slots)} available slot option(s)")
                    selected_slot = choose_next_slot(all_slots, attempted_slot_timings)
                    if not selected_slot:
                        print("ℹ️ All visible slot timings were already tried; ending this availability cycle.")
                        break

                    slot, slot_text, slot_key = selected_slot
                    attempted_slot_timings.add(slot_key)

                    try:
                        selected_date = driver.find_element(By.ID, "SelectedDate").get_attribute("value").strip()
                    except Exception:
                        selected_date = ""

                    slot_description = f"{selected_date}, {slot_text}" if selected_date else slot_text

                    screenshot_path = SCREENSHOT_PATH
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", slot_table)
                        time.sleep(0.2)
                        slot_table.screenshot(screenshot_path)
                    except Exception:
                        try:
                            driver.save_screenshot(screenshot_path)
                        except Exception:
                            screenshot_path = None
                            print("⚠️ Screenshot failed")

                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", slot)
                    driver.execute_script("arguments[0].click();", slot)
                    print(f"✅ Selected slot: {slot_description}")

                    if screenshot_path and os.path.exists(screenshot_path):
                        send_telegram_screenshot(screenshot_path, f"✅ SLOT SELECTED: {slot_description}")
                        try:
                            os.remove(screenshot_path)
                        except Exception:
                            pass

                    time.sleep(SLOT_SELECTION_SETTLE_SECONDS)
                    if is_modal_present():
                        close_visible_modal_if_exists()
                    open_payment_review_tab()

                    try:
                        click_check_for_availability()
                    except Exception as e:
                        print(f"❌ Failed to refresh availability after slot selection: {e}")
                        break

                    if is_captcha_present():
                        wait_for_captcha_to_clear()
                        break

                except Exception as e:
                    print(f"⚠️ Could not load availability table: {e}")

                    if is_captcha_present():
                        print("🔍 CAPTCHA detected after availability check")
                        wait_for_captcha_to_clear()
                        break

                    if is_modal_present():
                        print("ℹ️  Modal detected; closing it")
                        close_modal_if_exists()
                        ensure_modal_is_closed()
                        break

                    send_telegram_alert("⚠️ Could not load availability table.")
                    break

            # Wait before next check
            wait_interval()

        except InvalidSessionIdException as session_error:
            print(f"❌ Browser session lost: {session_error}")
            send_telegram_alert("❌ Browser session lost. Restart the script.")
            stop_keep_awake()
            break
            
        except Exception as loop_error:
            print(f"❗ Unexpected monitoring error: {loop_error}")
            
            try:
                # Check for CAPTCHA first
                if is_captcha_present():
                    print("🔍 CAPTCHA detected during error handling")
                    wait_for_captcha_to_clear()
                    continue
                
                if is_modal_present():
                    print("ℹ️  detected during recovery")
                    close_modal_if_exists()
                    ensure_modal_is_closed()
            except InvalidSessionIdException:
                print("❌ Browser session lost during recovery")
                send_telegram_alert("❌ Browser session lost. Restart the script.")
                stop_keep_awake()
                break
            
            send_telegram_alert(f"❗ Unexpected error: {short_error_message(loop_error)}")
            wait_interval()

# --- Entry point ---
if __name__ == "__main__":
    try:
        # Start sleep prevention
        start_keep_awake()
        
        login_and_continue()
        go_to_booking_page()
        monitor_loop()
    except KeyboardInterrupt:
        print("\n🛑 Script interrupted by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        send_telegram_alert(f"❌ Fatal error: {short_error_message(e)}")
    finally:
        # Clean up browser and sleep prevention
        stop_keep_awake()
        try:
            driver.quit()
            print("🔒 Browser closed")
        except Exception:
            pass
