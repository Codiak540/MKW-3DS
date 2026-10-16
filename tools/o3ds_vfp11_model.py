#!/usr/bin/env python3
"""ARM11 MPCore / VFP11 cost model over a single-stepped instruction trace.

Azahar charges one cycle per guest instruction. This replays a trace from
tools/o3ds_vfp11_trace.py through an in-order, single-issue pipeline model with
the VFP11 latencies of docs/o3ds-kinoko-arm11.md section 2 (FMAC 8, simple
VFP 4, divide/sqrt 19, loads 4, fmstat blocking), a load-use penalty, a bimodal
branch predictor and 16 KB 4-way 32-byte-line L1 instruction and data caches
with a miss cost that defaults to 40 cycles (--fcram-cycles, or --hardware
memtest-result.json from the console). It reports, per function, the
instruction count, the modelled cycles, the ratio R between them, and what the
cycles went to. Use it to rank functions by how far their hardware cost exceeds
the emulator's count, not as an absolute timing.
"""
import argparse, collections, json, re, struct, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBJDUMP = '/opt/devkitpro/devkitARM/bin/arm-none-eabi-objdump'
NM = '/opt/devkitpro/devkitARM/bin/arm-none-eabi-nm'

LAT_FMAC = 8      # fadds/fsubs/fmuls/fmacs... single
LAT_FMACD = 9
LAT_SIMPLE = 4    # fabss/fnegs/fcpys/fcmps/fconsts
LAT_DIV = 19      # fdivs/fsqrts result; DS busy 15
LAT_DIVD = 33
LAT_FLD = 4
LAT_XFER = 4      # fmrs/fmsr/fmrrd/fmdrr
LAT_FMSTAT = 4
LAT_LOAD = 3      # ARM load result available for the instruction 3 cycles later (2 interlock cycles)
LAT_MUL = 3
MISPREDICT = 6

FMAC_S = {'fadds', 'fsubs', 'fmuls', 'fmacs', 'fnmacs', 'fmscs', 'fnmscs', 'fnmuls', 'fconsts', 'fcvtds', 'fcvtsd',
          'fsitos', 'fuitos', 'ftosis', 'ftosizs', 'ftouis', 'ftouizs', 'fsitod', 'fuitod', 'ftosid', 'ftosizd', 'ftouid', 'ftouizd',
          'vadd.f32', 'vsub.f32', 'vmul.f32', 'vmla.f32', 'vmls.f32', 'vnmla.f32', 'vnmls.f32', 'vnmul.f32', 'vcvt.f64.f32',
          'vcvt.f32.f64', 'vcvt.f32.s32', 'vcvt.f32.u32', 'vcvt.s32.f32', 'vcvt.u32.f32', 'vcvtr.s32.f32', 'vcvtr.u32.f32',
          'vcvt.f64.s32', 'vcvt.f64.u32', 'vcvt.s32.f64', 'vcvt.u32.f64', 'vcvtr.s32.f64', 'vcvtr.u32.f64'}
FMAC_D = {'faddd', 'fsubd', 'fmuld', 'fmacd', 'fnmacd', 'fmscd', 'fnmscd', 'fnmuld', 'fconstd', 'vadd.f64', 'vsub.f64', 'vmul.f64',
          'vmla.f64', 'vmls.f64', 'vnmla.f64', 'vnmls.f64', 'vnmul.f64'}
SIMPLE = {'fabss', 'fnegs', 'fcpys', 'fcmps', 'fcmpes', 'fcmpzs', 'fcmpezs', 'fabsd', 'fnegd', 'fcpyd', 'fcmpd', 'fcmped', 'fcmpzd',
          'fcmpezd', 'vabs.f32', 'vneg.f32', 'vmov.f32', 'vcmp.f32', 'vcmpe.f32', 'vabs.f64', 'vneg.f64', 'vmov.f64', 'vcmp.f64',
          'vcmpe.f64'}
