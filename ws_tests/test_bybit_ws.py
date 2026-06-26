"""Bybit WebSocket ticker test."""
import json, time, threading, websocket

def on_message(ws, message):
    msg = json.loads(message)
    if msg.get("topic", "").startswith("tickers"):
        d = msg["data"]
        print(f"[Bybit WS] BTCUSDT price: {d.get('lastPrice')} (bid={d.get('bid1Price')}, ask={d.get('ask1Price')})")
    elif msg.get("op") == "pong" or msg.get("op") == "subscribe":
        print(f"[Bybit WS] {msg.get('op')}: {str(msg)[:120]}")

def on_error(ws, error):
    print(f"[Bybit WS] ERROR: {error}")

def on_close(ws, *args):
    print("[Bybit WS] Connection closed")

def on_open(ws):
    print("[Bybit WS] Connected — subscribing to BTCUSDT ticker")
    sub = {"op": "subscribe", "args": ["tickers.BTCUSDT"]}
    ws.send(json.dumps(sub))

if __name__ == "__main__":
    url = "wss://stream.bybit.com/v5/public/spot"
    print(f"[Bybit WS] Connecting to {url}")
    ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message,
                                on_error=on_error, on_close=on_close)
    t = threading.Thread(target=ws.run_forever, daemon=True)
    t.start()
    time.sleep(10)
    ws.close()
    print("Done.")
