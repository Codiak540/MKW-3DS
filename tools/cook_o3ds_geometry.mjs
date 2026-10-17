#!/usr/bin/env node
// Experimental variants of the authored Wii -> GMDL pipeline.
// No assets are downloaded, no original file is changed, and no QA marker is set.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { MeshoptSimplifier } from 'meshoptimizer';

const STRIDE = 44;
const RECORD = { 3: 44, 4: 56, 5: 92 };
export const DEFAULTS = Object.freeze({ profile: 'conservative', ratio: 0.5, maxError: 0.005, minTriangles: 24 });
export const AGGRESSIVE_DEFAULTS = Object.freeze({ profile: 'aggressive', ratio: 0.3, maxError: 0.12, minTriangles: 8, protectFeatures: false });
const sha = data => createHash('sha256').update(data).digest('hex');
const ensure = (condition, message) => { if (!condition) throw new Error(message); };

export function parseModel(data) {
  ensure(data.length >= 24 && data.toString('ascii', 0, 4) === 'GMDL', 'Not a GMDL model');
  const [version, vertices, indices, count, textures] = [4, 8, 12, 16, 20].map(n => data.readUInt32LE(n));
  ensure(RECORD[version] && vertices > 0 && vertices <= 65535 && indices <= 3000000 &&
    indices % 3 === 0 && count <= 2048 && textures <= 512, 'Unsupported GMDL header');
  const vertexOffset = 24 + RECORD[version] * count + textures * 64;
  const indexOffset = vertexOffset + vertices * STRIDE;
  const geometryEnd = indexOffset + indices * 2;
  ensure(data.length >= geometryEnd, 'Truncated GMDL bytes');
  let sourceVertexIds = null, originalVertices = vertices;
  if (data.length !== geometryEnd) {
    ensure(data.length >= geometryEnd + 16 && data.toString('ascii', geometryEnd, geometryEnd + 4) === 'O3RM' &&
      data.readUInt32LE(geometryEnd + 4) === 1, 'Invalid or trailing GMDL remap bytes');
    originalVertices = data.readUInt32LE(geometryEnd + 8);
    ensure(originalVertices >= vertices && originalVertices <= 65535 && data.readUInt32LE(geometryEnd + 12) === vertices &&
      data.length === geometryEnd + 16 + vertices * 4, 'Invalid GMDL remap count');
    sourceVertexIds = Uint32Array.from({ length: vertices }, (_, i) => data.readUInt32LE(geometryEnd + 16 + i * 4));
    for (let i = 0; i < vertices; ++i) ensure(sourceVertexIds[i] < originalVertices &&
      (i === 0 || sourceVertexIds[i] > sourceVertexIds[i - 1]), 'Invalid GMDL remap source vertex');
  }
  const submeshes = [];
  let next = 0;
  for (let n = 0; n < count; ++n) {
    const offset = 24 + n * RECORD[version];
    const first = data.readUInt32LE(offset), size = data.readUInt32LE(offset + 4);
    // Authored cooker emits a complete, contiguous triangle partition. Refuse
    // arbitrary overlapping spans: these can encode effects with different semantics.
    ensure(first === next && size % 3 === 0 && first + size <= indices, 'Noncontiguous submesh indices');
    for (const field of [8, 16]) {
      const texture = data.readInt32LE(offset + field);
      ensure(texture >= -1 && texture < textures, 'Invalid GMDL texture index');
    }
    submeshes.push({ first, count: size, offset, flags: data.readUInt32LE(offset + 12) });
    next += size;
  }
  ensure(next === indices, 'Unclaimed GMDL indices');
  for (let n = 0; n < textures; ++n) {
    const raw = data.subarray(24 + count * RECORD[version] + n * 64, 24 + count * RECORD[version] + (n + 1) * 64);
    const end = raw.indexOf(0), name = raw.toString('utf8', 0, end < 0 ? raw.length : end);
    ensure(end >= 0 && !/[\\/]/.test(name) && !name.includes('..'), 'Invalid GMDL texture name');
  }
  const positions = new Float32Array(vertices * 3), attributes = new Float32Array(vertices * 11);
  for (let i = 0; i < vertices; ++i) {
    const at = vertexOffset + i * STRIDE;
    for (let j = 0; j < 10; ++j) {
      const value = data.readFloatLE(at + j * 4);
      ensure(Number.isFinite(value), 'Nonfinite GMDL vertex');
      if (j < 3) positions[i * 3 + j] = value;
      else attributes[i * 11 + j - 3] = value;
    }
    for (let j = 0; j < 4; ++j) attributes[i * 11 + 7 + j] = data[at + 40 + j] / 255;
  }
  const indexData = Uint32Array.from({ length: indices }, (_, i) => data.readUInt16LE(indexOffset + i * 2));
  for (const i of indexData) ensure(i < vertices, 'GMDL index out of bounds');
  return { data, version, vertices, originalVertices, sourceVertexIds, indices, submeshes, textures,
    vertexOffset, indexOffset, positions, attributes, indexData };
}

