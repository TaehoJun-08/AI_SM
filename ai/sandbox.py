"""A best-effort sandbox for the Python Nova runs on your behalf.

It is NOT a security boundary against a hostile stranger: it exists so Nova can test an idea or do a quick
calculation for the person it's talking to, not so this app could safely run code from the public internet.
The subprocess gets no `import`, no `open`, no network -- just a short list of safe builtins -- and a hard
timeout. If this app is ever opened to untrusted users, this is the first thing to harden further or replace
with a real container."""
import subprocess
import sys
import tempfile

TIMEOUT = 8
MAX_OUTPUT = 4000
MAX_CODE = 20_000

ALLOWED_BUILTINS = [
    "abs", "all", "any", "bool", "chr", "dict", "divmod", "enumerate", "filter", "float",
    "format", "frozenset", "hash", "hex", "int", "isinstance", "len", "list", "map", "max",
    "min", "oct", "ord", "pow", "print", "range", "repr", "reversed", "round", "set", "slice",
    "sorted", "str", "sum", "tuple", "zip", "True", "False", "None",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError", "ZeroDivisionError",
    "StopIteration", "ArithmeticError", "RuntimeError", "AssertionError",
]

_WRAPPER = """
import sys, builtins as _b
code = sys.stdin.read()
_allowed = {allowed!r}
_safe = {{name: getattr(_b, name) for name in _allowed if hasattr(_b, name)}}
_g = {{"__builtins__": _safe}}
try:
    exec(compile(code, "<nova>", "exec"), _g)
except Exception as e:
    print(f"Error: {{type(e).__name__}}: {{e}}", file=sys.stderr)
"""


def run(code):
    """Returns (output, ok). `output` already mixes stdout and stderr, truncated to MAX_OUTPUT."""
    code = (code or "")[:MAX_CODE]
    script = _WRAPPER.format(allowed=ALLOWED_BUILTINS)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", "-c", script],
                input=code, capture_output=True, text=True, timeout=TIMEOUT, cwd=tmp)
        output = (proc.stdout or "") + (proc.stderr or "")
        return output[:MAX_OUTPUT] or "(no output)", proc.returncode == 0 and not proc.stderr
    except subprocess.TimeoutExpired:
        return f"Timed out after {TIMEOUT}s.", False
    except Exception as e:
        return f"Sandbox error: {e}", False
