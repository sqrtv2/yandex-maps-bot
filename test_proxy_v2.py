#!/usr/bin/env python3
"""Test different proxy approaches with undetected_chromedriver."""
import os, sys, json, tempfile, zipfile, time, threading

os.environ['YANDEX_BOT_BROWSER_HEADLESS'] = 'false'

import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options

PROXY = {
    'host': 'mproxy.site',
    'port': 12138,
    'username': 'Hes9yF',
    'password': 'zAU2vaEUf4TU',
}

def check_ip(driver) -> str:
    """Navigate to yandex.ru/internet and return IP info."""
    driver.get("https://yandex.ru/internet/")
    time.sleep(5)
    body = driver.find_element("tag name", "body").text
    print(f"📄 Page:\n{body[:400]}")
    print(f"📍 URL: {driver.current_url}")
    return body


def test_mv3_extension():
    """Test with Manifest V3 service worker."""
    print("\n" + "="*60)
    print("TEST: Manifest V3 proxy extension")
    print("="*60)
    
    ext_dir = tempfile.mkdtemp(prefix='proxy_mv3_')
    
    manifest = {
        "manifest_version": 3,
        "name": "Proxy Auth MV3",
        "version": "1.0",
        "permissions": ["proxy", "webRequest", "webRequestAuthProvider"],
        "host_permissions": ["<all_urls>"],
        "background": {"service_worker": "background.js"}
    }

    bg_js = """
chrome.proxy.settings.set({
    value: {
        mode: "fixed_servers",
        rules: {
            singleProxy: {
                scheme: "http",
                host: "%s",
                port: %s
            },
            bypassList: ["localhost", "127.0.0.1"]
        }
    },
    scope: "regular"
});

chrome.webRequest.onAuthRequired.addListener(
    (details, callback) => {
        callback({
            authCredentials: {
                username: "%s",
                password: "%s"
            }
        });
    },
    {urls: ["<all_urls>"]},
    ["asyncBlocking"]
);
""" % (PROXY['host'], PROXY['port'], PROXY['username'], PROXY['password'])

    with open(os.path.join(ext_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f)
    with open(os.path.join(ext_dir, 'background.js'), 'w') as f:
        f.write(bg_js)
    
    print(f"📁 MV3 extension: {ext_dir}")
    
    options = Options()
    options.add_argument(f"--load-extension={ext_dir}")
    options.add_argument("--no-sandbox")
    
    driver = uc.Chrome(options=options, version_main=144)
    time.sleep(3)
    check_ip(driver)
    driver.quit()


def test_local_proxy_forwarder():
    """Test using a local HTTP proxy that forwards to remote with auth."""
    print("\n" + "="*60)
    print("TEST: Local proxy forwarder + --proxy-server")
    print("="*60)
    
    import http.server
    import urllib.request
    import socketserver
    import base64
    import socket
    import select
    
    LOCAL_PORT = 18901
    REMOTE_HOST = PROXY['host']
    REMOTE_PORT = PROXY['port']
    auth_str = base64.b64encode(f"{PROXY['username']}:{PROXY['password']}".encode()).decode()
    
    class ProxyHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Suppress logs
        
        def do_CONNECT(self):
            """Handle HTTPS CONNECT tunnel through remote proxy."""
            try:
                # Connect to remote proxy
                remote = socket.create_connection((REMOTE_HOST, REMOTE_PORT), timeout=30)
                
                # Send CONNECT to remote proxy with auth
                connect_req = (
                    f"CONNECT {self.path} HTTP/1.1\r\n"
                    f"Host: {self.path}\r\n"
                    f"Proxy-Authorization: Basic {auth_str}\r\n"
                    f"\r\n"
                )
                remote.sendall(connect_req.encode())
                
                # Read response from remote proxy
                response = b""
                while b"\r\n\r\n" not in response:
                    chunk = remote.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                
                if b"200" in response.split(b"\r\n")[0]:
                    self.send_response(200, "Connection Established")
                    self.end_headers()
                    
                    # Tunnel data between client and remote
                    self._tunnel(self.connection, remote)
                else:
                    self.send_error(502, f"Remote proxy returned: {response[:100]}")
                    remote.close()
            except Exception as e:
                self.send_error(502, str(e))
        
        def _tunnel(self, client, remote):
            """Bidirectional tunnel."""
            sockets = [client, remote]
            timeout = 60
            try:
                while True:
                    readable, _, err = select.select(sockets, [], sockets, timeout)
                    if err:
                        break
                    if not readable:
                        break
                    for s in readable:
                        data = s.recv(65536)
                        if not data:
                            return
                        if s is client:
                            remote.sendall(data)
                        else:
                            client.sendall(data)
            except:
                pass
            finally:
                remote.close()
        
        def do_GET(self):
            self._proxy_request()
        
        def do_POST(self):
            self._proxy_request()
        
        def _proxy_request(self):
            """Forward HTTP request through remote proxy."""
            try:
                import urllib.parse
                
                remote = socket.create_connection((REMOTE_HOST, REMOTE_PORT), timeout=30)
                
                # Reconstruct request with proxy auth
                path = self.path
                req_line = f"{self.command} {path} HTTP/1.1\r\n"
                headers = f"Proxy-Authorization: Basic {auth_str}\r\n"
                
                for key, val in self.headers.items():
                    if key.lower() != 'proxy-authorization':
                        headers += f"{key}: {val}\r\n"
                
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length) if content_length else b""
                
                remote.sendall((req_line + headers + "\r\n").encode() + body)
                
                # Read response
                response = b""
                while True:
                    chunk = remote.recv(65536)
                    if not chunk:
                        break
                    response += chunk
                    if len(response) > 10*1024*1024:
                        break
                
                self.wfile.write(response)
                remote.close()
            except Exception as e:
                self.send_error(502, str(e))
    
    class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True
    
    server = ThreadedServer(("127.0.0.1", LOCAL_PORT), ProxyHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"🔄 Local proxy forwarder running on localhost:{LOCAL_PORT}")
    
    # Now start Chrome with local proxy
    options = Options()
    options.add_argument(f"--proxy-server=http://127.0.0.1:{LOCAL_PORT}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    driver = uc.Chrome(options=options, version_main=144)
    time.sleep(2)
    check_ip(driver)
    driver.quit()
    server.shutdown()


def test_selenium_wire():
    """Test using seleniumwire (if installed)."""
    print("\n" + "="*60)
    print("TEST: seleniumwire approach")
    print("="*60)
    
    try:
        from seleniumwire import undetected_chromedriver as swuc
        
        wire_options = {
            'proxy': {
                'http': f"http://{PROXY['username']}:{PROXY['password']}@{PROXY['host']}:{PROXY['port']}",
                'https': f"http://{PROXY['username']}:{PROXY['password']}@{PROXY['host']}:{PROXY['port']}",
                'no_proxy': 'localhost,127.0.0.1'
            }
        }
        
        options = Options()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-blink-features=AutomationControlled")
        
        driver = swuc.Chrome(
            options=options,
            seleniumwire_options=wire_options,
            version_main=144
        )
        time.sleep(2)
        check_ip(driver)
        driver.quit()
    except ImportError:
        print("❌ seleniumwire not installed.")
        print("Install: pip install selenium-wire")


if __name__ == "__main__":
    print("Proxy tests:")
    print("1: Manifest V3 extension")
    print("2: Local proxy forwarder")
    print("3: seleniumwire")
    
    choice = sys.argv[1] if len(sys.argv) > 1 else input("Enter 1-3: ").strip()
    
    if choice == "1":
        test_mv3_extension()
    elif choice == "2":
        test_local_proxy_forwarder()
    elif choice == "3":
        test_selenium_wire()
    else:
        test_local_proxy_forwarder()
