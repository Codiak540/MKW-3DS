#!/usr/bin/env python3
"""Old 3DS low-poly character cook (Wii-derived only).

Builds DS-style reduced driver models from the staged Wii GMDL/DSKM pair:

* Half-edge collapses only: every kept vertex is an authored Wii vertex with its
  authored bone binding, so every animation clip stays byte-identical.
* Welded by (position, bone). Joint seams, open/material borders, pointed tips
  (ears, noses, shoes), eye overlays and the surface beneath them, and every
  PAT/UVAN-animated or alpha material are protected. Detail is distributed per
  bone part, so hands and feet keep proportionate shape.
* Triangles that stay inside one original UV chart keep the authored Wii texel
  mapping. Triangles that now span charts are re-baked from the original Wii
  texture (same bone, same facing surface) into tiles under the original atlas
  (texture `<name>_o3`); original texels are copied unchanged.

Output: O3RM v2 sidecars in <out>/models/o3ds/ and PNG textures in <out>/textures/.
"""
import argparse
import hashlib
import heapq
import json
import math
import multiprocessing
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from o3ds_lowpoly_io import read_gmdl, read_dskm, read_dobj, static_bank, write_variant  # noqa: E402
from devkitpro import tool  # noqa: E402

UV_SUFFIX = '_o3'  # re-baked textures: <original>_o3<model tag>
TILE_GUTTER = 2
WRAP_ROWS = 4


# ---------------------------------------------------------------------------
# Geometry helpers

def plane_quadric(a, b, c):
    n = np.cross(b - a, c - a)
    area2 = np.linalg.norm(n)
    if area2 < 1e-12:
        return np.zeros((4, 4)), 0.0
    n = n / area2
    p = np.array([n[0], n[1], n[2], -n.dot(a)])
    return np.outer(p, p) * (area2 * 0.5), area2 * 0.5


def edge_quadric(a, b, face_normal, weight):
    e = b - a
    length = np.linalg.norm(e)
    if length < 1e-12:
        return np.zeros((4, 4))
    n = np.cross(e / length, face_normal)
    nl = np.linalg.norm(n)
    if nl < 1e-12:
        return np.zeros((4, 4))
    n /= nl
    p = np.array([n[0], n[1], n[2], -n.dot(a)])
    return np.outer(p, p) * weight * length * length


def tri_normal(a, b, c):
    n = np.cross(b - a, c - a)
    length = np.linalg.norm(n)
    return n / length if length > 1e-12 else n


def cross2(a, b):
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def tri_quality(a, b, c):
    """4*sqrt(3)*area / sum(edge^2): 1 for equilateral, 0 for degenerate."""
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a))
    s = ((b - a) ** 2).sum() + ((c - b) ** 2).sum() + ((a - c) ** 2).sum()
    return 4 * math.sqrt(3) * area / s if s > 0 else 0.0


# ---------------------------------------------------------------------------
# Model analysis

