#!/usr/bin/env python3
"""AST-based GENLAB_* read detector (v2). Run identically local + VPS.

v2 fix: a string starting with GENLAB_ is only a variable NAME if the whole
string matches ^GENLAB_[A-Z0-9_]+$. v1 counted log and exit messages such as
"GENLAB_DOMAIN is required for ..." as reads (16 static + 5 dynamic FPs).

Known limitation, deliberately not fixed here: detects only literal-string
reads in Python. Reads via shell, systemd ExecCondition, or os.environ.get(k)
with a variable key are invisible. See Section 3.
"""
import ast
import pathlib
import re
import sys

VAR = re.compile(r'^GENLAB_[A-Z0-9_]+$')
SKIP = {'.venv', 'node_modules', '__pycache__', '.tmp', 'build', 'dist',
        '.hypothesis', '.playwright-mcp', '.grimp_cache',
        '.import_linter_cache', '.audit', '.serena', '.media', '.logs'}

reads, dynamic = set(), set()
root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.')

for p in root.rglob('*.py'):
    if any(x in p.parts for x in SKIP):
        continue
    try:
        tree = ast.parse(p.read_text(errors='ignore'))
    except SyntaxError:
        continue
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and n.args:
            a = n.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str) \
               and VAR.match(a.value):
                reads.add(a.value)
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
           and isinstance(n.slice.value, str) and VAR.match(n.slice.value):
            reads.add(n.slice.value)
        if isinstance(n, ast.JoinedStr):
            lit = ''.join(v.value for v in n.values
                          if isinstance(v, ast.Constant)
                          and isinstance(v.value, str))
            # a real dynamic read is a bare prefix: "GENLAB_X_" + f"{niche}"
            if lit.startswith('GENLAB_') and lit.endswith('_') \
               and VAR.match(lit + 'X'):
                dynamic.add(lit)

print(f'STATIC {len(reads)}')
for v in sorted(reads):
    print(f'R {v}')
print(f'DYNAMIC {len(dynamic)}')
for v in sorted(dynamic):
    print(f'D {v}')
