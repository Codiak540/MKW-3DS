#!/usr/bin/env python3
"""Generate vendor/kinoko/source/game/SimHashFields.cc: field-by-field hashing of
Kinoko's simulation state classes, from their headers.

Every non-pointer data member of each listed class is hashed by value (floats as
their 4 bytes, so -0.0, NaN payloads and denormals all count). Pointers,
references, spans and containers are skipped and listed in the report, so the
coverage is explicit. The generated functions are static members of
Kinoko::SimHash, which the headers befriend (--apply inserts the friend lines).

Usage: gen_sim_hash.py [--apply]   (run from anywhere; paths are repo-relative)
"""
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIN = ROOT / 'vendor/kinoko/source'
OUT = KIN / 'game/SimHashFields.cc'

# (header, [classes]) in hashing order. Nested structs are found automatically.
CLASSES = [
    ('game/kart/KartDynamics.hh', ['KartDynamics']),
    ('game/kart/KartPhysics.hh', ['KartPhysics']),
    ('game/kart/CollisionGroup.hh', ['CollisionData', 'Hitbox', 'CollisionGroup']),
    ('game/kart/KartSuspensionPhysics.hh', ['WheelPhysics', 'KartSuspensionPhysics']),
    ('game/kart/KartBoost.hh', ['KartBoost']),
    ('game/kart/KartBurnout.hh', ['KartBurnout']),
    ('game/kart/KartReject.hh', ['KartReject']),
    ('game/kart/KartScale.hh', ['KartScale']),
    ('game/kart/KartPullPath.hh', ['KartPullPathTracker', 'KartPullPath']),
    ('game/kart/KartJump.hh', ['KartJump', 'KartJumpBike']),
    ('game/kart/KartHalfPipe.hh', ['KartHalfPipe']),
    ('game/kart/KartMove.hh', ['KartMove', 'KartMoveBike']),
    ('game/kart/KartState.hh', ['KartState']),
    ('game/kart/KartCollide.hh', ['KartCollide']),
    ('game/kart/KartSub.hh', ['KartSub']),
    ('game/kart/KartAction.hh', ['KartAction']),
    ('game/kart/KartBody.hh', ['KartBody']),
    ('game/render/KartModel.hh', ['KartModel']),
    ('game/system/TimerManager.hh', ['Timer', 'TimerManager']),
    ('game/system/Random.hh', ['Random']),
    ('game/system/KPadController.hh', ['RaceInputState', 'KPad']),
    ('game/system/RaceManager.hh', ['RaceManager']),
    ('game/item/ItemInventory.hh', ['ItemInventory']),
    ('game/item/KartItem.hh', ['KartItem']),
    ('game/field/obj/ObjectBase.hh', ['ObjectBase']),
]
NAMESPACES = {'game/kart': 'Kart', 'game/render': 'Render', 'game/system': 'System',
              'game/item': 'Item', 'game/field': 'Field'}
SCALARS = {'f32', 'f64', 's8', 'u8', 's16', 'u16', 's32', 'u32', 's64', 'u64', 'bool',
           'size_t', 'int', 'unsigned', 'char', 'float', 'double', 'u128'}
VALUE_TYPES = {'EGG::Vector3f', 'EGG::Vector2f', 'EGG::Quatf', 'EGG::Matrix34f',
               'EGG::BoundBox3f', 'EGG::BoundBox2f', 'Vector3f', 'Quatf', 'Matrix34f'}
# Enums declared outside the parsed classes.
KNOWN_ENUMS = {'System::Trick', 'Trick', 'Action', 'ItemId', 'Course', 'Character', 'Vehicle',
               'ObjectId', 'Stage', 'eStatus', 'Reaction', 'CollisionCheckType'}
CONTAINER_SKIP = ('owning_span', 'fixed_vector', 'std::list', 'std::vector', 'std::span',
                  'std::unordered_map', 'std::map', 'std::function')


def strip_comments(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)


