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
* **A vector literal is one token:** `23 <3 x f32>`, and a rotator literal is `22 <3 x i32>`.

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


# The refusal's own context: what the walk was looking at when it gave up. Cleared once per body, filled by the
# refusal site (and by `_class_of` when it answers nothing), and written into `--residue-out` beside the reason.
REFUSAL = {}


def _note(**kw):
    REFUSAL.update(kw)


def _no(why):
    """Record *why* `_class_of` found no class, then answer "" the way its callers expect."""
    REFUSAL["why"] = why
    return ""


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
STRUCT_TOKEN, CLASS_CONTEXT_TOKEN, CONTEXT_TOKEN = 0x36, 0x12, 0x19

# The record class a struct export carries. `Engine.FilePath` is export 2502 of class `Struct`, and its members
# (`Directory` 3006, `FullPath` 3007) are property records whose Outer is that export -- so a property's owner being
# one of these kinds is what makes it a *struct* member rather than a class member.
STRUCT_KINDS = {"Struct", "ScriptStruct"}

# The types a source cast can name as a primitive: `string(x)`/`int(x)`/`bool(x)`/`rotator(x)` are `PrimitiveCast`,
# and anything else in front of a parenthesised expression that names a class is a `DynamicCast`.
CAST_TYPES = ("byte", "int", "bool", "float", "string", "name", "vector", "rotator", "class")

# The type byte a `Context` node writes is the *member's* value size, measured on 31,622 nodes: 4 for the
# pointer-sized types, 1 for a byte, 12 for a vector and 0 for a string (`MyNumericEdit.Value` is a string and its
# node writes `05 00 00`, `Items.Next` is an object and writes `05 00 04`).
# The last two are measured on the shipped nodes, not summed: a string member's `Context` writes size 0
# (`MyNumericEdit.Value`, `Caption`), and an **array** member's writes 0 as well -- `MyList.Elements.Length` is
# `37 19 01 28 05 00 00 01 ...` and `source.modifiers.Length` `37 19 00 66 35 05 00 00 01 58 0f`, over every
# `array<T>` member read in the corpus. An array's element type lives in its own record, so the read yields
# nothing, not the element's width.
PROP_SIZE = {"bool": 4, "int": 4, "float": 4, "object": 4, "name": 4, "class": 4, "byte": 1, "vector": 12,
             "string": 0, "array": 0}

# A **struct-valued** member's size is not in `PROP_SIZE`: the record says `StructProperty` and the artifact's
# `friendly_type` names the struct. The three engine structs the corpus reaches that way are all 12 bytes, and the
# size byte is the same 12 a `vector` property writes -- `return Pawn.Location;` is `04 19 01 86 05 00 0c 01 8a`
# (`AmbientCreatures.TransientAmbientCreature.PickDestination`). A struct the artifact names but this table does not
# is still refused rather than given a size the corpus never wrote.
#
# The four the UI packages add are pinned by the bodies that refused for want of them, each a byte-identical match
# only at one width: `Color` is `byte B,G,R,A` (**4**) -- `HUD.DrawCustomBeacon`, `GUIMenuOption.InitComponent`;
# `Plane` is four floats (**16**) -- `HUD.PostRender`; `Region` is `UWindowBase`'s four ints `X,Y,W,H` (**16**) --
# `UWindowWindow.DrawUpBevel`, `UWindowWin95LookAndFeel.FW_SetupFrameButtons`; `MouseCursor` is `UWindowWindow`'s
# `Texture tex; int HotX, HotY; byte WindowsCursor` -- the fields sum to 13 and the compiler's slot is **16**, which
# is what every one of the corpus's 30 `MouseCursor` `Context` nodes writes (`Root.NormalCursor`,
# `MouseWindow.Cursor`; `tools/context-size.py`, `inventory/context_size.csv`). 13 was the fields' sum and made
# nine UWindow bodies differ at that one byte (`UWindowGridColumn.MouseMove`,
# `UWindowFramedWindow.MouseMove`, `UWindowConsoleClientWindow.Initialized`); `HTMLStyle` is **28**, which no field
# sum gives (`UWindowBase` declares an int, a
# string, two colors and seven bools) -- the width is the compiler's own static size and every read of an
# `HTMLStyle` property is `Context 05 00 1c` (`UWindowHTMLTextRow(L).StartStyle` in
# `UWindowHTMLTextArea.DrawTextLine` at offset `000d`, and seven more nodes across `GetWrapPos`, `RemoveWrap` and
# `WrapRow`). A width one byte either side of these puts the emitted tail out of step with the shipped body, so the
# match is the measurement. `PointRegion` is **12** -- its three fields sum to 9 (`Zone` pointer, `iLeaf` int,
# `ZoneNumber` byte), so only the node measures it: `PlayerReplicationInfo.UpdatePlayerLocation`'s
# `PlayerZone = P.Region.Zone;` is `36 41 0f 19 00 52 16 05 00 0c 01 78 0e`, the `Context` around `P.Region`
# writing `0c` (see the 7 bodies that refused as *a struct member declared pointregion*).
# Seven more widths, each from `inventory/context_size.csv`'s `not in table` rows: every `Context` node that
# reads the struct writes that byte and no other (`tools/context-size.py` -- `animrep` 12 over 4 nodes, `id3field`
# 36 over 3, `liveuser` 24 over 2, `orientation` 92 over 3, `range` 8 over 1, `rangevector` 24 over 13, `voicedesc`
# 64 over 8). A width here is the compiler's **stack slot**, the same byte a `Context` writes, not a field sum;
# each is already a multiple of 4, so the slot and the sum agree. The bodies that pin them are the ones that
# refused as *a struct member declared <name>*: `Controller.BeginState` (`animrep`), `Stream.GetSongTitle`
# (`id3field`), `PlayerController.WriteDisconnectEvent` (`liveuser`), `SceneManager.ResolveActorsInActions`
# (`orientation`), `Vehicle.SpectatorSpecialCalcView` (`range`), `SurfaceInfo.BulletImpact` (`rangevector`) and
# `SGVoicePackExtended.SearchVoice`/`StargateVoicePack.SelectVoice` (`voicedesc`).
STRUCT_SIZE = {"vector": 12, "rotator": 12, "coords": 12, "color": 4, "plane": 16, "region": 16,
               "mousecursor": 16, "htmlstyle": 28, "pointregion": 12,
               "animrep": 12, "id3field": 36, "liveuser": 24, "orientation": 92,
               "range": 8, "rangevector": 24, "voicedesc": 64, "interpcurve": 12}

def _value_width(ptype, friendly):
    """A declared type's value size: `PROP_SIZE` for a primitive, `STRUCT_SIZE` for a **struct**.

    The two tables had a lookup each, and the struct-member path used neither: it asked `PROP_SIZE` only, so a
    struct field whose own type is a struct (`A.B.C`, where `B` is a struct-valued member of struct `A`) was
    refused as *a struct member of type struct* even when `STRUCT_SIZE` carried the width -- 89 bodies at
    `main` 1d1ade0. The width is the same one a `Context` writes for the struct, so the same table answers it.
    """
    if ptype == "struct":
        return STRUCT_SIZE.get((friendly or "").lower())
    return PROP_SIZE.get(ptype)


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


def _int32(v):
    """An integer literal is an `int32` in the compiler: a value past the range wraps, it does not grow.

    `BestScore = -9999999999;` in `BotEnemy.PickDestination` ships `1e c817a8ce` -- the `FloatConst` of
    **-1410065408**, not of -9,999,999,999: the magnitude 9,999,999,999 wraps to `0x540be3ff` (1,410,065,407),
    the sign is applied to *that* int, and the int is then converted to `float`. `9999999999` without the sign
    (`BotEnemy.UpdateCoverNodes`' `HideNodeDistances[k] = 9999999999;`) is `1e c817a84e`, the same int positive.
    """
    u = v & 0xFFFFFFFF
    return u - 0x100000000 if u >= 0x80000000 else u


def _int_bytes(v):
    """The compiler's own token for an int constant: `25` zero, `26` one, `2c <byte>` 2..255, else `1d <int32>`."""
    v = _int32(v)
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

    The **literal text** is kept, not the number it parses to. A const is substituted at its site, and its own
    spelling says which type the site is: `const Pi = 3.1415926535897932;` on `Object` and `const MAXSTEPHEIGHT =
    35.0;` on `Actor` are floats, while `NUMTEAMS = 2` is an int. Keeping only the integers dropped every float
    const from every body's name table -- 56 operator rows named a const the walk could not type (`pi`,
    `MAXSTEPHEIGHT`, `SUPERPUNCH_RANGE`) -- and `str(3.1415926535897932)` is not the text the source wrote, so the
    value is re-emitted from the token rather than reformatted.
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
            out.setdefault(toks[i + 1][1].lower(), toks[j][1])
    return out


def _const_int(raw):
    """The whole number a const's literal text spells, or None -- an array dimension cannot be a float."""
    if not raw:
        return None
    try:
        v = _number(raw)
    except ValueError:
        return None
    return v if isinstance(v, int) and v >= 0 else None


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
                elif _const_int(consts.get(dim_tok[1].lower())) is not None:
                    out.setdefault(toks[k][1].lower(), _const_int(consts[dim_tok[1].lower()]))
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
                m = re.match(r"^.*?\boperator\((\d+)\)\s+([A-Za-z<>]+)\s+(\S+?)\s*"
                             r"\(\s*((?:out\s+|skip\s+|coerce\s+|optional\s+|const\s+)*)([A-Za-z<>]+)\s+\w+\s*,\s*"
                             r"((?:out\s+|skip\s+|coerce\s+|optional\s+|const\s+)*)([A-Za-z<>]+)\s+\w+\s*\)", decl)
                if m:
                    prec, ret, sym = int(m.group(1)), m.group(2).lower(), m.group(3)
                    a, b = m.group(5).lower(), m.group(7).lower()
                    if token.isdigit():
                        OPERATORS[(sym, a, b)] = (int(token), ret)
                    else:
                        # An operator declared with **no index** has no opcode at all, and the compiler writes an
                        # ordinary `FinalFunction` on the operator's own record -- the same node a native with no
                        # slot gets, not the token path. `Object.uc`'s
                        # `native static final operator(24) bool ClockwiseFrom( int A, int B )` is the one this
                        # corpus spells, and `ScriptedController.SetFireYaw`'s
                        # `if ( FireYaw ClockwiseFrom Rotation.Yaw )` is `1c c5 04 00 47 01 ... 16` -- a
                        # `FinalFunction` to import -261 (`ClockwiseFrom_IntInt`), which is why looking for an
                        # opcode could never find one.
                        OP_FN[(sym, a, b)] = ((r.get("composed") or "").strip(), ret)
                    OPERATOR_PREC.setdefault(sym, prec)
                    continue
                if not token.isdigit():
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
OP_SPELLING = {}
OP_FN = {}
ALL_CLASS_NAMES = set()
UNARY = {}
SOLE_ROWS = {}
SOLE_UNARY = {}


def _op_spelling():
    """`{lower-case spelling: the table's own spelling}` for the operators whose symbol is a **word**.

    The table spells three of them with a capital -- `static final operator(16) float Dot ( vector A, vector B )`,
    `Cross`, and `ClockwiseFrom` -- and the source spells them in whatever case the author used:
    `LookDir cross vect(0,0,1)`, `OffDir dot (Pawn.Location - MyManager.Location)`,
    `if ( FireYaw ClockwiseFrom Rotation.Yaw )`. The pair is a dictionary key, so `cross` never met `Cross` and 29
    bodies refused as *an operator the image's table does not install (vector cross vector)* while
    `Cross_VectorVector` (220), `Dot_VectorVector` (219) and `ClockwiseFrom` (316) sat in the table.
    """
    if not OP_SPELLING:
        for (sym, _a, _b) in _operators():
            if sym[:1].isalpha():
                OP_SPELLING.setdefault(sym.lower(), sym)
        # A no-index operator is not in `OPERATORS` at all, so the spelling map has to be seeded from its own table
        # too: `if ( yaw clockwisefrom other )` in a body whose author spelled it in lower case would otherwise look
        # up the pair under `clockwisefrom` and miss the `ClockwiseFrom` row.
        for (sym, _a, _b) in _operator_fn():
            if sym[:1].isalpha():
                OP_SPELLING.setdefault(sym.lower(), sym)
    return OP_SPELLING


def _operator_fn():
    """`{(symbol, A type, B type): the operator's own record name}` for the operators declared with **no index**.

    Three declarations in `inventory/native_operators.csv` spell no `native(NNN)` at all -- `ClockwiseFrom`,
    `string *` and `string *=` -- and a compiler given no slot writes an ordinary call to the record instead of an
    opcode (`ScriptedController.SetFireYaw`'s `1c c5 04`). The record's own name is the table's `composed` column
    (`ClockwiseFrom_IntInt`), which is also the name the package's import table carries.
    """
    _operators()
    return OP_FN


def _sole_rows():
    """`{symbol: (a, b)}` for every binary symbol the table installs exactly **one** row for.

    An operator's token is chosen by its symbol *and* its operand pair (`+` is `Add_IntInt`, `Add_FloatFloat` and
    `Add_VectorVector`), so a pair the walk cannot type has no answer -- unless the symbol has only one row, in
    which case the symbol alone is the answer. `&&`, `||` and `^^` are each declared once in all 18 packages
    (`native(130) static final operator(30) bool && ( bool A, skip bool B )`), so there is no other overload the
    compiler could pick and both operands are `bool` by the declaration itself. The walk types an operand from the
    receiver chain it reads; when that chain is not in the artifacts the side is unknown, and on a one-row symbol
    that unknown cannot change the row -- the body was refused for want of the only row there is.
    """
    if SOLE_ROWS:
        return SOLE_ROWS
    seen = {}
    for (sym, a, b) in _operators():
        seen.setdefault(sym, set()).add((a, b))
    for sym, pairs in seen.items():
        if len(pairs) == 1:
            SOLE_ROWS[sym] = next(iter(pairs))
    return SOLE_ROWS


def _sole_unary():
    """`{symbol: operand type}` for every one-operand symbol the table installs exactly one row for.

    `!` is `native(129) static final preoperator bool ! ( bool A )` and the corpus declares no second row, so its
    operand is `bool` by the declaration. `-` has three rows (`Subtract_PreInt`/`PreFloat`/`PreVector`) and is not
    in the table here.
    """
    if SOLE_UNARY:
        return SOLE_UNARY
    seen = {}
    for (sym, a) in UNARY:
        seen.setdefault(sym, set()).add(a)
    for sym, ops in seen.items():
        if len(ops) == 1:
            SOLE_UNARY[sym] = next(iter(ops))
    return SOLE_UNARY

# The compound-assignment spellings. A `+=` is **not** a `Let` around a `+`: its token is a separate native
# (`AddEqual_IntInt` 161 against `Add_IntInt` 146) and its first parameter is an `out`, so the row is chosen by the
# target's type first.
COMPOUND = ("+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")


