#!/usr/bin/env python3
"""AST-based GENLAB_* read detector. Run identically local + VPS.

Emits:
  STATIC <N>        # literal os.environ["X"] / os.getenv("X") / os.environ.get("X")
  R <var>           # each static literal read
  DYNAMIC <N>       # f"GENLAB_..._{niche}" composed reads (whole f-string with literal prefix)
  D <literal-prefix>

Skips scratch/tool dirs.
"""
import ast
import pathlib
import sys

SKIP = {'.venv', 'node_modules', '__pycache__', '.tmp', 'build', 'dist',
        '.hypothesis', '.playwright-mcp', '.grimp_cache', '.import_linter_cache',
        '.audit', '.serena', '.media', '.logs'}

reads = set()
dynamic = set()

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.')
for p in root.rglob('*.py'):
    if any(x in p.parts for x in SKIP):
        continue
    try:
        tree = ast.parse(p.read_text(errors='ignore'))
    except SyntaxError:
        continue
    for n in ast.walk(tree):
        # os.environ.get("X") / os.getenv("X")
        if isinstance(n, ast.Call) and n.args:
            arg0 = n.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str) \
               and arg0.value.startswith('GENLAB_'):
                reads.add(arg0.value)
        # os.environ["X"]
        if isinstance(n, ast.Subscript):
            slc = n.slice
            if isinstance(slc, ast.Constant) and isinstance(slc.value, str) \
               and slc.value.startswith('GENLAB_'):
                reads.add(slc.value)
        # f"GENLAB_..._{niche}" dynamic composition
        if isinstance(n, ast.JoinedStr):
            lit = ''.join(v.value for v in n.values if isinstance(v, ast.Constant)
                          and isinstance(v.value, str))
            if lit.startswith('GENLAB_'):
                dynamic.add(lit)

print(f'STATIC {len(reads)}')
for v in sorted(reads):
    print(f'R {v}')
print(f'DYNAMIC {len(dynamic)}')
for v in sorted(dynamic):
    print(f'D {v}')
