#!/usr/bin/env node
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { bindingFingerprint, cook, newlyExposedBoundaryPoints, parseModel, simplifyModel } from './cook_o3ds_geometry.mjs';

// An authored triangle soup, deliberately retaining duplicate skin vertices.
function grid({ version = 4, flags = 0, size = 16, groups = 1, seam = false } = {}) {
  const vertices = [], spans = [];
  for (let g = 0; g < groups; ++g) {
    const first = vertices.length;
    for (let y = 0; y < size; ++y) for (let x = 0; x < size; ++x) {
      for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 0], [1, 1], [0, 1]]) {
        const px = (x + dx) / size, py = (y + dy) / size;
        const v = Buffer.alloc(44);
        [px + g * 2, py, 0, 0, 0, 1, px + (seam && x >= size / 2 ? 1 : 0), py, px, py]
          .forEach((f, n) => v.writeFloatLE(f, n * 4));
        v.fill(255, 40); vertices.push(v);
      }
    }
    spans.push([first, vertices.length - first]);
  }
  const stride = { 3: 44, 4: 56, 5: 92 }[version];
  const header = Buffer.alloc(24 + groups * stride + 64);
  header.write('GMDL'); [version, vertices.length, vertices.length, groups, 1].forEach((n, i) => header.writeUInt32LE(n, 4 + i * 4));
  spans.forEach(([first, count], i) => {
    const at = 24 + i * stride;
    header.writeUInt32LE(first, at); header.writeUInt32LE(count, at + 4);
    header.writeUInt32LE(flags, at + 12); header.writeInt32LE(-1, at + 16);
    header.writeUInt32LE(0xffffffff, at + 20);
  });
  header.write('synthetic_texture', 24 + groups * stride);
  const indices = Buffer.alloc(vertices.length * 2);
  vertices.forEach((_, n) => indices.writeUInt16LE(n, n * 2));
  return Buffer.concat([header, ...vertices, indices]);
}

function binding(model, { magic = 'DSKM', version = 2, mixed = false } = {}) {
  const start = magic === 'DOBJ' ? 40 : version === 5 ? 32 : 28;
  const bank = Buffer.alloc(start + model.vertices * 28 + 8);
  bank.write(magic); bank.writeUInt32LE(version, 4); bank.writeUInt32LE(model.vertices, 8);
  bank.writeUInt32LE(mixed ? 2 : 1, 12);
  if (magic === 'DOBJ') bank.writeUInt32LE(1, 16);
  else { bank.writeUInt32LE(1, 16); bank.writeUInt32LE(1, 20); }
  for (let i = 0; i < model.vertices; ++i) {
    model.data.copy(bank, start + i * 28, model.vertexOffset + i * 44, model.vertexOffset + i * 44 + 24);
    bank.writeUInt32LE(mixed ? i % 3 === 0 ? 1 : 0 : 0, start + i * 28 + 24);
  }
  bank.writeUInt32LE(model.vertices, start + model.vertices * 28 + 4);
  return bank;
}

// Separate body and head palette slots with the exact authored shape ranges.
function characterBinding(model) {
  const bank = Buffer.alloc(28 + model.vertices * 28 + model.submeshes.length * 8);
  binding(model).copy(bank, 0, 0, 28 + model.vertices * 28);
  bank.writeUInt32LE(model.submeshes.length, 12);
  bank.writeUInt32LE(model.submeshes.length, 20);
  model.submeshes.forEach((span, shape) => {
    const at = 28 + model.vertices * 28 + shape * 8;
    bank.writeUInt32LE(span.first, at); bank.writeUInt32LE(span.count, at + 4);
    for (let i = span.first; i < span.first + span.count; ++i) bank.writeUInt32LE(shape, 28 + i * 28 + 24);
  });
  return bank;
}

function triangleBytes(model, material) {
  const span = model.submeshes[material];
  return Array.from(model.indexData.subarray(span.first, span.first + span.count), i =>
    model.data.subarray(model.vertexOffset + i * 44, model.vertexOffset + (i + 1) * 44).toString('hex'));
}