def _operator_types(expr, env, site="", root=False, typing=False):
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
    # Every `class < Z > ( ... )` head in the expression is guarded, not only one at the head: its `<` and `>`
    # are the cast's spelling, and a scan that read them as comparisons split the expression at the `>`.
    guarded = set()
    for start, _close, end in _metacast_spans(expr):
        guarded.update(range(start, end + 1))
    for i, (kind, val) in enumerate(expr):
        if i in guarded:
            continue
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
            # spelled as an infix word: `FireYaw ClockwiseFrom Rotation.Yaw` -- and keyed by the spellings below,
            # which are the table's (`Dot`, `Cross`, `ClockwiseFrom`), not the source's
            sym = _op_spelling().get(val.lower(), val)
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
    # An operand whose declared type is an **enum's own name** is stored in a byte (`_enum_byte`), and the pair is
    # looked up on the promoted type like any other byte. `StargatePawn.PlayWaiting`'s
    # `Controller.GetAggroLevel() != AGGRO_High` refused as `(eaggrolevel != byte)` for want of that mapping, and
    # the same mapping is what `_operand` needs to write the `39 3a` (`ByteToInt`) around the call.
    t1, t2 = _enum_byte(t1, env), _enum_byte(t2, env)
    n1, n2 = _numeric(t1, env), _numeric(t2, env)
    # Only the **left** operand of a node carries the statement's type. The compiler hands its expected type to the
    # left child and types the right one from the left operand, so `CurvedStairBuilder`'s
    # `(65536.0f * (AngleOfCurve / 360.0f)) / NumSteps` narrows the outer group and leaves the inner one a
    # `Divide_FloatFloat` (`_group_narrow`).
    # ... but only where the node is being *emitted*. A group's own reading is its float type: `_type_of` asks
    # this table for an operand's type, and a narrowing here made the group read `int` to the node above it --
    # `HUD.DisplayMessages`' `XPos = (PosX * Scale * SizeX) + (((1 - Scale) / 2) * SizeX)` then took the
    # `(int, int)` row and cast both groups, where the shipped body keeps the `float` row (`Add_FloatFloat`) and
    # round-trips only the left group. `typing=True` is that distinction.
    if not typing and _group_narrow(left, env):
        t1, n1 = "int", "int"
    # A float **literal** whose value is an exact integer is typed `int` at an `int`-typed node: the constant is
    # narrowed to the site and the pair is looked up on `int` (`_integral_float` carries the three bodies).
    if (site or "").lower() == "int" and _integral_float(left) is not None:
        # The **leading** operand only: `SpiralStairBuilder`'s `(360.0f / NumStepsPer360)`, on the right of the same
        # expression, keeps its `FloatConst`, so the narrowing is the left spine's, like the int-literal fold's.
        t1, n1 = "int", "int"
    if not (t1 and t2):
        # A side the walk cannot type on a **one-row** symbol: `&&`/`||`/`^^` install one row each, so the symbol
        # alone decides the token and both operands are `bool`. 48 `bool && unknown`, 25 `unknown && unknown`, 15
        # `bool || unknown` and 12 `unknown && bool` bodies are refused on exactly this and nothing else the body
        # spells -- the row is not missing, the side is.
        sole = _sole_rows().get(sym)
        if sole == ("bool", "bool"):
            hit = _operators().get((sym, "bool", "bool"))
            if hit is not None:
                token, ret = hit
                return sym, "bool", "bool", ret, token, at
    # A **float site promotes both operands**, and the promotion is per operand: `W.WinHeight = 12 +
    # MiscBevelT[2].H + MiscBevelB[2].H` ships `Add_FloatFloat(FloatConst 12, IntToFloat(.H), IntToFloat(.H))`
    # where this walk wrote `IntToFloat(Add_IntInt(...))`. The table installs no `(int, int)` row a site could
    # promote, so the exact `(int, int)` row answered first and the `(float, float)` row was never reached. `root`
    # and a `float` site are the bounds: the narrowing branch below owns a root `int` site, and a nested node
    # keeps its own type.
    # A **divide** is the one pair the leaves rule does not bound: `paintTransparentBlob`'s
    # `tempFloat = (yloc) / image.VSize` has no literal and still ships `Divide_FloatFloat` with both operands
    # converted. The bound that does hold is the *group* rule: a parenthesised group compiled at the expression
    # root's `int` (`_group_narrow`) folds and promotes nothing, so `UWindowComboList.SetSelected`'s
    # `i = (Y - VBorder) / ItemHeight + VertSB.Pos` keeps `Divide_IntInt` behind an `IntToFloat`.
    if (root and (site or "").lower() == "float" and (env.get("_assign") or env.get("_promote_tree"))
            and (not env.get("_arg") or env.get("_promote_tree"))
            and sym in ("+", "-", "*", "/") and n1 == "int" and n2 == "int"
            and (_leaves_promotable(expr) or (sym == "/" and not _group_narrow(left, env)))):
        hit = _operators().get((sym, "float", "float"))
        if hit is not None:
            token, ret = hit
            return sym, "float", "float", ret, token, at
    # A **compound assignment on a byte** keeps the byte operator: the target's declared type picks the family and
    # the right operand converts *down* at its own site, where the expression rule promotes both sides and writes the
    # int operator instead (`_numeric` turns `byte` into `int` before the pair is looked up). `PlayerController.
    # CallServerMove`'s `PendingCompress += 2;` (a `byte` local) ships `87 00 67 0a 39 3d 2c 02 16` --
    # `AddEqual_ByteByte(byteTarget, IntToByte(2))` -- and the walk wrote `a1 39 3a 00 67 0a 2c 02 16`
    # (`AddEqual_IntInt(ByteToInt(target), 2)`), one byte longer; `Mover.KeyFrameReached`'s
    # `KeyNum += StepDirection` is the same word with an int property on the right (`87 01 3b 39 3d 01 43 15 16`).
    # The three byte rows the declaration table carries are `AddEqual_ByteByte` (135), `SubtractEqual_ByteByte`
    # (136) and `MultiplyEqual_ByteByte` (133), so the row is only taken when the table installs it.
    if sym in COMPOUND and t1 == "byte":
        hit = _operators().get((sym, "byte", "byte"))
        if hit is not None:
            token, ret = hit
            return sym, "byte", "byte", ret, token, at
    for a, b in ((n1, n2), (t1, t2)):
        hit = _operators().get((sym, a, b))
        if hit is not None:
            token, ret = hit
            return sym, a, b, ret, token, at
        fn = _operator_fn().get((sym, a, b))
        if fn is not None:
            # A no-index operator has no token at all; `_emit_operator` writes the record the table names, and the
            # result type is still the declaration's (`bool` for `ClockwiseFrom`).
            return sym, a, b, fn[1], None, at
    # `int` against `float`: the table installs the `(float, float)` row and the compiler wraps the int operand in
    # a `PrimitiveCast` -- `Pickup.BotDesireability`'s `RespawnTime < 10` is `b0 01 72 09 39 3f 2c 0a 16`, an
    # `IntConstByte 10` behind `IntToFloat`. That row is the row for a **literal** on the int side, because a
    # literal is retyped from the site.
    if (False and n1 == "float" and t2 == "int" and not _is_literal(right) and not _is_literal(left)
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
    if t.startswith("class.") or t.startswith("class<"):
        # A class reference is an object whatever spelling the artifacts carry it in: a local declared
        # `class<Info>` arrives as `class<info>`, and a property record spells `class.AIMessage`.
        # `Classes[i].Class != GetAIMsgClass(msgID)` compares two of them as `EqualEqual_ObjectObject`.
        return "object"
    if env and t and (t in (env.get("class_names") or set()) or t in _all_class_names()):
        return "object"
    if env and t:
        # A class the body's **own package imports** is a class even when no shipped package declares it. `Font`
        # and `Viewport` are the engine's native classes: `Engine.u` and `GUI.u` each carry
        # `Font <- Class <- Core` in their import tables, and the class census -- which is built from the export
        # tables -- has no row for either, so `HUD.LargerFontThan`'s `LoadFontStatic(i) != aFont` and
        # `GUIController.AddChildViewport`'s comparison refused as `(font == object)` / `(viewport == object)`
        # while `EqualEqual_ObjectObject` sat in the table. The import's own class field is the evidence, and it
        # is per package, so this answers only where the name is actually visible.
        if any(kind == "Class" for _ref, kind in (env.get("imports") or {}).get(t, [])):
            return "object"
    return t


def _all_class_names():
    """Every class name the corpus's class table carries -- not only the ones on the body's own chain.

    `_numeric` read a class-typed operand as an `object` only when the class sat on the body's chain, so
    `LoadFontStatic(i) == aFont` read `(font, object)` and `Viewport` in GUI read `(viewport, object)` as pairs the
    table does not install, while `EqualEqual_ObjectObject` -- the row the compiler used -- sat in it. A class name
    is an object reference wherever it is declared.
    """
    if not ALL_CLASS_NAMES:
        ALL_CLASS_NAMES.update(c for (_p, c) in _chain())
    return ALL_CLASS_NAMES


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
    return bool(expr) and expr[0][0] in ("ident", "keyword") and expr[0][1].lower() in ("vect", "rot")


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
    """`Context(Outer, 5, <size>) <read>` for a name the package exports once, on a class outside the body's chain.

    `PlayerInput.ChangeSnapView` writes `19 01 8e 05 00 04 2d 01 6c 28` where the text spells `bSnapToLevel` and the
    record (export 2604) is declared on `PlayerController`, which `PlayerInput` (extends `Object`) does not reach --
    so the compiled read goes through `Outer` (property import 14, `01 8e`). The word is 5 for every one of the 60
    `Outer.<bool>` nodes (a bare export bool writes 6).

    The size byte is the **value's** size, and it is the same node for a non-bool name **in a class declared
    `within X`**: `CheatManager.Amphibious`'s `Pawn.UnderwaterTime = +999999.0` opens
    `19 19 01 8e 05 00 04 01 05 05 00 04 01 77` -- `Context(Outer, 5, 4) Pawn`, then the member read -- and
    `Invisible`'s `Pawn.bHidden` is the same receiver. Outside a `within` class a non-bool name on another chain is
    read **bare** (the 27 names the fallback answers are), so it keeps that read and is not given a node the package
    does not write.
    """
    scope = env.get("scope") or {}
    wrap_any = low in (scope.get("within_props") or ()) and (env.get("within") or "")
    if low not in (scope.get("outer_props") or ()) and not wrap_any:
        return None
    size = PROP_SIZE.get(ptype)
    if scope.get("outer_ref") is None or size is None:
        raise Refused("a name this package declares only on another class's chain (the `Outer.<%s>` shape)"
                      % (ptype or "typeless"))
    outer = bytes([INSTANCE_TOKEN]) + write_index(scope["outer_ref"])
    return _converted(bytes([0x19]) + outer + struct.pack("<H", 5) + bytes([size]) + read, ptype, want)


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


def _static_at(expr):
    """The index of a **`.static`** link in a receiver chain, or None.

    `class'AIMessage'.static.Assign(msg, NewMsg)` reaches the walk as one chain whose last link is the keyword
    `static`: the `.static` names the class object itself, so the receiver a call resolves on is the chain *before*
    it and the node the call is written in is a `ClassContext` (`12`), not a `Context` (`19`) --
    `Speech.SetAIMsg` is `12 20 19 10 00 04 1c 56 07 ...`, `ObjectConst(19)` then the call.
    """
    at = _dot_at(expr)
    if at is not None and len(expr[at + 1:]) == 1 and expr[at + 1][0] in ("ident", "keyword") \
            and expr[at + 1][1].lower() == "static":
        return at
    return None


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
    """`array<export editinline ScriptEvent>` -> `scriptevent`; "" when the artifact does not spell one.

    The **local** walk spells a declaration with the tokens' own spaces (`local array<Interaction> A;` comes back
    `array < interaction >`), so the brackets are normalised before the shape test; the words inside keep their
    spaces, because `_ARRAY_MODS` reads them one at a time.
    """
    f = re.sub(r"\s*([<>])\s*", r"\1", (friendly or "").strip())
    if not (f.startswith("array<") and f.endswith(">")):
        return ""
    words = [w for w in f[6:-1].split() if w.lower() not in _ARRAY_MODS]
    return words[-1].lower() if words else ""


def _element_declared(declared, friendly, env):
    """The element type of a **static** array: the declared *class* for an object element, the kind otherwise.

    `var TransportRingMesh MyRings[NUM_RINGS]` is an `ObjectProperty`, so the record's kind spells `object` while
    `inventory/class_properties.csv`'s `friendly_type` spells the class. Answering the kind typed `MyRings[i]` on
    `Object`, whose own chain declares no `Destroy`: the callee's declared return was then empty and
    `TransportRings.Destroyed` wrote a `Context` size byte of **0** where the package writes Actor's `bool` (**4**)
    -- the last `context size` row in `inventory/emit_differ.csv`. `class<Z>` unwraps to `Z`, the rule `_class_of`
    already carries for a class reference, and a word the class table does not carry leaves the kind in place.
    """
    if (declared or "").lower() != "object":
        return declared
    word = (friendly or "").strip()
    named = (_class_ref_class(word) or word).lower()
    return named if named in (env.get("class_names") or set()) else declared


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
        # The element's class has three witnesses and the package record is only the first: `_friendly` reads
        # `class_properties.csv`, which holds **class properties**, and a local or a parameter is in neither table --
        # `local array<Interaction> InteractionArray;` is a dynamic array whose element is `Interaction`, and
        # `_record_kind` answers `array` for it while `_friendly` answers "". The element went untyped and
        # `InteractionArray[Index].bActive` was refused as "an element of an array the artifacts do not type" with
        # the class sitting in the same body's own `local` statement.
        word = ((env.get("localtypes") or {}).get(nm) or (env.get("paramtypes") or {}).get(nm) or "")
        el = _array_element(_friendly(nm, env)) or _array_element(word)
        if _record_kind(head, env) == "array" or word.strip().lower().startswith("array"):
            # a dynamic array, wherever the record or the declaration spells it: `10` (`DynamicArrayElement`)
            return 0x10, el
        declared = _declared_type(nm, env)
        if declared in ("", "array"):
            return None, ""
        return 0x1A, _element_declared(declared, _friendly(nm, env), env)
    at = _dot_at(head)
    if at is not None and len(head[at + 1:]) == 1 and head[at + 1][0] in ("ident", "keyword"):
        # The member's own name is the token's **text** -- the first version read `head[at + 1][0][1]`, the
        # second character of the kind (`"ident"[1]` is `"d"`), so every member read asked the package for a
        # property called `d` and refused as *an element of an array the artifacts do not type* even when
        # `inventory/class_properties.csv` carried the array's element type.
        obj = head[:at]
        nm = head[at + 1][1].lower()
        hit = None
        if len(obj) == 1 and obj[0][0] in ("ident", "keyword") and obj[0][1].lower() == "default":
            # `Default.X` / `default.X` is the class default object's own record, read on **this** body's chain
            # -- the rule `_emit_member` already writes with `DefaultVariable` (0x02).
            hit = _prop_by_name(nm, env)
        else:
            chain_at = _dot_at(obj)
            if (chain_at is not None and len(obj[chain_at + 1:]) == 1
                    and obj[chain_at + 1][0] in ("ident", "keyword")
                    and obj[chain_at + 1][1].lower() == "default"):
                # `X.Default.Y`: the member is read on X's class, the same receiver `_emit_member` resolves for
                # a `ClassContext` default chain.
                hit = _member_record(obj[:chain_at], nm, env)
            else:
                hit = _member_record(obj, nm, env)
        if hit is None:
            return None, ""
        kind, friendly = hit[2], (hit[3] if len(hit) > 3 else "") or ""
        if kind == "array":
            return 0x10, _array_element(friendly)
        declared = friendly if kind == "struct" else _element_declared(kind, friendly, env)
        return (0x1A, declared) if declared else (None, "")
    if _element_at(head) is not None:
        # a nested element (`A[B[i]][j]`): its elements are the innermost array's
        return _element_type(head[:_element_at(head)], env)
    return None, ""


def _element_dim(head, env):
    """The declared dimension of the **static** array `head` names, `None` when no declaration carries one.

    A subscript on a member whose record is not an array is a static array -- a dynamic one is a `UArrayProperty`
    and `_element_type` sends it down `0x10` -- so the dimension comes from the corpus's own `var` line, read per
    receiver class: `StargatePawn` declares `var name RegularMovementAnims[7]` and `StargateWeapon` `[2]`, and a
    body in the former reading `StargateWeapon(Weapon).RegularMovementAnims[1]` must take the latter's. A read on
    *self* needs no dimension: the compiler writes no `Context` for it (`BaseVisible[BaseNum] = 1;` is the bare
    `1A <index> 01 <record>`), so a bare name answers 0.
    """
    head = _strip_parens(head)
    at = _dot_at(head)
    if at is None or len(head[at + 1:]) != 1 or head[at + 1][0] not in ("ident", "keyword"):
        return 0
    nm = head[at + 1][1].lower()
    recv = head[:at]
    chain_at = _dot_at(recv)
    if (chain_at is not None and len(recv[chain_at + 1:]) == 1 and recv[chain_at + 1][0] in ("ident", "keyword")
            and recv[chain_at + 1][1].lower() == "default"):
        recv = recv[:chain_at]
    cls = (_class_of(recv, env) or "").lower()
    if not cls:
        return None
    for _p, anc in _ancestors(env["scope"].get("chain") or {}, "", cls):
        dim = ((env.get("dims_by_cls") or {}).get(anc) or {}).get(nm)
        if dim:
            return dim
    return None


def _emit_element(head, index, rettype, env):
    """`X[i]` -> `10`/`1A`, the index, the array read; then the element converts at its own site."""
    token, etype = _element_type(head, env)
    if token is None:
        raise Refused("an element of an array the artifacts do not type")
    # This node is the only place that knows the read is being subscripted, so it is the only place that can hand
    # `_emit_member` the dimension the `Context` size byte needs. A **dynamic** array (`0x10`, a `UArrayProperty`)
    # writes 0 and passes nothing; a static one passes its declared dimension, or -1 for one no declaration spells.
    dim = _element_dim(head, env) if token == 0x1A else 0
    arrb = emit_expr(head, "", env, element_dim=-1 if (token == 0x1A and dim is None) else (dim or 0))[0]
    # The subscript is an **int** site whatever the array holds: a literal index compiles as an int literal and
    # not as the element's type -- `TransportPlatform.Activated`'s `Skins[1] = ActiveTexture;` writes `10 26 01`
    # (IntOne) and `StoryModeGame.GetBotTeam`'s `return Teams[0];` writes `1a 25 ...` (IntZero), and against the
    # element's own type 185 bodies refused as `a numeric literal in a typeless function`. A **named** byte index
    # converts at that site too: `Mover.SetKeyframe`'s `KeyPos[KeyNum] = NewLocation;` ships
    # `1a 39 3a 01 3b 01 6e 36 00` -- a `ByteToInt` around the `byte KeyNum` -- and `Mover.BeginPlay`'s
    # `KeyPos[KeyNum] = ...` writes the same two bytes in a body that differed by exactly them. An int index
    # is a no-op through `_converted`, so every body that already matched stays byte-identical.
    idxb = emit_expr(index, "int", env)[0]
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


def _enum_byte(t, env):
    """An **enum's own name** is its storage: a `byte`.

    `var EFileType T` and `function EAggroLevel GetAggroLevel()` declare types that are neither a builtin nor a
    class, and the value they carry is a byte. `ConvertToFileType(Ext) != FILE_None` is `NotEqual_IntInt` with
    **both** operands wrapped in `39 3a` (`ByteToInt`) -- 182 bytes against the 178 a pair-only reading wrote, and
    the missing four were the two casts -- and an enum *constant* already answers `byte` (`_emit_enum`).

    A declaration may spell the enum **qualified by the class that declares it** -- `StargateVoicePack`'s
    `function Actor.EMsgTone GetVoiceTone()` -- and the value is still the byte: the qualified spelling read as an
    unknown type, which `AIMSG_Stargate.AutoBuildTone` (`GetSuitableMsgTone()`) and
    `SGVoicePackExtended.BuildCustomVoiceDesc` (`GetVoiceCustomize()`) each refused on.

    Kept in one place because three producers report a declared type -- a property/local/parameter record, a
    call's return type, and a callee's -- and a pair looked up on the name alone writes a body four bytes short,
    which is a silent difference rather than a refusal.
    """
    if not (t and env):
        return t
    low = t.lower()
    names = env.get("enum_names") or set()
    if low in names:
        return "byte"
    head, dot, tail = low.partition(".")
    if dot and tail in names and head in (env.get("class_names") or set()):
        return "byte"
    return t


def _declared_type(nm, env):
    """A read's declared type, with an enum's own name answering the byte it is stored in (`_enum_byte`)."""
    return _enum_byte(_declared_type_record(nm, env), env)


def _declared_type_record(nm, env):
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
        if ptype:
            return ptype
        # A **parameter** has no row in either table: its declared type is in the body's own signature, which
        # `paramtypes` carries, and `localtypes` carries it too when the class declares the name once.
        # `Controller.WarnTarget(FireProperties FiredAmmunition, ...)` reads
        # `FiredAmmunition.ProjectileClass.Default.Speed`, and `FireProperties.ProjectileClass`'s declared type is
        # the only thing that names the class that chain ends on -- answering "" refused it as "an unnamed class".
        return (env.get("paramtypes") or {}).get(nm) or (env.get("localtypes") or {}).get(nm) or ptype
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
                # A `const NAME = <literal>;` is substituted by the compiler, so the site carries the literal's own
                # type: `MAX_CORRECT_MOVER < 5` is `Less_IntInt` because the const is a whole number, while
                # `MAXSTEPHEIGHT + 14` and a bare `pi` are floats -- the 56 operator rows whose unknown side was a
                # const on an ancestor class were all float consts, which the table did not hold at all.
                return "float" if isinstance(_number(scope["consts"][low]), float) else "int"
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
    mc = _metacast(expr, env)
    if mc is not None:
        # `class<Z>(x)` is a class reference: `_numeric` reads it as the `object` row, which is the pair the
        # compiler compares with (`LocalMessage.GetString`'s `class<Actor>(OptionalObject) != None` is `77`).
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
        if low == "rot":
            # `rot(pitch, yaw, roll)` is the compiler's own struct literal for a rotator, exactly as `vect` is
            # for a vector: the constant components fold into `22` + three i32, so the site's own type is a
            # rotator and not a call's return type (`Spawn(..., rot(0,0,0))` in a `rotator` slot converts to
            # nothing).
            return "rotator"
        for cls in env["order"]:
            hit = (env.get("rets") or {}).get((cls, low))
            if hit:
                return _enum_byte(hit.lower(), env)
        return ""
    op = _operator_types(expr, env, typing=True)
    if op is not None:
        # an operator's own result type: `DirtyMask & 2` is an `int`, which is what `bool(...)` casts *from*
        if op[3]:
            return op[3]
        if (op[0] in ("==", "!=") and op[1] and op[1] == op[2]
                and op[1] in (env.get("scope") or {}).get("structrefs", {})):
            # a struct compare (`StructCmpEq`/`StructCmpNe`) yields a bool; the pair table has no row for it
            return "bool"
        return ""
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
            obj = expr[:at]
            if len(obj) == 1 and obj[0][0] in ("ident", "keyword") \
                    and obj[0][1].lower() in ("static", "global"):
                # `Static.F()` / `Global.F()`: the prefix names the class scope, so the call resolves on the body's
                # own chain exactly as a bare `F()` does (`_prefix_call`). Without the type the operator above it
                # stayed an uninstalled pair -- `GameInfo.AllowMutator`'s
                # `!Default.bAllowVehicles && static.IsVehicleMutator(MutatorClassName)` is `AndAnd_BoolBool`
                # (`21`), and `GameInfo.AddMutator`'s `!Static.AllowMutator(MutName)` is a `Not_PreBool`.
                return _callee_return_type(member[0][1], env)
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
            if len(obj) == 1 and obj[0][0] in ("ident", "keyword"):
                # a qualified enum constant is a `byte`, exactly as the bare spelling is (see `_emit_member`)
                if (env.get("enum_consts") or {}).get(obj[0][1].lower(), {}).get(nm) is not None:
                    return "byte"
            # The member record is chosen by the receiver's *record kind*, exactly as `_emit_member` chooses
            # its node: `local Color C; C.R = R;` is a `color` by its own statement and a `StructMember` record
            # in the package, and the record is what names `R`. Testing the declared word here instead refused
            # 42 bodies the walk already modelled -- `Canvas.SetDrawColor` is the first of them.
            objkind = _receiver_kind(obj, env)
            chain_at = _dot_at(obj)
            default_chain = (chain_at is not None and len(obj[chain_at + 1:]) == 1
                             and obj[chain_at + 1][0] in ("ident", "keyword")
                             and obj[chain_at + 1][1].lower() == "default")
            if default_chain:
                # `AmmoName.Default.MaxAmmo` is a member of the **class of `AmmoName`**, read off its default
                # object (`12 <obj> 05 00 04 02 <idx>`), so the record is that class's, not the body's.
                hit = _member_record(obj[:chain_at], nm, env)
            elif objkind == "struct":
                # A struct member's record is keyed by (struct, member); a class-property lookup by name cannot
                # find it, and that miss left `Dest.Z` (float), `Rotation.Pitch` (int) and every operator over
                # them untyped -- the head of the operator residue (353 rows). The struct's own record is asked
                # **first**, exactly as `_emit_member` now does: a name the class also spells (`Region.X`) is the
                # field, and reading the property's type here types the operator over it wrong.
                hit = _struct_member(obj, nm, env) or _prop_by_name(nm, env)
            else:
                hit = _member_record(obj, nm, env)
            if hit is not None:
                if hit[2] == "struct" and len(hit) > 3 and hit[3]:
                    return hit[3]
                if hit[2] == "object" and len(hit) > 3 and (hit[3] or "").lower() in (env.get("class_names") or set()):
                    # the declared **class** of an object property, not the word `object`: `Controller.Pawn` is a
                    # `Pawn`, and a further link (`.bWaterVolume` on a `NewVolume`) needs the class, not the kind
                    return hit[3].lower()
                return hit[2]
    return ""


def _receiver_kind(expr, env):
    """The *kind* of a receiver expression: the package's own record kind, else the declared type word.

    `_record_kind` answers only for a bare name, so every receiver that is a **chain** or a **call** fell through
    to its declared type word -- and `_emit_member` (and the chain branch of `_type_of`) compares that word to
    `struct` to choose the `(struct, member)` record. A struct receiver's word is the struct's name (`color`,
    `pointregion`, `coords`, `rotator`), never `struct`, so the struct's own record was never asked and the member
    read refused on the receiver's class: `Canvas.DrawColor.B`, `Pawn.SimAnim.AnimRate`,
    `GetBoneCoords('FireStartPos').Origin`, `rotator(HackingPawn.Location).Yaw`.

    The set of struct names is the corpus's own `struct` declarations (`inventory/struct_members.csv`, the same
    artifact `_struct_member` reads), and a word that names a **class** is never read as one: a class name reaches
    `_member_record`, which is the join that already answers for a class-typed receiver.
    """
    kind = _record_kind(expr, env)
    if kind:
        return kind
    word = _type_of(expr, env).lower()
    if word and word not in (env.get("class_names") or set()) and word in _struct_names():
        return "struct"
    return word


STRUCT_NAMES = set()


def _struct_names():
    """The struct names the corpus declares -- the first column of `inventory/struct_members.csv`."""
    if not STRUCT_NAMES:
        STRUCT_NAMES.update(name for name, _member in _structs())
    return STRUCT_NAMES


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
    # An enum's own name is the byte it is stored in (`_enum_byte`), and the cast is keyed on the *storage* type:
    # `byte` promoted to `int` is `39 3a`, so `GetAggroLevel() != AGGRO_High` writes the call behind `ByteToInt`
    # exactly as the enum-typed read does. Without this the key is the enum's name and no slot exists.
    own = _enum_byte(own, env)
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
    v = _int32(v)                      # the literal is an int32 before the sign and the float conversion
    return bytes([0x1E]) + struct.pack("<f", float(-v if neg else v))


def _integral_float(expr):
    """The int an expression is a float literal of, when the value is an exact integer -- else None.

    The compiler types a **constant** from the site: `RotStep.Yaw = 65536.0f / (NumSheets * 2)` in
    `VolumetricBuilder.BuildVolumetric` ships `91 1d 00000100 90 <local> 2c 02 16` -- `Divide_IntInt(IntConst
    65536, Multiply_IntInt(...))`, the float literal narrowed to the `int` the site is, and then the pair table
    taking its `(int, int)` row. In the same build `65536.0` at a **float** site stays a `FloatConst`
    (`Pawn.Tool_AngleLinearInterpolate`'s `Abs(currentAngle - targetAngle) > 32768.0`, whose body emits
    identically), so the narrowing is the site's, not the value's.
    """
    expr = _strip_parens(expr)
    neg = False
    if len(expr) == 2 and expr[0] == ("op", "-") and expr[1][0] == "number":
        expr, neg = [expr[1]], True
    if len(expr) != 1 or expr[0][0] != "number":
        return None
    try:
        v = _number(expr[0][1])
    except ValueError:
        return None
    if not isinstance(v, float) or not v.is_integer():
        return None
    return int(-v if neg else v)


def _narrowed_const(part, want, site=""):
    """An integral float literal at an `int`-typed node, written as the int constant the site narrows it to.

    The literal is *emitted* through this shape, not only typed by it: `_operand`'s form for a literal whose own
    type differs from the want is the cast, and the cast is what the package does not write. Where the operator's
    own slot is `float` the narrowing is followed by the `IntToFloat` back (`_converted`), which is exactly
    `393f 1d 00000100` under the `Multiply_FloatFloat` of `CurvedStairBuilder`.
    """
    if (site or "").lower() != "int":
        return None
    v = _integral_float(part)
    if v is None:
        return None
    return _converted(_int_bytes(v), "int", want)


def _leading_literal(expr):
    """The token run in front of a parenthesised group's first operator, if it is a numeric literal, else None.

    `(65536.0f * (AngleOfCurve / 360.0f))` leads with the literal; `(Y - VBorder)` leads with an identifier and is
    not narrowable, which is what keeps `UWindowPulldownMenu.SetSelected`'s shipped `FloatToInt(Y - ...)` shape.
    """
    core = _strip_parens(expr)
    if not core:
        return None
    neg = [(core[0][0], core[0][1])] if core[0][0] in ("number",) else None
    if neg is None and len(core) > 1 and core[0] == ("op", "-") and core[1][0] == "number":
        neg = [core[0], core[1]]
    return neg


def _group_narrow(part, env):
    """True when a parenthesised float group is compiled at the expression root's `int` type.

    `Row = ((Y - 12) / RowHeight) + TopRow` in `UWindowGridColumn.Click` ships
    `Add_IntInt(FloatToInt(Divide_FloatFloat(IntToFloat(FloatToInt(Subtract_FloatFloat(Y, IntToFloat(IntConstByte
    12)))), RowHeight)), TopRow)`: the group `(Y - 12)` is typed **int** -- the type of the expression's own root, not
    the type of the node it sits under -- and then widened back where the row it feeds is `float`.
    `UWindowPulldownMenu.SetSelected`'s `(Y - VBorder) / ItemHeight` is the same group with an `int` right operand,
    so it pays only the narrowing; `Binoculars.CheckPawns`' `(float(Pawn(Other).Health)) / float(...)` is a group
    with no operator inside and reads the same way; `Pawn.BaseChange`'s `(1 - Velocity.Z / 400)` narrows around a
    float subtraction two nodes above its own, which is why its `1` is `IntToFloat(IntOne)` and not a folded
    `FloatConst 1.0` -- a group compiled at `int` folds nothing.
    """
    if (env.get("root_site") or "").lower() != "int" or not _paren_wrapped(part):
        return False
    return (_type_of(_strip_parens(part), env) or "").lower() == "float"


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


def _leaves_promotable(expr):
    """True when an `int` chain at a `float` site is **retyped** root and branch instead of cast as a whole.

    Two measured bounds decide it, both about the chain's leaves rather than about the site:

    * A **call's `int` result** anywhere in the chain keeps the whole chain `int` and casts its result:
      `NewValue = Int(TempF + 0.5) * Step;` ships `IntToFloat(Multiply_IntInt(...))` although both operands are
      `int` and the target is `float` -- `UWindowHSliderControl.CheckValue`, `CrystalTunnel.RandomVect` (twice,
      through `CrystalTunnelLayer`), `moTextOption.GetAdjustedSize`, `DestructableActor.TakeDamage` and
      `GoaAutoTurretMK2Pawn.UpdateAimingRotation` all cast the chain's *result* for that reason. A call is a word
      immediately followed by `(`, which is also how a meta-cast is spelled (`Int(...)`, `int(...)`).
    * A chain of **pure reads** is not retyped either: `deltaX = InactiveOffsetX - ViewOffsetX;` into a `float`
      local and `desiredPitchOffset = newRot.Pitch - Rotation.pitch;` into a `float` property ship
      `IntToFloat(Subtract_IntInt(...))`. A **literal** is what makes the compiler retype: it folds the literal to a
      `FloatConst` (`W.WinHeight = 12 + MiscBevelT[2].H + MiscBevelB[2].H` is `Add_FloatFloat` all the way down),
      and with one operand `float` the pair takes the float row and every other leaf converts at its own read.

    An array subscript, a struct member and a parenthesised group are none of them a call.
    """
    literal = False
    for i, (kind, val) in enumerate(expr):
        if kind == "number":
            literal = True
        elif kind == "op" and val == "(" and i and expr[i - 1][0] in ("ident", "keyword"):
            return False
    return literal


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
    head = None
    if not token:
        fn = _operator_fn().get((sym, t1, t2)) or _operator_fn().get(
            (sym, _numeric(t1, env), _numeric(t2, env)))
        if fn:
            # An operator declared with **no index** is not a native the VM dispatches by opcode: the compiler
            # writes a `FinalFunction` on the operator's own record, and the record is an **import** when the
            # declaring class lives in another package -- `ScriptedController.SetFireYaw`'s
            # `if ( FireYaw ClockwiseFrom Rotation.Yaw )` is `1c c5 04` + the two operands + `16`, i.e.
            # `FinalFunction -261` -> `ClockwiseFrom_IntInt` in `GamePlay.u`'s import table. The same head shape a
            # `Super.F()` value writes for a parent's record, and the tail below is the ordinary operator node.
            ref = next((r for r, kind in (env.get("imports") or {}).get(fn[0].lower(), []) if kind == "Function"),
                       None)
            if ref is not None:
                head = bytes([0x1C]) + write_index(ref)
    if not token and head is None:
        left, right = expr[:at], expr[at + 1:]
        if sym in ("==", "!=") and t1 and t1 == t2:
            # A **struct comparison** is its own node: `0x33`/`0x32` (`StructCmpNe`/`StructCmpEq`), the struct's
            # object reference, the two operands, and no `16`. The corpus carries six of them and all six name a
            # `Struct` (`Color` -12 in UWindow, `IpAddr`, `VoiceRef` 160 four times); the operator table installs no
            # `(color, color)` row -- its four color rows are `-`, `*`, `+`, `*` -- so this is the fallback the
            # compiler takes when the pair table has nothing.
            ref = (env["scope"] or {}).get("structrefs", {}).get(t1)
            if ref is not None:
                node = (bytes([0x33 if sym == "!=" else 0x32]) + write_index(ref)
                        + emit_expr(left, "", env)[0] + emit_expr(right, "", env)[0])
                return node, "struct-cmp"
        if os.environ.get("SG1_DBG_OP"):
            raise Refused("an operator the image's table does not install (%s %s %s) <<%s | %s>>"
                          % (t1 or "an unknown type", sym, t2 or "an unknown type",
                             " ".join(v for _k, v in left), " ".join(v for _k, v in right)))
        raise Refused("an operator the image's table does not install (%s %s %s)"
                      % (t1 or "an unknown type", sym, t2 or "an unknown type"))
    left, right = expr[:at], expr[at + 1:]
    # **Which operand the site converts, and how.** A mixed pair (`int` against `float`) converts the `int`
    # *subtree*: `ConeBuilder.BuildCone`'s `sin((2 * i + Ofs) * pi / Sides)` ships
    # `Multiply_FloatFloat(Add_FloatFloat(Multiply_FloatFloat(FloatConst 2, IntToFloat(i)), IntToFloat(Ofs)),
    # FloatConst pi)` where this walk wrote `IntToFloat(Add_IntInt(Multiply_IntInt(2, i), Ofs))`. A pair the
    # **site** promoted (both sides `int`) instead casts each operand, and a **parenthesised** operand keeps its
    # own type: `tempFloat = (xloc - 1) / image.USize` is
    # `Divide_FloatFloat(IntToFloat(Subtract_IntInt(xloc, 1)), IntToFloat(USize))`. `promote` names the sides whose
    # own int arithmetic is re-emitted at `float` instead of wrapped.
    # The shipped bytes decide it by the operand's **kind**: a chain all of whose leaves are variables,
    # properties or literals is retyped root and branch, and a chain with a **call's** `int` result anywhere is
    # cast as a whole (`IntToFloat(Multiply_IntInt(Int(TempF + 0.5), Step))`). `_leaves_promotable` is that
    # bound, and it is asked at the pair table and here, so both the node's own row and the operands agree.
    promote = {"l": False, "r": False}
    if (rettype or "").lower() == "float" and sym in ("+", "-", "*", "/"):
        own = {"l": _numeric(_enum_byte(_type_of(left, env).lower(), env), env),
               "r": _numeric(_enum_byte(_type_of(right, env).lower(), env), env)}
        if {own["l"], own["r"]} == {"int", "float"}:
            side = "l" if own["l"] == "int" else "r"
            promote[side] = ((t1 if side == "l" else t2) == "float"
                             and _leaves_promotable(left if side == "l" else right))
        elif own["l"] == own["r"] == "int":
            # The **site's** promotion, which the pair table has to have widened: a call's argument keeps its int
            # arithmetic and casts at the argument's own site (`Abs(BlendC - NewC)` and `FRand()-style` graders are
            # `IntToFloat(Subtract_IntInt(...))`), and so does a parenthesised group.
            promote["l"] = t1 == "float" and not _paren_wrapped(left) and _leaves_promotable(left)
            promote["r"] = t2 == "float" and not _paren_wrapped(right) and _leaves_promotable(right)
    left, right = expr[:at], expr[at + 1:]
    if _is_literal(left) and _is_literal(right):
        raise Refused("an operator whose two operands are literals, which the compiler folds")
    if head is not None:
        pass                                   # a no-index operator's `FinalFunction` record, set above
    elif 0x70 <= token <= 0xFF:
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
    inner = dict(env, byte_root=True) if (rettype or "").lower() == "byte" else dict(env)
    # The **node's own type**, replaced at every child: an integral float literal under an `int` node is written as
    # the `int` constant the site narrows it to, and takes an `IntToFloat` back where the operator's slot is float
    # (`(65536.0f * (AngleOfCurve / 360.0f)) / NumSteps` is `393f 1d 00000100` under `Multiply_FloatFloat`). It is
    # replaced rather than inherited because a float-typed group inside the same statement keeps its `360.0f` as a
    # `FloatConst` -- `SpiralStairBuilder`'s `(360.0f / NumStepsPer360)` is one node in, and inheriting the flag read
    # that literal as an int and cost four bytes.
    inner["int_site"] = (rettype or "").lower() == "int"
    # A byte-typed site types a literal before the cast table is asked (`39 3c 24 a0`), and an int literal at a
    # `float` site folds into a `FloatConst` in exactly two measured positions (see `_float_literal`); both are
    # tried before `_operand`, whose form for a literal is the cast.
    # The **leading** operand of the expression keeps the root, so `(640 / RangeToTarget) * 85` folds the 640 two
    # nodes in; an operand to the right of an operator does not (`5` of `10 + 5 * FRand()` casts).
    left_env = dict(inner, _root=True) if root else inner
    # A right operand carries no site of its own: it is typed from the left operand, so a parenthesised group in that
    # position is its own `float` (`Vehicle.HealDamage`'s `Min(Health + (Amount * LinkHealMult), HealthMax)` promotes
    # the pair to `float` and narrows only at the argument's boundary).
    right_env = {k: v for k, v in inner.items()
                  if k not in ("root_site", "int_site", "group_int")}

    def _side(part, want, side_env, do_promote, folded, narrow=True):
        """One operand: a promoted subtree, else the literal forms, else the cast, else a plain emit."""
        if narrow and _group_narrow(part, env):
            # The group's **result** is the root's type; its own arithmetic keeps the type it has. So the group is
            # emitted at its own `float`, narrowed with the `FloatToInt` at its boundary, and widened back where a
            # `float` consumer reads it -- the `IntToFloat(FloatToInt(...))` round trip four bodies ship. Emitting
            # the group at `int` retypes its **inner** nodes too, which is not what the package writes:
            # `UWindowPulldownMenu.SetSelected`'s `(Y - VBorder)` is `FloatToInt(Subtract_FloatFloat(Y,
            # IntToFloat(VBorder))` and not an `Subtract_IntInt`, and twelve bodies were lost to that read.
            try:
                core = emit_expr(_strip_parens(part), "float", dict(side_env, _root=False, group_int=True))[0]
                return _converted(_converted(core, "float", "int"), "int", want)
            except Refused:
                pass
        if ((want or "").lower() == "int" and (rettype or "").lower() == "int" and _paren_wrapped(part)
                and _leading_literal(part) is not None):
            # A group the site narrows keeps its leading literal int-narrowed (`CurvedStairBuilder`); the group's own
            # children see the narrowing, and a group that does not lead with a literal keeps the cast form.
            try:
                return emit_expr(_strip_parens(part), "int", dict(side_env, _root=True))[0]
            except Refused:
                pass
        if do_promote:
            try:
                return emit_expr(part, "float", dict(side_env, _promote_tree=True))[0]
            except Refused:
                pass                       # not a subtree this walk can promote -- the cast shape is the fallback
        return (_byte_literal(part, want, rettype, inner)
                or folded
                or _operand(part, want, rettype, env, side_env)
                or emit_expr(part, want, side_env)[0])

    # A group compiled at the root's `int` folds nothing, so its leading literal keeps its int encoding and is
    # widened at the node it sits in (`Pawn.BaseChange`'s `-2` is `IntToFloat(IntConst -2)`, not a `FloatConst`).
    # A group compiled at the root's `int` folds nothing, so its leading literal keeps its int encoding and is
    # widened at the node it sits in (`Pawn.BaseChange`'s `-2` is `IntToFloat(IntConst -2)`, not a `FloatConst`).
    # The fold is the **expression root's** rule and not the node's own site: the node's arithmetic can be float
    # while the root is the `int Damage` parameter (`Base.TakeDamage((-2 * Mass / decorMass * Velocity.Z / 400))`),
    # and then it still folds nothing -- while `ULength = -1 * ULength` at a float assignment writes
    # `FloatConst -1.0`. Six bodies are the evidence: two that gained (`Pawn.BaseChange`,
    # `Pawn.TakeFallingDamage`'s `TakeDamage(-100 * (...)/MaxFallSpeed)`) and four that must keep folding
    # (`Weapon.DrawMuzzleFlash`/`StargateWeapon.DrawMuzzleFlash`'s `-1 * ULength`, `Replicator.PlayWaiting`'s
    # `LoopAnim('idle', -1/GroundSpeed)`, `BotEnemy.AdjustAround`).
    narrow_left = _narrowed_const(left, t1, rettype)
    fold_left = None if narrow_left is not None else _float_literal(left, t1, rettype)
    if fold_left is not None and (env.get("root_site") or "").lower() != "float":
        fold_left = None
    folded_left = ((narrow_left or fold_left) if root and not env.get("group_int") else None)
    node = (head + _side(left, t1, left_env, promote["l"] and _operator_types(_strip_parens(left), env) is not None,
                         folded_left)
            + _side(right, t2, right_env, promote["r"] and _operator_types(_strip_parens(right), right_env) is not None,
                    None, narrow=False)
            + b"\x16")
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
    if not t and _sole_unary().get(sym) == "bool":
        # The one-row rule on the unary side: `!` is declared once, over `bool`, so an operand the walk cannot
        # type is still a bool and the row is the only candidate. 25 bodies refuse on `an unknown type !` alone.
        hit = UNARY.get((sym, "bool"))
        if hit is not None:
            return sym, "bool", hit[1], hit[0]
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


def _metacast_span(expr):
    """`(index of the closing '>', index of the matching ')')` for a `class < Z > ( ... )` head, or None.

    Kept apart from `_metacast` because `_operator_types` needs the span even when the cast is only the *left*
    operand: its `<` and `>` are not comparisons, and reading them as such split
    `class<Actor>(OptionalObject) != None` at the `<`.
    """
    if len(expr) < 6 or expr[0][0] not in ("ident", "keyword") or expr[0][1].lower() != "class":
        return None
    if expr[1] != ("op", "<"):
        return None
    depth, close = 0, None
    for i in range(1, len(expr)):
        kind, val = expr[i]
        if kind != "op":
            continue
        if val == "<":
            depth += 1
        elif val == ">":
            depth -= 1
            if depth == 0:
                close = i
                break
    if close is None or close + 1 >= len(expr) or expr[close + 1] != ("op", "("):
        return None
    end = _paren_end(expr, close + 1)
    if end is None:
        return None
    return close, end


def _metacast_spans(expr):
    """Every `class < Z > ( ... )` head's `(start, close, end)` in an expression, as absolute indices.

    `_metacast_span` reads one cast only at the expression's head, which is enough for the *value* a cast
    yields but not for the **scan** `_operator_types` runs: a cast can sit anywhere in an expression, and its
    `<` and `>` are the cast's spelling, not comparisons. `1.2 * class<Weapon>(InventoryType).Default.AIRating`
    was split at the `>` into `1.2 * class < Weapon` and `( InventoryType ) . Default . AIRating`, two untyped
    sides, so the body refused as a pair the table does not install.
    """
    out, i = [], 0
    while i < len(expr) - 2:
        kind, val = expr[i]
        if kind in ("ident", "keyword") and val.lower() == "class" and expr[i + 1] == ("op", "<"):
            span = _metacast_span(expr[i:])
            if span is not None:
                out.append((i, i + span[0], i + span[1]))
                i += span[1] + 1
                continue
        i += 1
    return out


def _metacast(expr, env):
    """`class<Z>(inner)` -> `(Z, [inner tokens])`, or None when the expression is not that shape.

    A cast to a *class* type is the group-13 `MetaClassCast` (`0x13`), not the `2E` a cast to an object type
    writes: `class<Actor>(OptionalObject)` in `LocalMessage.GetString` is `13 02 00 41 44` and
    `class<Pawn>(DynamicLoadObject(...))` in `Controller.SetPawnClass` is `13 01 1c 9f ...`. The operand is an
    **object reference** (`02` and `01` are `Actor` and `Pawn`, exports 2 and 1 of Engine.u), the same table
    `_emit_cast` reads for a `DynamicCast` -- `--resolve` reports all 129 `MetaClassCast` operands in the corpus
    naming a `Class`. The source spells it with or without spaces (`class < Pawn > ( ... )`), so the tokens are
    the same either way and the `<`/`>` are what `_operator_types` would otherwise read as comparisons.
    """
    span = _metacast_span(expr)
    if span is None or span[1] != len(expr) - 1:
        return None
    close, end = span
    name = expr[2:close]
    if len(name) != 1 or name[0][0] not in ("ident", "keyword"):
        return None
    return name[0][1], expr[close + 2:end]


def _emit_metacast(name, inner, env, rettype=""):
    """`class<Z>(expr)` -> `13 <Z's object index> <expr>`, the group-13 node `--resolve` calls `MetaClassCast`."""
    ref = (env["scope"] or {}).get("classrefs", {}).get(name.lower())
    if ref is None:
        raise Refused("a cast to a class this package neither exports nor imports")
    node = bytes([0x13]) + write_index(ref) + emit_expr(inner, "", env)[0]
    return _converted(node, "class", rettype), "metacast"


def _super_cast_class(expr, env):
    """`Super(GUIComponent)` -> `guicomponent`: the class a `Super` cast names, or "" for any other expression.

    A `Super` in a receiver position has two spellings. `Super.F()` steps to the body's parent -- `order[1]`, the
    rule `_super_call` measures -- and `Super(C).F()` names the class on the arrow instead. Both write the same
    node (the callee's record behind `1c`, no `Context`, no skip word), so the second is the first with the class
    read out of the expression. Every residue row that spells it refused on the receiver
    (`Super(Actor).PostBeginPlay()`, `Super(GUIComponent).SetFocus()`, `Super(UWindowWindow).GetDesiredDimensions`),
    because `Super(...)` read as a call to a function named `super`, whose declared return type nothing carries.
    """
    expr = _strip_parens(expr)
    if not (len(expr) == 4 and expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "super"
            and expr[1] == ("op", "(") and expr[3] == ("op", ")")):
        return ""
    if expr[2][0] not in ("ident", "keyword"):
        return ""
    nm = expr[2][1].lower()
    return nm if nm in (env.get("class_names") or set()) else ""


def _prop_by_name(nm, env):
    """A property record by name through the body's own class chain -- what a struct member and a bare read use."""
    hit = (env["scope"] or {}).get("props", {}).get(nm)
    if hit is None:
        return None
    return (hit[0], hit[1], hit[2], _friendly(nm, env))


def _structs():
    """`{(struct, member): declared type}` from `inventory/struct_members.csv` (`tools/struct-members.py`).

    A struct member's *record* gives its size (`Vector.Z` is a `FloatProperty`) but not the struct a nested member
    is, nor an array's element type: `Coords.Origin` is a `vector` and `FilePath.DirectoryParts` an `array<string>`,
    and both are statements of the source's `struct` block. The census is the corpus's own `var` lines, so this is
    a join between the package's record and the text's declaration, exactly as `_type_of` joins a class property's
    record with `class_properties.csv`.
    """
    if STRUCT_TYPES:
        return STRUCT_TYPES
    path = os.path.join(ROOT, "inventory", "struct_members.csv")
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("type"):
                    STRUCT_TYPES[(r["struct"].lower(), r["member"].lower())] = r["type"].lower()
    except OSError:
        pass
    return STRUCT_TYPES


STRUCT_TYPES = {}


def _struct_of(expr, env):
    """The **struct** a receiver is declared as (`vector`), not the record kind every struct property shares."""
    expr = _strip_parens(expr)
    if len(expr) == 1 and expr[0][0] in ("ident", "keyword"):
        return (_declared_type(expr[0][1].lower(), env) or "").lower()
    return _type_of(expr, env).lower()


def _declaring_class(expr, env):
    """The class that declares the property a struct-typed receiver comes from, or `""` when the walk cannot say.

    A struct name is only unique inside the class that declares it: `StargateGame.u` nests one
    `DriverWeaponStruct` in `StargateVehicle` and another in `StargateWeaponEmplacement`, and each class's
    `PostNetBeginPlay` reads **its own** copy's `WeaponClass`/`WeaponBone`. The receiver is the property those
    copies are held in (`DriverWeapons`, re-declared by the subclass with its own struct type), so the declaring
    class is what picks the copy -- the package's index order cannot, because the two copies' fields are not in
    the same relative order (4360/6597 against 4692/5974).

    Only a bare name, or a name read as an array element, is answered: a chain's member is `_member_record`'s
    record and its declaring class would be that call's answer, which this rule does not claim. A **local** of a
    struct type is declared in the body's own class scope, so its declaring class is the body's.
    """
    expr = _strip_parens(expr)
    at = _element_at(expr)
    if at is not None:
        expr = _strip_parens(expr[:at])
    if len(expr) != 1 or expr[0][0] not in ("ident", "keyword"):
        return ""
    nm = expr[0][1].lower()
    scope = env.get("scope") or {}
    if nm in scope.get("locals", {}):
        return (scope.get("cls") or "").lower()
    return (scope.get("prop_cls") or {}).get(nm, "")


def _struct_record(struct, member, decl_cls, env):
    """The record `(struct, member)` names for **this** body: the declaring class's own copy first.

    `struct_cands` holds every record the pair names, in the package's export order (imports after them). A key
    with one record needs no choice. A key with several is resolved by walking the **declaring class's** chain --
    nearest ancestor first, so a subclass's own nested copy wins over the parent's -- and a pair that chain does
    not carry falls back to the package's order, which is the `lowest` index the census counts.

    The body's own class is deliberately **not** a witness: `SceneManager.ResolveActorsInActions` reads
    `subActOrient.CamOrientation.LookAtActorTag` where the struct-typed property is declared on
    `SubActionOrientation`, which nests no `Orientation` of its own, and the package names the **MatObject**
    copy (4800) -- every class's search falls back to the package order there, so the body's class must not
    vote. That body is the control for this rule and it stays byte-identical.
    """
    scope = env.get("scope") or {}
    cands = (scope.get("struct_cands") or {}).get((struct, member))
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0][:2]
    if decl_cls:
        for _p, anc in _ancestors(scope.get("chain") or {}, scope.get("pack") or "", decl_cls):
            for c in cands:
                if c[2] == anc:
                    return c[:2]
    return cands[0][:2]


