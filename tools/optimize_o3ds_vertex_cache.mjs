#!/usr/bin/env node
// Old 3DS post-transform vertex cache order for the low-poly overlay
// (models/o3ds/*.gmdl and models/o3ds/rival/*.gmdl, O3RM v1-v3 sidecars).
//
// Only the order of triangles inside a draw range changes. Every vertex record,
// remap, O3SP block, texture name and submesh span stays byte-identical, and
// every triangle keeps its exact (a,b,c) index tuple, so winding, provoking
// vertex and the O3RM loader checks are untouched. Vertex (fetch) order cannot
// move: O3RM v1 remaps are strictly increasing and v2/v3 only allow equal source
// ids to swap, which would change no fetch address.
//
// Pixel safety. The scene depth test is GEQUAL (a later equal-depth fragment
// wins) so triangle order is invisible only where no two triangles of a range
// can produce the same pixel at the same depth. Hence:
//  - blended, depth-test-off and depth-write-off submeshes keep their order;
//  - sky models (course *_sky.list, sun objects: drawn as backdrop, no depth
//    test) and models with runtime material binders/icon layers keep it;
//  - triangles that are coplanar-overlapping or interpenetrating with another
//    triangle of the same range keep their mutual relative order (each such
//    conflict group is written back into the slots it occupies, in authored
//    order).
// Static block culling (Model::enableStaticCulling: course track lists and the
// Coconut Mall palm) works on fixed 128-triangle index blocks; for those models
// each block is reordered on its own so block membership and bounds - and so
// culling - are unchanged. makeRigid's stable sort by bone keeps the relative
// order inside each bone group, so rigid parts inherit the order.
//
// A range is rewritten only if the new order is no worse at every modelled FIFO
// cache size (4, 8, 16, 32: GPUREG_POST_VERTEX_CACHE_NUM is undocumented) and
// strictly better in total.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';
import { MeshoptEncoder } from 'meshoptimizer';
import { Assets, listNames, parseGmdl, positions } from './o3ds_gmdl.mjs';

export const SIZES = [4, 8, 16, 32];
const BLOCK_TRIANGLES = 128;

// ---------------------------------------------------------------- cache model
export function fifoMisses(indices, size, first = 0, count = indices.length) {
  // FIFO: an entry inserted at miss m is evicted at miss m+size.
  const inserted = new Map();
  let misses = 0;
  for (let i = first; i < first + count; ++i) {
    const v = indices[i], at = inserted.get(v);
    if (at === undefined || misses - at >= size) { inserted.set(v, misses); ++misses; }
  }
  return misses;
}
const missVector = (idx) => SIZES.map(s => fifoMisses(idx, s));
const sum = (a) => a.reduce((x, y) => x + y, 0);

// ------------------------------------------------------------ order candidates
function meshopt(idx, strip) {
  const work = Uint32Array.from(idx);
  const [remap] = MeshoptEncoder.reorderMesh(work, true, strip);
  // reorderMesh also renumbers vertices for fetch; map them back.
  const inverse = new Map();
  remap.forEach((n, old) => { if (n !== 0xffffffff) inverse.set(n, old); });
  return Uint32Array.from(work, n => inverse.get(n));
}

