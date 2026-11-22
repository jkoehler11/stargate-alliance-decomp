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

# The types a source cast can name as a primitive: `string(x)`/`int(x)`/`bool(x)`/`rotator(x)` are `PrimitiveCast`,
# and anything else in front of a parenthesised expression that names a class is a `DynamicCast`.
CAST_TYPES = ("byte", "int", "bool", "float", "string", "name", "vector", "rotator", "class")

# The type byte a `Context` node writes is the *member's* value size, measured on 31,622 nodes: 4 for the
# pointer-sized types, 1 for a byte, 12 for a vector and 0 for a string (`MyNumericEdit.Value` is a string and its
# node writes `05 00 00`, `Items.Next` is an object and writes `05 00 04`).
PROP_SIZE = {"bool": 4, "int": 4, "float": 4, "object": 4, "name": 4, "class": 4, "byte": 1, "vector": 12,
             "string": 0}

# The skip word a `Context` writes before its type byte. For a member that is an `InstanceVariable` it is 5 in all
# 17,710 such nodes, whatever width the member's index was written with (`01 18` and `01 69 04` both write 5). A
# *call* member is not this constant -- over `VirtualFunction` members of one shape the word ranges 1..6 -- so the
# emitter refuses to compute it for one and models only the property chain.
CONTEXT_SKIP = 5


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
    """`[(type, name, optional)]` for a declaration's parameter list; `toks[i]` is its `(`.

    The declared type is what picks an argument's token (`PHYS_Swimming` is a byte, `1.0` a float), so the join to
    the callee's declaration has to carry it: `inventory/natives.csv` holds a parameter *count* and no types.
    `optional` is the third field because a call may stop short of it: `GotoState('FadeOut')` passes one of
    `GotoState(name NewState, optional name Label)`'s two and the package writes no placeholder for the other.
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
        optional = False
        for mod in ("out", "optional", "coerce", "const", "init"):
            if typ.lower().startswith(mod):
                typ = typ[len(mod):]
                optional = optional or mod == "optional"
        out.append((typ, name, optional))
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


def _int_literal(expr):
    """The integer an expression is literally, or None: `255`, `-1`, `0x10`, `(7)` -- a leaf, not a compound."""
    expr = _strip_parens(expr)
    if len(expr) == 2 and expr[0] == ("op", "-") and expr[1][0] == "number":
        expr = [("number", "-" + expr[1][1])]
    if len(expr) == 1 and expr[0][0] == "number":
        v = _number(expr[0][1])
        return v if isinstance(v, int) else None
    return None


def _is_call(expr):
    """`Name(...)` -- the whole expression is one call."""
    return (len(expr) >= 3 and expr[0][0] in ("ident", "keyword") and expr[1] == ("op", "(")
            and _paren_end(expr, 1) == len(expr) - 1)


CAST_SLOTS = {}


def _obj_classes():
    """`{(package, class, property): declared type}` from `inventory/class_properties.csv`.

    A receiver chain's member is a property of the *receiver's* class, not of the class the body is in. `List.Index`
    in `GUIComboBox.GetIndex` names `GUIComboBox.List` (an object of class `GUIListBase`) and then `GUIListBase.Index`
    -- resolving `Index` through the body's own chain instead wrote export 8263 rather than 13.

    The same table spells a struct property's type (`SquadDir` is a `Vector`, not a nameless struct), which is what a
    cast's slot is chosen by, so it is read once for the whole walk: rebuilding it per body was most of a run's time.
    """
    if OBJ_CLASSES:
        return OBJ_CLASSES
    path = os.path.join(ROOT, "inventory", "class_properties.csv")
    out = {}
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                friendly = (r.get("friendly_type") or "").strip()
                if not friendly or friendly.lower() == "none":
                    continue
                OBJ_CLASSES[(r["package"].lower(), r["class"].lower(), r["property"].lower())] = friendly.lower()
    except OSError:
        pass
    return OBJ_CLASSES


OBJ_CLASSES = {}


def _cast_slots():
    """`{(from, to): slot}` from `inventory/cast_operands.csv` -- the image's own cast table, named `IntToFloat`.

    The operand of `0x39 PrimitiveCast` *is* one of those slot numbers: the three casts this corpus exercises at an
    expression site are `3a` (58 `ByteToInt`), `3d` (61 `IntToByte`) and `3f` (63 `IntToFloat`), which is how the
    table identifies the byte rather than a guess about the operand's meaning.
    """
    if CAST_SLOTS:
        return CAST_SLOTS
    path = os.path.join(ROOT, "inventory", "cast_operands.csv")
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                m = re.match(r"^([A-Z][a-z]+)To([A-Z][a-z]+)$", r["name"] or "")
                if not m or not int(r["tokens"] or 0):
                    continue
                CAST_SLOTS[(m.group(1).lower(), m.group(2).lower())] = int(r["slot"])
    except OSError:
        pass
    return CAST_SLOTS


def _converted(body, ptype, want=""):
    """`39 <slot>` in front of a read whose declared type is not the type the site wants.

    A read whose declared type is not the type the site wants is wrapped in `PrimitiveCast`, slot from the image's
    own cast table: `return Damage;` in a `float` function whose parameter is an `int` writes `04 39 3f 00 697e`
    (`IntToFloat`). Only pairs the table has a *used* slot for are cast.
    """
    want = (want or "").lower()
    if ptype in ("byte", "int", "float") and want in ("byte", "int", "float") and want != ptype:
        slot = _cast_slots().get((ptype, want))
        if slot is not None:
            return bytes([0x39, slot]) + body
    return body


def _operators():
    """`{(symbol, A type, B type): (native token, result type)}` from `inventory/native_operators.csv`.

    An operator's symbol does not name its token: `+` is `Add_IntInt` (146), `Add_FloatFloat` (174) and
    `Add_VectorVector` (215), so the *pair of operand types* is part of the key. The table's own declaration spells
    all three -- `native(156) static final operator(28) int & ( int A, int B )` -- so the symbol, the two operand types
    and the return type are read from it rather than composed by a naming rule.

    The precedence is the operator's own `operator(28)` value: smaller binds tighter, so the *loosest* operator at
    depth 0 is the expression's root (`a + b * c` is `a + (b * c)`).
    """
    if OPERATORS:
        return OPERATORS
    path = os.path.join(ROOT, "inventory", "native_operators.csv")
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                decl = (r.get("declaration") or "").strip()
                token = (r.get("native_token") or "").strip()
                if not token.isdigit():
                    continue
                m = re.match(r"^.*?\boperator\((\d+)\)\s+([A-Za-z<>]+)\s+(\S+?)\s*"
                             r"\(\s*((?:out\s+|skip\s+|coerce\s+)*)([A-Za-z<>]+)\s+\w+\s*,\s*"
                             r"((?:out\s+|skip\s+|coerce\s+)*)([A-Za-z<>]+)\s+\w+\s*\)", decl)
                if not m:
                    continue
                prec, ret, sym = int(m.group(1)), m.group(2).lower(), m.group(3)
                a, b = m.group(5).lower(), m.group(7).lower()
                OPERATORS[(sym, a, b)] = (int(token), ret)
                OPERATOR_PREC.setdefault(sym, prec)
    except OSError:
        pass
    return OPERATORS


OPERATORS = {}
OPERATOR_PREC = {}


def _operator_types(expr, env):
    """`(symbol, left type, right type, result type, token, position)` for the loosest depth-0 operator, or None.

    A `byte` operand is promoted to `int` before the pair is looked up -- `DirtyMask & 254` is `And_IntInt` with the
    read wrapped in `39 3a` (`ByteToInt`), not a byte operator -- and an exact pair is preferred when the table has
    one.
    """
    ops = _operators()
    if not ops:
        return None
    spelled = {"dot", "cross", "clockwisefrom"}
    symbols = {sym for (sym, _a, _b) in ops if not sym[:1].isalpha()}
    depth, best = 0, None
    for i, (kind, val) in enumerate(expr):
        if kind == "op" and val in "([{":
            depth += 1
            continue
        if kind == "op" and val in ")]}":
            depth -= 1
            continue
        if depth != 0:
            continue
        if kind == "op" and val in symbols:
            sym = val
        elif kind in ("ident", "keyword") and val.lower() in spelled:
            sym = val  # spelled as an infix word: `FireYaw ClockwiseFrom Rotation.Yaw`
        else:
            continue
        if i == 0 or i == len(expr) - 1:
            continue                    # a unary position: the sign of a literal, or a lone symbol
        prec = OPERATOR_PREC.get(sym, 0)
        # the loosest operator wins, and the rightmost on a tie (left-associative: `a - b - c` is `(a - b) - c`)
        if best is None or prec >= best[0]:
            best = (prec, i, sym)
    if best is None:
        return None
    _prec, at, sym = best
    left, right = expr[:at], expr[at + 1:]
    if not left or not right:
        return None
    t1, t2 = _type_of(left, env).lower(), _type_of(right, env).lower()
    for a, b in ((_numeric(t1), _numeric(t2)), (t1, t2)):
        hit = _operators().get((sym, a, b))
        if hit is not None:
            token, ret = hit
            return sym, a, b, ret, token, at
    return sym, t1, t2, "", None, at


def _numeric(t):
    """`byte` operands are promoted to `int` by the compiler: `DirtyMask & 254` is `And_IntInt`."""
    return "int" if t == "byte" else t


def _is_literal(expr):
    """A leaf literal: a number, a string, a quoted name, `true`/`false`/`None`, or a `vect(...)`.

    An operator whose *both* operands are literals is folded by the compiler at build time (`Foo(1 + 2)` holds `2c
    03`), so the emitter refuses it rather than writing the unfolded node.
    """
    expr = _strip_parens(expr)
    if len(expr) == 2 and expr[0] == ("op", "-") and expr[1][0] == "number":
        return True
    if len(expr) == 1:
        kind, val = expr[0]
        if kind in ("number", "string", "quoted"):
            return True
        return kind in ("ident", "keyword") and val.lower() in ("true", "false", "none")
    return bool(expr) and expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "vect"


def _emit_var(name, env, want=""):
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
    body = bytes([0x2D]) + out if ptype == "bool" else out
    return _converted(body, ptype, want)


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


def _dot_at(expr):
    """The last depth-0 `.` in an expression, or None: `Gun.GetAimingBoneCoords()` keeps its dot inside the call."""
    depth, pos = 0, None
    for i, (kind, val) in enumerate(expr):
        if kind == "op" and val in "([{":
            depth += 1
        elif kind == "op" and val in ")]}":
            depth -= 1
        elif kind == "op" and val == "." and depth == 0:
            pos = i
    return pos


def _record_kind(expr, env):
    """The package record's own kind for a simple name (`StructProperty`, `StrProperty`, ...), or ""."""
    expr = _strip_parens(expr)
    if len(expr) != 1 or expr[0][0] not in ("ident", "keyword"):
        return ""
    nm = expr[0][1].lower()
    scope = env.get("scope") or {}
    for table in ("locals", "props"):
        hit = scope.get(table, {}).get(nm)
        if hit is not None:
            return hit[2]
    return ""


