// Shared readers for the Old 3DS GPU data tools: GMDL / O3RM sidecars and the
// MPK1 asset packs (menu.pak, race.pak) exactly as the runtime resolves them.
// Nothing here writes a file.
import fs from 'node:fs';
import path from 'node:path';

// Submesh record size by GMDL version (src/renderer.cpp Model::load).
export const RECORD = { 3: 44, 4: 56, 5: 92 };
export const VERTEX = 44;

// Parse a GMDL or an O3RM sidecar. `indexOffset` locates the index bytes so a
// rewrite can change only them; `tail` is everything after the indices.
export function parseGmdl(buf, name = '') {
  if (buf.length < 24 || buf.toString('latin1', 0, 4) !== 'GMDL') throw new Error(`${name}: not GMDL`);
  const version = buf.readUInt32LE(4), nv = buf.readUInt32LE(8), ni = buf.readUInt32LE(12);
  const ns = buf.readUInt32LE(16), nt = buf.readUInt32LE(20);
  const size = RECORD[version];
  if (!size) throw new Error(`${name}: GMDL version ${version}`);
  let at = 24;
  const submeshes = [];
  for (let i = 0; i < ns; ++i, at += size) {
    submeshes.push({
      first: buf.readUInt32LE(at), count: buf.readUInt32LE(at + 4), texture: buf.readInt32LE(at + 8),
      flags: buf.readUInt32LE(at + 12), secondary: buf.readInt32LE(at + 16), offset: at,
    });
  }
  const names = [];
  for (let i = 0; i < nt; ++i) names.push(buf.toString('latin1', at + 64 * i, at + 64 * (i + 1)).split('\0')[0]);
  at += 64 * nt;
  const vertexOffset = at;
  at += nv * VERTEX;
  const indexOffset = at;
  if (indexOffset + ni * 2 > buf.length) throw new Error(`${name}: truncated`);
  const indices = new Uint16Array(ni);
  for (let i = 0; i < ni; ++i) indices[i] = buf.readUInt16LE(indexOffset + 2 * i);
  at += ni * 2;
  let o3rm = null;
  if (at < buf.length) {
    if (buf.toString('latin1', at, at + 4) !== 'O3RM') throw new Error(`${name}: unexpected tail`);
    o3rm = { version: buf.readUInt32LE(at + 4), original: buf.readUInt32LE(at + 8), count: buf.readUInt32LE(at + 12) };
  }
  for (const s of submeshes) if (s.first + s.count > ni || s.count % 3) throw new Error(`${name}: bad span`);
  for (const v of indices) if (v >= nv) throw new Error(`${name}: index out of range`);
  return { version, nv, ni, submeshes, names, vertexOffset, indexOffset, indices, o3rm, buf };
}

export function positions(model) {
  const p = new Float32Array(model.nv * 3);
  for (let i = 0; i < model.nv; ++i) {
    const at = model.vertexOffset + i * VERTEX;
    p[3 * i] = model.buf.readFloatLE(at); p[3 * i + 1] = model.buf.readFloatLE(at + 4); p[3 * i + 2] = model.buf.readFloatLE(at + 8);
  }
  return p;
}

// The runtime opens menu.pak/race.pak entries before loose SD files.
export class Assets {
  constructor(root) {
    this.root = root;
    this.entries = new Map();
    for (const pack of ['menu.pak', 'race.pak']) {
      const file = path.join(root, pack);
      if (!fs.existsSync(file)) continue;
      const fd = fs.openSync(file, 'r');
      const head = Buffer.alloc(12);
      fs.readSync(fd, head, 0, 12, 0);
      if (head.toString('latin1', 0, 4) !== 'MPK1') throw new Error(`${pack}: not MPK1`);
      const count = head.readUInt32LE(4), nameBytes = head.readUInt32LE(8);
      const table = Buffer.alloc(count * 12), names = Buffer.alloc(nameBytes);
      fs.readSync(fd, table, 0, table.length, 12);
      fs.readSync(fd, names, 0, nameBytes, 12 + table.length);
      for (let i = 0; i < count; ++i) {
        const nameAt = table.readUInt32LE(i * 12);
        const rel = names.toString('utf8', nameAt, names.indexOf(0, nameAt));
        if (!this.entries.has(rel)) this.entries.set(rel, { fd, offset: table.readUInt32LE(i * 12 + 4), size: table.readUInt32LE(i * 12 + 8), pack });
      }
    }
  }
  has(rel) { return this.entries.has(rel) || fs.existsSync(path.join(this.root, rel)); }
  source(rel) { return this.entries.has(rel) ? this.entries.get(rel).pack : 'loose'; }
  read(rel) {
    const e = this.entries.get(rel);
    if (e) { const b = Buffer.alloc(e.size); fs.readSync(e.fd, b, 0, e.size, e.offset); return b; }
    return fs.readFileSync(path.join(this.root, rel));
  }
  list(prefix, suffix) {
    const out = new Set();
    for (const k of this.entries.keys()) if (k.startsWith(prefix) && k.endsWith(suffix)) out.add(k);
    const dir = path.join(this.root, prefix);
    if (fs.existsSync(dir)) for (const f of fs.readdirSync(dir)) if (f.endsWith(suffix)) out.add(prefix + f);
    return [...out].sort();
  }
}

// Model names of a course list file (models/course_08.list, one name per line).
export function listNames(assets, rel) {
  if (!assets.has(rel)) return [];
  return assets.read(rel).toString('utf8').split(/\s+/).filter(Boolean).map(n => n.replace(/\.gmdl$/, ''));
}
