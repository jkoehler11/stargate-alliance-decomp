#!/usr/bin/env python3
"""Stage 3, first light: emit a function body that is one constant return, and score it byte for byte.

Stages 2a-2c scored the parser against the packages one column at a time (names, signatures, call targets).
Those are all *recall* measures: they ask whether the source contains something the package recorded. The emitter is
the first stage with no recall knob -- the package holds the compiled bytes, so an emitted body either equals them or
it does not, and a percentage is not available to hide behind.

The family here is deliberately the smallest one that is still real: a function body that is a single constant return
(`return true;`, `return 0;`, `return "";`, `return None;`, `return self;`, `return vect(0,0,0);`), an empty body, or
`return;`. Every one of the 2,197 such bodies in the dump ends `04 0b 47` (Return Nothing; EndOfScript), which is the
compiler's own trailer and not something the source spells -- so the emitter appends it and the comparison is exact.

What the corpus settled, and what the emitter therefore encodes (measured 2026-11-22, `--score`):

* **The declared return type picks the token, not the value.** `return 0` is `25` (IntZero) in an `int` function,
  `1e 00000000` (FloatConst) in a `float` one and `24 00` (ByteConst) in a `byte` one. `return 1.0` and `return 1`
  in a float function both emit `1e 0000803f`.
* **Small ints have their own tokens:** 0 -> `25`, 1 -> `26`, 2..255 -> `2c <byte>`, anything else `1d <int32>`.
* **A string is NUL-terminated** (`1f 00` for ""), and a `name` literal is a *name-table index* (`21 <index>`), so
  the emitter has to encode the archive's compact index and join the package's own name table to do it.
* **A vector literal is one token:** `23 <3 x f32>`.

Anything outside the family is refused with the reason named (a variable operand needs the class's property table, an
enum constant needs its declared values, two statements need control flow), because a guess here is a wrong body that
looks like a measurement. The residue is what the next stage reads.

    python3 tools/uscript-emit.py --check                       # synthetic, no dump, writes nothing
    python3 tools/uscript-emit.py --tree $WORK/scripts-all \\
        --dump $SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/System -o inventory

`inventory/uscript_emit.csv` is one row per modelled body: what was emitted, what the package holds, and whether they
are the same bytes. A census row carries the population counts, and `--check` re-derives them from the rows.
"""

import argparse
import collections
import csv
import importlib.util
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_spec = importlib.util.spec_from_file_location("uscript_lex", os.path.join(ROOT, "tools", "uscript-lex.py"))
lexer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lexer)

_spec2 = importlib.util.spec_from_file_location("uscript_decls", os.path.join(ROOT, "tools", "uscript-decls.py"))
decls = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(decls)

SKIP = {"space", "comment", "high"}
TRAILER = bytes([0x04, 0x0B, 0x47])          # Return Nothing; EndOfScript -- every shipped body ends with it
ARTIFACT = os.path.join(ROOT, "inventory", "uscript_emit.csv")
FIELDS = ["package", "class", "state", "function", "model", "rettype", "source", "ours", "theirs",
          "verdict", "walked", "differ"]

# `function native static final int Foo()` -- the type is whatever is left after the modifiers.
MODIFIERS = {"native", "static", "final", "simulated", "latent", "iterator", "singular", "private", "protected",
             "public", "reliable", "unreliable", "exec", "coerce", "const", "config", "global", "noexport",
             "preoperator", "postoperator", "operator", "abstract", "deprecated", "travel", "repnotify"}


class Refused(Exception):
    pass


# The record classes an `InstanceVariable`/`LocalVariable` operand can name. The operand is the record's *index* --
# positive for an export, negative for an import -- so a property needs no order rebuilt, just its own row found.
PROPERTY_CLASSES = {"IntProperty", "ByteProperty", "BoolProperty", "FloatProperty", "ObjectProperty",
                    "NameProperty", "StrProperty", "StringProperty", "StructProperty", "ArrayProperty",
                    "ClassProperty", "DelegateProperty", "PointerProperty", "InterfaceProperty"}
PROP_TYPE = {"IntProperty": "int", "ByteProperty": "byte", "BoolProperty": "bool", "FloatProperty": "float",
             "ObjectProperty": "object", "ClassProperty": "object", "NameProperty": "name",
             "StrProperty": "string", "StringProperty": "string", "StructProperty": "struct",
             "ArrayProperty": "array", "DelegateProperty": "delegate", "PointerProperty": "pointer",
             "InterfaceProperty": "object"}
LOCAL_TOKEN, INSTANCE_TOKEN, DEFAULT_TOKEN = 0x00, 0x01, 0x02


# ----------------------------------------------------------------------------------------------------------------
# the archive's compact index, written the way `uscript-dis.read_index` reads it

def write_index(value):
    """`0` -> one byte; six bits then seven per byte, with the sign in bit 7 and bit 6 as "more follows"."""
    if value == 0:
        return bytes([0])
    sign = 0x80 if value < 0 else 0
    v = abs(value)
    first = sign | (v & 0x3F)
    v >>= 6
    if not v:
        return bytes([first])
    out = [first | 0x40]
    while v:
        b = v & 0x7F
        v >>= 7
        out.append(b | 0x80 if v else b)
    return bytes(out)


# ----------------------------------------------------------------------------------------------------------------
# the source side: every function of a class, at class scope or inside a state

def tokens_of(text):
    toks, bad = lexer.lex(text)
    if bad is not None:
        return None
    out = [(k, text[s:e]) for k, s, e in toks if k not in SKIP]
    return decls.unbuilt_removed(out)