def _is_import_read(expr, env):
    """True when the expression is one name whose record is a *property import* of this package."""
    expr = _strip_parens(expr)
    if len(expr) != 1 or expr[0][0] not in ("ident", "keyword"):
        return False
    nm = expr[0][1].lower()
    scope = env.get("scope") or {}
    for table in ("locals", "props"):
        hit = scope.get(table, {}).get(nm)
        if hit is not None:
            return hit[1] < 0
    return False


def _cast_kind(name, env):
    """`primitive` for `int(x)`, `object` for `AIController(x)`, and "" when the name is neither.

    A name that is *both* a class and a function in scope is not decided here: the call wins, because a call the
    package declares is a call, and a class of the same name would have to be refused rather than guessed.
    """
    low = name.lower()
    if low in CAST_TYPES:
        return "primitive"
    scope = env["scope"] or {}
    if low in scope.get("classrefs", {}) and low not in scope.get("funcs", {}):
        return "object"
    return ""


def _friendly(nm, env):
    """The type word the package artifact spells for a property: `vector`/`rotator` for a struct, `string` for a
    `StrProperty`. `_type_of` needs the *declared* type (`SquadDir` is a `Vector`), not the record kind."""
    scope = env.get("scope") or {}
    table = scope.get("friendly") or {}
    for p, c in _ancestors(scope.get("chain") or {}, scope.get("pack") or "", scope.get("cls") or ""):
        hit = table.get((p, c, nm))
        if hit:
            return hit
    return ""


def _declared_type(nm, env):
    """A read's declared type: the record's kind, spelled out when the record only says `struct`.

    A parameter's struct name is in its declaration (`Vector momentum`), a property's in
    `inventory/class_properties.csv` (`SquadDir` is a `Vector`); a local `local Vector V;` is neither, and stays
    `struct` -- which refuses rather than guesses.
    """
    scope = env.get("scope") or {}
    ptype = ""
    for table in ("locals", "props"):
        if nm in scope.get(table, {}):
            ptype = scope[table][nm][2]
            break
    if ptype != "struct":
        return ptype
    return _friendly(nm, env) or (env.get("paramtypes") or {}).get(nm, "") or ptype