for (const version of [3, 4, 5]) test(`v${version}: halve a dense mesh while preserving authored bytes, materials and borders`, async () => {
  const original = grid({ version, groups: 2 });
  const before = parseModel(original), { output, report } = await simplifyModel(original), after = parseModel(output);
  assert.ok(after.indices <= before.indices * 0.55);
  assert.ok(report.submeshes.every(s => s.error <= 0.005));
  assert.equal(before.vertices, after.vertices);
  assert.deepEqual(output.subarray(before.vertexOffset, before.indexOffset), original.subarray(before.vertexOffset, before.indexOffset));
  for (let n = 0; n < before.submeshes.length; ++n) {
    const a = before.submeshes[n], b = after.submeshes[n], stride = { 3: 44, 4: 56, 5: 92 }[version];
    assert.deepEqual(output.subarray(b.offset + 8, b.offset + stride), original.subarray(a.offset + 8, a.offset + stride));
    const used = new Set(Array.from(after.indexData.slice(b.first, b.first + b.count)));
    const coordinates = new Set([...used].map(i => `${after.positions[i * 3]},${after.positions[i * 3 + 1]}`));
    for (let i = 0; i <= 16; ++i) for (const [x, y] of [[n * 2, i / 16], [n * 2 + 1, i / 16], [n * 2 + i / 16, 0], [n * 2 + i / 16, 1]]) {
      assert.ok(coordinates.has(`${x},${y}`), `lost locked outline ${x},${y}`);
    }
  }
});

test('UV seam keeps distinct original texture coordinates', async () => {
  const { output } = await simplifyModel(grid({ seam: true }));
  const result = parseModel(output);
  const seamU = new Set(Array.from(result.indexData).filter(i => result.positions[i * 3] === 0.5).map(i => result.attributes[i * 11 + 3]));
  assert.deepEqual(seamU, new Set([0.5, 1.5]));
});

for (const [magic, version] of [['DSKM', 2], ['DSKM', 3], ['DSKM', 4], ['DSKM', 5], ['DOBJ', 1]]) {
  test(`${magic}${version}: retain animation vertex layout and lock deforming triangles`, async () => {
    const original = grid(), model = parseModel(original);
    const rigid = await simplifyModel(original, binding(model, { magic, version }));
    assert.ok(rigid.report.candidateTriangles < rigid.report.originalTriangles * 0.6);
    assert.deepEqual(rigid.output.subarray(model.vertexOffset, model.indexOffset), original.subarray(model.vertexOffset, model.indexOffset));
    const mixed = await simplifyModel(original, binding(model, { magic, version, mixed: true }));
    assert.equal(mixed.report.candidateTriangles, mixed.report.originalTriangles);
    assert.deepEqual(mixed.output, original);
  });
}

test('alpha geometry, small details, invalid inputs and bad skin bindings fail safely', async () => {
  for (const original of [grid({ flags: 1 }), grid({ flags: 2 }), grid({ size: 2 })]) {
    assert.deepEqual((await simplifyModel(original)).output, original);
  }
  const original = grid(), parsed = parseModel(original);
  const invalidIndex = Buffer.from(original); invalidIndex.writeUInt16LE(65535, parsed.indexOffset);
  await assert.rejects(simplifyModel(invalidIndex), /index out of bounds/);
  await assert.rejects(simplifyModel(original.subarray(0, original.length - 1)), /Truncated/);
  await assert.rejects(simplifyModel(original, null, { ratio: 0 }), /ratio/);
  await assert.rejects(simplifyModel(original, null, { maxError: NaN }), /max-error/);
  const badBank = binding(parsed); badBank.writeUInt32LE(1, 8);
  await assert.rejects(simplifyModel(original, badBank), /vertex mismatch/);
});

test('same-matrix vertices with independently animated shape ranges remain separate', async () => {
  const original = grid({ size: 3 }), model = parseModel(original);
  // One range per vertex means every triangle crosses shape animation domains,
  // although its matrices and neutral render attributes are otherwise identical.
  const bank = Buffer.alloc(28 + model.vertices * 28 + model.vertices * 8);
  binding(model).copy(bank, 0, 0, 28 + model.vertices * 28);
  bank.writeUInt32LE(model.vertices, 20);
  for (let i = 0; i < model.vertices; ++i) {
    bank.writeUInt32LE(i, 28 + model.vertices * 28 + i * 8);
    bank.writeUInt32LE(1, 28 + model.vertices * 28 + i * 8 + 4);
  }
  assert.deepEqual((await simplifyModel(original, bank, { minTriangles: 1 })).output, original);
});

