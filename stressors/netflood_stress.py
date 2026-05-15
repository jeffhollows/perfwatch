#!/usr/bin/env python3
"""
Network Flood — streams data at high speed between two loopback sockets,
driving the System Network I/O card in PerfWatch to show real throughput.

The existing Connection stressor opens many sockets but sends no data, so
the net card shows near-zero bandwidth.  This stressor sends 64 KB chunks
in a continuous loop (capped at ~200 MB/s) so recv_bps and sent_bps both
show significant values in the dashboard.

Safety: loopback only (127.0.0.1) — no traffic reaches any real NIC.
        Soft rate cap at 200 MB/s.  All sockets closed cleanly on stop.
"""
import os, sys, time, socket, threading, signal

PORT       = 19878    # distinct from conn_stress.py (19876)
CHUNK      = 65536    # 64 KB per send
MAX_MBS    = 200      # soft throughput cap in MB/s
REPORT_SEC = 5        # status line interval

stop_event = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
signal.signal(signal.SIGINT,  lambda *_: stop_event.set())

print(f"[netflood] PID {os.getpid()} — flooding loopback at up to {MAX_MBS} MB/s  (Ctrl+C to stop)")


def receiver():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(1)
    srv.settimeout(1)
    try:
        while not stop_event.is_set():
            try:
                conn, _ = srv.accept()
                conn.settimeout(1)
                while not stop_event.is_set():
                    try:
                        data = conn.recv(CHUNK * 4)
                        if not data:
                            break
                    except socket.timeout:
                        continue
                try:
                    conn.close()
                except OSError:
                    pass
            except socket.timeout:
                continue
    finally:
        srv.close()


recv_thread = threading.Thread(target=receiver, daemon=True)
recv_thread.start()
time.sleep(0.3)   # let server bind before connecting

sender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sender.connect(("127.0.0.1", PORT))
try:
    sender.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
except OSError:
    pass

payload   = b"\x00" * CHUNK
sent      = 0
t_start   = time.monotonic()
t_report  = t_start

try:
    while not stop_event.is_set():
        sender.sendall(payload)
        sent += len(payload)

        now     = time.monotonic()
        elapsed = now - t_start
        if elapsed > 0 and (sent / elapsed / 1_048_576) > MAX_MBS:
            time.sleep(0.002)   # brief pause to stay under the cap

        if now - t_report >= REPORT_SEC:
            rate = sent / (now - t_start) / 1_048_576
            print(f"[netflood] {sent / 1_048_576:.0f} MB sent  {rate:.0f} MB/s")
            t_report = now

except (KeyboardInterrupt, BrokenPipeError, ConnectionResetError):
    pass
finally:
    stop_event.set()
    try:
        sender.close()
    except OSError:
        pass

total_mb = sent / 1_048_576
print(f"\n[netflood] stopped.  Total sent: {total_mb:.1f} MB")
