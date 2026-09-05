import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

app = QApplication(sys.argv)

def run_gui_test():
    class InterceptorPage(QWebEnginePage):
        navigate_signal = Signal(str)
        
        def __init__(self, parent=None):
            super().__init__(parent)
            self.clicks = 0
            self.types = 0

        def javaScriptConsoleMessage(self, level, msg, line, source):
            if msg.startswith("ANTIGRAVITY_CMD:"):
                cmd_str = msg[len("ANTIGRAVITY_CMD:"):]
                print("INTERCEPTED VIA CONSOLE:", cmd_str)
                if cmd_str.startswith("remote-click:"):
                    self.clicks += 1
                elif cmd_str.startswith("remote-type:"):
                    self.types += 1
            else:
                pass # print(f"JS: {msg}")

    view = QWebEngineView()
    page = InterceptorPage(view)
    view.setPage(page)
    view.resize(800, 600)
    view.show()
    
    html_template = """
    <html>
    <body>
        <input type="checkbox" id="chk1"> checkbox 1<br>
        <button id="alert-btn">Click for JS Alert</button>
        <input type="text" id="username" value="">
    </body>
    <script>
      document.addEventListener('click', function(e) {
          if (e.target.id === 'chk1' || e.target.id === 'alert-btn') {
              console.log("ANTIGRAVITY_CMD:remote-click:" + e.target.id);
          }
      }, true);
  
      document.addEventListener('change', function(e) {
          if (e.target.id === 'username') {
              console.log("ANTIGRAVITY_CMD:remote-type:" + e.target.value);
          }
      }, true);
    </script>
    </html>
    """
    
    def simulate_clicks(ok):
        if not ok: return
        print("\n--- Simulating GUI Interaction ---")
        
        # Trigger ALL events simultaneously!
        page.runJavaScript("""
            var el1 = document.getElementById('chk1');
            el1.click();
            var el2 = document.getElementById('username');
            el2.value = 'hello';
            el2.dispatchEvent(new Event('change', { bubbles: true }));
            var el3 = document.getElementById('alert-btn');
            el3.click();
        """)
        
        QTimer.singleShot(500, app.quit)

    page.loadFinished.connect(simulate_clicks)
    view.setHtml(html_template, QUrl("http://localhost/test.html"))
    
    app.exec()
    
    print("\n--- GUI Interceptor Results ---")
    print(f"Intercepted Clicks: {page.clicks}")
    print(f"Intercepted Types: {page.types}")
    if page.clicks == 2 and page.types == 1:
        print("PASS")
    else:
        print("FAIL")

for i in range(1):
    print(f"\n========== LOOP {i+1} ==========")
    run_gui_test()