test('package cooker writes only isolated candidates, receipts and no activation marker; reruns remove obsolete candidates', async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'mkw-o3ds-geometry-'));
  try {
    const assets = path.join(work, 'source'), output = path.join(work, 'candidate');
    fs.mkdirSync(path.join(assets, 'models'), { recursive: true });
    const file = path.join(assets, 'models', 'sample_000.gmdl'), original = grid();
    fs.writeFileSync(file, original);
    fs.writeFileSync(path.join(assets, 'models', 'sample.dskm'), binding(parseModel(original)));
    const receipt = await cook({ assets, output });
    assert.equal(receipt.totals.reducedModels, 1);
    assert.equal(receipt.models['models/sample_000.gmdl'].animation, 'models/sample.dskm');
    assert.equal(receipt.releaseEnabled, false);
    assert.equal(receipt.hardwareFpsValidated, false);
    assert.equal(receipt.cpuSkinningWorkReduced, false);
    assert.deepEqual(fs.readFileSync(file), original);
    assert.ok(fs.existsSync(path.join(output, 'models/o3ds/sample_000.gmdl')));
    assert.ok(!fs.existsSync(path.join(output, 'o3ds-models-test.txt')));
    fs.writeFileSync(file, grid({ flags: 1 }));
    const rerun = await cook({ assets, output });
    assert.equal(rerun.totals.reducedModels, 0);
    assert.ok(!fs.existsSync(path.join(output, 'models/o3ds/sample_000.gmdl')));
    await assert.rejects(cook({ assets, output: assets }), /separate from source/);
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
});

test('final package listing selects the actual overlay and its course-object bank', async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'mkw-o3ds-listing-'));
  try {
    const original = grid({ version: 5 }), source = path.join(work, 'overlay.gmdl');
    const bank = path.join(work, 'bank.dobj'), visuals = path.join(work, 'visuals.list');
    fs.writeFileSync(source, original); fs.writeFileSync(bank, binding(parseModel(original), { magic: 'DOBJ', version: 1 }));
    fs.writeFileSync(visuals, 'tree tree model_000 animated_tree\n');
    const listing = path.join(work, 'listing.json');
    fs.writeFileSync(listing, JSON.stringify({ 'models/model_000.gmdl': source,
      'models/animated_tree.dobj': bank, 'models/course_00_visuals.list': visuals }));
    const receipt = await cook({ listing, output: path.join(work, 'out') });
    assert.equal(receipt.models['models/model_000.gmdl'].animation, 'models/animated_tree.dobj');
    assert.equal(receipt.models['models/model_000.gmdl'].source, source);
    assert.equal(parseModel(fs.readFileSync(path.join(work, 'out/models/o3ds/model_000.gmdl'))).version, 5);
    fs.writeFileSync(visuals, 'tree tree model_000 missing_bank\n');
    await assert.rejects(cook({ listing, output: path.join(work, 'other') }), /Missing animation binding/);
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
});

for (const version of [3, 4, 5]) test(`v${version}: aggressive variants compact exact source vertices with ordered O3RM IDs`, async () => {
  const original = grid({ version, groups: 2 }), before = parseModel(original);
  const { output, report } = await simplifyModel(original, null, { profile: 'aggressive' }), after = parseModel(output);
  assert.ok(after.indices <= before.indices * 0.35);
  assert.ok(after.vertices < before.vertices * 0.35);
  assert.equal(report.compacted, true);
  assert.equal(report.candidateVertices, after.vertices);
  assert.equal(after.originalVertices, before.vertices);
  assert.equal(after.sourceVertexIds.length, after.vertices);
  for (let i = 0; i < after.vertices; ++i) {
    const sourceId = after.sourceVertexIds[i];
    assert.ok(i === 0 || sourceId > after.sourceVertexIds[i - 1]);
    assert.deepEqual(output.subarray(after.vertexOffset + i * 44, after.vertexOffset + (i + 1) * 44),
      original.subarray(before.vertexOffset + sourceId * 44, before.vertexOffset + (sourceId + 1) * 44));
  }
  for (let n = 0; n < before.submeshes.length; ++n) {
    const a = before.submeshes[n], b = after.submeshes[n], stride = { 3: 44, 4: 56, 5: 92 }[version];
    assert.deepEqual(output.subarray(b.offset + 8, b.offset + stride), original.subarray(a.offset + 8, a.offset + stride));
    const sourceMembership = new Set(before.indexData.subarray(a.first, a.first + a.count));
    assert.ok(b.count > 0);
    for (const id of after.indexData.subarray(b.first, b.first + b.count)) assert.ok(sourceMembership.has(after.sourceVertexIds[id]));
  }
});

