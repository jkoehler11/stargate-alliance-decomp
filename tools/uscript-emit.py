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
enum constant needs its declared values, a statement form the walk does not model), because a guess here is a wrong
body that looks like a measurement. The residue is what the next stage reads.

A body is a **list of statements** (`stmt_tree`): each statement emits on its own, the body appends its trailer once,
and the control-flow forms whose operands the corpus pins are emitted from an absolute position in the
compiler's uncompacted code array -- `if`, `if`/`else`, `while`, `for`, `do`/`until`, `switch`, whose `Case` chain
carries the position of the next case and ends on `Case 0xffff`, and `foreach`, whose `Iterator` token carries the
`IteratorPop`'s position after its own call expression. `break` and `continue` are the `Jump`s those
constructs publish: a `break` names the end of the nearest loop or switch, a `continue` the next iteration of the
nearest loop -- and in a `foreach` a `continue` is the iterator's step (`IteratorNext`) followed by that `Jump`, so
the loop pays a step per skip rather than a back edge. A `local` declaration emits nothing.

Three shapes the same walk pins on the corpus: a `return` inside a `foreach` pops the iterator first (`IteratorPop`
before `Return`, `Pawn.TouchingWaterVolume`), a `switch` on an object compares at width 4 like an int
(`InGameMenu.ButtonClick`), and a `switch` with no `case` still writes the `Case 0xffff` terminator
(`ExtrasPage.ButtonClick`).

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
_CLASS_PKG = {}          # {class: package} for a name only one package declares (see `_chain`)

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

# A **struct-valued** member's size is not in `PROP_SIZE`: the record says `StructProperty` and the artifact's
# `friendly_type` names the struct. The three engine structs the corpus reaches that way are all 12 bytes, and the
# size byte is the same 12 a `vector` property writes -- `return Pawn.Location;` is `04 19 01 86 05 00 0c 01 8a`
# (`AmbientCreatures.TransientAmbientCreature.PickDestination`). A struct the artifact names but this table does not
# is still refused rather than given a size the corpus never wrote.
STRUCT_SIZE = {"vector": 12, "rotator": 12, "coords": 12}

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


def _int_bytes(v):
    """The compiler's own token for an int constant: `25` zero, `26` one, `2c <byte>` 2..255, else `1d <int32>`."""
    if v == 0:
        return bytes([0x25])
    if v == 1:
        return bytes([0x26])
    if 0 < v < 256:
        return bytes([0x2C, v])
    return bytes([0x1D]) + struct.pack("<i", v)


def _consts_of(text):
    """`{name: value}` for every `const NAME = <int literal>;` in the text -- what an array dimension spells.

    GUIController's `var() transient CachedRungData LiveRungData[TOTAL_LADDER_RUNGS];` sizes the array from
    `const TOTAL_LADDER_RUNGS=17;` on the same class, and ShieldBelt's `AbsorbSounds[MAX_ABSORB_SOUNDS]` from
    `const MAX_ABSORB_SOUNDS = 3;`. Only a bare number is read: a const written as an expression is left unresolved
    and refuses the `ArrayCount` rather than folding a value the source does not spell.
    """
    toks = tokens_of(text)
    if toks is None:
        return {}
    out = {}
    for i in range(len(toks) - 3):
        if not (toks[i][0] == "keyword" and toks[i][1].lower() == "const"):
            continue
        if toks[i + 1][0] not in ("ident", "keyword"):
            continue
        j = i + 2
        if j < len(toks) and toks[j] == ("op", "="):
            j += 1
        if j < len(toks) and toks[j][0] == "number":
            v = _number(toks[j][1])
            if isinstance(v, int):
                out.setdefault(toks[i + 1][1].lower(), v)
    return out


def _array_dims(text):
    """`{name: N}` for every declaration in the text that spells a dimension: `var string Foo[4];`.

    `ArrayCount(X)` is not a call at all -- the compiler writes the **declared dimension** as an int literal, so the
    rule needs the number the source spells rather than a runtime read (`PlayerController.SetProgressMessage`'s
    `Index < ArrayCount(ProgressMessage)` is `... 2c 04 16` for `var string ProgressMessage[4];`, and
    `LevelInfo.AddEventToCache` writes `2c 40` for `var name EventCache[64];`). Only a `var`/`local` statement is
    read, and a declarator's `]` has to be followed by `;` or `,`: that is what separates a *declaration* from an
    index in an initializer (`var int A[4], B[2];` reads both, `X = Foo[3],` reads neither).
    """
    toks = tokens_of(text)
    if toks is None:
        return {}
    depth_at, d = [], 0
    for kind, val in toks:
        depth_at.append(d)
        if kind == "op" and val in "([{":
            d += 1
        elif kind == "op" and val in ")]}":
            d -= 1
    consts = _consts_of(text)
    out = {}
    i, n = 0, len(toks)
    while i < n:
        kind, val = toks[i]
        if not (kind == "keyword" and val.lower() in ("var", "local")):
            i += 1
            continue
        base = depth_at[i]
        j = i + 1
        if j < n and toks[j] == ("op", "("):
            close = _paren_end(toks, j)
            if close is None:
                i += 1
                continue
            j = close + 1                # `var(Private) string Foo[4];` carries its modifiers in a group
        k = j
        while k + 4 < n:
            if toks[k] == ("op", ";") and depth_at[k] == base:
                break
            if (toks[k][0] in ("ident", "keyword") and depth_at[k] == base
                    and toks[k + 1] == ("op", "[") and toks[k + 2][0] in ("number", "ident", "keyword")
                    and toks[k + 3] == ("op", "]")
                    and toks[k + 4][0] == "op" and toks[k + 4][1] in (";", ",")):
                # The dimension is a number (`Doors[2]`) or the name of a `const` (`LiveRungData[TOTAL_LADDER_RUNGS]`
                # beside `const TOTAL_LADDER_RUNGS=17;`). A const declared on an *ancestor* class is resolved in
                # `_scope`, where the class chain is known -- this text may not carry the declaration.
                dim_tok = toks[k + 2]
                if dim_tok[0] == "number":
                    v = _number(dim_tok[1])
                    if isinstance(v, int) and v > 0:
                        out.setdefault(toks[k][1].lower(), v)
                elif dim_tok[1].lower() in consts:
                    out.setdefault(toks[k][1].lower(), consts[dim_tok[1].lower()])
                else:
                    out.setdefault(toks[k][1].lower(), dim_tok[1].lower())
                k += 5
                continue
            k += 1
        i = max(k, i + 1)
    return out


def _array_count(expr, env):
    """The integer `ArrayCount(X)` compiles to: the **declared dimension**, written as an int literal.

    A name whose dimension the source does not spell is refused by name rather than given a guessed number, because
    a wrong dimension is a wrong body that still scores.
    """
    if not _is_call(expr) or expr[0][1].lower() != "arraycount":
        return None
    inner = _strip_parens(expr[2:-1])
    if len(inner) != 1 or inner[0][0] not in ("ident", "keyword"):
        raise Refused("an ArrayCount of something that is not a bare name")
    nm = inner[0][1].lower()
    n = ((env.get("scope") or {}).get("dims") or {}).get(nm)
    if n is None:
        raise Refused("an ArrayCount of a name whose declared dimension the source does not spell")
    return n


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
    (`IntToFloat`). Only pairs the table has a *used* slot for are cast. The same rule covers a `coerce` argument:
    `GetPlayerController(int ViewportIndex)` passes it to `coerce string ViewportIndexString`, and the package
    writes `39 53` (`IntToString`, slot 83) in front of the local.
    """
    want = (want or "").lower()
    if ptype and want and want != ptype:
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

    A **one-operand** declaration is a `preoperator`/`postoperator` statement with no `operator(N)` at all
    (`native(129) static final preoperator bool ! ( bool A )`), and it lands in `UNARY` instead; the census only
    started carrying those rows when its `\\boperator\\b` match was fixed, which is why `Not_PreBool` had no token.
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
                             r"\(\s*((?:out\s+|skip\s+|coerce\s+|optional\s+|const\s+)*)([A-Za-z<>]+)\s+\w+\s*,\s*"
                             r"((?:out\s+|skip\s+|coerce\s+|optional\s+|const\s+)*)([A-Za-z<>]+)\s+\w+\s*\)", decl)
                if m:
                    prec, ret, sym = int(m.group(1)), m.group(2).lower(), m.group(3)
                    a, b = m.group(5).lower(), m.group(7).lower()
                    OPERATORS[(sym, a, b)] = (int(token), ret)
                    OPERATOR_PREC.setdefault(sym, prec)
                    continue
                mu = re.match(r"^.*?\b(?P<form>pre|post)?operator\s+([A-Za-z<>]+)\s+(\S+?)\s*"
                              r"\(\s*((?:out\s+|skip\s+|coerce\s+|optional\s+|const\s+)*)([A-Za-z<>]+)\s+\w+\s*\)", decl)
                if mu:
                    UNARY[(mu.group(3), mu.group(5).lower())] = (int(token), mu.group(2).lower(),
                                                                 (mu.group(1) or "").lower())
    except OSError:
        pass
    return OPERATORS


OPERATORS = {}
OPERATOR_PREC = {}
UNARY = {}

# The compound-assignment spellings. A `+=` is **not** a `Let` around a `+`: its token is a separate native
# (`AddEqual_IntInt` 161 against `Add_IntInt` 146) and its first parameter is an `out`, so the row is chosen by the
# target's type first.
COMPOUND = ("+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")


def _operator_types(expr, env, site="", root=False):
    """`(symbol, left type, right type, result type, token, position)` for the loosest depth-0 operator, or None.

    A `byte` operand is promoted to `int` before the pair is looked up -- `DirtyMask & 254` is `And_IntInt` with the
    read wrapped in `39 3a` (`ByteToInt`), not a byte operator -- and an exact pair is preferred when the table has
    one. `site` and `root` are the *expression's* own type and position, which the mixed `int`/`float` fallback
    reads (`_emit_operator` passes them; `_type_of` does not).
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
    n1, n2 = _numeric(t1, env), _numeric(t2, env)
    for a, b in ((n1, n2), (t1, t2)):
        hit = _operators().get((sym, a, b))
        if hit is not None:
            token, ret = hit
            return sym, a, b, ret, token, at
    # `int` against `float`: the table installs the `(float, float)` row and the compiler wraps the int operand in
    # a `PrimitiveCast` -- `Pickup.BotDesireability`'s `RespawnTime < 10` is `b0 01 72 09 39 3f 2c 0a 16`, an
    # `IntConstByte 10` behind `IntToFloat`. That row is the row for a **literal** on the int side, because a
    # literal is retyped from the site.
    if (n1 == "float" and t2 == "int" and not _is_literal(right) and not _is_literal(left)
            and root and (site or "").lower() == "int"):
        # At the **root** of an expression whose own site is an `int` the compiler takes the `(int, int)` row and
        # narrows the float operand with `FloatToInt` (slot 68). `UWindowPulldownMenu.SetSelected` writes
        # `Items.FindEntry((Y - VBorder) / ItemHeight)` -- `Y` is a float, `ItemHeight` an int property, and the
        # parameter is an `int` -- as `1b 60 02 | 91 39 44 af 00 51 19 39 3f 01 7b 0a 16 01 45 0b 16 16`:
        # `Divide_IntInt(FloatToInt(Y - IntToFloat(VBorder)), ItemHeight)`. The **nested** subtraction in the same
        # expression keeps the float row, and so does every comparison: `AmbientCreatureManager.InSpawnRange` and
        # `ShieldBelt.Tick` promote the int side at a `bool` site. Restricting the narrowing to a root int site is
        # what keeps the six UWindow bodies and leaves the corpus's other mixed pairs alone.
        hit = _operators().get((sym, "int", "int"))
        if hit is not None:
            token, ret = hit
            return sym, "int", "int", ret, token, at
    if sym in COMPOUND and t1:
        # A **compound assignment's** row is chosen by its first (`out`) parameter -- the target's own declared
        # type -- and only then by the value's. `NewRot . Yaw += Delta * SpinRate` targets an `int` and the table
        # installs no `+=(int, float)`, so the compiler takes `AddEqual_IntInt` and narrows the value (`39 44`, a
        # `FloatToInt` in front of the product); `a *= 2` has no `*=(int, int)` row at all and takes
        # `MultiplyEqual_IntFloat` with the literal widened (`39 3f 2c 02`); `Velocity *= - 1` takes
        # `MultiplyEqual_VectorFloat` and widens (`dd 01 98 39 3f 1d ffffffff 16`). The value is widened to `float`
        # before it is narrowed to the target, because that is the order the corpus shows on all three.
        for a, b in ((t1, n2), (t1, "float"), (t1, t1)):
            if a == n1 and b == n2:
                continue
            hit = _operators().get((sym, a, b))
            if hit is not None:
                token, ret = hit
                return sym, a, b, ret, token, at
    if n1 == "int" or n2 == "int":
        # A **binary** operator's `int` operand is promoted to `float` when the exact pair is not installed and the
        # pair with that side replaced by `float` is: `200 * VRand()` is `Multiply_FloatVector` with the literal
        # cast (`d5 39 3f 2c c8 fc 16 16`). The right side is tried first, then the left, then both -- a row that
        # exists on the other side is a different native.
        f1, f2 = ("float" if n1 == "int" else n1), ("float" if n2 == "int" else n2)
        for a, b in ((f1, n2), (n1, f2), (f1, f2)):
            if a == n1 and b == n2:
                continue
            hit = _operators().get((sym, a, b))
            if hit is not None:
                token, ret = hit
                return sym, a, b, ret, token, at
    # `$` and `@` are declared `coerce string` on both sides, so a non-string operand is converted with the
    # image's own cast table: `AIMessage.DecRefCount` writes `" msg DecRefCount = : " $ RefCount` as
    # `1f <string> 00 39 53 01 6e 08 16` (`IntToString`, slot 83) and `log("STAT QUERY" @ EntryName)` writes
    # `39 57 00 56 20` (`NameToString`, slot 87). Only a pair whose conversion the build installs is taken.
    if sym in ("$", "@", "$=", "@="):
        hit = _operators().get((sym, "string", "string"))
        if hit is not None and all(n == "string" or _cast_slots().get((n, "string")) is not None
                                   for n in (n1, n2)):
            token, ret = hit
            return sym, "string", "string", ret, token, at
    return sym, t1, t2, "", None, at


def _numeric(t, env=None):
    """`byte` operands are promoted to `int` by the compiler: `DirtyMask & 254` is `And_IntInt`.

    A `class` operand compares as an **`object`**: the table installs `!=` for (Object, Object) and no row for a
    class-typed pair, and the compiler uses that row -- `Inventory.Destroyed`'s `if (Pawn(Owner) != None)` and
    `Mover.Detach`'s alike. Substituting it is a lookup rule only; the operand's own bytes are unchanged.

    A value whose type is a **class name** (`Pawn`, `Controller`, `LevelInfo`) is an object too, and that is the
    type `_type_of` hands back for a member whose record is an object property: `Controller.Pawn == self` is
    `EqualEqual_ObjectObject`, and the pair table has no `(controller, object)` row to find it under.
    """
    if t == "byte":
        return "int"
    if t == "class":
        return "object"
    if env and t and t in (env.get("class_names") or set()):
        return "object"
    return t


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
    from_props = False
    if low in scope["locals"]:
        token, index, ptype = scope["locals"][low]
    elif low in scope["props"]:
        token, index, ptype = scope["props"][low]
        from_props = True
    else:
        return None
    out = bytes([token]) + write_index(index)
    body = bytes([0x2D]) + out if ptype == "bool" else out
    if from_props:
        body = _outer_wrapped(low, body, ptype, want, env) or body
        if (env.get("within_call") and low in (scope.get("outside_props") or ())
                and scope.get("outer_ref") is not None and low not in (scope.get("outer_props") or ())):
            # A `within` call's arguments are compiled in `Outer`'s scope, so a property the package declares on a
            # class outside the body's chain is read through `Outer` even when the type is not bool:
            # `TriggerEvent(EventName, Pawn, Pawn)` in `CheatManager` is `19 01 8e 05 00 04 01 05` for each `Pawn`.
            if ptype != "object":
                raise Refused("a `within` call's argument of type %s, whose wrapper is not measured"
                              % (ptype or "unknown"))
            body = (bytes([0x19, INSTANCE_TOKEN]) + write_index(scope["outer_ref"]) + struct.pack("<H", 5)
                    + bytes([4]) + body)
    return _converted(body, ptype, want)


def _outer_wrapped(low, read, ptype, want, env):
    """`Context(Outer, 5, 4) <read>` for a name the package exports once, on a class outside the body's chain.

    `PlayerInput.ChangeSnapView` writes `19 01 8e 05 00 04 2d 01 6c 28` where the text spells `bSnapToLevel` and the
    record (export 2604) is declared on `PlayerController`, which `PlayerInput` (extends `Object`) does not reach --
    so the compiled read goes through `Outer` (property import 14, `01 8e`). Only the bool shape is measured: all 60
    `Outer.<bool>` nodes write the word 5 (a bare export bool writes 6), so anything else is refused, not guessed.
    """
    scope = env.get("scope") or {}
    if low not in (scope.get("outer_props") or ()):
        return None
    if ptype != "bool" or scope.get("outer_ref") is None:
        raise Refused("a name this package declares only on another class's chain (the `Outer.<bool>` shape)")
    outer = bytes([INSTANCE_TOKEN]) + write_index(scope["outer_ref"])
    return _converted(bytes([0x19]) + outer + struct.pack("<H", 5) + bytes([4]) + read, ptype, want)


