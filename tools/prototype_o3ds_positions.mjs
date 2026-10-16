#!/usr/bin/env node
// Private visual experiment. Output is NOT an O3RM runtime sidecar: changed
// positions need a separately validated bind-space mapping before game use.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { MeshoptSimplifier } from 'meshoptimizer';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const ensure = (condition, message) => { if (!condition) throw new Error(message); };

// Protect sharp folds (for example cap bills) using only the Wii geometry.
function sharpFoldLocks(model) {
  const ids = new Uint32Array(model.vertices), points = new Map(), edges = new Map();
  for (let i = 0; i < model.vertices; ++i) {
    const key = Array.from(model.positions.subarray(i * 3, i * 3 + 3)).join(',');
    if (!points.has(key)) points.set(key, points.size);
    ids[i] = points.get(key);
  }
  for (let n = 0; n < model.indices; n += 3) {
    const triangle = Array.from(model.indexData.subarray(n, n + 3)), p = triangle.map(i => Array.from(model.positions.subarray(i * 3, i * 3 + 3)));
    const a = p[1].map((v, i) => v - p[0][i]), b = p[2].map((v, i) => v - p[0][i]);
    const normal = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]], length = Math.hypot(...normal);
    if (length < 1e-12) continue;
    for (let k = 0; k < 3; ++k) normal[k] /= length;
    for (let k = 0; k < 3; ++k) {
      const a = ids[triangle[k]], b = ids[triangle[(k + 1) % 3]], key = a < b ? `${a}:${b}` : `${b}:${a}`;
      if (!edges.has(key)) edges.set(key, []);
      edges.get(key).push(normal);
    }
  }
  const protectedPoints = new Set();
  for (const [edge, normals] of edges) if (normals.length === 2 && normals[0].reduce((sum, value, i) => sum + value * normals[1][i], 0) < -0.3)
    for (const point of edge.split(':')) protectedPoints.add(Number(point));
  return Uint8Array.from(ids, id => protectedPoints.has(id) ? 1 : 0);
}