def _type_of(expr, env):
    """The declared type of an expression, as the cast table keys it: a variable's own type, a call's return type,
    `object` for `self`/`class`/a class name, `vector` for `vect(...)`, a literal's own type, the member's type
    through the *receiver's* class for a receiver chain, and an operator's result type.
    """
    expr = _strip_parens(expr)
    if not expr:
        return ""
    if len(expr) == 2 and expr[0] == ("op", "-") and expr[1][0] == "number":
        expr = [expr[1]]
    if len(expr) == 1:
        kind, val = expr[0]
        low = val.lower()
        if kind in ("ident", "keyword"):
            if low in ("true", "false"):
                return "bool"
            if low in ("none", "self", "class"):
                return "object"
            scope = env["scope"] or {}
            for table in ("locals", "props"):
                if low in scope.get(table, {}):
                    return _declared_type(low, env)
            token, enum = _emit_enum(val, env)
            if token is not None:
                return (enum or "").lower() or "byte"
            if _cast_kind(val, env) == "object":
                return "class"
            return ""
        if kind == "number":
            return "float" if isinstance(_number(val), float) else "int"
        if kind == "quoted":
            return "name"
        if kind == "string":
            return "string"
        return ""
    if _is_call(expr):
        low = expr[0][1].lower()
        if low in CAST_TYPES:
            return low
        if _cast_kind(expr[0][1], env) == "object":
            return "class"
        if low == "vect":
            return "vector"
        for cls in env["order"]:
            hit = (env.get("rets") or {}).get((cls, low))
            if hit:
                return hit.lower()
        return ""
    op = _operator_types(expr, env)
    if op is not None:
        # an operator's own result type: `DirtyMask & 2` is an `int`, which is what `bool(...)` casts *from*
        return op[3]
    at = _dot_at(expr)
    if at is not None:
        member = expr[at + 1:]
        if len(member) == 1 and member[0][0] in ("ident", "keyword"):
            nm = member[0][1].lower()
            obj = expr[:at]
            hit = (_member_record(obj, nm, env) if _type_of(obj, env).lower() != "struct"
                   else _prop_by_name(nm, env))
            if hit is not None:
                if hit[2] == "struct" and len(hit) > 3 and hit[3]:
                    return hit[3]
                return hit[2]
    return ""


def _emit_operator(expr, rettype, env):
    """`A op B` -> `<token> A B 16`: the operator's token comes **first**, its operands after it.

    Measured on `PlayerProfile.SetDirty` (`DirtyMask = DirtyMask | 1;` -> `0f 01 48 11 | 39 3d | 9c 39 3a 01 48 11 26
    16`): the token is the native's own opcode (`Or_IntInt` 158), the left operand comes before the right, and the
    node ends on `16` (EndFunctionParms) like any other call. `PteroBoss.TakeDamage` (`AddVelocity(Momentum / Mass)`)
    is the same shape one level in, with `d6` (`Divide_VectorFloat` 214).
    """
    op = _operator_types(expr, env)
    if op is None:
        raise Refused("a compound expression, which needs the statement walk")
    sym, t1, t2, result, token, at = op
    if not token:
        raise Refused("an operator the image's table does not install (%s %s %s)"
                      % (t1 or "an unknown type", sym, t2 or "an unknown type"))
    left, right = expr[:at], expr[at + 1:]
    if _is_literal(left) and _is_literal(right):
        raise Refused("an operator whose two operands are literals, which the compiler folds")
    if sym in ("&&", "||", "^^"):
        # The right operand of a short-circuit operator is wrapped in `Skip` (`18 <uint16> <expr>`) and the word is
        # not the operand's length. The image says what it is: `execAndAnd_BoolBool` (GNatives[0x82] = 0x00041120)
        # evaluates the left operand, reads the word and, when that operand is false, sets `Code = <first byte of the
        # right operand> + word` (0x000411a4..0xad). So the word is a byte distance to the enclosing expression's
        # end -- and over this corpus it lands 1..3 bytes *past* the body's last token (`Pawn.PressingFire` writes 20
        # where the body holds 17 more bytes), i.e. it is a compiler code-array position this corpus does not
        # reproduce from the emitted bytes. Refused, with the measurement in `docs/COMPILER_FRONTEND.md`.
        raise Refused("a short-circuit operator, whose right operand is a `Skip` node")
    if 0x70 <= token <= 0xFF:
        head = bytes([token])
    elif token >= 0x100:
        head = bytes([0x60 + (token >> 8), token & 0xFF])
    else:
        raise Refused("an operator token below 0x70 (%d), which collides with a real opcode" % token)
    node = head + emit_expr(left, t1, env)[0] + emit_expr(right, t2, env)[0] + b"\x16"
    return _converted(node, result, rettype), "operator"


def _emit_cast(name, inner, env):
    """`Type(expr)` -> `39 <slot> <expr>` when the type is a primitive, `2E <class ref> <expr>` when it is a class.

    The slot is the image's own cast table read backwards: the row*named* `<From>To<To>` for the operand's declared
    type and the cast's target (`StringToInt` is slot 74, `FloatToString` 85, `ObjectToString` 86), and only a row
    the build installs is used -- a pair with no tokens in the artifact has no slot to write.
    """
    kind = _cast_kind(name, env)
    low = name.lower()
    if kind == "primitive":
        src = _type_of(inner, env).lower()
        slot = _cast_slots().get((src, low))
        if slot is None:
            raise Refused("a cast from %s to %s the image's cast table does not install"
                          % (src or "an unknown type", low))
        return bytes([0x39, slot]) + emit_expr(inner, src, env)[0], "primitive-cast"
    if kind == "object":
        ref = (env["scope"] or {}).get("classrefs", {}).get(low)
        if ref is None:
            raise Refused("a cast to a class this package neither exports nor imports")
        return bytes([0x2E]) + write_index(ref) + emit_expr(inner, "", env)[0], "object-cast"
    raise Refused("a call the class chain does not declare")


def _prop_by_name(nm, env):
    """A property record by name through the body's own class chain -- what a struct member and a bare read use."""
    hit = (env["scope"] or {}).get("props", {}).get(nm)
    if hit is None:
        return None
    return (hit[0], hit[1], hit[2], _friendly(nm, env))


def _member_record(obj, nm, env):
    """The property record of `nm` on the *receiver's* class, or None.

    The receiver's declared class comes from `inventory/class_properties.csv` (`GUIComboBox.List` is a `GUIListBase`),
    and the record is then the one that class or its nearest ancestor declares -- the same walk `_scope` does for the
    body, one level out. A member the package declares on more than one class in the chain is refused rather than
    guessed: a wrong index emits plausible bytes.
    """
    scope = env["scope"] or {}
    if len(obj) != 1 or obj[0][0] not in ("ident", "keyword"):
        return None
    pack = (scope.get("pack") or "").lower()
    classes = scope.get("objclass") or {}
    target = ""
    for anc in env.get("order") or []:
        target = classes.get((anc, obj[0][1].lower()), "")
        if target:
            break
    if not target:
        return None
    chain = scope.get("chain") or {}
    props = scope.get("props_all") or {}
    cands = props.get(nm) or []
    for p, c in _ancestors(chain, pack, target):
        hit = [x for x in cands if x[1] == c]
        if hit:
            friendly = (scope.get("friendly") or {}).get((p, c, nm), "")
            return (INSTANCE_TOKEN, hit[0][0], PROP_TYPE.get(hit[0][2], ""), friendly)
    return None


