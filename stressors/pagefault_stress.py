#!/usr/bin/env python3
"""
Page Fault Generator — allocates a 128 MB buffer, writes one byte to every
4 KB page, then discards the entire buffer and repeats.

The first write to any page that hasn't been physically mapped triggers a
minor page fault: the kernel allocates a physical frame, installs a page
table entry, and then lets the write proceed.  With 128 MB and 4 KB pages,
each cycle produces ~32,768 minor faults — clearly visible in PerfWatch's
Page Faults card and its sparkline.

Major faults (which require disk/swap reads) do NOT occur here unless the
system is already under severe memory pressure.

Safety: buffer is fully released each cycle — no cumulative memory growth.
        1-second sleep between cycles keeps CPU usage moderate.
"""
import os, sys, time, signal, threading

BUFFER_MB  = 128
PAGE_BYTES = 4096
SLEEP_SECS = 1

stop_event = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
signal.signal(signal.SIGINT,  lambda *_: stop_event.set())

pages = (BUFFER_MB * 1024 * 1024) // PAGE_BYTES

print(f"[pagefault] PID {os.getpid()} — generating minor page faults  (Ctrl+C to stop)")
print(f"[pagefault] ~{pages:,} minor faults per cycle  ({BUFFER_MB} MB buffer, {SLEEP_SECS}s sleep)")

buf_size = BUFFER_MB * 1024 * 1024
cycle    = 0

try:
    while not stop_event.is_set():
        cycle += 1
        # Fresh bytearray: physical pages are not mapped until first write.
        # Writing to offset i forces a minor fault for that 4 KB page.
        buf = bytearray(buf_size)
        for i in range(0, buf_size, PAGE_BYTES):
            buf[i] = cycle & 0xFF
        del buf   # release all pages back to OS; next cycle starts clean
        print(f"[pagefault] Cycle {cycle}: ~{pages:,} minor faults generated  ", end="\r")
        stop_event.wait(SLEEP_SECS)
except KeyboardInterrupt:
    pass

print(f"\n[pagefault] stopped after {cycle} cycles.")
