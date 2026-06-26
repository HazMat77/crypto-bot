"""Gate.io WebSocket ticker test."""
import json, time, threading, websocket

def on_message(ws, message):
    msg = json.loads(message)
    if msg.get("channel") == "spot.tickers" and msg.get("event") == "update":
        d = msg["result"]
        print(f"[Gate.io WS] BTC_USDT price: {d.get('last')} (bid={d.get('highest_bid')}, ask={d.get('lowest_ask')})")
    else:
        print(f"[Gate.io WS] {msg.get('event','?')}: {str(msg)[:120]}")

def on_error(ws, error):
    print(f"[Gate.io WS] ERROR: {error}")

def on_close(ws, *args):
    print("[Gate.io WS] Connection closed")

def on_open(ws):
    print("[Gate.io WS] Connected — subscribing to BTC_USDT ticker")
    sub = {"time": int(time.time()), "channel": "spot.tickers",
           "event": "subscribe", "payload": ["BTC_USDT"]}
    ws.send(json.dumps(sub))

if __name__ == "__main__":
    url = "wss://api.gateio.ws/ws/v4/"
    print(f"[Gate.io WS] Connecting to {url}")
    ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message,
                                on_error=on_error, on_close=on_close)
    t = threading.Thread(target=ws.run_forever, daemon=True)
    t.start()
    time.sleep(10)
    ws.close()
    print("Done.")
