#!/usr/bin/env python3
"""
Syscall Hammer — open/close pipe pairs thousands of times per second,
pushing CPU system (kernel) time high while user time stays near zero.

This exercises the CPU Time Breakdown card in PerfWatch, triggering the
"Kernel-Heavy" warning when sys% dominates. Each os.pipe() + os.close()
pair is 3 real kernel syscalls (pipe2, close, close).

Safety: duty-cycle throttle caps total CPU near 60%.
        At most 2 extra FDs exist at any instant — they are never held.
"""
import os, sys, time, signal, threading

BATCH      = 5000   # syscalls per burst before sleeping
TARGET_CPU = 0.60   # duty cycle — 60% active, 40% sleeping

stop_event = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
signal.signal(signal.SIGINT,  lambda *_: stop_event.set())


def run_batch():
    for _ in range(BATCH):
        r, w = os.pipe()
        os.close(r)
        os.close(w)


def duty_cycle_sleep(elapsed):
    sleep_for = elapsed * (1.0 - TARGET_CPU) / TARGET_CPU
    if sleep_for > 0:
        stop_event.wait(sleep_for)


def run():
    print(f"[syscall] PID {os.getpid()} — hammering syscalls to drive sys% high  (Ctrl+C to stop)")
    cycle = 0
    try:
        while not stop_event.is_set():
            t0 = time.perf_counter()
            run_batch()
            duty_cycle_sleep(time.perf_counter() - t0)
            cycle += 1
            if cycle % 20 == 0:
                print(f"[syscall] {cycle * BATCH:,} syscalls so far", end="\r")
    except KeyboardInterrupt:
        pass
    print(f"\n[syscall] stopped after {cycle} cycles  ({cycle * BATCH:,} syscall pairs).")


run()
