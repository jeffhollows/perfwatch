#!/usr/bin/env python3
"""
CPU Stressor — burns ~80% of ONE core.

Safety: nice=10 (lower priority than normal processes), single-threaded,
        target_pct cap enforced via sleep-based duty cycle.
"""
import os, time, signal, sys, math

TARGET_PCT = float(sys.argv[1]) if len(sys.argv) > 1 else 80.0
os.nice(10)

WORK_MS  = TARGET_PCT / 100.0 * 0.1
SLEEP_MS = 0.1 - WORK_MS

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(math.sqrt(n)) + 1):
        if n % i == 0:
            return False
    return True


def find_primes(limit):
    return [n for n in range(2, limit) if is_prime(n)]


def dot_product(a, b):
    return sum(x * y for x, y in zip(a, b))


def matrix_row_ops(size):
    row_a = list(range(size))
    row_b = list(range(size, size * 2))
    result = 0
    for _ in range(size):
        result += dot_product(row_a, row_b)
    return result


def collatz_steps(n):
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps


def collatz_survey(limit):
    return max(collatz_steps(n) for n in range(1, limit))


def work_cycle():
    find_primes(300)
    matrix_row_ops(40)
    collatz_survey(200)


def run():
    print(f"[cpu_stress] PID {os.getpid()} — targeting {TARGET_PCT:.0f}% on one core  (Ctrl+C to stop)")
    try:
        while True:
            deadline = time.perf_counter() + WORK_MS
            while time.perf_counter() < deadline:
                work_cycle()
            time.sleep(max(SLEEP_MS, 0))
    except KeyboardInterrupt:
        print("\n[cpu_stress] stopped.")


run()
