#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Remember running desktop wallpapers and restore them once per KDE login."""
import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import signal
import selectors
import socket
import struct
import subprocess
import time

BINARY = Path('/usr/local/bin/linux-wallpaperengine')
STATE = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'linux-wallpaperengine/last-wallpapers.json'
STOP = False
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def log(message):
    print(message, flush=True)


def screens_in(args):
    screens = []
    for index, arg in enumerate(args[:-1]):
        if arg == '--screen-root':
            screens.append(args[index + 1])
        elif arg == '--screen-span':
            screens.extend(args[index + 1].split(','))
    return sorted(set(screens))


def running():
    records = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            exe = (proc / 'exe').readlink()
            if exe.name != 'linux-wallpaperengine':
                continue
            args = [os.fsdecode(x) for x in (proc / 'cmdline').read_bytes().split(b'\0') if x][1:]
            screens = screens_in(args)
            if not screens:
                continue
            cwd = os.readlink(proc / 'cwd')
            start = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])
            records.append((start, {'screens': screens, 'args': args, 'cwd': cwd}))
        except (OSError, ValueError, IndexError):
            continue
    # In a handover, the newest process for an output represents the selection.
    result = []
    for _, record in sorted(records, key=lambda r: r[0]):
        result = remember(result, [record])
    return result


def remember(saved, active):
    result = list(saved)
    for record in active:
        screens = set(record['screens'])
        result = [old for old in result if not screens.intersection(old['screens'])]
        result.append(record)
    return sorted(result, key=lambda r: r['screens'])


def load_state():
    if not STATE.exists():
        return []
    data = json.loads(STATE.read_text())
    records = data.get('wallpapers', [])
    for record in records:
        if (not isinstance(record.get('args'), list)
                or not all(isinstance(a, str) and '\0' not in a for a in record['args'])
                or record.get('screens') != screens_in(record['args'])
                or not isinstance(record.get('cwd'), str)):
            raise ValueError('Invalid saved wallpaper command')
    return records


def save_state(records):
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = STATE.with_suffix('.tmp')
    with tmp.open('w') as stream:
        os.chmod(tmp, 0o600)
        json.dump({'version': 1, 'wallpapers': records}, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(STATE)


def connected():
    try:
        result = subprocess.run(['kscreen-doctor', '-j'], check=True, capture_output=True, text=True, timeout=5)
        data = json.loads(result.stdout)
        return {o['name'] for o in data.get('outputs', []) if o.get('connected') and o.get('enabled')}
    except (OSError, ValueError, subprocess.SubprocessError):
        return set()


def spawn(record):
    # Use the maintained symlink, and argv directly: never execute a shell command.
    cwd = record['cwd'] if Path(record['cwd']).is_dir() else str(BINARY.resolve().parent)
    return subprocess.Popen([str(BINARY), *record['args']], cwd=cwd, stdin=subprocess.DEVNULL,
                            start_new_session=True)


class ConfigEvents:
    """Linux inotify: sleep until relevant files change, including atomic replaces."""
    MASK = 0x8 | 0x80 | 0x100 | 0x400 | 0x800  # close-write, moved-to, create, delete/move-self

    def __init__(self, root=CONFIG):
        self.root = root
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.inotify_init1.argtypes = [ctypes.c_int]
        self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "Cannot initialize wallpaper configuration notifications")
        self.watches = {}
        try:
            self.refresh()
        except BaseException:
            os.close(self.fd)
            raise

    def refresh(self):
        for path in [self.root, self.root / 'Linux Wallpaper Engine']:
            if path.is_dir():
                wd = self.libc.inotify_add_watch(self.fd, os.fsencode(path), self.MASK)
                if wd < 0:
                    raise OSError(ctypes.get_errno(), f"Cannot watch {path}")
                self.watches[wd] = path

    def drain(self):
        changed = False
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            offset = 0
            while offset < len(data):
                wd, mask, _cookie, length = struct.unpack_from('iIII', data, offset)
                name = os.fsdecode(data[offset + 16:offset + 16 + length].split(b'\0')[0])
                offset += 16 + length
                parent = self.watches.get(wd)
                if mask & (0x4000 | 0x8000):  # queue overflow / removed watch
                    changed = True
                    if mask & 0x8000:
                        self.watches.pop(wd, None)
                if parent == self.root:
                    if name in ('Linux Wallpaper Engine', 'plasma-org.kde.plasma.desktop-appletsrc'):
                        changed = True
                elif parent == self.root / 'Linux Wallpaper Engine':
                    if name in ('active-wallpapers.json', 'settings.json', 'wallpaper-overrides.json'):
                        changed = True
        self.refresh()
        return changed

    def close(self):
        os.close(self.fd)