def _emit_enum(name, env, corpus=True):
    """`PHYS_Swimming` -> `24 03`: the ordinal of a constant the class chain declares, or -- with `corpus` --
    one the whole corpus declares exactly once.

    An enum constant is a **global** name to this compiler, not a name resolved along the class chain: of the
    1,414 operator sites whose operand was a bare name, 104 are `NM_DedicatedServer` (41), `NM_Client` (23),
    `NM_Standalone` (21) and their siblings -- `LevelInfo.ENetMode`'s constants, spelled in bodies whose chain
    never reaches `LevelInfo`, and compiled as the ordinal alone. The chain is still tried first, because a
    declaration the body's own chain carries is the one the compiler read; the corpus table answers only a
    name whose every declarer agrees on one `(ordinal, enum)` pair (`env["enum_by_name"]`, built in `score`),
    which is what keeps a name two enums spell with different ordinals out of the guess.
    """
    low = name.lower()
    for cls in env["order"]:
        hit = env["enums"].get((cls, low))
        if hit is None:
            continue
        ordinal, enum = hit
        if not 0 <= ordinal <= 255:
            return None, enum
        return bytes([0x24, ordinal]), enum
    hit = (env.get("enum_by_name") or {}).get(low) if corpus else None
    if hit is None:
        return None, ""
    ordinal, enum = hit
    if not 0 <= ordinal <= 255:
        return None, enum
    return bytes([0x24, ordinal]), enum


_WIDE_TABLE = None


def _wide(data):
    """The length a node occupies in the compiler's **uncompacted** code array.

    The archive compacts an index operand to one, two or four bytes, and the loader widens it back to four on the
    way into memory (`uscript-dis.operand_spans`, measured on a RAM dump: `b8 2a` in the package is `a0 1e 26 01`
    in the guest). The `Context` and `Skip` words are byte distances the compiler computed in that wider array, so a
    node's wide length is its compact length plus the widening of every index it carries. Measured over the shipped
    corpus: the `Skip` word equals this reading on **4,857 of 4,857** nodes, and the `Context` word on every member
    kind except a bool behind a property import (see `_emit_member`).
    """
    global _WIDE_TABLE
    dis = _dis()
    if _WIDE_TABLE is None:
        _WIDE_TABLE = dis.Table()
    try:
        spans = dis._spans_with_op(data, _WIDE_TABLE)
    except dis.Refused as exc:
        raise Refused("a node whose code-array width the walk cannot measure (%s; %s)"
                      % (exc, data.hex()))
    return len(data) + sum(4 - (b - a) for a, b, k, _op in spans if k == "index")


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


def _element_at(expr):
    """The `[` position when the whole run is `head[index]`, or None.

    The closing bracket has to be the run's **last** token and its partner the depth-0 `[`, so `A[i].B` and
    `A[i] + 1` are not elements here -- the operator and the member chain are tried first, exactly as the
    compiler nests them.
    """
    if len(expr) < 4 or expr[-1] != ("op", "]"):
        return None
    depth = 0
    for i in range(len(expr) - 1, -1, -1):
        kind, val = expr[i]
        if kind != "op":
            continue
        if val in ")]}":
            depth += 1
        elif val in "([{":
            depth -= 1
            if depth == 0:
                return i if val == "[" else None
    return None


_ARRAY_MODS = ("export", "editinline", "const", "coerce", "out", "native", "array")


def _array_element(friendly):
    """`array<export editinline ScriptEvent>` -> `scriptevent`; "" when the artifact does not spell one."""
    f = (friendly or "").strip()
    if not (f.startswith("array<") and f.endswith(">")):
        return ""
    words = [w for w in f[6:-1].split() if w.lower() not in _ARRAY_MODS]
    return words[-1].lower() if words else ""


def _element_type(head, env):
    """`(token, element type)` for the array `head` names, or `(None, "")` when it is not an array read.

    The token is the record's own kind. A **dynamic** array is a `UArrayProperty` and writes `10`
    (`DynamicArrayElement`); every other indexed record writes `1A` (`ArrayElement`) -- `XUID` is a
    `UByteProperty` whose `XUID[12]` dimension lives in the class record, and `ServerSetXUID` writes
    `0F 1A 25 01 58 05 ...`. The children are the **index first** and the array read second
    (`AIMsgManager.GetReceivedMsg`'s `return msgReceived[Idx];` is `04 10 00 7a 57 01 01 7e 03 04 0b 47`:
    `10` with the local `Idx` in front of the property), and the node writes no `16` of its own -- the
    enclosing `Return`/`Let` carries on directly.

    The element type is the declared one: `array<T>`'s `T` for a dynamic array, and the record's own kind
    for a static one (a `UByteProperty` with a dimension is `byte` elements). A struct element keeps the
    artifact's friendly name, because that is what the cast table and the member walk key on.
    """
    head = _strip_parens(head)
    if not head:
        return None, ""
    if len(head) == 1 and head[0][0] in ("ident", "keyword"):
        nm = head[0][1].lower()
        if _record_kind(head, env) == "array":
            return 0x10, _array_element(_friendly(nm, env))
        declared = _declared_type(nm, env)
        if declared in ("", "array"):
            return None, ""
        return 0x1A, declared
    at = _dot_at(head)
    if at is not None and len(head[at + 1:]) == 1 and head[at + 1][0][0] in ("ident", "keyword"):
        hit = _member_record(head[:at], head[at + 1][0][1].lower(), env)
        if hit is None:
            return None, ""
        kind, friendly = hit[2], (hit[3] if len(hit) > 3 else "") or ""
        if kind == "array":
            return 0x10, _array_element(friendly)
        declared = friendly if kind == "struct" else kind
        return (0x1A, declared) if declared else (None, "")
    if _element_at(head) is not None:
        # a nested element (`A[B[i]][j]`): its elements are the innermost array's
        return _element_type(head[:_element_at(head)], env)
    return None, ""


def _emit_element(head, index, rettype, env):
    """`X[i]` -> `10`/`1A`, the index, the array read; then the element converts at its own site."""
    token, etype = _element_type(head, env)
    if token is None:
        raise Refused("an element of an array the artifacts do not type")
    arrb = emit_expr(head, "", env)[0]
    idxb = emit_expr(index, "", env)[0]
    node = bytes([token]) + idxb + arrb
    return _converted(node, etype, rettype), "element"


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


def _import_of(expr, env):
    """The negative index a name reads its record through, or None: `Outer` is import 14 in Engine.u."""
    expr = _strip_parens(expr)
    if len(expr) != 1 or expr[0][0] not in ("ident", "keyword"):
        return None
    nm = expr[0][1].lower()
    scope = env.get("scope") or {}
    for table in ("locals", "props"):
        hit = scope.get(table, {}).get(nm)
        if hit is not None:
            return hit[1] if hit[1] < 0 else None
    return None


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