def _emit_member(expr, rettype, env):
    """The member node: a receiver chain is one of three different tokens, decided by the receiver's type.

    | the source | the bytes | why |
    |---|---|---|
    | `Target.Health` (an object) | `19 <obj> 05 00 04 <member>` | `Context`: object, skip word, member's value size, member |
    | `PathName.FullPath` (a struct) | `36 <member> <struct read>` | `StructMember` names the **field first** and the value after it |
    | `Recipients.Length` (an array) | `37 <array read>` | the length operator, no property at all |

    Only a property member is modelled. The size byte is the *member's* value size (see `PROP_SIZE`) and the skip word
    is `CONTEXT_SKIP` for an `InstanceVariable` member; a *call* member's word is not that constant, so `A.F()` and
    `A.F().B` are refused by name rather than written with a number the corpus does not support.
    """
    at = _dot_at(expr)
    if at is None:
        raise Refused("a compound expression, which needs the statement walk")
    obj, member = expr[:at], expr[at + 1:]
    if len(member) != 1 or member[0][0] not in ("ident", "keyword"):
        raise Refused("a receiver chain whose member is not one name")
    nm = member[0][1].lower()
    if len(obj) == 1 and obj[0][0] in ("ident", "keyword") and obj[0][1].lower() == "default":
        # `Default.X` is the class default object's own record, written with `DefaultVariable` (0x02) in place of
        # `InstanceVariable`: `return Default.PickupMessage;` is `04 02 47 7f` and `return default.bIsConsoleMessage;`
        # is `04 2d 02 5c 65`. The index is therefore the same record the bare name names on this class -- the
        # chain's own property table, not a receiver's.
        hit = _prop_by_name(nm, env)
        if hit is None:
            raise Refused("a default member the class chain does not declare")
        token, index, ptype, _friendly_type = hit
        if token != INSTANCE_TOKEN:
            raise Refused("a default member reached through a local")
        if ptype not in PROP_SIZE:
            raise Refused("a %s default member, whose value size this walk does not carry" % (ptype or "typeless"))
        if ptype == "bool":
            return _converted(bytes([0x2D, 0x02]) + write_index(index), "bool", rettype), "default-bool"
        return _converted(bytes([0x02]) + write_index(index), ptype, rettype), "default"
    objtype = _type_of(obj, env).lower()
    # The node is chosen by the receiver's *record kind*, not by its declared type word: `PathName` is a `FilePath`
    # struct (a `StructMember`), while a cast or an operator wants that word (`SquadDir` is a `Vector`).
    objkind = _record_kind(obj, env) or objtype
    if objkind in ("array", "string"):
        if nm != "length":
            raise Refused("a %s member that is not its length" % objkind)
        return _converted(bytes([0x37]) + emit_expr(obj, "", env)[0], "int", rettype), "length"
    hit = _member_record(obj, nm, env) if objkind != "struct" else _prop_by_name(nm, env)
    if hit is None:
        # No fallback to the body's own chain: `Index` is declared on half the GUI classes, and resolving it that way
        # wrote export 8263 for a property whose record is 13. Two bodies also resolve the name to a same-named
        # export of *this* package while the package names an import (`InitFor` writes -20), which is wrong bytes
        # that look right -- the receiver's class is the only honest join.
        raise Refused("a member that is not a property of the receiver's class")
    token, index, ptype, friendly = hit
    if objkind == "struct":
        if ptype not in PROP_SIZE:
            raise Refused("a struct member of type %s, whose value size this walk does not carry"
                          % (ptype or "typeless"))
        return (_converted(bytes([0x36]) + write_index(index) + emit_expr(obj, "", env)[0], ptype, rettype),
                "struct-member")
    if token != INSTANCE_TOKEN:
        raise Refused("a member reached through a local")
    if ptype not in PROP_SIZE:
        raise Refused("a %s member, whose value size this walk does not carry" % (ptype or "typeless"))
    if ptype == "bool":
        # A bool member is `19 <obj> <skip> 04 2d 01 <index>`: the size byte is 4 like every other pointer-sized
        # member, and the read is a `BoolVariable` around the property. The skip word is 6 in 2,212 of the corpus's
        # 2,272 such nodes and 5 in the other 60, and those 60 are all `Outer.<bool>` -- the object is a property
        # *import* (`01 8e`, import 14), which is the shape PlayerInput writes in its own bodies.
        skip = 5 if _is_import_read(obj, env) else 6
        read = bytes([0x2D, INSTANCE_TOKEN]) + write_index(index)
        node = (bytes([0x19]) + emit_expr(obj, "", env)[0] + struct.pack("<H", skip) + bytes([4]) + read)
        return _converted(node, "bool", rettype), "bool-context"
    read = bytes([INSTANCE_TOKEN]) + write_index(index)
    node = (bytes([0x19]) + emit_expr(obj, "", env)[0] + struct.pack("<H", CONTEXT_SKIP)
            + bytes([PROP_SIZE[ptype]]) + read)
    # the cast sits *outside* the node: `return w.Default.AutoSwitchPriority;` in a float function is
    # `04 39 3f 19 ...` -- IntToFloat, then the Context
    return _converted(node, ptype, rettype), "context"


def emit_expr(expr, rettype, env):
    """The bytes of one expression, or Refused with the reason."""
    names = env["names"]
    expr = _strip_parens(expr)
    if not expr:
        return bytes([0x0B]), "nothing"                  # Return; -> Return Nothing
    if len(expr) == 2 and expr[0] == ("op", "-") and expr[1][0] == "number":
        expr = [("number", "-" + expr[1][1])]
    if len(expr) != 1:
        if _operator_types(expr, env) is not None:
            # the loosest depth-0 operator is the expression's root, so it is tried before a call or a member:
            # `Foo(1) + 2` is an `Add_IntInt`, not a call
            return _emit_operator(expr, rettype, env)
        if expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "vect":
            return _emit_vector(expr), "vector"
        if _is_call(expr) and _cast_kind(expr[0][1], env):
            return _emit_cast(expr[0][1], expr[2:-1], env)
        if _is_call(expr):
            return emit_call(expr, env), "call-value"
        if _dot_at(expr) is not None:
            return _emit_member(expr, rettype, env)
        raise Refused("a compound expression, which needs the statement walk")
    kind, val = expr[0]
    if kind in ("ident", "keyword") and val.lower() == "class":
        # `class` (bare) compiles to `Self`: `GetItemName(string(class))` and `GetItemName(string(self))` are the
        # same seven bytes (`1b 31 39 56 17 16`), so the token is Self and the conversion names the object.
        return bytes([0x17]), "self-class"
    if kind in ("ident", "keyword") and val.lower() not in ("true", "false", "none", "self"):
        read = _emit_var(val, env, rettype)
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
        text = _literal_text(val[1:-1])
        if t == "string":
            return bytes([0x1F]) + text.encode("latin-1") + b"\x00", "string-const"
        if t == "name":
            # The empty name is index 0: `Vehicle.GetWeaponBoneFor` is `return '';` and the package writes
            # `04 21 00 04 0b 47`, so the literal is the name table's zero entry rather than an absent string (8
            # bodies).
            idx = 0 if not text else names.get(text.lower())
            if idx is None:
                raise Refused("a name literal the package does not carry (%d chars)" % len(text))
            return bytes([0x21]) + write_index(idx), "name-const"
        raise Refused("a quoted literal in a %s function" % (rettype or "typeless"))
    raise Refused("an expression the emitter does not model (%s)" % (val if kind != "string" else "a string"))


def _literal_text(raw):
    """The value a string literal spells: an escape in the text is one byte in the package.

    `IMusicActor.ResolveName` writes `1f 64 3a 5c 4d 55 53 49 43 5c 00` for `"d:\\Music\\"`, so `\\` is a single
    backslash -- the quoted form is the source's, not the value's.
    """
    out, i = [], 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t", "0": "\x00"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _emit_vector(expr):
    """`vect(a, b, c)` -> `23` and three little-endian f32 (measured: `return vect(0,0,0);`)."""
    parts = _split_commas(expr[2:-1]) if expr[1] == ("op", "(") and expr[-1] == ("op", ")") else []
    if len(parts) != 3 or any(len(p) != 1 or p[0][0] != "number" for p in parts):
        raise Refused("a vector literal that is not three numeric components")
    return bytes([0x23]) + b"".join(struct.pack("<f", float(_number(p[0][1]))) for p in parts)


