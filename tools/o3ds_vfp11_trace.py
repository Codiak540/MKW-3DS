#!/usr/bin/env python3
"""Single-step instruction trace of the kart step in the worktree's Azahar copy.

Starts the scripted race (tools/o3ds_perf_run.py markers) with the GDB stub on,
waits for the race to load, breaks at --symbol (default: the kart step), then
single-steps --steps instructions recording the PC and r0-r15 before each one.
The trace (build/traces/<label>.bin: 17 little-endian u32 per step) feeds
tools/o3ds_vfp11_model.py, which charges ARM11 and VFP11 pipeline latencies and
a 16 KB L1 model that Azahar's one-cycle-per-instruction time cannot show.

Only the emulator process this script starts is terminated.
"""
import argparse, re, socket, struct, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / 'build/azahar-o3ds'
CONFIG = EMU / 'user/config/qt-config.ini'
SD = EMU / 'user/sdmc/3ds/mkw-3ds'
APP = EMU / 'Azahar.app/Contents/MacOS/azahar'
NM = '/opt/devkitpro/devkitARM/bin/arm-none-eabi-nm'


class Rsp:
    def __init__(self, port):
        for _ in range(240):
            try:
                self.s = socket.create_connection(('127.0.0.1', port), timeout=60)
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise SystemExit('no GDB stub')
        self.buf = b''

    def packet(self):
        while True:
            m = re.search(rb'\$([^#]*)#..', self.buf)
            if m:
                self.buf = self.buf[m.end():]
                self.s.sendall(b'+')
                return m.group(1).decode(errors='replace')
            chunk = self.s.recv(1 << 16)
            if not chunk:
                raise EOFError
            self.buf += chunk

    def send(self, data):
        payload = data.encode()
        self.s.sendall(b'$' + payload + b'#%02x' % (sum(payload) & 0xff))

    def cmd(self, data):
        self.send(data)
        return self.packet()

    def interrupt(self):
        self.s.sendall(b'\x03')
        return self.packet()


def set_stub(enabled):
    text = CONFIG.read_text()
    text = re.sub(r'^use_gdbstub=.*$', f'use_gdbstub={"true" if enabled else "false"}', text, flags=re.M)
    text = re.sub(r'^use_gdbstub\\default=.*$', f'use_gdbstub\\\\default={"false" if enabled else "true"}', text, flags=re.M)
    CONFIG.write_text(text)


def symbol_address(elf, names):
    out = subprocess.run([NM, '-C', str(elf)], capture_output=True, text=True).stdout
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] in 'tTwW' and parts[0].strip():
            table[parts[2]] = int(parts[0], 16)
    for n in names:
        for sym, addr in table.items():
            if sym.startswith(n):
                return sym, addr
    raise SystemExit('symbol not found: ' + ', '.join(names))


def regs(hexdata):
    words = [int.from_bytes(bytes.fromhex(hexdata[i:i + 8]), 'little') for i in range(0, min(len(hexdata), 17 * 8), 8)]
    return words


def own_pids():
    r = subprocess.run(['pgrep', '-f', str(APP)], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--label', default='kartstep')
    p.add_argument('--binary', default=str(ROOT / 'build/mkw-3ds.3dsx'))
    p.add_argument('--elf', default=str(ROOT / 'build/mkw-3ds.elf'))
    p.add_argument('--symbol', action='append', default=[])
    p.add_argument('--steps', type=int, default=200000)
    p.add_argument('--skip-hits', type=int, default=400, help='breakpoint hits to skip (ticks into the race)')
    p.add_argument('--extra', action='append', default=[], help='marker NAME[=CONTENT]')
    p.add_argument('--port', type=int, default=24689)
    a = p.parse_args()
    names = a.symbol or ['Kinoko::Kart::KartObjectManager::calc()', 'Kinoko::Kart::KartObjectManager::calc']
    sym, addr = symbol_address(a.elf, names)
    print(f'breakpoint at {sym} = {addr:#x}')
    out = ROOT / 'build/traces'
    out.mkdir(parents=True, exist_ok=True)
    for _ in range(60):
        if not own_pids():
            break
        time.sleep(1)
    else:
        raise SystemExit("this worktree's emulator is already running")
    (SD / 'runtime.log').unlink(missing_ok=True)
    # A terminated runner leaves its marker files behind; a stale reference marker wins over the others.
    for stale in list(SD.glob('o3ds-kinoko-*.txt')) + [SD / 'sim-hash.txt']:
        stale.unlink(missing_ok=True)
    set_stub(True)
    extra = [x for m in a.extra for x in ('--extra', m)]
    run = subprocess.Popen([sys.executable, str(ROOT / 'tools/o3ds_perf_run.py'), '--mode', 'perf',
                            '--label', a.label + '-run', '--binary', a.binary, '--frames', '100000',
                            '--capture', '0', '--cpu-log', '0', '--timeout', '7200', *extra],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        if run.poll() is not None:
            raise SystemExit('the race runner exited early (emulator already running?)')
        rsp = Rsp(a.port)
        rsp.cmd('?')
        rsp.send('c')
        for _ in range(600):
            log = (SD / 'runtime.log').read_text(errors='replace') if (SD / 'runtime.log').exists() else ''
            if 'engine_class=' in log:
                break
            time.sleep(1)
        else:
            raise SystemExit('race did not load')
        time.sleep(8)
        print('stopped at', rsp.interrupt()[:20])
        print('breakpoint:', rsp.cmd(f'Z0,{addr:x},4'))
        for _ in range(a.skip_hits):
            rsp.cmd('c')
        rsp.cmd(f'z0,{addr:x},4')
        started = time.time()
        steps = 0
        with open(out / f'{a.label}.bin', 'wb') as f:
            for i in range(a.steps):
                r = regs(rsp.cmd('g'))
                if len(r) < 16:
                    print('short register packet', r)
                    break
                f.write(struct.pack('<17I', r[15], *r[:16]))
                reply = rsp.cmd('s')
                steps += 1
                if not reply.startswith(('T', 'S')):
                    print('unexpected stop reply', reply)
                    break
                if i % 20000 == 0 and i:
                    print(f'{i} steps, {i / (time.time() - started):.0f}/s', flush=True)
        print(f'trace: {out / (a.label + ".bin")} ({steps} steps, {time.time() - started:.0f} s)')
        rsp.s.close()
    finally:
        run.terminate()
        try:
            run.wait(timeout=60)
        except subprocess.TimeoutExpired:
            run.kill()
        for sig in ('-TERM', '-KILL'):
            pids = own_pids()
            if not pids:
                break
            subprocess.run(['kill', sig, *map(str, pids)], capture_output=True)
            for _ in range(20):
                if not own_pids():
                    break
                time.sleep(1)
        set_stub(False)
        for item in a.extra:
            (SD / item.partition('=')[0]).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
