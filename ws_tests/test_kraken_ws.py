"""Kraken WebSocket ticker test."""
import json, time, threading, websocket

def on_message(ws, message):
    msg = json.loads(message)
    if isinstance(msg, list) and len(msg) >= 4 and msg[2] == "ticker":
        ticker = msg[1]
        print(f"[Kraken WS] XBT/USDT price: {ticker['c'][0]} (bid={ticker['b'][0]}, ask={ticker['a'][0]})")
    elif isinstance(msg, dict):
        print(f"[Kraken WS] {msg.get('event','?')}: {str(msg)[:120]}")

def on_error(ws, error):
    print(f"[Kraken WS] ERROR: {error}")

def on_close(ws, *args):
    print("[Kraken WS] Connection closed")

def on_open(ws):
    print("[Kraken WS] Connected — subscribing to XBT/USDT ticker")
    sub = {"event": "subscribe", "pair": ["XBT/USDT"], "subscription": {"name": "ticker"}}
    ws.send(json.dumps(sub))

if __name__ == "__main__":
    url = "wss://ws.kraken.com"
    print(f"[Kraken WS] Connecting to {url}")
    ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message,
                                on_error=on_error, on_close=on_close)
    t = threading.Thread(target=ws.run_forever, daemon=True)
    t.start()
    time.sleep(10)
    ws.close()
    print("Done.")