def _is_outer(expr):
    """`Outer` -- the one receiver whose `Context` member does not widen (`skip` 5, not the member's width)."""
    expr = _strip_parens(expr)
    return len(expr) == 1 and expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "outer"


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
    `inventory/class_properties.csv` (`SquadDir` is a `Vector`), a **local's** in its own `local` statement
    (`local Vector Dir;` -- `env["localtypes"]`, which is per class and carries only names the class declares
    with one type); a local no statement spells stays `struct`, which refuses rather than guesses.
    """
    scope = env.get("scope") or {}
    ptype, is_local = "", False
    for table in ("locals", "props"):
        if nm in scope.get(table, {}):
            ptype = scope[table][nm][2]
            is_local = table == "locals"
            break
    if ptype != "struct":
        return ptype
    if is_local:
        # The property artifact has no row for a local, so a struct-typed local read as `struct` and every
        # operator over it was refused as a pair the table does not install: 78 bare operands (46 names) were
        # `StartTrace`, `Enemydir`, `TargetVector` -- `local Vector` in their own function.
        word = (env.get("localtypes") or {}).get(nm)
        if word:
            return word
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
            token, enum = _emit_enum(val, env, corpus=False)
            if token is not None:
                # An enum constant is a **`byte`** whatever enum names it: `NM_Client` is a `ByteConst`, and the
                # operator table installs its pairs against `byte`, not against the enum's own name. Returning
                # `enum` here read `byte == enetrole` (45 bodies) and `byte == ephysics` (12) as uninstalled pairs.
                return "byte"
            if low in (scope.get("consts") or {}):
                # A `const NAME = <int>;` is substituted by the compiler, so the site is an `int` site --
                # `MAX_CORRECT_MOVER < 5` is `Less_IntInt` whatever the const was written for.
                return "int"
            if _emit_enum(val, env)[0] is not None:
                return "byte"
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
    if (len(expr) == 2 and expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "class"
            and expr[1][0] == "quoted"):
        # `class'Pawn'` is an object reference: the same `class` a bare class name reads as, so a site that wants an
        # object takes it unconverted and one that wants a string casts it (`ObjectToString`).
        return "class"
    if _is_call(expr):
        low = expr[0][1].lower()
        if low in CAST_TYPES:
            return low
        if low == "arraycount":
            # `ArrayCount(X)` is a *compile-time* int: the compiler writes the declared dimension as a literal, so
            # the site is an int site and `i < ArrayCount(X)` is `Less_IntInt`, not an unknown-type pair.
            return "int"
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
    un = _unary_types(expr, env)
    if un is not None:
        # a one-operand node's result: `!bIsOn` is a bool, and this is what types it when the node sits *inside*
        # another operator -- `bInScope && !bHasZoomAnims` reads `bool && an unknown type` without it
        return un[2]
    at = _element_at(expr)
    if at is not None:
        # an element is its array's declared element type, which is what a cast around it and an operator
        # pair at its site both key on
        return _element_type(expr[:at], env)[1]
    at = _dot_at(expr)
    if at is not None:
        member = expr[at + 1:]
        if _is_call(member):
            # `A.F()` -- a **member call**: its return type is declared on the *receiver's* class, and a same-named
            # function on the body's own chain is a different record. `CurrentAction.WaitForPlayer()` returns a bool
            # declared on `ScriptedAction`, so reading the body's chain left it an unknown type and `A && B` never
            # reached `AndAnd_BoolBool` -- the largest family in the operator residue (518 rows, 311 right and 207
            # left). `_rettype_on` already refuses to fall back to the body's chain for the same reason (see its
            # note) and `_emit_member_call` joins through `_class_of`, so this is that join's type-level twin.
            rec = _class_of(expr[:at], env)
            if rec:
                return _rettype_on(rec, member[0][1], env)
            return ""
        if len(member) == 1 and member[0][0] in ("ident", "keyword"):
            nm = member[0][1].lower()
            obj = expr[:at]
            if nm == "length":
                # `PathList.Length` is the `37` length operator, which yields an **int** -- there is no property
                # record to look up, so the member chain read as an unknown type and `i < PathList.Length` (157
                # bodies) never reached `Less_IntInt`. The receiver's *record* kind is the test, as in
                # `_emit_member`; the declared type is the fallback for a chain (`Player.LocalInteractions`).
                if (_record_kind(obj, env) or _type_of(obj, env).lower()) in ("array", "string"):
                    return "int"
            # The member record is chosen by the receiver's *record kind*, exactly as `_emit_member` chooses
            # its node: `local Color C; C.R = R;` is a `color` by its own statement and a `StructMember` record
            # in the package, and the record is what names `R`. Testing the declared word here instead refused
            # 42 bodies the walk already modelled -- `Canvas.SetDrawColor` is the first of them.
            objkind = _record_kind(obj, env) or _type_of(obj, env).lower()
            hit = _member_record(obj, nm, env) if objkind != "struct" else _prop_by_name(nm, env)
            if hit is not None:
                if hit[2] == "struct" and len(hit) > 3 and hit[3]:
                    return hit[3]
                if hit[2] == "object" and len(hit) > 3 and (hit[3] or "").lower() in (env.get("class_names") or set()):
                    # the declared **class** of an object property, not the word `object`: `Controller.Pawn` is a
                    # `Pawn`, and a further link (`.bWaterVolume` on a `NewVolume`) needs the class, not the kind
                    return hit[3].lower()
                return hit[2]
    return ""


def _byte_literal(part, want, site, env):
    """`39 3a 24 <v>` for a literal the enclosing expression typed as `byte` and the operator widened to `int`.

    `UWindowHTMLTextArea.ParseColor` writes `C.R = 16 * GetHexDigit(...) + ...` as
    `39 3d <Add_IntInt> 90 39 3a 24 10 ...`: the `16` is a **ByteConst** behind `ByteToInt` (slot 0x3a), because the
    assignment's byte typed the literal before the int operator widened it -- not the `IntConstByte` the int
    operand alone takes. The byte type reaches the literal through the **operator tree**: the assignment types the
    outermost node (`+`), and a literal two operators in is still a byte.

    Three bounds are measured, not guessed, and each is a body this tool scores:
    - `PlayerProfile.SetDirty` writes `DirtyMask = DirtyMask | 1;` as `... 39 3a 01 48 11 26 16`: the **`1` is
      `IntOne`**, not a widened `ByteConst`, so 0 and 1 keep the compiler's dedicated opcodes.
    - `ParseColor`'s `Mid(S, 2, 1)` writes `2c 02 26` inside the same expression: a **call's argument** is typed by
      the parameter, so the byte context stops at a call (`emit_call` drops the flag).
    - `A = 255;` on a byte writes `39 3d 2c ff`: a literal at the assignment itself (site `byte`, no operator) is
      `emit_expr`'s case, and only **255** is the int form (254 and below are `ByteConst`; see the number branch).

    The **float** target is the same literal rule read one step further: `StargateHUD.SetIDColor` writes
    `Canvas.SetDrawColor(0, 160 * (IdentifyFadeTime / 3.0), 0)` as `39 3c 24 a0 ac ...` -- the argument's parameter
    is a `byte`, so the `160` is a **ByteConst** and the mixed pair widens it with `ByteToFloat` (slot 60), not the
    `IntConstByte` behind `IntToFloat` an int literal would take. The `255` of the `else` branch beside it writes
    `39 3f 2c ff` (`IntToFloat`, slot 63) because 255 is the one value the byte form does not carry.
    """
    if (want or "").lower() not in ("int", "float"):
        return None
    if (site or "").lower() != "byte" and not env.get("byte_root"):
        return None
    core = _strip_parens(part)
    if len(core) != 1 or core[0][0] != "number":
        return None
    v = _number(core[0][1])
    if isinstance(v, float):
        # A **float-spelled** literal keeps its float form: `StargateHUD.SetIDColor`'s
        # `160 * (IdentifyFadeTime / 3.0)` writes the `3.0` as `1e 00004040`, not as a `ByteConst 3` behind
        # `ByteToFloat` -- the byte context types an int literal, not a float one.
        return None
    # Widened to `int` the byte literal is a byte only while it fits a **signed** byte: `PlayerProfile.SetClean`
    # writes `DirtyMask | 254` as `9c 39 3a 01 48 11 2c fe 16` -- a plain `IntConstByte`, no `ByteToInt` -- while
    # `ParseColor`'s 16 is `39 3a 24 10`. Widened to `float` the form carries every value the byte form does:
    # 160 is `39 3c 24 a0`.
    if (want or "").lower() == "int":
        if not 2 <= v < 128:
            return None
    elif not 2 <= v < 255:
        return None
    slot = _cast_slots().get(("byte", (want or "").lower()))
    if slot is None:
        return None
    return bytes([0x39, slot, 0x24, v])


def _operand(part, want, rettype, env, inner):
    """One operand of a binary node, converted to the type the pair was looked up under, or None.

    The conversion is a `PrimitiveCast` written **around** the operand's own bytes, never a retyping of them:
    `RespawnTime < 10` is `39 3f 2c 0a` -- an `IntConstByte 10` behind `IntToFloat` -- not the `1e 00002041` a
    literal typed *as* a float takes, and `"a" $ RefCount` is `39 53 01 6e 08`.

    **The slot is keyed on the operand's declared type, not on the type the pair was looked up under.** A pair
    whose `byte` side is promoted to `int` reads `39 3a` (`ByteToInt`) and *the same* byte promoted to `float`
    reads `39 3c` (`ByteToFloat`) -- `Mover.FinishedClosing`'s `SoundVolume / 255.0` is `39 3c 01 55`, not the
    `39 3f` the promoted `int` would give. A `$`/`@` operand follows the same rule and is where it was found:
    `Role` is a `byte` and `Pawn.Fire` writes `39 52` (`ByteToString`, slot 82), not `39 53` (`IntToString`).
    A one-letter promotion (`class` -> `object`) is the fallback key, because `self $ "x"` is `39 56` and a class
    name is not the word `object`.

    One shape is *not* this rule and stays a named measurement: an `int` literal promoted to `float` is folded
    into a constant by the compiler in some sites (`return (10 + 5 * FRand());` holds `1e 00002041`) and cast in
    others (`39 3f 2c 05` for the `5` of `5 * FRand()`, `39 3f 2c 0a` for `RespawnTime < 10`). Neither the
    operator nor the site decides it, so it is not modelled.
    """
    own = _type_of(part, env).lower()
    src = own
    if not want or src == want:
        return None
    slot = _cast_slots().get((src, want))
    if slot is None:
        # A class-typed operand converts as an **object** (`self $ "x"` is `39 56`), and a class name is not the
        # word `object`, so the promoted type is the fallback key rather than the declared one.
        alt = _numeric(own, env) if own else ""
        slot = _cast_slots().get((alt, want)) if alt != src else None
    if slot is None:
        return None
    body = emit_expr(part, own, env)[0] if own else emit_expr(part, "", env)[0]
    return bytes([0x39, slot]) + body


def _float_literal(part, want, site=""):
    """`1e <f32>` for an **int literal** the compiler folded into a float constant at the root of an expression.

    The fold is positional, not a general rule: `TransientAmbientCreature.MaxHiddenTime`'s
    `return (10 + 5 * FRand());` is `04 ae 1e 00002041 ab 39 3f 2c 05 c3` -- the `10`, the **left operand of the
    expression's root**, is a `FloatConst`, while the `5` of `5 * FRand()`, a *nested* node, is an
    `IntConstByte` behind `IntToFloat`. Five more bodies are the same shape (`15 + 5 * FRand()`,
    `5 + 5 * FRand()` twice, `3 + 3 * FRand()` in `FishBase.GetSleepTime`), and the right-hand position casts
    instead: `RespawnTime < 10` is `39 3f 2c 0a`. A parenthesised operand is a root again -- `Beacons.DrawBeacon`
    folds the `640` of `(640 / RangeToTarget)` two nodes in, and `StargatePawn.DrawClassIcon` folds the `96` of
    `96 * scale` at the top of an argument.

    This was a recorded negative ("folding is not decided by the operator, the literal's position or the site") and
    the position is what was missing: folding every arithmetic literal fixed 5 bodies and broke 69. The **byte**
    site is the other bound: `StargatePawn.ShieldFlashOn` writes `255 * ...` at a `byte` parameter as
    `39 3f 2c ff` (`IntToFloat`), not as a `FloatConst`, so a byte-typed site casts.

    The position is the **leading** one, and the site must be a `float` one. A leading literal keeps that position
    down the chain of left operands -- `(640 / RangeToTarget) * 85 / ...` folds the 640 two nodes in -- while an
    operand to the right of an operator casts (`5` of `5 * FRand()`), a **compound assignment's** value casts
    (`Alpha -= (255 * VisibleTime) * Delta` is `39 3f 2c ff`), and an `int` site casts (`Min(0.25 * NewPing, 250)`
    keeps `39 3f 2c fa`). Eight bodies are the evidence: five the earlier parenthesised rule had lost
    (`PlayerController.ServerUpdatePing`, `Beacons.VisibleForABit.Tick`, `BotEnemy.DefendMelee`, both
    `CheckRange`) and three it wrote a `ByteToFloat(ByteConst 3)` into.
    """
    if (want or "").lower() != "float":
        return None
    # The fold is a **float site's** rule: at an `int` site the compiler keeps the literal in its int form and
    # casts it (`250` in `Min(0.25 * NewPing, 250)` is `39 3f 2c fa`), and a byte site casts too.
    if (site or "").lower() != "float":
        return None
    core = _strip_parens(part)
    neg = False
    if len(core) == 2 and core[0] == ("op", "-") and core[1][0] == "number":
        core, neg = [core[1]], True
    if len(core) != 1 or core[0][0] != "number":
        return None
    v = _number(core[0][1])
    if isinstance(v, float):
        return None                    # a source float literal is already written as `1e` by `emit_expr`
    return bytes([0x1E]) + struct.pack("<f", -float(v) if neg else float(v))


def _paren_wrapped(expr):
    """True when the **whole** token run is one parenthesised group -- a new expression root for the fold rule."""
    if len(expr) < 3 or expr[0] != ("op", "(") or expr[-1] != ("op", ")"):
        return False
    depth = 0
    for i, (kind, val) in enumerate(expr):
        if kind == "op" and val in "([{":
            depth += 1
        elif kind == "op" and val in ")]}":
            depth -= 1
            if depth == 0 and i != len(expr) - 1:
                return False
    return depth == 0


def _emit_operator(expr, rettype, env, root=False):
    """`A op B` -> `<token> A B 16`: the operator's token comes **first**, its operands after it.

    Measured on `PlayerProfile.SetDirty` (`DirtyMask = DirtyMask | 1;` -> `0f 01 48 11 | 39 3d | 9c 39 3a 01 48 11 26
    16`): the token is the native's own opcode (`Or_IntInt` 158), the left operand comes before the right, and the
    node ends on `16` (EndFunctionParms) like any other call. `PteroBoss.TakeDamage` (`AddVelocity(Momentum / Mass)`)
    is the same shape one level in, with `d6` (`Divide_VectorFloat` 214).
    """
    op = _operator_types(expr, env, rettype, root)
    if op is None:
        raise Refused("a compound expression, which needs the statement walk")
    sym, t1, t2, result, token, at = op
    if not token:
        left, right = expr[:at], expr[at + 1:]
        if os.environ.get("SG1_DBG_OP"):
            raise Refused("an operator the image's table does not install (%s %s %s) <<%s | %s>>"
                          % (t1 or "an unknown type", sym, t2 or "an unknown type",
                             " ".join(v for _k, v in left), " ".join(v for _k, v in right)))
        raise Refused("an operator the image's table does not install (%s %s %s)"
                      % (t1 or "an unknown type", sym, t2 or "an unknown type"))
    left, right = expr[:at], expr[at + 1:]
    if _is_literal(left) and _is_literal(right):
        raise Refused("an operator whose two operands are literals, which the compiler folds")
    if 0x70 <= token <= 0xFF:
        head = bytes([token])
    elif token >= 0x100:
        head = bytes([0x60 + (token >> 8), token & 0xFF])
    else:
        raise Refused("an operator token below 0x70 (%d), which collides with a real opcode" % token)
    if sym in ("&&", "||", "^^"):
        # The right operand of a short-circuit operator is wrapped in `Skip` (`18 <uint16> <expr>`), and the word is
        # a distance in the compiler's **uncompacted** code array: from the first byte of the right operand to the
        # end of the enclosing node. `Pawn.InGodMode` writes 36 for `(Controller != None) && (...)`: the right
        # operand is 25 compact bytes plus 10 for its four widened indices, and the node's own `16` makes 36. The
        # rule holds on all 4,857 `Skip` nodes the corpus carries (0 differences), so it is computed, not guessed.
        leftb = (_operand(left, t1, rettype, env, env) or _byte_literal(left, t1, rettype, env)
                 or emit_expr(left, t1, env)[0])
        rightb = (_operand(right, t2, rettype, env, env) or _byte_literal(right, t2, rettype, env)
                  or emit_expr(right, t2, env)[0])
        node = (head + leftb + bytes([0x18]) + struct.pack("<H", _wide(rightb) + 1) + rightb + b"\x16")
        return _converted(node, result, rettype), "short-circuit"
    # The byte context reaches a literal through the operator tree: the assignment types the outermost node, and a
    # literal two operators in is still a byte (`ParseColor`). It stops at a call, whose arguments are typed by the
    # callee's parameters -- `emit_call` drops the flag.
    inner = dict(env, byte_root=True) if (rettype or "").lower() == "byte" else env
    # A byte-typed site types a literal before the cast table is asked (`39 3c 24 a0`), and an int literal at a
    # `float` site folds into a `FloatConst` in exactly two measured positions (see `_float_literal`); both are
    # tried before `_operand`, whose form for a literal is the cast.
    # The **leading** operand of the expression keeps the root, so `(640 / RangeToTarget) * 85` folds the 640 two
    # nodes in; an operand to the right of an operator does not (`5` of `10 + 5 * FRand()` casts).
    left_env = dict(inner, _root=True) if root else inner
    right_env = inner
    node = (head + (_byte_literal(left, t1, rettype, inner)
                    or (_float_literal(left, t1, rettype) if root else None)
                    or _operand(left, t1, rettype, env, left_env)
                    or emit_expr(left, t1, left_env)[0])
            + (_byte_literal(right, t2, rettype, inner)
               or _operand(right, t2, rettype, env, right_env)
               or emit_expr(right, t2, right_env)[0]) + b"\x16")
    return _converted(node, result, rettype), "operator"


def _unary_types(expr, env):
    """`(symbol, operand type, result type, token)` for a leading one-operand operator, or None.

    A unary operator is *not* an operator at depth 0 with two operands: `_operator_types` skips position 0 (a sign,
    or a lone symbol), so `!bIsOn` and `-Health` never reached the pair table. The declaration the token comes from is
    the `preoperator` row (`native(129) static final preoperator bool ! ( bool A )`), keyed by the symbol and the
    operand's declared type -- `-` is `Subtract_PreInt` (143), `Subtract_PreFloat` (169) and `Subtract_PreVector`
    (211), so the operand's type is part of the key here too.
    """
    if len(expr) < 2 or expr[0][0] != "op":
        return None
    sym = expr[0][1]
    if not any(k[0] == sym for k in UNARY):
        return None
    t = _type_of(expr[1:], env).lower()
    for cand in (_numeric(t), t):
        hit = UNARY.get((sym, cand))
        if hit is not None:
            return sym, cand, hit[1], hit[0]
    return sym, t, "", None


def _emit_unary(expr, rettype, env):
    """`! x` -> `81 <x> 16`: the pre-operator's own opcode, its operand, then `16` like any other call.

    Measured on the shipped bytes, three bodies: `bIsOn = ! bIsOn;` is `14 2d 01 03 | 81 2d 01 03 16 | 04 0b 47` --
    the token is the native's own opcode (129, in the 0x70..0xff range the exec natives use), the operand is written
    exactly as a read of the same variable, and the node closes on `16`. As a short-circuit operand the node's width
    is what the `Skip` word counts: `bSquadBeingAlerted && ! bSquadAlertComplete` writes `18 09 00` around eight wide
    bytes of operand plus the `16`, the same `_wide(node) + 1` rule the binary nodes use.

    An operand the compiler folds (a literal) is refused by name, as with the two-operand case.
    """
    hit = _unary_types(expr, env)
    if hit is None:
        return None
    sym, t, result, token = hit
    if token is None:
        raise Refused("a unary operator the image's table does not install (%s %s)"
                      % (t or "an unknown type", sym))
    operand = expr[1:]
    if _is_literal(operand):
        raise Refused("a unary operator whose operand is a literal, which the compiler folds")
    if 0x70 <= token <= 0xFF:
        head = bytes([token])
    elif token >= 0x100:
        head = bytes([0x60 + (token >> 8), token & 0xFF])
    else:
        raise Refused("a unary operator token below 0x70 (%d), which collides with a real opcode" % token)
    node = head + emit_expr(operand, result, env)[0] + b"\x16"
    return _converted(node, result, rettype), "unary"


def _emit_cast(name, inner, env, rettype=""):
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
        # `X = Int(Root.MouseX);` in a float slot is `39 3f 39 44 19 ...`: the explicit cast is the *inner*
        # node and the site's own conversion wraps it, so a cast's result is converted like any other value.
        return (_converted(bytes([0x39, slot]) + emit_expr(inner, src, env)[0], low, rettype),
                "primitive-cast")
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


def _rettype_on(cls, name, env):
    """The declared return type of `name` as declared on `cls`'s chain (or, with no class, on the body's chain)."""
    low = name.lower()
    rets = env.get("rets") or {}
    scope = env.get("scope") or {}
    if cls:
        for _p, c in _ancestors(scope.get("chain") or {}, scope.get("pack") or "", cls.lower()):
            # Membership, not truthiness: an `event` declares no return type and its empty string is the answer.
            # `Controller.ServerXControllerEvent(...)` in GUIComponent returns void (`BaseGUIController`'s event),
            # and a falsy test fell through to the body's own chain and found GUIComponent's `bool` override --
            # writing value size 4 where the package has 0.
            if (c, low) in rets:
                return (rets[(c, low)] or "").lower()
        # No fallback to the body's own chain: a member call names the *receiver's* declaration, and a same-named
        # function on the body is a different record with its own return type. `GUIComponent.ServerXControllerEvent`
        # calls `Controller.ServerXControllerEvent(...)` (void) while GUIComponent's own override beside it returns
        # `bool`; the fallback read the body's and wrote value size 4 where the package has 0.
        return ""
    for c in env.get("order") or []:
        hit = rets.get((c, low))
        if hit:
            return hit.lower()
    return ""


def _params_on(cls, name, env):
    """`[(type, name, optional)]` for `name` declared on `cls`'s chain -- a member call's callee, not the body's."""
    low = name.lower()
    params = env.get("params") or {}
    scope = env.get("scope") or {}
    if cls:
        for _p, c in _ancestors(scope.get("chain") or {}, scope.get("pack") or "", cls.lower()):
            if (c, low) in params:
                return params[(c, low)]
    for c in env.get("order") or []:
        hit = params.get((c, low))
        if hit:
            return hit
    return None


def _func_on(cls, name, env):
    """`("native", token)` / `("final", index)` / `("virtual", name index)` for a callee on the receiver's chain.

    A member call's callee is a function of the *receiver's* class, which is often not on the body's chain:
    `Controller.RateWeapon` calls `w.GetAIRating()` where `w` is a `Weapon`. The token rules are the ones the
    class-scope lookup already measured; only the declaration's owner changes.
    """
    low = name.lower()
    scope = env.get("scope") or {}
    chain = scope.get("chain") or {}
    pack = scope.get("pack") or ""
    natives = env.get("natives") or {}
    funcs = env.get("funcs_all") or {}
    imports = env.get("imports") or {}
    finals = env.get("final_of") or {}
    for p, c in _ancestors(chain, pack, cls.lower()):
        hit = natives.get((p, c, low))
        if hit is not None:
            token, flags = hit
            if token:
                return ("native", token)
            if "0x1:Final" in flags:
                own = [x for x in funcs.get(low, []) if x[1] == c]
                if own:
                    return ("final", own[0][0])
                imp = [x for x in imports.get(low, []) if x[1] == "Function"]
                if len(imp) == 1:
                    return ("final", imp[0][0])
                continue
            break
        own = [x for x in funcs.get(low, []) if x[1] == c]
        if own:
            if low in finals.get(c, ()):
                return ("final", own[0][0])
            return ("virtual", (env.get("names") or {}).get(low))
        if low in finals.get(c, ()):
            continue
    imp = [x for x in imports.get(low, []) if x[1] == "Function"]
    if len(imp) == 1:
        # An import's owner class is not in this package's export table, so the `final` flag is read off the
        # receiver's *chain* in the text: `ProfileList.ItemCount()` is `1c d9 03` -- a FinalFunction naming the
        # import -- because `ItemCount` is `final` on `GUIListBox`, the chain's GUI ancestor.
        if any(low in finals.get(c, ()) for _p, c in _ancestors(chain, pack, cls.lower())):
            return ("final", imp[0][0])
        return ("virtual", (env.get("names") or {}).get(low))
    return None


def _class_of(expr, env):
    """The class a receiver expression holds, named the way the package names classes, or "".

    Three sources, all artifacts rather than guesses: a parameter's or local's declared type (`Pawn P` -> `pawn`),
    a property's declared class (`class_properties.csv`: `GUIComboBox.List` is a `GUIListBase`), and a call's
    declared return type (`function PlayerController GetPlayerController(...)`). This is what lets a receiver chain
    be resolved one link at a time -- `H.Level.TimeSeconds` is a member of `LevelInfo`, which is what `HUD.Level`
    is declared as.
    """
    expr = _strip_parens(expr)
    if not expr:
        return ""
    class_names = env.get("class_names") or set()
    if len(expr) == 1 and expr[0][0] in ("ident", "keyword"):
        low = expr[0][1].lower()
        for table in ("paramtypes", "localtypes", "proptypes"):
            t = (env.get(table) or {}).get(low, "")
            if t and t in class_names:
                return t
        if low == "self":
            own = (env.get("scope") or {}).get("cls") or ""
            return own if own in class_names else ""
        hit = _prop_by_name(low, env)
        if hit is not None and len(hit) > 3 and (hit[3] or "") in class_names:
            return hit[3]
        return ""
    if _is_call(expr):
        # `TransientAmbientPawn(Pawn).VerifyLastRenderTime()`: the receiver is a cast, and a cast names the class
        # it casts to -- the same `classrefs` rule `_emit_cast` uses for `DynamicCast`.
        if _cast_kind(expr[0][1], env) == "object" and expr[0][1].lower() in class_names:
            return expr[0][1].lower()
        t = _rettype_on("", expr[0][1], env)
        return t if t in class_names else ""
    at = _element_at(expr)
    if at is not None:
        # `GlobalEvents[size - 1].AddCondition(con)`: the receiver is an element, and the element type is what
        # the member call resolves against
        t = _element_type(expr[:at], env)[1]
        return t if t in class_names else ""
    at = _dot_at(expr)
    if at is not None:
        member = expr[at + 1:]
        if len(member) == 1 and member[0][0] in ("ident", "keyword"):
            hit = _member_record(expr[:at], member[0][1].lower(), env)
            if hit is not None and len(hit) > 3 and (hit[3] or "") in class_names:
                return hit[3]
    return ""


def _member_record(obj, nm, env):
    """The property record of `nm` on the *receiver's* class, or None.

    The receiver's declared class comes from `inventory/class_properties.csv` (`GUIComboBox.List` is a `GUIListBase`),
    and the record is then the one that class or its nearest ancestor declares -- the same walk `_scope` does for the
    body, one level out. A member the package declares on more than one class in the chain is refused rather than
    guessed: a wrong index emits plausible bytes.
    """
    scope = env["scope"] or {}
    pack = (scope.get("pack") or "").lower()
    classes = scope.get("objclass") or {}
    target = ""
    if len(obj) == 1 and obj[0][0] in ("ident", "keyword"):
        for anc in env.get("order") or []:
            target = classes.get((anc, obj[0][1].lower()), "")
            if target:
                break
    if not target:
        # The receiver is not a property of this package's class table: it is a parameter or a local named by the
        # declaration (`Pawn P` in `Controller.RateWeapon` is `w`), a call whose return type the text declares
        # (`w.GetAIRating()`), or a chain link whose own class the artifact names.
        target = _class_of(obj, env)
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
    # `props_all` holds this package's own exports, so a member of a class declared in *another* package has no
    # record there -- `Pawn.Acceleration` in AmbientCreatures is Engine's. The compiler still wrote an index for
    # it, a **negative import**, and the import table names it; only a single candidate of that name is used, the
    # same rule `_scope` applies to a property on another chain (`InteractionHands.EndState` writes -508).
    imp = [x for x in (scope.get("imports") or {}).get(nm, []) if x[1] in PROPERTY_CLASSES]
    if len(imp) == 1:
        kind = PROP_TYPE.get(imp[0][1], "")
        friendly = ""
        for p, c in _ancestors(chain, pack, target):
            friendly = (scope.get("friendly") or {}).get((p, c, nm), "")
            if friendly:
                break
        return (INSTANCE_TOKEN, imp[0][0], kind, friendly)
    return None