// Binding bytes are incorporated into vertex equivalence, including the source
// position/normal (not just matrix id). A neutral pose can hide different binds.
export function parseBinding(data, model) {
  ensure(data.length >= 28, 'Truncated animation bank');
  const magic = data.toString('ascii', 0, 4), version = data.readUInt32LE(4);
  const count = data.readUInt32LE(8), matrices = data.readUInt32LE(12);
  let start, shapes;
  if (magic === 'DSKM' && version >= 2 && version <= 5) {
    start = version === 5 ? 32 : 28;
    shapes = data.readUInt32LE(20);
    ensure(count === model.vertices, 'DSKM/model vertex mismatch');
  } else if (magic === 'DOBJ' && version === 1) {
    start = 40; shapes = data.readUInt32LE(16);
    ensure(count === 0 || count === model.vertices, 'DOBJ/model vertex mismatch');
  } else throw new Error('Unsupported animation bank');
  ensure(matrices > 0 && matrices <= 128 && shapes <= 256 && data.length >= start + count * 28 + shapes * 8,
    'Invalid animation binding header');
  // A material-only DOBJ has no vertex transforms; submeshes remain separate.
  if (!count) return null;
  const rangeIds = Array.from({ length: count }, () => []);
  const matrixIds = new Uint32Array(count);
  for (let i = 0; i < count; ++i) {
    for (let j = 0; j < 6; ++j) ensure(Number.isFinite(data.readFloatLE(start + i * 28 + j * 4)), 'Nonfinite skin binding');
    matrixIds[i] = data.readUInt32LE(start + i * 28 + 24);
    ensure(matrixIds[i] < matrices, 'Invalid skin matrix');
  }
  for (let n = 0; n < shapes; ++n) {
    const at = start + count * 28 + n * 8, first = data.readUInt32LE(at), size = data.readUInt32LE(at + 4);
    ensure(first <= count && size <= count - first, 'Invalid animation shape range');
    for (let i = first; i < first + size; ++i) rangeIds[i].push(n);
  }
  const groups = rangeIds.map(ranges => ranges.join(','));
  return { format: magic, matrixIds, groups, keys: groups.map((group, i) => `${group}:${data.subarray(start + i * 28, start + (i + 1) * 28).toString('hex')}`) };
}

// Palette slot numbers are meaningful only for this exact vertex binding and
// shape layout. Clip names/counts are deliberately excluded so vehicle aliases
// can share verified semantic metadata without copying any game geometry.
export function bindingFingerprint(data) {
  ensure(data.length >= 28 && data.toString('ascii', 0, 4) === 'DSKM', 'Character regions require DSKM');
  const version = data.readUInt32LE(4), count = data.readUInt32LE(8), shapes = data.readUInt32LE(20);
  const start = version === 5 ? 32 : 28, end = start + count * 28 + shapes * 8;
  ensure(version >= 2 && version <= 5 && end <= data.length, 'Truncated character binding');
  return sha(Buffer.concat([data.subarray(12, 16), data.subarray(start, end)]));
}

function facialShapeIds(data) {
  const version = data.readUInt32LE(4), vertices = data.readUInt32LE(8), matrices = data.readUInt32LE(12);
  const clips = data.readUInt32LE(16), shapes = data.readUInt32LE(20), faces = data.readUInt32LE(24);
  let cursor = (version === 5 ? 32 : 28) + vertices * 28 + shapes * 8;
  const result = new Set();
  const take = bytes => { ensure(Number.isSafeInteger(bytes) && bytes >= 0 && cursor + bytes <= data.length, 'Truncated facial animation'); const at = cursor; cursor += bytes; return at; };
  const clip = () => { const at = take(44), count = data.readUInt32LE(at + 40); ensure(count > 0 && count <= 2000, 'Invalid facial animation count'); return count; };
  for (let i = 0; i < clips; ++i) take(clip() * (matrices * 84 + shapes * 4));
  for (let i = 0; i < faces; ++i) {
    const frames = clip(), at = take(frames * shapes * 4);
    for (let f = 0; f < frames; ++f) for (let s = 0; s < shapes; ++s)
      if (data.readInt32LE(at + (f * shapes + s) * 4) >= 0) result.add(s);
  }
  if (version >= 3) {
    const at = take(12); ensure(data.toString('ascii', at, at + 4) === 'HLOC', 'Missing hair animation metadata');
    const nodes = data.readUInt32LE(at + 4), count = data.readUInt32LE(at + 8); take(nodes * 8);
    for (let i = 0; i < count; ++i) take((clip() + 1) * nodes * 84);
  }
  if (version >= 4) {
    const at = take(8); ensure(data.toString('ascii', at, at + 4) === 'UVAN', 'Missing facial UV metadata');
    const count = data.readUInt32LE(at + 4), identity = [1, 0, 0, 0, 1, 0];
    for (let i = 0; i < count; ++i) {
      const frames = clip(), start = take(frames * shapes * 24);
      for (let f = 0; f < frames; ++f) for (let s = 0; s < shapes; ++s) if (!result.has(s))
        for (let k = 0; k < 6; ++k) if (Math.abs(data.readFloatLE(start + (f * shapes + s) * 24 + k * 4) - identity[k]) > 1e-5) { result.add(s); break; }
    }
  }
  return result;
}

function protectFacialFront(model, binding, headBones, faceShapes, locks) {
  const faceVertices = new Set();
  for (const shape of faceShapes) {
    ensure(shape < model.submeshes.length, 'Facial shape is outside model materials');
    const span = model.submeshes[shape];
    for (const i of model.indexData.subarray(span.first, span.first + span.count)) { locks[i] |= 1; faceVertices.add(i); }
  }
  const normal = [0, 0, 0];
  for (const i of faceVertices) for (let axis = 0; axis < 3; ++axis) normal[axis] += model.attributes[i * 11 + axis];
  const length = Math.hypot(...normal);
  // No trustworthy facial plane: preserve the whole head. Never invent a
  // forward direction for opaque sunglasses or a differently posed skeleton.
  if (length < Math.max(1, faceVertices.size) * 0.25) {
    for (let i = 0; i < model.vertices; ++i) if (headBones.has(binding.matrixIds[i])) locks[i] |= 1;
    return { facialPlanes: faceShapes.size, frontalPlane: false };
  }
  for (let axis = 0; axis < 3; ++axis) normal[axis] /= length;
  const project = i => normal.reduce((sum, value, axis) => sum + value * model.positions[i * 3 + axis], 0);
  const plane = [...faceVertices].reduce((sum, i) => sum + project(i), 0) / faceVertices.size;
  let low = Infinity, high = -Infinity;
  for (let i = 0; i < model.vertices; ++i) if (headBones.has(binding.matrixIds[i])) { const d = project(i); low = Math.min(low, d); high = Math.max(high, d); }
  const front = plane - (high - low) * 0.15;
  for (let i = 0; i < model.vertices; ++i) if (headBones.has(binding.matrixIds[i]) && project(i) >= front) locks[i] |= 1;
  return { facialPlanes: faceShapes.size, frontalPlane: true, frontNormal: normal, frontThreshold: front };
}