DIV_S = {'fdivs', 'fsqrts', 'vdiv.f32', 'vsqrt.f32'}
DIV_D = {'fdivd', 'fsqrtd', 'vdiv.f64', 'vsqrt.f64'}
FLOAD = {'flds', 'fldd', 'vldr', 'fldmias', 'fldmiad', 'vldmia', 'vldmia.32', 'vldmia.64', 'fldmdbs', 'fldmdbd', 'vldmdb', 'vpop'}
FSTORE = {'fsts', 'fstd', 'vstr', 'fstmias', 'fstmiad', 'vstmia', 'vstmia.32', 'vstmia.64', 'fstmdbs', 'fstmdbd', 'vstmdb', 'vpush'}
XFER = {'fmrs', 'fmsr', 'fmrrd', 'fmdrr', 'fmrrs', 'fmsrr', 'vmov', 'fmrx', 'fmxr', 'vmrs', 'vmsr'}
FMSTAT = {'fmstat'}
ALOAD = {'ldr', 'ldrb', 'ldrh', 'ldrsb', 'ldrsh', 'ldrd', 'ldm', 'ldmia', 'ldmib', 'ldmda', 'ldmdb', 'pop', 'ldrex'}
ASTORE = {'str', 'strb', 'strh', 'strd', 'stm', 'stmia', 'stmib', 'stmda', 'stmdb', 'push', 'strex'}
MULS = {'mul', 'mla', 'smull', 'umull', 'smlal', 'umlal', 'smulbb', 'smultb', 'smulbt', 'smultt', 'smlabb', 'smlawb', 'smmul'}
BRANCH = {'b', 'bl', 'bx', 'blx', 'bxj'}

REG_RE = re.compile(r'\b(r\d+|sp|lr|pc|ip|fp|sl|s\d+|d\d+)\b')
RNAME = {'sp': 'r13', 'lr': 'r14', 'pc': 'r15', 'ip': 'r12', 'fp': 'r11', 'sl': 'r10'}


class Cache:
    def __init__(self, size=16384, ways=4, line=32):
        self.sets = size // (ways * line)
        self.ways, self.line = ways, line
        self.data = [collections.OrderedDict() for _ in range(self.sets)]
        self.misses = self.hits = 0

    def access(self, addr):
        tag = addr // self.line
        s = self.data[tag % self.sets]
        if tag in s:
            s.move_to_end(tag)
            self.hits += 1
            return False
        s[tag] = True
        if len(s) > self.ways:
            s.popitem(last=False)
        self.misses += 1
        return True


def load_disasm(elf):
    cache = ROOT / 'build/traces' / (Path(elf).name + '.disasm.json')
    if cache.exists() and cache.stat().st_mtime > Path(elf).stat().st_mtime:
        return {int(k): v for k, v in json.load(cache.open()).items()}
    out = subprocess.run([OBJDUMP, '-d', '--no-show-raw-insn', str(elf)], capture_output=True, text=True).stdout
    table = {}
    for line in out.splitlines():
        m = re.match(r'\s*([0-9a-f]+):\s+(\S+)\s*(.*)$', line)
        if m and not line.endswith(':'):
            table[int(m.group(1), 16)] = [m.group(2), m.group(3).split(';')[0].strip()]
    cache.parent.mkdir(parents=True, exist_ok=True)
    json.dump({str(k): v for k, v in table.items()}, cache.open('w'))
    return table


def load_symbols(elf):
    out = subprocess.run([NM, '-C', '-n', str(elf)], capture_output=True, text=True).stdout
    syms = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] in 'tTwW' and parts[0].strip():
            syms.append((int(parts[0], 16), parts[2]))
    return syms


def symbolize(syms, pc):
    import bisect
    i = bisect.bisect_right([s[0] for s in syms], pc) - 1
    return syms[i][1] if i >= 0 else '?'


def base_mnemonic(mn):
    # strip condition codes and the .w / s suffixes conservatively
    m = mn
    for cond in ('eq', 'ne', 'cs', 'cc', 'mi', 'pl', 'vs', 'vc', 'hi', 'ls', 'ge', 'lt', 'gt', 'le', 'al', 'hs', 'lo'):
        if m.endswith(cond) and len(m) > len(cond) + 1 and m[:-2] in (FMAC_S | FMAC_D | SIMPLE | DIV_S | DIV_D | FLOAD | FSTORE | XFER | FMSTAT | ALOAD | ASTORE | MULS | BRANCH | {'mov', 'add', 'sub', 'cmp', 'tst', 'and', 'orr', 'bic', 'eor', 'lsl', 'lsr', 'asr', 'rsb', 'mvn', 'teq', 'cmn', 'adc', 'sbc', 'ldr', 'str'}):
            return m[:-2]
    return m


def regs_of(operands):
    out = []
    for r in REG_RE.findall(operands):
        out.append(RNAME.get(r, r))
    return out


