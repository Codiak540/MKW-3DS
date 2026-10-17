import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { bindingFingerprint, parseModel } from './cook_o3ds_geometry.mjs';
import { generate } from './prototype_o3ds_positions.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function fixture(mixedBody = false) {
  const size = 8, points = [];
  for (let material = 0; material < 2; ++material) for (let y = 0; y < size; ++y) for (let x = 0; x < size; ++x)
    for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 0], [1, 1], [0, 1]]) {
      const bytes = Buffer.alloc(44), px = (x + dx) / size, py = (y + dy) / size;
      [px + material * 2, py, 0, 0, 0, 1, px, py, px, py].forEach((v, k) => bytes.writeFloatLE(v, k * 4));
      bytes.fill(255, 40); points.push(bytes);
    }
  const header = Buffer.alloc(24 + 2 * 56 + 64); header.write('GMDL');
  [4, points.length, points.length, 2, 1].forEach((v, i) => header.writeUInt32LE(v, 4 + i * 4));
  for (let shape = 0; shape < 2; ++shape) { const at = 24 + shape * 56; header.writeUInt32LE(shape * points.length / 2, at); header.writeUInt32LE(points.length / 2, at + 4); header.writeInt32LE(-1, at + 16); }
  header.write('synthetic_wii_texture', 24 + 112);
  const indices = Buffer.alloc(points.length * 2); points.forEach((_, i) => indices.writeUInt16LE(i, i * 2));
  const model = Buffer.concat([header, ...points, indices]);
  const bank = Buffer.alloc(28 + points.length * 28 + 16 + 44 + 8); bank.write('DSKM');
  [2, points.length, mixedBody ? 3 : 2, 0, 2, 1].forEach((v, i) => bank.writeUInt32LE(v, 4 + i * 4));
  points.forEach((bytes, i) => { bytes.copy(bank, 28 + i * 28, 0, 24); bank.writeUInt32LE(i < points.length / 2 ? mixedBody && bytes.readFloatLE(0) > 0.5 ? 2 : 0 : 1, 28 + i * 28 + 24); });
  const ranges = 28 + points.length * 28;
  for (let shape = 0; shape < 2; ++shape) { bank.writeUInt32LE(shape * points.length / 2, ranges + shape * 8); bank.writeUInt32LE(points.length / 2, ranges + shape * 8 + 4); }
  const pat = ranges + 16; bank.write('synthetic_eye', pat); bank.writeUInt32LE(1, pat + 40); bank.writeInt32LE(-1, pat + 44); bank.writeInt32LE(0, pat + 48);
  return { model, bank };
}