RETURN_SIZE = dict(PROP_SIZE, rotator=12)


def _emit_member_call(obj, member, rettype, env):
    """`A.F(args)` -> `19 <A> <skip> <size> <F>`: the call is the **member** of a `Context`.

    The word is the call's own width in the compiler's code array (`_wide`), not a constant: `List.ItemCount()` is
    `19 01 2b 06 00 04 1b 0a 16` and `P.BeyondViewDistance(SpawnLocation, 0)` is `19 00 7e 7e 10 00 04 1b 43 08 00
    7f 7e 1e 00000000 16`. The size byte is the *value* the call yields -- 4 for an object, an int or a bool, 0 for a
    string, 12 for a rotator (`AlkeshCannonAttachment(...).GetBoneRotation(...)` writes `0c`). When the object is a
    property **import** (`Outer`, `01 8e`) the corpus writes 5 whatever the member is (measured over `Engine.u`:
    `Outer.Log("...")` is `19 01 8e 05 00 04 1b 0f ...`), so that shape keeps the measured constant.
    """
    objb = emit_expr(obj, "", env)[0]
    cls = _class_of(obj, env)
    if not cls:
        # The receiver's class is the join. Falling back to the body's own chain here wrote a `VirtualFunction`
        # for `ProfileList.ItemCount()` -- resolved to `ProfilePanel.ItemCount`, a *different* function of the same
        # name two classes away -- where the package writes a `FinalFunction` naming the receiver's own record.
        raise Refused("a call member whose receiver's class the artifacts do not name")
    memb = emit_call(member, env, receiver=cls)
    # The word is the call's own width in the compiler's code array, whatever the receiver is. It was held at 5 for
    # an import receiver (`Outer.Log("...")`), but that shape's call *is* five wide: `19 01 8e 05 00 00 1b 0f ...`, and
    # the four bodies whose receiver is a property read with a longer call write 0x03, 0x0b, 0x0b and 0x10 --
    # `Pawn.NeedToTurn(targ)` is `19 01 8d 0b 00 04 1b 59 02 00 48 0a 16`, and `_wide` of that call is 11.
    skip = _wide(memb)
    rtype = _rettype_on(cls or "", member[0][1], env)
    if rtype in (env.get("class_names") or set()):
        rtype = "object"
    if rtype not in RETURN_SIZE:
        raise Refused("a call member returning %s, whose value size this walk does not carry"
                      % (rtype or "an unknown type"))
    node = bytes([0x19]) + objb + struct.pack("<H", skip) + bytes([RETURN_SIZE[rtype]]) + memb
    return _converted(node, rtype, rettype), "call-member"


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
        if _is_call(member):
            # `A.F(args)`: a call member is a `Context` like a property member, but its word is the call's width in
            # the compiler's code array rather than a constant (see `_emit_member_call`).
            return _emit_member_call(obj, member, rettype, env)
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
            if ptype != "struct":
                raise Refused("a %s default member, whose value size this walk does not carry"
                              % (ptype or "typeless"))
            # A struct default is the record alone: `return Default.DrawColor;` is `04 02 44 44` -- no size byte
            # and no `Context`, because the value is copied straight out of the default object.
            return bytes([0x02]) + write_index(index), "default-struct"
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
    size = PROP_SIZE.get(ptype)
    if size is None and ptype == "struct":
        # The record is a `StructProperty`; the artifact's declared type names the struct, and only a struct whose
        # size the corpus writes is modelled (`Pawn.Location` -> 12). A struct name the table does not carry keeps
        # the refusal, and the message names it so the residue says which one.
        size = STRUCT_SIZE.get((friendly or "").lower())
        if size is None:
            raise Refused("a struct member declared %s, whose value size this walk does not carry"
                          % (friendly or "without a friendly type"))
    if size is None:
        raise Refused("a %s member, whose value size this walk does not carry" % (ptype or "typeless"))
    if ptype == "bool":
        # A bool member is `19 <obj> <skip> 04 2d 01 <index>`: the size byte is 4 like every other pointer-sized
        # member, and the read is a `BoolVariable` around the property. The skip word is the *member's* width in
        # the compiler's uncompacted code array (`_wide`), which is 6 for `2d 01 <index>` whatever width the index
        # was compacted to -- 2,212 of the corpus's 2,272 such nodes. The other 60 are all `Outer.<bool>` and write
        # 5 (the object is a property import, `01 8e`, and the member read does not widen): that word is measured,
        # not derived, so it stays a constant for that shape only.
        skip = 5 if _is_outer(obj) else _wide(bytes([0x2D, INSTANCE_TOKEN]) + write_index(index))
        read = bytes([0x2D, INSTANCE_TOKEN]) + write_index(index)
        node = (bytes([0x19]) + emit_expr(obj, "", env)[0] + struct.pack("<H", skip) + bytes([4]) + read)
        return _converted(node, "bool", rettype), "bool-context"
    read = bytes([INSTANCE_TOKEN]) + write_index(index)
    node = (bytes([0x19]) + emit_expr(obj, "", env)[0] + struct.pack("<H", CONTEXT_SKIP)
            + bytes([size]) + read)
    # the cast sits *outside* the node: `return w.Default.AutoSwitchPriority;` in a float function is
    # `04 39 3f 19 ...` -- IntToFloat, then the Context
    return _converted(node, ptype, rettype), "context"


def _incdec(expr, env):
    """`x++`, `++x`, `x--`, `--x` -> the post/pre operator's own opcode, the read, `16`.

    The four forms are four *different* natives, not one with a flag: `AddAdd_Int` (0xa5) is postfix on an `int`,
    `AddAdd_PreInt` (0xa3) prefix, and the `byte` pair is `AddAdd_Byte` (0x8b) / `AddAdd_PreByte` (0x89);
    `SubtractSubtract_*` sit next to each (0xa6 / 0xa4 / 0x8c / 0x8a). The operand is written exactly as a read of
    the same variable, and the node ends on `16` like any other call -- `Actor.UpdatePrecacheMaterials`'s
    `i ++` is `a5 00 5a 21 <idx> 16` and the `for` header's increment is the same node. A `float` increment has no
    installed operator in this build, so it is refused by name rather than given a token.
    """
    if len(expr) != 2:
        return None
    # (postfix, prefix) per operand type: the four forms are four opcodes.
    syms = {"++": {"byte": (0x8B, 0x89), "int": (0xA5, 0xA3)},
            "--": {"byte": (0x8C, 0x8A), "int": (0xA6, 0xA4)}}
    if expr[0][0] == "op" and expr[0][1] in syms and expr[1][0] in ("ident", "keyword"):
        read, prefix, sym = expr[1], True, expr[0][1]        # `++ x` -- the operator is in front: the *pre* form
    elif expr[1][0] == "op" and expr[1][1] in syms and expr[0][0] in ("ident", "keyword"):
        read, prefix, sym = expr[0], False, expr[1][1]       # `x ++` -- the operator follows: the post form
    else:
        return None
    t = _type_of([read], env).lower()
    pair = syms[sym].get(t)
    token = pair[1 if prefix else 0] if pair else None
    if token is None:
        raise Refused("a %s on a %s operand, which no installed operator names" % (sym, t or "typeless"))
    body = _emit_var(read[1], env, "")
    if body is None:
        raise Refused("a %s on a name neither a local nor a property of the class chain" % sym)
    return bytes([token]) + body + b"\x16", "incdec"


def emit_expr(expr, rettype, env):
    """The bytes of one expression, or Refused with the reason."""
    names = env["names"]
    # `root` is the expression the compiler starts on -- a statement's value, an argument, a condition -- and it is
    # what decides whether an int literal at a `float` site is **folded** into a `FloatConst` or left behind an
    # `IntToFloat` cast (see `_float_literal`). Every nested operand clears it; a parenthesised operand sets it
    # again, because a parenthesised group is its own expression.
    root = env.get("_root", True)
    env = dict(env, _root=False)
    expr = _strip_parens(expr)
    if not expr:
        return bytes([0x0B]), "nothing"                  # Return; -> Return Nothing
    if len(expr) == 2 and expr[0] == ("op", "-") and expr[1][0] == "number":
        expr = [("number", "-" + expr[1][1])]
    if len(expr) != 1:
        inc = _incdec(expr, env)
        if inc is not None:
            return inc
        if _operator_types(expr, env) is not None:
            # the loosest depth-0 operator is the expression's root, so it is tried before a call or a member:
            # `Foo(1) + 2` is an `Add_IntInt`, not a call
            return _emit_operator(expr, rettype, env, root)
        un = _emit_unary(expr, rettype, env)
        if un is not None:
            # a one-operand node: `!bIsOn`, `-Health`. The pair table never sees it -- its operator sits at
            # position 0, which `_operator_types` skips as a sign -- so it is tried before the call and member
            # shapes rather than after them.
            return un
        at = _element_at(expr)
        if at is not None:
            # `X[i]`: the element node is read as `10`/`1A` + index + array, and it is tried before the
            # call and member shapes because an indexed read is neither.
            return _emit_element(expr[:at], expr[at + 1:-1], rettype, env)
        if expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "vect":
            return _emit_vector(expr), "vector"
        if (len(expr) == 2 and expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "class"
                and expr[1][0] == "quoted"):
            # `class'Pawn'` -- the class name in single quotes -- is an object reference and not a name constant;
            # `_emit_class_literal` reads it through the same table a `DynamicCast` uses.
            return _emit_class_literal(expr[1][1], env), "class-literal"
        if _is_call(expr) and _cast_kind(expr[0][1], env):
            return _emit_cast(expr[0][1], expr[2:-1], env, rettype)
        if _is_call(expr) and expr[0][1].lower() == "arraycount":
            # `ArrayCount(X)` is not a call: the compiler writes X's declared dimension as an int literal, and the
            # value converts at its site like any int. `PlayerController.SetProgressMessage`'s
            # `Index < ArrayCount(ProgressMessage)` is `... 2c 04 16` for `var string ProgressMessage[4];`.
            return _converted(_int_bytes(_array_count(expr, env)), "int", rettype), "array-count"
        if _is_call(expr):
            # The value a call yields is converted at *its* site, not inside the callee: `Adrenaline = Clamp(...)`
            # in a float slot is `0f <read> 39 3f fb ...` -- IntToFloat around the Clamp, because Clamp returns an
            # `int` -- and `Insert(Asc(...))` writes `39 3d` (IntToByte) around the `Asc`, whose result is an int.
            node = emit_call(expr, env)
            return _converted(node, _type_of(expr, env), rettype), "call-value"
        if _dot_at(expr) is not None:
            return _emit_member(expr, rettype, env)
        raise Refused("a compound expression, which needs the statement walk")
    kind, val = expr[0]
    if kind in ("ident", "keyword") and val.lower() == "class":
        # The *spelling* decides, and the two are different nodes in this build: lowercase `class` compiles to
        # `Self` (`GetItemName(string(class))` and `GetItemName(string(self))` are the same seven bytes,
        # `1b 31 39 56 17 16`, in `Actor.GetItemName`, `Pickup` and `Pawn`), while `Class` is the property
        # `Object` declares (`var native const editconst class Class;`) and compiles to its record:
        # `GameReplicationInfo.GameClass = string(Class);` writes `39 56 01 9e` -- `ObjectToString` of the import
        # `Class` -- and `Item.InventoryType == Class` writes `01 9e 16`. Two bodies each way, and the source the
        # emitter reads is the package's own `ScriptText`, so the spelling is the compiler's input.
        read = _emit_var(val, env, rettype)
        if read is not None:
            return read, "class-property"
        return bytes([0x17]), "self-class"
    if kind in ("ident", "keyword") and val.lower() not in ("true", "false", "none", "self"):
        read = _emit_var(val, env, rettype)
        if read is not None:
            return read, "variable"
        token, enum = _emit_enum(val, env, corpus=False)
        if token is None:
            value = (env["scope"].get("consts") or {}).get(val.lower())
            if value is not None:
                # A `const NAME = <int>;` is a **compile-time literal**: the compiler substitutes the value and
                # the site encodes it exactly as the number written there would be, which is why this re-enters
                # the literal path with the same site type and the same `root` position (an int const at the
                # root of a float expression folds like a written literal does). Of the 1,414 operator sites
                # with a bare unknown operand, 73 were `NUMTEAMS`, `NUM_PIECES`, `MAX_CORRECT_MOVER` -- consts
                # the emitter never consulted, though `_scope` already builds the table for `ArrayCount`.
                node, _shape = emit_expr([("number", str(value))], rettype, dict(env, _root=root))
                return node, "const-literal"
            token, enum = _emit_enum(val, env)
        if token is not None:
            # An enum constant is a byte and the compiler writes ByteConst there; passed where neither a byte, an
            # int nor its own enum is the declared type, the token would be a guess, so it is refused by name.
            want = (rettype or "").lower()
            if want == "int":
                # In an `int` site the byte is **widened**, not retyped: `Physics != PHYS_Falling` is
                # `9a 39 3a 01 28 39 3a 24 02 16` -- `ByteToInt` (slot 58) in front of the `24 <ordinal>`, the same
                # wrapper a `byte` *property* gets from the same pair lookup.
                return _converted(token, "byte", "int"), "enum-const"
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
            return _int_bytes(v), "int-const"
        if t == "byte":
            if not 0 <= v < 256:
                raise Refused("a byte literal out of range (%s)" % val)
            if v < 255:
                return bytes([0x24, int(v)]), "byte-const"
            # The boundary is **255**, not 127. `EX_ByteConst` carries every value up to 254 and only 255 takes the
            # *int* form behind an `IntToByte`: a census of all 18 packages finds **3,357** `ByteConst` nodes, 30 of
            # them at 128 or above (128, 137, 150, 160, 192, 200..254 -- `SayMessagePlus.RenderComplexMessage`'s
            # `SetDrawColor(0,128,0)` is `1c dc 01 24 00 24 80 24 00`), and **389** nodes whose operand is 255 in
            # the int form (`A = 255;` in Canvas.MakeColor and `FindControllerPortIndex(255)` are `39 3d 2c ff`).
            # The earlier note's "127 is the last value the byte form carries" was measured on six sites that all
            # sat below 128 except the 255 ones, so it recorded the 255 case as a 127 threshold. One 128 in the int
            # form exists (`PlayerController.CallServerMove`, a body this walk refuses) and is not explained here.
            slot = _cast_slots().get(("int", "byte"))
            if slot is None:
                raise Refused("a byte literal of %d, whose IntToByte slot the image's table does not install" % v)
            return bytes([0x39, slot, 0x2C, int(v)]), "byte-const-cast"
        if t == "float":
            return bytes([0x1E]) + struct.pack("<f", float(v)), "float-const"
        raise Refused("a numeric literal in a %s function" % (rettype or "typeless"))
    if kind == "quoted":
        # UnrealScript spells a *name* with single quotes and a string with double ones, and this build follows it:
        # `Log('STOP Bot being electrocuted')` in BotEnemy's state is `39 57 21 75 36` -- `NameToString` of a
        # NameConst -- while `Log("Leaving ...")` beside it is a StringConst. The declared return type does not
        # decide: the literal's own spelling does, and the site's coercion (name -> string) is `_converted`'s.
        text = _literal_text(val[1:-1])
        idx = 0 if not text else (env.get("names") or {}).get(text.lower())
        if idx is None:
            raise Refused("a name literal the package does not carry (%d chars)" % len(text))
        return _converted(bytes([0x21]) + write_index(idx), "name", t), "name-const"
    if kind == "string":
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


def _emit_class_literal(raw, env):
    """`class'Pawn'` -> `ObjectConst` and the class's own **object reference** (a compact index, like a cast's).

    The literal is not a string or a name: `Pickup.CheckTouching`'s `foreach TouchingActors(class'Pawn', P)` writes
    `2f 61 33 20 01 00 70 3d 16`, and `20 01` is `ObjectConst` of Engine.u's export 1, which is `Pawn`. The index is
    the same `classrefs` table a `DynamicCast` reads, so a class the body's package only imports is written as the
    negative import. A name the package's tables do not carry is refused rather than guessed -- a wrong index here
    is a body that reads as measured.
    """
    name = _literal_text(raw[1:-1])
    ref = (env.get("scope") or {}).get("classrefs", {}).get(name.lower())
    if ref is None:
        raise Refused("a class literal whose class the package's tables do not name")
    return bytes([0x20]) + write_index(ref)


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


def _callee_params(name, env, receiver=None):
    """`[(type, name)]` from the declaration of the callee the class chain inherits it from."""
    if receiver:
        hit = _params_on(receiver, name, env)
        if hit:
            return hit
    low = name.lower()
    for cls in env["order"]:
        hit = env["params"].get((cls, low))
        if hit:
            return hit
    return None


