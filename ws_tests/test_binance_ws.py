"""Binance WebSocket ticker test — public, no token needed."""
import json, time, threading, websocket

def on_message(ws, message):
    msg = json.loads(message)
    print(f"[Binance WS] BTC-USDT price: {msg.get('c')} (bid={msg.get('b')}, ask={msg.get('a')})")

def on_error(ws, error):
    print(f"[Binance WS] ERROR: {error}")

def on_close(ws, *args):
    print("[Binance WS] Connection closed")

def on_open(ws):
    print("[Binance WS] Connected")

if __name__ == "__main__":
    url = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
    print(f"[Binance WS] Connecting to {url}")
    ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message,
                                on_error=on_error, on_close=on_close)
    t = threading.Thread(target=ws.run_forever, daemon=True)
    t.start()
    time.sleep(10)
    ws.close()
    print("Done.")