function characterSeamVertices(model, binding, protectedBones) {
  const locks = new Uint8Array(model.vertices), groups = new Map(), pointIds = new Uint32Array(model.vertices);
  const minimum = [Infinity, Infinity, Infinity], maximum = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < model.vertices; ++i) for (let axis = 0; axis < 3; ++axis) {
    minimum[axis] = Math.min(minimum[axis], model.positions[i * 3 + axis]);
    maximum[axis] = Math.max(maximum[axis], model.positions[i * 3 + axis]);
  }
  // This tolerance only adds locks. It never welds or moves source vertices.
  const epsilon = Math.max(1e-6, Math.max(...maximum.map((v, i) => v - minimum[i])) * 1e-6);
  const materialOf = new Int32Array(model.vertices).fill(-1);
  model.submeshes.forEach((span, material) => {
    for (const i of model.indexData.subarray(span.first, span.first + span.count)) materialOf[i] = material;
  });
  for (let i = 0; i < model.vertices; ++i) {
    const key = [0, 1, 2].map(axis => Math.round(model.positions[i * 3 + axis] / epsilon)).join(',');
    if (!groups.has(key)) groups.set(key, { id: groups.size, vertices: [], seam: false, hardSeam: false });
    const group = groups.get(key); pointIds[i] = group.id;
    if (group.vertices.length) {
      const first = group.vertices[0];
      group.hardSeam ||= materialOf[first] !== materialOf[i] || binding.matrixIds[first] !== binding.matrixIds[i] || binding.groups[first] !== binding.groups[i];
      group.seam ||= [3, 4].some(k => Math.abs(model.attributes[first * 11 + k] - model.attributes[i * 11 + k]) > 1e-4);
    }
    group.vertices.push(i);
    if (protectedBones.has(binding.matrixIds[i])) locks[i] = 1;
  }
  const edges = new Map(), borderPoints = new Set();
  for (let n = 0; n < model.indices; n += 3) {
    const triangle = [0, 1, 2].map(k => pointIds[model.indexData[n + k]]);
    for (let k = 0; k < 3; ++k) {
      const a = triangle[k], b = triangle[(k + 1) % 3];
      if (a === b) continue;
      const key = a < b ? `${a}:${b}` : `${b}:${a}`;
      edges.set(key, (edges.get(key) || 0) + 1);
    }
  }
  for (const [key, count] of edges) if (count !== 2) for (const id of key.split(':')) borderPoints.add(Number(id));
  for (const group of groups.values()) for (const i of group.vertices) {
    if (group.hardSeam || borderPoints.has(group.id)) locks[i] |= 1;
    // meshoptimizer's Protect flag preserves an attribute seam but permits
    // coordinated collapses along it. Locking every UV seam instead prevents
    // almost all body simplification on the retail texture atlases.
    else if (group.seam) locks[i] |= 2;
  }
  // Facial planes, eyelashes, hair cards and other alpha outlines are too
  // small and semantically important to approximate by percentage.
  for (const span of model.submeshes) if (span.flags & 3)
    for (const i of model.indexData.subarray(span.first, span.first + span.count)) locks[i] = 1;
  return locks;
}

function vertexKey(model, index, binding) {
  const at = model.vertexOffset + index * STRIDE;
  return model.data.subarray(at, at + STRIDE).toString('hex') + (binding ? `:${binding.keys[index]}` : '');
}

// This only detects newly exposed points inside formerly closed surfaces.
// Original open cards/boundaries remain legal, and visual review is still
// required for lost components or a new edge joining old boundary points.
export function newlyExposedBoundaryPoints(original, candidate) {
  const low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < original.vertices; ++i) for (let axis = 0; axis < 3; ++axis) {
    low[axis] = Math.min(low[axis], original.positions[i * 3 + axis]);
    high[axis] = Math.max(high[axis], original.positions[i * 3 + axis]);
  }
  const epsilon = Math.max(1e-6, ...high.map((value, i) => (value - low[i]) * 1e-6));
  const points = new Map(), ids = new Uint32Array(original.vertices);
  for (let i = 0; i < original.vertices; ++i) {
    const key = [0, 1, 2].map(axis => Math.round(original.positions[i * 3 + axis] / epsilon)).join(',');
    if (!points.has(key)) points.set(key, points.size);
    ids[i] = points.get(key);
  }
  const boundaries = (indices, remap) => {
    const edges = new Map(), result = new Set();
    for (let n = 0; n < indices.length; n += 3) {
      const triangle = [0, 1, 2].map(k => ids[remap ? remap[indices[n + k]] : indices[n + k]]);
      if (new Set(triangle).size < 3) continue;
      for (let k = 0; k < 3; ++k) {
        const a = triangle[k], b = triangle[(k + 1) % 3], key = a < b ? `${a}:${b}` : `${b}:${a}`;
        edges.set(key, (edges.get(key) || 0) + 1);
      }
    }
    for (const [edge, count] of edges) if (count === 1) for (const point of edge.split(':')) result.add(Number(point));
    return result;
  };
  const before = boundaries(original.indexData, null), after = boundaries(candidate.indexData, candidate.sourceVertexIds);
  return [...after].filter(id => !before.has(id)).length;
}

function materialBoundaryVertices(model) {
  const firstMaterial = new Map(), shared = new Set();
  const key = i => model.data.subarray(model.vertexOffset + i * STRIDE, model.vertexOffset + i * STRIDE + 12).toString('hex');
  model.submeshes.forEach((span, material) => {
    for (const i of model.indexData.subarray(span.first, span.first + span.count)) {
      const position = key(i);
      if (firstMaterial.has(position) && firstMaterial.get(position) !== material) shared.add(position);
      else firstMaterial.set(position, material);
    }
  });
  return new Uint8Array(Array.from({ length: model.vertices }, (_, i) => shared.has(key(i)) ? 1 : 0));
}