def emit_call(stmt, env, receiver=None, head=None):
    """`Name(a, b)` -> the token its declaration earned, each argument, `16` (EndFunctionParms); no trailer.

    `receiver` is the class a member call's receiver holds (`A.F(args)`): the callee is then looked up on *that*
    class's chain first, because a member call names a function of the receiver's class, not of the body's.
    `head` is the call token already decided by the caller -- `Super.F()` writes the parent's own record as a
    `FinalFunction`, which no lookup on the body's chain can produce.
    """
    close = _paren_end(stmt, 1) if len(stmt) > 1 and stmt[1] == ("op", "(") else None
    if close is None or close != len(stmt) - 1:
        raise Refused("a call the statement walk cannot bound (a receiver, or a statement after the call)")
    if env.get("byte_root"):
        # A call's arguments are typed by the callee's parameters, not by the assignment that encloses the call:
        # `ParseColor` writes `Mid(S, 2, 1)` as `2c 02 26` inside a byte-typed expression.
        env = {k: v for k, v in env.items() if k != "byte_root"}
    args = _split_commas(stmt[2:close]) if stmt[2:close] else []
    scope = env["scope"]
    if head is None:
        how = _func_on(receiver, stmt[0][1], env) if receiver else None
        if how is None:
            how = (scope or {}).get("funcs", {}).get(stmt[0][1].lower())
        dele = (scope or {}).get("delegates", {}).get(stmt[0][1].lower())
    else:
        how, dele = None, None        # the caller named the record: a Super call, never a delegate
    if head is not None:
        pass
    elif dele is not None:
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
    params = _callee_params(stmt[0][1], env, receiver)
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
        # A byte-typed literal is written by `emit_expr`, which knows the form from the value (see the number
        # branch): `byte` takes ByteConst up to 254 and the int form for 255. Each argument is its own expression
        # root, so `_root` from an enclosing expression is cleared here.
        out += emit_expr(arg, ptype, {k: v for k, v in env.items() if k != "_root"})[0]
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
        if shape not in ("context", "bool-context", "struct-member", "element"):
            raise Refused("an assignment whose target is a %s expression" % shape)
        if ptype == "bool" or shape == "bool-context":
            return bytes([0x14]) + node + emit_expr(rhs, "bool", env)[0]
        if ptype == "struct":
            # A struct-typed target is a plain `Let`: the copy is by value and the token carries the extent, so
            # `newRotation = Rotation;` is `0f <LocalVariable> <InstanceVariable>` with no member node of its own.
            return bytes([0x0F]) + node + emit_expr(rhs, "", env)[0]
        if ptype in ("array", "delegate", "pointer", ""):
            raise Refused("an assignment to a %s member" % (ptype or "typeless"))
        return bytes([0x0F]) + node + emit_expr(rhs, ptype, env)[0]
    nm = lhs[0][1].lower()
    scope = env["scope"] or {}
    from_props = False
    if nm in scope.get("locals", {}):
        token, index, ptype = scope["locals"][nm]
    elif nm in scope.get("props", {}):
        token, index, ptype = scope["props"][nm]
        from_props = True
    else:
        raise Refused("an assignment to a name neither a local nor a property of the class chain")
    if ptype == "bool":
        # The value is one expression: a literal, a variable read (the bool flag is the read's own), a call, or any
        # expression that types as a bool -- `bIsOn = ! bIsOn;` is `14 <read> 81 <read> 16`, and the unary node's
        # own type is what admits it.
        if len(rhs) != 1 and not _is_call(rhs) and _type_of(rhs, env).lower() != "bool":
            raise Refused("a bool assignment whose value is a compound expression")
        read = bytes([0x2D, token]) + write_index(index)
        if from_props:
            read = _outer_wrapped(nm, read, ptype, "", env) or read
        return bytes([0x14]) + read + emit_expr(rhs, "bool", env)[0]
    if ptype == "struct":
        # Same plain `Let` for a property: `Velocity = 200 * VRand();` is `0f 01 98 <operator>`, and
        # `Pawn . Velocity = Pawn . Velocity + ...` is `0f 19 ... <Add_VectorVector>`. The value is written for the
        # property's own struct type when the artifacts name it (a local's does not -- `_friendly` is the class's
        # property table -- so a local copy takes no cast at all).
        read = bytes([token]) + write_index(index)
        if from_props:
            read = _outer_wrapped(nm, read, ptype, "", env) or read
        return bytes([0x0F]) + read + emit_expr(rhs, _friendly(nm, env) or "", env)[0]
    if ptype in ("array", "delegate", "pointer", ""):
        raise Refused("an assignment to a %s property" % (ptype or "typeless"))
    read = bytes([token]) + write_index(index)
    if from_props:
        read = _outer_wrapped(nm, read, ptype, "", env) or read
    return bytes([0x0F]) + read + emit_expr(rhs, ptype, env)[0]


def _callee_return_type(name, env):
    """The declared return type of a callee the class chain carries, or "" when the text does not say."""
    low = name.lower()
    for cls in env.get("order") or []:
        hit = (env.get("rets") or {}).get((cls, low))
        if hit:
            return hit.lower()
    return ""


def _block_at(toks, i, top=False):
    """`([nodes], next index)` for the statements between a `{` and its `}`, or to the end at `top`."""
    out = []
    while True:
        while i < len(toks) and toks[i] == ("op", ";"):
            i += 1
        if i >= len(toks):
            if top:
                return out, i
            raise Refused("a block that never closes")
        if toks[i] == ("op", "}"):
            if top:
                raise Refused("a body whose braces do not balance")
            return out, i + 1
        node, i = _stmt_at(toks, i)
        if node is not None:
            out.append(node)


def _split_semis(toks):
    """Split a `for` header at its depth-0 `;` tokens."""
    out, cur, depth = [], [], 0
    for tk in toks:
        if tk[0] == "op" and tk[1] in "([":
            depth += 1
        elif tk[0] == "op" and tk[1] in ")]":
            depth -= 1
        if tk == ("op", ";") and depth == 0:
            out.append(cur)
            cur = []
            continue
        cur.append(tk)
    out.append(cur)
    return out


def _switch_groups(toks, i):
    """`(groups, next index)` for a `switch` body, from just past its `{`.

    A group is `(label, nodes)`: `label` is the `case` value's own tokens, or `None` for `default:`. Every `case` is
    its own group even when it shares a body with the next (`case A: case B: X;` leaves the first group empty), which
    is the chain the compiler writes -- each `Case` token links to the next one. `default:` is the list's terminator
    (`Case 0xffff`), so the source order is kept as written and `emit_flow` refuses a `default:` that is not last
    rather than reordering a list the build may have emitted in place.
    """
    groups, label, nodes = [], None, []
    while True:
        while i < len(toks) and toks[i] == ("op", ";"):
            i += 1
        if i >= len(toks):
            raise Refused("a switch body that never closes")
        if toks[i] == ("op", "}"):
            i += 1
            break
        kind, val = toks[i]
        low = val.lower()
        if kind == "keyword" and low in ("case", "default"):
            if label is not None or nodes:
                groups.append((label, nodes))
            label, nodes = None, []
            i += 1
            if low == "default":
                if i >= len(toks) or toks[i] != ("op", ":"):
                    raise Refused("a default whose colon the walk cannot find")
                i += 1
                continue
            j, depth = i, 0
            while j < len(toks):
                tk = toks[j]
                if tk[0] == "op" and tk[1] in "([":
                    depth += 1
                elif tk[0] == "op" and tk[1] in ")]":
                    depth -= 1
                elif tk == ("op", ":") and depth == 0:
                    break
                j += 1
            if j >= len(toks):
                raise Refused("a case whose colon the walk cannot find")
            label, i = toks[i:j], j + 1
            continue
        node, i = _stmt_at(toks, i)
        if node is not None:
            nodes.append(node)
    if label is not None or nodes:
        groups.append((label, nodes))
    return groups, i


def _stmt_at(toks, i):
    """`(node, next index)` for one statement at token `i`.

    A node is `("simple", tokens)`, `("local", tokens)`, `("block", [nodes])`, `("if", cond, then, else)`,
    `("while", cond, body)`, `("for", init, cond, next, body)`, `("until", cond, body)`,
    `("switch", expression, [(label tokens or None, [nodes])])` or `("foreach", iterator call tokens, body)`. The
    whole statement set is parsed here so the shape of a body is known before it is emitted; a statement form the
    emitter does not model is refused by `emit_flow`, by name.
    """
    while i < len(toks) and toks[i] == ("op", ";"):
        i += 1
    if i >= len(toks):
        return None, i
    kind, val = toks[i]
    low = val.lower()
    if kind == "op" and val == "{":
        nodes, j = _block_at(toks, i + 1)
        return ("block", nodes), j
    if kind == "keyword" and low in ("if", "while", "for"):
        j = i + 1
        if j >= len(toks) or toks[j] != ("op", "("):
            raise Refused("a %s without a parenthesised condition" % low)
        close = _paren_end(toks, j)
        if close is None:
            raise Refused("a %s whose condition the walk cannot bound" % low)
        inner = toks[j + 1:close]
        if low == "for":
            parts = _split_semis(inner)
            if len(parts) != 3:
                raise Refused("a for whose header is not three clauses")
            body, j = _stmt_at(toks, close + 1)
            if body is None:
                raise Refused("a for with no body")
            return ("for", parts[0], parts[1], parts[2], body), j
        body, j = _stmt_at(toks, close + 1)
        if body is None:
            raise Refused("a %s with no body" % low)
        if low == "while":
            return ("while", inner, body), j
        els = None
        if j < len(toks) and toks[j][0] == "keyword" and toks[j][1].lower() == "else":
            els, j = _stmt_at(toks, j + 1)
        return ("if", inner, body, els), j
    if kind == "keyword" and low == "do":
        body, j = _stmt_at(toks, i + 1)
        if j >= len(toks) or toks[j][0] != "keyword" or toks[j][1].lower() != "until":
            raise Refused("a do without an until")
        j += 1
        if j >= len(toks) or toks[j] != ("op", "("):
            raise Refused("a do without an until condition")
        close = _paren_end(toks, j)
        if close is None:
            raise Refused("a do whose condition the walk cannot bound")
        k = close + 1
        if k < len(toks) and toks[k] == ("op", ";"):
            k += 1
        return ("until", toks[j + 1:close], body), k
    if kind == "keyword" and low == "foreach":
        # `foreach F(a) S;` -- the iterator variable is an argument of the head's own call, so the statement is one
        # call expression and one body. The compiler writes `Iterator <the call> <wide offset of the IteratorPop>`:
        # the operand comes *after* the call, and `Pickup.CheckTouching`'s `2f 61 33 20 01 00 70 3d 16 1c 00` is
        # `Iterator`, `HighNative1 TouchingActors`, `ObjectConst`, `LocalVariable`, `EndFunctionParms`, `1c 00`.
        j = i + 1
        while j < len(toks) and toks[j] != ("op", "("):
            j += 1
        if j >= len(toks):
            raise Refused("a foreach without an iterator call")
        close = _paren_end(toks, j)
        if close is None:
            raise Refused("a foreach whose iterator call the walk cannot bound")
        body, k = _stmt_at(toks, close + 1)
        if body is None:
            raise Refused("a foreach with no body")
        return ("foreach", toks[i + 1:close + 1], body), k
    if kind == "keyword" and low == "switch":
        # `switch (e) { case v: ...; default: ...; }` is a chain of `Case` tokens, each carrying the absolute wide
        # offset of the next one (`execSwitch`, `0x0004ca20`), ending on `Case 0xffff`; the `Switch` token carries
        # the **comparison width** (`0` a wide string, `1` a byte, `4` an int). `emit_flow` writes it.
        j = i + 1
        if j >= len(toks) or toks[j] != ("op", "("):
            raise Refused("a switch without a parenthesised expression")
        close = _paren_end(toks, j)
        if close is None:
            raise Refused("a switch whose expression the walk cannot bound")
        k = close + 1
        while k < len(toks) and toks[k] == ("op", ";"):
            k += 1
        if k >= len(toks) or toks[k] != ("op", "{"):
            raise Refused("a switch whose body is not a block")
        groups, k = _switch_groups(toks, k + 1)
        return ("switch", toks[j + 1:close], groups), k
    if kind == "keyword" and low == "local":
        # `local rotator newRotation;` is a record the package declares, not code: it emits no bytes.
        j, depth = i, 0
        while j < len(toks):
            tk = toks[j]
            if tk[0] == "op" and tk[1] in "([":
                depth += 1
            elif tk[0] == "op" and tk[1] in ")]":
                depth -= 1
            elif tk == ("op", ";") and depth == 0:
                return ("local", toks[i:j]), j + 1
            j += 1
        raise Refused("a local declaration without its semicolon")
    j, depth = i, 0
    while j < len(toks):
        tk = toks[j]
        if tk[0] == "op" and tk[1] in "([":
            depth += 1
        elif tk[0] == "op" and tk[1] in ")]":
            depth -= 1
        elif tk == ("op", "{") and depth == 0:
            raise Refused("a nested block in a statement the walk does not model")
        elif tk == ("op", ";") and depth == 0:
            return ("simple", toks[i:j]), j + 1
        j += 1
    raise Refused("a statement without its semicolon")


def stmt_tree(body):
    """The body as a list of statement nodes (`_stmt_at`'s shapes) at class scope."""
    return _block_at(body, 0, top=True)[0]


def _off(value):
    """A branch operand: the **absolute** offset into the compiler's uncompacted code array.

    `None` is the measuring pass of a construct whose exit target is its own end (`switch`, `for`, `while`,
    `do`/`until`): the word is still three bytes, so the pass that measures a body's extent writes a placeholder and
    the second pass, which knows the end, writes the real one.
    """
    if value is None:
        return b"\x00\x00"
    if not 0 <= value <= 0xFFFF:
        raise Refused("a code offset of %d does not fit the word the compiler writes" % value)
    return struct.pack("<H", value)