// Tipsify (Sander, Nehab, Barczak 2007), tuned for a FIFO of k entries.
function tipsify(idx, k) {
  const local = new Map(), ids = [];
  const tri = Uint32Array.from(idx, v => { if (!local.has(v)) { local.set(v, ids.length); ids.push(v); } return local.get(v); });
  const nv = ids.length, nt = tri.length / 3;
  const live = new Int32Array(nv), start = new Int32Array(nv + 1);
  for (const v of tri) ++live[v];
  for (let v = 0; v < nv; ++v) start[v + 1] = start[v] + live[v];
  const adj = new Int32Array(tri.length), fill = start.slice(0, nv);
  for (let t = 0; t < nt; ++t) for (let c = 0; c < 3; ++c) adj[fill[tri[3 * t + c]]++] = t;
  const stamp = new Int32Array(nv), emitted = new Uint8Array(nt), out = [];
  const dead = [];
  let time = k + 1, cursor = 0, f = tri[0];
  while (f >= 0) {
    const candidates = [];
    for (let a = start[f]; a < start[f + 1]; ++a) {
      const t = adj[a];
      if (emitted[t]) continue;
      emitted[t] = 1;
      for (let c = 0; c < 3; ++c) {
        const v = tri[3 * t + c];
        out.push(ids[v]); dead.push(v); candidates.push(v); --live[v];
        if (time - stamp[v] > k) stamp[v] = time++;
      }
    }
    let best = -1, priority = -1;
    for (const v of candidates) {
      if (live[v] <= 0) continue;
      const p = time - stamp[v] + 2 * live[v] <= k ? time - stamp[v] : 0;
      if (p > priority) { priority = p; best = v; }
    }
    if (best < 0) {
      while (dead.length && best < 0) { const d = dead.pop(); if (live[d] > 0) best = d; }
      while (best < 0 && cursor < nv) { if (live[cursor] > 0) best = cursor; ++cursor; }
    }
    f = best;
  }
  return Uint32Array.from(out);
}

// Greedy against simulated 4- and 16-entry FIFOs: next is the unemitted
// triangle around a recently transformed vertex with the most hits (weight w4
// on the small cache), preferring vertices close to finishing their fans.
function greedyFifo(idx, w4) {
  const local = new Map(), ids = [];
  const tri = Uint32Array.from(idx, v => { if (!local.has(v)) { local.set(v, ids.length); ids.push(v); } return local.get(v); });
  const nv = ids.length, nt = tri.length / 3;
  const live = new Int32Array(nv), start = new Int32Array(nv + 1);
  for (const v of tri) ++live[v];
  for (let v = 0; v < nv; ++v) start[v + 1] = start[v] + live[v];
  const adj = new Int32Array(tri.length), fill = start.slice(0, nv);
  for (let t = 0; t < nt; ++t) for (let c = 0; c < 3; ++c) adj[fill[tri[3 * t + c]]++] = t;
  const at4 = new Int32Array(nv).fill(-1 << 30), at16 = new Int32Array(nv).fill(-1 << 30);
  let m4 = 0, m16 = 0, cursor = 0;
  const recent = [];                              // last 16 vertices inserted into the 16-entry FIFO
  const emitted = new Uint8Array(nt), out = [];
  for (let n = 0; n < nt; ++n) {
    let best = -1, score = -1;
    for (const v of recent) for (let a = start[v]; a < start[v + 1]; ++a) {
      const t = adj[a];
      if (emitted[t]) continue;
      let s = 0;
      for (let c = 0; c < 3; ++c) { const u = tri[3 * t + c];
        if (m4 - at4[u] < 4) s += w4;
        if (m16 - at16[u] < 16) s += 1;
        s += 1 / (1 + live[u]); }
      if (s > score) { score = s; best = t; }
    }
    if (best < 0) { while (emitted[cursor]) ++cursor; best = cursor; }
    emitted[best] = 1;
    for (let c = 0; c < 3; ++c) {
      const u = tri[3 * best + c];
      out.push(ids[u]); --live[u];
      if (m4 - at4[u] >= 4) at4[u] = m4++;
      if (m16 - at16[u] >= 16) { at16[u] = m16++; recent.push(u); if (recent.length > 16) recent.shift(); }
    }
  }
  return Uint32Array.from(out);
}