function featureVertices(model, binding, protectExtrema = true) {
  const points = [], positionMap = new Map(), pointIds = new Uint32Array(model.vertices);
  for (let i = 0; i < model.vertices; ++i) {
    const position = Array.from(model.positions.subarray(i * 3, i * 3 + 3)), key = position.join(',');
    if (!positionMap.has(key)) {
      positionMap.set(key, points.length);
      points.push({ position, angle: 0, edges: new Map(), triangles: 0, locked: false });
    }
    pointIds[i] = positionMap.get(key);
  }
  // A closed cone tip has a large angle deficit. Protect those tips so horns,
  // shell spikes, ears and scenery spires survive the reduced silhouette.
  for (let n = 0; n < model.indices; n += 3) {
    const ids = Array.from(model.indexData.subarray(n, n + 3), i => pointIds[i]);
    if (new Set(ids).size !== 3) continue;
    for (let c = 0; c < 3; ++c) {
      const p = points[ids[c]], a = points[ids[(c + 1) % 3]], b = points[ids[(c + 2) % 3]];
      const u = a.position.map((v, j) => v - p.position[j]), v = b.position.map((value, j) => value - p.position[j]);
      const lengths = Math.hypot(...u) * Math.hypot(...v);
      if (!lengths) continue;
      p.angle += Math.acos(Math.max(-1, Math.min(1, u.reduce((sum, value, j) => sum + value * v[j], 0) / lengths)));
      ++p.triangles;
      for (const neighbor of [ids[(c + 1) % 3], ids[(c + 2) % 3]]) p.edges.set(neighbor, (p.edges.get(neighbor) || 0) + 1);
    }
  }
  for (const p of points) p.locked = p.triangles >= 3 && p.angle < 4.0 && [...p.edges.values()].every(count => count === 2);
  // Each articulated part keeps its six extremities; head/nose/hat bounds must
  // not be traded for a few triangles. Preserve tied extrema symmetrically.
  const regions = new Map();
  for (const i of model.indexData) {
    const region = binding ? `${binding.groups[i]}:${binding.matrixIds[i]}` : 'static';
    if (!regions.has(region)) regions.set(region, new Set());
    regions.get(region).add(pointIds[i]);
  }
  if (protectExtrema) for (const ids of regions.values()) for (let axis = 0; axis < 3; ++axis) {
    let min = Infinity, max = -Infinity;
    for (const id of ids) { min = Math.min(min, points[id].position[axis]); max = Math.max(max, points[id].position[axis]); }
    if (max - min < 1e-6) continue;
    const epsilon = (max - min) * 1e-6;
    for (const id of ids) if (Math.abs(points[id].position[axis] - min) <= epsilon ||
      Math.abs(points[id].position[axis] - max) <= epsilon) points[id].locked = true;
  }
  return new Uint8Array(Array.from(pointIds, id => points[id].locked ? 1 : 0));
}

function simplifySpan(model, submesh, binding, options, materialLocks) {
  const original = model.indexData.slice(submesh.first, submesh.first + submesh.count);
  const report = { originalTriangles: original.length / 3, candidateTriangles: original.length / 3, error: 0 };
  if (options.preserveTopology) return { indices: original, report: { ...report, reason: 'preserve-authored-topology' } };
  const aggressive = options.profile === 'aggressive';
  if (!aggressive && (submesh.flags & 3)) return { indices: original, report: { ...report, reason: 'alpha-cutout-or-blended' } };
  if (original.length / 3 < options.minTriangles) return { indices: original, report: { ...report, reason: 'small-detail' } };
  const remap = new Map(), localToOriginal = [], indices = new Uint32Array(original.length);
  for (let n = 0; n < original.length; ++n) {
    const i = original[n], key = vertexKey(model, i, binding);
    if (!remap.has(key)) { remap.set(key, localToOriginal.length); localToOriginal.push(i); }
    indices[n] = remap.get(key);
  }
  const positions = new Float32Array(localToOriginal.length * 3), attributes = new Float32Array(localToOriginal.length * 11);
  for (let n = 0; n < localToOriginal.length; ++n) {
    const i = localToOriginal[n];
    positions.set(model.positions.subarray(i * 3, i * 3 + 3), n * 3);
    attributes.set(model.attributes.subarray(i * 11, i * 11 + 11), n * 11);
  }
  const locks = new Uint8Array(localToOriginal.length);
  // A welded vertex is locked when any authored duplicate is (road locks name
  // every vertex of every drivable triangle, whichever duplicate welds first).
  if (aggressive) for (let n = 0; n < original.length; ++n) locks[indices[n]] |= materialLocks[original[n]];
  if (binding) {
    // Do not collapse joints or change the connectivity of deforming triangles.
    // Interiors of a rigidly influenced region can still be simplified.
    for (let n = 0; n < indices.length; n += 3) {
      const a = indices[n], b = indices[n + 1], c = indices[n + 2];
      const ma = binding.matrixIds[localToOriginal[a]], mb = binding.matrixIds[localToOriginal[b]], mc = binding.matrixIds[localToOriginal[c]];
      const ga = binding.groups[localToOriginal[a]], gb = binding.groups[localToOriginal[b]], gc = binding.groups[localToOriginal[c]];
      if (ma !== mb || ma !== mc || ga !== gb || ga !== gc) locks[a] = locks[b] = locks[c] = 1;
    }
  }
  const target = Math.max(3, Math.floor(original.length / 3 * options.ratio) * 3);
  // Aggressive mode accepts facets and simplifies UV/normal seams using their
  // attribute error. It still preserves material joins, animated joints, every
  // component and exact retained vertex/binding bytes. Alpha outlines stay
  // locked: collapsing a cutout's edge can visibly clip its texture silhouette.
  // Seamless (Old 3DS levels): every open border stays put, so no crack can
  // open between pieces, and UV/normal seams only collapse along themselves.
  // freeBorders: open borders that touch nothing may slide along themselves
  // (touching ones arrive as vertex locks); alpha cards keep every border.
  const flags = options.seamless && options.freeBorders && !(submesh.flags & 3) ?
    ['RegularizeLight', ...(options.absoluteError ? ['ErrorAbsolute'] : [])] :
    options.seamless ? ['LockBorder', 'RegularizeLight', ...(options.absoluteError ? ['ErrorAbsolute'] : [])] :
    aggressive ? ['Permissive', 'RegularizeLight', ...(options.characterSafety || submesh.flags & 3 ? ['LockBorder'] : [])] :
    ['LockBorder', 'PreserveFolds'];
  const weights = options.characterRegion === 'head' ? [0.8, 0.8, 0.8, 1.5, 1.5, 1, 1, 0.75, 0.75, 0.75, 0.75] : options.characterSafety ? [0.35, 0.35, 0.35, 0.75, 0.75, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5] : aggressive ? (options.articulated ? [0.5, 0.5, 0.5, 0.75, 0.75, 0.75, 0.75, 0.5, 0.5, 0.5, 0.5] :
    [0.25, 0.25, 0.25, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]) :
    [1, 1, 1, 2, 2, 2, 2, 1, 1, 1, 1];
  const limit = options.absoluteError || options.maxError;
  const [simplified, error] = MeshoptSimplifier.simplifyWithAttributes(
    indices, positions, 3, attributes, 11, weights, locks, target, limit, flags);
  // A tiny part simplified away entirely keeps its authored triangles.
  if (simplified.length === 0) return { indices: original, report: { ...report, reason: 'would-vanish' } };
  ensure(simplified.length % 3 === 0 && simplified.length <= original.length &&
    Number.isFinite(error) && error <= limit * (1 + 1e-6) + 1e-6, 'Simplifier violated its error/count limits');
  const result = Uint32Array.from(simplified, i => {
    ensure(i < localToOriginal.length, 'Simplifier returned an invalid vertex');
    return localToOriginal[i];
  });
  // If no triangles were removed, keep exact authored topology and ordering.
  if (result.length === original.length) return { indices: original, report: { ...report, reason: 'quality-or-topology-limit' } };
  return { indices: result, report: { ...report, candidateTriangles: result.length / 3, error,
    lockedVertices: locks.reduce((a, b) => a + b, 0), reason: 'reduced' } };
}

