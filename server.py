#!/usr/bin/env python3
"""PerfWatch - Process Performance Dashboard"""

import json
import os
import re
import resource
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

import psutil

PORT = 8765
STATIC_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
STRESSORS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stressors')

STRESSOR_DEFS = {
    'cpu':       {'script': 'cpu_stress.py',       'label': 'CPU Burner',       'desc': 'Burns ~80% of one core (nice=10, low priority)'},
    'mem':       {'script': 'mem_stress.py',       'label': 'Memory Hog',       'desc': 'Allocates & holds 256 MB RAM, then releases'},
    'leak':      {'script': 'leak_stress.py',      'label': 'Memory Leak Sim',  'desc': 'Grows RSS steadily — never frees, simulates a real memory leak'},
    'io':        {'script': 'io_stress.py',        'label': 'Disk I/O',         'desc': 'Cycles 64 MB writes + reads in /tmp'},
    'thread':    {'script': 'thread_stress.py',    'label': 'Thread Storm',     'desc': 'Spawns 100 threads with light CPU work each'},
    'fd':        {'script': 'fd_stress.py',        'label': 'FD Leak Sim',      'desc': 'Holds 350 open file descriptors'},
    'conn':      {'script': 'conn_stress.py',      'label': 'Connection Flood', 'desc': '150 loopback TCP connections with cycling TIME_WAIT'},
    'syscall':   {'script': 'syscall_stress.py',   'label': 'Syscall Hammer',   'desc': 'Drives kernel sys% CPU time high via rapid pipe open/close'},
    'pagefault': {'script': 'pagefault_stress.py', 'label': 'Page Fault Gen',   'desc': '~32K minor page faults per cycle via 128 MB buffer touch'},
    'netflood':  {'script': 'netflood_stress.py',  'label': 'Network Flood',    'desc': 'Streams ~200 MB/s over loopback — shows real net throughput'},
}

_stressor_procs: dict = {}   # name -> subprocess.Popen
_stressor_lock  = threading.Lock()


# ── Stressor management ────────────────────────────────────────────

def get_stressor_status():
    with _stressor_lock:
        result = []
        for name, defn in STRESSOR_DEFS.items():
            proc = _stressor_procs.get(name)
            running = proc is not None and proc.poll() is None
            result.append({
                'name':    name,
                'label':   defn['label'],
                'desc':    defn['desc'],
                'running': running,
                'pid':     proc.pid if running else None,
            })
        return result


def start_stressor(name):
    if name not in STRESSOR_DEFS:
        return {'error': f'Unknown stressor: {name}'}
    with _stressor_lock:
        proc = _stressor_procs.get(name)
        if proc and proc.poll() is None:
            return {'error': f'{name} is already running', 'pid': proc.pid}
        script = os.path.join(STRESSORS_DIR, STRESSOR_DEFS[name]['script'])
        p = subprocess.Popen([sys.executable, script])
        _stressor_procs[name] = p
        return {'started': True, 'name': name, 'pid': p.pid}


def stop_stressor(name):
    if name not in STRESSOR_DEFS:
        return {'error': f'Unknown stressor: {name}'}
    with _stressor_lock:
        proc = _stressor_procs.get(name)
        if not proc or proc.poll() is not None:
            _stressor_procs.pop(name, None)
            return {'error': f'{name} is not running'}
        proc.terminate()
        _stressor_procs.pop(name, None)
        return {'stopped': True, 'name': name}


def stop_all_stressors():
    with _stressor_lock:
        for proc in _stressor_procs.values():
            if proc.poll() is None:
                proc.terminate()
        _stressor_procs.clear()


# ── System-wide rate tracking ──────────────────────────────────────
# Store previous I/O counter snapshots so we can calculate per-second rates.

_prev_disk_io: dict = {}   # device -> (counters, timestamp)
_prev_net_io:  dict = {}   # nic    -> (counters, timestamp)
_io_lock = threading.Lock()

# Filter devices that are not real storage (snap loop mounts, etc.)
def _is_real_disk(name: str) -> bool:
    return not name.startswith('loop') and not name.startswith('ram')


# ── System-wide metrics collector ─────────────────────────────────