def _paren_end(toks, i):
    depth = 0
    while i < len(toks):
        kind, val = toks[i]
        if kind == "op" and val == "(":
            depth += 1
        elif kind == "op" and val == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _brace_end(toks, i):
    depth = 0
    while i < len(toks):
        kind, val = toks[i]
        if kind == "op" and val == "{":
            depth += 1
        elif kind == "op" and val == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def params_of(toks, i):
    """`[(type, name)]` for a declaration's parameter list; `toks[i]` is its `(`.

    The declared type is what picks an argument's token (`PHYS_Swimming` is a byte, `1.0` a float), so the join to
    the callee's declaration has to carry it: `inventory/natives.csv` holds a parameter *count* and no types.
    """
    close = _paren_end(toks, i)
    if close is None:
        return []
    out = []
    for seg in _split_commas(toks[i + 1:close]):
        if not seg:
            continue
        words = [(k, v) for k, v in seg if k in ("ident", "keyword", "op")]
        cut = len(words)
        for n, (k, v) in enumerate(words):
            if k == "op" and v in ("=", "[", "."):
                cut = n
                break
        words = words[:cut]
        if not words:
            continue
        name = ""
        end = len(words)
        for n in range(len(words) - 1, -1, -1):
            if words[n][0] in ("ident", "keyword"):
                name, end = words[n][1], n
                break
        typ = "".join(v for _k, v in words[:end])
        for mod in ("out", "optional", "coerce", "const", "init"):
            if typ.lower().startswith(mod):
                typ = typ[len(mod):]
        out.append((typ, name))
    return out


def rettype_of(word_tokens):
    """The words between `function` and its name, minus modifiers: `int`, `float`, `VoiceChatRoom.EJoinChatResult`."""
    kept = [w for w in word_tokens if w.lower() not in MODIFIERS]
    return ".".join(kept) if kept else ""


def _modifiers_before(toks, i):
    """The declaration's own modifiers, which UnrealScript writes *in front of* the keyword: `native(257) final
    function ...`. A `native(...)` slot group is stepped over whole."""
    words, j = [], i - 1
    while j >= 0:
        kind, val = toks[j]
        if kind == "op" and val == ")":
            depth = 0
            while j >= 0:
                kk, vv = toks[j]
                if vv == ")":
                    depth += 1
                elif vv == "(":
                    depth -= 1
                    if depth == 0:
                        j -= 1
                        break
                j -= 1
            continue
        if kind in ("ident", "keyword") and val.lower() in MODIFIERS:
            words.append(val)
            j -= 1
            continue
        break
    return words


def enums_of(text):
    """`[(enum name, [(constant, ordinal)])]` -- a constant compiles to `24 <ordinal>` (measured: PHYS_Swimming).

    An enum is declared at class scope and is visible in every subclass, so the constant is looked up along the
    class chain, not in the body's own text.
    """
    toks = tokens_of(text)
    if toks is None:
        return []
    out = []
    for i, (kind, val) in enumerate(toks):
        if kind != "keyword" or val.lower() != "enum":
            continue
        j = i + 1
        if j >= len(toks) or toks[j][0] not in ("ident", "keyword"):
            continue
        name = toks[j][1]
        while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] == "{"):
            if toks[j][0] == "op" and toks[j][1] == ";":
                break
            j += 1
        if j >= len(toks) or toks[j][1] != "{":
            continue
        end = _brace_end(toks, j)
        if end is None:
            continue
        consts, ordinal = [], 0
        for seg in _split_commas(toks[j + 1:end]):
            if not seg:
                continue
            cname = seg[0][1]
            if len(seg) >= 3 and seg[1] == ("op", "=") and seg[2][0] == "number":
                ordinal = _number(seg[2][1])
            consts.append((cname, ordinal))
            ordinal += 1
        out.append((name, consts))
    return out


def functions_of(text):
    """`[(state, name, rettype, body_tokens, is_native, is_final, params)]` for every function/event."""
    toks = tokens_of(text)
    if toks is None:
        return []
    out = []
    # The brace depth of every token, computed once. Skipping a body whole (`i = end + 1`) is what makes a running
    # counter wrong: Mover's walk lost one level after a state and then read every later class-scope function as
    # nested, so `SetStoppedPosition`, `MoverLooped`, `UpdatePrecacheStaticMeshes` and `DisplayDebug` were missing
    # from a file whose braces balance. A table cannot drift.
    depth_at, d = [], 0
    for kind, val in toks:
        depth_at.append(d)
        if kind == "op" and val == "{":
            d += 1
        elif kind == "op" and val == "}":
            d -= 1
    i, state, state_depth = 0, "", None
    while i < len(toks):
        kind, val = toks[i]
        low = val.lower() if kind in ("keyword", "ident") else None
        if kind == "op" and val in ("{", "}"):
            if val == "}" and state_depth is not None and depth_at[i] == state_depth:
                state, state_depth = "", None
            i += 1
            continue
        if kind == "keyword" and low == "ignores" and (depth_at[i] == 0 or depth_at[i] == state_depth):
            # `ignores SeePlayer, HearNoise, KilledBy;` is not a comment: the compiler emits one bodyless Function
            # for each name, in the scope that writes the clause, and the package's script for it is the trailer.
            j = i + 1
            while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] == ";"):
                if toks[j][0] in ("ident", "keyword"):
                    out.append((state if depth_at[i] == state_depth and state else "", toks[j][1], "", None, False,
                                False, []))
                j += 1
            i = j + 1
            continue
        if kind == "keyword" and low == "state" and i + 1 < len(toks):
            j = i + 1
            # `state() LoopMove` is a state whose label is empty -- Mover writes twelve of its nineteen that way -- so
            # the name comes after the group, not immediately after the keyword (stage 2a measured the same form).
            if toks[j][0] == "op" and toks[j][1] == "(":
                close = _paren_end(toks, j)
                if close is None:
                    i += 1
                    continue
                j = close + 1
            label = toks[j][1] if j < len(toks) and toks[j][0] in ("ident", "keyword") else ""
            while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] in ("{", ";")):
                j += 1
            if label and j < len(toks) and toks[j][1] == "{":
                state, state_depth = label, depth_at[i] + 1
                i = j
            else:
                i = j + 1
            continue
        # A `delegate` declaration is one property (`__X__delegate`) *and* one Function named after it (75 names
        # across 20 classes), so it is a declaration this walk must read: its text carries no body and the package's
        # script is the stub.
        if (kind == "keyword" and low in ("function", "event", "delegate")
                and (depth_at[i] == 0 or depth_at[i] == state_depth)):
            j = i + 1
            words = []
            while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] == "("):
                if toks[j][0] in ("ident", "keyword"):
                    words.append(toks[j][1])
                j += 1
            close = _paren_end(toks, j)
            if not words or close is None:
                i += 1
                continue
            name, rettype = words[-1], rettype_of(words[:-1])
            params = params_of(toks, j)
            mods = [w.lower() for w in words[:-1] + _modifiers_before(toks, i)]
            is_native = "native" in mods
            # `final function X()` is a call the compiler binds to the Function record itself (`1c <index>`) rather
            # than resolving by name: `DestroyPlayerStatCache` and `myOnDeactivate` are the two bodies that say so.
            is_final = "final" in mods
            k = close + 1
            while k < len(toks) and not (toks[k][0] == "op" and toks[k][1] in ("{", ";")):
                k += 1
            if k >= len(toks) or toks[k][1] == ";":
                # a bodyless declaration is how the source spells an empty stub: the package holds `04 0b 47`
                out.append((state if depth_at[i] == state_depth and state else "", name, rettype, None, is_native,
                            is_final, params))
                i = k + 1
                continue
            end = _brace_end(toks, k)
            if end is None:
                break
            out.append((state if depth_at[i] == state_depth and state else "", name, rettype, toks[k + 1:end],
                        is_native, is_final, params))
            i = end + 1
            continue
        i += 1
    return out