def _split_commas(tokens):
    """Split at *depth-0* commas: `Explode(HitLocation, vect(0,0,0), none)` is three arguments, not five.

    The split is shared with `params_of` and `enums_of`, and without the depth a comma inside a nested call moved the
    argument count of every call that carries one -- 10 bodies were refused as "2 argument(s) to a 1-parameter
    callee" (`SetDrawScale(FMax(0.01, DrawScale - ...))`).
    """
    out, cur, depth = [], [], 0
    for t in tokens:
        if t[0] == "op" and t[1] in "([{":
            depth += 1
        elif t[0] == "op" and t[1] in ")]}":
            depth -= 1
        if t == ("op", ",") and depth == 0:
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
    dele = (scope or {}).get("delegates", {}).get(stmt[0][1].lower())
    if dele is not None:
        # `DelegateFunction`: the DelegateProperty's export index, then the callee's name index, then the arguments.
        idx = how[1] if how and how[0] == "virtual" else env["names"].get(stmt[0][1].lower())
        if idx is None:
            raise Refused("a delegate call whose name the name table does not carry")
        head = bytes([0x43]) + write_index(dele) + write_index(idx)
    elif how is None:
        raise Refused("a call to a name the class chain does not declare")
    elif how[0] == "native":
        # An exec native's *token is its own opcode* once it reaches 0x70: the compiler writes `71 21 ... 16` for
        # `GotoState(...)`, `75` for `Enable(113)` and `76` for `Disable(118)` -- one byte, not the HighNative pair
        # (measured on 135 declarers in this build; emitting `60 71` cost a whole family of differences).
        value = how[1]
        if 0x70 <= value <= 0xFF:
            head = bytes([value])
        elif value >= 0x100:
            head = bytes([0x60 + (value >> 8), value & 0xFF])
        else:
            raise Refused("a native token below 0x70 (%d), which collides with a real opcode" % value)
    elif how[0] == "final":
        head = bytes([0x1C]) + write_index(how[1])
    else:
        head = bytes([0x1B]) + write_index(how[1])
    params = _callee_params(stmt[0][1], env)
    if params is None:
        if args:
            raise Refused("a call whose callee declaration the text does not carry")
        return head + b"\x16"
    if len(args) > len(params):
        raise Refused("a call with %d argument(s) to a %d-parameter callee" % (len(args), len(params)))
    short = [p for p in params[len(args):] if not p[2]]
    if short:
        raise Refused("a call with %d argument(s) to a %d-parameter callee (%d required)"
                      % (len(args), len(params), len(params) - len(short)))
    if not args:
        return head + b"\x16"
    out = head
    for arg, (ptype, _pname, _optional) in zip(args, params):
        if not arg:
            raise Refused("a call that leaves an optional argument empty")
        if ptype.lower() == "byte" and _int_literal(arg) is not None:
            # An integer literal is typed `int` even at a `byte` parameter: the compiler writes `39 3d`
            # (IntToByte, the image's cast table) and then the *int* constant (`2c ff` IntConstByte), not `24 ff`
            # (ByteConst). Measured on `GUIController.GetFocusedControl`/`SetFocusedControl`, whose only difference
            # is those three bytes.
            slot = _cast_slots().get(("int", "byte"))
            if slot is not None:
                out += bytes([0x39, slot]) + emit_expr(arg, "int", env)[0]
                continue
        out += emit_expr(arg, ptype, env)[0]
    return out + b"\x16"


def emit_assign(stmt, at, env):
    """`X = value;` -> `0F <InstanceVariable> <index> <value>`, or `14 2D <InstanceVariable> <index> <bool>`."""
    lhs, rhs = stmt[:at], stmt[at + 1:]
    if len(lhs) != 1 or lhs[0][0] not in ("ident", "keyword"):
        # The target is itself an expression: `H.LastPickupTime = H.Level.TimeSeconds;` is `0f <Context read> <value>`
        # and `Level.bClassicView = true;` is `14 <Context read> 27` -- Let versus LetBool is the *target's* declared
        # type, and the target is written exactly as a read of the same chain. Shapes that are not a plain member
        # (`modifiers.Length = 0;` writes `0f 37 <read> 0f 25`, an extra token this walk does not model) are refused.
        ptype = _type_of(lhs, env).lower()
        node, shape = emit_expr(lhs, "", env)
        if shape not in ("context", "bool-context", "struct-member"):
            raise Refused("an assignment whose target is a %s expression" % shape)
        if ptype == "bool" or shape == "bool-context":
            return bytes([0x14]) + node + emit_expr(rhs, "bool", env)[0]
        if ptype in ("struct", "array", "delegate", "pointer", ""):
            raise Refused("an assignment to a %s member" % (ptype or "typeless"))
        return bytes([0x0F]) + node + emit_expr(rhs, ptype, env)[0]
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


def _callee_return_type(name, env):
    """The declared return type of a callee the class chain carries, or "" when the text does not say."""
    low = name.lower()
    for cls in env.get("order") or []:
        hit = (env.get("rets") or {}).get((cls, low))
        if hit:
            return hit.lower()
    return ""


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
    if stmt and stmt[0][0] == "keyword" and stmt[0][1].lower() == "assert":
        # `assert(false);` is `09 26 08 28`: the opcode, the **source line as uint16**, then the expression -- and a
        # `bool` slot, so `false` is `28`. `Actor.Crash`'s assert sits on line 2086 of the shipped text and the
        # package writes 0x0826, so the extracted text keeps the compiler's own numbering.
        line = env.get("assert_line")
        if not line or not 1 <= line <= 0xFFFF:
            raise Refused("an assert whose source line the corpus does not pin")
        if len(stmt) < 4 or stmt[1] != ("op", "(") or _paren_end(stmt, 1) != len(stmt) - 1:
            raise Refused("an assert whose argument the walk cannot bound")
        expr, shape = emit_expr(stmt[2:-1], "bool", env)
        return bytes([0x09]) + struct.pack("<H", line) + expr + TRAILER, "assert", "assert"
    if any(k == "keyword" and v.lower() in ("if", "for", "while", "do", "switch", "case", "else", "foreach")
           for k, v in stmt):
        raise Refused("a statement that is control flow")
    at = _assign_at(stmt)
    if at is not None:
        return emit_assign(stmt, at, env) + TRAILER, "assign", "assign"
    call = emit_call(stmt, env)
    if stmt and stmt[0][0] in ("ident", "keyword") and _callee_return_type(stmt[0][1], env) == "string":
        # A statement call whose declaration returns `string` is prefixed with `EatString` (0x0e):
        # `PlayerController.LoadGame` is `0e 1b 11 a8 1f "LoadSaveGame" 00 01 5e 6e 16 16` and the two calls this
        # walk emits without the prefix (`Logf(... $ ...)`, `log(... $ ...)`) return nothing. The temporary the call
        # produces is eaten instead of copied. `$`/`@` themselves are ordinary operator nodes (0x70/0xa8) -- the
        # previous note credited them with the prefix, which this reading corrects.
        return bytes([0x0E]) + call + TRAILER, "call", "eat-string-call"
    return call + TRAILER, "call", "call"


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
    """The package's own records as the compiler addressed them: property and function exports, the imports, and
    the classes an object cast can name."""
    props, funcs, imports, classes = {}, {}, {}, {}
    for i, e in enumerate(pkg.exports):
        kind = kinds.get(i + 1, "")
        nm = names.get(i + 1, "").lower()
        owner = names.get(e["outer"], "").lower() if e["outer"] > 0 else ""
        if kind in PROPERTY_CLASSES:
            props.setdefault(nm, []).append((i + 1, owner, kind))
        elif kind == "Function":
            funcs.setdefault(nm, []).append((i + 1, owner))
        elif kind == "Class":
            classes.setdefault(nm, []).append((i + 1, "Class"))
    for j, im in enumerate(pkg.imports):
        # an import's `class` field is a name index, not an export index, so `class_name_of` cannot read it
        iname, ikind = str(pkg.name(im["name"])).lower(), pkg.name(im["class"])
        imports.setdefault(iname, []).append((-(j + 1), ikind))
        if ikind == "Class":
            classes.setdefault(iname, []).append((-(j + 1), "Class"))
    return props, funcs, imports, classes