def collect_system_metrics():
    metrics = {}

    # CPU — per-core utilization + time breakdown + load average
    try:
        per_core  = psutil.cpu_percent(percpu=True, interval=None)
        times_pct = psutil.cpu_times_percent(interval=None)
        load1, load5, load15 = os.getloadavg()
        cpu_count = psutil.cpu_count(logical=True)
        iowait  = getattr(times_pct, 'iowait',  0.0)
        steal   = getattr(times_pct, 'steal',   0.0)
        softirq = getattr(times_pct, 'softirq', 0.0)

        if steal > 10:
            cpu_sev, cpu_status = 'danger',  'VM CPU Stolen'
        elif iowait > 20:
            cpu_sev, cpu_status = 'warning', 'I/O Wait High'
        elif times_pct.user + times_pct.system > 80:
            cpu_sev, cpu_status = 'warning', 'High Load'
        else:
            cpu_sev, cpu_status = 'good',    'Normal'

        load_ratio = load1 / cpu_count if cpu_count else 0
        load_sev   = 'danger' if load_ratio > 2 else 'warning' if load_ratio > 1 else 'good'

        metrics['system_cpu'] = {
            'per_core': per_core,
            'user':     round(times_pct.user, 1),
            'system':   round(times_pct.system, 1),
            'iowait':   round(iowait, 1),
            'steal':    round(steal, 1),
            'softirq':  round(softirq, 1),
            'idle':     round(times_pct.idle, 1),
            'load1':    round(load1, 2),
            'load5':    round(load5, 2),
            'load15':   round(load15, 2),
            'cpu_count': cpu_count,
            'severity': cpu_sev,
            'status':   cpu_status,
            'load_severity': load_sev,
            'advice':   (
                ['iowait is high — CPUs are idle waiting on disk. Check per-disk stats below.']
                if iowait > 20 else
                [f'Steal time {steal:.0f}% — the hypervisor is taking CPU from this VM. Contact your cloud provider or resize.']
                if steal > 10 else []
            ),
            'label': 'System CPU',
            'description': (
                'user = time running application code. system = time in kernel (I/O, syscalls). '
                'iowait = CPU idle because a process is waiting for disk. '
                'steal = CPU cycles taken by the hypervisor on VMs.'
            ),
        }
    except Exception:
        pass

    # Disk I/O rates — per real device, bytes/s and IOPS
    try:
        raw   = psutil.disk_io_counters(perdisk=True)
        now   = time.time()
        rates = {}
        with _io_lock:
            for dev, c in raw.items():
                if not _is_real_disk(dev):
                    continue
                if dev in _prev_disk_io:
                    prev, pt = _prev_disk_io[dev]
                    dt = now - pt
                    if dt > 0:
                        r_iops = max(c.read_count  - prev.read_count,  0) / dt
                        w_iops = max(c.write_count - prev.write_count, 0) / dt
                        rates[dev] = {
                            'read_bps':   max(c.read_bytes  - prev.read_bytes,  0) / dt,
                            'write_bps':  max(c.write_bytes - prev.write_bytes, 0) / dt,
                            'read_iops':  r_iops,
                            'write_iops': w_iops,
                            'busy_pct':   min(max(getattr(c,'busy_time',0) - getattr(prev,'busy_time',0), 0) / (dt * 10), 100),
                        }
                _prev_disk_io[dev] = (c, now)

        # Severity: any disk >80% busy
        max_busy = max((r['busy_pct'] for r in rates.values()), default=0)
        sev = 'danger' if max_busy > 80 else 'warning' if max_busy > 50 else 'good'
        metrics['disk_rates'] = {
            'disks':    rates,
            'severity': sev,
            'status':   f'{max_busy:.0f}% busy' if rates else 'No data yet',
            'label':    'Disk I/O Rates',
            'description': (
                'Per-device read/write throughput and operations per second, sampled over the last 2 seconds. '
                'Busy % measures how much of each 2-second window the disk spent serving I/O — '
                'above 80% the disk is saturated and requests start queuing.'
            ),
            'advice': (
                [f'Disk busy >80%. I/O is queuing — reads/writes are slowing down.']
                if max_busy > 80 else []
            ),
        }
    except Exception:
        pass

    # Network I/O rates — per interface, bytes/s and packet/s
    try:
        raw   = psutil.net_io_counters(pernic=True)
        now   = time.time()
        rates = {}
        with _io_lock:
            for nic, c in raw.items():
                if nic == 'lo':
                    continue
                if nic in _prev_net_io:
                    prev, pt = _prev_net_io[nic]
                    dt = now - pt
                    if dt > 0:
                        rates[nic] = {
                            'recv_bps':  max(c.bytes_recv   - prev.bytes_recv,   0) / dt,
                            'sent_bps':  max(c.bytes_sent   - prev.bytes_sent,   0) / dt,
                            'recv_pps':  max(c.packets_recv - prev.packets_recv, 0) / dt,
                            'sent_pps':  max(c.packets_sent - prev.packets_sent, 0) / dt,
                            'errin':     c.errin,
                            'errout':    c.errout,
                            'dropin':    c.dropin,
                            'dropout':   c.dropout,
                        }
                _prev_net_io[nic] = (c, now)

        total_errors = sum(r['errin'] + r['errout'] + r['dropin'] + r['dropout'] for r in rates.values())
        sev = 'danger' if total_errors > 1000 else 'warning' if total_errors > 0 else 'good'
        advice = []
        for nic, r in rates.items():
            if r['errin'] + r['dropin'] > 0:
                advice.append(f'{nic}: {r["errin"]} errors in, {r["dropin"]} drops in — possible NIC saturation or bad cable/driver.')
            if r['errout'] + r['dropout'] > 0:
                advice.append(f'{nic}: {r["errout"]} errors out, {r["dropout"]} drops out.')

        metrics['net_rates'] = {
            'nics':     rates,
            'severity': sev,
            'status':   f'{total_errors:,} total errors' if total_errors else 'Normal',
            'label':    'Network I/O',
            'description': (
                'Per-interface throughput and packet rates over the last 2 seconds. '
                'Errors indicate bad frames; drops mean the kernel discarded packets due to '
                'buffer overflow or NIC saturation.'
            ),
            'advice': advice,
        }
    except Exception:
        pass

    return metrics


