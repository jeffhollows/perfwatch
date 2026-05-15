#!/usr/bin/env python3
"""
Thread Stressor — spawns N threads that do light work, showing high thread count
and elevated involuntary context switches.

Safety: capped at 150 threads, each thread sleeps between bursts.
"""
import os, time, signal, sys, threading, random

NUM_THREADS = int(sys.argv[1]) if len(sys.argv) > 1 else 100
NUM_THREADS = min(NUM_THREADS, 150)

print(f"[thread_stress] PID {os.getpid()} — spawning {NUM_THREADS} threads  (Ctrl+C to stop)")

stop_event = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

def worker(tid):
    while not stop_event.is_set():
        # Light computation burst
        acc = 0
        for i in range(10_000):
            acc += i * i
        # Random sleep 10–50ms so threads contend on the CPU scheduler
        time.sleep(random.uniform(0.01, 0.05))

threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(NUM_THREADS)]
for t in threads:
    t.start()

print(f"[thread_stress] All {NUM_THREADS} threads running. Watch context switches in PerfWatch.")

try:
    while not stop_event.is_set():
        alive = sum(1 for t in threads if t.is_alive())
        print(f"[thread_stress] {alive} threads alive", end='\r')
        time.sleep(2)
except KeyboardInterrupt:
    pass

print("\n[thread_stress] Stopping...")
stop_event.set()
for t in threads:
    t.join(timeout=2)
print("[thread_stress] stopped.")