function resolveOptions(config) {
  const options = { ...(config.profile === 'aggressive' ? AGGRESSIVE_DEFAULTS : DEFAULTS), ...config };
  ensure(['conservative', 'aggressive'].includes(options.profile), 'profile must be conservative or aggressive');
  ensure(Number.isFinite(options.ratio) && options.ratio > 0 && options.ratio < 1, 'ratio must be greater than 0 and less than 1');
  const maxError = options.profile === 'aggressive' ? 0.15 : 0.02;
  ensure(Number.isFinite(options.maxError) && options.maxError > 0 && options.maxError <= maxError,
    `max-error must be greater than 0 and at most ${maxError}`);
  ensure(Number.isInteger(options.minTriangles) && options.minTriangles >= 1, 'min-triangles must be a positive integer');
  ensure(options.protectFeatures === undefined || typeof options.protectFeatures === 'boolean', 'protect-features must be true or false');
  ensure(options.characterError === undefined || (Number.isFinite(options.characterError) && options.characterError > 0 && options.characterError <= maxError),
    'character-error must be a positive error within the profile limit');
  ensure(options.characterHeadMode === undefined || ['preserve', 'balanced'].includes(options.characterHeadMode), 'character-head-mode must be preserve or balanced');
  ensure(options.characterSafety === undefined || typeof options.characterSafety === 'boolean', 'character-safety must be true or false');
  ensure(options.faceBones === undefined || (Array.isArray(options.faceBones) && options.faceBones.every(n => Number.isInteger(n) && n >= 0)), 'Invalid facial bone list');
  ensure(options.extremityBones === undefined || (Array.isArray(options.extremityBones) && options.extremityBones.every(n => Number.isInteger(n) && n >= 0)), 'Invalid extremity bone list');
  for (const key of ['preserveTopology', 'protectBoundaries']) ensure(options[key] === undefined || typeof options[key] === 'boolean', `Invalid ${key} option`);
  ensure(options.roadLocks === undefined || Array.isArray(options.roadLocks), 'Invalid road locks');
  ensure(options.seamless === undefined || typeof options.seamless === 'boolean', 'Invalid seamless option');
  ensure(options.freeBorders === undefined || typeof options.freeBorders === 'boolean', 'Invalid free-borders option');
  ensure(options.absoluteError === undefined || (Number.isFinite(options.absoluteError) && options.absoluteError > 0),
    'absolute-error must be a positive distance');
  return options;
}