# ----------------------------------------------------------------------------------------------------------------
# classification and emission

def statements(body):
    """Split a body into `;`-terminated statements at depth 0. A `{` at depth 0 means control flow: not modelled."""
    out, cur, depth = [], [], 0
    for kind, val in body:
        if kind == "op" and val in "([{":
            if val == "{" and depth == 0:
                raise Refused("a nested block: control flow is not modelled")
            depth += 1
        elif kind == "op" and val in ")]}":
            depth -= 1
        if kind == "op" and val == ";" and depth == 0:
            if cur:
                out.append(cur)
            cur = []
            continue
        cur.append((kind, val))
    if cur:
        raise Refused("a statement without its semicolon")
    return out


def _strip_parens(expr):
    while len(expr) >= 3 and expr[0] == ("op", "(") and expr[-1] == ("op", ")"):
        depth = 0
        for i, (_k, v) in enumerate(expr):
            if v == "(":
                depth += 1
            elif v == ")":
                depth -= 1
                if depth == 0 and i != len(expr) - 1:
                    return expr
        expr = expr[1:-1]
    return expr


def _number(text):
    t = text.lower()
    if t.startswith("0x"):
        return int(t, 16)
    if "." in t or "e" in t or t.endswith("f"):
        return float(t.rstrip("f"))
    return int(t)


def _is_call(expr):
    """`Name(...)` -- the whole expression is one call."""
    return (len(expr) >= 3 and expr[0][0] in ("ident", "keyword") and expr[1] == ("op", "(")
            and _paren_end(expr, 1) == len(expr) - 1)


def _emit_var(name, env):
    """One variable read: its own token and the record index the package holds, bools behind `2D` (BoolVariable).

    `bGodMode = NewGodModeValue;` is `14 2D 01 <idx> 2D 00 <idx>`: the *value*'s bool flag is the variable's own,
    which is why a read is emitted here and not by the caller.
    """
    low = name.lower()
    scope = env["scope"]
    if low in scope["locals"]:
        token, index, ptype = scope["locals"][low]
    elif low in scope["props"]:
        token, index, ptype = scope["props"][low]
    else:
        return None
    out = bytes([token]) + write_index(index)
    return bytes([0x2D]) + out if ptype == "bool" else out


def _emit_enum(name, env):
    """`PHYS_Swimming` -> `24 03`: the ordinal of the constant in the enum the class chain declares."""
    low = name.lower()
    for cls in env["order"]:
        hit = env["enums"].get((cls, low))
        if hit is None:
            continue
        ordinal, enum = hit
        if not 0 <= ordinal <= 255:
            return None, enum
        return bytes([0x24, ordinal]), enum
    return None, ""


def emit_expr(expr, rettype, env):
    """The bytes of one expression, or Refused with the reason."""
    names = env["names"]
    expr = _strip_parens(expr)
    if not expr:
        return bytes([0x0B]), "nothing"                  # Return; -> Return Nothing
    if len(expr) == 2 and expr[0] == ("op", "-") and expr[1][0] == "number":
        expr = [("number", "-" + expr[1][1])]
    if len(expr) != 1:
        if expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "vect":
            return _emit_vector(expr), "vector"
        if _is_call(expr):
            return emit_call(expr, env), "call-value"
        raise Refused("a compound expression, which needs the statement walk")
    kind, val = expr[0]
    if kind in ("ident", "keyword") and val.lower() not in ("true", "false", "none", "self"):
        read = _emit_var(val, env)
        if read is not None:
            return read, "variable"
        token, enum = _emit_enum(val, env)
        if token is not None:
            # An enum constant is a byte and the compiler writes ByteConst there; passed where neither a byte nor
            # its own enum is the declared type, the token would be a guess, so it is refused by name.
            want = (rettype or "").lower()
            if want != "byte" and want != (enum or "").lower():
                raise Refused("an enum constant passed as %s" % (rettype or "typeless"))
            return token, "enum-const"
        raise Refused("a name neither a local, a property nor an enum constant of the class chain")
    low = val.lower()
    t = (rettype or "").lower()
    if kind == "keyword" and low in ("true", "false"):
        if t != "bool":
            raise Refused("a bool literal in a %s function" % (rettype or "typeless"))
        return bytes([0x27 if low == "true" else 0x28]), "bool"
    if kind == "keyword" and low == "none":
        return bytes([0x2A]), "none"
    if kind == "keyword" and low == "self":
        return bytes([0x17]), "self"
    if kind == "number":
        v = _number(val)
        if t == "int":
            if isinstance(v, float):
                if not v.is_integer():
                    raise Refused("a fractional literal in an int slot (%s)" % val)
                v = int(v)
            if v == 0:
                return bytes([0x25]), "int-zero"
            if v == 1:
                return bytes([0x26]), "int-one"
            if 0 < v < 256:
                return bytes([0x2C, v]), "int-const-byte"
            return bytes([0x1D]) + struct.pack("<i", v), "int-const"
        if t == "byte":
            if not 0 <= v < 256:
                raise Refused("a byte literal out of range (%s)" % val)
            return bytes([0x24, int(v)]), "byte-const"
        if t == "float":
            return bytes([0x1E]) + struct.pack("<f", float(v)), "float-const"
        raise Refused("a numeric literal in a %s function" % (rettype or "typeless"))
    if kind in ("string", "quoted"):
        text = val[1:-1]
        if t == "string":
            return bytes([0x1F]) + text.encode("latin-1") + b"\x00", "string-const"
        if t == "name":
            idx = names.get(text.lower())
            if idx is None:
                raise Refused("a name literal the package does not carry (%d chars)" % len(text))
            return bytes([0x21]) + write_index(idx), "name-const"
        raise Refused("a quoted literal in a %s function" % (rettype or "typeless"))
    raise Refused("an expression the emitter does not model (%s)" % (val if kind != "string" else "a string"))