class Character:
    def __init__(self, gmdl_path, dskm_path):
        self.g = read_gmdl(gmdl_path)
        # Drivers carry DSKM banks, course objects DOBJ banks; unanimated
        # landmarks get a one-bone identity stand-in.
        if dskm_path is None:
            self.k = static_bank(self.g)
        elif str(dskm_path).endswith('.dobj'):
            self.k = read_dobj(dskm_path)
        else:
            self.k = read_dskm(dskm_path)
        if self.k.vertices == 0 and not (self.k.pat_shapes or getattr(self.k, 'extra_animation', False)):
            self.k = static_bank(self.g)   # material-only bank without material tracks
        if self.k.vertices != self.g.header[1]:
            raise ValueError('animation bank does not bind this mesh')
        g = self.g
        self.pos = g.positions
        self.uv = g.uv0
        self.tri = g.indices.reshape(-1, 3)
        self.tri_sub = np.zeros(len(self.tri), np.int64)
        for s, sub in enumerate(g.submeshes):
            self.tri_sub[sub.first // 3:(sub.first + sub.count) // 3] = s
        self.diag = float(np.linalg.norm(self.pos.max(0) - self.pos.min(0)))
        self._classify_submeshes()
        self.charts = self._charts()
        self.detail = np.zeros(len(self.tri))

    def measure_detail(self, texture_dir):
        """Per triangle texture busyness (RGB std of its texels, 0..1)."""
        bary = np.array([[a, b, 1 - a - b] for a in np.linspace(0.05, 0.9, 6)
                         for b in np.linspace(0.05, 0.9, 6) if a + b <= 0.95])
        cache = {}
        for t, rs in enumerate(self.tri):
            sub = self.g.submeshes[self.tri_sub[t]]
            if sub.texture < 0:
                continue
            name = self.g.names[sub.texture]
            if name not in cache:
                cache[name] = np.asarray(Image.open(Path(texture_dir) / f'{name}.png').convert('RGBA')).astype(np.float64)
            uv = bary @ self.uv[rs]
            col = sample_bilinear(cache[name], uv[:, 0], uv[:, 1], submesh_wrap(sub))[:, :3]
            self.detail[t] = float(col.std(0).mean() / 64.0)

    def _classify_submeshes(self):
        g, k = self.g, self.k
        animated_vertex = np.zeros(len(self.pos), bool)
        for s in sorted(k.pat_shapes | k.uvan_shapes):
            first, count = k.ranges[s]
            animated_vertex[first:first + count] = True
        self.eligible, self.reason = [], []
        for sub in g.submeshes:
            idx = g.indices[sub.first:sub.first + sub.count]
            why = None
            if sub.count == 0:
                why = 'empty'
            elif sub.flags & 3:
                why = 'alpha'
            elif animated_vertex[idx].any():
                why = 'face-animation'
            elif sub.texture < 0:
                why = 'untextured'
            elif sub.anim & 0xFFFFFF:
                why = 'material-animation'
            elif np.abs(np.frombuffer(sub.raw[24:40], '<f4')).max() > 1e-9:
                why = 'uv-scroll'
            self.eligible.append(why is None)
            self.reason.append(why or 'reduced')
        self.texture_users = defaultdict(set)
        for s, sub in enumerate(g.submeshes):
            if sub.texture >= 0:
                self.texture_users[sub.texture].add(s)
            if sub.secondary >= 0:
                self.texture_users[sub.secondary].add(-1 - s)

    def _charts(self):
        """Union original triangles of a submesh that share an edge with equal UVs."""
        parent = list(range(len(self.tri)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        rec = self.g.records
        edges = {}
        for t, rs in enumerate(self.tri):
            for i in range(3):
                r0, r1 = int(rs[i]), int(rs[(i + 1) % 3])
                k0 = rec[r0, 0:12].tobytes() + rec[r0, 24:32].tobytes()
                k1 = rec[r1, 0:12].tobytes() + rec[r1, 24:32].tobytes()
                key = (int(self.tri_sub[t]), min(k0, k1), max(k0, k1))
                if key in edges:
                    parent[find(t)] = find(edges[key])
                else:
                    edges[key] = t
        return np.array([find(t) for t in range(len(self.tri))])


class Decimator:
    def __init__(self, ch, eye_radius=0.10, feature_weight=4.0, chart_weight=1.0,
                 chart_penalty=0.01, tip_angle=4.4, head_weight=3.0, detail_weight=4.0, part_floor=0.3,
                 extra_locks=(), allow_moves=True, head_cap=0.035, body_cap=0.07, symmetric=True,
                 all_head=False, lock_borders=True, piece_keep=(0.75, 0.5)):
        self.ch = ch
        self.allow_moves = allow_moves
        self.symmetric = symmetric
        self.chart_penalty = chart_penalty
        pos, mat, tri, tri_sub = ch.pos, ch.k.matrix, ch.tri, ch.tri_sub
        elig = np.array(ch.eligible)
        rec = ch.g.records
        key_to_w = {}
        self.rec_w = np.zeros(len(pos), np.int64)
        for r in range(len(pos)):
            self.rec_w[r] = key_to_w.setdefault((rec[r, 0:12].tobytes(), int(mat[r])), len(key_to_w))
        nW = self.nW = len(key_to_w)
        self.wpos = np.zeros((nW, 3))
        self.wmat = np.zeros(nW, np.int64)
        self.wpos[self.rec_w] = pos
        self.wmat[self.rec_w] = mat
        self.opos = self.wpos.copy()
        self.bones = bone_transforms(ch)
        # Per bone part size: errors are measured relative to the part.
        part_lo, part_hi = {}, {}
        for w in range(nW):
            m = int(self.wmat[w])
            part_lo[m] = np.minimum(part_lo.get(m, self.wpos[w]), self.wpos[w])
            part_hi[m] = np.maximum(part_hi.get(m, self.wpos[w]), self.wpos[w])
        self.part_size2 = np.array([max(np.linalg.norm(part_hi[int(m)] - part_lo[int(m)]), 0.05 * ch.diag) ** 2
                                    for m in self.wmat])
        # Charts each welded vertex belongs to (per submesh).
        self.w_charts = defaultdict(set)
        self.w_subs = defaultdict(set)
        for t, rs in enumerate(tri):
            for r in rs:
                w = int(self.rec_w[r])
                self.w_charts[w].add(int(ch.charts[t]))
                self.w_subs[w].add(int(tri_sub[t]))
        self.locked = np.zeros(nW, bool)
        self.lock_reason = defaultdict(int)
        by_pos = defaultdict(set)
        for w in range(nW):
            by_pos[self.wpos[w].tobytes()].add(w)

        def lock(w, why):
            if not self.locked[w]:
                self.locked[w] = True
                self.lock_reason[why] += 1

        for ws in by_pos.values():
            if len(ws) > 1:
                for w in ws:
                    lock(w, 'joint-seam')
        # Busy texture around a vertex (emblems, buttons, mustache) is costly to move.
        self.w_detail = np.zeros(nW)
        for t, rs in enumerate(tri):
            for r in rs:
                w = int(self.rec_w[r])
                self.w_detail[w] = max(self.w_detail[w], ch.detail[t])
        self.detail_weight = detail_weight
        for w in extra_locks:
            lock(w, 'texture-detail')
        # Decals (cap emblems, belt buckles, badges): small, busy UV charts.
        chart_tris = defaultdict(list)
        for t in range(len(tri)):
            if elig[tri_sub[t]]:
                chart_tris[int(ch.charts[t])].append(t)
        for ts in chart_tris.values():
            if len(ts) <= 12 and float(np.mean(ch.detail[ts])) >= 0.6:
                for t in ts:
                    for r in tri[t]:
                        lock(int(self.rec_w[r]), 'decal')
        protected = []
        overlay_bones = set()
        self.fixed_faces = []
        for t in range(len(tri)):
            if not elig[tri_sub[t]]:
                self.fixed_faces.append(t)
                for r in tri[t]:
                    for w in by_pos[rec[r, 0:12].tobytes()]:
                        lock(w, 'protected-material')
                    protected.append(pos[r])
                    if ch.reason[tri_sub[t]] == 'face-animation':
                        overlay_bones.add(int(mat[r]))
        # The head keeps more detail, as on DS: the bone carrying the eyes and
        # everything inside its volume (jaws, snouts, hair, crowns).
        head = np.isin(self.wmat, list(overlay_bones))
        if all_head:
            head = np.ones(nW, bool)   # landmarks: every part is seen up close
        elif head.any():
            lo, hi = self.wpos[head].min(0), self.wpos[head].max(0)
            pad = (hi - lo) * 0.08
            head |= ((self.wpos >= lo - pad) & (self.wpos <= hi + pad)).all(1)
        self.weight = np.where(head, head_weight, 1.0)
        self.head = head
        # Facial decals (cap emblems, brows, moustache edges): high-contrast
        # triangles on the head keep their authored corners.
        for t in range(len(tri)):
            if elig[tri_sub[t]] and ch.detail[t] >= 0.55:
                ws = [int(self.rec_w[r]) for r in tri[t]]
                if all(head[w] for w in ws):
                    for w in ws:
                        lock(w, 'head-decal')
        self.head_cap, self.body_cap = head_cap, body_cap
        if protected:
            pts = np.array(protected)
            d = np.sqrt(((self.wpos[:, None, :] - pts[None]) ** 2).sum(-1)).min(1)
            for w in np.nonzero(d < eye_radius * ch.diag)[0]:
                lock(int(w), 'overlay-support')
        faces, face_sub = [], []
        for t, rs in enumerate(tri):
            if not elig[tri_sub[t]]:
                continue
            ws = [int(self.rec_w[r]) for r in rs]
            if len(set(ws)) == 3:
                faces.append(ws)
                face_sub.append(int(tri_sub[t]))
        self.faces = np.array(faces, np.int64).reshape(-1, 3)
        self.face_sub = np.array(face_sub, np.int64)
        self.face_alive = np.ones(len(faces), bool)
        self.vf = defaultdict(set)
        for f, ws in enumerate(self.faces):
            for w in ws:
                self.vf[w].add(f)
        edge_faces = defaultdict(list)
        for f, (a, b, c) in enumerate(self.faces):
            for x, y in ((a, b), (b, c), (c, a)):
                edge_faces[(min(x, y), max(x, y))].append(f)
        # Hard features: open borders and material borders.
        self.feature_edges = set()
        for e, fs in edge_faces.items():
            if len(fs) > 2:
                lock(e[0], 'nonmanifold')
                lock(e[1], 'nonmanifold')
            elif len(fs) == 1 and lock_borders:
                # Open borders are where rigid parts meet (cuffs, collars, joints);
                # moving one side alone would open a crack against the other part.
                lock(e[0], 'open-border')
                lock(e[1], 'open-border')
                self.feature_edges.add(e)
            elif len(fs) != 2 or self.face_sub[fs[0]] != self.face_sub[fs[1]]:
                self.feature_edges.add(e)
        self._feature_degree()
        for w, deg in list(self.feature_degree.items()):
            if deg != 2:
                lock(w, 'feature-corner')
        # Pointed tips (ears, nose, shoe toes, crowns): large angle deficit.
        angle = np.zeros(nW)
        for a, b, c in self.faces:
            for p, q, r in ((a, b, c), (b, c, a), (c, a, b)):
                u, v = self.wpos[q] - self.wpos[p], self.wpos[r] - self.wpos[p]
                lu = np.linalg.norm(u) * np.linalg.norm(v)
                if lu > 0:
                    angle[p] += math.acos(max(-1, min(1, u.dot(v) / lu)))
        for w in range(nW):
            if self.vf[w] and not self.feature_degree.get(w) and angle[w] < tip_angle:
                lock(w, 'tip')
        # Soft features: UV chart borders inside a material (eyes, emblems,
        # mustache and glove cuffs are drawn by chart outlines).
        self.chart_edges = set()
        for e, fs in edge_faces.items():
            if len(fs) == 2 and e not in self.feature_edges:
                t = [self._orig_face(f) for f in fs]
                if ch.charts[t[0]] != ch.charts[t[1]]:
                    self.chart_edges.add(e)
        self.Q = np.zeros((nW, 4, 4))
        self.A = np.zeros(nW)
        for a, b, c in self.faces:
            q, area = plane_quadric(*self.wpos[[a, b, c]])
            for w in (a, b, c):
                self.Q[w] += q
                self.A[w] += area
        for e in self.feature_edges | self.chart_edges:
            n = tri_normal(*self.wpos[self.faces[edge_faces[e][0]]])
            q = edge_quadric(self.wpos[e[0]], self.wpos[e[1]], n,
                             feature_weight if e in self.feature_edges else chart_weight)
            self.Q[e[0]] += q
            self.Q[e[1]] += q
        self.version = np.zeros(nW, np.int64)
        self._mirror_setup()
        self.absorbed = [self.wpos[w][None].copy() for w in range(nW)]
        self.absorbed_ids = [[w] for w in range(nW)]
        wn = np.zeros((nW, 3))
        for a, b, c in self.faces:
            n = np.cross(self.wpos[b] - self.wpos[a], self.wpos[c] - self.wpos[a])
            wn[[a, b, c]] += n
        wn /= np.maximum(np.linalg.norm(wn, axis=1, keepdims=True), 1e-12)
        self.absorbed_n = [wn[w][None].copy() for w in range(nW)]
        # Bone part per face (majority corner bone) and connected components.
        self.face_part = []
        for ws in self.faces:
            bones = [int(self.wmat[w]) for w in ws]
            self.face_part.append(max(set(bones), key=bones.count))
        self.part_alive = defaultdict(int)
        for p in self.face_part:
            self.part_alive[p] += 1
        self.part_floor = {p: math.ceil(n * part_floor) for p, n in self.part_alive.items()}
        parent = list(range(nW))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for a, b, c in self.faces:
            parent[find(a)] = find(b)
            parent[find(c)] = find(b)
        self.face_comp = [find(int(ws[0])) for ws in self.faces]
        self.comp_alive = defaultdict(int)
        for c in self.face_comp:
            self.comp_alive[c] += 1
        # Small separate pieces (crowns, buttons, ears, gems) keep most detail.
        tiny_keep, small_keep = piece_keep
        self.comp_floor = {c: (math.ceil(n * (tiny_keep if n <= 24 else small_keep)) if n <= 64 else 4)
                           for c, n in self.comp_alive.items()}

    _face_to_tri = None

    def _orig_face(self, f):
        if self._face_to_tri is None:
            m, ch = [], self.ch
            for t, rs in enumerate(ch.tri):
                if ch.eligible[ch.tri_sub[t]] and len({int(self.rec_w[r]) for r in rs}) == 3:
                    m.append(t)
            self._face_to_tri = m
        return self._face_to_tri[f]

    def _feature_degree(self):
        self.feature_degree = defaultdict(int)
        for a, b in self.feature_edges:
            self.feature_degree[a] += 1
            self.feature_degree[b] += 1

    def neighbors(self, w):
        out = set()
        for f in self.vf[w]:
            out.update(self.faces[f].tolist())
        out.discard(w)
        return out

    def placements(self, u, v):
        """Where v may sit after absorbing u: in place, or (same bone, free to
        move) at the quadric optimum / midpoint, which keeps volume instead of
        cutting chords through rounded heads."""
        out = [self.wpos[v]]
        if self.locked[v] or self.wmat[u] != self.wmat[v] or not self.allow_moves or \
                int(self.wmat[v]) not in self.bones:
            return out
        if self.feature_degree.get(v) and not self.feature_degree.get(u):
            return out
        Q = self.Q[u] + self.Q[v]
        A, b = Q[:3, :3], Q[:3, 3]
        mid = (self.wpos[u] + self.wpos[v]) / 2
        lam = 1e-4 * np.trace(A) + 1e-12
        try:
            x = np.linalg.solve(A + lam * np.eye(3), -b + lam * mid)
        except np.linalg.LinAlgError:
            x = mid
        span = np.linalg.norm(self.wpos[u] - self.wpos[v])
        d = x - mid
        length = np.linalg.norm(d)
        if length > span:
            x = mid + d * (span / length)
        if self.symmetric and self.mirror[v] == v and self.mirror_exact[v]:
            x = x * [0, 1, 1]
            mid = mid * [0, 1, 1]
        out.append(x)
        out.append(mid)
        return out

    def collapse_cost(self, u, v, force=None):
        """Best (cost, position of v) for collapsing u into v, or None."""
        if self.locked[u] or not self.w_subs[u] <= self.w_subs[v]:
            return None
        e = (min(u, v), max(u, v))
        if self.feature_degree.get(u) and e not in self.feature_edges:
            return None
        shared = [f for f in self.vf[u] if v in self.faces[f]]
        if not shared:
            return None
        if len(self.neighbors(u) & self.neighbors(v)) != len(shared):
            return None
        # Part budgets: every bone part (hands, shoes, head) keeps a share.
        removed = defaultdict(int)
        for f in shared:
            removed[self.face_part[f]] += 1
        for part, n in removed.items():
            if self.part_alive[part] - n < self.part_floor[part]:
                return None
        comp = self.face_comp[shared[0]]
        if self.comp_alive[comp] - len(shared) < self.comp_floor[comp]:
            return None
        touched = [f for f in self.vf[u] | self.vf[v] if f not in shared]
        old_tris = [self.faces[f] for f in touched]
        pts = np.concatenate([self.absorbed[u], self.absorbed[v]])
        pns = np.concatenate([self.absorbed_n[u], self.absorbed_n[v]])
        detail = max(self.w_detail[u], self.w_detail[v])
        chart_cost = 0.0
        if not self.w_charts[u] <= self.w_charts[v] or (
                len(self.w_charts[u]) > 1 and e not in self.chart_edges):
            if self.chart_penalty is None:
                return None
            chart_cost = self.chart_penalty * (0.25 + 4 * detail) * self.weight[u]
        best = None
        options = self.placements(u, v)
        # A mirrored position may only be forced on a vertex that may move at all
        # (locked borders, seams and decals stay exactly where they are).
        if force is not None and len(options) > 1:
            options = [force]
        for x in options:
            new = []
            ok = True
            min_quality = 1.0
            for tri in old_tris:
                old = self.wpos[tri]
                cur = old.copy()
                for k in range(3):
                    if tri[k] == u or tri[k] == v:
                        cur[k] = x
                n0, n1 = tri_normal(*old), tri_normal(*cur)
                if np.linalg.norm(n1) < 0.5 or n0.dot(n1) < 0.3:
                    ok = False
                    break
                min_quality = min(min_quality, tri_quality(*cur))
                new.append(cur)
            if not ok or not new:
                continue
            T = np.array(new)
            dist2, bary = closest_point_triangles(pts, T[:, 0], T[:, 1], T[:, 2])
            fn = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
            fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
            # Only surfaces facing the same way count (thin cap brims, ears).
            facing = pns @ fn.T > 0.2
            d2 = np.where(facing, dist2, np.inf)
            j = d2.argmin(1)
            dmin = d2[np.arange(len(pts)), j]
            # A surface that sinks below the authored one (caved heads) costs more
            # than one that bulges slightly outward.
            q = (bary[np.arange(len(pts)), j][..., None] * T[j]).sum(1)
            outside = ((pts - q) * fn[j]).sum(1) > 0
            dmin = np.where(outside, dmin * 2.5, dmin)
            dmin = np.where(np.isfinite(dmin), dmin, dist2.min(1) * 4 + self.part_size2[u] * 0.01)
            cost = math.sqrt(float(dmin.max()) / self.part_size2[u])
            # Hard shape limits no triangle budget may override.
            if cost > (self.head_cap if self.head[u] or self.head[v] else self.body_cap):
                continue
            cost *= self.weight[u] * (1 + self.detail_weight * detail)
            cost += chart_cost
            if min_quality < 0.2:
                cost += (0.2 - min_quality) * 0.05
            if best is None or cost < best[0]:
                best = (max(cost, 0.0), x)
        return best

    # -- symmetry -----------------------------------------------------------
    def _mirror_setup(self, exact_tol=1e-3, pair_tol=1.2e-2):
        """Pair every welded vertex with its mirror across x = 0. Pairs within
        exact_tol (model diagonal) are placed as exact mirrors; looser pairs
        (leaning bike poses) mirror the topology only."""
        P = self.wpos
        nW = self.nW
        self.mirror = np.full(nW, -1, np.int64)
        self.mirror_exact = np.zeros(nW, bool)
        if not self.symmetric:
            return
        M = P * [-1, 1, 1]
        D = np.sqrt(((P[:, None, :] - M[None]) ** 2).sum(-1))
        # Mirror partners must share materials (left/right bones may differ).
        j = D.argmin(1)
        d = D[np.arange(nW), j]
        for w in range(nW):
            m = int(j[w])
            if d[w] < pair_tol * self.ch.diag and int(D[m].argmin()) == w and self.w_subs[w] == self.w_subs[m]:
                self.mirror[w] = m
                self.mirror_exact[w] = d[w] < exact_tol * self.ch.diag
        # Locks and head/detail weights are mirrored so both halves agree.
        for w in range(nW):
            m = self.mirror[w]
            if m >= 0 and m != w:
                lk = self.locked[w] or self.locked[m]
                self.locked[w] = self.locked[m] = lk
                hd = self.head[w] or self.head[m]
                self.head[w] = self.head[m] = hd
                wt = max(self.weight[w], self.weight[m])
                self.weight[w] = self.weight[m] = wt
                dt = max(self.w_detail[w], self.w_detail[m])
                self.w_detail[w] = self.w_detail[m] = dt

    def mirrored(self, u, v):
        """The partner collapse of u->v, None if u->v is self-symmetric, or
        False if u->v cannot be mirrored (it would break symmetry)."""
        mu, mv = self.mirror[u], self.mirror[v]
        if mu < 0 and mv < 0:
            return None                   # asymmetric detail: free
        if mu < 0 or mv < 0:
            return False
        if mu == u:                       # on the plane: only along the plane
            return None if mv == v else False
        if mu == v or mv == u:            # across the plane onto its own mirror
            return False
        return (int(mu), int(mv))

    def mirror_position(self, x, u, v):
        m = self.mirrored(u, v)
        if m and self.mirror_exact[u] and self.mirror_exact[v]:
            return x * [-1, 1, 1]
        return None

    def snapshot(self):
        return dict(faces=self.faces.copy(), face_alive=self.face_alive.copy(),
                    vf={k: set(x) for k, x in self.vf.items()}, part_alive=dict(self.part_alive),
                    comp_alive=dict(self.comp_alive), feature_edges=set(self.feature_edges),
                    chart_edges=set(self.chart_edges), w_charts={k: set(x) for k, x in self.w_charts.items()},
                    w_detail=self.w_detail.copy(), Q=self.Q.copy(), A=self.A.copy(), wpos=self.wpos.copy(),
                    absorbed=list(self.absorbed), absorbed_ids=list(self.absorbed_ids),
                    absorbed_n=list(self.absorbed_n))

    def restore(self, snap):
        for k, x in snap.items():
            if k in ('vf', 'w_charts'):
                setattr(self, k, defaultdict(set, x))
            elif k in ('part_alive', 'comp_alive'):
                setattr(self, k, defaultdict(int, x))
            else:
                setattr(self, k, x)
        self._feature_degree()

    def _apply(self, u, v, x):
        """Collapse u into v placed at x. Returns faces removed."""
        removed = 0
        self.wpos[v] = x
        for f in list(self.vf[u]):
            tri = self.faces[f]
            if v in tri:
                self.face_alive[f] = False
                self.part_alive[self.face_part[f]] -= 1
                self.comp_alive[self.face_comp[f]] -= 1
                for w in tri:
                    self.vf[w].discard(f)
                removed += 1
            else:
                tri[tri == u] = v
                self.vf[v].add(f)
        self.vf[u].clear()
        for edges in (self.feature_edges, self.chart_edges):
            moved = set()
            for e in [e for e in edges if u in e]:
                edges.discard(e)
                o = e[0] if e[1] == u else e[1]
                if o != v:
                    moved.add((min(o, v), max(o, v)))
            edges |= moved
        self._feature_degree()
        self.w_charts[v] |= self.w_charts[u]
        self.w_detail[v] = max(self.w_detail[v], self.w_detail[u])
        self.Q[v] += self.Q[u]
        self.absorbed[v] = np.concatenate([self.absorbed[v], self.absorbed[u]])
        self.absorbed_ids[v] = self.absorbed_ids[v] + self.absorbed_ids[u]
        self.absorbed_n[v] = np.concatenate([self.absorbed_n[v], self.absorbed_n[u]])
        self.A[v] += self.A[u]
        return removed

    def pair_cost(self, u, v):
        """Cost of u->v together with its mirror collapse (the worse side)."""
        m = self.mirrored(u, v)
        if m is False:
            return None
        c = self.collapse_cost(u, v)
        if c is None or m is None:
            return c
        mc = self.collapse_cost(*m)
        if mc is None:
            return None
        return (max(c[0], mc[0]), c[1])

    def run(self, target_faces, max_error):
        heap = []
        counter = 0
        self.asymmetric_skips = 0

        def push(u):
            nonlocal counter
            for v in self.neighbors(u):
                c = self.pair_cost(u, v)
                if c is not None:
                    counter += 1
                    heapq.heappush(heap, (c[0], counter, u, v, self.version[u], self.version[v]))

        for u in range(self.nW):
            if self.vf[u]:
                push(u)
        alive = int(self.face_alive.sum())
        self.applied = []
        limit = max_error
        while heap and alive > target_faces:
            c, _, u, v, vu, vv = heapq.heappop(heap)
            if vu != self.version[u] or vv != self.version[v]:
                continue
            c2 = self.pair_cost(u, v)
            if c2 is None:
                continue
            if c2[0] > c + 1e-15:
                counter += 1
                heapq.heappush(heap, (c2[0], counter, u, v, vu, vv))
                continue
            if c2[0] > limit:
                break
            m = self.mirrored(u, v)
            snap = self.snapshot() if m else None
            removed = self._apply(u, v, c2[1])
            touched = self.neighbors(v) | {v, u}
            if m:
                # The partner collapse, re-checked after the first half.
                x = self.mirror_position(c2[1], u, v)
                mc = self.collapse_cost(*m, force=x) if x is not None else self.collapse_cost(*m)
                if mc is None or mc[0] > limit:
                    self.restore(snap)
                    self.asymmetric_skips += 1
                    self.version[u] += 1
                    continue
                removed += self._apply(m[0], m[1], mc[1])
                touched |= self.neighbors(m[1]) | {m[0], m[1]}
            alive -= removed
            self.applied.append(c2[0])
            for w in touched:
                self.version[w] += 1
            for w in touched - {u} - ({m[0]} if m else set()):
                push(w)
        return [(self.faces[f].tolist(), int(self.face_sub[f])) for f in np.nonzero(self.face_alive)[0]]


# ---------------------------------------------------------------------------
# UV retention and tile re-bake

def closest_point_triangles(p, a, b, c):
    """Closest points from p (N,3) to triangles (M,3): (N,M) dist2 and barycentrics."""
    p = p[:, None, :]
    ab, ac = (b - a)[None], (c - a)[None]
    ap, bp, cp = p - a[None], p - b[None], p - c[None]
    d1, d2 = (ab * ap).sum(-1), (ac * ap).sum(-1)
    d3, d4 = (ab * bp).sum(-1), (ac * bp).sum(-1)
    d5, d6 = (ab * cp).sum(-1), (ac * cp).sum(-1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    with np.errstate(divide='ignore', invalid='ignore'):
        den = va + vb + vc
        bv, bw = vb / den, vc / den
        bu = 1 - bv - bw
        done = np.zeros(d1.shape, bool)

        def setb(mask, u, v, w):
            m = mask & ~done
            bu[m], bv[m], bw[m] = u[m], v[m], w[m]
            done[m] = True

        one, zero = np.ones(d1.shape), np.zeros(d1.shape)
        setb((d1 <= 0) & (d2 <= 0), one, zero, zero)
        setb((d3 >= 0) & (d4 <= d3), zero, one, zero)
        t = d1 / (d1 - d3)
        setb((vc <= 0) & (d1 >= 0) & (d3 <= 0), 1 - t, t, zero)
        setb((d6 >= 0) & (d5 <= d6), zero, zero, one)
        t = d2 / (d2 - d6)
        setb((vb <= 0) & (d2 >= 0) & (d6 <= 0), 1 - t, zero, t)
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        setb((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), zero, 1 - t, t)
    bary = np.nan_to_num(np.stack([bu, bv, bw], -1))
    q = bary[..., 0:1] * a[None] + bary[..., 1:2] * b[None] + bary[..., 2:3] * c[None]
    return ((q - p) ** 2).sum(-1), bary


def sample_bilinear(img, u, v, wrap=(1, 1)):
    """img (H,W,4); PNG sampling (u, 1-v); wrap modes 0 clamp, 1 repeat, 2 mirror."""
    H, W = img.shape[:2]
    x, y = u * W - 0.5, (1 - v) * H - 0.5
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]

    def at(yy, xx):
        return img[wrap_coord(yy, H, wrap[1]).astype(np.int64), wrap_coord(xx, W, wrap[0]).astype(np.int64)]
    return (at(y0, x0) * (1 - fx) * (1 - fy) + at(y0, x0 + 1) * fx * (1 - fy) +
            at(y0 + 1, x0) * (1 - fx) * fy + at(y0 + 1, x0 + 1) * fx * fy)


def submesh_wrap(sub):
    return ((sub.flags >> 8) & 3, (sub.flags >> 10) & 3)


SAMPLE_BARY = np.array([[i / 6, j / 6, 1 - i / 6 - j / 6] for i in range(7) for j in range(7)
                        if i + j <= 6]) * 0.96 + 0.04 / 3


class Baker:
    """Chooses authored Wii UVs for reduced triangles, judged by the colours
    they show against the original surface, and re-bakes the rest."""

    def __init__(self, ch, dec, texture_dir, mean_tol=10.0, max_tol=48.0):
        self.ch, self.dec = ch, dec
        self.mean_tol, self.max_tol = mean_tol, max_tol
        self.w_chart_rec = defaultdict(dict)
        for t, rs in enumerate(ch.tri):
            s = int(ch.tri_sub[t])
            for r in rs:
                self.w_chart_rec[(int(dec.rec_w[r]), s)].setdefault(int(ch.charts[t]), int(r))
        self.tri_normals = np.array([tri_normal(*ch.pos[t]) for t in ch.tri])
        self.rec_chart = np.zeros(len(ch.pos), np.int64)
        self.rec_tri = np.zeros(len(ch.pos), np.int64)
        for t, rs in enumerate(ch.tri):
            self.rec_chart[rs] = ch.charts[t]
            self.rec_tri[rs] = t
        self._uv_cache = {}
        self.w_tris = defaultdict(set)
        for t, rs in enumerate(ch.tri):
            for r in rs:
                self.w_tris[int(dec.rec_w[r])].add(t)
        self.images = {}
        for sub in ch.g.submeshes:
            if sub.texture >= 0 and sub.texture not in self.images:
                name = ch.g.names[sub.texture]
                self.images[sub.texture] = np.asarray(
                    Image.open(Path(texture_dir) / f'{name}.png').convert('RGBA')).astype(np.float64)

    def project_uv(self, X, ws, s):
        """Authored UV of the original surface nearest X (same material, bones, facing)."""
        ch, dec = self.ch, self.dec
        P = dec.wpos[ws]
        bones = {int(dec.wmat[w]) for w in ws}
        n_new = tri_normal(*P)
        mats = ch.k.matrix
        same = np.nonzero(ch.tri_sub == s)[0]
        # History: the original triangles around every vertex this face's
        # corners absorbed are exactly the surface it now stands in for.
        region = set()
        for w in ws:
            for x in dec.absorbed_ids[w]:
                region.update(self.w_tris[x])
        cand = np.array(sorted(t for t in region if ch.tri_sub[t] == s), np.int64)
        if len(cand) == 0:
            cand = same[np.isin(mats[ch.tri[same, 0]], list(bones))]
        if len(cand) == 0:
            cand = same
        facing = cand[self.tri_normals[cand] @ n_new > 0.25]
        if len(facing) == 0:
            facing = cand
        TA, TB, TC = (ch.pos[ch.tri[facing, i]] for i in range(3))
        dist2, bary = closest_point_triangles(X, TA, TB, TC)
        j = dist2.argmin(1)
        bb = bary[np.arange(len(X)), j]
        # A reduced surface cuts chords through concave detail (mouths, ears);
        # cast along its normal and prefer the authored surface outside it.
        e1, e2 = TB - TA, TC - TA
        pvec = np.cross(n_new[None], e2)
        det = (e1 * pvec).sum(1)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1 / np.where(ok, det, 1), 0)
        tvec = X[:, None, :] - TA[None]
        u = (tvec * pvec[None]).sum(-1) * inv[None]
        qvec = np.cross(tvec, e1[None])
        v = (qvec @ n_new) * inv[None]
        t = (qvec * e2[None]).sum(-1) * inv[None]
        hit = ok[None] & (u >= -1e-4) & (v >= -1e-4) & (u + v <= 1 + 1e-4) & (np.abs(t) < 0.12 * ch.diag)
        score = np.where(hit, np.where(t >= 0, t, -1.5 * t), np.inf)
        k = score.argmin(1)
        use = np.isfinite(score[np.arange(len(X)), k])
        if use.any():
            ku = k[use]
            uu, vv = u[use, ku], v[use, ku]
            bb[use] = np.stack([1 - uu - vv, uu, vv], 1)
            j = np.where(use, k, j)
        bb = np.clip(bb, 0, 1)
        bb = bb / bb.sum(1, keepdims=True) * 0.94 + 0.02
        tt = ch.tri[facing[j]]
        return bb[:, 0:1] * ch.uv[tt[:, 0]] + bb[:, 1:2] * ch.uv[tt[:, 1]] + bb[:, 2:3] * ch.uv[tt[:, 2]]

    def local_density(self, ws, s, W, H):
        """Texels per unit of the authored surface this face replaces."""
        ch, dec = self.ch, self.dec
        region = set()
        for w in ws:
            for x in dec.absorbed_ids[w]:
                region.update(self.w_tris[x])
        a3 = a2 = 0.0
        for t in region:
            if ch.tri_sub[t] != s:
                continue
            P = ch.pos[ch.tri[t]]
            U = ch.uv[ch.tri[t]] * [W, H]
            a3 += 0.5 * np.linalg.norm(np.cross(P[1] - P[0], P[2] - P[0]))
            a2 += 0.5 * abs(cross2(U[1] - U[0], U[2] - U[0]))
        return math.sqrt(a2 / a3) if a3 > 1e-12 else 0.0

    def moved(self, w):
        return not np.array_equal(self.dec.wpos[w], self.dec.opos[w])

    def uv_of(self, r, w):
        """UV for record r at its welded vertex's current position: a moved
        vertex takes the authored UV of the nearest point of its own chart."""
        if not self.moved(w):
            return self.ch.uv[r]
        key = (r, w)
        if key not in self._uv_cache:
            ch, dec = self.ch, self.dec
            chart, s = self.rec_chart[r], ch.tri_sub[self.rec_tri[r]]
            region = set()
            for x in dec.absorbed_ids[w]:
                region.update(self.w_tris[x])
            cand = np.array([t for t in region if ch.charts[t] == chart and ch.tri_sub[t] == s], np.int64)
            if len(cand) == 0:
                self._uv_cache[key] = ch.uv[r]
            else:
                T = ch.tri[cand]
                d2, bary = closest_point_triangles(dec.wpos[w][None], ch.pos[T[:, 0]], ch.pos[T[:, 1]], ch.pos[T[:, 2]])
                j = int(d2[0].argmin())
                bb = bary[0, j]
                self._uv_cache[key] = bb[0] * ch.uv[T[j, 0]] + bb[1] * ch.uv[T[j, 1]] + bb[2] * ch.uv[T[j, 2]]
        return self._uv_cache[key]

    def retained(self, ws, s):
        ch = self.ch
        # An authored triangle that survived untouched keeps its exact records.
        if not any(self.moved(w) for w in ws):
            for t in self.w_tris[ws[0]] & self.w_tris[ws[1]] & self.w_tris[ws[2]]:
                if ch.tri_sub[t] == s:
                    rs = [int(r) for r in ch.tri[t]]
                    order = [int(self.dec.rec_w[r]) for r in rs]
                    if sorted(order) == sorted(ws):
                        return [rs[order.index(w)] for w in ws], 0.0
        sub = ch.g.submeshes[s]
        img, wrap = self.images[sub.texture], submesh_wrap(sub)
        P = self.dec.wpos[ws]
        X = SAMPLE_BARY @ P
        uvo = self.project_uv(X, ws, s)
        want = sample_bilinear(img, uvo[:, 0], uvo[:, 1], wrap)
        recs = [self.w_chart_rec[(w, s)] for w in ws]
        common = sorted(set(recs[0]) & set(recs[1]) & set(recs[2]))
        combos = [[r[c] for r in recs] for c in common]
        others = [list(r.values()) for r in recs]
        for a in others[0]:
            for b in others[1]:
                for c in others[2]:
                    if len(combos) < 64 and [a, b, c] not in combos:
                        combos.append([a, b, c])
        best, best_err = None, None
        for rs in combos:
            uv = SAMPLE_BARY @ np.array([self.uv_of(r, w) for r, w in zip(rs, ws)])
            got = sample_bilinear(img, uv[:, 0], uv[:, 1], wrap)
            err = np.abs(got - want)[:, :3].mean(1)
            # Judge by typical and worst sampled colour error.
            score = max(err.mean() / self.mean_tol, err.max() / self.max_tol)
            if best is None or score < best_err:
                best, best_err = rs, score
        return best, best_err

    def bake_tile(self, ws, s, corners_px, x, y, c):
        """Bake the c*c cell at (x,y) of texture for face ws (clamped barycentrics dilate)."""
        sub = self.ch.g.submeshes[s]
        img, wrap = self.images[sub.texture], submesh_wrap(sub)
        P = self.dec.wpos[ws]
        m = np.array([[corners_px[1, 0] - corners_px[0, 0], corners_px[2, 0] - corners_px[0, 0]],
                      [corners_px[1, 1] - corners_px[0, 1], corners_px[2, 1] - corners_px[0, 1]]])
        inv = np.linalg.inv(m)
        ys, xs = np.mgrid[y:y + c, x:x + c]
        acc = np.zeros((c, c, 4))
        offsets = [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)]
        for ox, oy in offsets:
            px, py = (xs + ox).ravel(), (ys + oy).ravel()
            l12 = inv @ np.stack([px - corners_px[0, 0], py - corners_px[0, 1]])
            l1, l2 = np.clip(l12[0], 0, 1), np.clip(l12[1], 0, 1)
            over = l1 + l2 > 1
            total = l1[over] + l2[over]
            l1[over] /= total
            l2[over] /= total
            l0 = 1 - l1 - l2
            X = l0[:, None] * P[0] + l1[:, None] * P[1] + l2[:, None] * P[2]
            uvq = self.project_uv(X, ws, s)
            acc += sample_bilinear(img, uvq[:, 0], uvq[:, 1], wrap).reshape(c, c, 4)
        return acc / len(offsets)


def wrap_coord(x, size, mode):
    if mode == 1:
        return np.mod(x, size)
    if mode == 2:
        m = np.mod(x, 2 * size)
        return np.where(m < size, m, 2 * size - 1 - m)
    return np.clip(x, 0, size - 1)


def texture_footprint(ch, tex, W, H, dilate=2):
    """Texels any original triangle can sample from texture slot `tex`.
    Animated face materials are included whatever their resting texture."""
    used = np.zeros((H, W), bool)
    for t, rs in enumerate(ch.tri):
        s = int(ch.tri_sub[t])
        sub = ch.g.submeshes[s]
        if sub.texture != tex and sub.secondary != tex and not (
                ch.reason[s] == 'face-animation' and tex in ch.k.pat_textures):
            continue
        px = ch.uv[rs] * [W, H]
        n = int(min(1400, max(4, math.ceil(np.ptp(px, 0).max() * 1.5) + 2)))
        i, j = np.mgrid[0:n + 1, 0:n + 1]
        keep = i + j <= n
        l1, l2 = (i[keep] / n), (j[keep] / n)
        l0 = 1 - l1 - l2
        uv = l0[:, None] * ch.uv[rs[0]] + l1[:, None] * ch.uv[rs[1]] + l2[:, None] * ch.uv[rs[2]]
        x = wrap_coord(np.floor(uv[:, 0] * W), W, (sub.flags >> 8) & 3).astype(int)
        y = wrap_coord(np.floor((1 - uv[:, 1]) * H), H, (sub.flags >> 10) & 3).astype(int)
        used[y, x] = True
    for _ in range(dilate):
        grown = used.copy()
        grown[1:] |= used[:-1]
        grown[:-1] |= used[1:]
        grown[:, 1:] |= used[:, :-1]
        grown[:, :-1] |= used[:, 1:]
        used = grown
    return used


def pack_free(used, sizes, min_tile=4, max_tile=64):
    """Place one square, ETC1-block aligned cell per triangle in wholly unused
    4x4 blocks of the original atlas. Sizes are in priority order; when space
    runs out at the minimum size, the lowest-priority cells are left as None."""
    H, W = used.shape
    blocks = ~used[:H // 4 * 4, :W // 4 * 4].reshape(H // 4, 4, W // 4, 4).any((1, 3))
    scale = 1.0

    def attempt(scale, max_tile, partial):
        occupied = ~blocks
        placed = [None] * len(sizes)
        Hb, Wb = occupied.shape
        order = range(len(sizes)) if partial else sorted(range(len(sizes)), key=lambda i: -sizes[i])
        for i in order:
            c = int(min(max_tile, max(min_tile, math.ceil(sizes[i] * scale)))) + 2 * TILE_GUTTER
            cb = (c + 3) // 4
            if cb > Hb or cb > Wb:
                if partial:
                    continue
                return None
            integral = np.pad(occupied.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
            window = integral[cb:, cb:] - integral[:-cb, cb:] - integral[cb:, :-cb] + integral[:-cb, :-cb]
            spots = np.argwhere(window == 0)
            if len(spots) == 0:
                if partial:
                    continue
                return None
            by, bx = spots[0]
            occupied[by:by + cb, bx:bx + cb] = True
            placed[i] = (int(bx) * 4, int(by) * 4, cb * 4)
        return placed

    for _ in range(30):
        placed = attempt(scale, max_tile, False)
        if placed is not None:
            return placed
        scale *= 0.85
        max_tile = max(min_tile, int(max_tile * 0.9))
    return attempt(0.0, min_tile, True)


def texel_density(ch, users, W, H):
    dens = []
    for s in users:
        sub = ch.g.submeshes[s]
        for a, b, c in ch.tri[sub.first // 3:(sub.first + sub.count) // 3]:
            A3 = 0.5 * np.linalg.norm(np.cross(ch.pos[b] - ch.pos[a], ch.pos[c] - ch.pos[a]))
            U = ch.uv[[a, b, c]] * [W, H]
            A2 = 0.5 * abs(cross2(U[1] - U[0], U[2] - U[0]))
            if A3 > 1e-12:
                dens.append(math.sqrt(A2 / A3))
    return float(np.median(dens))


def bone_transforms(ch):
    """Each bone's authored neutral pose (model = skin @ L + t), recovered by
    least squares from its own vertices; exact to float precision."""
    out = {}
    src = ch.k.source[:, 0:3].astype(np.float64)
    for m in np.unique(ch.k.matrix):
        idx = np.nonzero(ch.k.matrix == m)[0]
        X = np.hstack([src[idx], np.ones((len(idx), 1))])
        # Needs four non-coplanar vertices; otherwise that bone's vertices never move.
        sv = np.linalg.svd(X - np.r_[X.mean(0)[:3], 0], compute_uv=False) if len(idx) >= 4 else [0]
        if len(idx) < 4 or sv[2] < 1e-6 * max(sv[0], 1e-12):
            continue
        A, *_ = np.linalg.lstsq(X, ch.pos[idx], rcond=None)
        if np.abs(X @ A - ch.pos[idx]).max() > 1e-3 or abs(np.linalg.det(A[:3])) < 1e-9:
            continue
        out[int(m)] = (A[:3], A[3])
    return out


def cook(gmdl_path, dskm_path, texture_dir, out_dir, ratio, max_error, eye_radius, chart_penalty, rounds=4,
         soft=1.4, hard=3.0, head_cap=0.035, body_cap=0.07, variant='main', part_floor=0.3, all_head=False,
         piece_keep=(0.75, 0.5), lock_borders=True):
    stem = Path(gmdl_path).name
    tag = hashlib.sha1((stem if variant == 'main' else f'{stem}:{variant}').encode()).hexdigest()[:6]
    model_dir = Path(out_dir) / 'models' / 'o3ds' / ('' if variant == 'main' else variant)
    ch = Character(gmdl_path, dskm_path)
    if getattr(ch.k, 'extra_animation', False) or getattr(ch.k, 'morph', False):
        return dict(model=stem, skipped='UV/colour/morph animation: keeps the generic cook')
    ch.measure_detail(texture_dir)
    g = ch.g
    names = list(g.names)
    total = len(ch.tri)
    extra_locks = set()
    for attempt in range(rounds):
        dec = Decimator(ch, eye_radius=eye_radius, chart_penalty=chart_penalty, extra_locks=extra_locks,
                        head_cap=head_cap, body_cap=body_cap, part_floor=part_floor, all_head=all_head,
                        piece_keep=piece_keep, lock_borders=lock_borders)
        target = max(int(total * ratio) - len(dec.fixed_faces), 8)
        faces = dec.run(target, max_error)
        baker = Baker(ch, dec, texture_dir)
        corners, tile_faces, errors = [], [], {}
        for ws, s in faces:
            rs, err = baker.retained(ws, s)
            if err > 1.0:
                tile_faces.append(len(corners))
                errors[len(corners)] = err
            corners.append([s, ws, [(r, tuple(baker.uv_of(r, w)) if baker.moved(w) else None)
                                    for r, w in zip(rs, ws)]])
        by_texture = defaultdict(list)
        for i in sorted(tile_faces, key=lambda i: -errors[i]):
            by_texture[g.submeshes[corners[i][0]].texture].append(i)
        rebaked, unplaced, placed_count, residual = {}, [], 0, 0
        for tex, items in by_texture.items():
            src = baker.images[tex]
            H, W = src.shape[:2]
            users = [u for u in ch.texture_users[tex] if u >= 0]
            density = texel_density(ch, users, W, H)
            sizes = []
            for i in items:
                P = dec.wpos[corners[i][1]]
                local = baker.local_density(corners[i][1], corners[i][0], W, H)
                sizes.append(max(density, min(local, 4 * density)) *
                             math.sqrt(np.linalg.norm(np.cross(P[1] - P[0], P[2] - P[0]))))
            cells = pack_free(texture_footprint(ch, tex, W, H), sizes)
            out = src.copy()
            for i, cell in zip(items, cells):
                if cell is None:
                    # No room left: a modest colour mismatch is acceptable low-poly
                    # simplification; a wrong-looking one keeps authored detail.
                    # Patterned surfaces (shell hexes, belts) accept less colour error
                    # than plain ones before their authored detail is kept instead.
                    face_detail = max(dec.w_detail[w] for w in corners[i][1])
                    limit = min(1.2, soft) if face_detail >= 0.2 and soft <= 1.4 else soft
                    if errors[i] > limit and attempt < rounds - 1:
                        unplaced.append(i)
                    elif errors[i] > hard:
                        residual += 1
                    continue
                x, y, c = cell
                s, ws, cs = corners[i]
                x0, x1, y0, y1 = x + TILE_GUTTER, x + c - TILE_GUTTER, y + TILE_GUTTER, y + c - TILE_GUTTER
                corners_px = np.array([[x0, y1], [x1, y1], [x0, y0]], np.float64)
                out[y:y + c, x:x + c] = baker.bake_tile(ws, s, corners_px, x, y, c)
                corners[i][2] = [(r, (px / W, 1 - py / H)) for (r, _), (px, py) in zip(cs, corners_px)]
                placed_count += 1
            if any(cell is not None for cell in cells):
                rebaked[tex] = (f'{names[tex]}{UV_SUFFIX}{tag}', out)
        if not unplaced:
            break
        # No room for these re-bakes: keep the authored surface under each
        # mismatched triangle and retry.
        for i in unplaced:
            P = dec.wpos[corners[i][1]]
            centre = P.mean(0)
            radius = np.sqrt(((P - centre) ** 2).sum(1)).max() * 1.05
            for w in corners[i][1]:
                for x in dec.absorbed_ids[w]:
                    if np.linalg.norm(dec.wpos[x] - centre) <= radius:
                        extra_locks.add(int(x))
    for tex, (name, img) in rebaked.items():
        png = Path(out_dir) / 'textures' / f'{name}.png'
        pixels = np.clip(img + 0.5, 0, 255).astype(np.uint8)
        Image.fromarray(pixels).save(png)
        # Same encoder settings as the authored cook (tools/cook_model.py).
        fmt = 'etc1a4' if pixels[..., 3].min() < 255 else 'etc1'
        subprocess.run([str(tool('tex3ds')), '-f', fmt, '-q', 'high', '-z', 'none', '-o', str(png.with_suffix('.t3x')),
                        str(png)], check=True, stdout=subprocess.DEVNULL)
    # Authored Wii meshes store three private records per triangle; byte-identical
    # records (same binding, material and animation shape) collapse to one.
    shape_of = defaultdict(tuple)
    for k_, (first, count) in enumerate(ch.k.ranges):
        for r in range(first, first + count):
            shape_of[r] += (k_,)
    canon_key = {}
    canonical = {}
    for t, rs in enumerate(ch.tri):
        for r in rs:
            r = int(r)
            key = (g.records[r].tobytes(), ch.k.source[r].tobytes(), shape_of[r], int(ch.tri_sub[t]))
            canonical[r] = canon_key.setdefault(key, r)
    corners = [[s, ws, [(canonical[r], uvo) for r, uvo in cs]] for s, ws, cs in corners]
    face_keys = defaultdict(list)
    for s, ws, cs in corners:
        face_keys[s].append([(int(r), None if uvo is None else tuple(np.float32(x).item() for x in uvo))
                             for r, uvo in cs])
    for t in dec.fixed_faces:
        face_keys[int(ch.tri_sub[t])].append([(canonical[int(r)], None) for r in ch.tri[t]])
    all_keys = sorted({k for fs in face_keys.values() for f in fs for k in f},
                      key=lambda k: (k[0], k[1] is not None, k[1] or (0.0, 0.0)))
    index_of = {k: i for i, k in enumerate(all_keys)}
    if len(all_keys) > g.header[1]:
        raise RuntimeError('variant has more vertices than the original')
    records = g.records[[k[0] for k in all_keys]].copy()
    skin = ch.k.source[[k[0] for k in all_keys], 0:3].astype(np.float32).copy()
    bones = dec.bones
    moved = 0
    for i, (r, uvo) in enumerate(all_keys):
        if uvo is not None:
            records[i, 24:32] = np.array(uvo, '<f4').view(np.uint8)
        w = int(dec.rec_w[r])
        if not np.array_equal(dec.wpos[w], dec.opos[w]):
            # Same bone, new place: re-bind through the authored neutral pose.
            L, t = bones[int(ch.k.matrix[r])]
            skin[i] = ((dec.wpos[w] - t) @ np.linalg.inv(L)).astype(np.float32)
            records[i, 0:12] = dec.wpos[w].astype('<f4').view(np.uint8)
            moved += 1
    indices, spans = [], []
    for s in range(len(g.submeshes)):
        first = len(indices)
        for f in face_keys.get(s, []):
            indices.extend(index_of[k] for k in f)
        spans.append((first, len(indices) - first))
    out_names = [rebaked[i][0] if i in rebaked else n for i, n in enumerate(names)]
    version = 3 if moved else 2 if rebaked or any(k[1] is not None for k in all_keys) else 1
    write_variant(model_dir / stem, g, records, [k[0] for k in all_keys],
                  spans, indices, out_names, version=version, skin=skin if version == 3 else None)
    return dict(model=stem, format=version, moved_vertices=moved, rounds=attempt + 1, locked_for_texture=len(extra_locks), original_triangles=total, triangles=len(indices) // 3,
                original_vertices=g.header[1], vertices=len(records), protected_triangles=len(dec.fixed_faces),
                rebaked_triangles=placed_count, colour_residual=residual, approximated=len(tile_faces) - placed_count, textures=sorted(n for n, _ in rebaked.values()),
                materials=ch.reason, locks=dict(dec.lock_reason))


# Art direction: fraction of authored triangles kept, per character family.
# Characters with long hair, crowns or open mouths keep more.
BUDGET = {
    'rosalina': 0.62, 'koopa_troopa': 0.66, 'peach': 0.55, 'daisy': 0.55,
    'baby_daisy': 0.52, 'baby_peach': 0.52, 'dry_bowser': 0.55, 'bowser': 0.52,
    'funky_kong': 0.5, 'bowser_jr': 0.52,
}


RIVAL_SCALE = 0.7


def character_of(stem):
    for name in sorted(BUDGET, key=len, reverse=True):
        if stem.startswith(name + '_'):
            return name
    return None


def bank_for(models, stem):
    """<stem>.dskm/.dobj, also without a _000 partition suffix; None = static."""
    stems = [stem] + ([stem[:-4]] if stem.endswith('_000') else [])
    for base in stems:
        for ext in ('.dskm', '.dobj'):
            if (models / f'{base}{ext}').exists():
                return models / f'{base}{ext}'
    return None


def cook_one(job):
    try:
        return _cook_one(job)
    except Exception as error:  # one unusual model must not stop a batch
        return dict(model=f'{job[0]}.gmdl', skipped=f'{type(error).__name__}: {error}')


def _cook_one(job):
    stem, a = job
    if a.landmark:
        # Track landmarks (statues, airships, signature props): seen large and
        # often, so every part keeps tight shape; no head/body split applies.
        info = cook(a.models / f'{stem}.gmdl', bank_for(a.models, stem), a.textures, a.output,
                    a.ratio, a.max_error, a.eye_radius, a.chart_penalty, head_cap=a.landmark_cap,
                    body_cap=a.landmark_cap, all_head=True, piece_keep=(0.5, 0.35),
                    lock_borders=not a.slide_borders)
        info['budget'] = a.ratio
        return info
    ratio = BUDGET.get(character_of(stem), a.ratio) if a.ratio_override is None else a.ratio_override
    head_cap, body_cap, eye_radius, part_floor = a.head_cap, a.body_cap, a.eye_radius, 0.3
    if a.variant == 'rival':
        # Opponents (seen mostly at a distance, like Mario Kart DS rival models).
        ratio, head_cap, body_cap = ratio * RIVAL_SCALE, head_cap * 3, body_cap * 2.5
        eye_radius, part_floor = eye_radius * 0.5, 0.15
    info = cook(a.models / f'{stem}.gmdl', a.models / f'{stem}.dskm', a.textures, a.output,
                ratio, a.max_error, eye_radius, a.chart_penalty, head_cap=head_cap, body_cap=body_cap,
                variant=a.variant, part_floor=part_floor, soft=2.4 if a.variant == 'rival' else 1.4)
    info['budget'] = ratio
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--landmark', action='store_true', help='track landmark objects (any model + bank)')
    ap.add_argument('--landmark-cap', type=float, default=0.03, help='landmark shape error (part size)')
    ap.add_argument('--slide-borders', action='store_true',
                    help='landmark: open borders slide along themselves instead of staying locked '
                         '(single rigid hard-surface models such as karts)')
    ap.add_argument('--variant', choices=('main', 'rival'), default='main',
                    help='rival: leaner opponent drivers in models/o3ds/rival/')
    ap.add_argument('--models', required=True, type=Path, help='staged models/ directory')
    ap.add_argument('--textures', required=True, type=Path, help='staged textures/ directory with PNGs')
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--only', action='append', help='model stem (e.g. mario_mdf_kart_driver)')
    ap.add_argument('--ratio', type=float, default=0.40, help='default budget (see BUDGET)')
    ap.add_argument('--ratio-override', type=float, help='ignore BUDGET and use this for every model')
    ap.add_argument("--max-error", type=float, default=0.5,
                    help='weighted silhouette error ceiling, relative to each bone part')
    ap.add_argument('--eye-radius', type=float, default=0.06)
    ap.add_argument('--chart-penalty', type=float, default=0.08)
    ap.add_argument('--head-cap', type=float, default=0.025, help='max head shape error (head size)')
    ap.add_argument('--body-cap', type=float, default=0.12, help='max body shape error (bone part size)')
    a = ap.parse_args()
    (a.output / 'models' / 'o3ds' / ('' if a.variant == 'main' else a.variant)).mkdir(parents=True, exist_ok=True)
    (a.output / 'textures').mkdir(parents=True, exist_ok=True)
    # The race loads models/<vehicle prefix>driver.gmdl with <prefix>driver.dskm.
    stems = a.only or sorted(p.name[:-5] for p in a.models.glob('*_driver.gmdl'))
    jobs = [(stem, a) for stem in stems]
    report = []
    with multiprocessing.Pool(a.jobs) as pool:
        for info in pool.imap_unordered(cook_one, jobs):
            report.append(info)
            print(json.dumps({k: info.get(k) for k in ('model', 'rounds', 'locked_for_texture', 'skipped',
                                                        'original_triangles', 'triangles', 'vertices',
                                                        'rebaked_triangles', 'approximated', 'colour_residual')}),
                  flush=True)
    receipt = a.output / 'lowpoly-receipt.json'
    old = json.loads(receipt.read_text()) if receipt.exists() else []
    merged = {r['model']: r for r in old}
    merged.update({r['model']: r for r in report})
    receipt.write_text(json.dumps(sorted(merged.values(), key=lambda r: r['model']), indent=1))


if __name__ == '__main__':
    main()