test('aggressive mode reduces alpha interiors while retaining their complete outlines', async () => {
  for (const flags of [1, 2]) {
    const original = grid({ flags });
    const { output, report } = await simplifyModel(original, null, { profile: 'aggressive' }), result = parseModel(output);
    assert.ok(report.candidateTriangles < report.originalTriangles * 0.4);
    const coordinates = new Set(Array.from(result.indexData, i => `${result.positions[i * 3]},${result.positions[i * 3 + 1]}`));
    for (let i = 0; i <= 16; ++i) for (const [x, y] of [[0, i / 16], [1, i / 16], [i / 16, 0], [i / 16, 1]]) {
      assert.ok(coordinates.has(`${x},${y}`), `lost alpha outline ${x},${y}`);
    }
  }
});

test('aggressive mode retains shared material boundaries on both sides', async () => {
  const original = grid({ groups: 2 }), before = parseModel(original);
  // Put the two material panels adjacent, so every x=1 vertex is a material join.
  for (let i = before.submeshes[1].first; i < before.vertices; ++i) {
    const at = before.vertexOffset + i * 44;
    original.writeFloatLE(original.readFloatLE(at) - 1, at);
  }
  const after = parseModel((await simplifyModel(original, null, { profile: 'aggressive' })).output);
  for (const span of after.submeshes) {
    const seam = new Set(Array.from(after.indexData.subarray(span.first, span.first + span.count))
      .filter(i => after.positions[i * 3] === 1).map(i => after.positions[i * 3 + 1]));
    for (let n = 0; n <= 16; ++n) assert.ok(seam.has(n / 16));
  }
});

for (const [magic, version] of [['DSKM', 2], ['DSKM', 5], ['DOBJ', 1]]) {
  test(`aggressive ${magic}${version}: compact skin bindings without collapsing mixed joints`, async () => {
    const original = grid(), before = parseModel(original);
    const bank = binding(before, { magic, version, mixed: true });
    const { output, report } = await simplifyModel(original, bank, { profile: 'aggressive' }), after = parseModel(output);
    assert.equal(report.candidateTriangles, report.originalTriangles);
    assert.ok(after.vertices < before.vertices);
    const bankStart = magic === 'DOBJ' ? 40 : version === 5 ? 32 : 28;
    // Every output triangle must still contain the same one-matrix-1/two-matrix-0 joint pattern.
    for (let n = 0; n < after.indices; n += 3) {
      const matrices = Array.from(after.indexData.subarray(n, n + 3), i => bank.readUInt32LE(bankStart + after.sourceVertexIds[i] * 28 + 24)).sort();
      assert.deepEqual(matrices, [0, 0, 1]);
    }
  });
}

test('aggressive mode keeps small geometry intact while welding duplicate CPU skin vertices', async () => {
  const original = grid({ size: 1 }), before = parseModel(original);
  const { output, report } = await simplifyModel(original, binding(before), { profile: 'aggressive' });
  assert.equal(report.candidateTriangles, 2);
  assert.equal(report.candidateVertices, 4);
  assert.equal(parseModel(output).sourceVertexIds.length, 4);
});

test('malformed source remaps and recooking compact variants are rejected', async () => {
  const { output } = await simplifyModel(grid(), null, { profile: 'aggressive' });
  const parsed = parseModel(output), trailer = parsed.indexOffset + parsed.indices * 2;
  for (const [offset, value] of [[4, 2], [8, 65536], [12, parsed.vertices + 1], [16, parsed.originalVertices], [20, parsed.sourceVertexIds[0]]]) {
    const bad = Buffer.from(output); bad.writeUInt32LE(value, trailer + offset);
    assert.throws(() => parseModel(bad), /remap/);
  }
  assert.throws(() => parseModel(output.subarray(0, output.length - 1)), /remap/);
  await assert.rejects(simplifyModel(output, null, { profile: 'aggressive' }), /original authored model/);
  await assert.rejects(simplifyModel(grid(), null, { profile: 'unknown' }), /profile/);
  await assert.rejects(simplifyModel(grid(), null, { profile: 'conservative', maxError: 0.12 }), /max-error/);
});