def emit_flow(node, rettype, env, pos):
    """`(bytes, model, shape)` for one statement whose first byte sits at wide offset `pos`.

    A branch operand is an **absolute** position in the compiler's uncompacted code array, measured from the body's
    first byte -- not a distance from the instruction. Measured on the shipped bodies: `if (c) S;` writes the
    position just past `S`, `if (c) S; else T;` writes the `else` branch's position and its `Jump` writes the end,
    and `while (c) S;` writes the trailing `Jump`'s position while that `Jump` writes the loop's own start.
    """
    kind = node[0]
    if kind == "simple":
        if len(node[1]) == 1 and node[1][0][0] == "keyword" and node[1][0][1].lower() in ("break", "continue"):
            # `break` leaves the nearest `switch` or loop, `continue` the nearest loop: both are a bare `Jump` to an
            # absolute wide position the enclosing construct publishes in `env["_exit"]`. A `switch` is not a loop,
            # so it passes `continue` through to whatever encloses it.
            word = node[1][0][1].lower()
            exits = env.get("_exit") or {}
            if word not in exits:
                raise Refused("a `%s` outside the construct it leaves" % word)
            target = exits[word]
            if isinstance(target, tuple):
                # A `foreach`'s `continue` is its **step**: `IteratorNext` then a `Jump` to the pop, so the skip pays
                # one iterator call. `TestInfo.TestContinueForEach` writes `31 06 4f 00` for the arm of an `if` whose
                # body is `continue;`, and 0x4f is the `IteratorPop`'s wide position.
                return bytes([0x31]) + bytes([0x06]) + _off(target[1]), word, word
            return bytes([0x06]) + _off(target), word, word
        return emit_stmt(node[1], rettype, env)
    if kind == "local":
        return b"", "local", "local"
    if kind == "block":
        out, models = b"", []
        for child in node[1]:
            chunk, _m, _s = emit_flow(child, rettype, env, pos + _wide(out))
            out += chunk
            models.append(_m)
        return out, "+".join(models) if models else "block", "block"
    if kind == "if":
        _k, cond, then, els = node
        cbytes, _cshape = emit_expr(cond, "bool", env)
        then_pos = pos + 3 + _wide(cbytes)
        tbytes, _tm, _ts = emit_flow(then, rettype, env, then_pos)
        if els is None:
            target = then_pos + _wide(tbytes)
            return bytes([0x07]) + _off(target) + cbytes + tbytes, "if", "if"
        jump_pos = then_pos + _wide(tbytes)
        ebytes, _em, _es = emit_flow(els, rettype, env, jump_pos + 3)
        end = jump_pos + 3 + _wide(ebytes)
        return (bytes([0x07]) + _off(jump_pos + 3) + cbytes + tbytes
                + bytes([0x06]) + _off(end) + ebytes), "if-else", "if-else"
    if kind == "while":
        _k, cond, body = node
        cbytes, _cshape = emit_expr(cond, "bool", env)
        body_pos = pos + 3 + _wide(cbytes)
        # A `break` in the body names the loop's end and a `continue` its own start, and neither is known until the
        # body's extent is -- so the body is emitted twice, the first time with placeholder targets (each still three
        # bytes, so the extent the second pass measures is the first pass's).
        rough = emit_flow(body, rettype, dict(env, _exit={"break": None, "continue": None}), body_pos)[0]
        jump_pos = body_pos + _wide(rough)
        bbytes, _bm, _bs = emit_flow(body, rettype, dict(env, _exit={"break": jump_pos + 3, "continue": pos}),
                                     body_pos)
        # The target is the *end of the loop*, i.e. the position after the back-jump: `Object.GetItemName` writes
        # 0x47 for a body whose `Jump` sits at 0x44 and whose next statement starts at 0x47.
        return (bytes([0x07]) + _off(jump_pos + 3) + cbytes + bbytes
                + bytes([0x06]) + _off(pos)), "while", "while"
    if kind == "for":
        # `for (init; cond; step) S;` = init, `JumpIfNot <end>`, cond, S, step, `Jump <the JumpIfNot's position>`.
        # The jump operand is an **absolute** position in the uncompacted array, like every other branch operand:
        # `Actor.UpdatePrecacheMaterials` writes `06 0700` for a `JumpIfNot` at wide 7 and `07 3700` for the end of
        # a loop whose step ends at wide 0x37. `playercontroller`-style `for (i = -1; i < 2; i += 2)` is the same
        # shape with a compound assignment for the step; both are measured in the corpus.
        _k, init, cond, step, body = node
        initb = _emit_clause(init, env)
        top = pos + _wide(initb)                       # the `JumpIfNot`'s own position: what the back-jump names
        condb = emit_expr(cond, "bool", env)[0]
        body_pos = top + 3 + _wide(condb)
        stepb = _emit_clause(step, env)
        # A `continue` names the step and a `break` the position past it, so the body is emitted twice as in `while`.
        rough = emit_flow(body, rettype, dict(env, _exit={"break": None, "continue": None}), body_pos)[0]
        tail = body_pos + _wide(rough)
        end = tail + _wide(stepb) + 3
        bodyb, _bm, _bs = emit_flow(body, rettype, dict(env, _exit={"break": end, "continue": tail}), body_pos)
        # The position just past the loop is *after* the back-`Jump`: `Actor.UpdatePrecacheMaterials`'s `06 0700`
        # sits at wide 0x34 and the shipped `07 3700` names 0x37, three bytes further on -- the same rule the `while`
        # shape follows, and reading it as the jump's own position left every `for` three bytes short.
        return (initb + bytes([0x07]) + _off(end) + condb + bodyb + stepb
                + bytes([0x06]) + _off(top)), "for", "for"
    if kind == "until":
        # `do S until (c);` = S, `JumpIfNot <S's first byte>`, c: the condition is written *after* the jump that
        # reads it, and the target is the body's own start. `Engine.TestInfo.TestContinueDoUntil` writes
        # `07 1000` for a body that begins at wide 0x10, and the `Jump` a `continue` compiles to names the position
        # after that `JumpIfNot` (`0x60`), which is why the condition's position is the continue target below.
        _k, cond, body = node
        # A `continue` in a `do`/`until` names the position after the `JumpIfNot` -- the condition code -- and a
        # `break` the position past it, so the body is emitted twice as in `while`.
        rough = emit_flow(body, rettype, dict(env, _exit={"break": None, "continue": None}), pos)[0]
        cond_pos = pos + _wide(rough) + 3
        condb = emit_expr(cond, "bool", env)[0]
        bodyb, _bm, _bs = emit_flow(body, rettype, dict(env, _exit={"break": cond_pos + _wide(condb),
                                                                   "continue": cond_pos}), pos)
        return bodyb + bytes([0x07]) + _off(pos) + condb, "do-until", "do-until"
    if kind == "foreach":
        # `foreach F(a) S;` = `Iterator <F(a)> <wide offset of the IteratorPop>`, S, `IteratorNext`, `IteratorPop`.
        # The loop has no back edge: the step is the `IteratorNext` the body ends on, and the token's operand is the
        # pop's own position, so a `break` is a `Jump` to it and a `continue` is the step plus that `Jump` (see the
        # simple-statement branch above). `Pickup.CheckTouching` is `2f 61 33 20 01 00 70 3d 16 1c 00 1b 2d 00 70 3d
        # 16 31 30`, and 0x1c is the `IteratorPop`'s wide offset -- the body starts at 0x0b and is 10 wide bytes.
        # The operand is known only after the body's extent is, so, like `switch`, the body is emitted twice with a
        # two-byte placeholder, which keeps the extent the second pass measures the first pass's.
        _k, head, body = node
        call = emit_expr(head, "bool", env)[0]
        body_pos = pos + 3 + _wide(call)

        def once(pop):
            exits = {"break": pop, "continue": ("iterate", pop)}
            # `_iter` counts the iterators a `return` inside the body has to pop (see `emit_stmt`).
            inner = dict(env, _exit=exits, _iter=int(env.get("_iter") or 0) + 1)
            return emit_flow(body, rettype, inner, body_pos)[0]

        rough = once(None)
        pop = body_pos + _wide(rough) + 1
        bodyb = once(pop)
        return (bytes([0x2F]) + call + _off(pop) + bodyb + bytes([0x31, 0x30]), "foreach", "foreach")
    if kind == "switch":
        # `Switch <width>` + the expression, then one `Case` per labelled group: `0a <absolute wide offset of the
        # next case> <value expression>` (the opcode is `0x0a`, not `Return`'s `0x04`), and `0a ff ff` for the
        # terminator (`default:`). An arm's `break` is a
        # `Jump` to the switch's own end, which is known only after the last group is emitted -- so, as in the loops,
        # the switch is emitted twice; every placeholder word is three bytes, like the real jump, so the extent the
        # second pass measures is the first pass's.
        _k, expr_toks, groups = node
        stype = _type_of(expr_toks, env)
        width = SWITCH_WIDTH.get(stype)
        if width is None:
            raise Refused("a switch on a %s, whose comparison width the corpus does not pin"
                          % (stype or "value the walk cannot type"))
        if groups and groups[0][0] is None:
            raise Refused("a switch whose first label is not a `case`")
        if sum(1 for lab, _s in groups if lab is None) > 1:
            raise Refused("a switch with two `default:` labels")
        if groups and groups[-1][0] is not None and any(lab is None for lab, _s in groups):
            raise Refused("a switch whose `default:` is not its last label")
        head = bytes([0x05, width]) + emit_expr(expr_toks, stype, env)[0]

        def once(end_target):
            out = bytearray(head)
            src = env.get("_exit") or {}
            exits = {"break": end_target}
            if "continue" in src:
                exits["continue"] = src["continue"]
            benv = dict(env, _exit=exits)
            # The terminator is written whether or not the source has a `default:`: a `switch` with none still ends
            # on `Case 0xffff` and an empty body (`PlayerProfile.GetCategoryVolume`), so the list's last case links to
            # it and control runs from it into the statement after the switch. A switch with **no case at all** is
            # the terminator alone: `ExtrasPage.ButtonClick` writes `05 04 <Sender> 0a ff ff`, with no case word to
            # patch -- its source is `switch (Sender) { }`.
            seq = list(groups)
            if not seq or seq[-1][0] is not None:
                seq.append((None, []))
            cases = []
            for lab, stmts in seq:
                cases.append((len(out) + 1, pos + _wide(bytes(out))))
                if lab is None:
                    out += bytes([0x0A, 0xFF, 0xFF])
                else:
                    out += bytes([0x0A, 0x00, 0x00]) + emit_expr(lab, stype, env)[0]
                for st in stmts:
                    out += emit_flow(st, rettype, benv, pos + _wide(bytes(out)))[0]
            for i in range(len(cases) - 1):          # the terminator's own word stays 0xffff
                at = cases[i][0]
                out[at:at + 2] = _off(cases[i + 1][1])
            assert out[cases[-1][0] - 1] == 0x0A and out[cases[-1][0]:cases[-1][0] + 2] == b"\xff\xff"
            return bytes(out)

        rough = once(None)
        return once(pos + _wide(rough)), "switch", "switch"
    raise Refused("a %s statement" % kind)


def _emit_clause(toks, env):
    """One `for`-header clause: an assignment, a compound assignment or an increment -- never a trailer."""
    if not toks:
        return b""
    at = _assign_at(toks)
    if at is not None:
        return emit_assign(toks, at, env)
    return emit_expr(toks, "", env)[0]



# The `Switch` token's operand is the number of bytes the case comparison reads, not a case count: `0` a wide string,
# `1` a byte and `4` an int (`docs/BYTECODE.md`, measured against `execSwitch` at `0x0004ca20` and the corpus's 200
# sites). A `name` compares as its four-byte index, and a `bool` as one byte, like the `ByteConst` cases the corpus
# writes under width 1. An **object** compares at 4 too: `InGameMenu.ButtonClick`'s `switch (Sender)` writes
# `05 04` in front of a `LocalVariable` that holds a `GUIButton`, and its `case ExitBN:` is an `InstanceVariable`
# read of an `ObjectProperty` -- a pointer is four bytes wide at the switch, like an int.
SWITCH_WIDTH = {"string": 0, "byte": 1, "bool": 1, "int": 4, "name": 4, "float": 4, "object": 4}


def _compound(stmt, env):
    """`i ++;` / `x += y;` -> the node alone. There is no `Let` around a compound assignment.

    `Adrenaline += Amount;` writes `b8 <read> <value> 16`: the operator *is* the statement, and its token is a
    separate native from the binary one (`AddEqual_FloatFloat` 184 against `Add_FloatFloat` 174, `AddEqual_IntInt`
    161 against `Add_IntInt` 146), so the declared (left, right) pair picks it exactly as it picks `+`.
    """
    inc = _incdec(stmt, env)
    if inc is not None:
        return inc
    op = _operator_types(stmt, env)
    if op is not None and op[0] in COMPOUND:
        return emit_expr(stmt, "", env)
    return None


def _value_size(rtype, env):
    """The size byte a `Context` writes for a call's **value**: 0 for a void call, 4 for an object."""
    if not rtype:
        return 0
    if rtype in (env.get("class_names") or set()):
        return 4
    if rtype in RETURN_SIZE:
        return RETURN_SIZE[rtype]
    raise Refused("a call returning %s, whose value size this walk does not carry" % rtype)


def _declared_on_chain(low, order, env):
    """True when a function of that name is declared on the body's own class chain."""
    order = set(order)
    for idx, owner in (env.get("funcs_all") or {}).get(low, []):
        if owner in order:
            return True
    for (_p, c, f) in (env.get("natives") or {}):
        if f == low and c in order:
            return True
    return any(low in (env.get("final_of") or {}).get(c, ()) for c in order)


def _record_on(cls, name, env):
    """The **record** a `Super.F()` call names: an export of this package or an import, never a name index.

    `TransientAmbientCreature.PostBeginPlay` writes `1c bc 16` for `Super.PostBeginPlay()`: `bc` is import 60,
    `PostBeginPlay` of class `Function`; `FishBase.PhysicsVolumeChange` writes `1c 46 02 ...`, export 134 on the
    parent in the same package. 1,061 bodies open this way in the corpus.

    The walk covers the **whole** parent chain before it falls back to the import: `TeamDeathMatch.RestartPlayer`
    writes `1c 50 49` (export 4688, `DeathMatch.RestartPlayer`), not the `RestartPlayer` import, and
    `BotGuide.SetNewScript` writes export 3832 (`BotBase`). An import fallback taken at the first ancestor that
    declares nothing wrote the `Function` import for both. Inside a state the parent's same-state override wins
    (`func_owners`), which is what `Armor.BeginState` (`Super.BeginState()` -> `Powerups.Activated.BeginState`) and
    `BotSG.Tick` name.
    """
    low = name.lower()
    scope = env.get("scope") or {}
    chain = scope.get("chain") or {}
    pack = scope.get("pack") or ""
    rows = (env.get("func_owners") or {}).get(low, [])
    state = (env.get("state") or "").lower()
    for _p, c in _ancestors(chain, pack, cls.lower()):
        if state:
            hit = [x for x in rows if x[1] == c and x[2] == state]
            if hit:
                return hit[0][0]
        hit = [x for x in rows if x[1] == c and not x[2]]
        if hit:
            return hit[0][0]
    imp = [x for x in (env.get("imports") or {}).get(low, []) if x[1] == "Function"]
    owners = env.get("import_owners") or {}
    if imp:
        # Two ancestors that declare the same name import it once each (`AmbientCreatures` carries
        # `PostBeginPlay` for `Controller`, `Actor` and `Pawn`), and the compiler's record is the one whose owner
        # is the **nearest** class of the parent's chain -- and the walk starts **at the parent**, because this is
        # called with `cls` = the parent: skipping it (`[1:]`) wrote the grandparent's import on 70 bodies
        # (`EmptyHands.PostBeginPlay` imports `PostBeginPlay` for `StargateWeapon` (-134), `Actor` (-127) and
        # three more, and the build writes -134). The 29 that still do not agree are `BeginState`/`EndState`,
        # which name a state rather than a function.
        for _p, c in _ancestors(chain, pack, cls.lower()):
            hit = [x for x in imp if owners.get(x[0]) == c]
            if hit:
                return hit[0][0]
        if len(imp) == 1:
            return imp[0][0]
    return None


def _super_call(stmt, env):
    """`Super.F(args);` -> `1c <F's record> args 16`, or None when the statement is not a Super call."""
    if not (len(stmt) >= 4 and stmt[0][0] in ("ident", "keyword") and stmt[0][1].lower() == "super"
            and stmt[1] == ("op", ".")):
        return None
    order = env.get("order") or []
    if len(order) < 2:
        raise Refused("a Super call in a class with no super")
    parent = order[1]
    name = stmt[2]
    if name[0] not in ("ident", "keyword"):
        raise Refused("a Super call whose member the walk cannot name")
    rec = _record_on(parent, name[1], env)
    if rec is None:
        raise Refused("a Super call to a name the parent's chain does not declare")
    return emit_call(stmt[2:], env, receiver=parent, head=bytes([0x1C]) + write_index(rec))


def _within_call(stmt, env):
    """The class a bare call resolves on when this class is declared `within` it, or "".

    `class CheatManager extends Object within PlayerController` calls `DeactivateAstralSight()` with no receiver,
    and the compiler writes it as a member of `Outer`: `19 01 8e 05 00 00 1b 5b 15 16`. Five classes carry the
    clause in this corpus (`AIMsgManager`, `CheatManager`, `PlayerInput`, `SGPlayerInput`, `StreamTag`).
    """
    within = env.get("within") or ""
    if not within or len(stmt) < 2 or stmt[0][0] not in ("ident", "keyword") or stmt[1] != ("op", "("):
        return ""
    low = stmt[0][1].lower()
    if _declared_on_chain(low, env.get("order") or [], env):
        return ""
    if _func_on(within, stmt[0][1], env) is None:
        return ""
    return within


def _emit_within_call(stmt, env, within):
    """`F(args);` in a `within X` class -> `19 <Outer> 05 00 <value size> <call>`; `0e` in front for a string."""
    scope = env.get("scope") or {}
    outer = scope.get("outer_ref")
    if outer is None:
        raise Refused("a `within` call whose package imports no `Outer`")
    inner = dict(env)
    inner["within_call"] = True
    call = emit_call(stmt, inner, receiver=within)
    rtype = _rettype_on(within, stmt[0][1], env)
    node = (bytes([0x19, INSTANCE_TOKEN]) + write_index(outer) + struct.pack("<H", 5)
            + bytes([_value_size(rtype, env)]) + call)
    if rtype == "string":
        return bytes([0x0E]) + node, "within-call", "eat-string-within-call"
    return node, "within-call", "within-call"


def _stmt_member_call(stmt, env):
    """`A.F(args);` -> `19 <A> <skip> <size> <F>`: a call statement whose callee is a member of a receiver.

    The word is the call's own width in the uncompacted array (`_wide`) -- `PlayerController.SwitchWeapon` writes
    `19 01 05 0b 00 00 1b 61 03 00 4b 6e 16` for `Pawn.SwitchWeapon(F);` and the call is 11 wide -- except behind
    a property **import** (`Outer`), whose shape writes 5 whatever the member is (measured on
    `CheatManager.astral`/`puss`). The size byte is the call's value size: 0 for a void call.
    """
    at = _dot_at(stmt)
    if at is None:
        return None
    obj, member = stmt[:at], stmt[at + 1:]
    if not _is_call(member):
        raise Refused("a statement that is a member read, not a call")
    cls = _class_of(obj, env)
    if not cls:
        raise Refused("a call statement whose receiver's class the artifacts do not name")
    if _func_on(cls, member[0][1], env) is None:
        raise Refused("a member call statement to a name the receiver's chain does not declare")
    # A `delegate` is two records, and the call is `43 <DelegateProperty> <name index>` -- a `DelegateFunction` over
    # the receiver's own property, which no function lookup produces. `PlayerController.AdminReply` calls
    # `Player.GUIController.OnAdminReply(Reply)` and the package writes `43 5d 63 4d 1e`; read as a member call it
    # came out a `VirtualFunction` of the wrong width. Refused by name until the wrapper is measured.
    if "__%s__delegate" % member[0][1].lower() in ((env.get("scope") or {}).get("props_all") or {}):
        raise Refused("a delegate call through a receiver (the callee is a DelegateProperty record)")
    memb = emit_call(member, env, receiver=cls)
    rtype = _rettype_on(cls, member[0][1], env)
    # The word is the call's own width in the uncompacted array -- except behind `Outer`, whose shape writes 5
    # whatever the member is (`CheatManager.astral`/`puss`). Every *other* property import uses the computed word:
    # `Pawn.SetSprinting(true)` in BotBase is `19 01 8a 07 00 00` (the call is 7 wide), and reading every import as
    # `Outer` wrote 5 there -- nine bodies, all of them one word short.
    imp = _import_of(obj, env)
    skip = 5 if imp is not None and imp == (env.get("scope") or {}).get("outer_ref") else _wide(memb)
    node = (bytes([0x19]) + emit_expr(obj, "", env)[0] + struct.pack("<H", skip)
            + bytes([_value_size(rtype, env)]) + memb)
    if rtype == "string":
        return bytes([0x0E]) + node, "call-member", "eat-string-call-member"
    return node, "call-member", "call-member"


