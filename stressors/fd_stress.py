#!/usr/bin/env python3
"""
File Descriptor Stressor — opens and holds many files/pipes to push FD count high.

Safety: caps at 400 FDs (well below typical system limit of 1024+),
        uses /dev/null and pipes — no disk space consumed.
"""
import os, time, signal, sys, resource

TARGET_FDS = int(sys.argv[1]) if len(sys.argv) > 1 else 350
soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
TARGET_FDS = min(TARGET_FDS, soft_limit - 50, 400)  # leave headroom

print(f"[fd_stress] PID {os.getpid()} — opening {TARGET_FDS} file descriptors  (limit={soft_limit})  (Ctrl+C to stop)")

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

fds = []
try:
    # Open TARGET_FDS /dev/null handles
    for i in range(TARGET_FDS):
        fds.append(open('/dev/null', 'rb'))

    print(f"[fd_stress] Holding {len(fds)} open FDs. Check lsof panel in PerfWatch.")

    try:
        while True:
            time.sleep(5)
            print(f"[fd_stress] Still holding {len(fds)} FDs", end='\r')
    except KeyboardInterrupt:
        pass

finally:
    print(f"\n[fd_stress] Closing {len(fds)} FDs...")
    for f in fds:
        f.close()
    print("[fd_stress] stopped.")