// --------------------------------------------------------------- pixel safety
// Triangles of one range that can tie in depth: near-coplanar with overlapping
// interiors, or interpenetrating. Returns the conflicting pairs [earlier, later]
// in authored order; only these pairs must keep their relative order.
function conflictPairs(idx, pos) {
  const nt = idx.length / 3;
  const out = [];
  if (nt < 2) return out;
  const P = (t, c) => { const v = idx[3 * t + c]; return [pos[3 * v], pos[3 * v + 1], pos[3 * v + 2]]; };
  const tris = [], lo = [], hi = [];
  let ext = 0;
  for (let t = 0; t < nt; ++t) {
    const a = P(t, 0), b = P(t, 1), c = P(t, 2);
    tris.push([a, b, c]);
    const l = [0, 1, 2].map(i => Math.min(a[i], b[i], c[i])), h = [0, 1, 2].map(i => Math.max(a[i], b[i], c[i]));
    lo.push(l); hi.push(h); ext += Math.max(h[0] - l[0], h[1] - l[1], h[2] - l[2]);
  }
  let diag = 0;
  { const l = [0, 1, 2].map(i => Math.min(...lo.map(x => x[i]))), h = [0, 1, 2].map(i => Math.max(...hi.map(x => x[i])));
    diag = Math.hypot(h[0] - l[0], h[1] - l[1], h[2] - l[2]) || 1; }
  const eps = 1e-5 * diag;                       // plane tolerance (24-bit depth ties)
  const cell = Math.max(ext / nt * 2, diag / 256, 1e-6);
  const grid = new Map();
  const key = (x, y, z) => `${x},${y},${z}`;
  for (let t = 0; t < nt; ++t) {
    const [l, h] = [lo[t], hi[t]];
    for (let x = Math.floor((l[0] - eps) / cell); x <= Math.floor((h[0] + eps) / cell); ++x)
      for (let y = Math.floor((l[1] - eps) / cell); y <= Math.floor((h[1] + eps) / cell); ++y)
        for (let z = Math.floor((l[2] - eps) / cell); z <= Math.floor((h[2] + eps) / cell); ++z) {
          const k = key(x, y, z); if (!grid.has(k)) grid.set(k, []); grid.get(k).push(t);
        }
  }
  const seen = new Set();
  for (const list of grid.values()) {
    for (let i = 0; i < list.length; ++i) for (let j = i + 1; j < list.length; ++j) {
      const a = list[i], b = list[j];
      const pk = a < b ? a * nt + b : b * nt + a;
      if (seen.has(pk)) continue;
      seen.add(pk);
      if (lo[a].some((v, k) => v > hi[b][k] + eps) || lo[b].some((v, k) => v > hi[a][k] + eps)) continue;
      if (conflict(tris[a], tris[b], eps)) out.push(a < b ? [a, b] : [b, a]);
    }
  }
  return out;
}

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const same = (p, q, eps) => Math.abs(p[0] - q[0]) <= eps && Math.abs(p[1] - q[1]) <= eps && Math.abs(p[2] - q[2]) <= eps;

function conflict(A, B, eps) {
  const nA = cross(sub(A[1], A[0]), sub(A[2], A[0])), lA = Math.hypot(...nA);
  const nB = cross(sub(B[1], B[0]), sub(B[2], B[0])), lB = Math.hypot(...nB);
  if (!(lA > 0) || !(lB > 0)) return false;     // degenerate: covers no pixels
  const dB = B.map(p => dot(nA, sub(p, A[0])) / lA);
  const dA = A.map(p => dot(nB, sub(p, B[0])) / lB);
  const shared = A.filter(p => B.some(q => same(p, q, eps))).length;
  const coplanar = dB.every(d => Math.abs(d) <= eps) && dA.every(d => Math.abs(d) <= eps);
  if (coplanar) return overlap2d(A, B, nA, eps);
  if (shared) return false;                     // touching at a vertex/edge only
  // Interpenetration (Moller): each triangle must straddle the other's plane.
  const straddles = (d) => Math.min(...d) < -eps && Math.max(...d) > eps;
  if (!straddles(dB) || !straddles(dA)) return false;
  return segmentOverlap(A, B, nA, nB, dA, dB);
}