def _emit_vector(expr):
    """`vect(a, b, c)` -> `23` and three little-endian f32 (measured: `return vect(0,0,0);`)."""
    parts = _split_commas(expr[2:-1]) if expr[1] == ("op", "(") and expr[-1] == ("op", ")") else []
    if len(parts) != 3 or any(len(p) != 1 or p[0][0] != "number" for p in parts):
        raise Refused("a vector literal that is not three numeric components")
    return bytes([0x23]) + b"".join(struct.pack("<f", float(_number(p[0][1]))) for p in parts)


def _split_commas(tokens):
    out, cur = [], []
    for t in tokens:
        if t == ("op", ","):
            out.append(cur)
            cur = []
            continue
        cur.append(t)
    out.append(cur)
    return out


def _assign_at(stmt):
    """The index of the statement's own `=` (depth 0), or None. `==`/`!=`/`+=` are one token, so they do not match."""
    depth = 0
    for i, (kind, val) in enumerate(stmt):
        if kind == "op" and val in "([{":
            depth += 1
        elif kind == "op" and val in ")]}":
            depth -= 1
        elif kind == "op" and val == "=" and depth == 0:
            return i
    return None


def _callee_params(name, env):
    """`[(type, name)]` from the declaration of the callee the class chain inherits it from."""
    low = name.lower()
    for cls in env["order"]:
        hit = env["params"].get((cls, low))
        if hit:
            return hit
    return None


def emit_call(stmt, env):
    """`Name(a, b)` -> the token its declaration earned, each argument, `16` (EndFunctionParms); no trailer."""
    close = _paren_end(stmt, 1) if len(stmt) > 1 and stmt[1] == ("op", "(") else None
    if close is None or close != len(stmt) - 1:
        raise Refused("a call the statement walk cannot bound (a receiver, or a statement after the call)")
    args = _split_commas(stmt[2:close]) if stmt[2:close] else []
    scope = env["scope"]
    how = (scope or {}).get("funcs", {}).get(stmt[0][1].lower())
    if how is None:
        raise Refused("a call to a name the class chain does not declare")
    kind, value = how
    if kind == "native":
        op = 0x60 + (value >> 8)
        if op > 0x6F:
            raise Refused("a native token outside the six opcode groups")
        head = bytes([op, value & 0xFF])
    elif kind == "final":
        head = bytes([0x1C]) + write_index(value)
    else:
        head = bytes([0x1B]) + write_index(value)
    if not args:
        return head + b"\x16"
    params = _callee_params(stmt[0][1], env)
    if params is None:
        raise Refused("a call whose callee declaration the text does not carry")
    if len(params) != len(args):
        raise Refused("a call with %d argument(s) to a %d-parameter callee" % (len(args), len(params)))
    out = head
    for arg, (ptype, _pname) in zip(args, params):
        if not arg:
            raise Refused("a call that leaves an optional argument empty")
        out += emit_expr(arg, ptype, env)[0]
    return out + b"\x16"


def emit_assign(stmt, at, env):
    """`X = value;` -> `0F <InstanceVariable> <index> <value>`, or `14 2D <InstanceVariable> <index> <bool>`."""
    lhs, rhs = stmt[:at], stmt[at + 1:]
    if len(lhs) != 1 or lhs[0][0] not in ("ident", "keyword"):
        raise Refused("an assignment whose target is not one name (a receiver chain, or an index)")
    nm = lhs[0][1].lower()
    scope = env["scope"] or {}
    if nm in scope.get("locals", {}):
        token, index, ptype = scope["locals"][nm]
    elif nm in scope.get("props", {}):
        token, index, ptype = scope["props"][nm]
    else:
        raise Refused("an assignment to a name neither a local nor a property of the class chain")
    if ptype == "bool":
        # The value is one expression: a literal, a variable read (the bool flag is the read's own) or a call.
        if len(rhs) != 1 and not _is_call(rhs):
            raise Refused("a bool assignment whose value is a compound expression")
        return bytes([0x14, 0x2D, token]) + write_index(index) + emit_expr(rhs, "bool", env)[0]
    if ptype in ("struct", "array", "delegate", "pointer", ""):
        raise Refused("an assignment to a %s property" % (ptype or "typeless"))
    return bytes([0x0F, token]) + write_index(index) + emit_expr(rhs, ptype, env)[0]


def emit_body(body, rettype, env, is_native=False):
    """`(bytes, model, shape)` for a modelled body, or Refused naming what is not modelled."""
    if body is None:
        if is_native:
            raise Refused("a native declaration: its body relays its parameters to the native")
        return TRAILER, "stub", "stub"          # declared without a body: the package compiles the trailer alone
    stmts = statements(body)
    if not stmts:
        return TRAILER, "empty", "empty"
    if len(stmts) > 1:
        raise Refused("%d statements, which needs control flow" % len(stmts))
    stmt = stmts[0]
    if stmt and stmt[0][0] == "keyword" and stmt[0][1].lower() == "return":
        expr, shape = emit_expr(stmt[1:], rettype, env)
        return bytes([0x04]) + expr + TRAILER, "return", shape
    if any(k == "keyword" and v.lower() in ("if", "for", "while", "do", "switch", "case", "else", "foreach")
           for k, v in stmt):
        raise Refused("a statement that is control flow")
    at = _assign_at(stmt)
    if at is not None:
        return emit_assign(stmt, at, env) + TRAILER, "assign", "assign"
    return emit_call(stmt, env) + TRAILER, "call", "call"


# ----------------------------------------------------------------------------------------------------------------
# scoring against the dump