def emit_stmt(stmt, rettype, env):
    """`(bytes, model, shape)` for one simple statement, without the body trailer."""
    if stmt and stmt[0][0] == "keyword" and stmt[0][1].lower() == "return":
        expr, shape = emit_expr(stmt[1:], rettype, env)
        # A `return` that leaves a `foreach` pops the iterator first: `Pawn.TouchingWaterVolume` ships
        # `... 6d 02 | 30 04 27 | 31 30 | 04 28` -- the `IteratorPop` in front of `Return True`, and the loop's own
        # `IteratorNext`/`IteratorPop` after it as dead code. The iterator is popped once per `return` that leaves
        # it, so a body with two returns carries two pops; two **nested** iterators would need two pops each, and the
        # corpus has no such body (`0` rows), so that shape is refused rather than doubled.
        depth = int(env.get("_iter") or 0)
        if depth > 1:
            raise Refused("a `return` inside %d nested `foreach` loops, whose pop count the corpus does not pin"
                          % depth)
        return bytes([0x30]) * depth + bytes([0x04]) + expr, "return", shape
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
        return bytes([0x09]) + struct.pack("<H", line) + expr, "assert", "assert"
    if stmt and stmt[0][0] == "keyword" and stmt[0][1].lower() in ("if", "for", "while", "do", "switch", "case",
                                                                  "else", "foreach"):
        # The guard is on the statement's **first** token: a control-flow form the parse did not take is refused by
        # name, while an argument that happens to be spelled like one is not. `Switch` is a `LocalMessage`
        # parameter, and `GameInfo.BroadcastLocalized` passes it bare, so a guard that scanned the whole statement
        # refused every localization body in the corpus (6 in Engine.u alone).
        raise Refused("a statement that is control flow")
    at = _assign_at(stmt)
    if at is not None:
        return emit_assign(stmt, at, env), "assign", "assign"
    comp = _compound(stmt, env)
    if comp is not None:
        return comp[0], "expr", comp[1]
    sup = _super_call(stmt, env)
    if sup is not None:
        return sup, "super-call", "super-call"
    within = _within_call(stmt, env)
    if within:
        return _emit_within_call(stmt, env, within)
    mem = _stmt_member_call(stmt, env)
    if mem is not None:
        return mem
    call = emit_call(stmt, env)
    if stmt and stmt[0][0] in ("ident", "keyword") and _callee_return_type(stmt[0][1], env) == "string":
        # A statement call whose declaration returns `string` is prefixed with `EatString` (0x0e):
        # `PlayerController.LoadGame` is `0e 1b 11 a8 1f "LoadSaveGame" 00 01 5e 6e 16 16` and the two calls this
        # walk emits without the prefix (`Logf(... $ ...)`, `log(... $ ...)`) return nothing. The temporary the call
        # produces is eaten instead of copied. `$`/`@` themselves are ordinary operator nodes (0x70/0xa8) -- the
        # previous note credited them with the prefix, which this reading corrects.
        return bytes([0x0E]) + call, "call", "eat-string-call"
    return call, "call", "call"


def emit_body(body, rettype, env, is_native=False):
    """`(bytes, model, shape)` for a modelled body, or Refused naming what is not modelled."""
    if body is None:
        if is_native:
            raise Refused("a native declaration: its body relays its parameters to the native")
        return TRAILER, "stub", "stub"          # declared without a body: the package compiles the trailer alone
    nodes = stmt_tree(body)
    if not nodes:
        return TRAILER, "empty", "empty"
    if sum(1 for k, v in body if k == "keyword" and v.lower() == "assert") > 1:
        # The line operand is pinned once per declaration, from the whole body's token run, so a body with two
        # asserts has no second line to read: refuse rather than number both from the first.
        raise Refused("two asserts, whose lines the walk pins once per body")
    out, models, shape = b"", [], ""
    for node in nodes:
        chunk, model, shape = emit_flow(node, rettype, env, _wide(out))
        out += chunk
        models.append(model)
    if len(models) == 1:
        return out + TRAILER, models[0], shape
    return out + TRAILER, "sequence", "sequence(%d)" % len(nodes)


# ----------------------------------------------------------------------------------------------------------------
# scoring against the dump

JOIN_FIELDS = ["package", "class", "state", "function", "cause"]


