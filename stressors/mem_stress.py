#!/usr/bin/env python3
"""
Memory Stressor — allocates up to TARGET_MB of RAM, holds it, then releases.

Safety: hard cap of 512 MB, checks available RAM before each allocation,
        will not allocate if system free RAM drops below LOW_WATER_MB.
"""
import os, time, signal, sys
import psutil

TARGET_MB    = int(sys.argv[1]) if len(sys.argv) > 1 else 256
TARGET_MB    = min(TARGET_MB, 512)   # hard cap
LOW_WATER_MB = 300                   # stop allocating if system free RAM drops below this

CHUNK_MB  = 16
HOLD_SECS = 30   # hold at peak before releasing and repeating

print(f"[mem_stress] PID {os.getpid()} — target {TARGET_MB} MB  (hard cap 512 MB)  (Ctrl+C to stop)")

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

def free_mb():
    return psutil.virtual_memory().available // (1024 * 1024)

try:
    while True:
        chunks = []
        allocated = 0
        print(f"[mem_stress] Allocating... ({free_mb()} MB system RAM free)")
        while allocated < TARGET_MB:
            if free_mb() < LOW_WATER_MB:
                print(f"[mem_stress] System RAM low ({free_mb()} MB free) — pausing allocation")
                break
            chunk = bytearray(CHUNK_MB * 1024 * 1024)
            # Touch every page so it's actually committed (not just reserved)
            for i in range(0, len(chunk), 4096):
                chunk[i] = 1
            chunks.append(chunk)
            allocated += CHUNK_MB
            print(f"[mem_stress] Allocated {allocated} / {TARGET_MB} MB", end='\r')

        print(f"\n[mem_stress] Holding {allocated} MB for {HOLD_SECS}s...")
        time.sleep(HOLD_SECS)
        print(f"[mem_stress] Releasing memory...")
        del chunks
        time.sleep(5)
except KeyboardInterrupt:
    print("\n[mem_stress] stopped.")