def capture(saved, force=False):
    active = running()
    updated = remember(saved, active)
    if updated and (force or updated != saved):
        save_state(updated)
        log('Saved wallpaper selection: ' + ', '.join(s for r in updated for s in r['screens']))
    return updated


def restore_pending(saved, handled, attempts, children):
    pending = [r for r in saved if not set(r['screens']).intersection(handled)
               and attempts.get(tuple(r['screens']), 0) < 3]
    if not pending:
        return False
    outputs = connected()
    for record in pending:
        group = set(record['screens'])
        key = tuple(record['screens'])
        if not group.issubset(outputs):
            continue
        if any(group.intersection(r['screens']) for r in running()):
            handled.update(group)
            continue
        attempts[key] = attempts.get(key, 0) + 1
        try:
            child = spawn(record)
            children.append(child)
            time.sleep(0.5)
            if child.poll() is None:
                handled.update(group)
                log('Restored wallpaper on ' + ', '.join(record['screens']))
            else:
                log(f'Renderer exited during startup on {key}: {child.returncode}')
        except OSError as error:
            log(f'Could not restore {key}: {error}')
    return any(not set(r['screens']).intersection(handled)
               and attempts.get(tuple(r['screens']), 0) < 3 for r in saved)


def monitor(saved):
    global STOP
    STOP = False
    events = ConfigEvents()
    wake_read, wake_write = socket.socketpair()
    wake_read.setblocking(False)
    wake_write.setblocking(False)
    previous_fd = signal.set_wakeup_fd(wake_write.fileno())
    previous_handlers = {}
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1, signal.SIGCHLD):
        previous_handlers[signum] = signal.signal(signum, stop if signum in (signal.SIGTERM, signal.SIGINT) else lambda *_: None)
    selector = selectors.DefaultSelector()
    selector.register(events.fd, selectors.EVENT_READ, 'config')
    selector.register(wake_read, selectors.EVENT_READ, 'signal')
    try:
        saved = capture(saved)
        handled = {s for r in running() for s in r['screens']}
        if handled:
            log('Already running; no duplicate launch: ' + ', '.join(sorted(handled)))
        deadline = time.monotonic() + 90
        retry = time.monotonic()
        debounce = None
        attempts, children = {}, []
        log('Event-driven wallpaper tracking started (no periodic process scans).')
        while not STOP:
            now = time.monotonic()
            if retry is not None and now >= retry:
                pending = restore_pending(saved, handled, attempts, children)
                retry = now + 10 if pending and now + 10 < deadline else None
            if debounce is not None and now >= debounce:
                saved = capture(saved)
                debounce = None
            deadlines = [t for t in (retry, debounce) if t is not None]
            timeout = max(0, min(deadlines) - time.monotonic()) if deadlines else None
            if STOP:
                break
            for key, _ in selector.select(timeout):
                if key.data == 'config':
                    if events.drain():
                        debounce = time.monotonic() + 0.5
                else:
                    signals = wake_read.recv(4096)
                    if signal.SIGUSR1 in signals:
                        debounce = time.monotonic()
                    children = [p for p in children if p.poll() is None]
        # KillMode=mixed sends TERM to us first, so renderer children still exist
        # during this final snapshot. Empty process lists never clear saved choices.
        capture(saved, force=True)
        log('Final wallpaper selection saved for next login.')
    finally:
        selector.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        signal.set_wakeup_fd(previous_fd)
        wake_read.close()
        wake_write.close()
        events.close()


def main():
    global BINARY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=BINARY, help='Renderer executable or symlink')
    parser.add_argument('--snapshot', action='store_true', help='Save running wallpaper commands and exit')
    parser.add_argument('--dry-run', action='store_true', help='Show saved/current commands without changing anything')
    args = parser.parse_args()
    BINARY = args.binary.absolute()
    saved = load_state()
    if args.dry_run:
        print(json.dumps({'state': str(STATE), 'saved': saved, 'running': running()}, indent=2))
        return
    lock_path = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid()))) / 'linux-wallpaperengine-restore.lock'
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('Wallpaper tracking is already running; existing state retained.')
            return
        if args.snapshot:
            capture(saved)
            return
        if not BINARY.is_file():
            raise FileNotFoundError(f'Renderer not found: {BINARY}')
        monitor(saved)


def stop(_signum, _frame):
    global STOP
    STOP = True


if __name__ == '__main__':
    main()
