"""MEXC WebSocket ticker test."""
import json, time, threading, websocket, sys
# Force UTF-8 so MEXC's Chinese subscription response doesn't crash the console
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def on_message(ws, message):
    msg = json.loads(message)
    if "d" in msg and "deals" in msg.get("d", {}):
        deals = msg["d"]["deals"]
        if deals:
            p = deals[0].get("p")
            print(f"[MEXC WS] BTCUSDT trade price: {p}")
    else:
        print(f"[MEXC WS] msg: {str(msg)[:150]}")

def on_error(ws, error):
    print(f"[MEXC WS] ERROR: {error}")

def on_close(ws, *args):
    print("[MEXC WS] Connection closed")

def on_open(ws):
    print("[MEXC WS] Connected — subscribing to BTCUSDT mini ticker")
    sub = {"method": "SUBSCRIPTION", "params": ["spot@public.deals.v3.api@BTCUSDT"]}
    ws.send(json.dumps(sub))

if __name__ == "__main__":
    url = "wss://wbs.mexc.com/ws"
    print(f"[MEXC WS] Connecting to {url}")
    ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message,
                                on_error=on_error, on_close=on_close)
    t = threading.Thread(target=ws.run_forever, daemon=True)
    t.start()
    time.sleep(10)
    ws.close()
    print("Done.")