def find_body(text, kind, name, start=0):
    """Return (open_brace_index, close_brace_index) of `kind name ... {...}`."""
    m = re.search(r'\b%s\s+%s\b[^;{]*\{' % (kind, re.escape(name)), text[start:])
    if not m:
        return None
    open_i = start + m.end() - 1
    depth = 0
    for i in range(open_i, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return open_i, i
    raise ValueError('unbalanced braces in ' + name)


def split_statements(body):
    """Depth-1 statements of a class body: text ended by ';' or by a '{...}' block."""
    out, depth, cur, i = [], 0, '', 0
    while i < len(body):
        c = body[i]
        if c == '{':
            depth += 1
            cur += c
        elif c == '}':
            depth -= 1
            cur += c
            if depth == 0:
                out.append(cur.strip())
                cur = ''
        elif c == ';' and depth == 0:
            out.append((cur + ';').strip())
            cur = ''
        else:
            cur += c
        i += 1
    return [s for s in out if s]


class Parsed:
    def __init__(self):
        self.enums = set()      # qualified enum names found in headers
        self.aliases = {}       # qualified alias -> underlying type text
        self.structs = {}       # qualified name -> [(type, name, dims)]
        self.order = []
        self.skipped = []       # (class, member, type, reason)
        self.friends = []       # (header, class name) needing a friend line


def parse_class(parsed, text, kind, name, qual, header_ns, header=None):
    if header and kind == 'class':
        parsed.friends.append((header, name))
    res = find_body(text, kind, name)
    if not res:
        raise SystemExit(f'{qual}: not found')
    body = text[res[0] + 1:res[1]]
    members = []
    for st in split_statements(body):
        s = re.sub(r'\s+', ' ', st).strip()
        for label in ('public:', 'protected:', 'private:'):
            s = s.replace(label, '').strip()
        if not s:
            continue
        m = re.match(r'(enum class|enum) (\w+)', s)
        if m:
            parsed.enums.add(qual + '::' + m.group(2))
            parsed.enums.add(m.group(2))
            continue
        m = re.match(r'(struct|class) (\w+)\s*(?::[^{]*)?\{', s)
        if m:
            inner = m.group(2)
            parse_class(parsed, st, m.group(1), inner, qual + '::' + inner, header_ns, header)
            continue
        m = re.match(r'typedef (.+) (\w+);$', s)
        if m:
            parsed.aliases[qual + '::' + m.group(2)] = m.group(1)
            parsed.aliases[m.group(2)] = m.group(1)
            continue
        m = re.match(r'using (\w+) = (.+);$', s)
        if m:
            parsed.aliases[qual + '::' + m.group(1)] = m.group(2)
            parsed.aliases[m.group(1)] = m.group(2)
            continue
        if s.startswith(('static ', 'friend ', 'template', 'virtual ', 'explicit ', 'constexpr ', 'STATIC_ASSERT', 'using ', '~')):
            continue
        eq = s.find(' = ')
        head = s if eq < 0 else s[:eq]
        if '(' in head or 'operator' in head or s.endswith('}'):
            continue  # function declaration/definition
        m = re.match(r'(?:mutable |volatile )?(.+?)\s+(\w+)((?:\[[^\]]+\])*)\s*(?:=\s*[^;]*|\{[^;]*\})?;$', s)
        if not m:
            continue
        typ, mname, dims = m.group(1).strip(), m.group(2), m.group(3)
        if kind == 'class' and not mname.startswith('m_'):
            continue  # a stray local/typedef; class members are m_-prefixed in Kinoko
        members.append((typ, mname, dims))
    parsed.structs[qual] = members
    parsed.order.append(qual)


def scan_file_level(parsed, text):
    for m in re.finditer(r'\benum(?: class)? (\w+)', text):
        parsed.enums.add(m.group(1))
    for m in re.finditer(r'\btypedef ([^;{}]+?) (\w+);', text):
        parsed.aliases.setdefault(m.group(2), m.group(1).strip())
    for m in re.finditer(r'\busing (\w+) = ([^;{}]+);', text):
        parsed.aliases.setdefault(m.group(1), m.group(2).strip())


def element_type(typ):
    m = re.match(r'std::array<(.+),\s*([^,>]+)>$', typ)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def classify(parsed, typ, owner):
    """Return ('bytes'|'array'|'struct'|'skip', detail)."""
    t = typ.replace('const ', '').strip()
    if '*' in t or '&' in t or t.startswith(CONTAINER_SKIP) or any(c in t for c in CONTAINER_SKIP):
        return 'skip', 'pointer/container'
    if t in SCALARS or t in VALUE_TYPES or t.startswith(('EGG::TBitFlag', 'EGG::TBitFlagExt', 'TBitFlag')):
        return 'bytes', t
    for cand in (owner + '::' + t, t, owner.rsplit('::', 1)[0] + '::' + t, owner.split('::')[0] + '::' + t, t.split('::')[-1]):
        if cand in parsed.aliases:
            return classify(parsed, parsed.aliases[cand], owner)
        if cand in parsed.enums:
            return 'bytes', 'enum ' + cand
        if cand in parsed.structs:
            return 'struct', cand
    if t in KNOWN_ENUMS or t.split('::')[-1] in KNOWN_ENUMS:
        return 'bytes', 'enum ' + t
    arr = element_type(t)
    if arr:
        inner = classify(parsed, arr[0], owner)
        if inner[0] == 'skip':
            return 'skip', 'array of ' + inner[1]
        return 'array', (arr[0], arr[1], inner)
    return 'skip', 'unknown type ' + t


def cxx_name(qual):
    return qual.replace('::', '_')


def emit_member(parsed, lines, owner, expr, label, typ, dims):
    if dims:
        sizes = re.findall(r'\[([^\]]+)\]', dims)
        var = 'i%d' % len(label)
        lines.append(f'    for (size_t {var} = 0; {var} < std::size({expr}); ++{var}) {{')
        emit_member(parsed, lines, owner, f'{expr}[{var}]', label + '[]', typ, dims[dims.index(']') + 1:])
        lines.append('    }')
        return
    kind, detail = classify(parsed, typ, owner)
    if kind == 'bytes':
        lines.append(f'    h.field("{label}", &{expr}, sizeof({expr}));')
    elif kind == 'struct':
        lines.append(f'    hash_{cxx_name(detail)}(h, {expr});')
    elif kind == 'array':
        etype, count, inner = detail
        var = 'j%d' % len(label)
        lines.append(f'    for (size_t {var} = 0; {var} < {expr}.size(); ++{var}) {{')
        emit_member(parsed, lines, owner, f'{expr}[{var}]', label + '[]', etype, '')
        lines.append('    }')
    else:
        parsed.skipped.append((owner, label, typ, detail))


def main():
    apply = '--apply' in sys.argv
    parsed = Parsed()
    includes = []
    for extra in ('game/kart/Status.hh', 'game/field/KCollisionTypes.hh', 'game/kart/KartObjectProxy.hh'):
        scan_file_level(parsed, strip_comments((KIN / extra).read_text()))
    for header, names in CLASSES:
        text = strip_comments((KIN / header).read_text())
        ns = NAMESPACES[str(Path(header).parent).split('/obj')[0]]
        includes.append(header)
        scan_file_level(parsed, text)
        for name in names:
            kind = 'class' if re.search(r'\bclass\s+%s\b' % name, text) else 'struct'
            parse_class(parsed, text, kind, name, ns + '::' + name, ns, header)
    lines = ['// Generated by tools/sim_determinism/gen_sim_hash.py; do not edit.',
             '// Field-by-field hashing of the simulation state (docs/o3ds-kinoko-arm11.md).',
             '#include "game/SimHash.hh"', '', '#include <iterator>', '']
    lines += [f'#include "{h}"' for h in includes]
    lines += ['', 'namespace Kinoko {', '']
    for qual in parsed.order:
        lines.append(f'void SimHash::hash_{cxx_name(qual)}(Hasher &h, const {qual} &o) {{')
        for typ, mname, dims in parsed.structs[qual]:
            emit_member(parsed, lines, qual, 'o.' + mname, qual.split('::', 1)[1] + '::' + mname, typ, dims)
        lines.append('}')
        lines.append('')
    lines += ['} // namespace Kinoko', '']
    decl = ['    // Generated declarations (gen_sim_hash.py).']
    decl += [f'    static void hash_{cxx_name(q)}(Hasher &h, const {q} &o);' for q in parsed.order]
    OUT.write_text('\n'.join(lines))
    (KIN / 'game/SimHashDecl.inc').write_text('\n'.join(decl) + '\n')
    (KIN / 'game/SimHashIncludes.inc').write_text('// Generated by gen_sim_hash.py.\n' + '\n'.join(f'#include "{h}"' for h in includes) + '\n')
    print(f'{OUT.relative_to(ROOT)}: {len(parsed.order)} types, '
          f'{sum(len(v) for v in parsed.structs.values())} members')
    for owner, label, typ, why in parsed.skipped:
        print(f'  skipped {label}: {typ} ({why})')
    if apply:
        by_header = {}
        for header, name in parsed.friends:
            by_header.setdefault(header, []).append(name)
        for header, names in by_header.items():
            path = KIN / header
            text = path.read_text()
            fwd = '#pragma once\n\nnamespace Kinoko {\nstruct SimHash; // mkw-3ds: game/SimHash.hh\n}\n'
            if 'struct SimHash;' not in text:
                text = text.replace('#pragma once\n', fwd, 1)
            for name in names:
                m = re.search(r'^([ ]*)(class|struct) %s\b[^\n{;]*\{\n' % re.escape(name), text, flags=re.M)
                if not m:
                    raise SystemExit(f'{header}: cannot find {name}')
                marker = m.group(1) + '    friend struct ::Kinoko::SimHash;\n'
                if text[m.end():m.end() + len(marker)] != marker:
                    text = text[:m.end()] + marker + text[m.end():]
            path.write_text(text)
        print('friend declarations applied')


if __name__ == '__main__':
    main()
