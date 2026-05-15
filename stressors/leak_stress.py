#!/usr/bin/env python3
"""
Memory Leak Simulator — allocates RAM in small chunks and never frees them,
causing RSS to grow steadily over time without ever dropping back.

Unlike the Memory Hog stressor (allocate → hold → release → repeat), this
one simulates a real application memory leak: memory is acquired but the
references are kept forever.  Watch the Memory sparkline trend upward
continuously — the classic diagnostic signature of a leak.

Safety: hard cap at 512 MB total, pauses if system free RAM < 300 MB,
        releases everything immediately on stop.
"""
import os, sys, time, signal, threading
import psutil

CHUNK_MB  = 8     # how much to allocate each step
MAX_MB    = 512   # absolute ceiling
STEP_SECS = 3     # seconds between allocations

stop_event = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
signal.signal(signal.SIGINT,  lambda *_: stop_event.set())

print(f"[leak] PID {os.getpid()} — simulating a memory leak  (Ctrl+C to stop)")
print(f"[leak] +{CHUNK_MB} MB every {STEP_SECS}s up to {MAX_MB} MB cap")

leaked    = []   # intentionally never cleared until stop
total_mb  = 0

try:
    while not stop_event.is_set() and total_mb < MAX_MB:
        free_mb = psutil.virtual_memory().available // (1024 * 1024)
        if free_mb < 300:
            print(f"[leak] Low RAM ({free_mb} MB free) — pausing allocation")
            stop_event.wait(10)
            continue

        chunk = bytearray(CHUNK_MB * 1024 * 1024)
        # Touch every 4 KB page so the OS commits physical frames now
        for i in range(0, len(chunk), 4096):
            chunk[i] = 0xAB
        leaked.append(chunk)
        total_mb += CHUNK_MB
        print(f"[leak] Holding {total_mb} MB  ({free_mb} MB system RAM free)")
        stop_event.wait(STEP_SECS)

    if total_mb >= MAX_MB:
        print(f"[leak] Cap reached ({MAX_MB} MB). Holding until stopped.")
        stop_event.wait()

except KeyboardInterrupt:
    pass

total = sum(len(c) for c in leaked)
print(f"\n[leak] Releasing {total // (1024*1024)} MB and stopping.")
leaked.clear()
print("[leak] stopped.")
