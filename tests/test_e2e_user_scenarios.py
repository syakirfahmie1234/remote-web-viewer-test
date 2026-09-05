import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

def test_inputs(driver, wait):
    print("\n--- Testing Inputs ---")
    driver.get("https://the-internet.herokuapp.com/inputs")
    
    for i in range(4):
        start = time.time()
        input_el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='number']")))
        input_el.clear()
        
        num_str = str(1000 + i)
        input_el.send_keys(num_str)
        
        # Read value with get_attribute("value")
        val = input_el.get_attribute("value")
        assert val == num_str, f"Expected {num_str}, got {val}"
        
        # Send Enter
        input_el.send_keys(Keys.ENTER)
        
        # Confirm it's unchanged
        val_after = input_el.get_attribute("value")
        assert val_after == num_str, f"After Enter: Expected {num_str}, got {val_after}"
        
        print(f"Inputs loop {i+1}: PASS in {time.time()-start:.3f}s")

def test_login(driver, wait):
    print("\n--- Testing Login ---")
    
    for i in range(4):
        start = time.time()
        driver.get("https://the-internet.herokuapp.com/login")
        
        user_field = wait.until(EC.presence_of_element_located((By.ID, "username")))
        pass_field = wait.until(EC.presence_of_element_located((By.ID, "password")))
        btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
        
        if i % 2 == 0:
            user_field.send_keys("tomsmith")
            pass_field.send_keys("SuperSecretPassword!")
        else:
            user_field.send_keys("wrong_user")
            pass_field.send_keys("wrong_pass")
            
        # Capture BEFORE submit
        u_val = user_field.get_attribute("value")
        p_val = pass_field.get_attribute("value")
        print(f"Captured before submit: user='{u_val}', pass='{p_val}'")
        
        btn.click()
        
        flash = wait.until(EC.visibility_of_element_located((By.ID, "flash")))
        if i % 2 == 0:
            assert "You logged into a secure area!" in flash.text
            # logout
            logout_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.button[href='/logout']")))
            logout_btn.click()
            wait.until(EC.url_contains("/login"))
        else:
            assert "Your username is invalid!" in flash.text
            
        print(f"Login loop {i+1}: PASS in {time.time()-start:.3f}s")

def test_key_presses(driver, wait):
    print("\n--- Testing Key Presses ---")
    driver.get("https://the-internet.herokuapp.com/key_presses")
    
    keys_to_test = [Keys.ENTER, Keys.SPACE, "A", Keys.TAB]
    expected_results = ["ENTER", "SPACE", "A", "TAB"]
    
    for i in range(4):
        start = time.time()
        input_el = wait.until(EC.presence_of_element_located((By.ID, "target")))
        input_el.click()
        
        # Send one key
        input_el.send_keys(keys_to_test[i])
        
        # Read #result.text, NOT input's value
        result_el = wait.until(EC.presence_of_element_located((By.ID, "result")))
        wait.until(lambda d: expected_results[i] in d.find_element(By.ID, "result").text)
        
        assert expected_results[i] in result_el.text, f"Expected {expected_results[i]} in {result_el.text}"
        print(f"Key Presses loop {i+1}: PASS in {time.time()-start:.3f}s")

if __name__ == "__main__":
    options = Options()
    options.add_argument("--headless=new")
    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 5)
    
    try:
        test_inputs(driver, wait)
        test_login(driver, wait)
        test_key_presses(driver, wait)
    finally:
        driver.quit()
