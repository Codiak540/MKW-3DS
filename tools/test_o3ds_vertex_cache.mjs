// node --test tools/test_o3ds_vertex_cache.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { MeshoptEncoder } from 'meshoptimizer';
import { fifoMisses, processModel } from './optimize_o3ds_vertex_cache.mjs';
import { parseGmdl } from './o3ds_gmdl.mjs';

// A v3 GMDL with an O3RM v1 tail: `subs` = [{tris:[[a,b,c],...], flags}].
function gmdl(points, subs) {
  const nv = points.length, tris = subs.flatMap(s => s.tris), ni = tris.length * 3;
  const out = [];
  const u32 = (...v) => { const b = Buffer.alloc(4 * v.length); v.forEach((x, i) => b.writeUInt32LE(x >>> 0, 4 * i)); out.push(b); };
  out.push(Buffer.from('GMDL')); u32(3, nv, ni, subs.length, 1);
  let first = 0;
  for (const s of subs) {
    const r = Buffer.alloc(44);
    r.writeUInt32LE(first, 0); r.writeUInt32LE(s.tris.length * 3, 4); r.writeInt32LE(0, 8); r.writeUInt32LE(s.flags >>> 0, 12); r.writeInt32LE(-1, 16);
    out.push(r); first += s.tris.length * 3;
  }
  out.push(Buffer.from('tex'.padEnd(64, '\0')));
  for (const p of points) { const v = Buffer.alloc(44); p.forEach((x, i) => v.writeFloatLE(x, 4 * i)); v.writeFloatLE(1, 16); out.push(v); }
  const idx = Buffer.alloc(ni * 2); tris.flat().forEach((v, i) => idx.writeUInt16LE(v, 2 * i)); out.push(idx);
  out.push(Buffer.from('O3RM')); u32(1, nv + 10, nv);
  u32(...points.map((_, i) => i));
  return Buffer.concat(out);
}

// An n x n grid of quads, triangles listed column-major (poor locality).
function grid(n) {
  const points = [];
  for (let y = 0; y <= n; ++y) for (let x = 0; x <= n; ++x) points.push([x, y, 0]);
  const tris = [];
  for (let x = 0; x < n; ++x) for (let y = 0; y < n; ++y) {
    const a = y * (n + 1) + x, b = a + 1, c = a + n + 1, d = c + 1;
    tris.push([a, b, d], [a, d, c]);
  }
  return { points, tris };
}

const tuples = (ix, f, n) => { const r = []; for (let i = f; i < f + n; i += 3) r.push(`${ix[i]},${ix[i + 1]},${ix[i + 2]}`); return r.sort().join(' '); };
const OPAQUE = 0, BLENDED = 2;

test('FIFO model counts transforms', () => {
  assert.equal(fifoMisses([0, 1, 2, 2, 1, 3], 4), 4);
  assert.equal(fifoMisses([0, 1, 2, 3, 4, 5, 0], 4), 7);
});

test('reorder changes only index order, never worse at 4 entries', async () => {
  await MeshoptEncoder.ready;
  const { points, tris } = grid(12);
  // Deterministic shuffle: an order with no locality at all.
  const shuffled = tris.map((t, i) => [(i * 7919) % tris.length, t]).sort((x, y) => x[0] - y[0]).map(x => x[1]);
  const src = gmdl(points, [{ tris: shuffled, flags: OPAQUE }, { tris: shuffled.slice(0, 40), flags: BLENDED }]);
  const { buf, stat } = processModel(src, 't', { keep: null, blocks: false });
  const a = parseGmdl(src), b = parseGmdl(buf);
  assert.equal(Buffer.compare(src.subarray(0, a.indexOffset), buf.subarray(0, a.indexOffset)), 0);
  assert.equal(Buffer.compare(src.subarray(a.indexOffset + a.ni * 2), buf.subarray(a.indexOffset + a.ni * 2)), 0);
  for (const s of a.submeshes) assert.equal(tuples(a.indices, s.first, s.count), tuples(b.indices, s.first, s.count));
  const blended = a.submeshes[1];
  assert.deepEqual([...b.indices.subarray(blended.first, blended.first + blended.count)], [...a.indices.subarray(blended.first, blended.first + blended.count)]);
  assert.ok(stat.after[0] <= stat.before[0]);
  assert.ok(stat.after[2] < stat.before[2], JSON.stringify(stat));
});

test('culling blocks keep their triangle sets', async () => {
  await MeshoptEncoder.ready;
  const { points, tris } = grid(20);                 // 800 triangles: 7 blocks of 128
  const src = gmdl(points, [{ tris, flags: OPAQUE }]);
  const { buf } = processModel(src, 't', { keep: null, blocks: true });
  const a = parseGmdl(src), b = parseGmdl(buf);
  for (let f = 0; f < a.ni; f += 384) assert.equal(tuples(a.indices, f, Math.min(384, a.ni - f)), tuples(b.indices, f, Math.min(384, a.ni - f)));
});

test('coplanar overlapping triangles keep their authored order', async () => {
  await MeshoptEncoder.ready;
  const { points, tris } = grid(8);
  // A decal: two triangles over the grid, same plane, sharing no vertex.
  const base = points.length;
  points.push([2.2, 2.2, 0], [3.8, 2.2, 0], [3.8, 3.8, 0], [5.2, 5.2, 0], [6.8, 5.2, 0], [6.8, 6.8, 0]);
  const decalA = [base, base + 1, base + 2], decalB = [base + 3, base + 4, base + 5];
  const all = [...tris.slice(0, 50), decalA, ...tris.slice(50), decalB];
  const src = gmdl(points, [{ tris: all, flags: OPAQUE }]);
  const { buf, stat } = processModel(src, 't', { keep: null, blocks: false });
  assert.ok(stat.conflicts > 0);
  const b = parseGmdl(buf);
  const pos = (t) => { for (let i = 0; i < b.ni; i += 3) if (b.indices[i] === t[0] && b.indices[i + 1] === t[1] && b.indices[i + 2] === t[2]) return i; return -1; };
  // Every grid triangle under decal A must still precede it; decal B after its grid triangles.
  const under = (t, d) => { const [x0, x1] = [Math.min(...d.map(v => points[v][0])), Math.max(...d.map(v => points[v][0]))];
    const xs = t.map(v => points[v][0]), ys = t.map(v => points[v][1]);
    return Math.max(...xs) > x0 && Math.min(...xs) < x1 && Math.max(...ys) > x0 && Math.min(...ys) < x1; };
  for (const t of tris.slice(0, 50)) if (under(t, decalA)) assert.ok(pos(t) < pos(decalA));
  for (const t of tris) if (under(t, decalB)) assert.ok(pos(t) < pos(decalB));
});

test('kept models are byte-identical', async () => {
  await MeshoptEncoder.ready;
  const { points, tris } = grid(6);
  const src = gmdl(points, [{ tris, flags: OPAQUE }]);
  const { buf } = processModel(src, 't', { keep: 'sky-backdrop', blocks: false });
  assert.equal(Buffer.compare(src, buf), 0);
});