def _struct_member(obj, nm, env):
    """`(INSTANCE_TOKEN, index, kind, declared)` for `nm` on the struct the receiver is declared as, or None.

    The pair is what resolves it: `Dest.Z` is `Z` on `Vector`, not any `z` the package happens to export, and the
    record is either this package's export or an import (`-21` here, written `95`). A pair the package does not
    hold, or a name whose declared type the census does not carry, answers None -- the refusal, not a guess.
    """
    name = _struct_of(obj, env)
    if not name:
        return None
    hit = _struct_record(name, nm, _declaring_class(obj, env), env)
    if hit is None:
        return None
    index, kind = hit
    ptype = PROP_TYPE.get(kind, "")
    declared = _structs().get((name, nm), "")
    if kind in ("StructProperty", "ArrayProperty", "ClassProperty", "ObjectProperty"):
        # The test is the **record kind**, not the mapped type word: `PROP_TYPE` answers `object` for a
        # `ClassProperty` (a class reference is a pointer), so keying on `ptype` dropped the declared word and
        # `FireProperties.ProjectileClass` lost its `class<Projectile>` -- the chain then refused as *a default
        # chain whose member the receiver's class does not declare*. The record says only
        # `StructProperty`/`ArrayProperty`/`ClassProperty`; the census names the struct, the element or the class
        # a `class<Z>` member holds.
        # An `ObjectProperty` keeps its word for the same reason, one link further out: `PointRegion.Zone` is
        # declared `zoneinfo` in the corpus's `struct` block, and `Viewer.Region.Zone.bDistanceFog` needs that
        # class to join the *next* member on -- without it the link read as the word `object` and the chain
        # refused as one whose member the artifacts name no class for.
        return (INSTANCE_TOKEN, index, ptype, declared)
    return (INSTANCE_TOKEN, index, ptype, "")


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


def _declares_void(cls, name, env):
    """True when the callee's declaration exists and names **no return type** (`event`s and `iterator` functions).

    `_rettype_on` answers `""` for both "declared with no return" and "no declaration found", and the call's value
    size needs the difference: a void callee yields nothing, so its `Context` writes size **0**, while an unknown
    callee is a refusal. The `iterator` family is the population that needed it -- `native(304) final iterator
    function AllActors(class<actor> BaseClass, out actor Actor, optional name MatchTag)` names no return because the
    value a `foreach` consumes is its `out` parameter, so the call's own `Context` is size 0
    (`foreach C.DynamicActors(class'StargatePlayer', Player)` ships `2f 19 00 7c 18 0d 00 00 61 39 ...`: skip
    0x000d, size 0x00). Membership, not truthiness: the empty string *is* the answer.
    """
    low = name.lower()
    voids = env.get("voids") or set()
    scope = env.get("scope") or {}
    if cls:
        for _p, c in _ancestors(scope.get("chain") or {}, scope.get("pack") or "", cls.lower()):
            if (c, low) in voids:
                return True
        return False
    return any((c, low) in voids for c in env.get("order") or [])


def _params_on(cls, name, env):
    """`[(type, name, optional)]` for `name` declared on `cls`'s chain -- a member call's callee, not the body's."""
    low = name.lower()
    params = env.get("params") or {}
    scope = env.get("scope") or {}
    if cls:
        for _p, c in _ancestors(scope.get("chain") or {}, scope.get("pack") or "", cls.lower()):
            if (c, low) in params:
                return params[(c, low)]
        # No fallback to the body's own chain: a member call's parameter list is the receiver's declaration, the
        # same rule `_rettype_on` already carries for its return type. `HaakenWeaponShield.BeginState`'s
        # `ShieldFX.Lower()` is a no-argument `Lower()` on `HaakenWeaponShieldEffect` while the body's own chain
        # declares `Lower(float)`, and the fallback refused the call as `0 argument(s) to a 1-parameter callee`.
        return None
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
            # A `native` declaration with **no index** (`natives.csv` token 0) has no opcode of its own, so
            # the compiler writes an ordinary call over the name: `GameInfo.ProcessServerTravel`'s
            # `P.ClientTravel(...)` is `1b 75 03` -- name 245, this package's `clienttravel` -- and
            # `Pawn.EndClimbLadder`'s `SetViewPitch` the same. Only a token the table carries is an opcode.
            idx = (env.get("names") or {}).get(low)
            if idx is None:
                break
            return ("virtual", idx)
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


def _class_ref_class(word):
    """The class a **type word** names when that word is a class reference, or "".

    A `class<Z>` declaration reaches the walk in three spellings and all three name the same class: the artifact
    spells it `Class<Ammunition>` (`class_properties.csv`, a `UClassProperty`), the source writes it with or without
    spaces (`var class<Ammunition> AmmoName;`, `local class < StargateWeapon > WeaponClass;`), and a parameter keeps
    its own concatenation (`class<Pawn>`, because `params_of` joins the words). `Z` is what a `.Default` member of
    the receiver is read on and what a `ClassContext` (`12`) is chosen for -- the reference's own record says
    `UClassProperty` and nothing about `Z`.
    """
    low = (word or "").replace(" ", "").lower()
    if low.startswith("class<") and low.endswith(">"):
        return low[6:-1]
    if low.startswith("class."):
        return low[6:]
    return ""


