"""
Local browser test - 10 visits to Yandex Maps through proxy.
On captcha: clicks checkbox, waits for puzzle, keeps browser open.
"""
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
import time
import random
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from core.browser_manager import _LocalProxyForwarder

# Proxy settings  
PROXY_HOST = "bproxy.site"
PROXY_PORT = 12138
PROXY_USER = "Hes9yF"
PROXY_PASS = "zAU2vaEUf4TU"
PROXY_TYPE = "socks5"

TARGETS = [
    "https://yandex.ru/maps/org/medsemya/108007547689",
    "https://yandex.ru/maps/org/benesque/193289471730/",
]


def handle_captcha(driver, visit_num):
    """Click checkbox, wait for puzzle captcha, keep browser open."""
    print("   ⚠️  CAPTCHA DETECTED! Attempting checkbox click...")
    
    # Save screenshot before click
    try:
        driver.save_screenshot(f"screenshots/captcha_before_{visit_num}.png")
    except:
        pass
    
    # Try clicking the checkbox
    checkbox_clicked = False
    checkbox_selectors = [
        ".CheckboxCaptcha-Button",
        "[class*='CheckboxCaptcha'] button",
        "button[type='submit']",
        ".CheckboxCaptcha-Anchor",
    ]
    
    for selector in checkbox_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for el in elements:
                if el.is_displayed():
                    # Human-like: move to element, pause, click
                    time.sleep(random.uniform(0.5, 1.5))
                    ActionChains(driver).move_to_element(el).pause(random.uniform(0.3, 0.8)).click().perform()
                    checkbox_clicked = True
                    print(f"   ✅ Clicked checkbox: {selector}")
                    break
        except Exception as e:
            continue
        if checkbox_clicked:
            break
    
    if not checkbox_clicked:
        # Try JS click
        try:
            result = driver.execute_script("""
                var btn = document.querySelector('.CheckboxCaptcha-Button') || 
                          document.querySelector('[class*="CheckboxCaptcha"] button') ||
                          document.querySelector('button[type="submit"]');
                if (btn) { btn.click(); return true; }
                var form = document.getElementById('checkbox-captcha-form');
                if (form) { form.submit(); return true; }
                return false;
            """)
            if result:
                checkbox_clicked = True
                print("   ✅ Clicked checkbox via JS")
        except:
            pass
    
    if not checkbox_clicked:
        print("   ❌ Could not find/click checkbox")
        return "checkbox_failed"
    
    # Wait for reaction
    print("   ⏳ Waiting for captcha reaction (up to 15s)...")
    pre_url = driver.current_url
    
    for i in range(15):
        time.sleep(1)
        try:
            new_url = driver.current_url
            # Check if redirected to actual page (captcha passed!)
            if new_url != pre_url and 'captcha' not in new_url.lower():
                print(f"   🎉 Captcha passed! Redirected to: {new_url[:80]}")
                return "passed"
        except:
            pass
        
        # Check if puzzle/silhouette appeared
        page_src = driver.page_source.lower()
        if any(x in page_src for x in ['silhouette', 'advancedcaptcha', 'task-grid', 'puzzlecaptcha']):
            print(f"   🧩 PUZZLE CAPTCHA appeared at {i+1}s!")
            try:
                driver.save_screenshot(f"screenshots/captcha_puzzle_{visit_num}.png")
                # Save HTML for debug
                with open(f"screenshots/captcha_puzzle_{visit_num}.html", 'w') as f:
                    f.write(driver.page_source)
                print(f"   📄 Saved: screenshots/captcha_puzzle_{visit_num}.png/.html")
            except:
                pass
            return "puzzle"
        
        # Check for image grid
        grid_selectors = [
            "[class*='AdvancedCaptcha']",
            "[class*='Task-Grid']",
            "canvas[class*='captcha']",
        ]
        for sel in grid_selectors:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                if elems and any(e.is_displayed() for e in elems):
                    print(f"   🖼️  Image grid appeared at {i+1}s: {sel}")
                    driver.save_screenshot(f"screenshots/captcha_grid_{visit_num}.png")
                    return "image_grid"
            except:
                continue
    
    # Check final state
    final_url = driver.current_url
    if 'captcha' not in final_url.lower():
        print("   🎉 Captcha seemed to pass (no captcha in URL)")
        return "passed"
    
    print("   ❓ Checkbox clicked but nothing happened after 15s")
    try:
        driver.save_screenshot(f"screenshots/captcha_after_{visit_num}.png")
        with open(f"screenshots/captcha_after_{visit_num}.html", 'w') as f:
            f.write(driver.page_source)
    except:
        pass
    return "stuck"