for (const [regional, rebake, joints, anatomyFolds = 'false', retainPositions = 'false'] of [['false', 'false', 'false'], ['true', 'false', 'false'], ['true', 'true', 'false'], ['true', 'true', 'true'], ['true', 'true', 'true', 'true'], ['true', 'true', 'true', 'false', 'true']]) test(`position prototype regional=${regional} rebake=${rebake} joints=${joints} anatomyFolds=${anatomyFolds} retainPositions=${retainPositions}: retains protected PAT topology and complete Wii mapping`, async () => {
  const work = fs.mkdtempSync(path.join(root, 'build/position-unit-'));
  try {
    const { model, bank } = fixture(joints === 'true'), modelPath = path.join(work, 'source.gmdl'), bankPath = path.join(work, 'source.dskm');
    const metadata = path.join(work, 'regions.json');
    fs.writeFileSync(modelPath, model); fs.writeFileSync(bankPath, bank);
    fs.writeFileSync(metadata, JSON.stringify({ version: 1, entries: { [bindingFingerprint(bank)]: { nodes: joints === 'true' ? [['arm_l1'], ['face'], ['arm_l2']] : [['body'], ['face']], protectedMatrices: [1] } } }));
    const options = { model: modelPath, bank: bankPath, regions: metadata, output: path.join(work, 'output'), ratio: '0.25', error: '0.12', regional, rebake, joints, anatomical: joints, 'terminal-support': joints, 'bone-weight': '0.25', 'anatomy-folds': anatomyFolds, 'retain-positions': retainPositions };
    if (joints === 'true') {
      options.landmarks = path.join(work, 'landmarks.json');
      fs.writeFileSync(options.landmarks, JSON.stringify({ version: 1, sourceModelSha256: createHash('sha256').update(model).digest('hex'), bindingFingerprint: bindingFingerprint(bank), triangles: [{ name: 'opaque-art', material: 0, triangle: 40 }] }));
    }
    const [variant] = await generate(options), report = JSON.parse(fs.readFileSync(variant.metadata)), candidate = parseModel(fs.readFileSync(variant.model)), original = parseModel(model);
    assert.equal(candidate.sourceVertexIds, null);
    assert.equal(report.runtimeReady, false);
    assert.equal(report.source, 'authored-Wii-only');
    assert.equal(report.requiresTextureRebake, rebake === 'true');
    assert.equal(report.jointAware, joints === 'true');
    assert.equal(report.exactSourceVertexRecords, retainPositions === 'true');
    assert.ok(candidate.indices < original.indices);
    assert.equal(report.sourceVertexIds.length, candidate.vertices);
    assert.deepEqual(report.faceShapes, [1]);
    for (let i = 0; i < candidate.vertices; ++i) {
      const source = report.sourceVertexIds[i], shape = source < original.vertices / 2 ? 0 : 1;
      assert.equal(report.sourceMatrixIds[i], bank.readUInt32LE(28 + source * 28 + 24)); assert.deepEqual(report.bindingGroups[i], [shape]);
      if (retainPositions === 'true') assert.deepEqual(candidate.data.subarray(candidate.vertexOffset + i * 44, candidate.vertexOffset + (i + 1) * 44), model.subarray(original.vertexOffset + source * 44, original.vertexOffset + (source + 1) * 44));
    }
    if (retainPositions === 'true') {
      assert.equal(report.changedVertexCount, 0);
      assert.equal(report.maximumVertexDisplacement, 0);
      assert.equal(report.maximumSkinAttributeDeviation, 0);
      assert.match(report.algorithm, /simplifyWithAttributes$/);
    }
    if (joints === 'true') {
      assert.equal(report.retainedSourceBoneAssignments, true);
      assert.equal(report.anatomyAwareFolds, anatomyFolds === 'true');
      assert.ok(Number.isFinite(report.maximumSkinAttributeDeviation));
      assert.equal(report.boneAttributeWeight, 0.25);
      assert.equal(report.preservedOpaqueTriangles.length, 1);
      const landmark = report.preservedOpaqueTriangles[0];
      assert.equal(landmark.sourceTriangle, 40);
      assert.equal(landmark.material, 0);
      const sourceBytes = landmark.sourceIndices.map(i => model.subarray(original.vertexOffset + i * 44, original.vertexOffset + (i + 1) * 44).toString('hex')).sort();
      const candidateBytes = landmark.candidateIndices.map(i => candidate.data.subarray(candidate.vertexOffset + i * 44, candidate.vertexOffset + (i + 1) * 44).toString('hex')).sort();
      assert.deepEqual(candidateBytes, sourceBytes);
      await assert.rejects(generate({ ...options, 'bone-weight': '0' }), /valid bone weight/);
      if (anatomyFolds === 'true') await assert.rejects(generate({ ...options, joints: 'false' }), /Anatomy-aware folds require/);
      const wrong = JSON.parse(fs.readFileSync(options.landmarks)); wrong.sourceModelSha256 = '0'.repeat(64);
      fs.writeFileSync(options.landmarks, JSON.stringify(wrong));
      await assert.rejects(generate(options), /Landmarks must match/);
    }
    const a = original.submeshes[1], b = candidate.submeshes[1];
    assert.equal(a.count, b.count);
    for (let n = 0; n < a.count; ++n) {
      const source = original.indexData[a.first + n], compact = candidate.indexData[b.first + n];
      assert.deepEqual(model.subarray(original.vertexOffset + source * 44, original.vertexOffset + (source + 1) * 44),
        candidate.data.subarray(candidate.vertexOffset + compact * 44, candidate.vertexOffset + (compact + 1) * 44));
    }
    fs.writeFileSync(metadata, JSON.stringify({ version: 1, entries: {} }));
    await assert.rejects(generate(options), /Exact verified Wii semantic binding/);
    assert.deepEqual(fs.readFileSync(modelPath), model); assert.deepEqual(fs.readFileSync(bankPath), bank);
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
});

test('hard UV chart constraints preserve atlas seams and reject mismatched metadata', async () => {
  const work = fs.mkdtempSync(path.join(root, 'build/position-unit-'));
  try {
    const { model, bank } = fixture(), original = parseModel(model);
    const triangleCharts = Array.from({ length: original.indices / 3 }, (_, triangle) => triangle >= 128 ? 2 : triangle % 16 < 8 ? 0 : 1);
    for (let n = 0; n < original.indices; ++n) if (triangleCharts[Math.floor(n / 3)] === 1) {
      const at = original.vertexOffset + original.indexData[n] * 44;
      model.writeFloatLE(2 - model.readFloatLE(at + 24), at + 24);
      model.writeFloatLE(2 - model.readFloatLE(at + 32), at + 32);
    }
    const cornerLabels = new Map(), cornerKeys = [];
    for (let i = 0; i < original.vertices; ++i) {
      const at = original.vertexOffset + i * 44;
      const key = [...[0, 4, 8, 24, 28].map(offset => model.readFloatLE(at + offset)), bank.readUInt32LE(28 + i * 28 + 24)].join(',');
      cornerKeys.push(key);
      if (!cornerLabels.has(key)) cornerLabels.set(key, new Set());
      cornerLabels.get(key).add(triangleCharts[Math.floor(i / 3)]);
    }
    const digest = bytes => createHash('sha256').update(bytes).digest('hex');
    const charts = { source: 'authored-Wii-only', modelSha256: digest(model), bankSha256: digest(bank), triangleCharts,
      equivalentCornerCharts: cornerKeys.map(key => [...cornerLabels.get(key)]) };
    const options = { model: path.join(work, 'source.gmdl'), bank: path.join(work, 'source.dskm'), regions: path.join(work, 'regions.json'),
      'uv-charts': path.join(work, 'charts.json'), output: path.join(work, 'output'), regional: 'true', rebake: 'true', ratio: '0.3', error: '0.12' };
    fs.writeFileSync(options.model, model); fs.writeFileSync(options.bank, bank);
    fs.writeFileSync(options.regions, JSON.stringify({ version: 1, entries: { [bindingFingerprint(bank)]: { nodes: [['body'], ['face']], protectedMatrices: [1] } } }));
    fs.writeFileSync(options['uv-charts'], JSON.stringify(charts));
    const [variant] = await generate(options), report = JSON.parse(fs.readFileSync(variant.metadata)), candidate = parseModel(fs.readFileSync(variant.model));
    assert.ok(candidate.indices < original.indices);
    assert.equal(report.hardUvChartConstraints, true);
    assert.equal(report.crossChartTriangles, 0);
    assert.equal(report.originalUvChartCount, 3);
    assert.equal(report.candidateTriangleCharts.length, candidate.indices / 3);
    for (let n = 0; n < candidate.indices; n += 3) {
      const labels = Array.from(candidate.indexData.subarray(n, n + 3), i => charts.equivalentCornerCharts[report.sourceVertexIds[i]]);
      assert.ok(labels[0].some(label => labels[1].includes(label) && labels[2].includes(label)));
    }
    // Both sides of the UV discontinuity retain coincident geometry and exact
    // vertex data despite zero UV weights in the geometry-first experiment.
    for (let i = 0; i < candidate.vertices; ++i) {
      const source = report.sourceVertexIds[i], at = original.vertexOffset + source * 44;
      if (source < original.vertices / 2 && model.readFloatLE(at) === 0.5)
        assert.deepEqual(candidate.data.subarray(candidate.vertexOffset + i * 44, candidate.vertexOffset + (i + 1) * 44), model.subarray(at, at + 44));
    }
    fs.writeFileSync(options['uv-charts'], JSON.stringify({ ...charts, modelSha256: '0'.repeat(64) }));
    await assert.rejects(generate(options), /UV charts must match/);
    fs.writeFileSync(options['uv-charts'], JSON.stringify({ ...charts, triangleCharts: [] }));
    await assert.rejects(generate(options), /Incomplete original UV chart/);
    fs.writeFileSync(options['uv-charts'], JSON.stringify({ ...charts, equivalentCornerCharts: charts.equivalentCornerCharts.map(() => [99]) }));
    await assert.rejects(generate(options), /Original triangle corners disagree/);
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
});