export async function simplifyModel(data, bank = null, config = {}) {
  const options = resolveOptions(config);
  await MeshoptSimplifier.ready;
  const model = parseModel(data), binding = bank ? parseBinding(bank, model) : null;
  ensure(!model.sourceVertexIds, 'Input must be an original authored model, not a compacted variant');
  const materialLocks = options.profile === 'aggressive' ? materialBoundaryVertices(model) : null;
  // Old 3DS courses: vertices on drivable collision stay exactly authored.
  if (materialLocks && options.roadLocks) for (const i of options.roadLocks) {
    ensure(Number.isInteger(i) && i >= 0 && i < model.vertices, 'Invalid road lock vertex');
    materialLocks[i] |= 1;
  }
  let characterPolicy = null, headBones = null;
  if (materialLocks && options.characterSafety) {
    ensure(binding?.format === 'DSKM', 'Character safety requires authored driver bindings');
    ensure(options.protectedBones === undefined || (Array.isArray(options.protectedBones) && options.protectedBones.length > 0 &&
      options.protectedBones.every(n => Number.isInteger(n) && n >= 0 && n < bank.readUInt32LE(12))), 'Invalid protected character bones');
    // Unknown semantic layouts retain their topology instead of guessing where
    // a face ends. Exact duplicate compaction can still save vertex work.
    headBones = new Set(options.protectedBones || binding.matrixIds);
    const balanced = options.characterHeadMode === 'balanced' && options.protectedBones && Array.isArray(options.faceBones);
    const fixedBones = balanced ? new Set([...headBones].filter(i => !options.faceBones.includes(i))) : headBones;
    for (const bone of options.extremityBones || []) {
      ensure(bone < bank.readUInt32LE(12), 'Invalid extremity bone index');
      fixedBones.add(bone);
    }
    const seamLocks = characterSeamVertices(model, binding, fixedBones), features = featureVertices(model, binding, false);
    let facialPolicy = null;
    if (balanced) {
      facialPolicy = protectFacialFront(model, binding, headBones, facialShapeIds(bank), seamLocks);
      const extrema = featureVertices(model, binding);
      for (let i = 0; i < model.vertices; ++i) if (headBones.has(binding.matrixIds[i])) seamLocks[i] |= extrema[i];
    }
    for (let i = 0; i < model.vertices; ++i) materialLocks[i] |= seamLocks[i] | features[i];
    options.ratio = Math.max(options.ratio, 0.3);
    options.maxError = Math.min(options.maxError, 0.06);
    options.articulated = true;
    characterPolicy = { method: options.protectedBones ? balanced ? 'verified-facial-front-and-seams' : 'verified-head-bones-and-seams' : 'unmapped-preserve-topology',
      protectedBones: [...headBones].sort((a, b) => a - b), lockedVertices: materialLocks.reduce((a, b) => a + Boolean(b & 1), 0),
      protectedSeamVertices: materialLocks.reduce((a, b) => a + Boolean(b & 2), 0),
      protectedExtremities: options.extremityBones || [],
      bodyTargetRatio: options.ratio, maxError: options.maxError, ...(facialPolicy || {}) };
  }
  if (materialLocks && options.protectFeatures) {
    const featureLocks = featureVertices(model, binding);
    for (let i = 0; i < model.vertices; ++i) materialLocks[i] |= featureLocks[i];
    options.articulated = Boolean(binding && new Set(binding.matrixIds).size >= 6);
    if (options.articulated) options.maxError = Math.min(options.maxError, options.characterError || 0.08);
  }
  const spans = model.submeshes.map(submesh => {
    if (!characterPolicy || options.characterHeadMode !== 'balanced') return simplifySpan(model, submesh, binding, options, materialLocks);
    const regions = { head: [], body: [] };
    for (let n = submesh.first; n < submesh.first + submesh.count; n += 3) {
      const triangle = Array.from(model.indexData.subarray(n, n + 3));
      regions[triangle.some(i => headBones.has(binding.matrixIds[i])) ? 'head' : 'body'].push(...triangle);
    }
    const results = {};
    for (const [region, indices] of Object.entries(regions)) {
      if (!indices.length) continue;
      const localModel = { ...model, indexData: Uint32Array.from(indices) };
      results[region] = simplifySpan(localModel, { ...submesh, first: 0, count: indices.length }, binding,
        { ...options, characterRegion: region, ...(region === 'head' ? { ratio: 0.65, maxError: Math.min(options.maxError, 0.03) } : {}) }, materialLocks);
    }
    const indices = Uint32Array.from(Object.values(results).flatMap(r => [...r.indices]));
    const report = { originalTriangles: submesh.count / 3, candidateTriangles: indices.length / 3,
      error: Math.max(0, ...Object.values(results).map(r => r.report.error)), reason: indices.length < submesh.count ? 'reduced' : 'quality-or-topology-limit',
      regions: Object.fromEntries(Object.entries(results).map(([key, value]) => [key, value.report])) };
    return { indices, report };
  });
  let sourceVertexIds = null;
  if (options.profile === 'aggressive') {
    // Weld only identical render + animation vertices within each original
    // material span. Cross-material welding would lose source membership.
    for (const span of spans) {
      const equivalents = new Map();
      span.indices = Uint32Array.from(span.indices, i => {
        const key = vertexKey(model, i, binding);
        if (!equivalents.has(key)) equivalents.set(key, i);
        return equivalents.get(key);
      });
    }
    sourceVertexIds = [...new Set(spans.flatMap(span => [...span.indices]))].sort((a, b) => a - b);
    if (sourceVertexIds.length === 0 || sourceVertexIds.length === model.vertices) sourceVertexIds = null;
  }
  const count = spans.reduce((n, span) => n + span.indices.length, 0);
  const vertices = sourceVertexIds ? sourceVertexIds.length : model.vertices;
  const indexOffset = model.vertexOffset + vertices * STRIDE, geometryEnd = indexOffset + count * 2;
  const output = Buffer.alloc(geometryEnd + (sourceVertexIds ? 16 + vertices * 4 : 0));
  model.data.copy(output, 0, 0, model.vertexOffset);
  const sourceToCompact = sourceVertexIds ? new Map(sourceVertexIds.map((id, i) => [id, i])) : null;
  if (sourceVertexIds) sourceVertexIds.forEach((id, i) =>
    model.data.copy(output, model.vertexOffset + i * STRIDE, model.vertexOffset + id * STRIDE, model.vertexOffset + (id + 1) * STRIDE));
  else model.data.copy(output, model.vertexOffset, model.vertexOffset, model.indexOffset);
  output.writeUInt32LE(vertices, 8);
  output.writeUInt32LE(count, 12);
  let first = 0;
  spans.forEach((span, n) => {
    output.writeUInt32LE(first, model.submeshes[n].offset);
    output.writeUInt32LE(span.indices.length, model.submeshes[n].offset + 4);
    for (const index of span.indices) output.writeUInt16LE(sourceToCompact ? sourceToCompact.get(index) : index, indexOffset + 2 * first++);
  });
  if (sourceVertexIds) {
    output.write('O3RM', geometryEnd); output.writeUInt32LE(1, geometryEnd + 4);
    output.writeUInt32LE(model.vertices, geometryEnd + 8); output.writeUInt32LE(vertices, geometryEnd + 12);
    sourceVertexIds.forEach((id, i) => output.writeUInt32LE(id, geometryEnd + 16 + i * 4));
  }
  const candidateModel = parseModel(output);
  if (options.protectBoundaries && !options.preserveTopology) {
    const exposed = newlyExposedBoundaryPoints(model, candidateModel);
    if (exposed) {
      const fallback = await simplifyModel(data, bank, { ...config, preserveTopology: true });
      fallback.report.topologyFallback = { reason: 'newly-exposed-interior-boundary', rejectedBoundaryPoints: exposed,
        rejectedTriangles: count / 3, retainedTopology: true };
      return fallback;
    }
  }
  return { output, report: { originalSha256: sha(data), candidateSha256: sha(output),
    originalBytes: data.length, candidateBytes: output.length, vertices: model.vertices, candidateVertices: vertices,
    compacted: Boolean(sourceVertexIds),
    qualityClass: options.articulated ? 'articulated' : 'general', effectiveMaxError: options.maxError,
    ...(characterPolicy ? { characterPolicy } : {}),
    originalTriangles: model.indices / 3, candidateTriangles: count / 3,
    submeshes: spans.map(span => span.report), skinned: Boolean(binding),
    ...(bank ? { animationSha256: sha(bank) } : {}) } };
}

