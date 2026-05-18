#!/usr/bin/env python3
"""
Thread Stressor — spawns N threads that do light work, showing high thread count
and elevated involuntary context switches.

Safety: capped at 150 threads, each thread yields 20 ms between bursts.
"""
import os, time, signal, sys, threading, random, math

NUM_THREADS = int(sys.argv[1]) if len(sys.argv) > 1 else 100
NUM_THREADS = min(NUM_THREADS, 150)

stop_event = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop_event.set())


def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(math.sqrt(n)) + 1):
        if n % i == 0:
            return False
    return True


def find_primes(limit):
    return [n for n in range(2, limit) if is_prime(n)]


def collatz_steps(n):
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps


def sort_work(size):
    data = [random.random() for _ in range(size)]
    data.sort()
    return data[0]


def compute_burst(tid):
    # Different threads emphasise different work so the flamegraph branches
    bucket = tid % 3
    if bucket == 0:
        find_primes(200)
    elif bucket == 1:
        max(collatz_steps(n) for n in range(1, 200))
    else:
        sort_work(400)


def worker(tid):
    while not stop_event.is_set():
        compute_burst(tid)
        stop_event.wait(0.02)  # brief yield — prevents all 100 threads from saturating every core


def run():
    print(f"[thread_stress] PID {os.getpid()} — spawning {NUM_THREADS} threads  (Ctrl+C to stop)")

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


run()
