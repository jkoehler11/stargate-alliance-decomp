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


def functions_of(text):
    """`[(state, name, rettype, body_tokens)]` for every function/event with a body."""
    toks = tokens_of(text)
    if toks is None:
        return []
    out = []
    i, depth, state, state_depth = 0, 0, "", None
    while i < len(toks):
        kind, val = toks[i]
        low = val.lower() if kind in ("keyword", "ident") else None
        if kind == "op" and val == "{":
            depth += 1
            i += 1
            continue
        if kind == "op" and val == "}":
            if state_depth is not None and depth == state_depth:
                state, state_depth = "", None
            depth = max(0, depth - 1)
            i += 1
            continue
        if kind == "keyword" and low == "state" and i + 1 < len(toks):
            j = i + 1
            while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] in ("{", ";")):
                j += 1
            if j < len(toks) and toks[j][1] == "{":
                state, state_depth = toks[i + 1][1], depth + 1
                i = j
            else:
                i = j + 1
            continue
        if kind == "keyword" and low in ("function", "event") and (depth == 0 or depth == state_depth):
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
            is_native = any(w.lower() == "native" for w in words[:-1] + _modifiers_before(toks, i))
            k = close + 1
            while k < len(toks) and not (toks[k][0] == "op" and toks[k][1] in ("{", ";")):
                k += 1
            if k >= len(toks) or toks[k][1] == ";":
                # a bodyless declaration is how the source spells an empty stub: the package holds `04 0b 47`
                out.append((state if depth == state_depth and state else "", name, rettype, None, is_native))
                i = k + 1
                continue
            end = _brace_end(toks, k)
            if end is None:
                break
            out.append((state if depth == state_depth and state else "", name, rettype, toks[k + 1:end], is_native))
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


def emit_expr(expr, rettype, names):
    """The bytes of one constant expression, or Refused with the reason. `names` maps lower name -> table index."""
    expr = _strip_parens(expr)
    if not expr:
        return bytes([0x0B]), "nothing"                  # Return; -> Return Nothing
    if len(expr) == 2 and expr[0] == ("op", "-") and expr[1][0] == "number":
        expr = [("number", "-" + expr[1][1])]
    if len(expr) != 1:
        if expr[0][1].lower() == "vect":
            return _emit_vector(expr), "vector"
        raise Refused("a compound expression, which needs the statement walk")
    kind, val = expr[0]
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


def emit_body(body, rettype, names, is_native=False):
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
    if stmt[0][0] != "keyword" or stmt[0][1].lower() != "return":
        raise Refused("a body whose statement is not a return: not modelled")
    expr, shape = emit_expr(stmt[1:], rettype, names)
    return bytes([0x04]) + expr + TRAILER, "return", shape


# ----------------------------------------------------------------------------------------------------------------
# scoring against the dump

def _dis():
    """`uscript-dis.py` as a module: the locator and the token walk are its own, one answer for both halves."""
    spec = importlib.util.spec_from_file_location("uscript_dis", os.path.join(ROOT, "tools", "uscript-dis.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _source_index(tree):
    """`{(class.lower(), state.lower(), function.lower()): (rettype, body)}` from the extracted corpus."""
    index = {}
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
            for state, fn, rettype, body, is_native in functions_of(text):
                for key in ((cls, state.lower(), fn.lower()), (cls, "", fn.lower())):
                    old = index.get(key)
                    if old is None or len(body or ()) > len(old[1] or ()):
                        # a class that ships two declarations of one name (an `#if` branch, or a stub beside its
                        # body) compiled the longer one: prefer it, so the comparison is not scored against a stub
                        index[key] = (rettype, body, text, is_native)
    return index, files


def score(tree, dump, out_dir):
    dis = _dis()
    from ue2pkg import Package                                  # noqa: PLC0415 - only with a real dump

    index, files = _source_index(tree)
    table = dis.Table()
    rows = []
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
                continue
            rettype, src_body, _text, is_native = got
            try:
                ours, model, shape = emit_body(src_body, rettype, table_index, is_native)
            except Refused as exc:
                tally["not-modelled"] += 1
                reasons[str(exc).split("(")[0].strip()] += 1
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
    joined = tally["walked"] - tally["no-source"] - tally["no-state-source"]
    print("%d source file(s); %d bod(ies) walked, %d joined to a declaration (%d class-scope names and %d state "
          "names the text does not carry), %d not modelled"
          % (files, tally["walked"], joined, tally["no-source"], tally["no-state-source"], tally["not-modelled"]))
    print("every body ends on the trailer %d of %d (and %d are the trailer alone)"
          % (tally["trailer"], tally["walked"], tally["bare-trailer"]))
    print("modelled %d: %d identical, %d differ" % (tally["modelled"], tally["identical"], tally["differ"]))
    print("shapes: %s" % ", ".join("%s %d" % kv for kv in shapes.most_common()))
    print("not modelled: %s" % ", ".join("%s %d" % kv for kv in reasons.most_common()))
    print("wrote %s" % os.path.relpath(path, ROOT))
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
"""

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
}


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
    got = {fn.lower(): (rettype, body, nat) for _state, fn, rettype, body, nat in functions_of(SYNTH)}
    for fn, want in WANT.items():
        if fn not in got:
            print("FAIL uscript-emit: the synthetic class does not declare %s" % fn)
            return 1
        rettype, body, is_native = got[fn]
        try:
            ours, _model, _shape = emit_body(body, rettype, names, is_native)
        except Refused as exc:
            print("FAIL uscript-emit: %s refused: %s" % (fn, exc))
            return 1
        if ours.hex() != want:
            print("FAIL uscript-emit: %s emits %s, expected %s" % (fn, ours.hex(), want))
            return 1
    try:
        rettype, body, is_native = got["call"]
        emit_body(body, rettype, names, is_native)
    except Refused:
        pass
    else:
        print("FAIL uscript-emit: a call expression was emitted instead of refused")
        return 1
    try:
        rettype, body, is_native = got["nativestub"]
        emit_body(body, rettype, names, is_native)
    except Refused:
        pass
    else:
        print("FAIL uscript-emit: a native declaration was emitted instead of refused")
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
    print("ok uscript-emit: %d synthetic bodies, artifact %d rows (%d identical, %d differ)"
          % (len(WANT), len(body_rows), identical, differ))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tree", help="the extracted source corpus ($WORK/scripts-all)")
    ap.add_argument("--dump", help="the dump's System directory")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not args.tree or not args.dump:
        print("FAIL uscript-emit: --tree and --dump are required (the corpus and the dump stay outside the repo)")
        return 2
    return score(args.tree, args.dump, args.out)


if __name__ == "__main__":
    sys.exit(main())