def _chain():
    """{(package, class): (super, super's package)} -- the ancestor walk, from the committed class table.

    The reverse map (`_CLASS_PKG`) is what makes the walk usable for a **receiver** class: a member call names a
    class the body's package only imports (`Instigator.AttachToBone` in StargateContent, whose receiver is Engine's
    `Pawn`), and a walk started in the body's package stops at the class's own row. Only a class that one package
    declares is re-homed; a name two packages carry (`Trigger` in Engine and in a game package) keeps the body's.
    """
    global _CLASS_PKG
    chain, pkgs = {}, {}
    path = os.path.join(ROOT, "inventory", "classes.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            # stored in the walk's own order: the super's package first, then the super
            chain[(r["package"].lower(), r["class"].lower())] = (r["package_of_super"].lower(), r["super"].lower())
            pkgs.setdefault(r["class"].lower(), set()).add(r["package"].lower())
    _CLASS_PKG = {c: next(iter(ps)) for c, ps in pkgs.items() if len(ps) == 1}
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
    crosses a package boundary where the build did -- and it *starts* in the class's own package when the name was
    only imported (`_CLASS_PKG`), because a member call's receiver is often declared in another package."""
    out, seen, p, c = [], set(), pack, cls.lower()
    while c and (p, c) not in seen:
        if (p, c) not in chain and c in _CLASS_PKG:
            p = _CLASS_PKG[c]
        if (p, c) in seen:
            break
        seen.add((p, c))
        out.append((p, c))
        p, c = chain.get((p, c), ("", ""))
    return out


def _class_tables(pkg, kinds, names):
    """The package's own records as the compiler addressed them: property and function exports, the imports, and
    the classes an object cast can name."""
    props, funcs, imports, classes, import_owners = {}, {}, {}, {}, {}
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
        # ...and its `package` field is the class the member belongs to (a negative import index, or an export of
        # this package): that owner is what a `Super.F()` picks between two same-named imports
        ref = im.get("package", 0)
        ref = ref - 2 ** 32 if ref > 2 ** 31 else ref
        if ref < 0 and -ref <= len(pkg.imports):
            import_owners[-(j + 1)] = str(pkg.name(pkg.imports[-ref - 1]["name"])).lower()
        elif ref > 0 and ref <= len(pkg.exports):
            import_owners[-(j + 1)] = str(pkg.name(pkg.exports[ref - 1]["name"])).lower()
        if ikind == "Class":
            classes.setdefault(iname, []).append((-(j + 1), "Class"))
    return props, funcs, imports, classes, import_owners


def _func_owners(pkg, kinds, names):
    """{name: [(export index, owning class, owning state)]} -- a function's owner walked to the nearest `Class`.

    A `Function` export's Outer is a *state* as often as a class: `Powerups`' `Activated.BeginState` is export 4466
    whose Outer is the state, and an owner read as `names[outer]` then names `Activated` and no class walk can match
    it. The owning class is `owner_class`'s walk; the state name is kept because the compiler resolves a
    `Super.F()` written inside a state to the parent's *same-state* override first -- `BotSG.Tick` in state
    `BotUnconscious` writes export 4129 (`BotEnemy.BotUnconscious.Tick`), not export 417 (`BotEnemy.Tick`).
    """
    out = {}
    dis = _dis()
    for i, e in enumerate(pkg.exports):
        if kinds.get(i + 1) != "Function":
            continue
        outer = e["outer"]
        owner = dis.owner_class(pkg.exports, kinds, names, outer).lower()
        state = names.get(outer, "").lower() if kinds.get(outer) == "State" else ""
        out.setdefault(names.get(i + 1, "").lower(), []).append((i + 1, owner, state))
    return out


def _scope(pkg, kinds, names, tables, pack, cls, func_index, chain, natives, table_index, final_of=None,
           dims_by_cls=None, consts_by_cls=None):
    """The names one body may mention, each with the index and token the package's own record earns.

    A property is found by *name and owner*: the record's index is what the compiler wrote, so nothing is rebuilt.
    A callee is resolved by walking the ancestor chain to the class that declares it -- the native table says the
    token (0 means "not a native opcode, call it by name"), and its `final` flag picks the call token.
    """
    props, funcs, imports, classes, _import_owners = tables
    scope = {"props": {}, "locals": {}, "funcs": {}, "classrefs": {}, "props_all": props, "chain": chain,
             "pack": pack, "cls": cls.lower(),
             # a property the package exports once, on a class outside this body's chain, is read through `Outer`
             "outer_props": set(),
             # every name that fallback picked, whatever its type: a `within` call's argument reads it through
             # `Outer` too (`CheatManager.CauseEvent` passes `Pawn`, which is `PlayerController`'s property)
             "outside_props": set(),
             # the imports, so a member of a class this package only imports can find its record (a negative
             # index): `Pawn.Location` in AmbientCreatures is not an export of AmbientCreatures
             "imports": imports,
             # the `Outer` record itself -- a property import (`01 8e` is import 14 of Engine.u)
             "outer_ref": next((c[0] for c in imports.get("outer", []) if c[1] in PROPERTY_CLASSES), None),
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
    # The dimension `ArrayCount(X)` folds to, the nearest declaration winning exactly as a property record does:
    # `var string ProgressMessage[4];` is on `PlayerController` itself, while `EventCache[64]` is `LevelInfo`'s.
    cst = {}
    for anc in order:
        for nm, v in (consts_by_cls or {}).get(anc, {}).items():
            cst.setdefault(nm, v)
    dim = {}
    for anc in order:
        for nm, n in (dims_by_cls or {}).get(anc, {}).items():
            if isinstance(n, str):
                # `Doors[MAX_DOORS]`: the dimension is a `const` this class or an ancestor declares
                n = cst.get(n)
                if n is None:
                    continue
            dim.setdefault(nm, n)
    scope["dims"] = dim
    # The `const NAME = <int>;` table itself, which `ArrayCount` already read and a bare `NUMTEAMS` did not:
    # a const is a compile-time literal, so `emit_expr` substitutes it at the site.
    scope["consts"] = cst
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
                # One record by that name in the whole package, declared on a class that is *not* on this body's
                # chain: `PlayerInput.ChangeSnapView` writes `bSnapToLevel` (export 2604, on `PlayerController`) as
                # `19 01 8e 05 00 04 2d 01 6c 28` -- a `Context` around `Outer`. The index is right, the node is not
                # a bare read, so it is marked and the read wraps it (see `_outer_wrapped`). Only the bool shape is
                # wrapped at a bare read: the 27 non-bool names that reach this fallback are read *bare* in this
                # corpus, so they keep that read and are not given a node the package does not write -- except as a
                # `within` call's argument, where the read is compiled in `Outer`'s scope (`outside_props`).
                picked = cands[0]
                scope["outside_props"].add(nm)
                if PROP_TYPE.get(picked[2]) == "bool":
                    scope["outer_props"].add(nm)
        if picked is not None:
            scope["props"][nm] = (INSTANCE_TOKEN, picked[0], PROP_TYPE.get(picked[2], ""))
    # A property this package only *imports* -- an ancestor in another package declares it and no class here
    # re-declares it -- is still a record the compiler wrote, with a negative index: `Corroded.GetPawnDamageEffect`
    # is `04 02 d8 02` (`Default.PawnDamageEffect`, import 152) and `VoiceChatReplicationInfo.JoinChannelAt` reads
    # its `Level.Game`-style names the same way. The class chain is not the authority here; the package's own
    # import table is.
    for nm, cands in imports.items():
        if nm in scope["props"]:
            continue
        imp = [c for c in cands if c[1] in PROPERTY_CLASSES]
        if len(imp) == 1:
            scope["props"][nm] = (INSTANCE_TOKEN, imp[0][0], PROP_TYPE.get(imp[0][1], ""))
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


_DIS_MODULE = None


def _dis():
    """`uscript-dis.py` as a module: the locator and the token walk are its own, one answer for both halves.

    Cached: the emitter asks it for the code-array width of every member node it writes, and re-executing a
    6,000-line module per node would cost more than the walk.
    """
    global _DIS_MODULE
    if _DIS_MODULE is None:
        spec = importlib.util.spec_from_file_location("uscript_dis", os.path.join(ROOT, "tools", "uscript-dis.py"))
        _DIS_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_DIS_MODULE)
    return _DIS_MODULE


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
    slot and an argument's token need -- and every `local Type Name;`, which is how a receiver's class is read when
    the receiver is a local rather than a property."""
    index = {}
    params = {}
    rets = {}
    localtypes = {}
    proptypes = {}
    withins = {}
    dims, consts = {}, {}
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
            # A class declared `within X` resolves an unqualified name against X: `CheatManager extends Object
            # within PlayerController` calls `DeactivateAstralSight()` bare and the compiler writes it through
            # `Outer` (`19 01 8e 05 00 00 <call>`). Only five classes in this corpus carry the clause.
            m = re.search(r"(?is)\bclass\s+[A-Za-z_]\w*\s+extends\s+[A-Za-z_]\w*[^;{]*?\bwithin\s+([A-Za-z_]\w*)",
                          text[:4000])
            if m:
                withins[cls] = m.group(1).lower()
            for m in re.finditer(r"\blocal\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)", text, re.I):
                localtypes.setdefault((cls, m.group(2).lower()), m.group(1).lower())
            # A property's *declared type word*, which is how a receiver's class is named when the package's own
            # artifact cannot: `StargateMenus.ProfilePanel` has no row for `Automated ProfileList ProfileList` in
            # `class_properties.csv`, so `ProfileList.ItemCount()` had no class to join on.
            for m in re.finditer(r"(?m)^[ \t]*(?:var|Automated)\s*(?:\([^)]*\))?\s*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)",
                                 text, re.I):
                proptypes.setdefault((cls, m.group(2).lower()), m.group(1).lower())
            # The declared dimension of an array: `ArrayCount(X)` folds to it, so a name whose declarations
            # disagree (two functions declaring one local name at two sizes) is left out rather than guessed.
            for nm, dim in _array_dims(text).items():
                dims.setdefault((cls, nm), set()).add(dim)
            for nm, v in _consts_of(text).items():
                consts.setdefault((cls, nm), set()).add(v)
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
                for enum, members in enums_of(text):
                    enum_names.add(enum.lower())
                    for cname, ordinal in members:
                        enums.setdefault((cls, cname.lower()), (ordinal, enum))
    return index, files, params, enums, enum_names, rets, localtypes, proptypes, withins, dims, consts


def score(tree, dump, out_dir, residue_dir=None, only=None):
    dis = _dis()
    from ue2pkg import Package                                  # noqa: PLC0415 - only with a real dump

    index, files, params, enums, enum_names, rets, localtypes, proptypes, withins, dims, consts = _source_index(tree)
    src_texts = {}
    for key, value in index.items():
        src_texts.setdefault(key[0], []).append(value[2])
    dims_by_cls = {}
    for (c, n), sizes in dims.items():
        if len(sizes) == 1:
            dims_by_cls.setdefault(c, {})[n] = next(iter(sizes))
    consts_by_cls = {}
    for (c, n), values in consts.items():
        if len(values) == 1:
            consts_by_cls.setdefault(c, {})[n] = next(iter(values))
    enum_seen = {}
    for (_cls, low), hit in enums.items():
        enum_seen.setdefault(low, set()).add(hit)
    # The names the whole corpus spells as one enum constant: keyed by name, valued `(ordinal, enum)`, and only
    # where every declarer agrees. `NM_DedicatedServer` is `LevelInfo.ENetMode`'s ordinal 1 in one class and
    # nothing else, so a body whose chain never reaches `LevelInfo` can still write `24 01` for it.
    enum_by_name = {nm: next(iter(v)) for nm, v in enum_seen.items() if len(v) == 1}
    localtypes_by_cls = {}
    for (c, n), t in localtypes.items():
        localtypes_by_cls.setdefault(c, {})[n] = t
    proptypes_by_cls = {}
    for (c, n), t in proptypes.items():
        proptypes_by_cls.setdefault(c, {})[n] = t
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
        if only and fn.lower() not in only:
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
        func_owners = _func_owners(pkg, kinds, names)
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
            scope = _scope(pkg, kinds, names, tables, pack, cls, ei + 1, chain, natives, table_index, final_of,
                           dims_by_cls, consts_by_cls)
            env = {"names": table_index, "scope": scope, "params": params, "enums": enums,
                   "enum_names": enum_names, "enum_by_name": enum_by_name,
                   "class_names": class_names, "order": scope["order"],
                   "rets": rets, "assert_line": aline,
                   # the three joins a member call's callee needs: where its declaration sits, its `final` flag,
                   # and the records of the package it is declared in
                   "natives": natives, "funcs_all": tables[1], "imports": tables[2], "final_of": final_of,
                   "import_owners": tables[4],
                   "localtypes": localtypes_by_cls.get(cls.lower(), {}),
                   "proptypes": proptypes_by_cls.get(cls.lower(), {}),
                   # a parameter's struct name is in its own declaration (`Vector momentum`), and the package's
                   # record only says `StructProperty`
                   "paramtypes": {p[1].lower(): p[0].lower() for p in (decl_params or [])},
                   # the class this one is declared `within`, whose names resolve through `Outer`
                   "within": withins.get(cls.lower(), ""),
                   # which state the body is written in: a `Super.F()` prefers the parent's same-state override
                   "state": state.lower(),
                   # a function record's owning class and state, walked past the state's own Outer
                   "func_owners": func_owners}
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
            if ours != body:
                # A modelled body that differs is a **bug in the walk**, not a measurement: every family here was
                # built until it matched, so a diff means a rule that is wrong for this shape. Refusing it keeps the
                # artifact's contract -- a row is either byte-identical or names what is not modelled -- and keeps a
                # wrong body out of `inventory/uscript_emit.csv`, which the next run would read as evidence. It is
                # counted under `not-modelled` and **not** under `modelled`, because the census's `modelled` column
                # is the row count (`--check` holds the two equal) and a body with no row is not modelled here.
                tally["not-modelled"] += 1
                reasons["an emitted body that differs from the shipped bytes"] += 1
                if residue_dir:
                    examples.setdefault("an emitted body that differs from the shipped bytes", []).append(
                        "%s.%s" % (cls, fname))
                    refused.append({"package": fn, "class": cls, "state": state, "function": fname,
                                    "rettype": rettype,
                                    "reason": "an emitted body that differs from the shipped bytes (%d vs %d)"
                                              % (len(ours), len(body)),
                                    "source": " ".join(v for _k, v in (src_body or ()))[:300],
                                    "theirs": body.hex(),
                                    "ours": ours.hex()})
                continue
            tally["modelled"] += 1
            shapes[shape] += 1
            verdict = "identical"
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
            fields = ["package", "class", "state", "function", "rettype", "reason", "source", "theirs", "ours"]
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
function Sequence() { Foo(1); Health = 1; }
function Locals() { local int Extra; Health = 1; }
function IfElse() { if (bDead) Health = 1; else Health = 2; }
function WhileLoop() { while (bDead) Health = 1; }
function WhileThen() { while (bDead) Health = 1; Health = 2; }
function ForLoop() { for (Slot = 0; Slot < 2; Slot++) Health = 1; }
function DoLoop() { do Health = 1; until (bDead); }
function ForCompound() { for (Slot = -1; Slot < 2; Slot += 2) Health = 1; }
function IncSlot() { Slot++; }
function PreIncSlot() { ++Slot; }
function AddToSlot() { Slot += 2; }
function SubFromSlot() { Slot -= 2; }
function IncFloat() { FCount++; }
function CallMember() { Target.Ping(); }
function CallMemberArg() { Target.Pong(1); }
function CallMemberText() { Target.Say("x"); }
function CallUnknown() { Target.Missing(); }
function bool NotFlag() { return !bDead; }
function NotSet() { bDead = !bDead; }
function bool NotAnd() { return !bDead && bFlag; }
function int NegInt() { return -Health; }
function bool NotCall() { return !Target.bFlag; }
function int ACount() { return ArrayCount(Items); }
function ACountSet() { Health = ArrayCount(Items); }
function ACountUnknown() { return ArrayCount(Caption); }
function EachPlain() { foreach Iter(Slot) Foo(1); }
function EachStop() { foreach Iter(Slot) break; }
function EachSkip() { foreach Iter(Slot) { if (bDead) continue; Foo(1); } }
function ClassLit() { TakeClass(class'Probe'); }
function BadClass() { TakeClass(class'Missing'); }
function bool EachRet() { foreach Iter(Slot) return true; }
function bool EachRetIf() { foreach Iter(Slot) { if (bDead) return true; } return false; }
function ObjSwitch() { switch (Target) { case Target: Foo(1); break; } }
function EmptySwitch() { switch (Target) { } }
function EachNested() { foreach Iter(Slot) foreach Iter(Slot) return true; }
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
                   "items": (0x01, 700, "array"), "fcount": (0x01, 640, "float")},
         "chain": {("probe", "probe"): ("", "")}, "pack": "probe", "cls": "probe",
         "locals": {"slot": (0x00, 611, "int"), "count": (0x00, 612, "byte")},
         "classrefs": {"probe": 4, "actor": -77},
         "funcs": {"destroy": ("native", 279), "myfinal": ("final", 763), "myvirtual": ("virtual", 39),
                   "foo": ("virtual", 41), "bar": ("virtual", 44), "baz": ("native", 705),
                   "mix": ("virtual", 46), "bump": ("virtual", 47), "gotostate": ("native", 113),
                   "tinytoken": ("native", 2), "onchange": ("virtual", 26),
                   "mix3": ("virtual", 48), "mixbyte": ("virtual", 49), "mytext": ("virtual", 50),
                   "ping": ("virtual", 51), "pong": ("virtual", 52), "say": ("virtual", 53),
                   "iter": ("native", 300), "takeclass": ("virtual", 55)},
         "delegates": {"onchange": 51}}

# The receiver walk's join: `Target` is a property of class `Probe`, so `Target.Health` is `Probe.Health`.
SCOPE["objclass"] = {("probe", "target"): "probe"}

# The declared dimension of an array property, which `ArrayCount` folds to (`var int Items[4];`).
SCOPE["dims"] = {"items": 4}

# The raw record table the receiver walk reads: `{name: [(index, owner, kind)]}`.
SCOPE["props_all"] = {nm: [(v[1], "probe", "ObjectProperty" if v[2] == "object" else
                            "BoolProperty" if v[2] == "bool" else
                            "StructProperty" if v[2] == "struct" else "IntProperty")]
                      for nm, v in SCOPE["props"].items()}

# The type words the package artifact spells: `SquadDir` is a `Vector`, and `bFlag` is a `bool` member.
SCOPE["friendly"] = {("probe", "probe", "squaddir"): "vector", ("probe", "probe", "bflag"): "bool",
                     ("probe", "probe", "health"): "int", ("probe", "probe", "bdead"): "bool",
                     ("probe", "probe", "flag2"): "bool", ("probe", "probe", "target"): "probe",
                     ("probe", "probe", "fcount"): "float"}

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
PARAMS[("probe", "pong")] = [("int", "A", False)]
PARAMS[("probe", "say")] = [("string", "S", False)]
# A `foreach`'s head is a call whose last argument is the loop variable, and a class literal is passed where a
# `class` is declared -- the two shapes the statement walk's new families emit.
PARAMS[("probe", "iter")] = [("int", "Item", False)]
PARAMS[("probe", "takeclass")] = [("class", "C", False)]
ENUMS = {("probe", "probe_walking"): (1, "EProbe"), ("probe", "probe_swimming"): (3, "EProbe")}
ENV = {"names": {"ping": 51, "pong": 52, "say": 53}, "scope": SCOPE, "params": PARAMS, "enums": ENUMS,
       "enum_names": {"eprobe"},
       "class_names": {"probe"}, "order": ["probe"],
       "rets": {("probe", "bump"): "int", ("probe", "mytext"): "string", ("probe", "say"): "string"},
       # the receiver walk's own join: a function *record* of the synthetic class, which a member call resolves on
       "funcs_all": {nm: [(1, "probe")] for nm in ("ping", "pong", "say")},
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
    "acount": "042c04040b47",                     # return ArrayCount(Items); -- the declared 4, an IntConstByte
    "acountset": "0f016e042c04040b47",            # Health = ArrayCount(Items); -- the same literal at an int site
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
    # Two statements are one after the other, with the body's own trailer once at the end: no separator is emitted.
    "sequence": "1b2926160f016e0426040b47",       # Foo(1); Health = 1;
    # A `local` declaration is a record the package declares, not code: `local int Extra;` writes nothing.
    "locals": "0f016e0426040b47",
    # `if (c) S;` is `07 <absolute end> cond S`: the operand is the end of S, a position in the uncompacted array.
    "ifstmt": "0710002d012d0f016e0426040b47",
    # `if/else` writes the else branch's position in the JumpIfNot and the end in the Jump between the two.
    "ifelse": "0713002d012d0f016e0426061b000f016e042c02040b47",
    # `while (c) S;` puts the condition at the top and jumps back with `06 <the loop's own start>`, and its
    # JumpIfNot names the position *after* that jump -- the end of the loop, not the jump itself.
    "whileloop": "0713002d012d0f016e0426060000040b47",
    "whilethen": "0713002d012d0f016e04260600000f016e042c02040b47",
    # `for (init; cond; step) S;`: the init, `07 <the position just past the loop>`, the condition, S, the step,
    # then `06 <the JumpIfNot's own position>` -- the back edge names the loop's top, not the condition's first byte.
    "forloop": "0f00630925072400960063092c02160f016e0426a500630916060700040b47",
    # `do S until (c);`: the body, `07 <the body's first byte>`, then the condition -- written *after* the jump.
    "doloop": "0f016e04260700002d012d040b47",
    # The same `for` with a compound assignment for its step: `Slot += 2` is AddEqual_IntInt, not Add_IntInt.
    "forcompound": "0f0063091dffffffff072a00960063092c0216"
                   "0f016e0426a10063092c0216060b00" "040b47",
    # The four increment forms are four opcodes: postfix/prefix on an int (0xa5/0xa3) and on a byte (0x8b/0x89).
    "incslot": "a500630916040b47",
    "preincslot": "a300630916040b47",
    "addtoslot": "a10063092c0216040b47",
    "subfromslot": "a20063092c0216040b47",
    # A call statement whose callee is a member of a receiver: `Context`, the skip word (`_wide` of the call), the
    # value size (0 for a void call) and the call. `Target.Say` returns `string`, so its statement carries `0e`.
    "callmember": "190163090600001b3316040b47",
    "callmemberarg": "190163090700001b342616040b47",
    "callmembertext": "0e190163090900001b351f780016040b47",
    # A one-operand node: `81 <operand> 16`. `bIsOn = ! bIsOn;` in the shipped bytes is
    # `14 2d 01 03 81 2d 01 03 16 04 0b 47`, and `-MyList.ItemsPerPage` in `GUIVertScrollBar.WheelUp` writes
    # `1b 2d 8f 19 01 30 05 00 04 01 19 16 16` -- `8f` (Subtract_PreInt) around the member read.
    "notflag": "04812d012d16040b47",
    "notset": "142d012d812d012d16040b47",
    "notcall": "0481190163090600042d012f16040b47",
    "negint": "048f016e0416040b47",
    # `&&` whose left operand is a `!` node: `82 <!bDead> 18 0700 <bFlag> 16`; the skip word is that node's wide
    # width plus one, exactly as `bSquadBeingAlerted && ! bSquadAlertComplete` writes `18 09 00` for an 8-wide node.
    "notand": "04" "82" "812d012d16" "180700" "2d012f" "16" "040b47",
    # A `foreach` is `Iterator <call> <wide offset of the IteratorPop>`, the body, `IteratorNext`, `IteratorPop`,
    # and the operand comes **after** the call: `Pickup.CheckTouching` writes
    # `2f 61 33 20 01 00 70 3d 16 1c 00 1b 2d 00 70 3d 16 31 30` for `foreach TouchingActors(class'Pawn', P)
    # Touch(P);`. Here the head is `Iter(Slot)` -- `61 2c` is the native token 300 -- and the body `Foo(1)` is 7
    # wide bytes, so the pop sits at 11 + 7 + 1 = 0x13.
    "eachplain": "2f612c00630916" "1300" "1b292616" "3130" "040b47",
    # A `break` leaves the loop with a bare `Jump` to that same position: `HUD.PostBeginPlay`'s
    # `2f 61 30 20 4f 01 4c 63 16 26 00 06 26 00 31 30` -- the `Jump` and the `Iterator` name one word.
    "eachstop": "2f612c00630916" "0f00" "060f00" "3130" "040b47",
    # A `continue` is the step plus that `Jump` (`TestInfo.TestContinueForEach`'s `31 06 4f 00`), so the arm is four
    # bytes and the `JumpIfNot` over it names the statement after it. The condition `bDead` is 6 wide bytes (its
    # `InstanceVariable` index widens), so the arm sits at 0x0b + 3 + 6 = 0x14 and the body is 20 wide bytes: the
    # `JumpIfNot` names 0x18 and the pop 0x20.
    "eachskip": "2f612c00630916" "2000" "07" "1800" "2d012d" "31" "062000" "1b292616" "3130" "040b47",
    # `class'Probe'` is an **object reference**: `ObjectConst` of the class's own index (SCOPE's classrefs says 4),
    # not a name constant -- `foreach TouchingActors(class'Pawn', P)` writes `20 01`, which is Engine.u's export 1.
    "classlit": "1b37200416040b47",
    # A `return` inside a `foreach` pops the iterator first: `30` then the `Return`, and the loop's own
    # `IteratorNext`/`IteratorPop` still follow the body. `Pawn.TouchingWaterVolume`'s arm is `30 04 27`, and here
    # the body is three wide bytes, so the pop sits at 11 + 3 + 1 = 0x0f.
    "eachret": "2f612c006309160f003004273130040b47",
    "eachretif": "2f612c0063091618000717002d012d30042731300428040b47",
    # `switch (Target)` on an object compares at width 4 (`InGameMenu.ButtonClick` writes `05 04`), and a `case`
    # value is an ordinary expression at that width, so `case Target:` is the property read again.
    "objswitch": "05040163090a19000163091b292616061c000affff040b47",
    # A `switch` with no `case` at all writes the terminator alone: `ExtrasPage.ButtonClick` is `05 04 <Sender>
    # 0a ff ff`, no case word to patch.
    "emptyswitch": "05040163090affff040b47",
}

# Bodies the emitter must refuse, each for a named reason rather than a guess.
REFUSE = ("compoundarg", "floatarg", "wrongargc", "nativestub", "callbadnative", "callmissing",
          "castnoslot", "opnoslot", "setlength", "incfloat", "callunknown", "acountunknown", "badclass",
          "eachnested")


def check(args):
    """Synthetic corpus plus the committed artifact's own consistency: no dump, no writes."""
    names = {"weapon_bone": 2063, "probe": 5, "ping": 51, "pong": 52, "say": 53}
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
    # `_array_dims` reads a declaration's dimension and not an index: a `var` with two declarators answers both,
    # an index in an initializer answers nothing, and a `local` inside a body counts too.
    dims = _array_dims("class P;\nvar string A[4], B[2];\nvar int C;\n"
                       "function F()\n{\n\tlocal int D[8];\n\tC = A[0] + 3;\n}\n")
    if dims != {"a": 4, "b": 2, "d": 8}:
        print("FAIL uscript-emit: _array_dims answers %r" % (dims,))
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
        sidx, _f, _p, _e, _en, _r, _lt, _pt, _w, _d, _c = _source_index(tmp)
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
    # P4: `Super.F()` names the *record* of F on the parent's chain -- a `FinalFunction` over an export or an
    # import, never the body's own name index. `Super.Ping()` with the parent declaring Ping as export 763 is
    # `1c 7b 0b 16`.
    super_env = dict(env)
    super_env["order"] = ["probe", "parent"]
    super_env["scope"] = dict(SCOPE, chain={("probe", "probe"): ("probe", "parent"),
                                            ("probe", "parent"): ("", "")})
    super_env["funcs_all"] = {"ping": [(763, "parent")]}
    super_env["func_owners"] = {"ping": [(763, "parent", "")]}
    super_env["imports"] = {}
    super_toks = [("ident", "Super"), ("op", "."), ("ident", "Ping"), ("op", "("), ("op", ")")]
    try:
        got, _m, _s = emit_stmt(super_toks, "", super_env)
    except Refused as exc:
        print("FAIL uscript-emit: Super.Ping() refused: %s" % exc)
        return 1
    if got.hex() != "1c7b0b16":
        print("FAIL uscript-emit: Super.Ping() emits %s, expected 1c7b0b16" % got.hex())
        return 1
    # P4b: inside a state the parent's *same-state* override is the record. `Super.Ping()` in state `follow`, where
    # the parent declares `Ping` at class scope (export 763) and again in state `follow` (export 764), is `1c 7c 0b`.
    state_env = dict(super_env)
    state_env["state"] = "follow"
    state_env["func_owners"] = {"ping": [(763, "parent", ""), (764, "parent", "follow")]}
    got, _m, _s = emit_stmt(super_toks, "", state_env)
    if got.hex() != "1c7c0b16":
        print("FAIL uscript-emit: Super.Ping() in a state emits %s, expected 1c7c0b16" % got.hex())
        return 1
    # P4c: the class-scope record wins when the parent has no same-state one.
    state_env["func_owners"] = {"ping": [(763, "parent", ""), (764, "parent", "other")]}
    got, _m, _s = emit_stmt(super_toks, "", state_env)
    if got.hex() != "1c7b0b16":
        print("FAIL uscript-emit: Super.Ping() in a state without an override emits %s" % got.hex())
        return 1
    # P5: a bare call in a class declared `within X` is a member of `Outer`, and a bare call the body's own chain
    # declares is not: the same synthetic class, one name that resolves only on the `within` class.
    within_env = dict(env)
    within_env.update({"within": "parent", "names": {"ping": 5, "foo": 41},
                       "funcs_all": {"ping": [(763, "parent")], "foo": [(41, "probe")]},
                       "func_owners": {"ping": [(763, "parent", "")], "foo": [(41, "probe", "")]},
                       "imports": {}, "natives": {}, "final_of": {}})
    within_env["scope"] = dict(SCOPE, outer_ref=-14)
    bare = [("ident", "Ping"), ("op", "("), ("op", ")")]
    try:
        got, _m, _s = emit_stmt(bare, "", within_env)
    except Refused as exc:
        print("FAIL uscript-emit: a `within` call refused: %s" % exc)
        return 1
    if got.hex() != "19018e0500001b0516":
        print("FAIL uscript-emit: a `within` call emits %s, expected 19018e0500001b0516" % got.hex())
        return 1
    own = [("ident", "Foo"), ("op", "("), ("number", "1"), ("op", ")")]
    got, _m, _s = emit_stmt(own, "", within_env)
    if got.hex() != "1b292616":
        print("FAIL uscript-emit: a bare call the chain declares was wrapped as `Outer` (%s)" % got.hex())
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
    if differ:
        print("FAIL uscript-emit: %d modelled body(ies) differ from the shipped bytes" % differ)
        return 1
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
    ap.add_argument("--only", metavar="PKG", help="score only these packages (comma separated). A debug aid -- the "
                                                  "artifact it writes is partial and must not be committed")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not args.tree or not args.dump:
        print("FAIL uscript-emit: --tree and --dump are required (the corpus and the dump stay outside the repo)")
        return 2
    only = {p.strip().lower() for p in args.only.split(",") if p.strip()} if args.only else None
    return score(args.tree, args.dump, args.out, args.residue_out, only)


if __name__ == "__main__":
    sys.exit(main())