function inputListing(options) {
  if (options.listing) {
    const listing = JSON.parse(fs.readFileSync(options.listing, 'utf8'));
    ensure(listing && typeof listing === 'object' && !Array.isArray(listing), 'listing must map relative asset paths to source paths');
    for (const [relative, source] of Object.entries(listing)) ensure(typeof source === 'string' &&
      !path.posix.isAbsolute(relative) && !relative.split('/').includes('..') && !relative.includes('\\'), 'Invalid package listing path');
    return listing;
  }
  const folder = path.resolve(options.assets, 'models'), listing = {};
  for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
    if (entry.isFile() && !entry.name.startsWith('._')) listing[`models/${entry.name}`] = path.join(folder, entry.name);
  }
  return listing;
}

function bankMapping(listing) {
  const banks = new Map();
  for (const [relative, source] of Object.entries(listing)) {
    if (!/^models\/course_\d+_visuals\.list$/.test(relative)) continue;
    for (const line of fs.readFileSync(source, 'utf8').split(/\r?\n/).filter(Boolean)) {
      const fields = line.trim().split(/\s+/);
      ensure(fields.length === 4 && /^[a-z0-9_]+$/.test(fields[2]) && /^(?:-|[a-z0-9_]+)$/.test(fields[3]), 'Invalid course visuals entry');
      if (fields[3] !== '-') banks.set(`models/${fields[2]}.gmdl`, `models/${fields[3]}.dobj`);
    }
  }
  // The blast overlay intentionally shares its original bomb_core pose bank.
  if (listing['models/bomb_blast.gmdl']) banks.set('models/bomb_blast.gmdl', 'models/bomb_core.dskm');
  for (const relative of Object.keys(listing).filter(n => /^models\/[^/]+\.gmdl$/.test(n))) {
    if (banks.has(relative)) continue;
    const stem = relative.slice(0, -5), stems = [stem, stem.replace(/_000$/, '')];
    const candidates = [...new Set(stems.flatMap(s => [s + '.dskm', s + '.dobj']).filter(n => listing[n]))];
    ensure(candidates.length <= 1, `Ambiguous animation bindings: ${relative}`);
    if (candidates.length) banks.set(relative, candidates[0]);
  }
  for (const bank of banks.values()) ensure(listing[bank], `Missing animation binding: ${bank}`);
  return banks;
}

