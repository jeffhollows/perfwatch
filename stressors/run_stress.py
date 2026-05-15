#!/usr/bin/env python3
"""
PerfWatch Stress Launcher — start/stop individual stressors or all at once.

Usage:
  python3 run_stress.py all           # start all stressors
  python3 run_stress.py cpu mem io    # start specific ones
  python3 run_stress.py list          # show available stressors

Each stressor runs as a separate subprocess so it appears as its own
selectable process in PerfWatch. Press Ctrl+C to stop everything.
"""
import os, sys, subprocess, signal, time

STRESSORS = {
    'cpu':       ('cpu_stress.py',       'Burns ~80% of one CPU core (nice=10, low priority)'),
    'mem':       ('mem_stress.py',       'Allocates 256 MB RAM, holds it, releases, repeats'),
    'leak':      ('leak_stress.py',      'Grows RSS steadily — never frees, simulates a memory leak'),
    'io':        ('io_stress.py',        'Cycles 64 MB writes+reads in /tmp'),
    'thread':    ('thread_stress.py',    'Spawns 100 threads with light CPU work each'),
    'fd':        ('fd_stress.py',        'Holds 350 open file descriptors'),
    'conn':      ('conn_stress.py',      'Opens 150 loopback TCP connections'),
    'syscall':   ('syscall_stress.py',   'Drives kernel sys% CPU high via rapid pipe open/close'),
    'pagefault': ('pagefault_stress.py', '~32K minor page faults per cycle via buffer touch'),
    'netflood':  ('netflood_stress.py',  'Streams ~200 MB/s over loopback to show net throughput'),
}

HERE = os.path.dirname(os.path.abspath(__file__))

def usage():
    print("Usage: python3 run_stress.py [all | list | <name> ...]")
    print("\nAvailable stressors:")
    for name, (_, desc) in STRESSORS.items():
        print(f"  {name:<10} {desc}")
    sys.exit(0)

args = sys.argv[1:]
if not args or 'list' in args:
    usage()

targets = list(STRESSORS.keys()) if 'all' in args else args
unknown = [t for t in targets if t not in STRESSORS]
if unknown:
    print(f"Unknown stressor(s): {', '.join(unknown)}")
    usage()

procs = {}
print("Starting stressors — each will appear as a separate process in PerfWatch.")
print("Open http://localhost:8765 and search for the script name.\n")

for name in targets:
    script, desc = STRESSORS[name]
    path = os.path.join(HERE, script)
    p = subprocess.Popen([sys.executable, path], cwd=HERE)
    procs[name] = p
    print(f"  Started {name:<10} PID {p.pid}  ({desc})")

print(f"\n{len(procs)} stressor(s) running. Press Ctrl+C to stop all.\n")

def stop_all(sig=None, frame=None):
    print("\nStopping all stressors...")
    for name, p in procs.items():
        if p.poll() is None:
            p.terminate()
    # Give them a moment to clean up
    time.sleep(2)
    for name, p in procs.items():
        if p.poll() is None:
            p.kill()
            print(f"  Force-killed {name} (PID {p.pid})")
    print("All stopped.")
    sys.exit(0)

signal.signal(signal.SIGTERM, stop_all)
signal.signal(signal.SIGINT,  stop_all)

try:
    while True:
        for name, p in list(procs.items()):
            if p.poll() is not None:
                print(f"  [{name}] exited with code {p.returncode}")
                del procs[name]
        if not procs:
            print("All stressors have exited.")
            break
        time.sleep(2)
except KeyboardInterrupt:
    stop_all()