def effective_address(mn, operands, regs):
    """Address of the first byte an ARM/VFP load or store touches, from the pre-instruction registers."""
    m = re.search(r'\[([^\]]+)\]', operands)
    if m:
        inner = [x.strip() for x in m.group(1).split(',')]
        base = regs.get(RNAME.get(inner[0], inner[0]))
        if base is None:
            return None
        if inner[0] in ('pc', 'r15'):
            base += 8
        off = 0
        if len(inner) >= 2 and inner[1].startswith('#'):
            off = int(inner[1][1:], 0)
        elif len(inner) >= 2:
            neg = inner[1].startswith('-')
            rm = regs.get(RNAME.get(inner[1].lstrip('-'), inner[1].lstrip('-')), 0)
            shift = 0
            if len(inner) >= 3 and inner[2].startswith('lsl'):
                shift = int(inner[2].split('#')[1], 0)
            off = -(rm << shift) if neg else (rm << shift)
        return (base + off) & 0xffffffff
    m = re.match(r'(\w+)!?,\s*\{', operands)  # ldm/stm/fldm/vpush...
    if m:
        base = regs.get(RNAME.get(m.group(1), m.group(1)))
        return base
    if mn in ('push', 'pop', 'vpush', 'vpop'):
        return regs.get('r13')
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('trace')
    p.add_argument('--elf', default=str(ROOT / 'build/mkw-3ds.elf'))
    p.add_argument('--fcram-cycles', type=float, default=40.0)
    p.add_argument('--hardware', help='memtest-result.json from the console (sets the miss cost and VFP latencies)')
    p.add_argument('--top', type=int, default=40)
    p.add_argument('--json', help='write the per-function table here')
    p.add_argument('--warm', action='store_true', help='replay the trace twice and report the second pass (caches and predictor warm)')
    p.add_argument('--until-hits', type=int, default=0, help='stop at the N-th execution of the first PC (whole phases of one kart)')
    a = p.parse_args()
    miss = a.fcram_cycles
    lat_fmac, lat_div = LAT_FMAC, LAT_DIV
    if a.hardware:
        hw = json.load(open(a.hardware))
        if 'chase_ns_8388608' in hw:
            miss = hw['chase_ns_8388608'] * 268.111856e-3
        if 'fmacs_dep_cycles' in hw:
            lat_fmac = max(1.0, hw['fmacs_dep_cycles'])
        if 'fdivs_cycles' in hw:
            lat_div = hw['fdivs_cycles']
    disasm = load_disasm(a.elf)
    syms = load_symbols(a.elf)
    data = Path(a.trace).read_bytes()
    n = len(data) // 68
    steps = [struct.unpack_from('<17I', data, i * 68) for i in range(n)]
    if a.until_hits:
        first = steps[0][0]
        hits = 0
        for i, st in enumerate(steps):
            if st[0] == first:
                hits += 1
                if hits == a.until_hits:
                    steps = steps[:i]
                    break
        n = len(steps)
        print(f'window: {n} instructions up to hit {a.until_hits} of {first:#x}')
    if a.warm:
        steps = steps + steps

    icache, dcache = Cache(), Cache()
    ready = collections.defaultdict(int)    # register -> cycle its value is ready
    cycle = 0                               # issue cycle of the previous instruction
    fmac_free = ds_free = 0
    last_cmp_ready = 0
    predictor = collections.defaultdict(lambda: 2)  # pc -> 2-bit counter, >=2 predicts taken
    per_fn = collections.defaultdict(lambda: collections.Counter())
    unmodelled = collections.Counter()
    for i, st in enumerate(steps):
        if a.warm and i == n:
            per_fn = collections.defaultdict(lambda: collections.Counter())
            unmodelled = collections.Counter()
        pc = st[0]
        regs = {f'r{k}': st[1 + k] for k in range(16)}
        ins = disasm.get(pc)
        fn = symbolize(syms, pc)
        c = per_fn[fn]
        c['instructions'] += 1
        if ins is None:
            unmodelled['no disassembly'] += 1
            cycle += 1
            continue
        mn, ops = ins
        base = base_mnemonic(mn)
        issue = cycle + 1
        srcs = regs_of(ops)
        # Operand readiness (destination is the first register for most forms).
        dst = srcs[0] if srcs else None
        wait_regs = srcs[1:] if dst and base not in ASTORE and base not in FSTORE and base not in ('cmp', 'tst', 'teq', 'cmn') else srcs
        for r in wait_regs:
            if ready[r] > issue:
                c['stall_dependency'] += ready[r] - issue
                issue = ready[r]
        if base in FMAC_S or base in FMAC_D:
            if fmac_free > issue:
                c['stall_fmac_busy'] += fmac_free - issue
                issue = fmac_free
            lat = lat_fmac if base in FMAC_S else LAT_FMACD
            fmac_free = issue + (1 if base in FMAC_S else 2)
            if dst:
                ready[dst] = issue + lat
            c['vfp_ops'] += 1
        elif base in SIMPLE:
            if dst:
                ready[dst] = issue + LAT_SIMPLE
            if base.startswith(('fcmp', 'vcmp')):
                last_cmp_ready = issue + LAT_SIMPLE
            c['vfp_ops'] += 1
        elif base in DIV_S or base in DIV_D:
            if ds_free > issue:
                c['stall_div_busy'] += ds_free - issue
                issue = ds_free
            lat = lat_div if base in DIV_S else LAT_DIVD
            ds_free = issue + (15 if base in DIV_S else 29)
            if dst:
                ready[dst] = issue + lat
            c['vfp_div'] += 1
        elif base in FMSTAT or (base in ('vmrs', 'fmrx') and 'apsr' in ops.lower()):
            if last_cmp_ready > issue:
                c['stall_fmstat'] += last_cmp_ready - issue
                issue = last_cmp_ready
            issue += LAT_FMSTAT - 1  # the ARM side waits for the flags
            c['fmstat'] += 1
        elif base in XFER:
            if dst:
                ready[dst] = issue + LAT_XFER
            c['vfp_xfer'] += 1
        elif base in FLOAD or base in ALOAD:
            addr = effective_address(base, ops, regs)
            if addr is None:
                unmodelled['load address'] += 1
            elif dcache.access(addr):
                c['dcache_misses'] += 1
                issue += miss
            if base in FLOAD:
                for r in srcs[:1] if base in ('flds', 'fldd', 'vldr') else srcs:
                    ready[r] = issue + LAT_FLD
            else:
                for r in srcs[:1] if base.startswith('ldr') else srcs:
                    ready[r] = issue + LAT_LOAD
            c['loads'] += 1
        elif base in FSTORE or base in ASTORE:
            addr = effective_address(base, ops, regs)
            if addr is None:
                unmodelled['store address'] += 1
            elif dcache.access(addr):
                c['dcache_misses'] += 1
                issue += miss * 0.5  # write-allocate line fill overlaps
            c['stores'] += 1
        elif base in MULS:
            if dst:
                ready[dst] = issue + LAT_MUL
        elif base in BRANCH or base.startswith('b') and base[1:3] in ('eq', 'ne', 'cs', 'cc', 'mi', 'pl', 'vs', 'vc', 'hi', 'ls', 'ge', 'lt', 'gt', 'le', 'al', 'hs', 'lo', 'x', 'l'):
            pass
        else:
            if dst and base not in ('cmp', 'tst', 'teq', 'cmn'):
                ready[dst] = issue + 1
        # Instruction fetch
        if icache.access(pc):
            c['icache_misses'] += 1
            issue += miss
        # Branch outcome from the trace: taken when the next PC is not pc + 4
        if i + 1 < len(steps):
            taken = steps[i + 1][0] != pc + 4
            is_branch = base in BRANCH or base[:1] == 'b' or (dst == 'r15') or base in ('pop', 'ldm', 'ldmia', 'ldmfd') and 'pc' in ops
            if is_branch or taken:
                pred = predictor[pc] >= 2
                if pred != taken:
                    c['mispredicts'] += 1
                    issue += MISPREDICT
                elif taken:
                    issue += 1  # taken, predicted: one bubble (BTAC hit)
                predictor[pc] = min(3, predictor[pc] + 1) if taken else max(0, predictor[pc] - 1)
        c['cycles'] += issue - cycle
        cycle = issue
    total_i = sum(c['instructions'] for c in per_fn.values())
    total_c = sum(c['cycles'] for c in per_fn.values())
    rows = sorted(per_fn.items(), key=lambda kv: -kv[1]['cycles'])
    print(f'{total_i} instructions, {total_c:.0f} modelled cycles, R = {total_c / max(1, total_i):.2f} '
          f'(fcram {miss:.0f} cycles, fmac {lat_fmac}, div {lat_div}, {"warm" if a.warm else "cold"} caches); unmodelled: {dict(unmodelled)}')
    print(f'{"function":<70} {"instr":>8} {"cycles":>9} {"R":>5} {"dep":>7} {"fmstat":>6} {"divb":>5} {"dmiss":>6} {"imiss":>6} {"mispr":>6}')
    for fn, c in rows[:a.top]:
        print(f'{fn[:70]:<70} {c["instructions"]:>8} {c["cycles"]:>9.0f} {c["cycles"] / c["instructions"]:>5.2f} '
              f'{c["stall_dependency"]:>7.0f} {c["stall_fmstat"]:>6.0f} {c["stall_div_busy"]:>5.0f} {c["dcache_misses"]:>6} {c["icache_misses"]:>6} {c["mispredicts"]:>6}')
    if a.json:
        json.dump({'instructions': total_i, 'cycles': total_c, 'fcram_cycles': miss,
                   'functions': {fn: dict(c) for fn, c in rows}}, open(a.json, 'w'), indent=1)


if __name__ == '__main__':
    main()