# ── perf counters (best-effort, requires low perf_event_paranoid) ──

def run_perf_stat(pid: int) -> dict:
    """Run `perf stat` for 1 second and parse results. Returns graceful error if unavailable."""
    try:
        result = subprocess.run(
            ['perf', 'stat', '-p', str(pid), 'sleep', '1'],
            capture_output=True, text=True, timeout=5
        )
        output = result.stderr
        if 'Access to performance monitoring' in output or 'not supported' in output.lower():
            paranoid = _read_perf_paranoid()
            return {
                'available': False,
                'reason':    'blocked',
                'paranoid':  paranoid,
                'fix':       f'sudo sysctl kernel.perf_event_paranoid=1   (currently {paranoid})',
            }

        counters = {}
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or 'seconds' in line:
                continue
            m = re.match(r'^([\d,\.]+)\s+([\w/\-]+)', line)
            if m:
                val_str = m.group(1).replace(',', '')
                name    = m.group(2)
                try:
                    counters[name] = float(val_str)
                except ValueError:
                    pass

        cycles       = counters.get('cycles')
        instructions = counters.get('instructions')
        cache_misses = counters.get('cache-misses')
        cache_refs   = counters.get('cache-references')
        br_misses    = counters.get('branch-misses')
        branches     = counters.get('branches')

        out = {'available': True, 'raw': counters}
        if cycles and instructions and cycles > 0:
            out['ipc'] = round(instructions / cycles, 3)
        if cache_misses is not None and cache_refs:
            out['cache_miss_pct'] = round(cache_misses / cache_refs * 100, 2)
        if br_misses is not None and branches:
            out['branch_miss_pct'] = round(br_misses / branches * 100, 2)
        if cycles:
            out['cycles']       = int(cycles)
        if instructions:
            out['instructions'] = int(instructions)
        return out

    except subprocess.TimeoutExpired:
        return {'available': False, 'reason': 'timeout'}
    except FileNotFoundError:
        return {'available': False, 'reason': 'perf_not_found'}
    except Exception as e:
        return {'available': False, 'reason': str(e)}


def _read_perf_paranoid() -> int:
    try:
        with open('/proc/sys/kernel/perf_event_paranoid') as f:
            return int(f.read().strip())
    except Exception:
        return -99


# ── Resource limit helpers ─────────────────────────────────────────

_RLIMIT_DEFS = [
    ('Open Files',    resource.RLIMIT_NOFILE,  'Max file descriptors (files, sockets, pipes)'),
    ('Processes',     resource.RLIMIT_NPROC,   'Max processes/threads this user can create'),
    ('Locked Memory', resource.RLIMIT_MEMLOCK, 'Max bytes that can be locked in RAM (mlock)'),
    ('Stack Size',    resource.RLIMIT_STACK,   'Max stack size per thread'),
    ('Core Dump',     resource.RLIMIT_CORE,    'Max core dump file size (0 = disabled)'),
    ('CPU Time',      resource.RLIMIT_CPU,     'Max CPU seconds before SIGXCPU is sent'),
    ('File Size',     resource.RLIMIT_FSIZE,   'Max size of any single file this process can write'),
    ('Virtual Mem',   resource.RLIMIT_AS,      'Max virtual address space (RSS + mmap + stack)'),
]