def _scope(pkg, kinds, names, tables, pack, cls, func_index, chain, natives, table_index, final_of=None):
    """The names one body may mention, each with the index and token the package's own record earns.

    A property is found by *name and owner*: the record's index is what the compiler wrote, so nothing is rebuilt.
    A callee is resolved by walking the ancestor chain to the class that declares it -- the native table says the
    token (0 means "not a native opcode, call it by name"), and its `final` flag picks the call token.
    """
    props, funcs, imports, classes = tables
    scope = {"props": {}, "locals": {}, "funcs": {}, "classrefs": {}, "props_all": props, "chain": chain,
             "pack": pack, "cls": cls.lower(),
             # the type word the package artifact spells for every property record, keyed by (package, class, name):
             # a cast's slot needs `SquadDir` as `Vector`, and a receiver's member may live in another package
             "friendly": _obj_classes(),
             # the declared class of every object property of this package: what a receiver chain's member is
             # resolved through (`GUIComboBox.List` is a `GUIListBase`, so `List.Index` is `GUIListBase.Index`)
             "objclass": {(c, pr): t for (pk, c, pr), t in _obj_classes().items() if pk == pack.lower()}}
    # The class an object cast names is a compact *object reference*: `AIController(Controller)` writes `2e 59 01`,
    # and 89 is that class's export in Engine.u; `Material(...)` in StargateGame writes `2e c8 01`, which is import
    # 72 (-72) -- the class is named the way the compiler names any object, not by its name-table index.
    for nm, cands in classes.items():
        own = [idx for idx, _k in cands if idx > 0]
        imp = [idx for idx, _k in cands if idx < 0]
        if own:
            scope["classrefs"][nm] = own[0]
        elif len(imp) == 1:
            scope["classrefs"][nm] = imp[0]
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
    # A `delegate` is *two* records: a DelegateProperty named `__X__delegate` and a Function named `X`, so the call
    # `OnChange(Self)` is a `DelegateFunction` naming **both** -- `43 33 1a 17 16` is export 51
    # (`GUI.GUIComponent.__OnChange__Delegate`) and name index 26 (`OnChange`), which is why the base name has to be
    # recovered from the mangled one rather than looked up as a property.
    scope["delegates"] = {}
    for nm, cands in props.items():
        if not (nm.startswith("__") and nm.endswith("__delegate")):
            continue
        picked = None
        for anc in order:
            hit = [c for c in cands if c[1] == anc]
            if hit:
                picked = hit[0]
                break
        if picked is not None:
            scope["delegates"][nm[2:-len("__delegate")]] = picked[0]
    # A native that an *ancestor in another package* declares is still in this class's chain: `Object` lives in
    # `Core.u`, so `GetItemName`/`GetStateName` were refused as "not declared by the class chain" for every class
    # outside Core (51 bodies). The filter is the class in the chain, not the package: the lookup below still keys
    # the token by (package, class, name), so a same-named native of another class cannot answer.
    declared = set(funcs) | {f for (_p2, c2, f) in natives if c2 in order}
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


def _assert_line(text, body):
    """The source line the compiler recorded for the `assert` in `body`, or None when the text does not pin it.

    The body's tokens are matched against the file's own span-carrying token stream and the match must be **unique**:
    `assert(false);` occurs once in `Actor.uc`, so its line (2086) is the compiler's operand; a body whose token run
    occurs twice is refused rather than numbered by the first hit.
    """
    toks, bad = lexer.lex(text)
    if bad is not None:
        return None
    spans = [(k, text[a:b], a) for k, a, b in toks if k not in ("space", "comment", "high")]
    want = list(body or ())
    n = len(want)
    if not n:
        return None
    hits = [i for i in range(len(spans) - n + 1) if [(k, v) for k, v, _a in spans[i:i + n]] == want]
    if len(hits) != 1:
        return None
    for j in range(n):
        if want[j][0] == "keyword" and want[j][1].lower() == "assert":
            return text.count("\n", 0, spans[hits[0] + j][2]) + 1
    return None


def _source_index(tree):
    """`{(class, state, function): (rettype, body, text, native, final)}` from the extracted corpus, plus every
    declaration's parameter list, every enum constant and every callee's return type -- the three joins a cast's
    slot and an argument's token need."""
    index = {}
    params = {}
    rets = {}
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
                if rettype and not state:
                    # a cast's slot is chosen by the operand's type, and an operand that is a call is typed by the
                    # callee's declared return type: `string(GetValue())` is FloatToString, not a guess
                    rets.setdefault((cls, fn.lower()), rettype)
                keys = [(cls, state.lower(), fn.lower())]
                if not state:
                    # The class-level key is filled by class-level declarations only. A state function of the same
                    # name is a *different* export -- Mover declares a bodyless `DisableTrigger()` at class scope and
                    # a `Disable('Trigger')` inside a state -- and letting the state body fill this key scored the
                    # stub against the state's bytes: 14 shipped bodies read as "differ" that way.
                    keys.append((cls, "", fn.lower()))
                # the `assert` statement's line operand is the compiler's own source line, so it is read here, from
                # the text, once per declaration -- and only for a body that carries one
                aline = _assert_line(text, body) if body and any(
                    k == "keyword" and v.lower() == "assert" for k, v in body) else None
                for key in keys:
                    old = index.get(key)
                    if old is None or len(body or ()) > len(old[1] or ()):
                        # a class that ships two *class-level* declarations of one name (an `#if` branch, or a stub
                        # beside its body) compiled the longer one: prefer it, so the comparison is not scored
                        # against a stub
                        index[key] = (rettype, body, text, is_native, is_final, decl_params, aline)
            if "enum" in text.lower():
                for enum, consts in enums_of(text):
                    enum_names.add(enum.lower())
                    for cname, ordinal in consts:
                        enums.setdefault((cls, cname.lower()), (ordinal, enum))
    return index, files, params, enums, enum_names, rets


