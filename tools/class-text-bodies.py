#!/usr/bin/env python3
"""What a disabled function *body* weighs, and whether the live twin of the same name has the same shape.

`tools/class-text-comments.py` counts the `function` declarations a block comment disables and joins them to
the package's exports by name; it leaves the bodies. The body is the cheaper question than the tree: an
operator is a token, so a commented body and its live twin can be compared token by token without a parser.

    tools/class-text-bodies.py DUMP/System --sweep -o inventory
    tools/class-text-bodies.py --report
    tools/class-text-bodies.py --write-doc docs/PACKAGE_SCRIPT_TEXT.md
    tools/class-text-bodies.py --check-doc docs/PACKAGE_SCRIPT_TEXT.md   # from the committed artifact
    tools/class-text-bodies.py --check                                    # synthetic, no dump, writes nothing

A comment disables a **declaration**, and most of the declarations it disables also keep their body inside the
same `/* ... */` -- so the population is readable per function: the body's brace span, its operator multiset
(the `tools/decompiler-operators.py` token table, reused so the two tools cannot drift), its statement count
and its byte length. Three populations come out of that read:

* a commented body whose name the live text of the same class also declares with a body -- both revisions of
  one function, compared token by token;
* a commented body with no live twin -- the functions this build did not compile, weighted by body size;
* the live declarations themselves, as the extractor's control: a body reader that finds fewer bodies in the
  live text than the text declares is not measuring the comment.

Nothing here prints or commits body text: the artifact carries sizes, counts and names. The operators are
counted with `decompiler-operators.py`'s own `clean()`/`counts()` so a change to that table moves both pages
at once. Reads the packages' `ScriptText`; no bytecode, no dump-side value, no boot.

**A cast is not a call.** `PlayerController(Owner)` and `vect(0,0,0)` are an identifier in front of a
parenthesis, so the call pattern counts them; a name the corpus exports as a `Class`/`Struct`/`Enum` and never
as a `Function` -- or one of the built-in constructors below -- is a type conversion, and the resolution of a
call name must skip it. The names that survive that filter *and* resolve to nothing are typed in the artifact's
`residue` rows: a declaration inside a block comment, a member call on a receiver whose declared type declares
no such function, or nothing at all.

**A name missing from the export table is not a name missing from the build.** The export table holds what the
loader must construct; three other vocabularies say whether the build ever saw a name at all:

* the `state` blocks the class texts declare,
* the labels at a state body's own statement level -- the targets `Goto` and a `GotoState(x, 'label')` name,
* each package's own **name table**, which the compiler fills with every name any of its objects referenced.

So a `residue` row carries `vocab`/`vocab_where`: `function` (never, by construction -- a name with an export
never reaches the residue), `state`, `state-label`, `name-only` (in a name table with no declaration) or
`absent`. A name in no name table of the eighteen packages was never compiled into this build; a name that is
only a state's label is a jump target, not something a call site can reach.
"""
import argparse
import csv
import importlib.util
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/class_text_bodies.csv")
DOC = os.path.join(ROOT, "docs/PACKAGE_SCRIPT_TEXT.md")
DOC_START = "<!-- generated:class-text-bodies -->"
DOC_END = "<!-- /generated:class-text-bodies -->"

FIELDS = ["kind", "package", "class", "name", "verdict", "c_ops", "c_stmts", "c_kw", "c_bytes",
          "l_ops", "l_stmts", "l_kw", "l_bytes", "c_calls", "c_calls_own", "c_calls_elsewhere",
          "c_calls_missing", "l_calls", "l_calls_own", "l_calls_elsewhere", "l_calls_missing",
          "keys", "count", "note", "vocab", "vocab_where"]

# The same declaration pattern `class-text-comments.py` counts, so the two censuses describe one population.
FUNC = re.compile(r"^[ \t]*(?:simulated[ \t]+|static[ \t]+|final[ \t]+|latent[ \t]+)*function[ \t]+"
                  r"(?:[\w<>\[\]]+[ \t]+)?(\w+)[ \t]*\(", re.M)
BLOCK = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"//[^\n]*")
KEYWORD = re.compile(r"\b(?:if|else|for|while|switch|case|return|do)\b")
LITERAL = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")
# A live body this short is a trailer, not a revision: the class kept the declaration and emptied the body.
STUB_BYTES = 32
# `vect(x,y,z)` is the one conversion whose name is not a type export of the corpus: UE2's vector literal.
BUILTIN_CONSTRUCTORS = {"vect"}
TYPE_KINDS = ("Class", "Struct", "Enum")
# The names that are types by declaration in the *language* rather than by an export, so `string.Format(..)` has a
# receiver type like any member call.
BUILTIN_TYPES = {"string", "class", "array", "bool", "byte", "int", "float", "name", "vector", "rotator"}
# A declaration anywhere inside the block comment, indentation and modifiers allowed, as `class-text-comments.py`
# counts them; used only to type a call name that resolves to nothing.
ANY_DECL = re.compile(r"(?m)^[ \t]*(?:(?:simulated|static|final|latent)[ \t]+)*"
                      r"(function|event|delegate)[ \t]+(?:[\w<>\[\], \t]+[ \t]+)?(\w+)[ \t]*\(")
IFDEF = re.compile(r"(?m)^[ \t]*#(ifdef|ifndef|if)[^\n]*\n((?:(?!#(?:endif|else)).*\n)*?)[ \t]*#endif")
LOCAL_DECL = re.compile(r"(?m)^[ \t]*local[ \t]+([\w<>\[\]]+(?:[ \t]*<[^>]*>)?)[ \t]+([^;]+);")