// Interiors of two coplanar triangles overlap (strictly, after a tiny shrink
// so triangles that only share an edge or vertex do not count).
function overlap2d(A, B, n, eps) {
  const ax = Math.abs(n[0]) > Math.abs(n[1]) ? (Math.abs(n[0]) > Math.abs(n[2]) ? 0 : 2) : (Math.abs(n[1]) > Math.abs(n[2]) ? 1 : 2);
  const u = (ax + 1) % 3, w = (ax + 2) % 3;
  const shrink = (T) => { const c = [0, 1, 2].map(i => (T[0][i] + T[1][i] + T[2][i]) / 3);
    return T.map(p => [c[u] + (p[u] - c[u]) * 0.999, c[w] + (p[w] - c[w]) * 0.999]); };
  const a = shrink(A), b = shrink(B);
  for (const T of [a, b]) for (let e = 0; e < 3; ++e) {
    const p = T[e], q = T[(e + 1) % 3], nx = q[1] - p[1], ny = p[0] - q[0];
    const pa = a.map(v => nx * v[0] + ny * v[1]), pb = b.map(v => nx * v[0] + ny * v[1]);
    if (Math.max(...pa) <= Math.min(...pb) || Math.max(...pb) <= Math.min(...pa)) return false;
  }
  return true;
}

function segmentOverlap(A, B, nA, nB, dA, dB) {
  const D = cross(nA, nB);
  const interval = (T, d) => {
    const p = T.map(v => dot(D, v)), out = [];
    for (let i = 0; i < 3; ++i) { const j = (i + 1) % 3;
      if ((d[i] < 0) !== (d[j] < 0) && d[i] !== d[j]) out.push(p[i] + (p[j] - p[i]) * d[i] / (d[i] - d[j])); }
    return [Math.min(...out), Math.max(...out)];
  };
  const [a0, a1] = interval(A, dA), [b0, b1] = interval(B, dB);
  return Math.min(a1, b1) - Math.max(a0, b0) > 1e-9 * Math.max(1, Math.abs(a1), Math.abs(b1));
}

// ------------------------------------------------------------------- ranges
// Reorder one draw range of triangle tuples; returns the chosen order (or null).
function optimizeRange(idx, pos, select) {
  const before = missVector(idx);
  if (idx.length < 6) return { order: null, before, after: before, conflicts: 0 };
  const pairs = conflictPairs(idx, pos);
  const candidates = [meshopt(idx, false), meshopt(idx, true), tipsify(idx, 4), tipsify(idx, 6), tipsify(idx, 10), tipsify(idx, 14), tipsify(idx, 24),
    greedyFifo(idx, 4), greedyFifo(idx, 1.5)];
  let best = null, bestMiss = before, bestSum = sum(before);
  for (let order of candidates) {
    if (pairs.length) order = keepPairOrder(idx, order, pairs);
    const m = missVector(order);
    if (select === 'pareto' && m.some((x, i) => x > before[i])) continue;
    if (select === 'robust' && m[0] > before[0]) continue;
    if (sum(m) < bestSum) { best = order; bestMiss = m; bestSum = sum(m); }
  }
  return { order: best, before, after: best ? bestMiss : before, conflicts: pairs.length };
}