def score(tree, dump, out_dir, residue_dir=None):
    dis = _dis()
    from ue2pkg import Package                                  # noqa: PLC0415 - only with a real dump

    index, files, params, enums, enum_names, rets = _source_index(tree)
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
        # the body's own export index: `_scope` reads locals off the export whose outer is this function, so the
        # index has to be the loop's own -- reusing `i` from the name-table sweep above scoped every body to the
        # last name index, so no local was ever built and a parameter read fell back to the property table
        for ei, e in enumerate(pkg.exports):
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
            rettype, src_body, _text, is_native, _is_final, decl_params, aline = got
            scope = _scope(pkg, kinds, names, tables, pack, cls, ei + 1, chain, natives, table_index, final_of)
            env = {"names": table_index, "scope": scope, "params": params, "enums": enums,
                   "enum_names": enum_names, "class_names": class_names, "order": scope["order"],
                   "rets": rets, "assert_line": aline,
                   # a parameter's struct name is in its own declaration (`Vector momentum`), and the package's
                   # record only says `StructProperty`
                   "paramtypes": {p[1].lower(): p[0].lower() for p in (decl_params or [])}}
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
function CallShort() { Mix(false); }
function CallMissing() { Foo(); }
function Bump() { Health = Bump(7); }
function FromVar() { Health = Slot; }
function int FromByte() { return Count; }
function FromFlag() { bDead = Flag2; }
function CompoundArg() { Foo(1 + 2); }
function FloatArg() { Foo(1.5); }
function WrongArgc() { Foo(1, 2); }
function IfStmt() { if (bDead) Health = 1; }
function CallExec() { GotoState('Probe'); }
function CallDelegate() { OnChange(Self); }
function CallBadNative() { TinyToken(); }
function name EmptyName() { return ''; }
function CallNested() { Mix3(false, vect(0, 0, 0)); }
function CallByteLit() { MixByte(255); }
function HealthOf() { return Target.Health; }
function Probe AsProbe() { return Probe(Target); }
function string AsText() { return string(self); }
function string AsClassText() { return string(class); }
function int AsInt() { return int(bDead); }
function bool CastNoSlot() { return bool(vect(0,0,0)); }
function CallCast() { Foo(int(bDead)); }
function Asserted() { assert(false); }
function AssertNoLine() { assert(true); }
function AndInt() { return Count & 2; }
function OpNoSlot() { return Health & 2.0; }
function BoolMember() { return Target.bFlag; }
function AsRotator() { return rotator(SquadDir); }
function string ConcatOp() { return Caption $ Suffix; }
function string AtOp() { return Caption @ Suffix; }
function string DefaultText() { return default.Caption; }
function bool DefaultFlag() { return default.bDead; }
function SetMember() { Target.Health = 7; }
function SetMemberFlag() { Target.bFlag = true; }
function Speak() { MyText(Caption); }
function SetLength() { Items.Length = 0; }
"""

# A class that declares a bodyless stub at class scope and a body of the same name inside a state: the two are
# different exports, and the class-level one is the stub (Mover.DisableTrigger).
STUB_STATE = """class Probe extends Actor;

function DisableTrigger();

