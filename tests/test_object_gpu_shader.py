"""Exercise the actual DOBJ shader branch against its existing CPU contract.

This tiny instruction interpreter checks operands/swizzles and singular-normal
fallbacks. It does not emulate PICA's reduced floating-point precision or prove
rendered parity; the shader must also compile and receive visual QA.
"""
import math
from pathlib import Path
import random
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / "src/mkw.v.pica").read_text()


def shader_normal(matrix, normal):
    block = SOURCE.split("ifu skinnedNormalsDirect\n", 1)[1].split("\n          .else\n", 1)[0]
    registers = {"innormal": [*normal, 0], "constants": [0, 1, .35, .65],
                 "normalEpsilon": [1e-12, 1e-10, 0, 0]}
    registers.update({f"bones[a0.x{suffix}]": list(row) for suffix, row in zip(("", "+1", "+2"), matrix)})
    compare = False
    active = True

    def read(token):
        token = token.strip()
        # Matrix indexing contains a period but no operand swizzle.
        if token.startswith("bones["):
            return registers[token]
        base, _, swizzle = token.partition(".")
        value = registers.get(base, [0] * 4)
        return [value["xyzw".index(c)] for c in swizzle] if swizzle else value

    def write(token, value):
        base, _, swizzle = token.partition(".")
        target = registers.setdefault(base, [0] * 4)
        for index, component in enumerate(swizzle or "xyzw"):
            target["xyzw".index(component)] = value[index if len(value) > 1 else 0]

    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line == "ifc cmp.x":
            active = compare
            continue
        if line == ".else":
            active = not compare
            continue
        if line == ".end":
            active = True
            continue
        if not active:
            continue
        op, rest = line.split(None, 1)
        operands = [part.strip() for part in rest.split(",")]
        if op == "cmp":
            assert operands[1:3] == ["lt", "lt"]
            compare = read(operands[0])[0] < read(operands[3])[0]
            continue
        dest, first, *remaining = operands
        a = read(first)
        b = read(remaining[0]) if remaining else []
        if op == "dp3":
            value = [sum(x * y for x, y in zip(a[:3], b[:3]))]
        elif op == "rsq":
            value = [1 / math.sqrt(a[0])]
        elif op == "mul":
            value = [x * b[i if len(b) > 1 else 0] for i, x in enumerate(a)]
        elif op == "mov":
            value = a
        else:
            raise AssertionError(f"Uncovered shader instruction: {line}")
        write(dest, value)
    return registers["r15"][:3]


def cpu_normal(matrix, normal):
    vector = [sum(row[i] * normal[i] for i in range(3)) for row in matrix]
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector] if length > .00001 else [0, 1, 0]


class ObjectGpuShaderTests(unittest.TestCase):
    def check(self, matrix, normal):
        for actual, expected in zip(shader_normal(matrix, normal), cpu_normal(matrix, normal)):
            self.assertAlmostEqual(actual, expected, places=12)

    def test_nonuniform_negative_and_sheared_scale_retains_dobj_semantics(self):
        for matrix in ([[2, 0, 0, 900], [0, 1, 0, -800], [0, 0, .5, 700]],
                       [[-3, .5, 0, 0], [1, 2, .3, 0], [0, .6, 4, 0]]):
            self.check(matrix, [1, 1, .5])

    def test_singular_zero_and_tiny_normals_use_cpu_fallback(self):
        for scale in (0, 1e-9, 5e-6, 2e-5):
            self.check([[scale, 0, 0, 12], [0, 0, 0, 13], [0, 0, 0, 14]], [1, 0, 0])

    def test_varied_affine_matrices_match_cpu_equation(self):
        rng = random.Random(414)
        for _ in range(300):
            matrix = [[rng.uniform(-4, 4) for _ in range(4)] for _ in range(3)]
            self.check(matrix, [rng.uniform(-1, 1) for _ in range(3)])


if __name__ == "__main__":
    unittest.main()
