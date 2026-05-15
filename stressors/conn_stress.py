#!/usr/bin/env python3
"""
Connection Stressor — opens many TCP connections to a local echo server,
producing ESTABLISHED and TIME_WAIT sockets for PerfWatch to show.

Safety: loopback only, caps at 200 connections, cleans up on exit.
"""
import os, time, signal, sys, socket, threading, random

NUM_CONNS   = int(sys.argv[1]) if len(sys.argv) > 1 else 150
NUM_CONNS   = min(NUM_CONNS, 200)
SERVER_PORT = 19876

print(f"[conn_stress] PID {os.getpid()} — creating {NUM_CONNS} loopback connections  (Ctrl+C to stop)")

stop_event = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

# Mini echo server running in a background thread
def run_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', SERVER_PORT))
    srv.listen(NUM_CONNS + 10)
    srv.settimeout(1)
    clients = []
    while not stop_event.is_set():
        try:
            c, _ = srv.accept()
            c.settimeout(2)
            clients.append(c)
        except socket.timeout:
            pass
    for c in clients:
        try: c.close()
        except Exception: pass
    srv.close()

server_thread = threading.Thread(target=run_server, daemon=True)
server_thread.start()
time.sleep(0.3)  # let server bind

# Open client connections
client_sockets = []
for i in range(NUM_CONNS):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(('127.0.0.1', SERVER_PORT))
        client_sockets.append(s)
    except Exception as e:
        print(f"[conn_stress] Could not connect #{i}: {e}")
        break

print(f"[conn_stress] {len(client_sockets)} ESTABLISHED connections. Watch the Connections card in PerfWatch.")

# Periodically close/reopen some connections to generate TIME_WAIT
try:
    cycle = 0
    while not stop_event.is_set():
        time.sleep(10)
        cycle += 1
        # Close 20 connections and reopen — generates TIME_WAIT sockets
        to_cycle = min(20, len(client_sockets))
        for _ in range(to_cycle):
            s = client_sockets.pop(0)
            try: s.close()
            except Exception: pass
        time.sleep(0.5)
        for _ in range(to_cycle):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(('127.0.0.1', SERVER_PORT))
                client_sockets.append(s)
            except Exception:
                pass
        print(f"[conn_stress] Cycle {cycle}: {len(client_sockets)} connections, TIME_WAIT sockets may appear")
except KeyboardInterrupt:
    pass

print(f"\n[conn_stress] Closing {len(client_sockets)} connections...")
stop_event.set()
for s in client_sockets:
    try: s.close()
    except Exception: pass
print("[conn_stress] stopped.")