state OpenTimedMover
{
    function DisableTrigger()
    {
        Disable('Trigger');
    }
}
"""

# The package's own tables, as `_scope()` builds them: a property's index, a local's, and a callee's token.
SCOPE = {"props": {"health": (0x01, 302, "int"), "bdead": (0x01, 45, "bool"),
                   "target": (0x01, 611, "object"), "flag2": (0x01, 46, "bool"),
                   "bflag": (0x01, 47, "bool"), "squaddir": (0x01, 620, "struct"),
                   "caption": (0x01, 5, "string"), "suffix": (0x01, 6, "string"),
                   "items": (0x01, 700, "array")},
         "chain": {("probe", "probe"): ("", "")}, "pack": "probe", "cls": "probe",
         "locals": {"slot": (0x00, 611, "int"), "count": (0x00, 612, "byte")},
         "classrefs": {"probe": 4, "actor": -77},
         "funcs": {"destroy": ("native", 279), "myfinal": ("final", 763), "myvirtual": ("virtual", 39),
                   "foo": ("virtual", 41), "bar": ("virtual", 44), "baz": ("native", 705),
                   "mix": ("virtual", 46), "bump": ("virtual", 47), "gotostate": ("native", 113),
                   "tinytoken": ("native", 2), "onchange": ("virtual", 26),
                   "mix3": ("virtual", 48), "mixbyte": ("virtual", 49), "mytext": ("virtual", 50)},
         "delegates": {"onchange": 51}}

# The receiver walk's join: `Target` is a property of class `Probe`, so `Target.Health` is `Probe.Health`.
SCOPE["objclass"] = {("probe", "target"): "probe"}

# The raw record table the receiver walk reads: `{name: [(index, owner, kind)]}`.
SCOPE["props_all"] = {nm: [(v[1], "probe", "ObjectProperty" if v[2] == "object" else
                            "BoolProperty" if v[2] == "bool" else
                            "StructProperty" if v[2] == "struct" else "IntProperty")]
                      for nm, v in SCOPE["props"].items()}

# The type words the package artifact spells: `SquadDir` is a `Vector`, and `bFlag` is a `bool` member.
SCOPE["friendly"] = {("probe", "probe", "squaddir"): "vector", ("probe", "probe", "bflag"): "bool",
                     ("probe", "probe", "health"): "int", ("probe", "probe", "bdead"): "bool",
                     ("probe", "probe", "flag2"): "bool", ("probe", "probe", "target"): "probe"}

# The declarations' parameter lists and the enum the class chain declares: what an argument's token comes from.
# The third field is `optional`: `Mix(false)` legitimately stops short of `Time`, `Foo()` does not.
PARAMS = {("probe", "foo"): [("int", "A", False)], ("probe", "bar"): [("bool", "Flag", False)],
          ("probe", "baz"): [("EProbe", "Mode", False)],
          ("probe", "mix"): [("bool", "Flag", False), ("float", "Time", True)],
          ("probe", "bump"): [("int", "A", False)], ("probe", "gotostate"): [("name", "NewState", False)],
          ("probe", "onchange"): [("object", "Sender", False)],
          ("probe", "mix3"): [("bool", "Flag", False), ("vector", "V", False)],
          ("probe", "mixbyte"): [("byte", "Mode", False)],
          ("probe", "mytext"): [("string", "S", False)]}
ENUMS = {("probe", "probe_walking"): (1, "EProbe"), ("probe", "probe_swimming"): (3, "EProbe")}
ENV = {"names": {}, "scope": SCOPE, "params": PARAMS, "enums": ENUMS, "enum_names": {"eprobe"},
       "class_names": {"probe"}, "order": ["probe"],
       "rets": {("probe", "bump"): "int", ("probe", "mytext"): "string"},
       "assert_line": 2086, "paramtypes": {}}

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
    "callshort": "1b2e2816040b47",                  # Mix(false) -- the optional `Time` gets no placeholder
    "bump": "0f016e041b2f2c0716040b47",      # Health = Bump(7);
    "fromvar": "0f016e04006309040b47",       # Health = Slot; -- a local's own index
    "frombyte": "04393a006409040b47",        # return Count; -- a byte local in an int function: PrimitiveCast ByteToInt
    "fromflag": "142d012d2d012e040b47",      # bDead = Flag2; -- bool property read as the value
    "callexec": "71210516040b47",            # GotoState('Probe') -- token 113 *is* the opcode; name index 5
    "calldelegate": "43331a1716040b47",      # OnChange(Self) -- DelegateFunction: export 51, name index 26
    "emptyname": "042100040b47",             # return ''; -- the name table's zero entry
    "callnested": "1b30282300000000000000000000000016" "040b47",   # Mix3(false, vect(0,0,0)): three arguments at
    "callbytelit": "1b31393d2cff16040b47",   # MixByte(255): IntToByte, then the int constant -- not ByteConst
    "healthof": "0419016309050004016e04040b47",   # return Target.Health; -> Context: 19, obj, 05 00, size 4, member
    "asprobe": "042e04016309040b47",              # return Probe(Target); -- DynamicCast names the class's export (4)
    "astext": "04395617040b47",                   # return string(self); -- ObjectToString
    "asclasstext": "04395617040b47",              # return string(class); -- `class` is Self: the same seven bytes
    "asint": "0439412d012d040b47",                # return int(bDead); -- BoolToInt
    "callcast": "1b2939412d012d16040b47",       # Foo(int(bDead)): the cast is the argument -- BoolToInt + the read
    "asserted": "09260828040b47",                  # assert(false); -- Assert, line 2086, False
    "andint": "049c393a0064092c0216040b47",        # return Count & 2; -- And_IntInt, ByteToInt, IntConstByte
    "boolmember": "04190163090600042d012f040b47",  # return Target.bFlag; -- Context skip 6, size 4, BoolVariable
    "asrotator": "043950016c09040b47",             # return rotator(SquadDir); -- a Vector property: VectorToRotator
    "concatop": "04700105010616040b47",            # return Caption $ Suffix; -- Concat_StringString (0x70), operand, operand, 16
    "atop": "04a80105010616040b47",                # return Caption @ Suffix; -- At_StringString is token 168, its own opcode
    "defaulttext": "040205040b47",                 # return default.Caption; -- DefaultVariable (0x02) + the record's index
    "defaultflag": "042d022d040b47",               # return default.bDead; -- BoolVariable around DefaultVariable + index 45
    "setmember": "0f19016309050004016e042c07" "040b47",   # Target.Health = 7; -- Let + the Context read + the value
    "setmemberflag": "14190163090600042d012f" "27040b47",  # Target.bFlag = true; -- LetBool + the bool Context + True
    "speak": "0e1b32010516040b47",                 # MyText(Caption); -- a call returning `string` is prefixed with EatString
}

# Bodies the emitter must refuse, each for a named reason rather than a guess.
REFUSE = ("compoundarg", "floatarg", "wrongargc", "ifstmt", "nativestub", "callbadnative", "callmissing",
          "castnoslot", "opnoslot", "setlength")


def check(args):
    """Synthetic corpus plus the committed artifact's own consistency: no dump, no writes."""
    names = {"weapon_bone": 2063, "probe": 5}
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
    # An `assert` is numbered from the corpus, so a body the walk cannot pin a line for is refused rather than
    # given the first line that looks plausible.
    rettype, body, is_native = got["assertnoline"]
    try:
        emit_body(body, rettype, dict(env, assert_line=None), is_native)
    except Refused:
        pass
    else:
        print("FAIL uscript-emit: assertnoline was emitted with no source line")
        return 1
    # `_assert_line` is the line reader itself: the same body twice in one file is ambiguous and must answer None.
    one = "class P;\nfunction A()\n{\n\tassert(false);\n}\n"
    stmt_tokens = [("keyword", "assert"), ("op", "("), ("keyword", "false"), ("op", ")"), ("op", ";")]
    if _assert_line(one, stmt_tokens) != 4:
        print("FAIL uscript-emit: _assert_line does not read line 4 of a one-assert file")
        return 1
    two = one + "function B()\n{\n\tassert(false);\n}\n"
    if _assert_line(two, stmt_tokens) is not None:
        print("FAIL uscript-emit: _assert_line numbers an ambiguous body")
        return 1
    # `_scope` reads a body's locals off the export whose `outer` is the body's own export index. The score loop
    # once handed it the index left by the name-table sweep, so no local was built and a *parameter* read fell back
    # to the property table -- `2d 01` where the package wrote `2d 00`. Gate the index itself: the same record must
    # read as a local for its own index and as nothing for a stale one.
    class _Pkg:
        def __init__(self):
            self.exports = [{"name": 0, "outer": 0}, {"name": 1, "outer": 1}, {"name": 2, "outer": 2}]
            self.imports = []
            self._names = ["Probe", "Fn", "Slot"]
        def name(self, i):
            return self._names[i]
    pkg = _Pkg()
    kinds = {1: "Class", 2: "Function", 3: "IntProperty"}
    exp_names = {i + 1: pkg.name(e["name"]) for i, e in enumerate(pkg.exports)}
    tables = _class_tables(pkg, kinds, exp_names)
    here = _scope(pkg, kinds, exp_names, tables, "probe", "probe", 2, {("probe", "probe"): ("", "")}, {}, {})
    if here["locals"].get("slot") != (0x00, 3, "int"):
        print("FAIL uscript-emit: the body's own export index does not scope its local (%r)" % (here["locals"],))
        return 1
    stale = _scope(pkg, kinds, exp_names, tables, "probe", "probe", 3, {("probe", "probe"): ("", "")}, {}, {})
    if stale["locals"]:
        print("FAIL uscript-emit: a stale export index still scopes a local (%r)" % (stale["locals"],))
        return 1
    # P1: a state body of the same name must not fill the class-level key. One file, one stub, one state body.
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="sg1-emit-")
    try:
        with open(os.path.join(tmp, "Probe.uc"), "w", encoding="latin-1") as fh:
            fh.write(STUB_STATE)
        sidx, _f, _p, _e, _en, _r = _source_index(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    stub = (sidx.get(("probe", "", "disabletrigger")) or (None, None))[1]
    state = (sidx.get(("probe", "opentimedmover", "disabletrigger")) or (None, None))[1]
    if stub is not None:
        print("FAIL uscript-emit: a state body fills the class-level stub key (%r)" % (stub,))
        return 1
    if not state:
        print("FAIL uscript-emit: the state body did not land under its own state name")
        return 1
    # P3: a native of an ancestor in *another* package answers, and a same-named native of a class outside the
    # chain does not.
    cross = {("probe", "probe"): ("engine", "actor"), ("engine", "actor"): ("core", "object")}
    nat = {("core", "object", "getitemname"): (10, ""), ("core", "text", "otherfn"): (11, "")}
    sc = _scope(pkg, kinds, exp_names, tables, "probe", "probe", 2, cross, nat, {})
    if sc["funcs"].get("getitemname") != ("native", 10):
        print("FAIL uscript-emit: a native of an ancestor in another package does not resolve (%r)"
              % (sc["funcs"].get("getitemname"),))
        return 1
    if "otherfn" in sc["funcs"]:
        print("FAIL uscript-emit: a native of a class outside the chain resolves anyway (%r)"
              % (sc["funcs"]["otherfn"],))
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
