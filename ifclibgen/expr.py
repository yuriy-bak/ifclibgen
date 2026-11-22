"""Safe evaluation of template expressions.

Numbers pass through unchanged. Strings are parsed as Python expressions
restricted to arithmetic, comparisons, conditionals, lists and a small set of
functions. Names resolve to template parameters.
"""

import ast
import math
import re

_ALLOWED = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Call, ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple, ast.Subscript,
    ast.Slice, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt,
    ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
)


def by_range(value, limits, results):
    """Range lookup: first result whose upper limit >= value.

    by_range(E, [900, 1300, 2000], [1, 2, 3]) -> 1 for E<=900, 2 for E<=1300 ...
    """
    for lim, res in zip(limits, results):
        if value <= lim:
            return res
    raise ValueError(f"{value} is above the last limit {limits[-1]}")


FUNCS = {
    "min": min, "max": max, "abs": abs, "round": round, "int": int, "float": float,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "radians": math.radians, "ceil": math.ceil, "floor": math.floor, "pi": math.pi,
    "by_range": by_range, "len": len, "str": str,
}

_cache = {}


class ExprError(Exception):
    pass


def _compile(src):
    code = _cache.get(src)
    if code is None:
        try:
            tree = ast.parse(src, mode="eval")
        except SyntaxError as e:
            raise ExprError(f"syntax error in expression {src!r}: {e}") from None
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED):
                raise ExprError(f"{type(node).__name__} is not allowed in {src!r}")
            if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
                raise ExprError(f"only plain function calls are allowed in {src!r}")
        code = compile(tree, "<expr>", "eval")
        _cache[src] = code
    return code


def ev(value, env):
    """Evaluate a number, an expression string, or a list of those."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [ev(v, env) for v in value]
    if isinstance(value, str):
        try:
            return eval(_compile(value), {"__builtins__": {}}, {**FUNCS, **env})
        except ExprError:
            raise
        except Exception as e:
            raise ExprError(f"cannot evaluate {value!r}: {e}") from None
    raise ExprError(f"unsupported value {value!r}")


_FIELD = re.compile(r"\{([^{}]+)\}")


def fmt(text, env):
    """Fill {expr} fields in a text template, e.g. 'Diffuser Ø{size}'."""
    def repl(m):
        v = ev(m.group(1), env)
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return str(v)
    return _FIELD.sub(repl, text)


def value(v, env):
    """Pset/attribute value: '=expr' is evaluated, other strings are text templates."""
    if isinstance(v, str):
        if v.startswith("="):
            return ev(v[1:], env)
        return fmt(v, env)
    return v