# ── System usernames whose processes are considered "system" processes
SYSTEM_USERS = {
    'root', 'daemon', 'nobody', 'www-data', 'syslog', 'messagebus',
    '_apt', 'uuidd', 'tcpdump', 'landscape', 'pollinate', 'systemd-timesync',
    'systemd-network', 'systemd-resolve', 'avahi', 'colord', 'cups-pk-helper',
    'geoclue', 'gnome-initial-setup', 'pulse', 'rtkit', 'saned', 'usbmux',
    'dnsmasq', 'lightdm', 'kernoops', 'speech-dispatcher',
}


# ── Process list ───────────────────────────────────────────────────

def get_process_list(include_system=True, search=''):
    procs = []
    search_lower = search.lower()
    for proc in psutil.process_iter(['pid', 'name', 'username', 'cmdline', 'status', 'create_time']):
        try:
            info = proc.info
            username = info.get('username') or ''
            name = info.get('name') or ''
            cmdline = info.get('cmdline') or []
            full_cmd = ' '.join(cmdline) if cmdline else name
            cmd_str  = full_cmd[:80]

            if not include_system and username in SYSTEM_USERS:
                continue

            if search_lower:
                if search_lower not in name.lower() and search_lower not in full_cmd.lower():
                    continue

            procs.append({
                'pid':      info['pid'],
                'name':     name,
                'username': username,
                'cmd':      cmd_str,
                'full_cmd': full_cmd,
                'status':   info.get('status', ''),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return sorted(procs, key=lambda p: p['name'].lower())


# ── Metric collectors ──────────────────────────────────────────────

def _read_proc_io(pid):
    """Read raw /proc/<pid>/io — gives logical rchar/wchar not exposed by psutil."""
    try:
        with open(f'/proc/{pid}/io') as f:
            return {k: int(v) for k, v in (line.strip().split(': ') for line in f)}
    except (FileNotFoundError, PermissionError, ValueError):
        return {}


def collect_metrics(pid, proc=None):
    try:
        if proc is None or not proc.is_running():
            proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return None

    metrics = {'pid': pid, 'name': proc.name(), 'timestamp': time.time()}

    with proc.oneshot():
        # CPU
        try:
            metrics['cpu'] = {
                'value': proc.cpu_percent(interval=None),
                'num_cores': psutil.cpu_count(),
                'label': 'CPU Usage',
                'description': (
                    'Percentage of a single CPU core used by this process. '
                    'Values above 100% mean the process is using multiple cores.'
                ),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

        # Memory
        try:
            mem = proc.memory_info()
            metrics['memory'] = {
                'rss': mem.rss,
                'vms': mem.vms,
                'percent': round(proc.memory_percent(), 2),
                'label': 'Memory',
                'description': (
                    'RSS (Resident Set Size) is physical RAM actually held by this process. '
                    'VMS (Virtual Memory Size) includes all mapped memory — shared libs, mmap files — '
                    'and is almost always much larger than what is actually used.'
                ),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

        # Threads
        try:
            metrics['threads'] = {
                'value': proc.num_threads(),
                'label': 'Threads',
                'description': (
                    'Number of OS threads spawned by this process. '
                    'More threads can improve parallelism but also increases '
                    'scheduling overhead and memory usage (each thread needs its own stack).'
                ),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

        # File descriptors
        try:
            fd_count = proc.num_fds()
            fd_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
            metrics['fds'] = {
                'value': fd_count,
                'limit': fd_limit,
                'label': 'Open File Descriptors',
                'description': (
                    'File descriptors are handles to open files, sockets, pipes, and devices. '
                    'Each process has a system limit. Exhausting FDs causes new operations to fail with "Too many open files".'
                ),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            pass

        # I/O counters — combine psutil (physical) with /proc/<pid>/io (logical)
        try:
            io = proc.io_counters()
            logical = _read_proc_io(pid)
            metrics['io'] = {
                # Logical = all bytes through the VFS layer (includes tmpfs, pipes, etc.)
                'logical_read_bytes':  logical.get('rchar', io.read_bytes),
                'logical_write_bytes': logical.get('wchar', io.write_bytes),
                # Physical = bytes that actually hit storage hardware
                'physical_read_bytes':  io.read_bytes,
                'physical_write_bytes': io.write_bytes,
                'read_count':  io.read_count,
                'write_count': io.write_count,
                'label': 'Disk I/O',
                'description': (
                    'Logical bytes include all I/O through the kernel VFS layer — files, pipes, '
                    'and RAM-backed filesystems like tmpfs. '
                    'Physical bytes are what actually reached storage hardware; '
                    'writes to /tmp (tmpfs) or the page cache show 0 here until flushed to disk.'
                ),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
            pass

        # Network connections
        try:
            conns = proc.net_connections()
            by_state = {}
            for c in conns:
                s = c.status or 'NONE'
                by_state[s] = by_state.get(s, 0) + 1
            metrics['connections'] = {
                'total': len(conns),
                'established': by_state.get('ESTABLISHED', 0),
                'time_wait': by_state.get('TIME_WAIT', 0),
                'listen': by_state.get('LISTEN', 0),
                'close_wait': by_state.get('CLOSE_WAIT', 0),
                'by_state': by_state,
                'label': 'Network Connections',
                'description': (
                    'Socket connections grouped by state. '
                    'ESTABLISHED = active sessions. LISTEN = accepting new connections. '
                    'TIME_WAIT = recently closed, waiting for late packets (normal but high numbers can exhaust ports). '
                    'CLOSE_WAIT = remote closed but local has not — often a sign of a bug.'
                ),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

        # Context switches
        try:
            ctx = proc.num_ctx_switches()
            metrics['ctx_switches'] = {
                'voluntary': ctx.voluntary,
                'involuntary': ctx.involuntary,
                'label': 'Context Switches',
                'description': (
                    'Voluntary switches happen when the process yields the CPU (waiting for I/O, sleeping). '
                    'Involuntary switches happen when the OS forcibly preempts the process — '
                    'high involuntary counts indicate CPU competition or time-slice exhaustion.'
                ),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

        # Nice / scheduling priority
        try:
            metrics['priority'] = {
                'nice': proc.nice(),
                'label': 'Scheduling Priority (nice)',
                'description': (
                    'The "nice" value controls CPU scheduling priority. '
                    'Range: -20 (highest priority, gets CPU first) to +19 (lowest priority, yields to others). '
                    'Default is 0. Only root can set negative values.'
                ),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

        # Open files (lsof-style)
        try:
            open_files = proc.open_files()
            metrics['open_files'] = {
                'value': len(open_files),
                'files': [f.path for f in open_files[:10]],
                'label': 'Open Files (lsof)',
                'description': (
                    'Files currently held open by the process. '
                    'A growing count over time indicates a file descriptor leak. '
                    'Use lsof -p <pid> in a terminal for the full list.'
                ),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

    # System memory context (vmstat-style)
    try:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        metrics['system_memory'] = {
            'total': vm.total,
            'available': vm.available,
            'used': vm.used,
            'percent': vm.percent,
            'swap_total': sw.total,
            'swap_used': sw.used,
            'swap_percent': sw.percent,
            'label': 'System Memory (vmstat)',
            'description': (
                'System-wide memory availability. When RAM fills up the OS swaps pages to disk, '
                'slowing all processes by orders of magnitude. '
                'This gives context for whether this process is contributing to system memory pressure.'
            ),
        }
    except Exception:
        pass

    # CPU time breakdown (user vs kernel)
    try:
        ct = proc.cpu_times()
        metrics['cpu_times'] = {
            'user':     round(ct.user, 2),
            'system':   round(ct.system, 2),
            'iowait':   round(getattr(ct, 'iowait', 0.0), 2),
            'label':    'CPU Time Breakdown',
            'description': (
                'Cumulative seconds this process has spent in user mode (your code) vs kernel mode (syscalls). '
                'High system time means the process calls the kernel very frequently — '
                'common causes are many small file reads/writes, frequent memory allocation, or heavy socket I/O.'
            ),
        }
    except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
        pass

    # Page faults — direct indicator of swap pressure on this specific process
    try:
        with open(f'/proc/{pid}/stat') as f:
            fields = f.read().split()
        metrics['page_faults'] = {
            'minor': int(fields[9]),
            'major': int(fields[11]),
            'label': 'Page Faults',
            'description': (
                'Minor faults are served from the page cache in RAM (fast, normal). '
                'Major faults require reading from disk or swap — each one stalls the process for milliseconds. '
                'A rising major fault count directly proves this process is suffering from swap pressure.'
            ),
        }
    except Exception:
        pass

    # Process uptime
    try:
        create_time = proc.create_time()
        metrics['uptime'] = {
            'seconds':     time.time() - create_time,
            'create_time': create_time,
            'label':       'Process Uptime',
            'description': (
                'How long this process has been running. '
                'For a service that should run continuously, a short uptime means it recently crashed or was restarted. '
                'Combine with the memory trend — if RSS grows steadily since start, there may be a memory leak.'
            ),
        }
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass

    # Resource limits (ulimits)
    try:
        limits = []
        for name, rtype, desc in _RLIMIT_DEFS:
            try:
                soft, hard = proc.rlimit(rtype)
                limits.append({'name': name, 'soft': soft, 'hard': hard, 'desc': desc})
            except Exception:
                pass
        if limits:
            metrics['rlimits'] = {
                'limits':      limits,
                'label':       'Resource Limits (ulimits)',
                'description': (
                    'Kernel-enforced caps on what this process can consume. '
                    'Soft limit = current enforced cap (process can raise it up to the hard limit). '
                    'Hard limit = ceiling only root can raise. -1 means unlimited.'
                ),
            }
    except Exception:
        pass

    return metrics


# ── Analysis / advice engine ───────────────────────────────────────

def analyze(metrics):
    out = dict(metrics)  # copy top-level fields

    if 'cpu' in metrics:
        v = metrics['cpu']['value']
        cores = metrics['cpu']['num_cores']
        if v > 90:
            status, severity, advice = 'Critical', 'danger', [
                f'Process is consuming ~{v:.0f}% of a CPU core — nearly maxed out.',
                'This often means a tight loop, blocked syscall, or infinite recursion.',
                'Run `perf top -p <pid>` or `strace -cp <pid>` to find the hot path.',
                'Consider CPU affinity (taskset) to isolate to a dedicated core.',
            ]
        elif v > 70:
            status, severity, advice = 'High', 'warning', [
                f'CPU at {v:.0f}%. Elevated but not yet saturated.',
                'If sustained, check IPC with: `perf stat -p <pid> sleep 5`',
                'Low IPC (<0.5) with high CPU often points to cache misses.',
            ]
        elif v > 40:
            status, severity, advice = 'Moderate', 'info', [
                f'CPU at {v:.0f}%. Normal for compute-active workloads.',
                f'System has {cores} cores — this process is using {v/cores:.0f}% of total capacity.',
            ]
        else:
            status, severity, advice = 'Normal', 'good', [
                f'CPU at {v:.0f}%. Healthy.',
            ]
        out['cpu'] = {**metrics['cpu'], 'status': status, 'severity': severity, 'advice': advice}

    if 'memory' in metrics:
        pct = metrics['memory']['percent']
        rss = metrics['memory']['rss']
        if pct > 25:
            status, severity, advice = 'High', 'danger', [
                f'Using {pct:.1f}% of system RAM ({fmt_bytes(rss)}).',
                'If RSS is growing over time this is a memory leak.',
                'Track growth: `watch -n 5 "cat /proc/<pid>/status | grep VmRSS"`',
                'Profile with: `valgrind --leak-check=full ./program` or `heaptrack`',
            ]
        elif pct > 10:
            status, severity, advice = 'Moderate', 'warning', [
                f'Using {pct:.1f}% of RAM ({fmt_bytes(rss)}). Watch for upward trend.',
            ]
        else:
            status, severity, advice = 'Normal', 'good', [
                f'Using {pct:.1f}% of RAM ({fmt_bytes(rss)}). Healthy.',
            ]
        out['memory'] = {**metrics['memory'], 'status': status, 'severity': severity, 'advice': advice}

    if 'threads' in metrics:
        v = metrics['threads']['value']
        if v > 200:
            status, severity, advice = 'Very High', 'danger', [
                f'{v} threads. Each thread costs ~8MB of stack by default.',
                'This is likely a thread-per-connection design under high load.',
                'Consider async I/O (asyncio, libuv) or a fixed-size thread pool.',
            ]
        elif v > 50:
            status, severity, advice = 'Elevated', 'warning', [
                f'{v} threads. If growing over time, check for thread leaks.',
                'Threads that never exit after their task is done are a common source.',
            ]
        else:
            status, severity, advice = 'Normal', 'good', [f'{v} threads. Healthy.']
        out['threads'] = {**metrics['threads'], 'status': status, 'severity': severity, 'advice': advice}

    if 'fds' in metrics:
        v = metrics['fds']['value']
        limit = metrics['fds']['limit']
        pct = (v / limit * 100) if limit else 0
        if pct > 80:
            status, severity, advice = 'Critical', 'danger', [
                f'{v} FDs open — {pct:.0f}% of the limit ({limit}).',
                'New file/socket opens will fail with EMFILE very soon.',
                'Raise limit: add `<user> soft nofile 65536` to /etc/security/limits.conf',
                'Find the leak: `lsof -p <pid> | sort -k9 | uniq -c | sort -rn | head`',
            ]
        elif pct > 50:
            status, severity, advice = 'Elevated', 'warning', [
                f'{v} FDs open ({pct:.0f}% of limit {limit}). Monitor for growth.',
                '`lsof -p <pid> | awk \'{print $5}\' | sort | uniq -c | sort -rn` shows types.',
            ]
        else:
            status, severity, advice = 'Normal', 'good', [f'{v} FDs open ({pct:.0f}% of limit). Healthy.']
        out['fds'] = {**metrics['fds'], 'status': status, 'severity': severity, 'advice': advice, 'percent_of_limit': round(pct, 1)}

    if 'connections' in metrics:
        c = metrics['connections']
        issues, severity, status = [], 'good', 'Normal'
        if c['close_wait'] > 20:
            issues.append(f"{c['close_wait']} CLOSE_WAIT sockets: the remote closed but this process hasn't. Usually a bug — the process is not calling close() after reading EOF.")
            severity, status = 'danger', 'Critical'
        if c['time_wait'] > 200:
            issues.append(f"{c['time_wait']} TIME_WAIT sockets may exhaust local port range. Tune: sysctl net.ipv4.tcp_fin_timeout=15 and net.ipv4.tcp_tw_reuse=1")
            if severity != 'danger':
                severity, status = 'warning', 'Elevated'
        if c['established'] > 1000:
            issues.append(f"{c['established']} ESTABLISHED connections is very high. Verify connection pool limits and check for leaks.")
            severity, status = 'danger', 'Critical'
        if not issues:
            issues = [f"All {c['total']} connections look healthy."]
        out['connections'] = {**c, 'status': status, 'severity': severity, 'advice': issues}

    if 'ctx_switches' in metrics:
        inv = metrics['ctx_switches']['involuntary']
        if inv > 5000:
            status, severity, advice = 'Critical', 'danger', [
                f'{inv} involuntary context switches: the OS is preempting this process constantly.',
                'System is CPU-oversubscribed. Check load: `uptime` — if load > nCPUs, reduce runnable threads.',
                'Pin process to a core: `taskset -cp 0 <pid>`',
            ]
        elif inv > 1000:
            status, severity, advice = 'Elevated', 'warning', [
                f'{inv} involuntary context switches: moderate CPU competition.',
                'Check system-wide CPU with `vmstat 1 5` — look at the "r" (run queue) column.',
            ]
        else:
            status, severity, advice = 'Normal', 'good', ['Context switch rate is healthy.']
        out['ctx_switches'] = {**metrics['ctx_switches'], 'status': status, 'severity': severity, 'advice': advice}

    if 'priority' in metrics:
        nice = metrics['priority']['nice']
        if nice < 0:
            status, severity, advice = 'High Priority', 'info', [
                f'Nice={nice}: this process gets preferential CPU scheduling.',
                'Negative nice values require root. Useful for latency-sensitive services.',
            ]
        elif nice > 5:
            status, severity, advice = 'Low Priority', 'info', [
                f'Nice={nice}: this process yields to higher-priority work.',
                'Increase priority with `renice -n 0 -p <pid>` if it is too slow.',
            ]
        else:
            status, severity, advice = 'Normal', 'good', [f'Nice={nice}: default scheduling priority.']
        out['priority'] = {**metrics['priority'], 'status': status, 'severity': severity, 'advice': advice}

    if 'system_memory' in metrics:
        sm = metrics['system_memory']
        if sm['swap_percent'] > 50:
            status, severity, advice = 'Swapping Heavily', 'danger', [
                f"System swap is {sm['swap_percent']:.0f}% full. Swap is disk — ~100–1000x slower than RAM.",
                'All processes are degraded. Identify top consumers: `ps aux --sort=-%mem | head -10`',
                'Add RAM or reduce workload. Short-term: `swapoff -a && swapon -a` clears swap.',
            ]
        elif sm['percent'] > 85:
            status, severity, advice = 'Pressure', 'warning', [
                f"RAM is {sm['percent']:.0f}% used. OOM killer may terminate processes.",
                'Check top consumers: `ps aux --sort=-%mem | head`',
            ]
        else:
            status, severity, advice = 'Normal', 'good', [
                f"RAM {sm['percent']:.0f}% used, swap {sm['swap_percent']:.0f}% used. Healthy.",
            ]
        out['system_memory'] = {**sm, 'status': status, 'severity': severity, 'advice': advice}

    if 'open_files' in metrics:
        v = metrics['open_files']['value']
        if v > 200:
            status, severity, advice = 'High', 'warning', [
                f'{v} files open. If growing, this process may have a file descriptor leak.',
                'Run `lsof -p <pid>` to see all open files.',
            ]
        else:
            status, severity, advice = 'Normal', 'good', [f'{v} files open. Normal.']
        out['open_files'] = {**metrics['open_files'], 'status': status, 'severity': severity, 'advice': advice}

    if 'cpu_times' in metrics:
        ct = metrics['cpu_times']
        total = ct['user'] + ct['system']
        sys_pct = (ct['system'] / total * 100) if total > 0 else 0
        if sys_pct > 60:
            status, severity, advice = 'Kernel-Heavy', 'warning', [
                f'{sys_pct:.0f}% of CPU time is in the kernel — very frequent syscall usage.',
                'Common causes: many small reads/writes, frequent socket ops, lots of memory allocation/free.',
                'Profile with: `perf trace -p <pid> sleep 5` to see which syscalls dominate.',
            ]
        else:
            status, severity, advice = 'Normal', 'good', [
                f'User: {ct["user"]:.1f}s  System: {ct["system"]:.1f}s — healthy balance.',
            ]
        out['cpu_times'] = {**ct, 'status': status, 'severity': severity, 'advice': advice, 'sys_pct': round(sys_pct, 1)}

    if 'page_faults' in metrics:
        pf = metrics['page_faults']
        if pf['major'] > 1000:
            status, severity, advice = 'Heavy Swapping', 'danger', [
                f'{pf["major"]:,} major page faults — this process is actively hitting disk/swap.',
                'Each major fault stalls the process for milliseconds while the kernel fetches the page.',
                'Reduce memory pressure: free RAM, reduce process footprint, or add more RAM.',
            ]
        elif pf['major'] > 100:
            status, severity, advice = 'Some Swapping', 'warning', [
                f'{pf["major"]:,} major faults — some swap access is occurring for this process.',
            ]
        else:
            status, severity, advice = 'Normal', 'good', [
                f'Minor: {pf["minor"]:,}  Major: {pf["major"]:,} — no significant swap pressure on this process.',
            ]
        out['page_faults'] = {**pf, 'status': status, 'severity': severity, 'advice': advice}

    if 'uptime' in metrics:
        secs = metrics['uptime']['seconds']
        out['uptime'] = {**metrics['uptime'], 'status': 'Running', 'severity': 'good', 'advice': []}

    if 'rlimits' in metrics:
        rl  = metrics['rlimits']
        issues = []
        for lim in rl['limits']:
            soft = lim['soft']
            if soft == 0 and lim['name'] != 'Core Dump':
                issues.append(f'{lim["name"]} soft limit is 0 — any operation using this resource will fail immediately.')
        sev = 'warning' if issues else 'good'
        out['rlimits'] = {**rl, 'status': 'Restricted' if issues else 'Normal', 'severity': sev, 'advice': issues}

    return out


def fmt_bytes(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1024:
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} PB'


# ── HTTP + SSE request handler ─────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence default access log

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == '/':
            self._serve_file('index.html', 'text/html; charset=utf-8')
        elif path == '/api/processes':
            include_system = qs.get('system', ['1'])[0] != '0'
            search = qs.get('q', [''])[0]
            self._json(get_process_list(include_system=include_system, search=search))
        elif path == '/api/metrics':
            try:
                pid = int(qs.get('pid', ['0'])[0])
            except ValueError:
                self.send_error(400, 'Invalid PID')
                return
            self._sse_stream(pid)
        elif path == '/api/stressors':
            self._json(get_stressor_status())
        elif path == '/api/system':
            self._sse_system()
        elif path == '/api/perf':
            try:
                pid = int(qs.get('pid', ['0'])[0])
            except ValueError:
                self.send_error(400, 'Invalid PID')
                return
            self._json(run_perf_stat(pid))
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except json.JSONDecodeError:
            self.send_error(400, 'Bad JSON')
            return

        if path == '/api/stressors/start':
            self._json(start_stressor(body.get('name', '')))
        elif path == '/api/stressors/stop':
            self._json(stop_stressor(body.get('name', '')))
        else:
            self.send_error(404)

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filename, content_type):
        filepath = os.path.join(STATIC_DIR, filename)
        try:
            with open(filepath, 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse_stream(self, pid):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        # Create one Process object and prime cpu_percent on it.
        # cpu_percent(interval=None) always returns 0.0 on the first call
        # to a new object — reusing the same object gives correct readings.
        try:
            proc = psutil.Process(pid)
            proc.cpu_percent(interval=None)  # prime; discard this 0.0
        except Exception:
            proc = None

        try:
            while True:
                metrics = collect_metrics(pid, proc)
                if metrics is None:
                    payload = json.dumps({'error': 'Process no longer exists', 'pid': pid})
                else:
                    payload = json.dumps(analyze(metrics))
                self.wfile.write(f'data: {payload}\n\n'.encode())
                self.wfile.flush()
                if metrics is None:
                    break
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _sse_system(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        try:
            while True:
                metrics = collect_system_metrics()
                payload = json.dumps(metrics)
                self.wfile.write(f'data: {payload}\n\n'.encode())
                self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == '__main__':
    server = ThreadedHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'PerfWatch  →  http://localhost:{PORT}')
    print('Press Ctrl+C to stop.\n')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stop_all_stressors()
        print('\nStopped.')
