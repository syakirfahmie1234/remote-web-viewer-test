import time
import traceback
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def get_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,1024")
    return webdriver.Chrome(options=options)

def test_login():
    print("\n--- Testing Login ---")
    driver = get_driver()
    wait = WebDriverWait(driver, 5)
    passes, fails = 0, 0
    total_time = 0
    
    for i in range(10):
        start = time.time()
        try:
            driver.get("about:blank") # force fresh navigation
            driver.get("https://the-internet.herokuapp.com/login")
            
            user_field = wait.until(EC.presence_of_element_located((By.ID, "username")))
            pass_field = wait.until(EC.presence_of_element_located((By.ID, "password")))
            btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
            
            user_field.clear()
            pass_field.clear()
            
            if i % 2 == 0:
                user_field.send_keys("tomsmith")
                pass_field.send_keys("SuperSecretPassword!")
                btn.click()
                
                flash = wait.until(EC.presence_of_element_located((By.ID, "flash")))
                wait.until(lambda d: "You logged into a secure area!" in d.find_element(By.ID, "flash").text)
                
                logout_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.button[href='/logout']")))
                logout_btn.click()
                wait.until(EC.url_contains("/login"))
            else:
                user_field.send_keys("tomsmith")
                pass_field.send_keys("WrongPassword!")
                btn.click()
                
                flash = wait.until(EC.presence_of_element_located((By.ID, "flash")))
                wait.until(lambda d: "invalid" in d.find_element(By.ID, "flash").text)
                
            passes += 1
            print(f"Loop {i+1} (Correct={i%2==0}): PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1} (Correct={i%2==0}): FAIL in {time.time()-start:.3f}s - {type(e).__name__}")
        total_time += time.time() - start
        
    driver.quit()
    print(f"Summary Login: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

def test_js_alerts():
    print("\n--- Testing JavaScript Alerts ---")
    driver = get_driver()
    wait = WebDriverWait(driver, 5)
    
    passes, fails = 0, 0
    total_time = 0
    
    for i in range(10):
        start = time.time()
        try:
            driver.get("about:blank")
            driver.get("https://the-internet.herokuapp.com/javascript_alerts")
            
            # Alert
            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='Click for JS Alert']"))).click()
            alert = wait.until(EC.alert_is_present())
            alert.accept()
            wait.until(lambda d: "You successfully clicked an alert" in d.find_element(By.ID, "result").text)
            
            # Confirm
            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='Click for JS Confirm']"))).click()
            alert = wait.until(EC.alert_is_present())
            alert.dismiss()
            wait.until(lambda d: "You clicked: Cancel" in d.find_element(By.ID, "result").text)
            
            # Prompt
            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='Click for JS Prompt']"))).click()
            alert = wait.until(EC.alert_is_present())
            alert.send_keys("Hello World")
            alert.accept()
            wait.until(lambda d: "You entered: Hello World" in d.find_element(By.ID, "result").text)
            
            passes += 1
            print(f"Loop {i+1}: PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1}: FAIL in {time.time()-start:.3f}s - {type(e).__name__}")
        total_time += time.time() - start
        
    driver.quit()
    print(f"Summary JS Alerts: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

def test_checkboxes():
    print("\n--- Testing Checkboxes ---")
    driver = get_driver()
    wait = WebDriverWait(driver, 5)
    
    passes, fails = 0, 0
    total_time = 0
    
    for i in range(10):
        start = time.time()
        try:
            driver.get("about:blank")
            driver.get("https://the-internet.herokuapp.com/checkboxes")
            
            wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='checkbox']")))
            
            for idx in [0, 1]:
                box = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")[idx]
                before = box.is_selected()
                
                box.click()
                time.sleep(0.05)
                
                box = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")[idx]
                after = box.is_selected()
                
                if after == before:
                    raise ValueError(f"Checkbox {idx+1} state did not flip! Before: {before}, After: {after}")
            
            passes += 1
            print(f"Loop {i+1}: PASS in {time.time()-start:.3f}s")
        except Exception as e:
            fails += 1
            print(f"Loop {i+1}: FAIL in {time.time()-start:.3f}s - {e}")
        total_time += time.time() - start
        
    driver.quit()
    print(f"Summary Checkboxes: {passes} PASS, {fails} FAIL. Avg time: {total_time/10:.3f}s")

if __name__ == "__main__":
    test_login()
    test_js_alerts()
    test_checkboxes()
