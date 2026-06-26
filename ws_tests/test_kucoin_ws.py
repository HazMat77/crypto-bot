"""
KuCoin WebSocket ticker test.
KuCoin requires fetching a token via REST first, then connecting to the WS endpoint with that token.
"""
import json, time, threading, requests, websocket

def get_kucoin_ws_token():
    resp = requests.post("https://api.kucoin.com/api/v1/bullet-public", timeout=10)
    data = resp.json()["data"]
    token = data["token"]
    server = data["instanceServers"][0]
    endpoint = server["endpoint"]
    ping_interval = server["pingInterval"] // 1000  # ms → s
    return f"{endpoint}?token={token}", ping_interval

def on_message(ws, message):
    msg = json.loads(message)
    if msg.get("type") == "message" and "data" in msg:
        d = msg["data"]
        price = d.get("price") or d.get("bestAsk")
        print(f"[KuCoin WS] BTC-USDT price: {price}")
    else:
        print(f"[KuCoin WS] {msg.get('type','?')}: {message[:120]}")

def on_error(ws, error):
    print(f"[KuCoin WS] ERROR: {error}")

def on_close(ws, *args):
    print("[KuCoin WS] Connection closed")

def on_open(ws):
    print("[KuCoin WS] Connected — subscribing to BTC-USDT ticker")
    sub = {"id": "1", "type": "subscribe", "topic": "/market/ticker:BTC-USDT", "response": True}
    ws.send(json.dumps(sub))

if __name__ == "__main__":
    url, ping_s = get_kucoin_ws_token()
    print(f"[KuCoin WS] Connecting to {url[:60]}...")
    ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message,
                                on_error=on_error, on_close=on_close)
    t = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": ping_s}, daemon=True)
    t.start()
    time.sleep(15)
    ws.close()
    print("Done.")