def _class_of(expr, env):
    """The class a receiver expression holds, named the way the package names classes, or "".

    Three sources, all artifacts rather than guesses: a parameter's or local's declared type (`Pawn P` -> `pawn`),
    a property's declared class (`class_properties.csv`: `GUIComboBox.List` is a `GUIListBase`), and a call's
    declared return type (`function PlayerController GetPlayerController(...)`). This is what lets a receiver chain
    be resolved one link at a time -- `H.Level.TimeSeconds` is a member of `LevelInfo`, which is what `HUD.Level`
    is declared as. A *class reference* (`class<Z>`, `Class<Z>`) resolves to `Z`: `AmmoName.Default.MaxAmmo` is a
    member of `Ammunition`, and the record lives on `Z`'s chain, not on the word `class`.
    """
    expr = _strip_parens(expr)
    if not expr:
        return _no("an empty receiver expression")
    class_names = env.get("class_names") or set()
    stat_at = _static_at(expr)
    if stat_at is not None:
        # `.static` is the class object itself, so the chain before it is what holds the class:
        # `class'AIMessage'.static.Assign(...)` resolves `Assign` on `AIMessage`, and
        # `class<Actor>(OptionalObject).static.GetLocalString(...)` on `Actor`.
        return _class_of(expr[:stat_at], env)
    if len(expr) == 1 and expr[0][0] in ("ident", "keyword"):
        low = expr[0][1].lower()
        # a `local class<Z> N;` / `var class<Z> N;` declaration, which the two type tables below cannot carry
        # (their declaration regexes stop at the `<`)
        ref = (env.get("classreftypes") or {}).get(low, "")
        if ref:
            return ref if ref in class_names else _no(
                "a `class<Z>` declaration whose class the class table does not carry")
        for table in ("paramtypes", "localtypes", "proptypes"):
            t = (env.get(table) or {}).get(low, "")
            if t and t in class_names:
                return t
            ref = _class_ref_class(t)
            if ref:
                return ref if ref in class_names else _no(
                    "a `class<Z>` declaration whose class the class table does not carry")
        if low == "self":
            own = (env.get("scope") or {}).get("cls") or ""
            return own if own in class_names else ""
        hit = _prop_by_name(low, env)
        if hit is not None and len(hit) > 3:
            friendly = hit[3] or ""
            if friendly in class_names:
                return friendly
            ref = _class_ref_class(friendly)
            if ref:
                return ref if ref in class_names else _no(
                    "a `class<Z>` member whose class the class table does not carry")
        # A `within` class spells the outer class's properties bare, and the package's class table does **not**
        # carry their types (`PlayerController`'s `Pawn`, `Level` and `Outer` are native records with no
        # `class_properties.csv` row, so the friendly word is empty and `Pawn.UnderwaterTime` had no class to join
        # on). The `within` chain's own source text is the declaration the compiler read: `Controller.uc`'s
        # `var Pawn Pawn;`, `Actor.uc`'s `var const LevelInfo Level;`.
        word = (env.get("within_proptypes") or {}).get(low, "")
        if word:
            if word in class_names:
                return word
            ref = _class_ref_class(word)
            if ref:
                return ref if ref in class_names else _no(
                    "a `within` property whose class the class table does not carry")
        for table in ("paramtypes", "localtypes", "proptypes"):
            t = (env.get(table) or {}).get(low, "")
            if t.replace(" ", "").lower().startswith("array"):
                return _no("an array name, whose element type the artifacts do not name")
            if t:
                return _no("`%s` is declared `%s`, which is not a class the class table carries"
                           % (low, t))
        return _no("a bare name this body declares no type for")
    if (len(expr) == 2 and expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "class"
            and expr[1][0] == "quoted"):
        # `class'GameInfo'.Default.DefaultPlayerName`: the literal names its own class, and a package prefix is the
        # package's, not part of the class name (`class'Engine.PlayerInput'`).
        name = expr[1][1].strip("'").strip('"').lower().split(".")[-1]
        return name if name in class_names else _no(
            "a class literal whose class the class table does not carry")
    mc = _metacast(expr, env)
    if mc is not None:
        # `class<Actor>(OptionalObject).static.GetLocalString(...)`: the receiver holds `Z` itself, so a member
        # lookup runs on `Z`'s chain rather than on the word `class`
        return mc[0].lower() if mc[0].lower() in class_names else _no(
            "a `class<Z>(x)` cast whose class the class table does not carry")
    if _is_call(expr):
        # `TransientAmbientPawn(Pawn).VerifyLastRenderTime()`: the receiver is a cast, and a cast names the class
        # it casts to -- the same `classrefs` rule `_emit_cast` uses for `DynamicCast`.
        if _cast_kind(expr[0][1], env) == "object" and expr[0][1].lower() in class_names:
            return expr[0][1].lower()
        t = _rettype_on("", expr[0][1], env)
        return t if t in class_names else _no(
            "a call whose declared return type the class table does not carry as a class")
    at = _element_at(expr)
    if at is not None:
        # `GlobalEvents[size - 1].AddCondition(con)`: the receiver is an element, and the element type is what
        # the member call resolves against
        t = _element_type(expr[:at], env)[1]
        return t if t in class_names else _no("an element of an array the artifacts do not type")
    at = _dot_at(expr)
    if at is not None:
        member = expr[at + 1:]
        if _is_call(member):
            # `A.F().X`: the chain's last link is a **call**, and the class the chain holds is the return type
            # `F` declares on A's class. `Controller.GetPlayerController().GamepadID` refused as *a receiver
            # shape `_class_of` does not resolve* while `GetPlayerController()`'s declaration names
            # `PlayerController`; `xAdminGroup.Users.Get(i).bMasterAdmin` is the same shape two links out. The
            # return type is asked on the receiver's chain (`_rettype_on` refuses to fall back to the body's),
            # so a call link with no receiver class stays unresolved rather than borrowing the body's function.
            cls = _class_of(expr[:at], env)
            t = _rettype_on(cls, member[0][1], env) if cls else ""
            return t if t in class_names else _no(
                "a member call whose declared return type the class table does not carry as a class")
        if len(member) == 1 and member[0][0] in ("ident", "keyword"):
            nm = member[0][1].lower()
            hit = _member_record(expr[:at], nm, env)
            if hit is None or not hit[3]:
                # The receiver is a **struct**, and a struct's members are records too: `FiredAmmunition` is a
                # `FireProperties` parameter, so `FiredAmmunition.ProjectileClass.Default.Speed` needs
                # `FireProperties.ProjectileClass`'s declared type (`class<Projectile>`) and not a class lookup --
                # `fireproperties` is no class, so the class join answers "". The pair is the same `_struct_member`
                # `_emit_member` uses for a struct field read.
                #
                # A *class* record with no declared word is the same miss one shape out: `Region.Zone` is
                # `PointRegion.Zone`, a struct field, and `_member_record` answers it from the package's own
                # export with an empty friendly type -- `PointRegion.Zone` is not a class property, so
                # `class_properties.csv` has no row for it and `Region.Zone.bSoftKillZ` then refused as *a
                # receiver chain whose member the artifacts name no class for*. The struct's own declaration
                # says `zoneinfo`, so the struct link is asked whenever the class record carries no type word.
                sh = _struct_member(expr[:at], nm, env)
                if sh is not None and sh[3]:
                    hit = sh
            if hit is not None and len(hit) > 3:
                friendly = hit[3] or ""
                if friendly in class_names:
                    return friendly
                # a member declared `class<Z>`: `Chosen.PawnClass.Default.ControllerClass` is read on `Pawn`
                ref = _class_ref_class(friendly)
                if ref:
                    return ref if ref in class_names else _no(
                        "a chain member declared `class<Z>` whose class the table does not carry")
            return _no("a receiver chain whose member the artifacts name no class for")
    return _no("a receiver shape `_class_of` does not resolve")


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
    if target:
        # A receiver declared `class<Z>` has its property's record on **Z**: `Weapon.AmmoName` is
        # `var class<Ammunition> AmmoName`, so `AmmoName.Default.MaxAmmo`'s record is `Ammunition.MaxAmmo` and not
        # a record on the word `class`. The class table already spells it `class<ammunition>` for a property and
        # `_class_of` returns `Z` for a local, so the unwrap has to sit here, on both paths.
        target = _class_ref_class(target) or target
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
    if len(imp) > 1:
        # The receiver's class is known, so the import's *owner* is the record: `Chosen.PawnClass.Default.
        # ControllerClass` is `Pawn.ControllerClass`, and `StargateGame.u` imports `ControllerClass` from more
        # than one class, so the name alone is ambiguous and the chain's own ancestor decides.
        owners = env.get("import_owners") or {}
        anc = {c for _p, c in _ancestors(chain, pack, target)}
        owned = [x for x in imp if (owners.get(x[0]) or "").lower() in anc]
        if len(owned) == 1:
            imp = owned
    if len(imp) == 1:
        kind = PROP_TYPE.get(imp[0][1], "")
        friendly = ""
        for p, c in _ancestors(chain, pack, target):
            friendly = (scope.get("friendly") or {}).get((p, c, nm), "")
            if friendly:
                break
        return (INSTANCE_TOKEN, imp[0][0], kind, friendly)
    return None


# The size byte a **call** writes for its value, per declared return kind. `PROP_SIZE` is the property table (the
# same measure, taken over 31,622 `Context` nodes); the struct/byte words here are the ones the refusal families
# pin, each read off the shipped node that refused for want of it -- the size is a property of the return kind, so a
# body with one call node in it pins the pair:
#   `coords` -> **48**, not 12: four vectors (`Origin`, `XAxis`, `YAxis`, `ZAxis`), measured on 10 bodies
#     (`Pawn.GetBoneCoords(...)`, `AlkeshCannonAttachment.GetBoneCoords`); `STRUCT_SIZE`'s 12 for a coords *property*
#     is a different shape and stays as measured.
#   `color` -> 4 (one node; the same width `STRUCT_SIZE` carries for the property), `font` -> 4 (a class, so a
#     pointer: two nodes), `framehittest` -> 1 (one node -- a byte-sized UWindow struct).
#   `plane` -> 16, `region` -> 16, `box` -> 28 and `array.string` -> 0 are read off the `Context` node each
#     refuses at, and the skip word beside the size is its own check (`skip == _wide(call)`):
#     `ShadowProjector.InitShadow`'s `ShadowActor.GetRenderBoundingSphere()` is `19 01 62 16 06 00 10 1c 61 41 16`
#     (skip 6, size 0x10); `UWindowFramedWindow.Resized` and `UWindowMessageBox.BeforePaint` both write
#     `19 01 06 07 00 10 1b 73 01 17 16` for `LookAndFeel.FW_GetClientArea(Self)` (skip 7, size 0x10);
#     `BotEnemy.SetNewHideNode`'s box-returning call is `19 01 14 06 00 1c 1c e7 0e 16` (skip 6, size 0x1c = 28,
#     not the 25 a field sum of `box` would give); and `GameInfo.GetMapRotation`'s
#     `MaplistHandler.GetCurrentMapRotation()` is `19 01 68 1f 06 00 00 1b 50 0d 16` (skip 6, size 0).
RETURN_SIZE = dict(PROP_SIZE, rotator=12, coords=48, color=4, font=4, framehittest=1,
                   plane=16, region=16, box=28, **{"array.string": 0})


def _emit_member_call(obj, member, rettype, env):
    """`A.F(args)` -> `19 <A> <skip> <size> <F>`: the call is the **member** of a `Context`.

    The word is the call's own width in the compiler's code array (`_wide`), not a constant: `List.ItemCount()` is
    `19 01 2b 06 00 04 1b 0a 16` and `P.BeyondViewDistance(SpawnLocation, 0)` is `19 00 7e 7e 10 00 04 1b 43 08 00
    7f 7e 1e 00000000 16`. The size byte is the *value* the call yields -- 4 for an object, an int or a bool, 0 for a
    string, 12 for a rotator (`AlkeshCannonAttachment(...).GetBoneRotation(...)` writes `0c`). When the object is a
    property **import** (`Outer`, `01 8e`) the corpus writes 5 whatever the member is (measured over `Engine.u`:
    `Outer.Log("...")` is `19 01 8e 05 00 04 1b 0f ...`), so that shape keeps the measured constant.
    """
    stat_at = _static_at(obj)
    if stat_at is not None:
        # `class'AIMessage'.static.Assign(m, n)`: `.static` names the class object, so the call sits inside a
        # **`ClassContext`** (`12`) whose expression is the chain before the link -- `Speech.SetAIMsg` writes
        # `12 20 19 10 00 04 1c 56 07` (ObjectConst of the class, skip 16, size 4, the call). The skip word is the
        # same measure as a `Context`'s: over all 113 `ClassContext` call nodes in the corpus it equals `_wide` of
        # the member (the compact length plus 3 bytes per one-byte index), never the compact length itself -- which
        # is why the word read as unexplainable at `AIMsgManager.AddReceivedMsg` (skip 22 over a 14-byte member).
        obj = obj[:stat_at]
    if len(obj) == 1 and obj[0][0] in ("ident", "keyword") and obj[0][1].lower() in ("static", "global"):
        # `Static.F(args)` / `Global.F(args)` in a value position: `ScriptedController.GetAggroLevel`'s
        # `return Global.GetAggroLevel();` is `04 38 64 03 16`, and `GameInfo.AddMutator`'s
        # `!Static.AllowMutator(MutName)` is `1b 7f 16`. The prefix writes no receiver node; the return type is the
        # callee's own and converts at the site, exactly as a bare call's does.
        node, _model, shape = _prefix_call(obj, member, env)
        rtype = _callee_return_type(member[0][1], env)
        if rtype == "string":
            return node, shape                   # the `EatString` prefix is already in the node
        return _converted(node, rtype, rettype), shape
    if len(obj) == 1 and obj[0][0] in ("ident", "keyword") and obj[0][1].lower() == "super":
        # `Super.F(args)` in an **expression** is the same `FinalFunction` the statement path writes: the parent
        # class's own record behind `1c`, with no `Context` around it (`_super_call`). The statement form has been
        # modelled since the parent-record rule; a return or an assignment value reaches `_emit_member` instead and
        # had no path, so `return Super.F();` refused on the receiver.
        order = env.get("order") or []
        if len(order) < 2:
            raise Refused("a Super call in a class with no super")
        parent = order[1]
        if member[0][0] not in ("ident", "keyword"):
            raise Refused("a Super call whose member the walk cannot name")
        rec = _record_on(parent, member[0][1], env)
        if rec is None:
            raise Refused("a Super call to a name the parent's chain does not declare")
        call = emit_call(member, env, receiver=parent, head=bytes([0x1C]) + write_index(rec))
        return _converted(call, _rettype_on(parent, member[0][1], env), rettype), "super-call-value"
    sup = _super_cast_class(obj, env)
    if sup:
        # `Super(C).F(...)` in a value position: the same record-behind-`1c` node as `Super.F(...)`, with the
        # class read from the cast instead of from the body's parent. Its declared return type converts at the
        # site, exactly as the parent spelling's does.
        if member[0][0] not in ("ident", "keyword"):
            raise Refused("a Super(C) call whose member the walk cannot name")
        rec = _record_on(sup, member[0][1], env)
        if rec is None:
            raise Refused("a Super(C) call to a name the class's chain does not declare")
        call = emit_call(member, env, receiver=sup, head=bytes([0x1C]) + write_index(rec))
        return _converted(call, _rettype_on(sup, member[0][1], env), rettype), "super-cast-value"
    objb = emit_expr(obj, "", env)[0]
    cls = _class_of(obj, env)
    if not cls:
        # The receiver's class is the join. Falling back to the body's own chain here wrote a `VirtualFunction`
        # for `ProfileList.ItemCount()` -- resolved to `ProfilePanel.ItemCount`, a *different* function of the same
        # name two classes away -- where the package writes a `FinalFunction` naming the receiver's own record.
        _note(family="member-call-expr", expr=" ".join(v for _k, v in obj), member=member[0][1].lower(),
              receiver="")
        raise Refused("a call member whose receiver's class the artifacts do not name")
    memb = emit_call(member, env, receiver=cls)
    # The word is the call's own width in the compiler's code array, whatever the receiver is. It was held at 5 for
    # an import receiver (`Outer.Log("...")`), but that shape's call *is* five wide: `19 01 8e 05 00 00 1b 0f ...`, and
    # the four bodies whose receiver is a property read with a longer call write 0x03, 0x0b, 0x0b and 0x10 --
    # `Pawn.NeedToTurn(targ)` is `19 01 8d 0b 00 04 1b 59 02 00 48 0a 16`, and `_wide` of that call is 11.
    skip = _wide(memb)
    rtype = _rettype_on(cls or "", member[0][1], env)
    if _class_ref_class(rtype):
        # A `class<Z>` return is a **class reference**, and a class reference is pointer-sized: the artifact spells
        # the word three ways (`class<GameInfo>`, `Class.GameInfo`, `class`), and only the last reached the size
        # table -- `Level.GetGameClass()` is `class<GameInfo>` and `G = Level.GetGameClass()` refused as
        # `class.gameinfo` in `PlayerController.Fire`.
        rtype = "class"
    if rtype in (env.get("class_names") or set()):
        rtype = "object"
    # An **enum's own name** is the byte it is stored in, here as everywhere (`_enum_byte`): the size word is the
    # width of the value the call yields, and `GetAggroLevel()` returns an `EAggroLevel`. Seven bodies refused on
    # this one byte -- `a call member returning eaggrolevel, whose value size this walk does not carry`
    # (`StargatePawn.PlayWaiting`, `SquadAISG.AssignSquadResponsibility`, ...) -- while the third `_enum_byte`
    # producer (a callee's declared return) already answered `byte` for the same type.
    rtype = _enum_byte(rtype, env)
    if rtype in RETURN_SIZE:
        size = RETURN_SIZE[rtype]
    elif not rtype and _declares_void(cls or "", member[0][1], env):
        # A callee **declared** with no return type yields nothing, and the `Context` around its call writes size
        # **0**. The family this closes is the iterators: the value a `foreach` consumes is the iterator's `out`
        # parameter, not a return, so `foreach C.DynamicActors(class'StargatePlayer', Player)` ships
        # `2f 19 00 7c 18 0d 00 00 61 39 20 ...` -- a `Context` with skip 0x000d and a size byte of 0 where a walk
        # that read the iterator as an object return wrote 4 (76 of the 84 rows differed at exactly this byte).
        size = 0
    else:
        # Name the callee and the receiver: this is the population `tools/call-member-ret.py` measures, and a
        # residue row without them cannot be joined to the node the package wrote.
        _note(family="call-member-ret", expr=" ".join(v for _k, v in obj), member=member[0][1].lower(),
              receiver=cls)
        raise Refused("a call member returning %s, whose value size this walk does not carry"
                      % (rtype or "an unknown type"))
    node = (bytes([CLASS_CONTEXT_TOKEN if stat_at is not None else CONTEXT_TOKEN])
            + objb + struct.pack("<H", skip) + bytes([size]) + memb)
    return _converted(node, rtype, rettype), "class-member-call" if stat_at is not None else "call-member"


def _is_class_ref(expr, env):
    """Whether the receiver of a `.Default.` chain holds a **class reference** rather than an object.

    `X.Default.Y` is two different opcodes and the receiver decides which: the shipped bytes write
    `12 <X> 05 00 <size> 02 <Y>` (a `ClassContext`) when `X` is a class -- `Weapon.AmmoName` is declared
    `var class<Ammunition> AmmoName`, and `return AmmoName.Default.MaxAmmo;` is `12 01 75 0a 05 00 04 02 5b 0f` --
    and `19 <X> 05 00 <size> 02 <Y>` (a plain `Context`) when it is an object, which is what every one of the
    corpus's 30 modelled default chains over a variable writes (`P.Default.AirSpeed`, `Driver.default.bOwnerNoSee`).
    A class literal (`class'GameInfo'.Default.X`) and a bare class name are references by their own spelling.
    """
    expr = _strip_parens(expr)
    if not expr:
        return False
    if len(expr) == 1 and expr[0][0] in ("ident", "keyword") and \
            (env.get("classreftypes") or {}).get(expr[0][1].lower()):
        # a `class<Z>` declaration is the reference whatever the package's record kind says: the record is a
        # `UClassProperty` and its friendly word is `Class<Z>`, neither of which spells `class` on its own.
        # `StargatePawn.AnimateSniping`'s `local class < StargateWeapon > WeaponClass` writes `12 00 67 29`
        # for `WeaponClass.Default.SnipIdleAnim` -- the `ClassContext`, not a `Context`.
        return True
    if len(expr) == 2 and expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "class" \
            and expr[1][0] == "quoted":
        return True
    if len(expr) == 1 and expr[0][0] in ("ident", "keyword"):
        word = (env.get("paramtypes") or {}).get(expr[0][1].lower(), "") or _friendly(expr[0][1].lower(), env) or ""
        if word.lower().startswith("class<") or word.lower().startswith("class."):
            return True
        word = (env.get("localtypes") or {}).get(expr[0][1].lower(), "")
        if word.lower().startswith("class<") or word.lower().startswith("class."):
            return True
    at = _dot_at(expr)
    if at is not None:
        # A **member** can be the class reference: `Chosen.PawnClass` is `Pawn`'s `var class<Pawn> PawnClass`, and
        # `Chosen.PawnClass.Default.ControllerClass` is `12 ...`, a `ClassContext`. Every receiver shape above is a
        # bare name, a literal or a cast, so a chain link fell through to `False` and seven bodies wrote `19` where
        # the package wrote `12` (the seven differ rows: `DeathMatch`/`StoryModeGame.SpawnBot` and the six
        # `class<Z>` rows -- `AmbientCreatureManager.Timer`, `Powerups.HandlePickupQuery`,
        # `WeaponPickup.BotDesireability`, both `CalcGrenadeLanding`, `AirborneTurretController.
        # GetAimingTargetSpeedOffset`). The link's own declared word is the same join `_class_of` uses, so ask it.
        member = expr[at + 1:]
        if len(member) == 1 and member[0][0] in ("ident", "keyword"):
            nm = member[0][1].lower()
            hit = _member_record(expr[:at], nm, env) or _struct_member(expr[:at], nm, env)
            if hit is not None and len(hit) > 3 and _class_ref_class(hit[3]):
                return True
    if _type_of(expr, env).lower() == "class" and not _is_call(expr):
        # A bare class name (`Pawn`) and a class literal read as `class`, but a **cast** reads as `class` too
        # (`Pawn(Owner)`) because `_type_of` answers the cast's own word for every object type. A cast is an
        # object, not a reference: `Pawn(Owner).Default.Visibility` is `19 2e 83 01` (a `Context`), and writing
        # `12` there was the whole of `ShieldBelt.StopInvisible`'s difference.
        return True
    return False


