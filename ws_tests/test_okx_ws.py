"""OKX WebSocket ticker test."""
import json, time, threading, websocket

def on_message(ws, message):
    msg = json.loads(message)
    if msg.get("event") == "subscribe":
        print(f"[OKX WS] Subscribed: {msg}")
    elif msg.get("arg", {}).get("channel") == "tickers" and msg.get("data"):
        d = msg["data"][0]
        print(f"[OKX WS] BTC-USDT price: {d.get('last')} (bid={d.get('bidPx')}, ask={d.get('askPx')})")

def on_error(ws, error):
    print(f"[OKX WS] ERROR: {error}")

def on_close(ws, *args):
    print("[OKX WS] Connection closed")

def on_open(ws):
    print("[OKX WS] Connected — subscribing to BTC-USDT ticker")
    sub = {"op": "subscribe", "args": [{"channel": "tickers", "instId": "BTC-USDT"}]}
    ws.send(json.dumps(sub))

if __name__ == "__main__":
    url = "wss://ws.okx.com:8443/ws/v5/public"
    print(f"[OKX WS] Connecting to {url}")
    ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message,
                                on_error=on_error, on_close=on_close)
    t = threading.Thread(target=ws.run_forever, daemon=True)
    t.start()
    time.sleep(10)
    ws.close()
    print("Done.")