test('aggressive package records actual compact vertex totals and writes compaction-only variants', async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'mkw-o3ds-aggressive-'));
  try {
    const assets = path.join(work, 'source'), output = path.join(work, 'candidate');
    fs.mkdirSync(path.join(assets, 'models'), { recursive: true });
    fs.writeFileSync(path.join(assets, 'models', 'detail.gmdl'), grid({ size: 1 }));
    const receipt = await cook({ assets, output, config: { profile: 'aggressive' } });
    assert.equal(receipt.options.ratio, 0.3);
    assert.equal(receipt.options.maxError, 0.12);
    assert.equal(receipt.vertexBuffersUnchanged, false);
    assert.equal(receipt.cpuSkinningWorkReduced, true);
    assert.equal(receipt.remapFormat, 'O3RM-v1');
    assert.equal(receipt.totals.originalTriangles, receipt.totals.candidateTriangles);
    assert.equal(receipt.totals.originalVertexBytes, 6 * 44);
    assert.equal(receipt.totals.candidateVertexBytes, 4 * 44);
    assert.equal(receipt.totals.reducedModels, 1);
    assert.ok(fs.existsSync(path.join(output, 'models/o3ds/detail.gmdl')));
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
});

test('optional feature protection retains a small silhouette extremity without changing aggressive defaults', async () => {
  const original = grid(), model = parseModel(original);
  for (let i = 0; i < model.vertices; ++i) if (model.positions[i * 3] === 0.5 && model.positions[i * 3 + 1] === 0.5) {
    original.writeFloatLE(0.05, model.vertexOffset + i * 44 + 8);
  }
  const strong = (await simplifyModel(original, null, { profile: 'aggressive' })).output;
  const protectedModel = parseModel((await simplifyModel(original, null, { profile: 'aggressive', protectFeatures: true })).output);
  const maxZ = model => Math.max(...Array.from(model.indexData, i => model.positions[i * 3 + 2]));
  assert.ok(maxZ(protectedModel) > 0.049);
  assert.deepEqual(strong, (await simplifyModel(original, null, { profile: 'aggressive', protectFeatures: false })).output);
  await assert.rejects(simplifyModel(original, null, { profile: 'aggressive', protectFeatures: 'yes' }), /protect-features/);
  await assert.rejects(simplifyModel(original, null, { profile: 'aggressive', characterError: 0.5 }), /character-error/);
});

test('verified character heads retain exact textured topology while body interiors simplify', async () => {
  const original = grid({ groups: 2, seam: true }), before = parseModel(original), bank = characterBinding(before);
  const { output, report } = await simplifyModel(original, bank, {
    profile: 'aggressive', characterSafety: true, protectedBones: [1],
  });
  const after = parseModel(output);
  assert.deepEqual(triangleBytes(after, 1), triangleBytes(before, 1));
  assert.ok(after.submeshes[0].count < before.submeshes[0].count / 2);
  assert.ok(after.vertices < before.vertices / 3);
  assert.equal(report.characterPolicy.method, 'verified-head-bones-and-seams');
  assert.equal(report.characterPolicy.maxError, 0.06);
  assert.deepEqual(report.characterPolicy.protectedExtremities, []);
  const seamU = new Set(Array.from(after.indexData).filter(i => after.positions[i * 3] === 0.5)
    .map(i => after.attributes[i * 11 + 3]));
  assert.deepEqual(seamU, new Set([0.5, 1.5]));
  await assert.rejects(simplifyModel(original, bank, { profile: 'aggressive', characterSafety: true, protectedBones: [2] }), /protected character bones/);
  await assert.rejects(simplifyModel(original, null, { profile: 'aggressive', characterSafety: true }), /authored driver bindings/);
});

test('unmapped character bindings retain all authored topology with exact duplicate compaction', async () => {
  const original = grid({ groups: 2 }), before = parseModel(original);
  const { output, report } = await simplifyModel(original, characterBinding(before), { profile: 'aggressive', characterSafety: true });
  const after = parseModel(output);
  assert.equal(after.indices, before.indices);
  for (let i = 0; i < 2; ++i) assert.deepEqual(triangleBytes(after, i), triangleBytes(before, i));
  assert.ok(after.vertices < before.vertices / 3);
  assert.equal(report.characterPolicy.method, 'unmapped-preserve-topology');
});

test('character fingerprints ignore clip metadata but reject changed bindings and truncated layouts', () => {
  const bank = characterBinding(parseModel(grid({ groups: 2 }))), changed = Buffer.from(bank);
  changed.writeUInt32LE(40, 16);
  assert.equal(bindingFingerprint(changed), bindingFingerprint(bank));
  changed.writeFloatLE(0.25, 28);
  assert.notEqual(bindingFingerprint(changed), bindingFingerprint(bank));
  assert.throws(() => bindingFingerprint(bank.subarray(0, bank.length - 1)), /Truncated character binding/);
});