def _emit_member(expr, rettype, env, element_dim=0):
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
        if os.environ.get("SG1_DBG_MEMBER"):
            raise Refused("a receiver chain whose member is not one name (obj %s | member %s)"
                          % (" ".join(v for _k, v in obj), " ".join(v for _k, v in member)))
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
    chain_at = _dot_at(obj)
    if (chain_at is not None and len(obj[chain_at + 1:]) == 1 and obj[chain_at + 1][0] in ("ident", "keyword")
            and obj[chain_at + 1][1].lower() == "default"):
        # `X.Default.Y` is a **ClassContext** (`12`), not a `Context` around a default read: the object expression
        # `X` is the node's receiver and the `DefaultVariable` (`02`) carries `Y`'s own record on X's class --
        # `return AmmoName.Default.MaxAmmo;` is `12 01 75 0a 05 00 04 02 5b 0f`, with no separate member read. The
        # size byte is the member's value size, the same one a `Context` would write.
        recv = obj[:chain_at]
        hit = _member_record(recv, nm, env)
        if hit is None:
            if os.environ.get("SG1_DBG_MEMBER"):
                raise Refused("a default chain whose member the receiver's class does not declare "
                              "(recv %s on %s)" % (" ".join(v for _k, v in recv),
                                                   _class_of(recv, env) or "an unnamed class"))
            raise Refused("a default chain whose member the receiver's class does not declare")
        _token, index, ptype, friendly = hit
        size = _value_width(ptype, friendly)
        if size is None:
            raise Refused("a default chain to a %s member, whose value size this walk does not carry"
                          % (ptype or "typeless"))
        if element_dim:
            # A **subscript** consumes this read, so the byte is the array's own size -- `dimension x element`,
            # the same rule the receiver-chain path below applies -- and not the element's. Eleven bodies wrote
            # the element's 4 here: `HUDClass.default.FontScreenWidthSmall[i]` (an `int[9]`, so **36**) in
            # `ScoreBoard`'s three font pickers, and `WeaponClass.Default.RegularMovementAnims[0]` (a `name[2]`,
            # so **8**) in seven `StargatePawn.Animate*` bodies (`inventory/emit_differ.csv`'s two `context size`
            # rows; `tools/context-size.py` measures the rule over the corpus).
            if element_dim < 0:
                raise Refused("a default-chain static array whose declared dimension the artifacts do not carry")
            size = element_dim * size
        member_read = bytes([DEFAULT_TOKEN]) + write_index(index)
        skip = CONTEXT_SKIP
        if ptype == "bool":
            # A bool member of the default object is read behind `BoolVariable` exactly as an instance bool member
            # is: `Driver.default.bOwnerNoSee` is `19 01 1b 06 00 04 2d 02 59 0a` -- `2d` (BoolVariable) sits
            # inside the `Context`'s own node and the `DefaultVariable` (0x02) follows it, so the skip word is the
            # member read's *wide* length (`_wide`, 6 here) and the value size stays 4.
            member_read = bytes([0x2D, DEFAULT_TOKEN]) + write_index(index)
            skip = _wide(member_read)
        node = (bytes([CLASS_CONTEXT_TOKEN if _is_class_ref(recv, env) else CONTEXT_TOKEN])
                + emit_expr(recv, "", env)[0] + struct.pack("<H", skip)
                + bytes([size]) + member_read)
        return _converted(node, ptype, rettype), "default-chain"
    if len(obj) == 1 and obj[0][0] in ("ident", "keyword"):
        # A **qualified enum constant** -- `ERenderStyle.STY_Normal`, `WeaponID.P90` -- is the ordinal alone, the
        # same two bytes the bare spelling writes: an enum constant is a global name to this compiler, so the
        # `Enum.` prefix names the enum and adds no node. 31 residue rows spell it in bodies whose chain never
        # declares the enum (`UWindow` classes and the SGMenu family read `Actor`'s `ERenderStyle`), and the
        # `(enum, constant)` pair is what makes the ordinal unambiguous -- the table drops a pair two declarations
        # spell at different ordinals.
        _ordinal = (env.get("enum_consts") or {}).get(obj[0][1].lower(), {}).get(nm)
        if _ordinal is not None:
            if not 0 <= _ordinal <= 255:
                raise Refused("an enum constant whose ordinal is above 255")
            return _converted(bytes([0x24, _ordinal]), "byte", rettype), "enum-member"
    objtype = _type_of(obj, env).lower()
    # The node is chosen by the receiver's *record kind*, not by its declared type word: `PathName` is a `FilePath`
    # struct (a `StructMember`), while a cast or an operator wants that word (`SquadDir` is a `Vector`).
    objkind = _receiver_kind(obj, env)
    if objkind in ("array", "string"):
        if nm != "length":
            raise Refused("a %s member that is not its length" % objkind)
        return _converted(bytes([0x37]) + emit_expr(obj, "", env)[0], "int", rettype), "length"
    if objkind == "struct":
        # The **struct's own** record wins over a class property of the same name. The class chain's table is
        # keyed by name alone, so a struct field whose name a property of the class also spells resolved to the
        # property: `UWindowFramedWindow.Resized`'s `ClientArea.WinLeft = R.X` read `X` as export 478 (a float
        # property of the GUI class) and wrote `36 de 01`, where the package writes `36 1f` -- UWindow's own
        # `Region.X`, export 31, an `int` -- and with the property's `float` type the `39 3f` `IntToFloat` in
        # front of the field went missing too. `_struct_member` answers only when the receiver's declared struct
        # declares the name, so a field the struct does not hold still falls back to the class chain.
        hit = _struct_member(obj, nm, env) or _prop_by_name(nm, env)
    else:
        hit = _member_record(obj, nm, env)
    if hit is None:
        # No fallback to the body's own chain: `Index` is declared on half the GUI classes, and resolving it that way
        # wrote export 8263 for a property whose record is 13. Two bodies also resolve the name to a same-named
        # export of *this* package while the package names an import (`InitFor` writes -20), which is wrong bytes
        # that look right -- the receiver's class is the only honest join.
        _note(family="member-read", expr=" ".join(v for _k, v in obj), member=nm,
              receiver=_class_of(obj, env) or "", kind=objkind)
        raise Refused("a member that is not a property of the receiver's class")
    token, index, ptype, friendly = hit
    if objkind == "struct":
        # A field of a struct (no `Context`, so no size byte of its own) still needs a *value size* for whatever
        # consumes it, and a field whose type is a struct is in `STRUCT_SIZE` like any other. The refusal names the
        # declared struct, not just the mapped word, so the next residue reads which width it wanted.
        if _value_width(ptype, friendly) is None:
            raise Refused("a struct member of type %s%s, whose value size this walk does not carry"
                          % (ptype or "typeless",
                             " (declared %s)" % friendly if friendly and ptype == "struct" else ""))
        read = bytes([0x36]) + write_index(index) + emit_expr(obj, "", env)[0]
        if ptype == "bool":
            # A bool **member of a struct** is written behind `BoolVariable`, exactly as a bool property is: the
            # `StructMember` node reads the record and the `2d` around it is what types the value.
            # `!SavedFireProperties.bInitialized` is `81 2d 36 52 43 01 5a 08 16`
            # (`Weapon.AdjustAim`) and `SimAnim.bAnimLoop = true;` is `14 2d 36 ...` (`BlueFlag.PostBeginPlay`).
            read = bytes([0x2D]) + read
        return (_converted(read, ptype, rettype), "struct-member")
    if token != INSTANCE_TOKEN:
        raise Refused("a member reached through a local")
    size = PROP_SIZE.get(ptype)
    if element_dim:
        # The read is the array a **subscript** consumes, and its `Context` writes the array's own byte size --
        # `dimension x element` -- not the element's: `StargatePawn.EndCrouch`'s
        # `StargateWeapon(Weapon).RegularMovementAnims[1]` ships `19 2e 09 01 a5 05 00 08 01 63 21` (a `name[2]`
        # is 8) where this walk wrote `PROP_SIZE["name"]` 4; `NavigationPoint.SetBaseVisibility`'s
        # `N.BaseVisible[BaseNum]` writes 2 (`byte[2]`), `PlayerController.ClientUpdateFlagHolder`'s
        # `GameReplicationInfo.FlagHolder[i]` 8 (`PlayerReplicationInfo[2]`), `BotBase.SetNextEnemy`'s
        # `pawn[16]` 64 and `StoryModeGame.AddSquadFromSpawner`'s `vector[16]` **192**
        # (`inventory/array_read_width.csv`, `tools/array-read-width.py`: 115 nodes, every one `dim x element`).
        # The dimension is the *receiver* class's declaration, because two classes spell one name with different
        # dimensions (`StargatePawn`'s `RegularMovementAnims[7]`, `StargateWeapon`'s `[2]`); a static array whose
        # dimension no declaration carries is refused rather than given the element's width.
        unit = STRUCT_SIZE.get((friendly or "").lower()) if ptype == "struct" else PROP_SIZE.get(ptype)
        if element_dim < 0 or unit is None:
            raise Refused("a static array whose declared dimension the artifacts do not carry")
        size = element_dim * unit
    if size is None and ptype == "struct":
        # The record is a `StructProperty`; the artifact's declared type names the struct, and only a struct whose
        # size the corpus writes is modelled (`Pawn.Location` -> 12). A struct name the table does not carry keeps
        # the refusal, and the message names it so the residue says which one.
        size = _value_width(ptype, friendly)
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


def emit_expr(expr, rettype, env, element_dim=0):
    """The bytes of one expression, or Refused with the reason.

    `element_dim` is non-zero only when the caller is a subscript (`_emit_element`): the array read it composes
    is then consumed by the element node, and a static array's `Context` writes the array's own byte size.
    """
    names = env["names"]
    # `root` is the expression the compiler starts on -- a statement's value, an argument, a condition -- and it is
    # what decides whether an int literal at a `float` site is **folded** into a `FloatConst` or left behind an
    # `IntToFloat` cast (see `_float_literal`). Every nested operand clears it; a parenthesised operand sets it
    # again, because a parenthesised group is its own expression.
    root = env.get("_root", True)
    env = dict(env, _root=False)
    if root:
        # The type of the expression the compiler starts on -- the assignment's target, the call's parameter, the
        # return -- which is what a parenthesised group is re-typed to (`_group_narrow`).
        env.setdefault("root_site", rettype)
    expr = _strip_parens(expr)
    if not expr:
        return bytes([0x0B]), "nothing"                  # Return; -> Return Nothing
    if len(expr) == 2 and expr[0] == ("op", "-") and expr[1][0] == "number":
        expr = [("number", "-" + expr[1][1])]
    if len(expr) != 1:
        mc = _metacast(expr, env)
        if mc is not None:
            # A cast to a class type is tried before the operator table: its own spelling carries a `<` and a `>`,
            # which that table reads as two comparisons (`Controller.SetPawnClass` was refused as "an unknown type
            # > object", `LocalMessage.GetString` as "an unknown type != object").
            return _emit_metacast(mc[0], mc[1], env, rettype)
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
        if expr[0][0] in ("ident", "keyword") and expr[0][1].lower() == "rot":
            # `rot(pitch, yaw, roll)` is a struct literal the compiler folds, like `vect`: `22` and three i32.
            return _emit_rotator(expr), "rotator"
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
            return _emit_member(expr, rettype, env, element_dim=element_dim)
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
                node, _shape = emit_expr([("number", value)], rettype, dict(env, _root=root))
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
            if (env.get("int_site") or env.get("group_int")) and float(v).is_integer():
                return _converted(_int_bytes(int(v)), "int", "float"), "float-const-narrowed"
            return bytes([0x1E]) + struct.pack("<f", float(_int32(int(v)) if isinstance(v, int) else v)), "float-const"
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


def _emit_rotator(expr):
    """`rot(pitch, yaw, roll)` -> `22` and three little-endian **i32** -- measured, not assumed.

    `PhysicsVolume.PlayExitSplash` ships `Spawn(ExitActor,,, ... ,rot(16384,0,0))` as
    `... 22 00 40 00 00 | 00 00 00 00 | 00 00 00 00`, so the components are whole rotator units in signed
    dwords rather than the f32 `vect` writes; `UnrealPawn`'s `Rot(-16384,0,0)` is the same field, two's
    complement. The components are compile-time constants -- an expression inside `rot(...)` is a struct
    literal the compiler cannot fold, and this walk refuses it rather than writing the node it guesses at.
    """
    parts = _split_commas(expr[2:-1]) if expr[1] == ("op", "(") and expr[-1] == ("op", ")") else []
    if len(parts) != 3:
        raise Refused("a rotation literal that is not three numeric components")
    out = bytearray([0x22])
    for p in parts:
        sign = 1
        if len(p) == 2 and p[0] == ("op", "-"):
            # `rot(-16384,0,0)` lexes as an operator and a number, as a negative literal does everywhere else.
            sign, p = -1, p[1:]
        if len(p) != 1 or p[0][0] != "number":
            raise Refused("a rotation literal that is not three numeric components")
        value = _number(p[0][1])
        if isinstance(value, float):
            if not value.is_integer():
                raise Refused("a rotation literal whose component is not a whole rotator unit")
            value = int(value)
        out += struct.pack("<i", sign * int(value))
    return bytes(out)


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
    """`[(type, name)]` from the declaration of the callee the class chain inherits it from.

    With a `receiver` the answer is the receiver's chain only: `_params_on` refuses to fall back, because a
    same-named function on the body is a different record with its own parameter list.
    """
    if receiver:
        return _params_on(receiver, name, env)
    low = name.lower()
    for cls in env["order"]:
        hit = env["params"].get((cls, low))
        if hit:
            return hit
    return None


def _by_name_index(name, env):
    """The name index a **by-name** `VirtualFunction` writes, or None when the callee is not that shape.

    A callee with no record in the body's package -- no export, no import, no native row -- is not nameless: the
    compiler writes the call by *name index*. `AmbientCreatures.TransientAmbientPawn.Died` ships
    `... 1b 41 02 00 73 02 00 74 02 16` for `PlayDying(DamageType, HitLocation)` (name 129 of that package's
    name table), and `GamePlay.ScriptedController.LeaveScripting` writes `19 01 8d 07 00 00 1b 39 28 16` for
    `Pawn.SetWalking(false)` (name 57); neither callee is imported, exported or in `natives.csv` there. The
    name has to be in this package's own table, because that index is what the compiler wrote, **and** a
    function of that name has to be declared somewhere in the corpus -- which is what keeps `rot(...)` (a
    vector constructor no class declares) and the reconstructed text's `super` out of the call path. A package
    that *does* import a record of the name is left alone: the compiler named the record, not the name.
    """
    low = name.lower()
    idx = (env.get("names") or {}).get(low)
    if idx is None:
        return None
    for _idx, kind in ((env.get("imports") or {}).get(low) or []):
        if kind in ("Function", "DelegateFunction"):
            return None
    if low not in (env.get("corpus_funcs") or set()):
        return None
    return idx


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
        env = {k: v for k, v in env.items() if k not in ("byte_root", "int_site", "group_int")}
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
        idx = _by_name_index(stmt[0][1], env)
        if idx is None:
            _note(family="bare-call" if not receiver else "member-call-expr",
                  receiver=receiver or "", member=stmt[0][1].lower())
            raise Refused("a call to a name the class chain does not declare")
        head = bytes([0x1B]) + write_index(idx)
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
    # **One** trailing hole disappears and the rest are written. `ACTION_IfProximity.ProceedToNextAction` spells
    # `ForEach C.DynamicActors(class'Actor', currentActor,)` -- one trailing comma, and the shipped call is
    # `19 00 58 04 0d 00 00 61 39 20 84 00 7d 0b 16`: `EndFunctionParms` right after the second argument, no
    # `Nothing` where the third would sit. `Pawn.SurfaceReduceDamage` spells `GetClosestBone(HitLocation,
    # StartTrace, OutDist, OutSurface,,)` -- two trailing commas -- and ships **one** `Nothing` before
    # `EndFunctionParms` (111 bytes; stripping both read 110). So the source's last comma is the end-of-list marker
    # and every comma before it is an omitted optional: an empty run of length k writes k-1. Measured over the
    # corpus: 10 bodies byte-identical for it, none lost (`inventory/emit_differ.csv`).
    if args and not args[-1]:
        args.pop()
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
            # A hole **between** two arguments is the compiler's own `Nothing` (`0x0B`), one byte in the slot the
            # omitted argument would take: `Controller.PostBeginPlay`'s
            # `Spawn(PlayerReplicationInfoClass, Self, , vect(0,0,0), rot(0,0,0))` is
            # `16 | 0b 23 <vector> 22 <rotator> 16` -- `Nothing` where `SpawnTag` sits. Measured on the 176 residue
            # rows that leave a slot empty (174 of them spell `, ,` in the source). A trailing hole is a different
            # shape and is not written at all -- see the strip above.
            out += b"\x0b"
            continue
        # A byte-typed literal is written by `emit_expr`, which knows the form from the value (see the number
        # branch): `byte` takes ByteConst up to 254 and the int form for 255. Each argument is its own expression
        # root, so `_root` from an enclosing expression is cleared here.
        # The argument is its own expression root, so it also carries its **own** site type: `root_site` decides
        # where a parenthesised group is narrowed (`_group_narrow`), and one inherited from the statement's root
        # types the group at an ancestor's site instead -- `UWindowPulldownMenu.SetSelected`'s `FindEntry((Y -
        # VBorder) / ItemHeight)` was read as a float divide because the assignment's root is an object.
        out += emit_expr(arg, ptype, dict(((k, v) for k, v in env.items() if k not in ("_root", "root_site")),
                                          _arg=True, root_site=ptype))[0]
    return out + b"\x16"


