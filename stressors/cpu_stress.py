#!/usr/bin/env python3
"""
CPU Stressor — burns ~80% of ONE core.

Safety: nice=10 (lower priority than normal processes), single-threaded,
        target_pct cap enforced via sleep-based duty cycle.
"""
import os, time, signal, sys

TARGET_PCT = float(sys.argv[1]) if len(sys.argv) > 1 else 80.0
os.nice(10)  # yield to normal-priority processes

print(f"[cpu_stress] PID {os.getpid()} — targeting {TARGET_PCT:.0f}% on one core  (Ctrl+C to stop)")

WORK_MS  = TARGET_PCT / 100.0 * 0.1   # seconds burning per 100ms cycle
SLEEP_MS = 0.1 - WORK_MS              # seconds sleeping per 100ms cycle

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

try:
    while True:
        deadline = time.perf_counter() + WORK_MS
        # Busy loop for WORK_MS
        while time.perf_counter() < deadline:
            _ = sum(i * i for i in range(500))
        time.sleep(max(SLEEP_MS, 0))
except KeyboardInterrupt:
    print("\n[cpu_stress] stopped.")
