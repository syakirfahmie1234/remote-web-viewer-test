import time
import traceback
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.keys import Keys

def setup_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    driver.implicitly_wait(2)
    return driver

def test_dropdown(driver):
    print("\n--- Testing Dropdown ---")
    driver.get("https://the-internet.herokuapp.com/dropdown")
    wait = WebDriverWait(driver, 5)
    el = wait.until(EC.presence_of_element_located((By.ID, "dropdown")))
    select = Select(el)
    
    passes, fails = 0, 0
    total_time = 0
    
    for i in range(10):
        start = time.time()
        try:
            select.select_by_visible_text("Option 1")
            assert select.first_selected_option.text == "Option 1"
            select.select_by_visible_text("Option 2")
            assert select.first_selected_option.text == "Option 2"
            passes += 1
            print(f"Loop {i+1}: PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1}: FAIL in {time.time()-start:.3f}s - {type(e).__name__}")
        total_time += time.time() - start
        
    print(f"Summary Dropdown: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

def test_checkboxes(driver):
    print("\n--- Testing Checkboxes ---")
    driver.get("https://the-internet.herokuapp.com/checkboxes")
    wait = WebDriverWait(driver, 5)
    
    passes, fails = 0, 0
    total_time = 0
    
    for i in range(10):
        start = time.time()
        try:
            boxes = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='checkbox']")))
            
            box1, box2 = boxes[0], boxes[1]
            
            initial1, initial2 = box1.is_selected(), box2.is_selected()
            
            box1.click()
            assert box1.is_selected() != initial1
            
            box2.click()
            assert box2.is_selected() != initial2
            
            passes += 1
            print(f"Loop {i+1}: PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1}: FAIL in {time.time()-start:.3f}s - {type(e).__name__}")
        total_time += time.time() - start
        
    print(f"Summary Checkboxes: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

def test_jquery_menu(driver):
    print("\n--- Testing jQuery UI Menu ---")
    driver.get("https://the-internet.herokuapp.com/jqueryui/menu")
    wait = WebDriverWait(driver, 5)
    
    passes, fails = 0, 0
    total_time = 0
    from selenium.webdriver.common.action_chains import ActionChains
    
    for i in range(10):
        start = time.time()
        try:
            # Re-locate fresh
            enabled = wait.until(EC.presence_of_element_located((By.LINK_TEXT, "Enabled")))
            ActionChains(driver).move_to_element(enabled).perform()
            
            downloads = wait.until(EC.visibility_of_element_located((By.LINK_TEXT, "Downloads")))
            ActionChains(driver).move_to_element(downloads).perform()
            
            pdf = wait.until(EC.visibility_of_element_located((By.LINK_TEXT, "PDF")))
            pdf.click()
            
            # Collapse by moving to header
            header = driver.find_element(By.TAG_NAME, "h3")
            ActionChains(driver).move_to_element(header).perform()
            
            passes += 1
            print(f"Loop {i+1}: PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1}: FAIL in {time.time()-start:.3f}s - {type(e).__name__}")
        total_time += time.time() - start
        
    print(f"Summary jQuery Menu: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

def test_slider(driver):
    print("\n--- Testing Horizontal Slider ---")
    driver.get("https://the-internet.herokuapp.com/horizontal_slider")
    wait = WebDriverWait(driver, 5)
    
    passes, fails = 0, 0
    total_time = 0
    
    for i in range(10):
        start = time.time()
        try:
            slider = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='range']")))
            initial_val = slider.get_attribute("value")
            
            for _ in range(5):
                slider.send_keys(Keys.ARROW_RIGHT)
            val_after_right = slider.get_attribute("value")
            
            for _ in range(5):
                slider.send_keys(Keys.ARROW_LEFT)
            val_after_left = slider.get_attribute("value")
            
            assert val_after_left == initial_val
            
            passes += 1
            print(f"Loop {i+1}: PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1}: FAIL in {time.time()-start:.3f}s - {type(e).__name__}")
        total_time += time.time() - start
        
    print(f"Summary Slider: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

def test_shadow_dom(driver):
    print("\n--- Testing Shadow DOM ---")
    driver.get("https://the-internet.herokuapp.com/shadowdom")
    wait = WebDriverWait(driver, 5)
    
    passes, fails = 0, 0
    total_time = 0
    
    for i in range(10):
        start = time.time()
        try:
            host = wait.until(EC.presence_of_element_located((By.TAG_NAME, "my-paragraph")))
            shadow_root = driver.execute_script("return arguments[0].shadowRoot", host)
            text_el = shadow_root.find_element(By.CSS_SELECTOR, "slot[name='my-text']")
            assert text_el is not None
            
            passes += 1
            print(f"Loop {i+1}: PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1}: FAIL in {time.time()-start:.3f}s - {type(e).__name__}")
        total_time += time.time() - start
        
    print(f"Summary Shadow DOM: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

def test_floating_menu(driver):
    print("\n--- Testing Floating Menu ---")
    driver.get("https://the-internet.herokuapp.com/floating_menu")
    wait = WebDriverWait(driver, 5)
    
    passes, fails = 0, 0
    total_time = 0
    
    for i in range(10):
        start = time.time()
        try:
            driver.execute_script("window.scrollTo(0, 500);")
            menu_item = wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "Home")))
            menu_item.click()
            
            driver.execute_script("window.scrollTo(0, 0);")
            menu_item = wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "Contact")))
            menu_item.click()
            
            passes += 1
            print(f"Loop {i+1}: PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1}: FAIL in {time.time()-start:.3f}s - {type(e).__name__}")
        total_time += time.time() - start
        
    print(f"Summary Floating Menu: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

def test_infinite_scroll(driver):
    print("\n--- Testing Infinite Scroll ---")
    driver.get("https://the-internet.herokuapp.com/infinite_scroll")
    wait = WebDriverWait(driver, 5)
    
    passes, fails = 0, 0
    total_time = 0
    
    for i in range(10):
        start = time.time()
        try:
            initial_count = len(driver.find_elements(By.CLASS_NAME, "jscroll-added"))
            height = driver.execute_script("return document.body.scrollHeight")
            driver.execute_script(f"window.scrollTo(0, {height});")
            
            wait.until(lambda d: len(d.find_elements(By.CLASS_NAME, "jscroll-added")) > initial_count)
            
            passes += 1
            print(f"Loop {i+1}: PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1}: FAIL in {time.time()-start:.3f}s - {type(e).__name__}")
        total_time += time.time() - start
        
    print(f"Summary Infinite Scroll: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

def test_js_alerts(driver):
    print("\n--- Testing JavaScript Alerts ---")
    driver.get("https://the-internet.herokuapp.com/javascript_alerts")
    wait = WebDriverWait(driver, 5)
    
    passes, fails = 0, 0
    total_time = 0
    
    for i in range(10):
        start = time.time()
        try:
            # Alert
            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='Click for JS Alert']"))).click()
            alert = wait.until(EC.alert_is_present())
            alert.accept()
            assert "You successfully clicked an alert" in driver.find_element(By.ID, "result").text
            
            # Confirm
            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='Click for JS Confirm']"))).click()
            alert = wait.until(EC.alert_is_present())
            alert.dismiss()
            assert "You clicked: Cancel" in driver.find_element(By.ID, "result").text
            
            # Prompt
            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='Click for JS Prompt']"))).click()
            alert = wait.until(EC.alert_is_present())
            alert.send_keys("Hello World")
            alert.accept()
            assert "You entered: Hello World" in driver.find_element(By.ID, "result").text
            
            passes += 1
            print(f"Loop {i+1}: PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1}: FAIL in {time.time()-start:.3f}s - {type(e).__name__}")
        total_time += time.time() - start
        
    print(f"Summary JS Alerts: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

if __name__ == "__main__":
    driver = None
    try:
        driver = setup_driver()
        test_dropdown(driver)
        test_checkboxes(driver)
        test_jquery_menu(driver)
        test_slider(driver)
        test_shadow_dom(driver)
        test_floating_menu(driver)
        test_infinite_scroll(driver)
        test_js_alerts(driver)
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        traceback.print_exc()
    finally:
        if driver:
            driver.quit()
