#!/usr/bin/env python3
"""QA: statistical PC profile of the game in the worktree's Azahar copy.

Enables Azahar's GDB stub in the worktree emulator's config for the duration
of one scripted race (tools/o3ds_perf_run.py --mode perf), waits for the race
to load (runtime.log "race_load_ms"), then repeatedly interrupts the emulated
CPU, reads the PC, LR and thread id, and resumes. Samples are symbolised
against the ELF and aggregated per function (self) and per caller.

Limits: samples follow host time, not emulated time, and the stub stops the
guest at block boundaries, so samples pile onto function entry points and
onto host-side slow paths (HLE file and GPU readback calls). Use it to find
neighbourhoods of work, then measure with the in-game emulated-time profile
(performance.csv). Memory stalls and VFP latency of real hardware are not
represented either way.
"""
import argparse, collections, json, re, socket, subprocess, sys, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# In the main checkout build/azahar-o3ds is the user's own play copy; QA runs use the automation copy.
EMU = ROOT / 'build/azahar-o3ds-auto'
CONFIG = EMU / 'user/config/qt-config.ini'
SD = EMU / 'user/sdmc/3ds/mkw-3ds'
ADDR2LINE = '/opt/devkitpro/devkitARM/bin/arm-none-eabi-addr2line'


class Rsp:
    def __init__(self, port):
        for _ in range(120):
            try:
                self.s = socket.create_connection(('127.0.0.1', port), timeout=30)
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise SystemExit('no GDB stub')
        self.buf = b''

    def _read_packet(self):
        while True:
            m = re.search(rb'\$([^#]*)#..', self.buf)
            if m:
                self.buf = self.buf[m.end():]
                self.s.sendall(b'+')
                return m.group(1).decode(errors='replace')
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk

    def send(self, data):
        payload = data.encode()
        self.s.sendall(b'$' + payload + b'#%02x' % (sum(payload) & 0xff))

    def cmd(self, data):
        self.send(data)
        return self._read_packet()

    def interrupt(self):
        self.s.sendall(b'\x03')
        return self._read_packet()


def set_stub(enabled):
    text = CONFIG.read_text()
    text = re.sub(r'^use_gdbstub=.*$', f'use_gdbstub={"true" if enabled else "false"}', text, flags=re.M)
    text = re.sub(r'^use_gdbstub\\default=.*$', f'use_gdbstub\\\\default={"false" if enabled else "true"}', text, flags=re.M)
    CONFIG.write_text(text)


def le32(hexword):
    return int.from_bytes(bytes.fromhex(hexword), 'little')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--label', default='pcprofile')
    p.add_argument('--binary', default=str(ROOT / 'build/mkw-3ds.3dsx'))
    p.add_argument('--elf', default=str(ROOT / 'build/mkw-3ds.elf'))
    p.add_argument('--samples', type=int, default=3000)
    p.add_argument('--interval', type=float, default=0.004, help='host seconds between samples')
    p.add_argument('--port', type=int, default=24689)
    p.add_argument('--stack', type=int, default=0, help='words of stack to scan for return addresses')
    a = p.parse_args()
    nm = subprocess.run(['/opt/devkitpro/devkitARM/bin/arm-none-eabi-nm', a.elf], capture_output=True, text=True).stdout
    syms = {l.split()[2]: int(l.split()[0], 16) for l in nm.splitlines() if len(l.split()) == 3}
    sections = subprocess.run(['/opt/devkitpro/devkitARM/bin/arm-none-eabi-objdump', '-h', a.elf], capture_output=True, text=True).stdout
    text = next(l.split() for l in sections.splitlines() if ' .text ' in l)
    text_lo = int(text[3], 16); text_hi = text_lo + int(text[2], 16)
    set_stub(True)
    run = subprocess.Popen([sys.executable, str(ROOT / 'tools/o3ds_perf_run.py'), '--mode', 'perf', '--label', a.label,
                            '--binary', a.binary, '--cpu-log', '0', '--capture', '300', '--timeout', '3600'])
    samples = []
    try:
        time.sleep(3)
        rsp = Rsp(a.port)
        rsp.cmd('?')
        rsp.send('c')
        log = SD / 'runtime.log'
        racing = False
        while run.poll() is None and len(samples) < a.samples:
            time.sleep(a.interval)
            if not racing:
                racing = log.exists() and 'race_load_ms' in log.read_text(errors='replace')
                if not racing:
                    continue
            stop = rsp.interrupt()
            thread = re.search(r'thread:([0-9a-fA-F]+)', stop)
            regs = rsp.cmd('g')
            pc, lr = le32(regs[15 * 8:16 * 8]), le32(regs[14 * 8:15 * 8])
            stack = []
            if a.stack:
                sp = le32(regs[13 * 8:14 * 8])
                words = rsp.cmd(f'm{sp:x},{4 * a.stack:x}')
                if len(words) == 8 * a.stack:
                    stack = [w for w in (le32(words[i:i + 8]) for i in range(0, len(words), 8)) if text_lo <= w < text_hi]
            samples.append((thread.group(1) if thread else '?', pc, lr, stack))
            rsp.send('c')
        rsp.s.close()
    finally:
        run.wait()
        set_stub(False)
    out = ROOT / 'build/runs' / a.label
    out.mkdir(parents=True, exist_ok=True)
    addrs = sorted({x for _, pc, lr, st in samples for x in (pc, lr, *st)})
    r = subprocess.run([ADDR2LINE, '-f', '-C', '-e', a.elf], input='\n'.join(f'{x:08x}' for x in addrs),
                       capture_output=True, text=True)
    lines = r.stdout.splitlines()
    names = {addr: lines[2 * i] for i, addr in enumerate(addrs)}
    self_count = collections.Counter(names[pc] for _, pc, _, _ in samples)
    caller = collections.Counter((names[pc], names[lr]) for _, pc, lr, _ in samples)
    threads = collections.Counter(t for t, _, _, _ in samples)
    chains = collections.Counter((names[pc], ' < '.join(dict.fromkeys(names[x] for x in st))[:300]) for _, pc, _, st in samples if st)
    total = len(samples)
    report = {'samples': total, 'threads': threads.most_common(),
              'self': [(n, c, round(100 * c / total, 2)) for n, c in self_count.most_common(80)],
              'pairs': [(f, c_, n) for (f, c_), n in caller.most_common(80)],
              'stacks': [(f, c_, n) for (f, c_), n in chains.most_common(60)]}
    (out / 'pc-profile.json').write_text(json.dumps(report, indent=1) + '\n')
    (out / 'pc-samples.txt').write_text(''.join(f'{t} {pc:08x} {lr:08x} {" ".join(f"{x:08x}" for x in st)}\n' for t, pc, lr, st in samples))
    print(f'{total} samples; threads {threads.most_common()}')
    for n, c, pct in report['self'][:50]:
        print(f'{pct:6.2f}% {c:5d}  {n[:110]}')


if __name__ == '__main__':
    main()