// The candidate order, minimally changed so that every conflicting pair keeps
// its authored order: a topological sort of the pair constraints (authored
// earlier -> later, so acyclic) that always emits the ready triangle the
// candidate placed first.
function keepPairOrder(idx, order, pairs) {
  const nt = idx.length / 3;
  const queue = new Map();                        // tuple -> authored triangle ids (duplicates allowed)
  for (let t = 0; t < nt; ++t) { const k = `${idx[3 * t]},${idx[3 * t + 1]},${idx[3 * t + 2]}`; if (!queue.has(k)) queue.set(k, []); queue.get(k).push(t); }
  const rank = new Int32Array(nt);                // candidate position of each authored triangle
  for (let s = 0; s < nt; ++s) rank[queue.get(`${order[3 * s]},${order[3 * s + 1]},${order[3 * s + 2]}`).shift()] = s;
  const indegree = new Int32Array(nt), after = Array.from({ length: nt }, () => []);
  for (const [a, b] of pairs) { after[a].push(b); ++indegree[b]; }
  // Binary heap of ready triangles keyed by candidate rank.
  const heap = [];
  const push = (t) => { heap.push(t); let i = heap.length - 1;
    while (i) { const p = (i - 1) >> 1; if (rank[heap[p]] <= rank[t]) break; heap[i] = heap[p]; i = p; } heap[i] = t; };
  const pop = () => { const top = heap[0], last = heap.pop(); if (heap.length) { let i = 0;
    for (;;) { const l = 2 * i + 1, r = l + 1; let m = i, mv = last;
      if (l < heap.length && rank[heap[l]] < rank[mv]) { m = l; mv = heap[l]; }
      if (r < heap.length && rank[heap[r]] < rank[mv]) { m = r; mv = heap[r]; }
      if (m === i) break; heap[i] = heap[m]; i = m; } heap[i] = last; } return top; };
  for (let t = 0; t < nt; ++t) if (!indegree[t]) push(t);
  const out = new Uint32Array(idx.length);
  for (let s = 0; s < nt; ++s) {
    const t = pop();
    out.set(idx.subarray(3 * t, 3 * t + 3), 3 * s);
    for (const b of after[t]) if (!--indegree[b]) push(b);
  }
  return out;
}

// ------------------------------------------------------------------ models
const FORCED_KEEP = [/^ui_/, /shadow/, /itembox/, /fakebox/, /bomb/, /blast/];

export function reorderable(s) {
  const sourceState = (s.flags >>> 31) & 1, blended = s.flags & 2;
  if (blended) return 'blended';
  if (sourceState && !(s.flags & (1 << 23))) return 'no-depth-test';
  if (sourceState && !(s.flags & (1 << 24))) return 'no-depth-write';
  return null;
}

export function processModel(buf, name, policy, select = 'robust') {
  const model = parseGmdl(buf, name);
  const pos = positions(model);
  const out = Uint16Array.from(model.indices);
  const stat = { name, triangles: model.ni / 3, before: SIZES.map(() => 0), after: SIZES.map(() => 0),
    kept: {}, changedRanges: 0, ranges: 0, conflicts: 0, blocks: policy.blocks, bound: 0 };
  const keep = (why, n) => { stat.kept[why] = (stat.kept[why] || 0) + n; };
  // Animated models: a triangle is labelled by the bones of its vertices
  // (authored source ids through the O3RM map). Triangles that move relative
  // to each other can meet in some pose, so only runs of one label are
  // reordered; their bind-pose relations then hold in every pose.
  let label = null;
  if (policy.matrix) {
    const source = model.o3rm ? sourceMap(buf, model) : null;
    const bone = (v) => policy.matrix[source ? source[v] : v];
    label = (t) => [...new Set([bone(model.indices[t]), bone(model.indices[t + 1]), bone(model.indices[t + 2])])].sort((x, y) => x - y).join('.');
  }
  for (const s of model.submeshes) {
    if (!s.count) continue;
    const reason = policy.keep || reorderable(s);
    const units = [];
    if (policy.blocks) for (let at = s.first; at < s.first + s.count; at += 3 * BLOCK_TRIANGLES)
      units.push([at, Math.min(3 * BLOCK_TRIANGLES, s.first + s.count - at)]);
    else units.push([s.first, s.count]);
    for (const [first, count] of units) {
      const idx = model.indices.subarray(first, first + count);
      ++stat.ranges;
      stat.bound += new Set(idx).size;          // every vertex of a range is transformed once at least
      const before = missVector(idx);
      before.forEach((x, i) => { stat.before[i] += x; });
      const kept = (why) => { before.forEach((x, i) => { stat.after[i] += x; }); keep(why, count / 3); };
      if (reason) { kept(reason); continue; }
      const runs = [];
      for (let t = first; t < first + count; t += 3) {
        const l = label ? label(t) : '';
        if (runs.length && runs[runs.length - 1].label === l) runs[runs.length - 1].count += 3;
        else runs.push({ label: l, first: t, count: 3 });
      }
      const order = Uint32Array.from(idx);
      for (const run of runs) {
        const r = optimizeRange(model.indices.subarray(run.first, run.first + run.count), pos, select);
        stat.conflicts += r.conflicts;
        if (r.order) order.set(r.order, run.first - first);
      }
      const after = missVector(order);
      if (after[0] <= before[0] && sum(after) < sum(before)) {
        out.set(order, first); ++stat.changedRanges;
        after.forEach((x, i) => { stat.after[i] += x; });
      } else kept('no-gain');
    }
  }
  const result = Buffer.from(buf);
  for (let i = 0; i < out.length; ++i) result.writeUInt16LE(out[i], model.indexOffset + 2 * i);
  verifyRewrite(buf, result, model, policy);
  return { buf: result, stat };
}