export async function cook(options) {
  const listing = inputListing(options), banks = bankMapping(listing);
  const output = path.resolve(options.output);
  const names = Object.keys(listing).filter(n => /^models\/[a-zA-Z0-9_-]+\.gmdl$/.test(n)).sort();
  ensure(names.length > 0, 'No cooked GMDL models found');
  for (const source of Object.values(listing)) ensure(!path.resolve(source).startsWith(output + path.sep), 'Output must be separate from source assets');
  // Rebuild only inside the dedicated sidecar directory; originals are never copied.
  const sidecars = path.join(output, 'models', 'o3ds');
  fs.mkdirSync(sidecars, { recursive: true });
  const receiptPath = path.join(output, 'receipt.json');
  // Remove the success receipt first so an interrupted run cannot be mistaken for complete.
  fs.rmSync(receiptPath, { force: true });
  for (const entry of fs.readdirSync(sidecars)) {
    ensure(/^[a-zA-Z0-9_-]+\.gmdl$/.test(entry), `Unexpected file in generated directory: ${entry}`);
    fs.unlinkSync(path.join(sidecars, entry));
  }
  const config = resolveOptions(options.config || {});
  const regions = options.characterRegions ? JSON.parse(fs.readFileSync(options.characterRegions, 'utf8')) : null;
  const roads = options.roadLocks ? JSON.parse(fs.readFileSync(options.roadLocks, 'utf8')) : null;
  ensure(!roads || (roads.version === 1 && roads.locks && typeof roads.locks === 'object'), 'Invalid road lock metadata');
  ensure(!regions || (regions.version === 1 && regions.entries && typeof regions.entries === 'object' && !Array.isArray(regions.entries)), 'Invalid character region metadata');
  const receipt = { version: 1, experimental: true, releaseEnabled: false, hardwareFpsValidated: false,
    algorithm: 'meshoptimizer@1.3.0 simplifyWithAttributes', options: config,
    cookerSha256: sha(fs.readFileSync(fileURLToPath(import.meta.url))),
    vertexBuffersUnchanged: config.profile !== 'aggressive', cpuSkinningWorkReduced: config.profile === 'aggressive',
    remapFormat: config.profile === 'aggressive' ? 'O3RM-v1' : null,
    characterRegionsSha256: options.characterRegions ? sha(fs.readFileSync(options.characterRegions)) : null,
    roadLocksSha256: options.roadLocks ? sha(fs.readFileSync(options.roadLocks)) : null,
    qualityReviewRequired: true, models: {} };
  for (const relative of names) {
    const bankPath = banks.get(relative), bank = bankPath ? fs.readFileSync(listing[bankPath]) : null;
    const character = config.profile === 'aggressive' && bank?.toString('ascii', 0, 4) === 'DSKM' &&
      /(?:_driver|ui_driver_\d+(?:_000)?|award_[a-z0-9_]+|lakitu_body|rosalina_luma)\.gmdl$/.test(relative);
    let characterConfig = {};
    if (character) {
      const signature = bindingFingerprint(bank), metadata = regions?.entries[signature];
      // Exact binding identity is mandatory before applying semantic slot IDs.
      // Missing metadata preserves character topology; scenery stays aggressive.
      characterConfig = { characterSafety: true, characterHeadMode: config.characterHeadMode || 'preserve' };
      if (metadata) {
        ensure(Array.isArray(metadata.protectedMatrices) && metadata.protectedMatrices.length > 0 && Array.isArray(metadata.nodes), 'Invalid character semantic entry');
        characterConfig.protectedBones = metadata.protectedMatrices;
        characterConfig.faceBones = metadata.nodes.flatMap((names, i) => Array.isArray(names) && names.some(n => /face/i.test(n)) ? [i] : []);
      }
    }
    const roadLocks = roads?.locks[relative];
    const result = await simplifyModel(fs.readFileSync(listing[relative]), bank, { ...config, ...characterConfig,
      ...(roadLocks ? { roadLocks } : {}),
      protectBoundaries: config.profile === 'aggressive' && bank?.toString('ascii', 0, 4) === 'DSKM' });
    const reduced = result.report.candidateTriangles < result.report.originalTriangles ||
      result.report.candidateVertices < result.report.vertices;
    const candidate = reduced ? `models/o3ds/${path.basename(relative)}` : null;
    if (candidate) fs.writeFileSync(path.join(output, candidate), result.output);
    receipt.models[relative] = { ...result.report, source: path.resolve(listing[relative]), candidate,
      animation: bankPath || null, ...(roadLocks ? { roadLockedVertices: roadLocks.length } : {}) };
  }
  const rows = Object.values(receipt.models);
  receipt.totals = { models: rows.length, reducedModels: rows.filter(r => r.candidate).length,
    originalTriangles: rows.reduce((n, r) => n + r.originalTriangles, 0),
    candidateTriangles: rows.reduce((n, r) => n + r.candidateTriangles, 0),
    originalVertexBytes: rows.reduce((n, r) => n + r.vertices * STRIDE, 0),
    candidateVertexBytes: rows.reduce((n, r) => n + r.candidateVertices * STRIDE, 0) };
  receipt.inputManifestSha256 = sha(Buffer.from(JSON.stringify(rows.map(r => [r.source, r.originalSha256, r.animationSha256 || null]))));
  fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2) + '\n');
  return receipt;
}

async function main() {
  const args = process.argv.slice(2), options = { config: {} };
  if (args.includes('--help')) {
    console.log('Usage: node tools/cook_o3ds_geometry.mjs (--assets STAGED_ROOT | --listing INPUTS.json) --output BUILD_DIR [--profile conservative|aggressive] [--ratio RATIO] [--max-error ERROR] [--protect-features true|false] [--character-regions VERIFIED_JSON] [--character-head-mode preserve|balanced] [--road-locks ROADS_JSON] [--seamless true] [--free-borders true] [--absolute-error UNITS]\nConservative: ratio 0.5, error 0.005, original vertex streams. Aggressive: ratio 0.3, error 0.12, compact streams with animation remap. Characters require verified semantic metadata; absent/mismatched entries retain their topology. Default preserve mode retains whole head geometry and limits body error to 0.06. Experimental balanced mode preserves facial planes/fronts, mouths, caps and hair, and permits moderate back-head reduction.\nProduces experimental models/o3ds/*.gmdl and receipt.json. No activation marker; no FPS claim.');
    return;
  }
  while (args.length) {
    const key = args.shift();
    ensure(args.length && !args[0].startsWith('--'), `Missing value for ${key}`);
    const value = args.shift();
    if (['--assets', '--listing', '--output'].includes(key)) options[key.slice(2)] = value;
    else if (key === '--character-regions') options.characterRegions = value;
    else if (key === '--road-locks') options.roadLocks = value;
    else if (key === '--seamless') { ensure(['true', 'false'].includes(value), 'seamless must be true or false'); options.config.seamless = value === 'true'; }
    else if (key === '--free-borders') { ensure(['true', 'false'].includes(value), 'free-borders must be true or false'); options.config.freeBorders = value === 'true'; }
    else if (key === '--absolute-error') options.config.absoluteError = Number(value);
    else if (key === '--character-head-mode') options.config.characterHeadMode = value;
    else if (key === '--ratio') options.config.ratio = Number(value);
    else if (key === '--max-error') options.config.maxError = Number(value);
    else if (key === '--profile') options.config.profile = value;
    else if (key === '--protect-features') { ensure(['true', 'false'].includes(value), 'protect-features must be true or false'); options.config.protectFeatures = value === 'true'; }
    else throw new Error(`Unknown option: ${key}`);
  }
  ensure(options.output && Boolean(options.assets) !== Boolean(options.listing), 'Specify --output and exactly one of --assets or --listing');
  console.log(JSON.stringify((await cook(options)).totals, null, 2));
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(`Old 3DS geometry: ${error.message}`); process.exitCode = 1; });
}

// Shared readers/constraints for the isolated position-update diagnostic.
// Exporting these does not enable that experiment in the production cook.
export { vertexKey, materialBoundaryVertices, characterSeamVertices, featureVertices, facialShapeIds };