JOIN_FIELDS = ["package", "class", "state", "function", "cause"]


def _chain():
    """{(package, class): (super, super's package)} -- the ancestor walk, from the committed class table."""
    chain = {}
    path = os.path.join(ROOT, "inventory", "classes.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            # stored in the walk's own order: the super's package first, then the super
            chain[(r["package"].lower(), r["class"].lower())] = (r["package_of_super"].lower(), r["super"].lower())
    return chain


def _natives():
    """{(package, class, function): (native_token, function_flags)} -- where a native's opcode comes from."""
    out = {}
    path = os.path.join(ROOT, "inventory", "natives.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[(r["package"].lower(), r["class"].lower(), r["function"].lower())] = (
                int(r["native_token"]), r["function_flags"])
    return out


def _ancestors(chain, pack, cls):
    """[(package, class)] from the class outwards. `classes.csv` keys a super by its own package, so the walk
    crosses a package boundary where the build did."""
    out, seen, p, c = [], set(), pack, cls.lower()
    while c and (p, c) not in seen:
        seen.add((p, c))
        out.append((p, c))
        p, c = chain.get((p, c), ("", ""))
    return out


def _class_tables(pkg, kinds, names):
    """The package's own records as the compiler addressed them: property and function exports, and the imports."""
    props, funcs, imports = {}, {}, {}
    for i, e in enumerate(pkg.exports):
        kind = kinds.get(i + 1, "")
        nm = names.get(i + 1, "").lower()
        owner = names.get(e["outer"], "").lower() if e["outer"] > 0 else ""
        if kind in PROPERTY_CLASSES:
            props.setdefault(nm, []).append((i + 1, owner, kind))
        elif kind == "Function":
            funcs.setdefault(nm, []).append((i + 1, owner))
    for j, im in enumerate(pkg.imports):
        # an import's `class` field is a name index, not an export index, so `class_name_of` cannot read it
        imports.setdefault(str(pkg.name(im["name"])).lower(), []).append((-(j + 1), pkg.name(im["class"])))
    return props, funcs, imports


def _scope(pkg, kinds, names, tables, pack, cls, func_index, chain, natives, table_index, final_of=None):
    """The names one body may mention, each with the index and token the package's own record earns.

    A property is found by *name and owner*: the record's index is what the compiler wrote, so nothing is rebuilt.
    A callee is resolved by walking the ancestor chain to the class that declares it -- the native table says the
    token (0 means "not a native opcode, call it by name"), and its `final` flag picks the call token.
    """
    props, funcs, imports = tables
    scope = {"props": {}, "locals": {}, "funcs": {}}
    for i, e in enumerate(pkg.exports):
        if e["outer"] == func_index and kinds.get(i + 1) in PROPERTY_CLASSES:
            scope["locals"][names[i + 1].lower()] = (LOCAL_TOKEN, i + 1, PROP_TYPE.get(kinds[i + 1], ""))
    order = [c for _p, c in _ancestors(chain, pack, cls)]
    scope["order"] = order
    for nm, cands in props.items():
        picked = None
        for anc in order:
            hit = [c for c in cands if c[1] == anc]
            if hit:
                picked = hit[0]
                break
        if picked is None:
            # A property an ancestor in *another* package declares is an import here, and the compiler wrote its
            # index as a negative number: `InteractionHands.EndState` reads `fc 07` (-508), not the export of a
            # same-named property some unrelated class in this package declares.
            imp = [c for c in imports.get(nm, []) if c[1] in PROPERTY_CLASSES]
            if len(imp) == 1:
                scope["props"][nm] = (INSTANCE_TOKEN, imp[0][0], PROP_TYPE.get(imp[0][1], ""))
                continue
            if len(cands) == 1:
                picked = cands[0]
        if picked is not None:
            scope["props"][nm] = (INSTANCE_TOKEN, picked[0], PROP_TYPE.get(picked[2], ""))
    declared = set(funcs) | {f for (p2, c2, f) in natives if p2 == pack and c2 in order}
    for c in order:
        declared |= set((final_of or {}).get(c, ()))
    for nm in declared:
        for p, c in _ancestors(chain, pack, cls):
            hit = natives.get((p, c, nm))
            if hit is None:
                continue
            token, flags = hit
            if token:
                scope["funcs"][nm] = ("native", token)
                break
            if "0x1:Final" in flags:
                own = [x for x in funcs.get(nm, []) if x[1] == c]
                imp = [x for x in imports.get(nm, []) if x[1] == "Function"]
                if own:
                    scope["funcs"][nm] = ("final", own[0][0])
                    break
                if len(imp) == 1:
                    scope["funcs"][nm] = ("final", imp[0][0])
                    break
                continue
            scope["funcs"][nm] = ("virtual", table_index.get(nm))
            break
        else:
            if any(nm in (final_of or {}).get(k, ()) for k in order):
                own = [x for x in funcs.get(nm, []) if x[1] in order]
                imp = [x for x in imports.get(nm, []) if x[1] == "Function"]
                if own:
                    scope["funcs"][nm] = ("final", own[0][0])
                    continue
                if len(imp) == 1:
                    scope["funcs"][nm] = ("final", imp[0][0])
                    continue
            if nm in funcs and nm not in scope["funcs"]:
                scope["funcs"][nm] = ("virtual", table_index.get(nm))
    return scope


def _residue_cause(texts, name, state):
    """Why a shipped body has no declaration to join: named, never a percentage."""
    if not texts:
        return "the text has no file for this class"
    if any(re.search(r"\bdelegate\b[^;{}]*\b%s\b" % re.escape(name), t, re.I) for t in texts):
        return "a delegate declaration"
    if any(re.search(r"\b%s\b" % re.escape(name), t, re.I) for t in texts):
        return "the text spells the name, but not as a declaration"
    if state:
        return "the text does not declare it in state %s" % state
    return "the text does not carry the name"


def _dis():
    """`uscript-dis.py` as a module: the locator and the token walk are its own, one answer for both halves."""
    spec = importlib.util.spec_from_file_location("uscript_dis", os.path.join(ROOT, "tools", "uscript-dis.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _source_index(tree):
    """`{(class, state, function): (rettype, body, text, native, final)}` from the extracted corpus, plus every
    declaration's parameter list and every enum constant -- the two joins an argument's token needs."""
    index = {}
    params = {}
    enums, enum_names = {}, set()
    files = 0
    for dirpath, _dirs, names in os.walk(tree):
        for name in sorted(names):
            if not name.endswith(".uc"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="latin-1", newline="") as fh:
                text = fh.read()
            files += 1
            cls = os.path.splitext(os.path.basename(path))[0].lower()
            for state, fn, rettype, body, is_native, is_final, decl_params in functions_of(text):
                if decl_params:
                    params.setdefault((cls, fn.lower()), decl_params)
                for key in ((cls, state.lower(), fn.lower()), (cls, "", fn.lower())):
                    old = index.get(key)
                    if old is None or len(body or ()) > len(old[1] or ()):
                        # a class that ships two declarations of one name (an `#if` branch, or a stub beside its
                        # body) compiled the longer one: prefer it, so the comparison is not scored against a stub
                        index[key] = (rettype, body, text, is_native, is_final)
            if "enum" in text.lower():
                for enum, consts in enums_of(text):
                    enum_names.add(enum.lower())
                    for cname, ordinal in consts:
                        enums.setdefault((cls, cname.lower()), (ordinal, enum))
    return index, files, params, enums, enum_names


def score(tree, dump, out_dir, residue_dir=None):
    dis = _dis()
    from ue2pkg import Package                                  # noqa: PLC0415 - only with a real dump

    index, files, params, enums, enum_names = _source_index(tree)
    src_texts = {}
    for key, value in index.items():
        src_texts.setdefault(key[0], []).append(value[2])
    chain, natives = _chain(), _natives()
    class_names = {c for (_p, c) in chain}
    final_of = {}
    for key, value in index.items():
        if value[4]:
            final_of.setdefault(key[0], set()).add(key[2])
    table = dis.Table()
    rows, residue, refused, examples = [], [], [], {}
    tally = collections.Counter()
    shapes = collections.Counter()
    reasons = collections.Counter()
    for fn in sorted(os.listdir(dump)):
        if not fn.endswith(".u"):
            continue
        pkg = Package(os.path.join(dump, fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        kinds = {i + 1: pkg.class_name_of(e) for i, e in enumerate(pkg.exports)}
        names = {i + 1: pkg.name(e["name"]) for i, e in enumerate(pkg.exports)}
        table_index = {}
        for i in range(len(pkg.names or [])):
            table_index.setdefault(str(pkg.name(i)).lower(), i)
        tables = _class_tables(pkg, kinds, names)
        pack = os.path.splitext(fn)[0].lower()
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function" or e["outer"] <= 0:
                continue
            status, body, _s, _e, _ne, _front = dis.locate(e, pkg.data)
            if status != "ok":
                tally["scriptless"] += 1
                continue
            try:
                nodes = dis.parse(body, table)
            except dis.Refused:
                tally["refused"] += 1
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != len(body):
                tally["uncovered"] += 1
                continue
            tally["walked"] += 1
            if body.endswith(TRAILER):
                tally["trailer"] += 1
            if body == TRAILER:
                tally["bare-trailer"] += 1
            cls = dis.owner_class(pkg.exports, kinds, names, e["outer"])
            outer = pkg.exports[e["outer"] - 1]
            state = pkg.name(outer["name"]) if pkg.class_name_of(outer) == "State" else ""
            fname = pkg.name(e["name"])
            got = index.get((cls.lower(), state.lower(), fname.lower()))
            if not got and not state:
                got = index.get((cls.lower(), "", fname.lower()))
            if not got:
                tally["no-source" if not state else "no-state-source"] += 1
                residue.append({"package": fn, "class": cls, "state": state, "function": fname,
                                "cause": _residue_cause(src_texts.get(cls.lower(), []), fname, state)})
                continue
            rettype, src_body, _text, is_native, _is_final = got
            scope = _scope(pkg, kinds, names, tables, pack, cls, i + 1, chain, natives, table_index, final_of)
            env = {"names": table_index, "scope": scope, "params": params, "enums": enums,
                   "enum_names": enum_names, "class_names": class_names, "order": scope["order"]}
            try:
                ours, model, shape = emit_body(src_body, rettype, env, is_native)
            except Refused as exc:
                tally["not-modelled"] += 1
                reasons[str(exc).split("(")[0].strip()] += 1
                if residue_dir:
                    examples.setdefault(str(exc).split("(")[0].strip(), []).append(
                        "%s.%s" % (cls, fname))
                    refused.append({"package": fn, "class": cls, "state": state, "function": fname,
                                    "rettype": rettype, "reason": str(exc),
                                    "source": " ".join(v for _k, v in (src_body or ()))[:300],
                                    "theirs": body.hex()})
                continue
            tally["modelled"] += 1
            shapes[shape] += 1
            verdict = "identical" if ours == body else "differ"
            tally[verdict] += 1
            rows.append({"package": fn, "class": cls, "function": fname, "state": state,
                         "model": model, "rettype": rettype, "source": shape,
                         "ours": ours.hex(), "theirs": body.hex(), "verdict": verdict,
                         "walked": "", "differ": ""})
    rows.sort(key=lambda r: (r["package"], r["class"].lower(), r["state"].lower(), r["function"].lower()))
    census = {"package": "(census)", "class": "", "state": "", "function": "", "model": "census", "rettype": "",
              "source": "", "ours": str(tally["modelled"]), "theirs": str(tally["identical"]),
              "verdict": "modelled/identical", "walked": str(tally["walked"]), "differ": str(tally["differ"])}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "uscript_emit.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerow({k: census.get(k, "") for k in FIELDS})
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    residue.sort(key=lambda r: (r["package"], r["class"].lower(), r["state"].lower(), r["function"].lower()))
    causes = collections.Counter(r["cause"] for r in residue)
    jpath = os.path.join(out_dir, "uscript_emit_join.csv")
    with open(jpath, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=JOIN_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerow({"package": "(census)", "class": "", "state": "", "function": "",
                    "cause": "%d bodies: %d joined, %d not (%s)"
                             % (tally["walked"], tally["walked"] - len(residue), len(residue),
                                "; ".join("%s %d" % kv for kv in causes.most_common()))})
        for r in residue:
            w.writerow(r)
    joined = tally["walked"] - tally["no-source"] - tally["no-state-source"]
    print("%d source file(s); %d bod(ies) walked, %d joined to a declaration (%d class-scope names and %d state "
          "names the text does not carry), %d not modelled"
          % (files, tally["walked"], joined, tally["no-source"], tally["no-state-source"], tally["not-modelled"]))
    print("every body ends on the trailer %d of %d (and %d are the trailer alone)"
          % (tally["trailer"], tally["walked"], tally["bare-trailer"]))
    print("modelled %d: %d identical, %d differ" % (tally["modelled"], tally["identical"], tally["differ"]))
    print("shapes: %s" % ", ".join("%s %d" % kv for kv in shapes.most_common()))
    print("not modelled: %s" % ", ".join("%s %d" % kv for kv in reasons.most_common()))
    if residue_dir:
        os.makedirs(residue_dir, exist_ok=True)
        rpath = os.path.join(residue_dir, "residue.csv")
        with open(rpath, "w", newline="", encoding="utf-8") as fh:
            fields = ["package", "class", "state", "function", "rettype", "reason", "source", "theirs"]
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            for r in refused:
                w.writerow(r)
        print("wrote %s (%d rows)" % (rpath, len(refused)))
        for reason, names in examples.items():
            print("  %-60s %s" % (reason[:60], ", ".join(names[:4])))
    print("wrote %s and %s" % (os.path.relpath(path, ROOT), os.path.relpath(jpath, ROOT)))
    return 0


# ----------------------------------------------------------------------------------------------------------------
# the dump-free gate

SYNTH = """class Probe extends Actor;

function bool Yes() { return true; }
function bool No()  { return false; }
function int Zero() { return 0; }
function int One()  { return 1; }
function int Two()  { return 2; }
function int Big()  { return 666; }
function int Neg()  { return -1; }
function float FZero() { return 0.0; }
function float FOne()  { return 1; }
function string Empty() { return ""; }
function string Text()  { return "ab"; }
function name Bone()    { return 'weapon_bone'; }
function Actor Nobody() { return None; }
function Actor Me()     { return self; }
function vector Vec()   { return vect(0,0,0); }
function Nothing() { }
function Nothing2() { return; }
event bool Paren() { return(true); }
function int Call() { return Foo(1); }
function Stub();
function native int NativeStub(int A, int B);
function SetHealth() { Health = 100; }
function SetFlag() { bDead = true; }
function SetLocal() { Slot = 7; }
function SetObject() { Target = None; }
function CallNative() { Destroy(); }
function CallFinal() { MyFinal(); }
function CallVirtual() { MyVirtual(); }
function CallArgs() { Foo(1); }
function CallBool() { Bar(bDead); }
function CallEnum() { Baz(PROBE_SWIMMING); }
function CallMix() { Mix(false, 1.5); }
function Bump() { Health = Bump(7); }
function FromVar() { Health = Slot; }
function FromFlag() { bDead = Flag2; }
function CompoundArg() { Foo(1 + 2); }
function FloatArg() { Foo(1.5); }
function WrongArgc() { Foo(1, 2); }
function IfStmt() { if (bDead) Health = 1; }
"""

# The package's own tables, as `_scope()` builds them: a property's index, a local's, and a callee's token.
SCOPE = {"props": {"health": (0x01, 302, "int"), "bdead": (0x01, 45, "bool"),
                   "target": (0x01, 611, "object"), "flag2": (0x01, 46, "bool")},
         "locals": {"slot": (0x00, 611, "int")},
         "funcs": {"destroy": ("native", 279), "myfinal": ("final", 763), "myvirtual": ("virtual", 39),
                   "foo": ("virtual", 41), "bar": ("virtual", 44), "baz": ("native", 705),
                   "mix": ("virtual", 46), "bump": ("virtual", 47)}}

# The declarations' parameter lists and the enum the class chain declares: what an argument's token comes from.
PARAMS = {("probe", "foo"): [("int", "A")], ("probe", "bar"): [("bool", "Flag")],
          ("probe", "baz"): [("EProbe", "Mode")], ("probe", "mix"): [("bool", "Flag"), ("float", "Time")],
          ("probe", "bump"): [("int", "A")]}
ENUMS = {("probe", "probe_walking"): (1, "EProbe"), ("probe", "probe_swimming"): (3, "EProbe")}
ENV = {"names": {}, "scope": SCOPE, "params": PARAMS, "enums": ENUMS, "enum_names": {"eprobe"},
       "class_names": {"probe"}, "order": ["probe"]}

WANT = {
    "yes": "0427040b47",
    "no": "0428040b47",
    "zero": "0425040b47",
    "one": "0426040b47",
    "two": "042c02040b47",
    "big": "041d9a020000040b47",
    "neg": "041dffffffff040b47",
    "fzero": "041e00000000040b47",
    "fone": "041e0000803f040b47",
    "empty": "041f00040b47",
    "text": "041f616200040b47",
    "bone": "04214f20040b47",          # name index 2063 -> compact 4f 20
    "nobody": "042a040b47",
    "me": "0417040b47",
    "vec": "0423000000000000000000000000040b47",
    "nothing": "040b47",
    "nothing2": "040b040b47",
    "paren": "0427040b47",
    "stub": "040b47",
    "sethealth": "0f016e042c64040b47",       # Let, InstanceVariable 302 (6e 04), IntConstByte 100
    "setflag": "142d012d27040b47",           # LetBool, BoolVariable, InstanceVariable 45, True
    "setlocal": "0f0063092c07040b47",        # Let, LocalVariable 611 (63 09), IntConstByte 7
    "setobject": "0f0163092a040b47",         # Let, InstanceVariable 611 (63 09), NoObject
    "callnative": "611716040b47",            # native token 279: opcode 0x61, operand 0x17, EndFunctionParms
    "callfinal": "1c7b0b16040b47",           # FinalFunction, export index 763 (7b 0b), EndFunctionParms
    "callvirtual": "1b2716040b47",           # VirtualFunction, name index 39
    "call": "041b292616040b47",              # return Foo(1); -- int parameter, IntOne
    "callargs": "1b292616040b47",            # Foo(1); as a statement
    "callbool": "1b2c2d012d16040b47",              # Bar(bDead) -- a bool argument behind BoolVariable
    "callenum": "62c1240316040b47",                # Baz(PROBE_SWIMMING) -- native token 705, ByteConst 3
    "callmix": "1b2e281e0000c03f16040b47",         # Mix(false, 1.5) -- False, FloatConst
    "bump": "0f016e041b2f2c0716040b47",      # Health = Bump(7);
    "fromvar": "0f016e04006309040b47",       # Health = Slot; -- a local's own index
    "fromflag": "142d012d2d012e040b47",      # bDead = Flag2; -- bool property read as the value
}

# Bodies the emitter must refuse, each for a named reason rather than a guess.
REFUSE = ("compoundarg", "floatarg", "wrongargc", "ifstmt", "nativestub")


def check(args):
    """Synthetic corpus plus the committed artifact's own consistency: no dump, no writes."""
    names = {"weapon_bone": 2063}
    for value, want in ((0, b"\x00"), (1, b"\x01"), (63, b"\x3f"), (64, b"\x40\x01"),
                        (2063, b"\x4f\x20"), (8191, b"\x7f\x7f")):
        got = write_index(value)
        if got != want:
            print("FAIL uscript-emit: write_index(%d) = %s, expected %s" % (value, got.hex(), want.hex()))
            return 1
    import importlib.util as _iu                              # noqa: PLC0415 - the reader is the oracle
    spec = _iu.spec_from_file_location("uscript_dis", os.path.join(ROOT, "tools", "uscript-dis.py"))
    dis = _iu.module_from_spec(spec)
    spec.loader.exec_module(dis)
    for value in (0, 1, 63, 64, 2063, 8191, -1, -2063):
        back, _end = dis.read_index(write_index(value) + b"\x00", 0)
        if back != value:
            print("FAIL uscript-emit: the reader answers %d for a written %d" % (back, value))
            return 1
    env = dict(ENV)
    env["names"] = names
    got = {f[1].lower(): (f[2], f[3], f[4]) for f in functions_of(SYNTH)}
    for fn, want in WANT.items():
        if fn not in got:
            print("FAIL uscript-emit: the synthetic class does not declare %s" % fn)
            return 1
        rettype, body, is_native = got[fn]
        try:
            ours, _model, _shape = emit_body(body, rettype, env, is_native)
        except Refused as exc:
            print("FAIL uscript-emit: %s refused: %s" % (fn, exc))
            return 1
        if ours.hex() != want:
            print("FAIL uscript-emit: %s emits %s, expected %s" % (fn, ours.hex(), want))
            return 1
    for case in REFUSE:
        if case not in got:
            print("FAIL uscript-emit: the synthetic class does not declare %s" % case)
            return 1
        rettype, body, is_native = got[case]
        try:
            emit_body(body, rettype, env, is_native)
        except Refused:
            continue
        print("FAIL uscript-emit: %s was emitted instead of refused" % case)
        return 1
    if not os.path.exists(ARTIFACT):
        print("ok uscript-emit: %d synthetic bodies, no artifact to check" % len(WANT))
        return 0
    with open(ARTIFACT, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    census = [r for r in rows if r["model"] == "census"]
    body_rows = [r for r in rows if r["model"] != "census"]
    if len(census) != 1:
        print("FAIL uscript-emit: the artifact carries %d census rows" % len(census))
        return 1
    c = census[0]
    identical = sum(1 for r in body_rows if r["verdict"] == "identical")
    differ = sum(1 for r in body_rows if r["verdict"] == "differ")
    if identical + differ != len(body_rows):
        print("FAIL uscript-emit: %d rows, %d identical + %d differ" % (len(body_rows), identical, differ))
        return 1
    if int(c["ours"]) != len(body_rows) or int(c["theirs"]) != identical or int(c["differ"]) != differ:
        print("FAIL uscript-emit: census %s/%s/%s against %d rows, %d identical, %d differ"
              % (c["ours"], c["theirs"], c["differ"], len(body_rows), identical, differ))
        return 1
    for r in body_rows:
        if (r["ours"] == r["theirs"]) != (r["verdict"] == "identical"):
            print("FAIL uscript-emit: %s.%s reads %s against its own bytes"
                  % (r["class"], r["function"], r["verdict"]))
            return 1
        if not r["ours"].endswith("040b47"):
            print("FAIL uscript-emit: %s.%s does not end on the trailer" % (r["class"], r["function"]))
            return 1
    jpath = os.path.join(ROOT, "inventory", "uscript_emit_join.csv")
    if os.path.exists(jpath):
        with open(jpath, newline="", encoding="utf-8") as fh:
            jrows = list(csv.DictReader(fh))
        jcensus = [r for r in jrows if r["package"] == "(census)"]
        if len(jcensus) != 1:
            print("FAIL uscript-emit: the join artifact carries %d census rows" % len(jcensus))
            return 1
        unjoined = [r for r in jrows if r["package"] != "(census)"]
        for r in unjoined:
            if not r["cause"]:
                print("FAIL uscript-emit: %s.%s is unjoined with no cause" % (r["class"], r["function"]))
                return 1
        if not jcensus[0]["cause"].startswith("%d bodies" % int(c["walked"])):
            print("FAIL uscript-emit: the join census reads %r against a walk of %s"
                  % (jcensus[0]["cause"][:40], c["walked"]))
            return 1
        if " %d not " % len(unjoined) not in jcensus[0]["cause"]:
            print("FAIL uscript-emit: the join census does not count its %d row(s)" % len(unjoined))
            return 1
    print("ok uscript-emit: %d synthetic bodies, artifact %d rows (%d identical, %d differ)"
          % (len(WANT), len(body_rows), identical, differ))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tree", help="the extracted source corpus ($WORK/scripts-all)")
    ap.add_argument("--dump", help="the dump's System directory")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--residue-out", metavar="DIR", help="write the refused bodies and their reasons here")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not args.tree or not args.dump:
        print("FAIL uscript-emit: --tree and --dump are required (the corpus and the dump stay outside the repo)")
        return 2
    return score(args.tree, args.dump, args.out, args.residue_out)


if __name__ == "__main__":
    sys.exit(main())
