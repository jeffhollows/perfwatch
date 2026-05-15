#!/usr/bin/env python3
"""
I/O Stressor — repeatedly writes and reads a temp file in /tmp.

Safety: max file size 128 MB, writes in small chunks with brief sleeps
        so the disk is stressed but not monopolised.
"""
import os, time, signal, sys, tempfile

FILE_MB   = int(sys.argv[1]) if len(sys.argv) > 1 else 64
FILE_MB   = min(FILE_MB, 128)
CHUNK     = 1024 * 256   # 256 KB per write
INTERVAL  = 0.02          # seconds between chunks (throttle)

print(f"[io_stress] PID {os.getpid()} — cycling {FILE_MB} MB I/O in /tmp  (Ctrl+C to stop)")

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

tmpfile = tempfile.mktemp(prefix='perfwatch_io_', suffix='.dat', dir='/tmp')
payload = os.urandom(CHUNK)

try:
    cycle = 0
    while True:
        cycle += 1
        written = 0
        target = FILE_MB * 1024 * 1024

        # Write phase
        with open(tmpfile, 'wb') as f:
            while written < target:
                f.write(payload)
                written += len(payload)
                time.sleep(INTERVAL)

        # Read phase
        read = 0
        with open(tmpfile, 'rb') as f:
            while True:
                buf = f.read(CHUNK)
                if not buf:
                    break
                read += len(buf)
                time.sleep(INTERVAL)

        print(f"[io_stress] Cycle {cycle}: wrote {written//1024//1024} MB, read {read//1024//1024} MB")
        time.sleep(1)

except KeyboardInterrupt:
    print("\n[io_stress] stopped.")
finally:
    try:
        os.unlink(tmpfile)
    except FileNotFoundError:
        pass