// O3RM compact map: compact vertex -> authored source vertex.
function sourceMap(buf, model) {
  const at = model.indexOffset + model.ni * 2 + 16;
  const map = new Uint32Array(model.o3rm.count);
  for (let i = 0; i < map.length; ++i) map[i] = buf.readUInt32LE(at + 4 * i);
  return map;
}

// Per source vertex bone (palette matrix) of a DSKM (drivers) or DOBJ (course
// objects) bank; null if none. MORF objects deform per vertex: no bones.
export function bankMatrices(data) {
  const tag = data.toString('latin1', 0, 4);
  let nv, at;
  if (tag === 'DSKM') { nv = data.readUInt32LE(8); at = data.readUInt32LE(4) === 5 ? 32 : 28; }
  else if (tag === 'DOBJ') { nv = data.readUInt32LE(8); at = 40; }
  else return null;
  const m = new Uint32Array(nv);
  for (let i = 0; i < nv; ++i) m[i] = data.readUInt32LE(at + 28 * i + 24);
  return m;
}

// Everything outside the index block is identical, and every draw range (or
// culling block) holds exactly the same multiset of (a,b,c) tuples.
export function verifyRewrite(a, b, model, policy) {
  const at = model.indexOffset, end = at + model.ni * 2;
  if (a.length !== b.length || Buffer.compare(a.subarray(0, at), b.subarray(0, at)) || Buffer.compare(a.subarray(end), b.subarray(end)))
    throw new Error(`${model.name}: bytes outside the index block changed`);
  const m2 = parseGmdl(b);
  for (const s of model.submeshes) {
    const step = policy.blocks ? 3 * BLOCK_TRIANGLES : Math.max(s.count, 3);
    for (let f = s.first; f < s.first + s.count; f += step) {
      const n = Math.min(step, s.first + s.count - f);
      const tuples = (ix) => { const r = []; for (let i = f; i < f + n; i += 3) r.push(`${ix[i]},${ix[i + 1]},${ix[i + 2]}`); return r.sort().join(' '); };
      if (tuples(model.indices) !== tuples(m2.indices)) throw new Error('triangle multiset changed');
    }
  }
}

// -------------------------------------------------------------------- main
function policies(assets) {
  const sky = new Set(), track = new Set(['coconut_palm_000']);
  for (const list of assets.list('models/', '.list')) {
    const names = listNames(assets, list);
    if (/_sky\.list$/.test(list)) names.forEach(n => sky.add(n));
    else if (/^models\/course_\d+\.list$/.test(list)) names.forEach(n => track.add(n));
  }
  return (stem) => {
    if (sky.has(stem) || /_sun__sun_/.test(stem)) return { keep: 'sky-backdrop', blocks: false };
    if (FORCED_KEEP.some(r => r.test(stem))) return { keep: 'runtime-material', blocks: false };
    let matrix = null;
    for (const rel of [`models/${stem}.dskm`, `models/${stem.replace(/_(low_)?\d{3}$/, '')}.dobj`]) {
      if (!assets.has(rel)) continue;
      const data = assets.read(rel);
      if (data.toString('latin1', 0, 4) === 'DOBJ' && data.includes('MORF')) return { keep: 'morph-animated', blocks: false };
      matrix = bankMatrices(data);
      break;
    }
    return { keep: null, blocks: track.has(stem), matrix };
  };
}