def emit_assign(stmt, at, env):
    """`X = value;` -> `0F <InstanceVariable> <index> <value>`, or `14 2D <InstanceVariable> <index> <bool>`."""
    # An assignment's value is the one site where the compiler **retypes an int subtree** at the target's `float`
    # width (`W.WinHeight = 12 + MiscBevelT[2].H + MiscBevelB[2].H` is `Add_FloatFloat` all the way down). A
    # comparison's operand and a call's argument keep their own int arithmetic and cast at the node
    # (`Abs(BlendC - NewC)` and `ArrowWidth - 0.5 > int(ArrowWidth - 0.5) + 1` are `IntToFloat(Subtract_IntInt(...))`),
    # so the flag travels as `_assign` and the pair table reads it.
    env = dict(env, _assign=True)
    lhs, rhs = stmt[:at], stmt[at + 1:]
    if len(lhs) != 1 or lhs[0][0] not in ("ident", "keyword"):
        # The target is itself an expression: `H.LastPickupTime = H.Level.TimeSeconds;` is `0f <Context read> <value>`
        # and `Level.bClassicView = true;` is `14 <Context read> 27` -- Let versus LetBool is the *target's* declared
        # type, and the target is written exactly as a read of the same chain. Shapes that are not a plain member
        # (`modifiers.Length = 0;` writes `0f 37 <read> 0f 25`, an extra token this walk does not model) are refused.
        ptype = _type_of(lhs, env).lower()
        node, shape = emit_expr(lhs, "", env)
        if shape not in ("context", "bool-context", "struct-member", "element", "length"):
            # `A.Length = n;` is a `Let` whose target is the `37` length node: `Profiles.Length = 0;` is
            # `0f 37 01 7d 01 25` and `Recipients.Length = 0;` `0f 37 01 4a 03 25 04 27` -- the length node, then the
            # value, with no extra token (`0f` is the Let; the three bytes after `01` are the member's own index).
            raise Refused("an assignment whose target is a %s expression" % shape)
        if ptype == "bool" or shape == "bool-context":
            return bytes([0x14]) + node + emit_expr(rhs, "bool", env)[0]
        if ptype == "struct":
            # A struct-typed target is a plain `Let`: the copy is by value and the token carries the extent, so
            # `newRotation = Rotation;` is `0f <LocalVariable> <InstanceVariable>` with no member node of its own.
            return bytes([0x0F]) + node + emit_expr(rhs, "", env)[0]
        if ptype in ("delegate", "pointer", ""):
            # An array value is copied by a plain `Let` like a struct is: `SongArray = Songs;` is
            # `0f <SongArray> <Songs>`, with no node around either side and no cast.
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
    if ptype in ("delegate", "pointer", ""):
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
            return _enum_byte(hit.lower(), env)
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
        bbytes, _bm, _bs = emit_flow(body, rettype, dict(env, _exit={"break": jump_pos + 3, "continue": jump_pos}),
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
        # `07 1000` for a body that begins at wide 0x10, and the `Jump` a `continue` compiles to names that
        # `JumpIfNot`'s own position (`0x60`) -- the loop's test node, not the condition's first byte.
        _k, cond, body = node
        # A `continue` in a `do`/`until` names the trailing `JumpIfNot`'s own position -- the test node -- and a
        # `break` the position past the condition, so the body is emitted twice as in `while`.
        rough = emit_flow(body, rettype, dict(env, _exit={"break": None, "continue": None}), pos)[0]
        test_pos = pos + _wide(rough)
        cond_pos = test_pos + 3
        condb = emit_expr(cond, "bool", env)[0]
        bodyb, _bm, _bs = emit_flow(body, rettype, dict(env, _exit={"break": cond_pos + _wide(condb),
                                                                   "continue": test_pos}), pos)
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
    """The size byte a `Context` writes for a call's **value**: 0 for a void call, 4 for an object.

    The **enum** rule is applied here as well as in `_emit_member_call`: a call statement's value is discarded, but
    the compiler still writes the width of what it yields, and a callee declared to return `Actor.EMsgTone` yields
    a byte. Read it through the one producer (`_enum_byte`) rather than as a second table.
    """
    if not rtype:
        return 0
    rtype = _enum_byte((rtype or "").lower(), env)
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
    declares nothing wrote the `Function` import for both. Inside a state the record is the state's own: the state
    this one `extends` first (`GRENADE.ClientPreparingToFire` -> `PreparingToFire`), then the parent's same-state
    override (`Armor.BeginState` -> `Powerups.Activated.BeginState`, `UnrealPawn.Dying.BaseChange` -> the import
    `BaseChange <- Dying <- Pawn`), and only then the class scope.
    """
    low = name.lower()
    scope = env.get("scope") or {}
    chain = scope.get("chain") or {}
    pack = scope.get("pack") or ""
    rows = (env.get("func_owners") or {}).get(low, [])
    state = (env.get("state") or "").lower()
    pairs = env.get("import_pairs") or {}
    imp = [x for x in (env.get("imports") or {}).get(low, []) if x[1] == "Function"]

    def on(c, st):
        """The record of `F` on class `c` in state `st` (`""` = class scope): an export, else an import."""
        if st:
            hit = [x for x in rows if x[1] == c and x[2] == st]
            if hit:
                return hit[0][0]
            for x in imp:
                if pairs.get(x[0]) == (st, c):
                    return x[0]
            return None
        hit = [x for x in rows if x[1] == c and not x[2]]
        if hit:
            return hit[0][0]
        for x in imp:
            if pairs.get(x[0]) == ("", c):
                return x[0]
        return None

    # A state that `extends` another state names **that** state's function first: `GRENADE.ClientPreparingToFire
    # extends PreparingToFire`, so `Super.BeginState()` writes export 2073 (`GRENADE.PreparingToFire.BeginState`)
    # and `Super.Tick()` export 2071 -- neither is on the class parent at all. The state this one extends is
    # looked for on the body's own chain, because the clause names a state of this class or an ancestor.
    if state:
        up = (env.get("state_parents") or {}).get(state) or ""
        up = up.split(".")[-1]
        if up:
            own = (scope.get("cls") or "").lower()
            for _p, c in _ancestors(chain, pack, own or cls.lower()):
                hit = on(c, up)
                if hit is not None:
                    return hit
    # The parent's **same-state** override, then its class scope. Both halves of the chain carry state-owned
    # records: an export when the ancestor is in this package, an import whose owner pair is `(state, class)` when
    # it is not (`BeginState <- Dying <- Pawn` is -1044, and the class-scope import is -1116).
    for _p, c in _ancestors(chain, pack, cls.lower()):
        if state:
            hit = on(c, state)
            if hit is not None:
                return hit
    for _p, c in _ancestors(chain, pack, cls.lower()):
        hit = on(c, "")
        if hit is not None:
            return hit
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


def _prefix_call(obj, member, env):
    """`Static.F(args)` / `Global.F(args)` -> the callee's own node: the prefix names the class scope.

    `Static.` (spelled `Static.` and `static.`) writes no receiver node at all -- the compiler emits exactly the
    token a bare `F()` earns. Measured: `GameInfo.AllowMutator`'s `static.IsVehicleMutator(MutatorClassName)` is
    `1b 40 17 6f 4e 16`, `GameInfo.AddMutator`'s `!Static.AllowMutator(MutName)` is `1b 7f 16`, and
    `LocalMessage.GetRelatedString`'s `return Static.GetString(...)` the same shape. `Global.` is that call behind
    a `GlobalFunction` (`38 <record>`), measured in a statement (`Vehicle.VehicleCeaseFire`'s
    `38 50 06 2d 00 74 81 01 16`) and in a value (`ScriptedController.GetAggroLevel`'s
    `return Global.GetAggroLevel();` is `04 38 64 03 16`), so the statement path keeps `_emit_global_call`.

    Returns `(bytes, model, shape)`; a `string`-returning callee carries the `EatString` prefix, as any call does.
    """
    if obj[0][1].lower() == "global":
        return _emit_global_call(member, env)
    call = emit_call(member, env)
    if _callee_return_type(member[0][1], env) == "string":
        return bytes([0x0E]) + call, "static-prefix-call", "eat-string-static-prefix-call"
    return call, "static-prefix-call", "static-prefix-call"


def _emit_global_call(member, env):
    """`Global.F(args);` -> `38 <record> <args> 16`: the call with the state override stepped over.

    `global.F()` names the class-scope function even inside a state, and the compiler writes it as a
    `GlobalFunction` (0x38) over the **same record** a bare `F()` on the body's chain names:
    `Vehicle.VehicleCeaseFire`'s `Global.VehicleCeaseFire(bWasAltFire);` is `38 50 06 2d 00 74 81 01 16` --
    export 400 (`50 06`), the parameter read, `16` -- and `VehicleFire` is the same shape over export 345.
    82 residue rows (41 bodies) spell the keyword and every one of them refuses today on the receiver.

    A native, a delegate or a string-returning callee is refused rather than guessed: the head `0x38` earns for
    those has not been measured. `_wide` is not consulted because the node writes no skip word: `0x38` is a
    call token like `0x1b`/`0x1c`, not a `Context`.
    """
    call = emit_call(member, env)
    if not call or call[0] not in (0x1B, 0x1C):
        raise Refused("a `Global.` call whose callee is not a virtual or final function")
    node = bytes([0x38]) + call[1:]
    if _callee_return_type(member[0][1], env) == "string":
        # a statement call whose declaration returns `string` is prefixed with `EatString`, as any other is
        return bytes([0x0E]) + node, "global-call", "eat-string-global-call"
    return node, "global-call", "global-call"


# The dynamic-array intrinsics this build's walker carries, by the name the reconstructed text spells. The opcode
# table has exactly two *method* tokens -- `0x40` `DynamicArrayInsert` and `0x41` `DynamicArrayRemove`, each
# `expr,expr,expr` (array, index, count) -- beside `0x37` `DynamicArrayLength` and `0x10`
# `DynamicArrayElement`. UELib's `Find`, `FindStruct`, `Sort`, `Add`, `AddItem`, `RemoveItem`, `InsertItem` and
# `Empty` have no opcode here: 0x48..0x5f are the walker's bad-token range, and the source's `.Add(` rows this
# residue carries are `GUIList.Add` on a receiver class, not an array. And this build is version 130, below
# UELib's `EndTokenAppendedToArrayTokenIntrinsics` (649) and its skip-word version, so the node writes neither a
# skip word nor an `EndFunctionParms`: `Paths.Remove(0, Paths.Length)` ships `41 01 6f 09 25 37 01 6f 09` and
# `msgReceived.Insert(Idx, 1)` ships `40 01 7e 03 00 60 30 26`.
ARRAY_METHODS = {"insert": (0x40, ("int", "int")), "remove": (0x41, ("int", "int"))}


def _emit_array_method(obj, member, env):
    """`A.Remove(i, n);` / `A.Insert(i, n);` -> the intrinsic's own opcode, the array read, then its two int slots.

    The receiver is a *dynamic array* by its record, which is why this is a node and not a receiver-class join: no
    class declares `Remove`, and the compiler wrote no record for it. The array read goes through the ordinary
    expression path -- a bare property is an `InstanceVariable`, a member is the `Context` whose size byte is 0
    (`PROP_SIZE["array"]`) -- and each argument is an int site, whatever the argument's own type is.
    """
    tok, wants = ARRAY_METHODS[member[0][1].lower()]
    close = _paren_end(member, 1) if len(member) > 1 and member[1] == ("op", "(") else None
    if close is None or close != len(member) - 1:
        raise Refused("an array method call the statement walk cannot bound")
    args = _split_commas(member[2:close]) if member[2:close] else []
    if len(args) != len(wants):
        raise Refused("an array method with %d argument(s), where this build's token takes %d"
                      % (len(args), len(wants)))
    out = bytes([tok]) + emit_expr(obj, "", env)[0]
    for arg, want in zip(args, wants):
        if not arg:
            raise Refused("an array method whose argument the walk cannot read")
        out += emit_expr(arg, want, env)[0]
    return out, "array-method", "array-method"


def _stmt_member_call(stmt, env):
    """`A.F(args);` -> `19 <A> <skip> <size> <F>`: a call statement whose callee is a member of a receiver.

    The word is the call's own width in the uncompacted array (`_wide`) -- `PlayerController.SwitchWeapon` writes
    `19 01 05 0b 00 00 1b 61 03 00 4b 6e 16` for `Pawn.SwitchWeapon(F);` and the call is 11 wide. A property
    **import** behind the receiver is no exception *in statement position*: the corpus's three statement-shaped
    `Outer.<call>` sites write the call's own width -- `CheatManager.Fly`/`Ghost`'s
    `Outer.GotoState('PlayerFlying')` is `19 01 8e 07 00 00 71 21 6d 0f 16` (7) and `CheatManager.LockCamera`'s
    `Outer.SetLocation(Locked)` is `19 01 8e 08 00 00 ...` (8). The measured constant **5** belongs to the *value*
    position (`Outer.Log(...)` is `19 01 8e 05 00 00 1b 0f ...`) and to a `within` class's bare call
    (`_emit_within_call`), where `CheatManager.astral`/`puss` are `19 01 8e 05 00 00 1b 5b 15 16`. The size byte is
    the call's value size: 0 for a void call.
    """
    at = _dot_at(stmt)
    if at is None:
        return None
    obj, member = stmt[:at], stmt[at + 1:]
    if not _is_call(member):
        raise Refused("a statement that is a member read, not a call")
    # A `.static` link names the class object itself, so the receiver is the chain **before** it and the call is
    # written inside a `ClassContext` (`12`), the node `_emit_member_call` already writes for the same shape in a
    # return position. `PlayerController.ClientReStart`'s `G.Static.SetupInteractionsFor(Self)` -- `G` is a
    # `local class<GameInfo>` -- is `12 07 00 00 <G> 1b 5f 0f 2a 16`; read as a plain `Context` the receiver was
    # `G.Static`, which is no property of `GameInfo`, and the body refused.
    stat_at = _static_at(obj)
    if stat_at is not None:
        obj = obj[:stat_at]
    if len(obj) == 1 and obj[0][0] in ("ident", "keyword") and obj[0][1].lower() in ("static", "global"):
        # `Static.F(args);` / `Global.F(args);` name the class scope, not a member of an object called `Static` or
        # `Global` -- the same rule the value position applies (`_prefix_call`).
        node, _model, shape = _prefix_call(obj, member, env)
        return node, shape, shape
    if (member[0][0] in ("ident", "keyword") and member[0][1].lower() in ARRAY_METHODS
            and _receiver_kind(obj, env) == "array"):
        # `Paths.Remove(0, Paths.Length);` is neither a member of a class nor a by-name call: the receiver is a
        # dynamic array and the member is one of this build's two intrinsic tokens. `_class_of` cannot answer for
        # an array (there is no class), which is what put 120 bodies in the bare-name family.
        return _emit_array_method(obj, member, env)
    sup = _super_cast_class(obj, env)
    if sup:
        # `Super(C).F(args);` -- the statement spelling of the value path above: the callee's record behind `1c`
        # and no `Context`, the node `_super_call` writes for `Super.F(...)`.
        rec = _record_on(sup, member[0][1], env)
        if rec is None:
            raise Refused("a Super(C) call statement to a name the class's chain does not declare")
        node = emit_call(member, env, receiver=sup, head=bytes([0x1C]) + write_index(rec))
        if _rettype_on(sup, member[0][1], env) == "string":
            return bytes([0x0E]) + node, "super-cast-call", "eat-string-super-cast-call"
        return node, "super-cast-call", "super-cast-call"
    cls = _class_of(obj, env)
    if not cls:
        _note(family="member-call-stmt", expr=" ".join(v for _k, v in obj), member=member[0][1].lower(),
              receiver="")
        raise Refused("a call statement whose receiver's class the artifacts do not name")
    if _func_on(cls, member[0][1], env) is None and _by_name_index(member[0][1], env) is None:
        # The same rule the bare call uses: a callee the body's package carries no record of is written by
        # name index, and `emit_call` takes that path when it is reached -- so the pre-check must not refuse
        # the shape first. `Pawn.SetWalking(false)` in `ScriptedController.LeaveScripting` is exactly this:
        # `19 01 8d 07 00 00 1b 39 28 16`, name 57, no record of it in GamePlay.u.
        _note(family="member-call-stmt", expr=" ".join(v for _k, v in obj), member=member[0][1].lower(),
              receiver=cls)
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
    skip = _wide(memb)
    node = (bytes([CLASS_CONTEXT_TOKEN if stat_at is not None else CONTEXT_TOKEN])
            + emit_expr(obj, "", env)[0] + struct.pack("<H", skip)
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


def _owner_pair(pkg, kinds, ref, depth=3):
    """`(state, class)` for an object index: a state's own outer names the class that declares it.

    A `Super.BeginState()` inside a state names a function **owned by a state**, and the import table spells that
    ownership out: `StargateGame.u` carries `BeginState` six times with owners `Object`, `Activated/Armor`,
    `Idle/Weapon`, `ClientFiring/Weapon` and so on. Reading only the immediate owner name therefore compares
    `idle` against a class and can never match; the pair is the join.
    """
    if depth <= 0 or not ref:
        return ("", "")
    if ref > 0:
        if ref > len(pkg.exports):
            return ("", "")
        e = pkg.exports[ref - 1]
        nm = str(pkg.name(e["name"])).lower()
        if kinds.get(ref) == "State":
            st, cl = _owner_pair(pkg, kinds, e["outer"], depth - 1)
            return (nm, cl or st)
        return ("", nm)
    if -ref > len(pkg.imports):
        return ("", "")
    im = pkg.imports[-ref - 1]
    nm = str(pkg.name(im["name"])).lower()
    ikind = pkg.name(im["class"])
    nxt = im.get("package", 0)
    nxt = nxt - 2 ** 32 if nxt > 2 ** 31 else nxt
    if ikind == "State":
        st, cl = _owner_pair(pkg, kinds, nxt, depth - 1)
        return (nm, cl or st)
    if ikind in ("Class", "ScriptStruct"):
        return ("", nm)
    return ("", "")


def _class_tables(pkg, kinds, names):
    """The package's own records as the compiler addressed them: property and function exports, the imports, and
    the classes an object cast can name."""
    props, funcs, imports, classes, import_owners, import_pairs = {}, {}, {}, {}, {}, {}
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
        # ...and the same owner read as a `(state, class)` pair, because a function owned by a *state* is what a
        # `Super.F()` inside a state names. `import_owners` keeps the bare name for the struct joins.
        import_pairs[-(j + 1)] = _owner_pair(pkg, kinds, ref)
        if ikind == "Class":
            classes.setdefault(iname, []).append((-(j + 1), "Class"))
    return props, funcs, imports, classes, import_owners, import_pairs


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
           dims_by_cls=None, consts_by_cls=None, within=None):
    """The names one body may mention, each with the index and token the package's own record earns.

    A property is found by *name and owner*: the record's index is what the compiler wrote, so nothing is rebuilt.
    A callee is resolved by walking the ancestor chain to the class that declares it -- the native table says the
    token (0 means "not a native opcode, call it by name"), and its `final` flag picks the call token.
    """
    props, funcs, imports, classes, _import_owners, _import_pairs = tables
    scope = {"props": {}, "locals": {}, "funcs": {}, "classrefs": {}, "props_all": props, "chain": chain,
             "pack": pack, "cls": cls.lower(),
             # the class that declares each property this body reads: `_declaring_class` answers a struct-typed
             # receiver's own copy through it (two `DriverWeaponStruct` copies, one per class)
             "prop_cls": {},
             # a property the package exports once, on a class outside this body's chain, is read through `Outer`
             "outer_props": set(),
             # every name that fallback picked, whatever its type: a `within` call's argument reads it through
             # `Outer` too (`CheatManager.CauseEvent` passes `Pawn`, which is `PlayerController`'s property)
             "outside_props": set(),
             # a name whose record is declared on the class this body is declared `within` (or its ancestors) and
             # not on the body's own chain: every read of it is compiled in `Outer`'s scope, whatever its type
             "within_props": set(),
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

    def _by_owner(imp):
        """An ambiguous import name is the one whose owner is on this body's chain.

        Two classes in another package declare the name, and the import table lists both: `Condition` and
        `ScriptScene` (both Engine.u) each carry `var Screenplay _ParentScreenplay;`, so
        `Cond_WhenPlayerTriggerChanges`'s `_ParentScreenplay.GetPlayerController()` had no single candidate and
        refused as *a name neither a local, a property nor an enum constant*. The import's `package` field is the
        owning class, and only one owner is an ancestor -- the same join `_member_record` applies to a receiver's
        ambiguous import.
        """
        if len(imp) > 1:
            owned = [c for c in imp if (_import_owners.get(c[0]) or "") in set(order)]
            if len(owned) == 1:
                return owned
        return imp
    scope["order"] = order
    # The `within` class's chain, so a property record can be attributed: a name declared there and not on this
    # body's chain is read through `Outer` (`CheatManager` -> `PlayerController`, whose `Pawn`, `Level`, `myHUD`
    # and `Weapon` are the names its cheats spell bare).
    within_chain = [c for _p, c in _ancestors(chain, pack, within)] if within else []
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
                # `Doors[MAX_DOORS]`: the dimension is a `const` this class or an ancestor declares, and the
                # table holds the const's *literal text*, so a float const resolves to no dimension rather than
                # folding `35.0` into an `ArrayCount`.
                n = _const_int(cst.get(n))
                if n is None:
                    continue
            dim.setdefault(nm, n)
    scope["dims"] = dim
    # The `const NAME = <literal>;` table itself, which `ArrayCount` already read and a bare `NUMTEAMS` did not:
    # a const is a compile-time literal, so `emit_expr` substitutes it at the site.
    scope["consts"] = cst
    # A **struct member**'s record is a property whose owner is a `Struct` export, or an import whose owner is a
    # struct in another package: `PathName.FullPath` is this package's export 3007 (owner `FilePath`), and `Dest.Z`
    # is import 21 of AmbientCreatures (`36 95`, owner `Vector`). The member's *name* alone is ambiguous -- Engine
    # declares `z` on half a dozen classes -- so the record is keyed by the (struct, member) pair the source spells,
    # which is the one a receiver's declared type names.
    struct_cands = {}
    # A struct's own **outer** is the class that declares it, and that is what makes a duplicate name resolvable:
    # `StargateGame.u` exports two `DriverWeaponStruct`s, one nested in `StargateVehicle` and one in
    # `StargateWeaponEmplacement`, so the member records' `(struct, member)` pairs collide while their declaring
    # classes differ. Keying the candidate on the struct's *name* alone would have lost exactly that.
    struct_outer = {}
    for i, e in enumerate(pkg.exports):
        if kinds.get(i + 1) in STRUCT_KINDS:
            struct_outer[i + 1] = names.get(e["outer"], "").lower()
    for i, e in enumerate(pkg.exports):
        outer = e["outer"]
        if outer > 0 and kinds.get(outer) in STRUCT_KINDS and kinds.get(i + 1) in PROPERTY_CLASSES:
            key = (names.get(outer, "").lower(), names.get(i + 1, "").lower())
            struct_cands.setdefault(key, []).append((i + 1, kinds[i + 1], struct_outer.get(outer, "")))
    for nm, cands in imports.items():
        for idx, kind in cands:
            owner = _import_owners.get(idx)
            if owner and kind in PROPERTY_CLASSES:
                struct_cands.setdefault((owner, nm), []).append((idx, kind, ""))
    # The key, with **every** record it holds and the class that exports each one. Nine nodes of the corpus's
    # 5,128 are ambiguous -- `Engine.u` nests `Orientation` in both `SceneManager` and `MatObject`, and the two
    # `DriverWeaponStruct` copies in `StargateGame.u` are nested one per class -- so the pair alone cannot decide
    # them; `_struct_record` asks the declaring class first (see `_declaring_class`).
    scope["struct_cands"] = struct_cands
    # The **struct itself** as the compiler names it: a `StructCmpEq`/`StructCmpNe` operand is an object reference
    # to the struct record (`0x33 8c` for `color` is import 12 of UWindow.u), so the compare needs the same kind of
    # table `classrefs` is for a cast. An export wins, as it does there, because a package re-declaring a struct is
    # naming its own record.
    structrefs = {}
    for i, e in enumerate(pkg.exports):
        if kinds.get(i + 1) in STRUCT_KINDS:
            structrefs.setdefault(names.get(i + 1, "").lower(), i + 1)
    for nm, cands in imports.items():
        if nm in structrefs:
            continue
        own = [idx for idx, kind in cands if kind in STRUCT_KINDS]
        if len(own) == 1:
            structrefs[nm] = own[0]
    scope["structrefs"] = structrefs
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
            imp = _by_owner([c for c in imports.get(nm, []) if c[1] in PROPERTY_CLASSES])
            if len(imp) == 1:
                scope["props"][nm] = (INSTANCE_TOKEN, imp[0][0], PROP_TYPE.get(imp[0][1], ""))
                # The import's own `package` field is the class that declares it -- the declaring class a
                # struct-typed receiver is resolved through (`_declaring_class`).
                scope["prop_cls"][nm] = (_import_owners.get(imp[0][0]) or "").lower()
                if within_chain and (_import_owners.get(imp[0][0]) or "") in within_chain \
                        and (_import_owners.get(imp[0][0]) or "") not in order:
                    scope["within_props"].add(nm)
                continue
            if within_chain:
                # A name the class this body is declared `within` declares is read in `Outer`'s scope, and the
                # within chain is the authority for *which* record: `Level` has five records in Engine.u (Actor's
                # object property and four unrelated properties of that name), so the two fallbacks below -- a
                # single import candidate, a single candidate in the whole package -- both missed it, and every
                # `Level.Game.…` read in `CheatManager`/`AdminBase` refused although the within chain carries it.
                for anc in within_chain:
                    hit = [c for c in cands if c[1] == anc]
                    if hit:
                        picked = hit[0]
                        break
            if picked is None and len(cands) == 1:
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
            # The class whose record answered -- the declaring class of the property, which is what a
            # struct-typed receiver's own copy is nested in.
            scope["prop_cls"][nm] = (picked[1] or "").lower()
            if within_chain and (picked[1] or "") in within_chain and (picked[1] or "") not in order:
                scope["within_props"].add(nm)
    # A property this package only *imports* -- an ancestor in another package declares it and no class here
    # re-declares it -- is still a record the compiler wrote, with a negative index: `Corroded.GetPawnDamageEffect`
    # is `04 02 d8 02` (`Default.PawnDamageEffect`, import 152) and `VoiceChatReplicationInfo.JoinChannelAt` reads
    # its `Level.Game`-style names the same way. The class chain is not the authority here; the package's own
    # import table is.
    for nm, cands in imports.items():
        if nm in scope["props"]:
            continue
        imp = _by_owner([c for c in cands if c[1] in PROPERTY_CLASSES])
        if len(imp) == 1:
            scope["props"][nm] = (INSTANCE_TOKEN, imp[0][0], PROP_TYPE.get(imp[0][1], ""))
            scope["prop_cls"][nm] = (_import_owners.get(imp[0][0]) or "").lower()
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
    # A delegate declared by an **ancestor in another package** is a property *import*, not an export, so the loop
    # above cannot see it: `GUI.GUIPage` declares `__OnOpen__Delegate` and the eight `XBoxLive*Menu.MyOnReopen`
    # bodies call `OnOpen()`, which the package writes `43 de 01 49 02 16` -- import **-94** then the callee's name
    # index -- where this walk had no entry and wrote a plain `VirtualFunction` (`1b 49 02 16`), two bytes short.
    # The owner is the import's own `package` field, resolved as `funcs` resolves it.
    for nm, cands in imports.items():
        if not (nm.startswith("__") and nm.endswith("__delegate")):
            continue
        if nm[2:-len("__delegate")] in scope["delegates"]:
            continue
        picked = None
        for anc in order:
            hit = [c for c in cands if (_import_owners.get(c[0]) or "") == anc and c[1] == "DelegateProperty"]
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


def _local_decls(text):
    """`[(function, name, type word, class-ref target)]` for every `local` declaration in one class file.

    A declaration belongs to the function whose body encloses it, and that is the join a local's type needs: a
    class-scoped table keyed by `(class, name)` keeps the *first* declaration of a name, so
    `StargateHUD.ToggleGameHUD`'s `local GameHUD G;` was read as a later function's `local class<GameInfo> G;` --
    `G.HideHud(!G.bHideHud)` then joined on `GameInfo`, found no `HideHud`, and the body was refused. Two `local`s
    of one name in one function is a compile error, so the function is the whole scope.
    """
    toks = tokens_of(text)
    if toks is None:
        return []
    out, fn, i, depth = [], "", 0, 0
    while i < len(toks):
        kind, val = toks[i]
        low = val.lower() if kind == "keyword" else ""
        if kind == "op" and val == "{":
            depth += 1
        elif kind == "op" and val == "}":
            depth -= 1
        elif low in ("function", "event", "delegate"):
            # the header's name is its last word before `(`; `function bool Pawn.Foo(` carries the class too, and
            # the *name* is what the package's export is called
            j, words = i + 1, []
            while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] in ("(", ";")):
                if toks[j][0] in ("ident", "keyword"):
                    words.append(toks[j][1])
                j += 1
            if words:
                fn = words[-1].lower()
            i = j
            continue
        elif low == "local" and depth > 0 and fn:
            j, segs, cur = i + 1, [], []
            while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] == ";"):
                if toks[j][0] == "op" and toks[j][1] == ",":
                    segs.append(cur)
                    cur = []
                else:
                    cur.append(toks[j])
                j += 1
            segs.append(cur)
            # the type is written once for the whole statement: `local int X, Y;` and `local actor Other, PathTarget;`
            # name two locals of one type, so it comes from the first segment and every name shares it
            first = segs[0] if segs else []
            words = [k for k, t in enumerate(first) if t[0] in ("ident", "keyword")]
            if words:
                typew = " ".join(t[1] for t in first[:words[-1]]).strip().lower()
                ref = _class_ref_class(typew)
                if ref:
                    typew = "class<%s>" % ref
                for seg in segs:
                    ws = [k for k, t in enumerate(seg) if t[0] in ("ident", "keyword")]
                    if ws:
                        out.append((fn, seg[ws[-1]][1].lower(), typew, ref))
            i = j
            continue
        i += 1
    return out


def _source_index(tree):
    """`{(class, state, function): (rettype, body, text, native, final)}` from the extracted corpus, plus every
    declaration's parameter list, every enum constant and every callee's return type -- the three joins a cast's
    slot and an argument's token need -- and every `local Type Name;`, which is how a receiver's class is read when
    the receiver is a local rather than a property."""
    index = {}
    dis = _dis()
    params = {}
    rets = {}
    # `(class, name)` of every declaration that names **no return type** (`event`s and `iterator` functions). It is
    # kept apart from `rets`, whose rows exist to answer a *type word*: a cast's slot join reads `rets`, and a call's
    # value size needs the other question -- "is there a declaration, and does it yield anything at all".
    voids = set()
    localtypes = {}
    localtypes_fn = {}
    classrefs_fn = {}
    proptypes = {}
    classrefs = {}
    withins = {}
    dims, consts = {}, {}
    enums, enum_names = {}, set()
    enum_members = collections.defaultdict(set)
    proptypes_mod = {}
    stateparents = {}
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
            # A state header names the state it `extends` (`state ClientPreparingToFire extends PreparingToFire`),
            # and that clause is the `Super` a `Super.F()` inside it resolves to -- the class parent is only the
            # fallback. Read once per file, keyed by class and state.
            for st, up in dis.state_parents(text).items():
                stateparents.setdefault(cls, {}).setdefault(st, up)
            # A class declared `within X` resolves an unqualified name against X: `CheatManager extends Object
            # within PlayerController` calls `DeactivateAstralSight()` bare and the compiler writes it through
            # `Outer` (`19 01 8e 05 00 00 <call>`). Only five classes in this corpus carry the clause.
            m = re.search(r"(?is)\bclass\s+[A-Za-z_]\w*\s+extends\s+[A-Za-z_]\w*[^;{]*?\bwithin\s+([A-Za-z_]\w*)",
                          text[:4000])
            if m:
                withins[cls] = m.group(1).lower()
            for m in re.finditer(r"\blocal\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)", text, re.I):
                localtypes.setdefault((cls, m.group(2).lower()), m.group(1).lower())
            # ...and the same two joins attributed to the function that declares them, which is a local's real
            # scope: the class-scoped tables above keep only the *first* `local` of a name, so a name declared
            # twice in one class read as its first declaration in both functions (`_local_decls`).
            for fnm, lname, ltype, lref in _local_decls(text):
                localtypes_fn.setdefault((cls, fnm), {})[lname] = ltype
                if lref:
                    classrefs_fn.setdefault((cls, fnm), {})[lname] = lref
            # A property's *declared type word*, which is how a receiver's class is named when the package's own
            # artifact cannot: `StargateMenus.ProfilePanel` has no row for `Automated ProfileList ProfileList` in
            # `class_properties.csv`, so `ProfileList.ItemCount()` had no class to join on.
            for m in re.finditer(r"(?m)^[ \t]*(?:var|Automated)\s*(?:\([^)]*\))?\s*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)",
                                 text, re.I):
                proptypes.setdefault((cls, m.group(2).lower()), m.group(1).lower())
            # ...and the same declaration with its **modifiers stepped over**: `var const LevelInfo Level;` reads
            # as type `const` above, and the property a `within` body needs the class of (`CheatManager`'s `Level`)
            # is exactly that spelling. This table feeds only the `within` join, so nothing else moves.
            for m in re.finditer(r"(?m)^[ \t]*(?:var|Automated)\s*(?:\([^)]*\))?\s*"
                                 r"(?:(?:const|config|private|protected|public|transient|native|editconst|"
                                 r"editinline|editinlineuse|export|noimport|duplicatetransient|localized|"
                                 r"input|travel)\s+)*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)", text, re.I):
                proptypes_mod.setdefault((cls, m.group(2).lower()), m.group(1).lower())
            # ...and a `class<Z>` declaration's **target**, which both regexes above stop short of: their type
            # group is one word, so `var class<Ammunition> AmmoName;` and
            # `local class < StargateWeapon > WeaponClass;` put nothing in either table. `Z` is the class a
            # `.Default` member of the receiver is read on (`AmmoName.Default.MaxAmmo` -> `Ammunition`).
            for m in re.finditer(r"(?m)^[ \t]*(?:var|local|Automated)\s*(?:\([^)]*\))?\s*class\s*<\s*"
                                 r"([A-Za-z_]\w*)\s*>\s+([A-Za-z_]\w*)", text, re.I):
                classrefs.setdefault((cls, m.group(2).lower()), m.group(1).lower())
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
                elif not rettype and not state:
                    voids.add((cls, fn.lower()))
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
                        # a **qualified** constant names its enum as well as the constant, so the pair is the key
                        # and the ordinal is unambiguous even where two classes spell the name differently
                        enum_members[(enum.lower(), cname.lower())].add(ordinal)
    return (index, files, params, enums, enum_names, rets, localtypes, proptypes, withins, dims, consts, stateparents,
            classrefs, localtypes_fn, classrefs_fn, (enum_members, proptypes_mod), voids)


def score(tree, dump, out_dir, residue_dir=None, only=None):
    dis = _dis()
    from ue2pkg import Package                                  # noqa: PLC0415 - only with a real dump

    index, files, params, enums, enum_names, rets, localtypes, proptypes, withins, dims, consts, stateparents, \
        classrefs, localtypes_fn, classrefs_fn, (enum_members, proptypes_mod), voids = _source_index(tree)
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
    # The qualified spelling's table: `(enum, constant)` -> ordinal, kept only where every declaration agrees.
    # `ERenderStyle.STY_Normal` is 1 in `Actor.uc` and in `SGMenuInteraction.uc`, so the pair answers; a pair two
    # classes spell at different ordinals is dropped rather than guessed.
    enum_consts = {}
    for (enum, cname), ords in enum_members.items():
        if len(ords) == 1:
            enum_consts.setdefault(enum, {})[cname] = next(iter(ords))
    proptypes_mod_by_cls = {}
    for (c, n), t in proptypes_mod.items():
        proptypes_mod_by_cls.setdefault(c, {})[n] = t
    localtypes_by_cls = {}
    for (c, n), t in localtypes.items():
        localtypes_by_cls.setdefault(c, {})[n] = t
    proptypes_by_cls = {}
    for (c, n), t in proptypes.items():
        proptypes_by_cls.setdefault(c, {})[n] = t
    classrefs_by_cls = {}
    for (c, n), t in classrefs.items():
        classrefs_by_cls.setdefault(c, {})[n] = t
    # Every function name the corpus's text declares, which is the guard the by-name call needs: the index the
    # compiler wrote is this package's, but "is this a function at all" is the corpus's answer.
    corpus_funcs = {key[2] for key in index}
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
            wcls = withins.get(cls.lower(), "")
            scope = _scope(pkg, kinds, names, tables, pack, cls, ei + 1, chain, natives, table_index, final_of,
                           dims_by_cls, consts_by_cls, within=wcls)
            # The declared type of every property the `within` class's chain spells, nearest ancestor first: the
            # package's class table carries no row for a native record (`Pawn`, `Level`), so the source text is the
            # only place the class of `CheatManager`'s bare names is written.
            within_proptypes = {}
            if wcls:
                for _wp, _wc in _ancestors(chain, pack, wcls):
                    for _n, _t in proptypes_mod_by_cls.get(_wc, {}).items():
                        within_proptypes.setdefault(_n, _t)
            env = {"names": table_index, "scope": scope, "params": params, "enums": enums,
                   "corpus_funcs": corpus_funcs,
                   "enum_names": enum_names, "enum_by_name": enum_by_name, "enum_consts": enum_consts,
                   "within_proptypes": within_proptypes,
                   "class_names": class_names, "order": scope["order"],
                   "rets": rets, "voids": voids, "assert_line": aline,
                   # the three joins a member call's callee needs: where its declaration sits, its `final` flag,
                   # and the records of the package it is declared in
                   "natives": natives, "funcs_all": tables[1], "imports": tables[2], "final_of": final_of,
                   "import_owners": tables[4], "import_pairs": tables[5],
                   # a local's declared type: the class-wide table (for a name the class declares once) extended by
                   # the *function's own* declarations, which is a local's real scope. `StargateHUD.ToggleGameHUD`
                   # declares `local GameHUD G` while a later function of the same class declares
                   # `local class<GameInfo> G`, and the class-wide table kept only the first for both bodies --
                   # `G.HideHud(!G.bHideHud)` joined on `GameInfo` and the body was refused.
                   "dims_by_cls": dims_by_cls,
                   "localtypes": dict(localtypes_by_cls.get(cls.lower(), {}),
                                      **localtypes_fn.get((cls.lower(), fname.lower()), {})),
                   "proptypes": proptypes_by_cls.get(cls.lower(), {}),
                   # a `class<Z>` declaration's target class: `local class<StargateWeapon> WeaponClass`, whose
                   # `.Default` member is read on `StargateWeapon` and not on the word `class`. Per function for the
                   # same reason as `localtypes`, and it is the table `_class_of` consults *first*.
                   "classreftypes": dict(classrefs_by_cls.get(cls.lower(), {}),
                                        **classrefs_fn.get((cls.lower(), fname.lower()), {})),
                   # a parameter's struct name is in its own declaration (`Vector momentum`), and the package's
                   # record only says `StructProperty`
                   "paramtypes": {p[1].lower(): p[0].lower() for p in (decl_params or [])},
                   # the class this one is declared `within`, whose names resolve through `Outer`
                   "within": withins.get(cls.lower(), ""),
                   # which state the body is written in: a `Super.F()` prefers the parent's same-state override
                   "state": state.lower(),
                   # ...and the state that one `extends`, whose functions are its own `Super` before the class parent
                   "state_parents": stateparents.get(cls.lower(), {}),
                   # a function record's owning class and state, walked past the state's own Outer
                   "func_owners": func_owners}
            REFUSAL.clear()
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
                                    "theirs": body.hex(),
                                    "family": REFUSAL.get("family", ""),
                                    "expr": REFUSAL.get("expr", ""),
                                    "member": REFUSAL.get("member", ""),
                                    "receiver": REFUSAL.get("receiver", ""),
                                    "why": REFUSAL.get("why", "")})
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
            fields = ["package", "class", "state", "function", "rettype", "reason", "source", "theirs", "ours",
                      "family", "expr", "member", "receiver", "why"]
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
function float WrapFloat()    { return 9999999999; }
function float WrapFloatNeg() { return -9999999999; }
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
function CallHoleNone() { Mix5(true,); }
function CallHoleTrailTwo() { Mix5(true,,); }
function CallHoleTrailThree() { Mix5(true,,,); }
function CallHoleMiddle() { Mix5(true, , 2); }
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
function bool NoIndexOp() { return Health ClockwiseFrom Health; }
function BoolMember() { return Target.bFlag; }
function AsRotator() { return rotator(SquadDir); }
function rotator RotZero() { return rot(0,0,0); }
function rotator RotPitch() { return rot(16384,0,0); }
function rotator RotNegative() { return Rot(-16384,0,0); }
function rotator RotExpression() { return rot(Slot,0,0); }
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
function WhileContinue() { while (bDead) { if (bFlag) continue; Health = 1; } }
function DoContinue() { do { if (bFlag) continue; Health = 1; } until (bDead); }
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
function Box TakeBox() { return Target.GetBox(); }
function Region TakeRegion() { return Target.GetRegion(); }
function CallHelper() { TargetH.Pong(); }
function CallUnknown() { Target.Missing(); }
function bool NotFlag() { return !bDead; }
function NotSet() { bDead = !bDead; }
function bool NotAnd() { return !bDead && bFlag; }
function int NegInt() { return -Health; }
function bool NotCall() { return !Target.bFlag; }
function bool ClassRefDefault() { local class<Probe> Cls; return Cls.Default.bDead; }
function Nothing StaticStmt() { Cls.Static.Ping(); }
function int ProductCast() { return 2 * class<Probe>(Target).Default.Health; }
function string ClassCall() { return class'Probe'.static.Say("x"); }
function string BadStatic() { return class'Missing'.static.Say("x"); }
function Actor CastChain() { return Probe(Target).Default.Target; }
function string LitDefaultText() { return class'Probe'.Default.Caption; }
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
function CallGlobal() { Global.Ping(); }
function CallGlobalNative() { Global.Destroy(); }
function int StaticValue() { return Static.Foo(1); }
function int StaticLowerValue() { return static.Foo(1); }
function int GlobalValue() { return Global.Foo(1); }
function byte EnumMember() { return ERenderStyle.STY_Normal; }
function int BadEnumMember() { return ERenderStyle.STY_Missing; }
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
                   "items": (0x01, 700, "array"), "fcount": (0x01, 640, "float"),
                   "mats": (0x01, 701, "array"), "songs": (0x01, 702, "array"),
                   "others": (0x01, 703, "array"), "targeth": (0x01, 614, "object")},
         "chain": {("probe", "probe"): ("", ""), ("probe", "helper"): ("", "")},
         "pack": "probe", "cls": "probe",
         "locals": {"slot": (0x00, 611, "int"), "count": (0x00, 612, "byte"),
                    "cls": (0x00, 613, "class")},
         "classrefs": {"probe": 4, "actor": -77},
         "funcs": {"destroy": ("native", 279), "myfinal": ("final", 763), "myvirtual": ("virtual", 39),
                   "foo": ("virtual", 41), "bar": ("virtual", 44), "baz": ("native", 705),
                   "mix": ("virtual", 46), "bump": ("virtual", 47), "gotostate": ("native", 113),
                   "tinytoken": ("native", 2), "onchange": ("virtual", 26),
                   "mix3": ("virtual", 48), "mixbyte": ("virtual", 49), "mytext": ("virtual", 50),
                   "ping": ("virtual", 51), "pong": ("virtual", 52), "say": ("virtual", 53),
                   "iter": ("native", 300), "takeclass": ("virtual", 55), "mix5": ("virtual", 56)},
         "delegates": {"onchange": 51}}

# The receiver walk's join: `Target` is a property of class `Probe`, so `Target.Health` is `Probe.Health`.
SCOPE["objclass"] = {("probe", "target"): "probe", ("probe", "targeth"): "helper"}

# The declared dimension of an array property, which `ArrayCount` folds to (`var int Items[4];`).
SCOPE["dims"] = {"items": 4}

# The raw record table the receiver walk reads: `{name: [(index, owner, kind)]}`.
SCOPE["props_all"] = {nm: [(v[1], "probe", "ObjectProperty" if v[2] == "object" else
                            "BoolProperty" if v[2] == "bool" else
                            "StructProperty" if v[2] == "struct" else "IntProperty")]
                      for nm, v in SCOPE["props"].items()}

# The record table spells a property's own kind, so a `StrProperty` must not be folded into `IntProperty`: a string
# member's `Context` writes size 0 (`MyNumericEdit.Value` is `05 00 00`), and reading `Caption` as an int adds a
# `39 53` cast that the shipped bytes do not carry.
SCOPE["props_all"]["caption"] = [(5, "probe", "StrProperty")]
# An array member's own record (the node `_member_record` reads), distinct from a fixed dimension on another kind.
SCOPE["props_all"]["mats"] = [(701, "probe", "ArrayProperty")]
SCOPE["props_all"]["songs"] = [(702, "probe", "ArrayProperty")]
SCOPE["props_all"]["others"] = [(703, "probe", "ArrayProperty")]

# The type words the package artifact spells: `SquadDir` is a `Vector`, and `bFlag` is a `bool` member.
SCOPE["friendly"] = {("probe", "probe", "squaddir"): "vector", ("probe", "probe", "bflag"): "bool",
                     ("probe", "probe", "health"): "int", ("probe", "probe", "bdead"): "bool",
                     ("probe", "probe", "flag2"): "bool", ("probe", "probe", "target"): "probe",
                     ("probe", "probe", "targeth"): "helper",
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
# Two required parameters and two optionals: the four hole shapes of a call's argument list need a callee that can
# hold them (`Mix5(true,)`, `Mix5(true,,)`, `Mix5(true,,,)`, `Mix5(true, , 2)`).
PARAMS[("probe", "mix5")] = [("bool", "Flag", False), ("int", "B", True),
                             ("int", "C", True), ("float", "D", True)]
PARAMS[("probe", "say")] = [("string", "S", False)]
# A `foreach`'s head is a call whose last argument is the loop variable, and a class literal is passed where a
# `class` is declared -- the two shapes the statement walk's new families emit.
PARAMS[("probe", "iter")] = [("int", "Item", False)]
PARAMS[("probe", "takeclass")] = [("class", "C", False)]
# A **member** call's parameter list is its *receiver's* declaration: `Pong` is one argument on `Probe` and no
# argument on the class `TargetH` holds, so a fallback to the body's chain would refuse `TargetH.Pong()` as
# `0 argument(s) to a 1-parameter callee`.
PARAMS[("helper", "pong")] = []
ENUMS = {("probe", "probe_walking"): (1, "EProbe"), ("probe", "probe_swimming"): (3, "EProbe")}
ENV = {"names": {"ping": 51, "pong": 52, "say": 53, "getbox": 54, "getregion": 55}, "scope": SCOPE,
       "params": PARAMS, "enums": ENUMS,
       "enum_names": {"eprobe"},
       "class_names": {"probe", "helper"}, "order": ["probe"],
       "rets": {("probe", "bump"): "int", ("probe", "mytext"): "string", ("probe", "say"): "string",
                ("probe", "getbox"): "box", ("probe", "getregion"): "region"},
       # the receiver walk's own join: a function *record* of the synthetic class, which a member call resolves on
       "funcs_all": {nm: [(1, "probe")] for nm in ("ping", "pong", "say", "getbox", "getregion")},
       "assert_line": 2086, "paramtypes": {}, "enum_consts": {"erenderstyle": {"sty_normal": 1}},
       "within_proptypes": {},
       # the source-side `class<Z>` table: a local's declared target class, which the class table does not carry
       "classreftypes": {"cls": "probe"},
       # the package's own import table: `{name: [(negative ref, kind)]}`. A no-index operator's record is an
       # import (`-261` is `ClockwiseFrom_IntInt` in GamePlay.u), and a name imported **as a `Class`** is a class
       # the class census has no row for (`Font`, `Viewport`, `StaticMesh`).
       "imports": {"clockwisefrom_intint": [(-261, "Function")], "font": [(-37, "Class")]}}

# `Pong` is a record of *both* classes: the receiver's chain picks the one `TargetH` holds, which is the `helper`
# spelling with no parameters.
ENV["funcs_all"]["pong"] = [(1, "probe"), (2, "helper")]

WANT = {
    "yes": "0427040b47",
    "no": "0428040b47",
    "zero": "0425040b47",
    "one": "0426040b47",
    "two": "042c02040b47",
    "big": "041d9a020000040b47",
    "neg": "041dffffffff040b47",
    # An int literal past the int32 range wraps before the float conversion: `BotEnemy.PickDestination`'s
    # `BestScore = -9999999999;` ships `1e c817a8ce` (-1410065408.0); without the sign, `1e c817a84e`.
    "wrapfloat": "041ec817a84e040b47",
    "wrapfloatneg": "041ec817a8ce040b47",
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
    # An empty run in a parameter list is `k-1` `Nothing`s, not k and not none: the source's last comma is the
    # end-of-list marker and every comma before it is an omitted optional. Corpus witnesses, one per shape:
    # `ACTION_IfProximity.ProceedToNextAction` (k=1, no placeholder), `Pawn.SurfaceReduceDamage`'s
    # `GetClosestBone(..., OutSurface,,)` (k=2, one `0b`), `SurfaceInfo.InitSurface`'s `Spawn(class'SoundTrigger',,,)`
    # (k=3, two), and `Controller.PostBeginPlay`'s `Spawn(..., , vect(0,0,0), ...)` (a hole between two arguments,
    # one `0b`). The first reading of the corpus wrote k and cost 13 bodies a byte and `SearchLight.SearchTick`.
    "callholenone": "1b382716040b47",
    "callholetrailtwo": "1b38270b16040b47",
    "callholetrailthree": "1b38270b0b16040b47",
    "callholemiddle": "1b38270b2c0216040b47",
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
    # `rot(pitch, yaw, roll)` is the compiler's own rotator literal -- `22` and three **i32** (`22 00 40 00 00`
    # is `rot(16384,0,0)`, measured on `PhysicsVolume.PlayExitSplash`), where `vect` writes f32.
    "rotzero": "0422000000000000000000000000040b47",
    "rotpitch": "0422004000000000000000000000040b47",
    "rotnegative": "042200c0ffff0000000000000000040b47",
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
    # A receiver declared `class<Z>` reads its member on `Z` and writes a **ClassContext** (`12`) whatever the
    # member is: `InputClass.default.bEnableDodging` is `12 01 53 1a 06 00 04 2d 02 7b 16`
    # (`PlayerController.SetDodging`). A bool member keeps the `2d` BoolVariable inside the node and the skip word
    # covers it (`Vehicle.ClientKDriverLeave` writes `19 01 1b 06 00 04 2d 02 59 0a` over an object receiver).
    "classrefdefault": "04120065090600042d022d040b47",
    # A `.static` call in a **statement** is the same `ClassContext` a return position writes: `Cls` is the probe's
    # `local class<Probe>`, the skip word is `_wide` of the call (`1b 33 16` is 3 bytes with a one-byte index, so 6)
    # and `Ping` returns nothing, so the value size is 0. Shipped: `PlayerController.ClientReStart`'s
    # `G.Static.SetupInteractionsFor(Self)` is `12 07 00 00 59 42 1b 5f 0f 2a 16` -- a two-byte index, so 7.
    "staticstmt": "120065090600001b3316040b47",
    # A `class<Z>( )` **inside** a larger expression: the `*` is the operator, and the cast is the `ClassContext`
    # over a `MetaClassCast` -- `12 13 04 01 63 09 05 00 04 02 6e 04`, the shape `WeaponPickup.PostBeginPlay`
    # ships as `12 05 00 04 13 0f | 02 74 07` over an `int` member (`Health`), so no value conversion.
    "productcast": "04902c02121304016309050004026e0416040b47",
    # A **cast** is an object, not a reference: `Pawn(Owner).Default.Visibility` is a plain `Context`
    # (`19 2e 83 01`, `ShieldBelt.StopInvisible`), and writing `12` there is a `ClassContext` the compiler did not.
    "castchain": "04192e04016309050004026309040b47",
    # A class literal names its own class for the member join, and a non-bool chain keeps skip 5: `class 'Localizer'
    # . default . sSelect` is `12 20 cc 01 05 00 00 02 d5 03` (`GUIMessageBox.InitComponent`), a string member.
    "litdefaulttext": "041220040500000205040b47",
    # A **`.static`** call is the same class object inside a `ClassContext` (`12`), and its skip word is `_wide` of the
    # call exactly as a `Context` member's is: `class'Probe'.static.Say("x")` -> `12 20 04 09 00 00 1b 35 1f 78 00 16`
    # (ObjectConst of the class, skip 9 over a 6-byte call with one one-byte index, size 0 for a string return).
    # Shipped: `Speech.SetAIMsg`'s `class'AIMessage'.static.Assign(msg, NewMsg)` is `12 20 19 10 00 04 1c 56 07 ...`.
    "classcall": "041220040900001b351f780016" "040b47",
    # A struct-typed return's value size is the `Context` byte: `plane` and `region` are 16 and `box` is 28
    # (`ShadowProjector.InitShadow` writes skip 6 / size 0x10; `BotEnemy.SetNewHideNode` skip 6 / size 0x1c).
    "takebox": "04190163090600" "1c" "1b3616" "040b47",
    "takeregion": "04190163090600" "10" "1b3716" "040b47",
    # A member call's parameter list is its receiver's declaration: `TargetH` holds `helper`, whose `Pong` takes no
    # argument, while the body's own `Probe.Pong(int A)` does -- a fallback to the body's chain refuses this call.
    "callhelper": "19016609060000" "1b3416" "040b47",
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
    # `Global.F();` is the same call behind `GlobalFunction` (0x38): `Vehicle.VehicleCeaseFire`'s
    # `Global.VehicleCeaseFire(bWasAltFire);` is `38 50 06 2d 00 74 81 01 16`, and here `Ping`'s record is 51 --
    # the same index `Ping();` writes behind `0x1b`.
    "callglobal": "383316" "040b47",
    # `Static.F(args)` / `Global.F(args)` in a **value** position: the prefix writes no receiver node at all.
    # `GameInfo.AddMutator`'s `!Static.AllowMutator(MutName)` is `1b 7f 16`, `GameInfo.AllowMutator`'s
    # `static.IsVehicleMutator(MutatorClassName)` is `1b 40 17 6f 4e 16`, and `ScriptedController.GetAggroLevel`'s
    # `return Global.GetAggroLevel();` is `04 38 64 03 16`. `Foo(1)` is `1b 29 26 16`; the `Static.` spelling is
    # byte-for-byte the bare call.
    "staticvalue": "041b292616040b47",
    "staticlowervalue": "041b292616040b47",
    "globalvalue": "0438292616040b47",
    # A **qualified** enum constant writes the ordinal alone, exactly as the bare spelling does: `24 01` in a
    # `byte` site (an `int` site widens it with `39 3a`, the same `ByteToInt` the bare spelling gets).
    "enummember": "042401" "040b47",
    # `Items.Length = 0;` is a `Let` on the `37` length node: `Profiles.Length = 0;` ships
    # `0f 37 01 7d 01 25` and `Recipients.Length = 0;` `0f 37 01 4a 03 25 04 27`. The property is export 700
    # (`7c 0a`), written by the same index the target's read uses.
    "setlength": "0f37017c0a25040b47",
    # `Health ClockwiseFrom Health` is an operator declared with **no index**, so the compiler writes a
    # `FinalFunction` on the record instead of an opcode: `ScriptedController.SetFireYaw` ships
    # `1c c5 04 | 00 47 01 | ... | 16`, and `-261` is `ClockwiseFrom_IntInt` in that package's import table. A
    # rule that guessed an opcode here would emit a wrong body that still walks, which is why this one is pinned.
    "noindexop": "041cc504016e04016e0416" "040b47",
}

# Bodies the emitter must refuse, each for a named reason rather than a guess.
REFUSE = ("compoundarg", "floatarg", "wrongargc", "nativestub", "callbadnative", "callmissing",
          "castnoslot", "opnoslot", "incfloat", "callunknown", "acountunknown", "badclass",
          "badstatic", "eachnested", "callglobalnative", "badenummember", "rotexpression")


def check(args):
    """Synthetic corpus plus the committed artifact's own consistency: no dump, no writes."""
    names = {"weapon_bone": 2063, "probe": 5, "ping": 51, "pong": 52, "say": 53,
             "getbox": 54, "getregion": 55}
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
    # A **one-row symbol** is typed by its symbol: `&&`, `||` and `^^` are declared once each in all 18 packages
    # and `!` is `preoperator bool ! ( bool A )`, so a side the walk cannot type still takes that row. Driven
    # directly because the probe class has no operand that is *emittable* and untyped at the same time -- a bare
    # unknown name is refused by `emit_expr` on its own.
    _operators()
    for sym in ("&&", "||", "^^"):
        if _sole_rows().get(sym) != ("bool", "bool"):
            print("FAIL uscript-emit: the table installs more than one row for %s" % sym)
            return 1
        got = _operator_types([("ident", "Unknown"), ("op", sym), ("keyword", "bFlag")], env)
        if got is None or got[4] is None or got[1:3] != ("bool", "bool"):
            print("FAIL uscript-emit: %s with an untyped side answers %r, expected the (bool, bool) row" % (sym, got))
            return 1
    if _sole_unary().get("!") != "bool":
        print("FAIL uscript-emit: `!` no longer installs one operand type")
        return 1
    got = _unary_types([("op", "!"), ("ident", "Unknown")], env)
    if got is None or got[3] is None or got[1] != "bool":
        print("FAIL uscript-emit: `!` with an untyped operand answers %r, expected the bool row" % (got,))
        return 1
    # A **parenthesised** int group at a mixed `float` pair is retyped root and branch, not cast as a whole:
    # `ConeBuilder.BuildCone`'s `sin((2 * i + Ofs) * pi / Sides)` ships
    # `Multiply_FloatFloat(Add_FloatFloat(Multiply_FloatFloat(FloatConst 2, IntToFloat(i)), IntToFloat(Ofs)),
    # FloatConst pi)`. The whole-group cast (`PrimitiveCast IntToFloat` around `Add_IntInt`) is what the first
    # reading wrote, two bytes long; the group's own operator is asked again with the promoted subtree flag.
    grp = [("op", "("), ("number", "2"), ("op", "*"), ("ident", "Slot"), ("op", "+"), ("ident", "Health"),
           ("op", ")"), ("op", "*"), ("number", "1.5")]
    got, _shape = emit_expr(grp, "float", dict(env, _root=True, root_site="float"))
    if not got.hex().startswith("abaeab1e00000040"):
        print("FAIL uscript-emit: a parenthesised int group at a float pair emits %s, expected "
              "ab ae ab 1e 00000040 (Multiply_FloatFloat(Add_FloatFloat(Multiply_FloatFloat(FloatConst 2, ..."
              % got.hex())
        return 1
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
        sidx, _f, _p, _e, _en, _r, _lt, _pt, _w, _d, _c, _sp, _cr, _ltf, _crf, _tabs, _vd = _source_index(tmp)
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
    # P4d: the same-state record can be an **import** whose owner pair is `(state, class)`. A parent in another
    # package carries `BaseChange <- Dying <- Pawn` as -1044 and the class-scope import as -1116, and
    # `UnrealPawn.Dying.BaseChange` writes the first -- reading only the immediate owner name compares `dying`
    # with a class and can never match, which is what wrote -1116 on 35 shipped bodies.
    imp_env = dict(state_env)
    imp_env["func_owners"] = {}
    imp_env["imports"] = {"ping": [(-51, "Function"), (-1044, "Function")]}
    imp_env["import_pairs"] = {-51: ("", "parent"), -1044: ("follow", "parent")}
    got, _m, _s = emit_stmt(super_toks, "", imp_env)
    if got.hex() != "1c" + write_index(-1044).hex() + "16":
        print("FAIL uscript-emit: Super.Ping() with a state-owned import emits %s, expected %s"
              % (got.hex(), "1c" + write_index(-1044).hex() + "16"))
        return 1
    # P4e: a state that `extends` another names **that** state's function before the class parent's:
    # `GRENADE.ClientPreparingToFire extends PreparingToFire` writes export 2073 for `Super.BeginState()` and
    # 2071 for `Super.Tick()`, and neither is on the class parent at all.
    ext_env = dict(state_env)
    ext_env["state"] = "follow"
    ext_env["state_parents"] = {"follow": "walk"}
    ext_env["func_owners"] = {"ping": [(763, "parent", ""), (765, "probe", "walk")]}
    got, _m, _s = emit_stmt(super_toks, "", ext_env)
    if got.hex() != "1c" + write_index(765).hex() + "16":
        print("FAIL uscript-emit: Super.Ping() in a state that extends another emits %s" % got.hex())
        return 1
    # P4f: `Super(C).F()` names the class on the arrow, and writes the same node `Super.F()` does -- the record of
    # F on C's chain behind `1c`. `Super(Parent).Ping()` is `1c 7b 0b 16` for the same export 763.
    sc_env = dict(super_env, class_names={"parent"})
    sc_toks = [("ident", "Super"), ("op", "("), ("ident", "Parent"), ("op", ")"), ("op", "."),
               ("ident", "Ping"), ("op", "("), ("op", ")")]
    got, _m, _s = emit_stmt(sc_toks, "", sc_env)
    if got.hex() != "1c7b0b16":
        print("FAIL uscript-emit: Super(Parent).Ping() emits %s, expected 1c7b0b16" % got.hex())
        return 1
    # P4g: a `Super(C)` whose class the table does not carry is no receiver at all -- the record cannot be named.
    if _super_cast_class(sc_toks[:4], dict(sc_env, class_names=set())):
        print("FAIL uscript-emit: Super(Unknown) resolved a class the table does not carry")
        return 1
    # P6: a receiver whose declared type is a **struct name** is a struct receiver, even when it is a call or a
    # chain (`_record_kind` answers only for a bare name). `GetBoneCoords(...).Origin` and `Canvas.DrawColor.B`
    # are the shipped shapes; a *class* word never reads as one.
    struct_env = dict(env, order=["probe"], rets={("probe", "getcoords"): "coords"})
    call_toks = [("ident", "GetCoords"), ("op", "("), ("op", ")")]
    if _receiver_kind(call_toks, struct_env) != "struct":
        print("FAIL uscript-emit: a call returning a struct is not a struct receiver")
        return 1
    cls_env = dict(struct_env, rets={("probe", "getcoords"): "pawn"}, class_names={"pawn"})
    if _receiver_kind(call_toks, cls_env) == "struct":
        print("FAIL uscript-emit: a class-typed receiver was read as a struct")
        return 1
    # P7: a chain link that is a **call** holds the callee's declared return type: `GetPawn().Health` is a member
    # of `Pawn`, joined on the receiver's chain, not on the body's.
    link_env = dict(env, order=["probe"], class_names={"pawn", "probe"},
                    localtypes={"owner": "pawn"}, paramtypes={}, proptypes={},
                    rets={("pawn", "getpawn"): "pawn"})
    link_env["scope"] = dict(SCOPE)
    link_toks = [("ident", "Owner"), ("op", "."), ("ident", "GetPawn"), ("op", "("), ("op", ")")]
    if _class_of(link_toks, link_env) != "pawn":
        print("FAIL uscript-emit: a call link in a receiver chain names %r, expected 'pawn'"
              % (_class_of(link_toks, link_env),))
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
    # P5b: a name whose record is declared on the `within` class's chain and not the body's is read through
    # `Outer` **whatever its type**: `CheatManager.Amphibious` writes `19 19 01 8e 05 00 04 01 05 05 00 04 01 77`
    # for `Pawn.UnderwaterTime` (the receiver wrapped at size 4), and `PlayerInput.ChangeSnapView`'s bool
    # `bSnapToLevel` is `19 01 8e 05 00 04 2d 01 6c 28`. A name on the body's own chain keeps its bare read.
    w_env = dict(within_env)
    w_env["class_names"] = {"probe", "helper", "pawn"}
    w_env["within_proptypes"] = {"pawn": "pawn"}
    w_env["scope"] = dict(within_env["scope"], within_props={"pawn"},
                          props=dict(SCOPE["props"], pawn=(0x01, 5, "object")))
    try:
        got = _emit_var("Pawn", w_env, "")
    except Refused as exc:
        print("FAIL uscript-emit: a `within` property read refused: %s" % exc)
        return 1
    if got.hex() != "19018e050004" "0105":
        print("FAIL uscript-emit: a `within` property read emits %s, expected 19018e0500040105" % got.hex())
        return 1
    if _class_of([("ident", "Pawn")], w_env) != "pawn":
        print("FAIL uscript-emit: the `within` text does not name the receiver's class (%r)"
              % (_class_of([("ident", "Pawn")], w_env),))
        return 1
    if _emit_var("Health", w_env, "") is None or _emit_var("Health", w_env, "")[:1] == b"\x19":
        print("FAIL uscript-emit: a property of the body's own chain was wrapped as `Outer`")
        return 1
    # ...and a type with no measured value size is refused rather than given a word: a struct-typed `within`
    # property is the shape no shipped body writes.
    st_env = dict(w_env, scope=dict(w_env["scope"],
                                    props=dict(w_env["scope"]["props"], squaddir=(0x01, 620, "struct")),
                                    within_props={"squaddir"}))
    try:
        _emit_var("SquadDir", st_env, "")
    except Refused:
        pass
    else:
        print("FAIL uscript-emit: a struct-typed `within` property was wrapped with an unmeasured size")
        return 1
    # P6: a bool **member of a struct** is read behind `BoolVariable`: `!SavedFireProperties.bInitialized` writes
    # `81 2d 36 52 43 01 5a 08 16` (`Weapon.AdjustAim`), `SimAnim.bAnimLoop = true;` writes `14 2d 36 ...`
    # (`BlueFlag.PostBeginPlay`), and the bare `36` was the whole of four differences.
    senv = dict(env)
    senv["scope"] = dict(SCOPE, struct_cands={("vector", "bhit"): [(77, "BoolProperty", "")],
                                                ("vector", "x"): [(78, "FloatProperty", "")]})
    got, _m = emit_expr([("ident", "SquadDir"), ("op", "."), ("ident", "bHit")], "bool", senv)
    want = b"\x2d\x36" + write_index(77) + b"\x01" + write_index(620)
    if got != want:
        print("FAIL uscript-emit: a bool struct member emits %s, expected %s" % (got.hex(), want.hex()))
        return 1
    got, _m = emit_expr([("ident", "SquadDir"), ("op", "."), ("ident", "X")], "float", senv)
    if got[:2] == b"\x2d\x36":
        print("FAIL uscript-emit: a float struct member is written behind BoolVariable")
        return 1
    # P6b: a struct **field** whose name the class chain also spells is the struct's record, not the property.
    # `UWindowFramedWindow.Resized` wrote `36 de 01` (the GUI class's `X`, export 478, a float) where UWindow's
    # own `Region.X` is export 31, an int -- and the property's float type is why the `39 3f` `IntToFloat` in
    # front of the field disappeared with it. The fallback is pinned too: a name the struct does not hold is
    # answered from the class chain, which is what `SquadDir.Health` must keep doing.
    sh_env = dict(senv)
    sh_env["scope"] = dict(senv["scope"], struct_cands={("vector", "x"): [(31, "IntProperty", "")]},
                           props=dict(SCOPE["props"], x=(0x01, 478, "float")))
    got, _m = emit_expr([("ident", "SquadDir"), ("op", "."), ("ident", "X")], "int", sh_env)
    want = b"\x36" + write_index(31) + b"\x01" + write_index(620)          # `01` = InstanceVariable: `SquadDir` is a property
    if got != want:
        print("FAIL uscript-emit: a struct field shadowed by a property emits %s, expected %s"
              % (got.hex(), want.hex()))
        return 1
    got, _m = emit_expr([("ident", "SquadDir"), ("op", "."), ("ident", "Health")], "int", sh_env)
    if _struct_member([("ident", "SquadDir")], "health", sh_env) is not None:
        print("FAIL uscript-emit: the shadowing struct answers a field it does not declare")
        return 1
    if _prop_by_name("health", sh_env) is None:
        print("FAIL uscript-emit: the class chain no longer answers a property the struct lacks")
        return 1
    # P6b: a struct name the package declares **twice** is resolved by the class that declares the struct-typed
    # property. `StargateGame.u` nests one `DriverWeaponStruct` in each of `StargateVehicle` (whose `WeaponClass`
    # is 4360) and `StargateWeaponEmplacement` (4692); each class's own copy wins, and a subclass that re-declares
    # the property with its own struct type reads the *child's* record, which the package's index order cannot
    # give it. The control is the same pair with no declaring class: it must fall back to the first record.
    dc_env = dict(env)
    dc_env["scope"] = dict(SCOPE, chain={("p", "stargateweaponemplacement"): ("p", "stargatevehicle"),
                                         ("p", "stargatevehicle"): ("", "")},
                           pack="p", cls="stargateweaponemplacement",
                           struct_cands={("driverweaponstruct", "weaponclass"): [
                               (4360, "ClassProperty", "stargatevehicle"),
                               (4692, "ClassProperty", "stargateweaponemplacement")]},
                           prop_cls={"driverweapons": "stargateweaponemplacement"})
    got = _struct_record("driverweaponstruct", "weaponclass",
                         _declaring_class([("ident", "DriverWeapons")], dc_env), dc_env)
    if got != (4692, "ClassProperty"):
        print("FAIL uscript-emit: the subclass's own struct copy answers %r, expected 4692" % (got,))
        return 1
    # The control: no declaring class means no witness, and the package's order decides -- the `lowest` index
    # `tools/struct-records.py` counts. `SceneManager.ResolveActorsInActions` is this case in the corpus: its
    # struct-typed property is declared on a class that nests no copy, and the package names the *other* class's
    # record, so a body-class witness would have made that body differ.
    fc_env = dict(dc_env, scope=dict(dc_env["scope"], cls="stargateweaponemplacement", prop_cls={}))
    got = _struct_record("driverweaponstruct", "weaponclass",
                         _declaring_class([("ident", "DriverWeapons")], fc_env), fc_env)
    if got != (4360, "ClassProperty"):
        print("FAIL uscript-emit: no witness answers %r, expected the package's first record" % (got,))
        return 1
    # P8b: a `class<Z>( )` cast **inside** a larger expression is a guarded span, not two comparisons. The
    # loosest operator of `1.2 * class<Weapon>(InventoryType).Default.AIRating` is the `*`, and the `>` inside
    # the cast head must not be read as an operator at all.
    mc_ops = _operator_types([("number", "1.2"), ("op", "*"), ("keyword", "class"), ("op", "<"),
                              ("ident", "Actor"), ("op", ">"), ("op", "("), ("ident", "OptionalObject"),
                              ("op", ")"), ("op", "."), ("ident", "Default"), ("op", "."), ("ident", "X")], env)
    if mc_ops is None or mc_ops[0] != "*" or mc_ops[5] != 1:
        print("FAIL uscript-emit: `1.2 * class<Z>(x).Default.Y` reads %r, expected the `*` at 1" % (mc_ops,))
        return 1
    # P8c: the cast as a `.Default` receiver is a **ClassContext** over the group-13 node:
    # `12 13 <Z> <inner> 05 00 <size> 02 <member>`. Shipped: `WeaponPickup.PostBeginPlay` writes
    # `12 05 00 04 13 0f | 02 74 07` for `class<Weapon>(InventoryType).Default.AIRating`.
    mc_env2 = dict(env, scope=dict(SCOPE, props=dict(SCOPE["props"]),
                                   objclass={("probe", "target"): "probe"}))
    got, _m = emit_expr([("keyword", "class"), ("op", "<"), ("ident", "Probe"), ("op", ">"), ("op", "("),
                         ("ident", "Target"), ("op", ")"), ("op", "."), ("ident", "Default"), ("op", "."),
                         ("ident", "Health")], "int", mc_env2)
    want = (b"\x12\x13" + write_index(4) + b"\x01" + write_index(611) + b"\x05\x00\x04"
            + b"\x02" + write_index(302))
    if got != want:
        print("FAIL uscript-emit: `class<Z>(x).Default.Y` emits %s, expected %s" % (got.hex(), want.hex()))
        return 1
    # P8: a cast to a **class** type is the group-13 `MetaClassCast` (`13 <object ref> <expr>`), and it is a class
    # reference at its site. `LocalMessage.GetString`'s `class<Actor>(OptionalObject) != None` is
    # `77 13 02 00 41 44 2a 16` -- `02` is `Actor`'s export in Engine.u, the operand a local -- and
    # `Controller.SetPawnClass`'s `class<Pawn>(DynamicLoadObject(...))` is `13 01 ...`. Its `<` and `>` are the
    # cast's own spelling: read as comparisons they made the whole condition a pair the table does not install.
    mc_env = dict(env, scope=dict(SCOPE, locals={"optionalobject": (0x00, 4353, "object")}))
    got, _m = emit_expr([("ident", "class"), ("op", "<"), ("ident", "Actor"), ("op", ">"), ("op", "("),
                         ("ident", "OptionalObject"), ("op", ")"), ("op", "!="), ("keyword", "None")],
                        "bool", mc_env)
    want = b"\x77\x13" + write_index(-77) + b"\x00" + write_index(4353) + b"\x2a\x16"
    if got != want:
        print("FAIL uscript-emit: class<Z>(x) != None emits %s, expected %s" % (got.hex(), want.hex()))
        return 1
    got, _m = emit_expr([("ident", "class"), ("op", "<"), ("ident", "Actor"), ("op", ">"), ("op", "("),
                         ("ident", "OptionalObject"), ("op", ")")], "", mc_env)
    if got[:1] != b"\x13":
        print("FAIL uscript-emit: a class cast alone emits %s, expected a `13` node" % got.hex())
        return 1
    # P9: a hole in an argument list is **written**, not skipped: the compiler's `Nothing` (`0b`).
    # `Controller.PostBeginPlay`'s `Spawn(PlayerReplicationInfoClass, Self, , vect(0,0,0), rot(0,0,0))` is
    # `... 16 | 0b 23 <vector> 22 <rotator> 16` -- one `Nothing` in the `SpawnTag` slot. 101 residue bodies were
    # refused for the hole alone.
    hole_env = dict(env)
    hole_env["scope"] = dict(SCOPE, funcs=dict(SCOPE["funcs"], holey=("virtual", 60)))
    hole_env["params"] = dict(PARAMS)
    hole_env["params"][("probe", "holey")] = [("int", "A", False), ("int", "B", False),
                                              ("vector", "V", False), ("int", "C", True)]
    got, _m, _s = emit_stmt([("ident", "Holey"), ("op", "("), ("number", "22"), ("op", ","), ("number", "33"),
                             ("op", ","), ("op", ","), ("number", "44"), ("op", ")")], "int", hole_env)
    want = b"\x1b" + write_index(60) + b"\x2c\x16\x2c\x21" + b"\x0b" + b"\x2c\x2c\x16"
    if got != want:
        print("FAIL uscript-emit: a call with an empty argument emits %s, expected %s" % (got.hex(), want.hex()))
        return 1
    # P10: a **struct comparison** is `33`/`32` + the struct's own object reference and **no** `16`:
    # `UWindowHTMLTextArea.DrawTextLine`'s `(CurrentStyle.TextColor != OldColor)` is `33 8c 36 7c 02 ... 01 55 06`,
    # where `8c` is import 12 (`Color`) of UWindow.u. The pair table installs no `(color, color)` row -- its four
    # color rows are `-`, `*`, `+`, `*` -- so the compare is the fallback, and all six such nodes in the corpus
    # name a `Struct` (`Color`, `IpAddr`, `VoiceRef`).
    cmp_env = dict(env, scope=dict(SCOPE, structrefs={"color": -12},
                                   locals={"oldcolor": (0x00, 700, "color"),
                                           "newcolor": (0x00, 701, "color")}))
    got, _m = emit_expr([("ident", "OldColor"), ("op", "!="), ("ident", "NewColor")], "bool", cmp_env)
    want = b"\x33" + write_index(-12) + b"\x00" + write_index(700) + b"\x00" + write_index(701)
    if got != want:
        print("FAIL uscript-emit: a `color != color` compare emits %s, expected %s" % (got.hex(), want.hex()))
        return 1
    got, _m = emit_expr([("ident", "OldColor"), ("op", "=="), ("ident", "NewColor")], "bool", cmp_env)
    if got[:1] != b"\x32":
        print("FAIL uscript-emit: a `color == color` compare emits %s, expected StructCmpEq" % got.hex())
        return 1
    # P11: the dynamic-array intrinsics. `0x41` Remove and `0x40` Insert take the array read and two int slots with
    # no skip word and no `16` (version 130 predates both), `A.Length = n;` is a `Let` on the `37` node, an
    # array-to-array assignment is a plain `Let`, and an array *member* read is a `Context` whose size byte is 0.
    # The refusals are part of the contract: a name this build has no token for (`Find`) and a wrong argument count
    # must refuse, not emit a plausible node.
    arr_stmts = [
        ("`Items.Remove(0, Items.Length)`",
         [("ident", "Items"), ("op", "."), ("ident", "Remove"), ("op", "("), ("number", "0"),
          ("op", ","), ("ident", "Items"), ("op", "."), ("ident", "Length"), ("op", ")")],
         b"\x41\x01" + write_index(700) + b"\x25" + b"\x37\x01" + write_index(700)),
        ("`Items.Insert(Count, 1)`",
         [("ident", "Items"), ("op", "."), ("ident", "Insert"), ("op", "("), ("ident", "Count"),
          ("op", ","), ("number", "1"), ("op", ")")],
         b"\x40\x01" + write_index(700) + b"\x39\x3a\x00" + write_index(612) + b"\x26"),
        ("`Items.Length = 0;`",
         [("ident", "Items"), ("op", "."), ("ident", "Length"), ("op", "="), ("number", "0")],
         b"\x0f\x37\x01" + write_index(700) + b"\x25"),
        ("`Songs = Others;`",
         [("ident", "Songs"), ("op", "="), ("ident", "Others")],
         b"\x0f\x01" + write_index(702) + b"\x01" + write_index(703)),
    ]
    for label, toks, want in arr_stmts:
        got, _m, _s = emit_stmt(toks, "", env)
        if got != want:
            print("FAIL uscript-emit: %s emits %s, expected %s" % (label, got.hex(), want.hex()))
            return 1
    got, _m = emit_expr([("ident", "Target"), ("op", "."), ("ident", "Mats"), ("op", "."),
                         ("ident", "Length")], "int", env)
    want = b"\x37\x19\x01" + write_index(611) + b"\x05\x00\x00\x01" + write_index(701)
    if got != want:
        print("FAIL uscript-emit: `Target.Mats.Length` emits %s, expected %s" % (got.hex(), want.hex()))
        return 1
    for label, toks in (("`Items.Find(0)`",
                         [("ident", "Items"), ("op", "."), ("ident", "Find"), ("op", "("),
                          ("number", "0"), ("op", ")")]),
                        ("`Items.Insert(0)` (one argument)",
                         [("ident", "Items"), ("op", "."), ("ident", "Insert"), ("op", "("),
                          ("number", "0"), ("op", ")")])):
        try:
            emit_stmt(toks, "", env)
        except Refused:
            continue
        print("FAIL uscript-emit: %s did not refuse" % label)
        return 1
    # P7: a `continue` names the loop's **test node** -- the trailing `Jump` in a `while`, the trailing
    # `JumpIfNot` in a `do`/`until` -- not the loop's first byte. Settled by two shipped bodies
    # (`TestInfo.TestContinueWhile` writes `06 59 00` for a back-`Jump` at wide 0x59 and `TestContinueDoUntil`
    # writes `06 60 00` for a `JumpIfNot` at wide 0x60), and checked here structurally: the emitted loop's own
    # test is its last branch node, and the `continue`'s `Jump` must name that node's position.
    decls = {f[1].lower(): (f[2], f[3], f[4]) for f in functions_of(SYNTH)}
    for fn in ("whilecontinue", "docontinue"):
        if fn not in decls:
            print("FAIL uscript-emit: the synthetic class does not declare %s" % fn)
            return 1
        rettype, body, is_native = decls[fn]
        out, _m, _s = emit_body(body, rettype, env, is_native)
        flat = []

        def flatten(ns):
            for n in ns:
                flat.append(n)
                flatten(n["children"])

        flatten(dis.parse(out, dis.Table()))
        branches = [n for n in flat if n["opcode"] in (0x06, 0x07)]
        if not branches:
            print("FAIL uscript-emit: %s emits no branch node" % fn)
            return 1
        test = branches[-1]
        jumps = [n for n in flat if n["opcode"] == 0x06 and n is not test]
        if not jumps:
            print("FAIL uscript-emit: %s emits no `continue` jump" % fn)
            return 1
        want = _wide(out[:test["offset"]])
        got_jump = out[jumps[0]["offset"]:jumps[0]["end"]]
        if got_jump != bytes([0x06]) + struct.pack("<H", want):
            print("FAIL uscript-emit: %s's `continue` emits %s, expected a jump to %d (the loop's test)"
                  % (fn, got_jump.hex(), want))
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