def run_visit(visit_num, target_url, forwarder_port):
    """Run a single visit."""
    print(f"\n{'='*60}")
    print(f"🚀 Visit #{visit_num} → {target_url}")
    print(f"{'='*60}")
    
    options = uc.ChromeOptions()
    options.add_argument("--lang=ru-RU")
    options.add_argument("--accept-lang=ru-RU")
    options.add_argument(f"--proxy-server=http://127.0.0.1:{forwarder_port}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    
    driver = None
    result = {"status": "unknown", "captcha": False, "captcha_result": None}
    
    try:
        driver = uc.Chrome(options=options, version_main=144)
        
        if visit_num == 1:
            print("🔍 Checking proxy IP...")
            driver.get("https://httpbin.org/ip")
            time.sleep(2)
            print(f"📡 Proxy IP: {driver.find_element('tag name', 'body').text}")
        
        print(f"🌐 Opening page...")
        driver.get(target_url)
        time.sleep(3)
        
        current_url = driver.current_url
        print(f"📍 URL: {current_url[:100]}")
        print(f"📄 Title: {driver.title}")
        
        if "showcaptcha" in current_url:
            result["captcha"] = True
            captcha_result = handle_captcha(driver, visit_num)
            result["captcha_result"] = captcha_result
            
            if captcha_result in ("puzzle", "image_grid", "stuck", "checkbox_failed"):
                result["status"] = "captcha"
                print(f"\n   👀 Browser open — look at the captcha! Press Enter to close...")
                input()
            else:
                result["status"] = "success"
                
        elif "maps" in current_url or "profile" in current_url:
            print("✅ Page loaded OK!")
            result["status"] = "success"
            time.sleep(random.uniform(3, 6))
        else:
            print(f"❓ Unexpected: {current_url[:100]}")
            result["status"] = "unexpected"
        
    except Exception as e:
        err = str(e)[:150]
        print(f"❌ Error: {err}")
        result["status"] = "error"
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
    
    return result


def main():
    print("="*60)
    print("🧪 10 visits with captcha checkbox click test")
    print(f"📡 Proxy: {PROXY_TYPE}://{PROXY_HOST}:{PROXY_PORT}")
    print("="*60)
    
    forwarder = _LocalProxyForwarder(PROXY_HOST, PROXY_PORT, PROXY_USER, PROXY_PASS, PROXY_TYPE)
    local_port = forwarder.start()
    print(f"✅ Forwarder on 127.0.0.1:{local_port}")
    
    results = []
    try:
        for i in range(1, 11):
            target = random.choice(TARGETS)
            r = run_visit(i, target, local_port)
            results.append(r)
            if i < 10:
                pause = random.uniform(2, 5)
                print(f"⏳ Pause {pause:.0f}s...")
                time.sleep(pause)
    except KeyboardInterrupt:
        print("\n🛑 Interrupted")
    finally:
        forwarder.stop()
        print(f"\n{'='*60}")
        print("📊 SUMMARY")
        print(f"{'='*60}")
        ok = sum(1 for r in results if r["status"] == "success")
        cap = sum(1 for r in results if r["status"] == "captcha")
        err = sum(1 for r in results if r["status"] == "error")
        print(f"  ✅ Success: {ok}/{len(results)}")
        print(f"  ⚠️  Captcha: {cap}/{len(results)}")
        print(f"  ❌ Error:   {err}/{len(results)}")
        captcha_results = [r["captcha_result"] for r in results if r.get("captcha_result")]
        if captcha_results:
            print(f"  📋 Captcha outcomes: {captcha_results}")

if __name__ == "__main__":
    main()