async function main() {
  const args = Object.fromEntries(process.argv.slice(2).join(' ').split(/\s*--/).filter(Boolean).map(s => { const [k, ...v] = s.split(' '); return [k, v.join(' ') || true]; }));
  if (!args.package || !args.assets) {
    console.error('usage: optimize_o3ds_vertex_cache.mjs --package <dir with models/o3ds> --assets <mkw-3ds root> [--out <dir>] [--receipt <json>] [--only <regex>]');
    process.exit(2);
  }
  await MeshoptEncoder.ready;
  const assets = new Assets(args.assets);
  const policyOf = policies(assets);
  const files = [];
  for (const sub of ['', 'rival']) {
    const dir = path.join(args.package, 'models', 'o3ds', sub);
    if (!fs.existsSync(dir)) continue;
    for (const f of fs.readdirSync(dir).sort()) if (f.endsWith('.gmdl')) files.push(path.join(sub, f));
  }
  const only = args.only ? new RegExp(args.only) : null;
  const receipt = { tool: 'tools/optimize_o3ds_vertex_cache.mjs', sizes: SIZES, select: args.select || 'robust', package: path.resolve(args.package), models: {} };
  const totals = { triangles: 0, before: SIZES.map(() => 0), after: SIZES.map(() => 0), kept: {}, changed: 0, conflicts: 0, bound: 0 };
  for (const rel of files) {
    const stem = path.basename(rel, '.gmdl');
    if (only && !only.test(stem)) continue;
    const src = fs.readFileSync(path.join(args.package, 'models', 'o3ds', rel));
    const { buf, stat } = processModel(src, rel, policyOf(stem), args.select || 'robust');
    totals.triangles += stat.triangles;
    stat.before.forEach((x, i) => { totals.before[i] += x; totals.after[i] += stat.after[i]; });
    for (const [k, v] of Object.entries(stat.kept)) totals.kept[k] = (totals.kept[k] || 0) + v;
    totals.conflicts += stat.conflicts;
    totals.bound += stat.bound;
    const changed = Buffer.compare(src, buf) !== 0;
    totals.changed += changed;
    receipt.models[rel] = { triangles: stat.triangles, acmrBefore: stat.before.map(x => +(x / stat.triangles).toFixed(4)),
      acmrAfter: stat.after.map(x => +(x / stat.triangles).toFixed(4)), acmrBound: +(stat.bound / stat.triangles).toFixed(4), blocks: stat.blocks, kept: stat.kept,
      conflictPairs: stat.conflicts, ranges: stat.ranges, changedRanges: stat.changedRanges,
      sha256In: crypto.createHash('sha256').update(src).digest('hex'), sha256Out: crypto.createHash('sha256').update(buf).digest('hex') };
    if (args.out) {
      // Unchanged files are left alone (an APFS clone of the package stays shared).
      const dest = path.join(args.out, 'models', 'o3ds', rel);
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      if (changed || !fs.existsSync(dest) || Buffer.compare(fs.readFileSync(dest), buf)) fs.writeFileSync(dest, buf);
    }
  }
  receipt.totals = { ...totals, acmrBefore: totals.before.map(x => +(x / totals.triangles).toFixed(4)), acmrAfter: totals.after.map(x => +(x / totals.triangles).toFixed(4)), acmrBound: +(totals.bound / totals.triangles).toFixed(4) };
  console.log(JSON.stringify(receipt.totals));
  if (args.receipt) fs.writeFileSync(args.receipt, JSON.stringify(receipt, null, 1));
}

// Compare resolved paths: the raw argv form never matches import.meta.url when the
// checkout path contains spaces (or the script is given relative).
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(e => { console.error(e); process.exit(1); });
}