test('boundary screen rejects a newly open interior while permitting original open surfaces', () => {
  const positions = Float32Array.from([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]);
  const tetrahedron = { vertices: 4, positions, indexData: Uint32Array.from([0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3]) };
  const opened = { ...tetrahedron, indexData: tetrahedron.indexData.subarray(0, 9) };
  assert.equal(newlyExposedBoundaryPoints(tetrahedron, opened), 3);
  assert.equal(newlyExposedBoundaryPoints(opened, opened), 0);
  const remapped = { indexData: Uint32Array.from([3, 1, 2, 3, 2, 0, 3, 0, 1]), sourceVertexIds: Uint32Array.from([3, 2, 1, 0]) };
  assert.equal(newlyExposedBoundaryPoints(tetrahedron, remapped), 3);
});

test('boundary fallback can retain authored triangles while compacting exact skin vertices', async () => {
  const original = grid({ groups: 2 }), before = parseModel(original);
  const { output, report } = await simplifyModel(original, characterBinding(before), { profile: 'aggressive', preserveTopology: true });
  const after = parseModel(output);
  assert.equal(after.indices, before.indices);
  assert.ok(after.vertices < before.vertices / 3);
  assert.ok(report.submeshes.every(span => span.reason === 'preserve-authored-topology'));
  for (let i = 0; i < 2; ++i) assert.deepEqual(triangleBytes(after, i), triangleBytes(before, i));
});

test('character package defaults to protected heads and falls back safely for missing or mismatched metadata', async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'mkw-o3ds-character-'));
  try {
    const assets = path.join(work, 'source'), output = path.join(work, 'candidate'), regions = path.join(work, 'regions.json');
    fs.mkdirSync(path.join(assets, 'models'), { recursive: true });
    const original = grid({ groups: 2 }), model = parseModel(original), bank = characterBinding(model);
    fs.writeFileSync(path.join(assets, 'models/test_driver.gmdl'), original);
    fs.writeFileSync(path.join(assets, 'models/test_driver.dskm'), bank);
    fs.writeFileSync(path.join(assets, 'models/ui_driver_0_000.gmdl'), original);
    fs.writeFileSync(path.join(assets, 'models/ui_driver_0.dskm'), bank);
    fs.writeFileSync(path.join(assets, 'models/terrain.gmdl'), original);
    const options = { assets, output, config: { profile: 'aggressive' } };
    const missing = await cook(options), key = 'models/test_driver.gmdl';
    assert.equal(missing.models[key].candidateTriangles, missing.models[key].originalTriangles);
    assert.equal(missing.models[key].characterPolicy.method, 'unmapped-preserve-topology');
    assert.equal(missing.models['models/ui_driver_0_000.gmdl'].characterPolicy.method, 'unmapped-preserve-topology');
    const terrain = fs.readFileSync(path.join(output, 'models/o3ds/terrain.gmdl'));
    const metadata = { version: 1, entries: { [bindingFingerprint(bank)]: { protectedMatrices: [1], nodes: [['wrist'], ['face']] } } };
    fs.writeFileSync(regions, JSON.stringify(metadata));
    const verified = await cook({ ...options, characterRegions: regions }), row = verified.models[key];
    assert.equal(row.characterPolicy.method, 'verified-head-bones-and-seams');
    assert.equal(verified.models['models/ui_driver_0_000.gmdl'].characterPolicy.method, 'verified-head-bones-and-seams');
    assert.deepEqual(row.characterPolicy.protectedExtremities, []);
    assert.ok(row.candidateTriangles < row.originalTriangles);
    assert.deepEqual(triangleBytes(parseModel(fs.readFileSync(path.join(output, row.candidate))), 1), triangleBytes(model, 1));
    assert.deepEqual(fs.readFileSync(path.join(output, 'models/o3ds/terrain.gmdl')), terrain);
    const changedBank = Buffer.from(bank); changedBank.writeFloatLE(0.125, 28);
    fs.writeFileSync(path.join(assets, 'models/test_driver.dskm'), changedBank);
    const mismatch = await cook({ ...options, characterRegions: regions });
    assert.equal(mismatch.models[key].characterPolicy.method, 'unmapped-preserve-topology');
    assert.equal(mismatch.models[key].candidateTriangles, mismatch.models[key].originalTriangles);
    assert.deepEqual(fs.readFileSync(path.join(assets, 'models/test_driver.gmdl')), original);
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
});