export async function generate(options) {
  ensure(options.model && options.bank && options.regions && options.output, 'Require --model, --bank, --regions and --output');
  const helperPath = options.helpers ? path.resolve(options.helpers) : fileURLToPath(new URL('./cook_o3ds_geometry.mjs', import.meta.url));
  const helpers = await import(pathToFileURL(helperPath));
  const data = fs.readFileSync(options.model), bank = fs.readFileSync(options.bank), regionBytes = fs.readFileSync(options.regions);
  const model = helpers.parseModel(data), binding = helpers.parseBinding(bank, model), signature = helpers.bindingFingerprint(bank);
  const regions = JSON.parse(regionBytes), semantic = regions.entries?.[signature];
  ensure(regions.version === 1 && semantic && Array.isArray(semantic.nodes), 'Exact verified Wii semantic binding is required');
  ensure(!model.sourceVertexIds && binding.format === 'DSKM', 'Only original authored Wii GMDL + DSKM inputs are supported');
  const landmarks = options.landmarks ? JSON.parse(fs.readFileSync(options.landmarks, 'utf8')) : null;
  ensure(!landmarks || (landmarks.version === 1 && landmarks.sourceModelSha256 === hash(data) && landmarks.bindingFingerprint === signature && Array.isArray(landmarks.triangles)), 'Landmarks must match this exact authored Wii model and binding');
  const chartBytes = options['uv-charts'] ? fs.readFileSync(options['uv-charts']) : null;
  const uvCharts = chartBytes ? JSON.parse(chartBytes) : null;
  if (uvCharts) {
    ensure(uvCharts.source === 'authored-Wii-only' && uvCharts.modelSha256 === hash(data) && uvCharts.bankSha256 === hash(bank), 'UV charts must match this exact authored Wii model and binding');
    ensure(Array.isArray(uvCharts.triangleCharts) && uvCharts.triangleCharts.length === model.indices / 3 &&
      Array.isArray(uvCharts.equivalentCornerCharts) && uvCharts.equivalentCornerCharts.length === model.vertices &&
      uvCharts.triangleCharts.every(Number.isInteger) && uvCharts.equivalentCornerCharts.every(labels => Array.isArray(labels) && labels.length && labels.every(Number.isInteger)), 'Incomplete original UV chart membership');
    for (let n = 0; n < model.indices; ++n) ensure(uvCharts.equivalentCornerCharts[model.indexData[n]].includes(uvCharts.triangleCharts[Math.floor(n / 3)]), 'Original triangle corners disagree with their UV chart');
  }
  const jointAware = options.joints === 'true', matrixCount = bank.readUInt32LE(12), attributeStride = 11 + (jointAware ? matrixCount : 0);
  const anatomyFolds = options['anatomy-folds'] === 'true';
  const retainPositions = options['retain-positions'] === 'true';
  ensure(!anatomyFolds || (jointAware && options.regional === 'true'), 'Anatomy-aware folds require joint-aware regional mode');
  const boneWeight = Number(options['bone-weight'] || 0.1);
  ensure(!jointAware || (attributeStride <= 32 && boneWeight > 0 && boneWeight <= 1), 'Joint-aware prototype requires <=21 palette matrices and a valid bone weight');
  const boneFamilies = semantic.nodes.map((names, matrix) => {
    if (semantic.protectedMatrices.includes(matrix)) return 'head';
    const families = new Set(names.map(name => {
      const side = name.match(/_(l|r)\d/i)?.[1]?.toLowerCase();
      if (side && /arm|wrist|hand|finger/i.test(name)) return `arm-${side}`;
      if (side && /leg|ankle|foot|toe/i.test(name)) return `leg-${side}`;
      return 'torso';
    }));
    return families.size === 1 ? [...families][0] : `weighted-${matrix}`;
  });
  const output = path.resolve(options.output);
  ensure(![options.model, options.bank, options.regions, helperPath].some(file => path.resolve(file).startsWith(output + path.sep)), 'Output must be separate from inputs');
  fs.mkdirSync(output, { recursive: true });
  await MeshoptSimplifier.ready;
  const faceShapes = helpers.facialShapeIds(bank), locks = helpers.characterSeamVertices(model, binding, new Set());
  const firstLockReason = new Map();
  const recordLocks = reason => { for (let i = 0; i < model.vertices; ++i) if ((locks[i] & 1) && !firstLockReason.has(i)) firstLockReason.set(i, reason); };
  recordLocks('material-binding-or-geometric-boundary');
  // A connected chart can still wrap across a UV seam. Preserve those seam
  // positions explicitly; normal-only seams remain eligible for simplification.
  if (uvCharts) for (let i = 0; i < model.vertices; ++i) if (locks[i] & 2) locks[i] |= 1;
  recordLocks('uv-seam');
  const tips = helpers.featureVertices(model, binding, options.regional !== 'true'), folds = sharpFoldLocks(model);
  for (let i = 0; i < model.vertices; ++i) locks[i] |= tips[i];
  recordLocks('geometric-tip');
  for (let i = 0; i < model.vertices; ++i) if (!anatomyFolds || boneFamilies[binding.matrixIds[i]] === 'head') locks[i] |= folds[i];
  recordLocks('sharp-fold');
  if (options.rebake === 'true') for (let i = 0; i < model.vertices; ++i) locks[i] &= 1;
  for (const shape of faceShapes) {
    ensure(shape < model.submeshes.length, 'Facial animation shape is outside model');
    const span = model.submeshes[shape];
    for (const i of model.indexData.subarray(span.first, span.first + span.count)) locks[i] |= 1;
  }
  recordLocks('facial-overlay');
  for (const landmark of landmarks?.triangles || []) {
    const span = model.submeshes[landmark.material];
    ensure(span && Number.isInteger(landmark.triangle) && landmark.triangle >= 0 && landmark.triangle * 3 + 3 <= span.count, 'Landmark triangle is outside material');
    for (const i of model.indexData.subarray(span.first + landmark.triangle * 3, span.first + landmark.triangle * 3 + 3)) locks[i] |= 1;
  }
  recordLocks('opaque-landmark');
  if (options.regional === 'true') {
    const headBones = new Set(semantic.protectedMatrices), eyeVertices = [];
    for (const shape of faceShapes) {
      const span = model.submeshes[shape];
      eyeVertices.push(...model.indexData.subarray(span.first, span.first + span.count));
    }
    const normal = [0, 0, 0];
    for (const i of eyeVertices) for (let k = 0; k < 3; ++k) normal[k] += model.attributes[i * 11 + k];
    const length = Math.hypot(...normal), head = [...new Set(model.indexData)].filter(i => headBones.has(binding.matrixIds[i]));
    if (head.length && eyeVertices.length && length > eyeVertices.length * 0.25) {
      for (let k = 0; k < 3; ++k) normal[k] /= length;
      const project = i => normal.reduce((sum, v, k) => sum + v * model.positions[i * 3 + k], 0);
      const values = head.map(project), depth = Math.max(...values) - Math.min(...values);
      const plane = eyeVertices.reduce((sum, i) => sum + project(i), 0) / eyeVertices.length;
      for (const i of head) if (project(i) >= plane + depth * 0.08) locks[i] |= 1;
      recordLocks('front-face-support');
      // Sparse support directions retain the curved upper head/cap profile;
      // these are measured from original Wii vertices, not another game's mesh.
      for (const elevation of [Math.PI / 5, Math.PI * 0.35]) for (let turn = 0; turn < 8; ++turn) {
        const angle = turn * Math.PI / 4, direction = [Math.cos(angle) * Math.cos(elevation), Math.sin(elevation), Math.sin(angle) * Math.cos(elevation)];
        let maximum = -Infinity, selected = -1;
        for (const i of head) { const score = direction.reduce((sum, v, k) => sum + v * model.positions[i * 3 + k], 0); if (score > maximum) { maximum = score; selected = i; } }
        const point = model.positions.subarray(selected * 3, selected * 3 + 3);
        for (const i of head) if ([0, 1, 2].every(k => model.positions[i * 3 + k] === point[k])) locks[i] |= 1;
      }
      recordLocks('upper-head-support');
      if (anatomyFolds) {
        const eyeHeight = eyeVertices.reduce((sum, i) => sum + model.positions[i * 3 + 1], 0) / eyeVertices.length;
        const height = Math.max(...head.map(i => model.positions[i * 3 + 1])) - Math.min(...head.map(i => model.positions[i * 3 + 1]));
        const horizontal = Math.hypot(normal[0], normal[2]);
        ensure(horizontal > 0.25 && height > 1e-6, 'Head silhouette support needs a stable authored face direction');
        const front = [normal[0] / horizontal, 0, normal[2] / horizontal], side = [front[2], 0, -front[0]];
        const support = (part, direction) => {
          if (!part.length) return;
          let maximum = -Infinity, selected = -1;
          for (const i of part) {
            const score = direction.reduce((sum, v, k) => sum + v * model.positions[i * 3 + k], 0);
            if (score > maximum) { maximum = score; selected = i; }
          }
          locks[selected] |= 1;
        };
        // The lower rear cap contour and ears need their own sparse supports;
        // upper-head extrema alone can turn the back of the cap into a point.
        const posteriorCap = head.filter(i => model.positions[i * 3 + 1] >= eyeHeight + height * 0.12 && project(i) <= plane + depth * 0.1);
        for (const lateral of [-0.7, 0, 0.7]) for (const vertical of [-0.6, 0, 0.6])
          support(posteriorCap, front.map((v, k) => -v + side[k] * lateral + (k === 1 ? vertical : 0)));
        recordLocks('posterior-cap-support');
        const ears = head.filter(i => Math.abs(model.positions[i * 3 + 1] - eyeHeight) <= height * 0.15 && project(i) <= plane);
        for (const sign of [-1, 1]) for (const vertical of [-0.5, 0.5]) for (const facing of [-0.5, 0.5])
          support(ears, side.map((v, k) => v * sign + front[k] * facing + (k === 1 ? vertical : 0)));
        recordLocks('ear-silhouette-support');
      }
    }
  }
  if (options['terminal-support'] === 'true') {
    const directions = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];
    for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) directions.push([x, y, z]);
    for (let matrix = 0; matrix < semantic.nodes.length; ++matrix) {
      if (!semantic.nodes[matrix].some(name => /wrist|hand|ankle|foot/i.test(name))) continue;
      const part = [...new Set(model.indexData)].filter(i => binding.matrixIds[i] === matrix);
      if (!part.length) continue;
      for (const direction of directions) {
        let maximum = -Infinity, selected = -1;
        for (const i of part) { const score = direction.reduce((sum, v, k) => sum + v * model.positions[i * 3 + k], 0); if (score > maximum) { maximum = score; selected = i; } }
        locks[selected] |= 1;
      }
    }
  }
  recordLocks('terminal-limb-support');
  // Ordinary mode retains mixed-matrix corners. The isolated joint-aware mode
  // instead encodes bone assignment in the simplification attributes, retaining
  // the selected source bone at output; posed validation is mandatory afterward.
  for (let n = 0; n < model.indices; n += 3) {
    const triangle = Array.from(model.indexData.subarray(n, n + 3));
    if ((!jointAware && new Set(triangle.map(i => binding.matrixIds[i])).size > 1) || new Set(triangle.map(i => binding.groups[i])).size > 1)
      for (const i of triangle) locks[i] |= 1;
  }
  recordLocks('mixed-binding-triangle');
  // Triangle soup can duplicate a joint boundary vertex in a rigid neighboring
  // region. Lock every coincident copy, otherwise separate regional passes can
  // collapse one side of the shared edge and leave a T-junction or an open seam.
  const low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < model.vertices; ++i) for (let k = 0; k < 3; ++k) {
    low[k] = Math.min(low[k], model.positions[i * 3 + k]); high[k] = Math.max(high[k], model.positions[i * 3 + k]);
  }
  const epsilon = Math.max(1e-6, ...high.map((v, k) => (v - low[k]) * 1e-6));
  const keyAt = i => [0, 1, 2].map(k => Math.round(model.positions[i * 3 + k] / epsilon)).join(',');
  const lockedPoints = new Set();
  for (let i = 0; i < model.vertices; ++i) if (locks[i] & 1) lockedPoints.add(keyAt(i));
  for (let i = 0; i < model.vertices; ++i) if (lockedPoints.has(keyAt(i))) locks[i] |= 1;
  recordLocks('coincident-copy');
  const grid = [];
  const ratios = options.ratio ? [Number(options.ratio)] : [0.25, 0.30, 0.35];
  const errors = options.error ? [Number(options.error)] : [0.04, 0.08, 0.12];
  ensure(ratios.every(n => n > 0 && n < 1) && errors.every(n => n > 0 && n <= 0.15), 'Invalid prototype ratio/error');
  for (const ratio of ratios) for (const maxError of errors) {
    const vertices = [], sourceVertexIds = [], sourceMatrixIds = [], bindingGroups = [], changedVertices = [], spans = [], indices = [], jobs = [], skinAttributeDeviations = [];
    for (let material = 0; material < model.submeshes.length; ++material) {
      const span = model.submeshes[material], groups = new Map();
      for (let n = span.first; n < span.first + span.count; n += 3) {
        const triangle = Array.from(model.indexData.subarray(n, n + 3));
        const matrix = new Set(triangle.map(i => binding.matrixIds[i]));
        const votes = new Map();
        for (const i of triangle) { const family = boneFamilies[binding.matrixIds[i]]; votes.set(family, (votes.get(family) || 0) + 1); }
        const anatomicalRegion = [...votes].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0][0];
        let key = options.regional === 'true' && !faceShapes.has(material) && !(span.flags & 3) ?
          jointAware ? options.anatomical === 'true' ? anatomicalRegion : triangle.some(i => semantic.protectedMatrices.includes(binding.matrixIds[i])) ? 'head' : 'body' :
            matrix.size === 1 ? [...matrix][0] : 'mixed' : 'all';
        // Each collapse operates on one authored UV chart. This prevents a new
        // triangle from joining corners that sample unrelated atlas regions.
        if (uvCharts && !faceShapes.has(material) && !(span.flags & 3)) key = `${key}:chart-${uvCharts.triangleCharts[n / 3]}`;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(...triangle);
      }
      for (const [region, sourceIndices] of groups) jobs.push({ material, region, sourceIndices });
    }
    if (jointAware || uvCharts) {
      const membership = new Map();
      for (let job = 0; job < jobs.length; ++job) for (const i of jobs[job].sourceIndices) {
        const key = keyAt(i); if (!membership.has(key)) membership.set(key, new Set()); membership.get(key).add(job);
      }
      for (let i = 0; i < model.vertices; ++i) if ((membership.get(keyAt(i))?.size || 0) > 1) locks[i] |= 1;
    }
    recordLocks('regional-job-boundary');
    for (const { material, region, sourceIndices } of jobs) {
      const span = { ...model.submeshes[material], count: sourceIndices.length }, equivalents = new Map(), localToSource = [], localIndices = new Uint32Array(span.count);
      for (let n = 0; n < span.count; ++n) {
        const i = sourceIndices[n], key = helpers.vertexKey(model, i, binding);
        if (!equivalents.has(key)) { equivalents.set(key, localToSource.length); localToSource.push(i); }
        localIndices[n] = equivalents.get(key);
      }
      const positions = new Float32Array(localToSource.length * 3), attributes = new Float32Array(localToSource.length * attributeStride);
      const localLocks = Uint8Array.from(localToSource, i => locks[i]);
      localToSource.forEach((i, n) => {
        positions.set(model.positions.subarray(i * 3, i * 3 + 3), n * 3);
        attributes.set(model.attributes.subarray(i * 11, i * 11 + 11), n * attributeStride);
        if (jointAware) attributes[n * attributeStride + 11 + binding.matrixIds[i]] = 1;
      });
      const lockedShape = faceShapes.has(material) || (span.flags & 3) !== 0;
      const weights = options.rebake === 'true' ? [0.12, 0.12, 0.12, 0, 0, 0, 0, 0, 0, 0, 0] :
        options.regional === 'true' ? [0.12, 0.12, 0.12, 0.25, 0.25, 0.1, 0.1, 0.25, 0.25, 0.25, 0.25] :
        [0.35, 0.35, 0.35, 0.75, 0.75, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5];
      if (jointAware) weights.push(...Array(matrixCount).fill(boneWeight));
      let count = localIndices.length, error = 0;
      if (!lockedShape && count >= 24) {
        const target = Math.max(3, Math.floor(count / 3 * ratio) * 3);
        const flags = options.regional === 'true' ? ['Permissive', 'RegularizeLight'] : ['Permissive', 'RegularizeLight', 'LockBorder'];
        if (retainPositions) {
          const [simplified, retainedError] = MeshoptSimplifier.simplifyWithAttributes(
            localIndices, positions, 3, attributes, attributeStride, weights, localLocks, target, maxError, flags);
          localIndices.set(simplified); count = simplified.length; error = retainedError;
        } else [count, error] = MeshoptSimplifier.simplifyWithUpdate(
          localIndices, positions, 3, attributes, attributeStride, weights, localLocks, target, maxError, flags);
      }
      ensure(count > 0 && count % 3 === 0 && count <= span.count && Number.isFinite(error) && error <= maxError + 1e-6, 'Invalid simplification result');
      const used = [...new Set(localIndices.subarray(0, count))].sort((a, b) => a - b), compact = new Map();
      for (const n of used) {
        const source = localToSource[n], record = Buffer.from(data.subarray(model.vertexOffset + source * 44, model.vertexOffset + (source + 1) * 44));
        if (!retainPositions && !(localLocks[n] & 1)) {
          for (let axis = 0; axis < 3; ++axis) record.writeFloatLE(positions[n * 3 + axis], axis * 4);
          const normal = Array.from(attributes.subarray(n * attributeStride, n * attributeStride + 3)), length = Math.hypot(...normal);
          for (let k = 0; k < 7; ++k) record.writeFloatLE(k < 3 ? length > 1e-10 ? normal[k] / length : model.attributes[source * 11 + k] : attributes[n * attributeStride + k], 12 + k * 4);
          for (let k = 0; k < 4; ++k) record[40 + k] = Math.round(Math.max(0, Math.min(1, attributes[n * attributeStride + 7 + k])) * 255);
        }
        const compactIndex = vertices.length; compact.set(n, compactIndex); vertices.push(record);
        sourceVertexIds.push(source); sourceMatrixIds.push(binding.matrixIds[source]);
        if (jointAware) skinAttributeDeviations.push(Math.hypot(...Array.from({ length: matrixCount }, (_, bone) =>
          attributes[n * attributeStride + 11 + bone] - (bone === binding.matrixIds[source] ? 1 : 0))));
        bindingGroups.push(binding.groups[source].split(',').filter(Boolean).map(Number));
        const original = data.subarray(model.vertexOffset + source * 44, model.vertexOffset + (source + 1) * 44);
        if (!original.equals(record)) changedVertices.push({ compactIndex, sourceIndex: source,
          originalPosition: Array.from(model.positions.subarray(source * 3, source * 3 + 3)),
          position: [0, 4, 8].map(at => record.readFloatLE(at)), normal: [12, 16, 20].map(at => record.readFloatLE(at)),
          uv: [24, 28, 32, 36].map(at => record.readFloatLE(at)), color: Array.from(record.subarray(40, 44)) });
      }
      const first = indices.length;
      for (const i of localIndices.subarray(0, count)) indices.push(compact.get(i));
      if (!spans[material]) spans[material] = { material, first, count: 0, originalCount: model.submeshes[material].count, error: 0, eyeOrFaceOverlay: faceShapes.has(material), lockedShape, regions: [] };
      spans[material].count += count; spans[material].error = Math.max(spans[material].error, error);
      spans[material].regions.push({ region, originalCount: span.count, count, error });
      if (lockedShape) {
        ensure(count === span.count, 'Protected overlay lost triangles');
        for (let n = 0; n < count; ++n) {
          const source = sourceIndices[n], candidate = vertices[indices[first + n]];
          ensure(data.subarray(model.vertexOffset + source * 44, model.vertexOffset + (source + 1) * 44).equals(candidate), 'Protected overlay vertex or UV changed');
        }
      }
    }
    ensure(vertices.length <= 65535, 'Compact prototype exceeds uint16 indices');
    const header = Buffer.from(data.subarray(0, model.vertexOffset)), indexBuffer = Buffer.alloc(indices.length * 2);
    header.writeUInt32LE(vertices.length, 8); header.writeUInt32LE(indices.length, 12);
    spans.forEach((span, n) => { header.writeUInt32LE(span.first, model.submeshes[n].offset); header.writeUInt32LE(span.count, model.submeshes[n].offset + 4); });
    indices.forEach((i, n) => indexBuffer.writeUInt16LE(i, n * 2));
    const candidate = Buffer.concat([header, ...vertices, indexBuffer]); helpers.parseModel(candidate);
    const reasonsByFamily = new Map(), assignedPoints = new Set();
    for (const [i, reason] of firstLockReason) {
      const family = boneFamilies[binding.matrixIds[i]], point = `${family}:${keyAt(i)}`;
      if (assignedPoints.has(point)) continue;
      assignedPoints.add(point);
      if (!reasonsByFamily.has(family)) reasonsByFamily.set(family, {});
      const counts = reasonsByFamily.get(family); counts[reason] = (counts[reason] || 0) + 1;
    }
    const candidateTriangleCharts = [];
    if (uvCharts) for (let n = 0; n < indices.length; n += 3) {
      const corners = indices.slice(n, n + 3).map(i => uvCharts.equivalentCornerCharts[sourceVertexIds[i]]);
      const common = corners[0].filter(chart => corners[1].includes(chart) && corners[2].includes(chart));
      ensure(common.length > 0, 'Candidate triangle crosses original UV charts');
      candidateTriangleCharts.push(common);
    }
    const preservedOpaqueTriangles = [];
    const triangleKey = records => [records.join(':'), [records[1], records[2], records[0]].join(':'), [records[2], records[0], records[1]].join(':')].sort()[0];
    for (const landmark of landmarks?.triangles || []) {
      const before = model.submeshes[landmark.material], after = spans[landmark.material], sourceIndices = Array.from(model.indexData.subarray(before.first + landmark.triangle * 3, before.first + landmark.triangle * 3 + 3));
      const expected = triangleKey(sourceIndices.map(i => data.subarray(model.vertexOffset + i * 44, model.vertexOffset + (i + 1) * 44).toString('hex')));
      let candidateTriangle = -1;
      for (let n = after.first; n < after.first + after.count; n += 3) {
        const actual = triangleKey(indices.slice(n, n + 3).map(i => vertices[i].toString('hex')));
        if (actual === expected) { candidateTriangle = n / 3; break; }
      }
      ensure(candidateTriangle >= 0, 'Protected opaque landmark triangle changed or disappeared');
      preservedOpaqueTriangles.push({ name: landmark.name, material: landmark.material, sourceTriangle: landmark.triangle, candidateTriangle, sourceIndices, candidateIndices: indices.slice(candidateTriangle * 3, candidateTriangle * 3 + 3) });
    }
    const stem = `${path.basename(options.model, '.gmdl')}${options.regional === 'true' ? '-regional' : ''}${options.rebake === 'true' ? '-rebake' : ''}${jointAware ? '-joints' : ''}${options.anatomical === 'true' ? '-limbs' : ''}${options['terminal-support'] === 'true' ? '-tips' : ''}${landmarks ? '-landmarks' : ''}${uvCharts ? '-charts' : ''}${anatomyFolds ? '-anatomy-folds' : ''}${retainPositions ? '-retained' : ''}-r${Math.round(ratio * 100)}-e${Math.round(maxError * 100)}`;
    const receipt = { version: 1, prototype: true, runtimeReady: false, visualApproved: false, inputs: {
      model: path.resolve(options.model), modelSha256: hash(data), bank: path.resolve(options.bank), bankSha256: hash(bank),
      semanticMetadata: path.resolve(options.regions), semanticSha256: hash(regionBytes), bindingFingerprint: signature,
      ...(landmarks ? { landmarks: path.resolve(options.landmarks), landmarksSha256: hash(fs.readFileSync(options.landmarks)) } : {}),
      ...(uvCharts ? { uvCharts: path.resolve(options['uv-charts']), uvChartsSha256: hash(chartBytes) } : {}),
      helperSnapshot: helperPath, helperSha256: hash(fs.readFileSync(helperPath)),
    }, source: 'authored-Wii-only', algorithm: `meshoptimizer@1.3.0 ${retainPositions ? 'simplifyWithAttributes' : 'simplifyWithUpdate'}`, retainPositions, exactSourceVertexRecords: retainPositions, ratio, maxError, regional: options.regional === 'true', requiresTextureRebake: options.rebake === 'true',
    jointAware, ...(jointAware ? { boneAttributeWeight: boneWeight, maximumSkinAttributeDeviation: Math.max(0, ...skinAttributeDeviations),
      changedSkinAttributeVertices: skinAttributeDeviations.filter(v => v > 1e-5).length, retainedSourceBoneAssignments: true,
      anatomicalRegions: options.anatomical === 'true', terminalSupport: options['terminal-support'] === 'true', anatomyAwareFolds: anatomyFolds, boneFamilies } : {}),
    originalTriangles: model.indices / 3, candidateTriangles: indices.length / 3,
    originalVertices: model.vertices, candidateVertices: vertices.length, changedVertexCount: changedVertices.length,
    materiallyMovedVertices: changedVertices.filter(v => Math.hypot(...v.position.map((p, k) => p - v.originalPosition[k])) > 1e-3).length,
    maximumVertexDisplacement: Math.max(0, ...changedVertices.map(v => Math.hypot(...v.position.map((p, k) => p - v.originalPosition[k])))),
    candidateSha256: hash(candidate), faceShapes: [...faceShapes], lockedSourceVertices: locks.reduce((sum, value) => sum + Boolean(value & 1), 0),
    lockedSourceVertexIds: Array.from(locks.keys()).filter(i => locks[i] & 1),
    firstLockReasonByFamily: Object.fromEntries(reasonsByFamily),
    materials: spans, sourceVertexIds, sourceMatrixIds, bindingGroups, changedVertices, preservedOpaqueTriangles,
    ...(uvCharts ? { hardUvChartConstraints: true, originalUvChartCount: new Set(uvCharts.triangleCharts).size, candidateTriangleCharts, crossChartTriangles: 0 } : {}) };
    fs.writeFileSync(path.join(output, stem + '.gmdl'), candidate);
    fs.writeFileSync(path.join(output, stem + '.json'), JSON.stringify(receipt, null, 2) + '\n');
    grid.push({ model: path.join(output, stem + '.gmdl'), metadata: path.join(output, stem + '.json'),
      ratio, maxError, triangles: receipt.candidateTriangles, vertices: vertices.length, changedVertices: changedVertices.length });
  }
  fs.writeFileSync(path.join(output, 'grid.json'), JSON.stringify({ source: 'authored-Wii-only', visualApproved: false, variants: grid }, null, 2) + '\n');
  return grid;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const args = process.argv.slice(2), options = {};
  while (args.length) { const key = args.shift(); ensure(/^--(?:model|bank|regions|output|helpers|ratio|error|regional|rebake|joints|bone-weight|anatomical|terminal-support|landmarks|uv-charts|anatomy-folds|retain-positions)$/.test(key) && args.length, `Invalid argument ${key}`); options[key.slice(2)] = args.shift(); }
  generate(options).then(grid => console.log(JSON.stringify(grid, null, 2))).catch(error => { console.error(error.stack); process.exitCode = 1; });
}