def load_tool(name, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- one body --------------------------------------------------------------------------------------
def body_span(text, start, limit):
    """(start, end) of the brace-matched body that follows `start`, bounded by `limit`, or None.

    A declaration that ends in `;` before its first `{` has no body, and neither has one whose `{` lies past
    `limit` -- the next declaration. That bound is not cosmetic: without it a bodyless declaration inherits the
    next function's body and two unrelated functions read as the same shape.
    """
    i = text.find("{", start)
    if i < 0 or i >= limit:
        return None
    semi = text.find(";", start)
    if 0 <= semi < i:
        return None
    depth = 0
    j = i
    while j < limit:
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (i, j + 1)
        j += 1
    return None


STATE_DECL = re.compile(r"(?m)^[ \t]*(?:auto[ \t]+)?state[ \t]*(?:\([ \t]*\d+[ \t]*\))?[ \t]*(\w+)")
LABEL_LINE = re.compile(r"(?m)^[ \t]*(\w+)[ \t]*:")
LITERAL_ANY = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def mask(text):
    """Blank block comments, line comments, then string literals, keeping every offset.

    The order is the census's, not a free choice: masking literals first runs an apostrophe inside a prose
    comment ("don't") to the next apostrophe hundreds of lines later and blanks the code between, which reads as
    a corpus-wide zero rather than as an error.
    """
    out = list(text)
    for rx in (BLOCK, LINE_COMMENT):
        for m in rx.finditer(text):
            for i in range(m.start(), m.end()):
                if out[i] != "\n":
                    out[i] = " "
    masked = "".join(out)
    for m in LITERAL_ANY.finditer(masked):
        for i in range(m.start(), m.end()):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


def depth_map(text):
    """The brace depth *inside* which each character sits: `{` opens, `}` closes before itself."""
    out, depth = [], 0
    for c in text:
        if c == "{":
            out.append(depth)
            depth += 1
        elif c == "}":
            depth -= 1
            out.append(depth)
        else:
            out.append(depth)
    return out


def state_vocabulary(text):
    """(state names, `label -> state`) for one class text, read from the comment-masked text.

    A `state ... { Begin: ... }` a block comment disables is not vocabulary this build carries. A label inside a
    function the state overrides sits one brace deeper and is not counted -- the depth rule
    `tools/ai-states.py` counts a state's labels by.
    """
    d = depth_map(text)
    spans = []
    for m in STATE_DECL.finditer(text):
        i = text.find("{", m.end())
        if i < 0:
            continue
        j = i + 1
        while j < len(text) and d[j] > d[i]:
            j += 1
        spans.append((m.group(1).lower(), i, j, d[i] + 1))
    labels = {}
    for name, a, b, level in spans:
        for m in LABEL_LINE.finditer(text, a, b):
            if d[m.start()] == level:
                labels.setdefault(m.group(1).lower(), name)
    return [s[0] for s in spans], labels


def vocab_of(name, index, states, labels, tables):
    """Where a name lives in the build: an export, a state, a state's label, only a name table, or nowhere."""
    if index.get(name):
        return "function", ", ".join(sorted("%s.%s" % k for k in index[name])[:3])
    if states.get(name):
        return "state", ", ".join(sorted(states[name])[:3])
    if labels.get(name):
        return "state-label", ", ".join(sorted(labels[name])[:3])
    if tables.get(name):
        return "name-only", ",".join(sorted(tables[name])[:6])
    return "absent", ""


def functions(text):
    """(name -> body, names without a body, declarations, bare, with a body) for one text.

    The first declaration of a name wins, so a text that ships the same function twice reads once -- which is
    why the resolved count is returned separately from the body map: the control compares declarations, and a
    deduplicated map would report 82% over a text whose every declaration the reader in fact resolves.
    """
    bodies, bare, declared, bare_total, resolved = {}, [], 0, 0, 0
    ms = list(FUNC.finditer(text))
    for k, m in enumerate(ms):
        declared += 1
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(text)
        span = body_span(text, m.end(), limit)
        if span is None:
            bare_total += 1
            if m.group(1).lower() not in bare:
                bare.append(m.group(1).lower())
            continue
        resolved += 1
        bodies.setdefault(m.group(1).lower(), text[span[0]:span[1]])
    return bodies, bare, declared, bare_total, resolved


def statements(body):
    """`;` at the body's own brace depth, and the control keywords -- a statement shape, not a parse."""
    depth, semis = 0, 0
    for c in body:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ";" and depth == 1:
            semis += 1
    return semis, len(KEYWORD.findall(body))


# A call is an identifier in front of a parenthesis. The keywords that do it are not calls, and `super` is the
# archive's own constructor form rather than a name the export table carries.
CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
NOT_A_CALL = {"if", "else", "for", "foreach", "while", "switch", "case", "return", "do", "goto", "break",
              "continue", "class", "function", "state", "event", "defaultproperties", "simulated", "static",
              "final", "latent", "native", "local", "super", "new", "exec", "assert", "const", "var", "out",
              "optional", "coerce", "config", "globalconfig", "byte", "bool", "int", "float", "string", "name",
              "array", "struct", "enum", "delegate"}


def calls(body, types=None, index=None):
    """The distinct call names a body makes, string literals removed, type conversions excluded.

    With no `types` the read is the raw one -- every identifier in front of a parenthesis that is not a keyword --
    which is what the earlier revision counted and what a pair's delta is still read against. Given the corpus's
    type table, a name that some package exports as a `Class`/`Struct`/`Enum` and none as a `Function` is a cast,
    and `BUILTIN_CONSTRUCTORS` names are conversions by the language; neither is a call.
    """
    text = LITERAL.sub('""', body)
    out = set()
    for m in CALL.finditer(text):
        n = m.group(1).lower()
        if n in NOT_A_CALL:
            continue
        if types and n in BUILTIN_CONSTRUCTORS:
            continue
        if types and n in types and not (index and n in index):
            continue
        out.add(n)
    return out


def type_index(pkg, package):
    """name -> {kind: {package}} over one package's `Class`, `Struct` and `Enum` exports."""
    out = {}
    for e in pkg.exports:
        kind = pkg.class_name_of(e)
        if kind in TYPE_KINDS:
            out.setdefault(pkg.name(e["name"]).lower(), {}).setdefault(kind, set()).add(package)
    return out


def param_types(text, fname_):
    """`parameter -> type` from the declaration of `fname_` in the same text, so a passed receiver is typed too.

    `S.MergeWith(...)` in a body whose function takes `SquadAIBasic S` is a member call like any other, and a
    reader that only looks at `local` declarations calls it unresolved for the wrong reason.
    """
    m = re.search(r"(?i)function[ \t]+[\w<>\[\], \t]*?\b%s[ \t]*\(([^)]*)\)" % re.escape(fname_), text)
    out = {}
    if m:
        for part in m.group(1).split(","):
            words = part.split()
            if len(words) >= 2:
                out[words[-1].strip().lower()] = words[-2]
    return out


def receivers(body, name, params=None, types=None):
    """The declared types of the receivers a body calls `name` on, read from its own `local` declarations.

    `B.DoRetreat()` is a member call, and the receiver's type is the body's own `local BotEnemy B` -- a name that
    resolves to nothing may be a function that class declares elsewhere, or one it never had. `string.Format(..)`
    is the same shape with a type name as the receiver, so the built-in type names count too.
    """
    locals_ = dict(params or {})
    for m in LOCAL_DECL.finditer(body):
        for v in m.group(2).split(","):
            v = v.strip()
            if v:
                locals_[v.lower()] = m.group(1).strip()
    out = set()
    for m in re.finditer(r"\b(\w+)\s*\.\s*%s\s*\(" % re.escape(name), body, re.I):
        recv = m.group(1).lower()
        if recv in ("super", "self"):
            out.add(recv)
        elif recv in locals_:
            out.add(locals_[recv])
        elif recv in BUILTIN_TYPES or (types and recv in types):
            out.add(recv)
    return out


def resolve(names, package, cls, index):
    """(own, elsewhere, missing) for a set of call names against the corpus's `Function` exports.

    `own` is a function this class declares (a state's counted to the class), `elsewhere` one some other class
    of the corpus declares, `missing` one no class in the eighteen packages declares.
    """
    own = elsewhere = missing = 0
    for n in names:
        keys = index.get(n, ())
        if not keys:
            missing += 1
        elif (package, cls.lower()) in keys:
            own += 1
        else:
            elsewhere += 1
    return own, elsewhere, missing


def shape(body, ops):
    """One body's row columns: the operator multiset, the statement counts and the byte length."""
    c = ops.counts(body)
    stmts, kw = statements(body)
    return dict(ops=c["operators"], per_op=c["per_op"], stmts=stmts, kw=kw, bytes=len(body))


def compare(commented, live):
    """How two revisions of one function differ, as a verdict.

    `identical` is the whole body byte for byte -- a class that ships the same text twice. `same-operators`
    keeps the operator multiset and differs in layout or operands, `differs` moves at least one operator, and
    `live-stub` is the case the byte lengths separate: the live body is a trailer under `STUB_BYTES`.
    """
    if commented["bytes"] == live["bytes"] and commented["text"] == live["text"]:
        return "identical"
    if live["bytes"] <= STUB_BYTES and commented["bytes"] > STUB_BYTES:
        return "live-stub"
    if commented["per_op"] == live["per_op"]:
        return "same-operators"
    return "differs"


# --- the sweep -------------------------------------------------------------------------------------
def residue_kind(name, recv, com_decl, ifdef_decl):
    """What an unresolved call name is, from evidence the text carries -- not from a table of names.

    * `commented-declaration` -- some class declares a `function`/`event`/`delegate` of that name inside a block
      comment, so the caller and the callee are both revisions the comment disabled;
    * `ifdef` -- the declaration sits in a `#ifdef` region no export came from (the Xbox build compiles it out);
    * `member-of` -- the name is called as `<receiver>.<name>(` and the receiver's declared type is known, so it
      is a function of that class which no package of the corpus declares;
    * `unresolved` -- none of the above: a bare call, or a cast to a type the export table does not carry.
    """
    if name in com_decl:
        evidence = "declared inside a block comment at " + ", ".join(
            sorted("%s.%s" % (p, c) for _k, p, c in com_decl[name]))[:150]
        if name in ifdef_decl:
            evidence += ", under " + ",".join(sorted(d for d, _p, _c in ifdef_decl[name]))
        return "commented-declaration", evidence
    if name in ifdef_decl:
        return "ifdef", "declared under %s at %s" % (
            ",".join(sorted(d for d, _p, _c in ifdef_decl[name])),
            ", ".join(sorted("%s.%s" % (p, c) for _d, p, c in ifdef_decl[name]))[:140])
    if recv:
        return "member-of", "called on a `%s` receiver and no class declares it" % ",".join(sorted(recv))
    return "unresolved", "no declaration anywhere in the corpus's text"


def residue_row(name, sites, kinds, recv, com_decl, ifdef_decl):
    kind, evidence = residue_kind(name, recv, com_decl, ifdef_decl)
    row = {k: "" for k in FIELDS}
    row.update(kind="residue", name=name, verdict=kind, count=str(len(sites)),
               package=",".join(sorted({s[0] for s in sites}))[:120],
               **{"class": ",".join(sorted({s[1] for s in sites}))[:120]},
               keys=",".join(sorted({"%s.%s.%s" % (s[0], s[1], s[2]) for s in sites}))[:200],
               note=evidence, l_bytes="")
    return row


def pair_row(package, cls, name, cshape, lshape, verdict):
    moved = sorted(k for k in cshape["per_op"] if cshape["per_op"][k] != lshape["per_op"][k])
    return dict(kind="pair", package=package, **{"class": cls}, name=name, verdict=verdict,
                c_ops=cshape["ops"], c_stmts=cshape["stmts"], c_kw=cshape["kw"], c_bytes=cshape["bytes"],
                l_ops=lshape["ops"], l_stmts=lshape["stmts"], l_kw=lshape["kw"], l_bytes=lshape["bytes"],
                c_calls=cshape["calls"], c_calls_own=cshape["own"], c_calls_elsewhere=cshape["elsewhere"],
                c_calls_missing=cshape["missing"],
                l_calls=lshape["calls"], l_calls_own=lshape["own"], l_calls_elsewhere=lshape["elsewhere"],
                l_calls_missing=lshape["missing"],
                keys=",".join(moved), count="1", note="")


def disabled_row(package, cls, name, cshape):
    return dict(kind="disabled", package=package, **{"class": cls}, name=name, verdict="no-live-twin",
                c_ops=cshape["ops"], c_stmts=cshape["stmts"], c_kw=cshape["kw"], c_bytes=cshape["bytes"],
                l_ops="", l_stmts="", l_kw="", l_bytes="",
                c_calls=cshape["calls"], c_calls_own=cshape["own"], c_calls_elsewhere=cshape["elsewhere"],
                c_calls_missing=cshape["missing"],
                l_calls="", l_calls_own="", l_calls_elsewhere="", l_calls_missing="",
                keys="", count="1", note="")


def corpus_index(pkg):
    """name -> {(package, class)} over one package's `Function` exports, a state's counted to its class."""
    out = {}
    for e in pkg.exports:
        if pkg.class_name_of(e) != "Function" or e["outer"] <= 0:
            continue
        oe = pkg.exports[e["outer"] - 1]
        if pkg.class_name_of(oe) == "State" and oe["outer"] > 0:
            oe = pkg.exports[oe["outer"] - 1]
        out.setdefault(pkg.name(e["name"]).lower(), set()).add(pkg.name(oe["name"]).lower())
    return out


def sweep(system_dir):
    pst = load_tool("pkg_script_text", "pkg-script-text.py")
    ops = load_tool("decompiler_operators", "decompiler-operators.py")
    # Pass one: every package's exports, so a call can be resolved against the whole corpus and not only its
    # own package -- an engine native a game class calls is declared in Core or Engine. The type table is the
    # other half: a name some package exports as a class, struct or enum and none as a function is a cast. The
    # name table is the third: it holds every name the compiler saw, declaration or reference.
    packages, index, types, tables = [], {}, {}, {}
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        package = fname[:-2]
        packages.append((package, pkg))
        for i in range(len(pkg.names)):
            try:
                tables.setdefault(pkg.name(i).lower(), set()).add(package)
            except (IndexError, ValueError, KeyError):
                pass
        for name, classes in corpus_index(pkg).items():
            index.setdefault(name, set()).update((package, c) for c in classes)
        for name, kinds in type_index(pkg, package).items():
            for kind, pkgs in kinds.items():
                types.setdefault(name, {}).setdefault(kind, set()).update(pkgs)
    rows = []
    t = dict(classes=0, with_block=0, declarations=0, with_body=0, no_body=0,
             live_declarations=0, live_with_body=0,
             pairs=0, identical=0, same_operators=0, differs=0, live_stub=0,
             pair_c_bytes=0, pair_l_bytes=0, pair_c_ops=0, pair_l_ops=0,
             disabled=0, disabled_bytes=0, disabled_ops=0, disabled_stmts=0, disabled_stub=0,
             disabled_calls=0, disabled_calls_own=0, disabled_calls_elsewhere=0, disabled_calls_missing=0,
             disabled_calls_all=0, disabled_casts=0,
             control_calls=0, control_calls_own=0, control_calls_elsewhere=0, control_calls_missing=0,
             control_calls_all=0, control_casts=0,
             pair_c_calls=0, pair_c_calls_missing=0, pair_l_calls=0, pair_l_calls_missing=0)
    com_decl, ifdef_decl, sites, states, labels = {}, {}, {}, {}, {}
    for package, pkg in packages:
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            text = pst.script_text(pkg, exp)
            if not text:
                continue
            text = text.replace("\r\n", "\n")
            spans = [m.span() for m in BLOCK.finditer(text)]
            t["classes"] += 1
            cls = pkg.name(exp["name"])
            st_names, st_labels = state_vocabulary(mask(text))
            for s in set(st_names):
                states.setdefault(s, set()).add("%s.%s" % (package, cls))
            for lab, st in st_labels.items():
                labels.setdefault(lab, set()).add("%s.%s.%s" % (package, cls, st))
            if not spans:
                continue
            t["with_block"] += 1
            comment = "\n".join(text[a:b] for a, b in spans)
            live = LINE_COMMENT.sub("", BLOCK.sub("", text))
            cbodies, _cbare, cdeclared, cbare_total, cresolved = functions(comment)
            lbodies, _lbare, ldeclared, _lbare_total, lresolved = functions(live)
            t["declarations"] += cdeclared
            t["with_body"] += cresolved
            t["no_body"] += cbare_total
            t["live_declarations"] += ldeclared
            t["live_with_body"] += lresolved
            for m in ANY_DECL.finditer(comment):
                com_decl.setdefault(m.group(2).lower(), set()).add(("comment", package, cls.lower()))
            for m in IFDEF.finditer(text):
                for d in ANY_DECL.finditer(m.group(2)):
                    ifdef_decl.setdefault(d.group(2).lower(), set()).add(("#" + m.group(1), package, cls.lower()))
            # the control: every live body of a class that carries a comment, resolved the same way
            for lbody in lbodies.values():
                names = calls(lbody, types, index)
                t["control_calls_all"] += len(calls(lbody))
                t["control_casts"] += len(calls(lbody)) - len(names)
                own, elsewhere, missing = resolve(names, package, cls, index)
                t["control_calls"] += len(names)
                t["control_calls_own"] += own
                t["control_calls_elsewhere"] += elsewhere
                t["control_calls_missing"] += missing
            for name in sorted(cbodies):
                body = cbodies[name]
                cshape = shape(body, ops)
                cshape["text"] = body
                names = calls(body, types, index)
                cshape["calls"] = len(names)
                (cshape["own"], cshape["elsewhere"], cshape["missing"]) = resolve(names, package, cls, index)
                if name in lbodies:
                    lbody = lbodies[name]
                    lshape = shape(lbody, ops)
                    lshape["text"] = lbody
                    lnames = calls(lbody, types, index)
                    lshape["calls"] = len(lnames)
                    (lshape["own"], lshape["elsewhere"], lshape["missing"]) = resolve(
                        lnames, package, cls, index)
                    verdict = compare(cshape, lshape)
                    t["pairs"] += 1
                    t[verdict.replace("-", "_")] += 1
                    t["pair_c_bytes"] += cshape["bytes"]
                    t["pair_l_bytes"] += lshape["bytes"]
                    t["pair_c_ops"] += cshape["ops"]
                    t["pair_l_ops"] += lshape["ops"]
                    t["pair_c_calls"] += cshape["calls"]
                    t["pair_c_calls_missing"] += cshape["missing"]
                    t["pair_l_calls"] += lshape["calls"]
                    t["pair_l_calls_missing"] += lshape["missing"]
                    rows.append(pair_row(package, cls, name, cshape, lshape, verdict))
                else:
                    t["disabled"] += 1
                    t["disabled_bytes"] += cshape["bytes"]
                    t["disabled_ops"] += cshape["ops"]
                    t["disabled_stmts"] += cshape["stmts"]
                    t["disabled_stub"] += cshape["bytes"] <= STUB_BYTES
                    t["disabled_calls"] += cshape["calls"]
                    t["disabled_calls_all"] += len(calls(body))
                    t["disabled_casts"] += len(calls(body)) - cshape["calls"]
                    t["disabled_calls_own"] += cshape["own"]
                    t["disabled_calls_elsewhere"] += cshape["elsewhere"]
                    t["disabled_calls_missing"] += cshape["missing"]
                    for n in sorted(names):
                        if not index.get(n):
                            sites.setdefault(n, []).append(
                                (package, cls, name, receivers(body, n, param_types(comment, name), types)))
                    rows.append(disabled_row(package, cls, name, cshape))
    residue, receivers_of = [], {}
    for n in sorted(sites):
        recv = set().union(*[s[3] for s in sites[n]]) if sites[n] else set()
        receivers_of[n] = recv
        row = residue_row(n, sites[n], None, recv, com_decl, ifdef_decl)
        row["vocab"], row["vocab_where"] = vocab_of(n, index, states, labels, tables)
        residue.append(row)
    kinds = {}
    for r in residue:
        kinds[r["verdict"]] = kinds.get(r["verdict"], 0) + 1
    kinds_vocab = {}
    for r in residue:
        kinds_vocab[r["vocab"]] = kinds_vocab.get(r["vocab"], 0) + 1
    # The bot override API: the names a `BotEnemy` receiver is handed, and how many of them the build carries.
    bot = sorted(n for n in receivers_of if "BotEnemy" in receivers_of[n])
    bot_present = [r["name"] for r in residue if r["name"] in bot and r["vocab"] != "absent"]
    rows.extend(residue)
    census = (
        ("classes_with_text", t["classes"], "class exports carrying a `ScriptText`"),
        ("classes_with_block", t["with_block"], "of those, carrying a `/* ... */` block"),
        ("comment_declarations", t["declarations"],
         "`function` declarations inside a block comment"),
        ("comment_with_body", t["with_body"], "of those, with a brace-matched body in the same comment"),
        ("comment_no_body", t["no_body"], "of those, a declaration without a body"),
        ("live_declarations", t["live_declarations"],
         "`function` declarations in the same classes' live text -- the control's denominator"),
        ("live_with_body", t["live_with_body"],
         "of those, whose body the same reader finds -- the extractor's rate"),
        ("pairs", t["pairs"], "commented bodies whose name the live text also declares with a body"),
        ("pairs_identical", t["identical"], "of those, byte-identical to the live body"),
        ("pairs_same_operators", t["same_operators"],
         "of those, a different layout with the same operator multiset"),
        ("pairs_differs", t["differs"], "of those, moving at least one operator"),
        ("pairs_live_stub", t["live_stub"],
         "of those, whose live body is a trailer of %d bytes or fewer" % STUB_BYTES),
        ("pairs_comment_bytes", t["pair_c_bytes"], "bytes of the commented revisions of those pairs"),
        ("pairs_live_bytes", t["pair_l_bytes"], "bytes of their live revisions"),
        ("pairs_comment_operators", t["pair_c_ops"], "operators in the commented revisions"),
        ("pairs_live_operators", t["pair_l_ops"], "operators in the live revisions"),
        ("pairs_comment_calls", t["pair_c_calls"],
         "distinct call names in the commented revisions, counted per body"),
        ("pairs_comment_calls_missing", t["pair_c_calls_missing"],
         "of those, naming no `Function` export of the eighteen packages"),
        ("pairs_live_calls", t["pair_l_calls"], "the same read over their live revisions"),
        ("pairs_live_calls_missing", t["pair_l_calls_missing"],
         "of those, naming no `Function` export of the eighteen packages"),
        ("disabled_bodies", t["disabled"], "commented bodies whose name no live body in the text carries"),
        ("disabled_bytes", t["disabled_bytes"], "bytes of those bodies"),
        ("disabled_operators", t["disabled_ops"], "operators in those bodies"),
        ("disabled_statements", t["disabled_stmts"], "statements at their own brace depth"),
        ("disabled_stubs", t["disabled_stub"],
         "of those, a trailer of %d bytes or fewer" % STUB_BYTES),
        ("disabled_calls", t["disabled_calls"],
         "distinct call names in those bodies, counted per body, type conversions excluded"),
        ("disabled_calls_all", t["disabled_calls_all"],
         "the same read with the type conversions counted -- the previous revision's number"),
        ("disabled_casts", t["disabled_casts"],
         "of those, an identifier in front of a parenthesis that names a class, struct, enum or built-in "
         "constructor -- a cast, not a call"),
        ("disabled_calls_own", t["disabled_calls_own"],
         "of those, declared by the class the body sits in"),
        ("disabled_calls_elsewhere", t["disabled_calls_elsewhere"],
         "of those, declared by another class of the corpus -- an engine or sibling native"),
        ("disabled_calls_missing", t["disabled_calls_missing"],
         "of those, naming no `Function` export of the eighteen packages"),
        ("residue_names", len(residue), "distinct unresolved call names, typed one by one"),
        ("residue_sites", sum(len(v) for v in sites.values()), "their call sites across the disabled bodies"),
        ("residue_commented_declaration", kinds.get("commented-declaration", 0),
         "of those, declared as a function/event/delegate inside a block comment"),
        ("residue_ifdef", kinds.get("ifdef", 0),
         "of those, declared under an `#ifdef` region no export came from"),
        ("residue_member_of", kinds.get("member-of", 0),
         "of those, called on a receiver whose declared type declares no such function"),
        ("residue_unresolved", kinds.get("unresolved", 0), "of those, no declaration anywhere in the text"),
        ("residue_state", kinds_vocab.get("state", 0),
         "of those, the name of a `state` block a class text declares"),
        ("residue_state_label", kinds_vocab.get("state-label", 0),
         "of those, a label at a state body's own statement level"),
        ("residue_name_only", kinds_vocab.get("name-only", 0),
         "of those, a name in a package name table with no declaration and no state"),
        ("residue_absent", kinds_vocab.get("absent", 0),
         "of those, in no name table of the eighteen packages"),
        ("residue_botenemy", len(bot), "of those, called on a `BotEnemy` receiver"),
        ("residue_botenemy_present", len(bot_present),
         "of those, which the build carries in some vocabulary"),
        ("state_names", len(states), "distinct `state` names the class texts declare"),
        ("state_names_exported", sum(1 for n in states if index.get(n)),
         "of those, a name the export table also carries as a `Function`"),
        ("label_names", len(labels), "distinct labels at a state body's own statement level"),
        ("label_names_exported", sum(1 for n in labels if index.get(n)),
         "of those, also a `Function` export somewhere in the corpus"),
        ("control_calls", t["control_calls"],
         "the same read over every live body of the classes that carry a comment -- the control"),
        ("control_calls_all", t["control_calls_all"],
         "the same control with the type conversions counted -- the previous revision's number"),
        ("control_casts", t["control_casts"], "of those, casts rather than calls"),
        ("control_calls_own", t["control_calls_own"], "of those, declared by the body's own class"),
        ("control_calls_elsewhere", t["control_calls_elsewhere"], "of those, declared elsewhere in the corpus"),
        ("control_calls_missing", t["control_calls_missing"],
         "of those, naming no `Function` export -- locals, delegates and the residue"),
    )
    for key, value, note in census:
        row = {k: "" for k in FIELDS}
        row.update(kind="census", name=key, count=str(value), note=note)
        rows.append(row)
    return rows


# --- the render ------------------------------------------------------------------------------------
def census_of(rows):
    return {r["name"]: r for r in rows if r["kind"] == "census"}


def render(rows):
    cen = census_of(rows)

    def n(key):
        return int(cen[key]["count"])

    def label_where(r):
        """`name` plus its state and class, from a residue row's `vocab_where` (`Pkg.Class.state`)."""
        parts = (r["vocab_where"] or "").split(".")
        if len(parts) >= 3:
            return "`%s`, the entry label of `%s` in `%s`" % (r["name"], parts[-1], ".".join(parts[:-1]))
        return "`%s`" % r["name"]

    def pct(a, b):
        return "%.1f" % (100.0 * a / b) if b else "-"
    pairs = [r for r in rows if r["kind"] == "pair"]
    residue = [r for r in rows if r["kind"] == "residue"]
    by_body = {}
    for r in residue:
        if r["verdict"] == "member-of" and r["keys"]:
            b = r["keys"].split(",")[0]
            by_body[b] = by_body.get(b, 0) + 1
    top_body, top_n = max(by_body.items(), key=lambda kv: (kv[1], kv[0])) if by_body else ("-", 0)
    out = []
    out.append("**%d of the %d `function` declarations a block comment disables also carry their body inside "
               "that comment, and %d of them have a live twin of the same name in the same class's text.** The "
               "reader is not the weak part: over the same classes' live text it finds a body for **%d of %d "
               "declarations (%s%%)**, against **%s%%** inside the comments -- so the shortfall is the comment's own "
               "residue, not the pattern's. What the %d pairs say is that a commented revision is **not** the function the "
               "build carries: **%d** are byte-identical, **%d** keep the operator multiset with a different "
               "layout, **%d** move at least one operator and **%d** leave a live body of %d bytes or fewer "
               "behind a substantial comment -- %d commented bytes against %d live ones over the population."
               % (n("comment_with_body"), n("comment_declarations"), n("pairs"),
                  n("live_with_body"), n("live_declarations"),
                  pct(n("live_with_body"), n("live_declarations")),
                  pct(n("comment_with_body"), n("comment_declarations")), n("pairs"),
                  n("pairs_identical"), n("pairs_same_operators"), n("pairs_differs"),
                  n("pairs_live_stub"), STUB_BYTES, n("pairs_comment_bytes"), n("pairs_live_bytes")))
    out.append("")
    out.append("| measure | value |")
    out.append("|---|---|")
    out.append("| classes with a `ScriptText` / carrying a block comment | **%d** / **%d** |"
               % (n("classes_with_text"), n("classes_with_block")))
    out.append("| `function` declarations inside a block comment | **%d** |" % n("comment_declarations"))
    out.append("| ... with a body in the same comment | **%d** |" % n("comment_with_body"))
    out.append("| ... a declaration without a body | **%d** |" % n("comment_no_body"))
    out.append("| live `function` declarations / whose body the reader finds | **%d** / **%d** |"
               % (n("live_declarations"), n("live_with_body")))
    out.append("| commented bodies with a live twin of the same name | **%d** |" % n("pairs"))
    out.append("| ... byte-identical | **%d** |" % n("pairs_identical"))
    out.append("| ... same operator multiset, different layout | **%d** |" % n("pairs_same_operators"))
    out.append("| ... at least one operator moved | **%d** |" % n("pairs_differs"))
    out.append("| ... whose live body is a trailer | **%d** |" % n("pairs_live_stub"))
    out.append("| bytes, commented revisions / live revisions of the pairs | **%d** / **%d** |"
               % (n("pairs_comment_bytes"), n("pairs_live_bytes")))
    out.append("| operators, the same pair | **%d** / **%d** |"
               % (n("pairs_comment_operators"), n("pairs_live_operators")))
    out.append("| distinct call names, the pairs' commented / live revisions | **%d** / **%d** |"
               % (n("pairs_comment_calls"), n("pairs_live_calls")))
    out.append("| ... naming no `Function` export of the corpus | **%d** / **%d** |"
               % (n("pairs_comment_calls_missing"), n("pairs_live_calls_missing")))
    out.append("| commented bodies with no live twin | **%d** |" % n("disabled_bodies"))
    out.append("| bytes / operators / statements of those bodies | **%d** / **%d** / **%d** |"
               % (n("disabled_bytes"), n("disabled_operators"), n("disabled_statements")))
    out.append("| distinct call names in those bodies | **%d** |" % n("disabled_calls"))
    out.append("| ... declared by the body's own class / elsewhere in the corpus / by nothing | "
               "**%d** / **%d** / **%d** |" % (n("disabled_calls_own"), n("disabled_calls_elsewhere"),
                                               n("disabled_calls_missing")))
    out.append("| ... of which a cast rather than a call (in the earlier revision's count) | **%d** of **%d** |"
               % (n("disabled_calls_all") - n("disabled_calls"), n("disabled_calls_all")))
    out.append("| the same read over every live body of the commented classes (control) | "
               "**%d** / **%d** / **%d** of **%d** |"
               % (n("control_calls_own"), n("control_calls_elsewhere"), n("control_calls_missing"),
                  n("control_calls")))
    out.append("| ... of which a cast rather than a call (in the earlier revision's count) | **%d** of **%d** |"
               % (n("control_casts"), n("control_calls_all")))
    out.append("| unresolved call names, typed by evidence / their call sites | **%d** / **%d** |"
               % (n("residue_names"), n("residue_sites")))
    out.append("| ... declared inside a block comment / a member call on a type / nowhere / `#ifdef` | "
               "**%d** / **%d** / **%d** / **%d** |"
               % (n("residue_commented_declaration"), n("residue_member_of"), n("residue_unresolved"),
                  n("residue_ifdef")))
    out.append("| ... a `state` name / a state label / only a name in a table / in no name table | "
               "**%d** / **%d** / **%d** / **%d** |"
               % (n("residue_state"), n("residue_state_label"), n("residue_name_only"),
                  n("residue_absent")))
    out.append("| ... called on a `BotEnemy` receiver, of which the build carries | **%d** / **%d** |"
               % (n("residue_botenemy"), n("residue_botenemy_present")))
    out.append("| distinct `state` names / also a `Function` export | **%d** / **%d** |"
               % (n("state_names"), n("state_names_exported")))
    out.append("| distinct state-label names / also a `Function` export | **%d** / **%d** |"
               % (n("label_names"), n("label_names_exported")))
    out.append("")
    if pairs:
        pairs.sort(key=lambda r: (r["verdict"], r["package"], r["class"], r["name"]))
        out.append("Every pair, `ops` being the operator multiset and `stmts` the statements at the body's own "
                   "brace depth:")
        out.append("")
        out.append("| class.function | verdict | commented ops / stmts / bytes | live ops / stmts / bytes | "
                   "operators that moved |")
        out.append("|---|---|---:|---:|---|")
        for r in pairs:
            out.append("| `%s.%s.%s` | %s | %s / %s / %s | %s / %s / %s | %s |"
                       % (r["package"], r["class"], r["name"], r["verdict"],
                          r["c_ops"], r["c_stmts"], r["c_bytes"],
                          r["l_ops"], r["l_stmts"], r["l_bytes"], r["keys"] or "-"))
        out.append("")
    out.append("**A cast is not a call, and with the conversions out the disabled bodies' residue is not a "
               "base rate.** The reader counts every identifier in front of a parenthesis, so a type conversion "
               "came in as a call: **%d of the %d** distinct names the disabled bodies make name a "
               "`Class`/`Struct`/`Enum` export or a built-in constructor and are no function, and the same filter "
               "takes **%d of %d** out of the control. What is left is **%d** names: **%d** declared by the "
               "body's own class, **%d** by another class of the corpus and **%d** by nothing at all "
               "(**%s%%**), against **%s%%** over every live body of the classes that carry a comment "
               "(%d of %d names resolve). The page's previous revision read **%s%%** against **%s%%** and called "
               "the difference the live base rate; its control residue was almost entirely conversions -- "
               "**%d of that read's %d** unresolved control names were casts -- so the pair it quoted was the "
               "reader's, not the corpus's."
               % (n("disabled_casts"), n("disabled_calls_all"), n("control_casts"), n("control_calls_all"),
                  n("disabled_calls"), n("disabled_calls_own"), n("disabled_calls_elsewhere"),
                  n("disabled_calls_missing"), pct(n("disabled_calls_missing"), n("disabled_calls")),
                  pct(n("control_calls_missing"), n("control_calls")),
                  n("control_calls") - n("control_calls_missing"), n("control_calls"),
                  pct(n("disabled_calls_all") - n("disabled_calls") + n("disabled_calls_missing"),
                      n("disabled_calls_all")),
                  pct(n("control_calls_all") - n("control_calls") + n("control_calls_missing"),
                      n("control_calls_all")),
                  n("control_casts"), n("control_casts") + n("control_calls_missing")))
    out.append("")
    if residue:
        out.append("**The %d names behind those %d sites are typed by evidence the text carries.** %d are "
                   "declared as a `function`/`event`/`delegate` inside a block comment themselves -- the caller "
                   "and the callee are both revisions the comment disabled -- %d are member calls whose "
                   "receiver's declared type declares no such function, %d are declared nowhere in the "
                   "eighteen packages%s. The member calls are concentrated: **%d of the %d** are called from one "
                   "body, `%s`."
                   % (n("residue_names"), n("residue_sites"), n("residue_commented_declaration"),
                      n("residue_member_of"), n("residue_unresolved"),
                      (" and %d under an `#ifdef` region no export came from" % n("residue_ifdef"))
                      if n("residue_ifdef") else "",
                      top_n, n("residue_member_of"), top_body))
        out.append("")
        labelled = [r for r in residue if r["vocab"] == "state-label"]
        lab_names = ", ".join(label_where(r) for r in labelled)
        out.append("**Those names are missing from the build, not only from its export table.** Each of the %d "
                   "is read against three further vocabularies -- the `state` blocks the class texts declare, "
                   "the labels at a state body's own statement level, and every package's own name table, "
                   "which the compiler fills with every name it saw: **%d** is a state, **%d** is a state "
                   "label%s, **%d** only a name in a package's table and **%d** in none of the three. The base "
                   "rates are what make the last column a finding rather than a reader artefact: **%d of the "
                   "%d** state names and **%d of the %d** state labels the corpus declares are also `Function` "
                   "exports, so a name this build ships is normally visible in the table, and an API that had "
                   "merely been renamed would leave its names there."
                   % (n("residue_names"), n("residue_state"), n("residue_state_label"),
                      (" (%s)" % lab_names) if labelled else "",
                      n("residue_name_only"), n("residue_absent"),
                      n("state_names_exported"), n("state_names"), n("label_names_exported"),
                      n("label_names")))
        out.append("")
        if n("residue_botenemy"):
            out.append("That is the whole answer for the AI override API: `StargateGame.TeamAI.setorders` calls "
                       "**%d** names on a `BotEnemy` receiver and the build carries **%d** of them -- %s -- "
                       "while the remaining **%d** sit in no name table of the eighteen packages. A call site "
                       "cannot reach a label, and nothing in the build references the other names at all, so "
                       "the commented revision is a dropped API rather than one this build presents under "
                       "another name."
                       % (n("residue_botenemy"), n("residue_botenemy_present"), lab_names or "-",
                          n("residue_botenemy") - n("residue_botenemy_present")))
            out.append("")
        out.append("| unresolved call name | sites | kind | called from | in the build | where | evidence |")
        out.append("|---|---:|---|---|---|---|---|")
        for r in sorted(residue, key=lambda r: (-int(r["count"]), r["name"])):
            out.append("| `%s` | %s | %s | `%s` | %s | %s | %s |"
                       % (r["name"], r["count"], r["verdict"],
                          r["keys"].split(",")[0] if r["keys"] else "-",
                          r["vocab"], (r["vocab_where"] or "-").replace("|", "/"),
                          r["note"].replace("|", "/")))
        out.append("")
    out.append("")
    out.append("**The disabled bodies are the weight of the comment, not a residue of it.** %d commented bodies "
               "have no live twin at all: %d bytes of source, %d operators and %d statements this build never "
               "compiled, %d of them a trailer of %d bytes or fewer (a declaration a class kept while emptying "
               "its body, the shape the pairs show from the other side). Those are the classes and functions "
               "`tools/class-text-comments.py` counts by declaration; this is the same population by size, so a "
               "cut-content read can rank it instead of counting it."
               % (n("disabled_bodies"), n("disabled_bytes"), n("disabled_operators"),
                  n("disabled_statements"), n("disabled_stubs"), STUB_BYTES))
    out.append("")
    disabled = [r for r in rows if r["kind"] == "disabled"]
    if disabled:
        disabled.sort(key=lambda r: (-int(r["c_bytes"]), r["package"], r["class"], r["name"]))
        out.append("The largest of the %d disabled bodies (`ops` / `stmts` at the body's own brace depth, the "
                   "artifact carrying all of them):" % len(disabled))
        out.append("")
        out.append("| class.function | bytes | ops | stmts |")
        out.append("|---|---:|---:|---:|")
        for r in disabled[:8]:
            out.append("| `%s.%s.%s` | %s | %s | %s |"
                       % (r["package"], r["class"], r["name"], r["c_bytes"], r["c_ops"], r["c_stmts"]))
        out.append("")
    out.append("Census: %d classes read, %d with a block comment, %d commented bodies."
               % (n("classes_with_text"), n("classes_with_block"),
                  n("comment_with_body")))
    return "\n".join(out)


# --- artifact and doc ------------------------------------------------------------------------------
def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice(path, block):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


# --- self-test -------------------------------------------------------------------------------------
def self_check():
    bad = []
    ops = load_tool("decompiler_operators", "decompiler-operators.py")
    body = "{\n\tif (A && B)\n\t\treturn A - B;\n\treturn 0;\n}"
    decl = "function int Foo( int A, int B )\n"
    text = "class A extends B;\n" + decl + body + "\n"
    bodies, _bare, declared, _bare_total, resolved = functions(text)
    if declared != 1 or resolved != 1 or "foo" not in bodies or len(bodies["foo"]) != len(body):
        bad.append("one live declaration read as %d declared / %r" % (declared, sorted(bodies)))
    s = shape(bodies["foo"], ops)
    if (s["ops"], s["stmts"], s["kw"]) != (2, 2, 3):
        bad.append("a body's shape read %d ops / %d stmts / %d keywords" % (s["ops"], s["stmts"], s["kw"]))
    # a text that declares one function twice resolves two declarations and holds one body: the control counts
    # declarations, so a deduplicated map must not be what feeds it
    twice_bodies, _tb, twice_declared, _tbt, twice_resolved = functions(text + "\n" + decl + body + "\n")
    if (twice_declared, twice_resolved, len(twice_bodies)) != (2, 2, 1):
        bad.append("a duplicated declaration read %d declared / %d resolved / %d bodies"
                   % (twice_declared, twice_resolved, len(twice_bodies)))
    # a declaration without a body must not inherit the next one's: the shape a commented revision leaves behind
    two = ("/*\nfunction Bar()\nfunction Baz()\n" + body + "\n*/\n")
    cbodies, cbare, cdeclared, _cbt, _cr = functions(two)
    if cdeclared != 2 or set(cbodies) != {"baz"} or cbare != ["bar"]:
        bad.append("a bodyless declaration took its neighbour's body: %r / %r" % (sorted(cbodies), sorted(cbare)))
    # `;` before the brace means no body, whatever follows
    native = "function int Quux( int A );\nfunction int Zed()\n" + body + "\n"
    nbodies, nbare, ndeclared, _nbt, _nr = functions(native)
    if "quux" not in nbare or "zed" not in nbodies or len(nbodies) != 1:
        bad.append("a `;`-terminated declaration took a body: %r" % (sorted(nbodies),))
    # nested braces and a brace inside a string literal
    nested = "function W()\n{\n\tif (X)\n\t{\n\t\tY = \"}{\";\n\t}\n}\n"
    wb, _wbare, _wd, _wbt, _wr = functions(nested)
    want = '{\n\tif (X)\n\t{\n\t\tY = "}{";\n\t}\n}'
    if wb.get("w") != want:
        bad.append("a nested body was cut short: %r" % (wb.get("w"),))
    # the three verdicts, each from a case the other two cannot match
    c = shape("{\n\tA = B + C;\n}", ops)
    c["text"] = "{\n\tA = B + C;\n}"
    same = dict(c)
    if compare(c, same) != "identical":
        bad.append("an unchanged body did not read identical")
    moved = shape("{\n\tA = B - C;\n}", ops)
    moved["text"] = "{\n\tA = B - C;\n}"
    if compare(c, moved) != "differs":
        bad.append("a moved operator did not read differs")
    layout = shape("{\n\n\tA = B + C;\n\n}", ops)
    layout["text"] = "{\n\n\tA = B + C;\n\n}"
    if compare(c, layout) != "same-operators":
        bad.append("a relaid body did not read same-operators")
    long_text = "{\n\tA = B + C + D + E + F + G + H + I + J;\n}"
    long_body = shape(long_text, ops)
    long_body["text"] = long_text
    stub = shape("{\n}", ops)
    stub["text"] = "{\n}"
    if compare(long_body, stub) != "live-stub":
        bad.append("an emptied live body did not read live-stub")
    # a call is an identifier in front of a parenthesis, and the keywords are not calls
    got = calls('{\n\tif (A) SuperMask(B);\n\tX = "Foo(1)";\n\tLocal = Bar();\n}')
    if got != {"supermask", "bar"}:
        bad.append("the call reader returned %r" % (sorted(got),))
    # a cast is not a call: a type export with no function of that name, and the built-in constructor
    cast_types = {"pawn": {"Class": {"P"}}, "einputkey": {"Enum": {"P"}}}
    got = calls('{\n\tA = Pawn(B);\n\tC = Foo(B);\n\tV = vect(1,0,0);\n\tE = EInputKey(I);\n}', cast_types,
                {"foo": {("P", "c")}})
    if got != {"foo"}:
        bad.append("the cast-aware call reader returned %r" % (sorted(got),))
    if calls('{\n\tA = Pawn(B);\n\tV = vect(1,0,0);\n}') != {"pawn", "vect"}:
        bad.append("the raw call reader must still count conversions -- the pair is read against it")
    # a name that is both a type and a function is a call, not a cast
    both = calls('{\n\tSpawn(Pawn(B));\n}', {"pawn": {"Class": {"P"}}}, {"pawn": {("P", "c")}})
    if both != {"pawn", "spawn"}:
        bad.append("a name the corpus exports as a function was taken for a cast: %r" % (sorted(both),))
    # the residue ladder: each kind from its own evidence
    recv = receivers('{\n\tlocal BotEnemy B;\n\tB.DoRetreat();\n}', "doretreat")
    if recv != {"BotEnemy"}:
        bad.append("a local receiver was not typed: %r" % (recv,))
    if receivers('{\n\tS.MergeWith(X);\n}', "mergewith", {"s": "SquadAIBasic"}) != {"SquadAIBasic"}:
        bad.append("a parameter receiver was not typed from the declaration")
    for want, args in (("commented-declaration", ("gone", set(), {"gone": {("comment", "P", "c")}}, {})),
                       ("ifdef", ("gone", set(), {}, {"gone": {("#ifdef", "P", "c")}})),
                       ("member-of", ("gone", {"BotEnemy"}, {}, {})),
                       ("unresolved", ("gone", set(), {}, {}))):
        kind, _why = residue_kind(args[0], args[1], args[2], args[3])
        if kind != want:
            bad.append("the residue ladder read %r, not %r" % (kind, want))
    index = {"foo": {("P", "c")}, "bar": {("Q", "d")}}
    if resolve({"foo", "bar", "baz"}, "P", "C", index) != (1, 1, 1):
        bad.append("the resolution split %r -- the class key is matched case-insensitively"
                   % (resolve({"foo", "bar", "baz"}, "P", "C", index),))
    # the state vocabulary: a label at a state body's own level, not one inside a function it overrides
    state_text = mask("state Alert\n{\nBegin:\n\tGoto('Again');\nAgain:\n\tSleep(1);\n"
                      "\tfunction Foo()\n\t{\n\t\tInner:\n\t}\n}\n")
    st_names, st_labels = state_vocabulary(state_text)
    if st_names != ["alert"] or set(st_labels) != {"begin", "again"}:
        bad.append("the state vocabulary read %r / %r" % (st_names, sorted(st_labels)))
    if st_labels.get("begin") != "alert":
        bad.append("a state label was not attributed to its state: %r" % (st_labels,))
    # a state a block comment disables is not vocabulary this build carries
    masked_state, masked_labels = state_vocabulary(mask("/*\nstate Gone\n{\nHere:\n}\n*/\n"))
    if masked_state or masked_labels:
        bad.append("a commented state counted as vocabulary: %r / %r" % (masked_state, sorted(masked_labels)))
    # a brace or a label inside a string literal must not move the depth
    lit_states, lit_labels = state_vocabulary(mask('state Alert\n{\n\tLog("}{  Here: ");\nBegin:\n}\n'))
    if lit_states != ["alert"] or set(lit_labels) != {"begin"}:
        bad.append("a literal desynchronised the state read: %r / %r" % (lit_states, sorted(lit_labels)))
    # the vocabulary ladder, each rung from its own dictionary
    for want, args in (("function", ({"n": {("P", "c")}}, {}, {}, {})),
                       ("state", ({}, {"n": {"P.C"}}, {}, {})),
                       ("state-label", ({}, {}, {"n": {"P.C.alert"}}, {})),
                       ("name-only", ({}, {}, {}, {"n": {"P"}})),
                       ("absent", ({}, {}, {}, {}))):
        kind, _where = vocab_of("n", *args)
        if kind != want:
            bad.append("the vocabulary ladder read %r, not %r" % (kind, want))
    # the census adds up over a synthetic population, and the render reads the artifact's own rows
    for sh in (c, moved, dict(c)):
        sh.update(calls=2, own=1, elsewhere=1, missing=0)
    rows = [pair_row("P", "C", "foo", c, moved, compare(c, moved)),
            pair_row("P", "C", "bar", c, dict(c), "identical"),
            disabled_row("P", "C", "baz", c),
            residue_row("qux", [("P", "C", "baz", {"BotEnemy"})], None, {"BotEnemy"}, {}, {}),
            residue_row("quux", [("P", "C", "baz", set())], None, set(), {}, {})]
    rows[3]["vocab"], rows[3]["vocab_where"] = "state-label", "P.C.alert"
    rows[4]["vocab"] = "absent"
    cen = (("classes_with_text", 2, ""), ("classes_with_block", 2, ""), ("comment_declarations", 4, ""),
           ("comment_with_body", 3, ""), ("comment_no_body", 1, ""), ("live_declarations", 5, ""),
           ("live_with_body", 5, ""), ("pairs", 2, ""), ("pairs_identical", 1, ""),
           ("pairs_same_operators", 0, ""), ("pairs_differs", 1, ""), ("pairs_live_stub", 0, ""),
           ("pairs_comment_bytes", 2 * c["bytes"], ""), ("pairs_live_bytes", c["bytes"] + moved["bytes"], ""),
           ("pairs_comment_operators", 2 * c["ops"], ""), ("pairs_live_operators", c["ops"] + moved["ops"], ""),
           ("disabled_bodies", 1, ""), ("disabled_bytes", c["bytes"], ""), ("disabled_operators", c["ops"], ""),
           ("disabled_statements", c["stmts"], ""), ("disabled_stubs", 0, ""),
           ("pairs_comment_calls", 4, ""), ("pairs_comment_calls_missing", 0, ""),
           ("pairs_live_calls", 4, ""), ("pairs_live_calls_missing", 0, ""),
           ("disabled_calls", 2, ""), ("disabled_calls_own", 1, ""),
           ("disabled_calls_elsewhere", 1, ""), ("disabled_calls_missing", 0, ""),
           ("control_calls", 4, ""), ("control_calls_own", 2, ""),
           ("control_calls_elsewhere", 2, ""), ("control_calls_missing", 0, ""),
           ("disabled_calls_all", 2, ""), ("disabled_casts", 0, ""),
           ("control_calls_all", 4, ""), ("control_casts", 0, ""),
           ("residue_names", 2, ""), ("residue_sites", 2, ""),
           ("residue_commented_declaration", 1, ""), ("residue_ifdef", 0, ""),
           ("residue_member_of", 1, ""), ("residue_unresolved", 0, ""),
           ("residue_state", 0, ""), ("residue_state_label", 1, ""), ("residue_name_only", 0, ""),
           ("residue_absent", 1, ""), ("residue_botenemy", 1, ""), ("residue_botenemy_present", 1, ""),
           ("state_names", 3, ""), ("state_names_exported", 1, ""),
           ("label_names", 4, ""), ("label_names_exported", 0, ""))
    for key, value, note in cen:
        row = {k: "" for k in FIELDS}
        row.update(kind="census", name=key, count=str(value), note=note)
        rows.append(row)
    block = render(rows)
    for want in ("3 of the 4", "2 of them have a live twin", "`P.C.foo`", "`P.C.bar`",
                 "A cast is not a call", "typed by evidence", "| `qux` |", "| `quux` |",
                 "missing from the build, not only from its export table", "dropped API",
                 "| in the build | where |"):
        if want not in block:
            bad.append("render is missing %r" % want)
    pair_lines = [l for l in block.splitlines() if l.startswith("| `P.C.")]
    baz_lines = [l for l in pair_lines if "`P.C.baz`" in l]
    if len(pair_lines) != 3 or not baz_lines or len(baz_lines[0].split("|")) != 6:
        bad.append("the pair table and the disabled table are not separated: %r" % (pair_lines,))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.csv")
        write_artifact(rows, path)
        again = read_artifact(path)
        if [r["name"] for r in again] != [r["name"] for r in rows]:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
        page = os.path.join(tmp, "page.md")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write("head\n" + DOC_START + "\nold\n" + DOC_END + "\ntail\n")
        if splice(page, "new") is None:
            bad.append("splice could not find its own markers")
    if bad:
        print("FAIL class-text-bodies: " + "; ".join(bad))
        return 1
    print("ok   class-text-bodies: a commented body is delimited inside its own declaration, `;` and a "
          "neighbouring declaration do not lend it a body, the four verdicts separate, and the render reads "
          "the artifact's census rows")
    return 0


# --- main ------------------------------------------------------------------------------------------
def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="the dump's System directory")
    ap.add_argument("--sweep", action="store_true", help="every package -> the artifact")
    ap.add_argument("--report", action="store_true", help="print the block from the committed artifact")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    if args.write_doc or args.check_doc or args.report:
        rows = read_artifact(ARTIFACT)
    else:
        if not args.system or not os.path.isdir(args.system):
            print("no System directory given")
            return 2
        rows = sweep(args.system)
        if args.out:
            path = os.path.join(args.out, "class_text_bodies.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL class-text-bodies: markers not found in %s" % path)
            return 1
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        if args.check_doc:
            if new != old:
                print("FAIL class-text-bodies: %s is stale against the committed artifact" % path)
                return 1
            print("ok   class-text-bodies: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
