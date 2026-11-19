#!/usr/bin/env python3
"""Resolve a class's default-property block by what the class declares, not just by where the block ends.

The previous attempt (`docs/CLASS_DEFAULTS.md`) located the block by the boundary rule alone and hit an
ambiguity: two candidate starts two bytes apart both end at the export end, so `Description` decoded as
`Str BeginState`. The class table already knows which properties a class declares
(`inventory/class_properties.csv`), so the ambiguity can be settled semantically: a candidate whose property
names are all declared by the class (or inherited from its supers) is the block, and one that invents a name
like `BeginState` is not.

    python3 tools/class-defaults.py DUMP/System --class SGOrder
    python3 tools/class-defaults.py DUMP/System --sweep -o inventory
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_prop_stream():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def super_map(path=os.path.join(ROOT, "inventory", "classes.csv")):
    """class -> superclass, taken from the whole tree rather than one package.

    Following supers within the package being read stops at the first class that extends a class from
    another package (StargateGame classes extend Engine ones), which truncated the inherited property set and
    made correct blocks look undeclared.
    """
    out = {}
    if os.path.isfile(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                out[r["class"]] = r["super"]
    return out


def declared_properties(path=os.path.join(ROOT, "inventory", "class_properties.csv")):
    """class -> set of property names it declares, plus the same set for all its ancestors."""
    by_class = collections.defaultdict(set)
    if not os.path.isfile(path):
        return by_class
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            by_class[r["class"]].add(r["property"])
    return by_class


# The locator keeps blocks of at least two properties, and that floor is measured rather than cautious. A floor of
# one promotes **320** of the 2,319 classes to "fully declared", every one of them on a single tag -- and 1,477 of
# the 1,664 blocks that are already located carry the same shape (a declared one-tag parse that ends on the
# payload's last byte), so the test passes on nine payloads in ten no matter what the class contains. It is also
# satisfied by a table tail rather than a block (`ActionString` is the tag for 53 of the 320, and `VoicePack`
# would be "fully declared" on `LifeSpan` out of a 108-byte payload), and it discards better evidence for 21
# classes whose two-property candidate names two declared names where the single tag names one. `single-declared`
# below names the residue instead of promoting it.
MIN_PROPERTIES = 2

# Which tag types each declared property *kind* can legitimately carry. A block whose tag type contradicts the
# class's own property record is reading something other than a tag stream -- the one test the name score cannot
# make, because a start inside a preceding native blob still lands on names the class declares.
# `UDelegateProperty` and `UPointerProperty` are deliberately unconstrained: a delegate's default is written by the
# engine rather than by the tag grammar, and a pointer carries no value in the block.
TYPE_AGREEMENT = {
    "UByteProperty": {"Byte"},
    "UIntProperty": {"Int"},
    "UFloatProperty": {"Float"},
    "UBoolProperty": {"Bool"},
    "UNameProperty": {"Name"},
    "UStrProperty": {"Str", "String"},
    "UObjectProperty": {"Object"},
    # A `Class<X>` property serialises as an object reference in this build: 734 tags in blocks that are
    # otherwise clean carry type `Object` for a `UClassProperty`, so the tag type cannot be `Class` alone.
    "UClassProperty": {"Class", "Object"},
    "UStructProperty": {"Struct", "Vector", "Rotator"},
    "UArrayProperty": {"Array"},
}


def declared_kinds(path=os.path.join(ROOT, "inventory", "class_properties.csv")):
    """class -> {property: kind}, the `kind` column of the property inventory."""
    out = collections.defaultdict(dict)
    if not os.path.isfile(path):
        return out
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out[r["class"]][r["property"]] = r["kind"]
    return out


_AUX = {}


def kinds_for(cls, kinds, supers):
    """{property: kind} for a class, closed over its ancestors (cross-package, from `classes.csv`)."""
    out, cur, seen = {}, cls, set()
    while cur and cur not in seen:
        seen.add(cur)
        for prop, kind in kinds.get(cur, {}).items():
            out.setdefault(prop, kind)
        cur = supers.get(cur)
    return out


def kinds_for_class(cls):
    """`kinds_for` with the inventory loaded once per process, for callers that only have a class name."""
    if not _AUX:
        _AUX["kinds"] = declared_kinds()
        _AUX["supers"] = super_map()
    return kinds_for(cls, _AUX["kinds"], _AUX["supers"])


def contradictions(props, kinds):
    """How many tags in this parse carry a type their declared kind cannot have (0 when `kinds` is empty)."""
    if not kinds:
        return 0
    bad = 0
    for p in props:
        allowed = TYPE_AGREEMENT.get(kinds.get(p["name"]))
        if allowed is not None and p["type"] not in allowed:
            bad += 1
    return bad


def candidates(pkg, mod, exp, limit=None, kinds=None, floor=MIN_PROPERTIES):
    """Every offset where a property block parses, bounded by the export's own size.

    The bound used to be a fixed 512 bytes, and that is what made the classes with the most defaults look
    like classes with none: a class export carries its record (script tables, super, dependencies) *before*
    the defaults block, so `Actor` (record 2,451 bytes), `Pawn` (3,992), `PlayerController` (13,665) and
    `GameInfo` (5,682) all began their block past the window and were reported as "no block". Scanning the
    whole payload recovers 114 of the 532 withheld classes, and those are precisely the base classes.
    """
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    out = []
    if limit is None:
        limit = len(data)
    for start in range(0, min(len(data), limit)):
        try:
            props, end, reason = mod.read_block(data[start:], pkg, False)
        except Exception:
            continue
        if reason != "None" or len(props) < floor:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        if start + end > len(data):
            continue
        trailer = len(data) - (start + end)
        if trailer > 64:
            # A class export can carry data after its defaults (a component table, for one). Requiring an
            # exact ending threw away every such class; a bounded trailer keeps them.
            continue
        # `bad` is the count of tags whose type the class's own property record contradicts; `choose` sorts on it
        # first, so a misparsed start cannot win just because it happens to hold declared names.
        out.append((len(props), start, [p["name"] for p in props], trailer, contradictions(props, kinds)))
    return out, data


def diagnose(pkg, mod, exp, declared=frozenset()):
    """Why a class has no usable block: the question `docs/CLASS_DEFAULTS.md` left as "not measured".

    `candidates()` returns only survivors, so a miss told us nothing. This walks the same offsets and classifies the
    closest attempt: no block parses at all, blocks parse but all end past the trailer bound, only single-property
    blocks parse, or the parsed block carries an unnamed property.
    """
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    parsed = single = unnamed = empty = 0
    max_declared = 0                             # most declared properties any single parse contains
    best = None                                  # (declared, props, trailer) among blocks rejected only by the bound
    for start in range(len(data)):
        try:
            props, end, reason = mod.read_block(data[start:], pkg, False)
        except Exception:
            continue
        if reason != "None":
            continue
        parsed += 1
        if not props:
            empty += 1
            continue
        max_declared = max(max_declared, sum(1 for p in props if p["name"] in declared))
        if len(props) < 2:
            single += 1
            continue
        if any(p["name"] == "?" for p in props):
            unnamed += 1
            continue
        if start + end > len(data):
            continue
        trailer = len(data) - (start + end)
        known = sum(1 for p in props if p["name"] in declared)
        if trailer > 64 and (best is None or (known, len(props)) > (best[0], best[1])):
            best = (known, len(props), trailer)
    if best is not None and best[0] >= 2:
        # A block that carries at least two of the class's properties and only fails the trailer bound: that is the
        # bound's problem, and raising it is the fix.
        return "trailer-over-64", parsed, best[1], best[2]
    if empty and max_declared < 2:
        # The payload parses a *valid empty* block and nothing anywhere carries even two of the class's properties:
        # either the class genuinely sets none of them, or its block does not have the shape read_block parses.
        return "empty-block", parsed, empty, best[2] if best else -1
    if parsed and max_declared < 2:
        # Nothing anywhere in the payload contains even two of the class's properties. The bound is irrelevant here:
        # either the block is absent or it does not have the shape read_block parses.
        return "no-declared-property", parsed, max_declared, best[2] if best else -1
    if single:
        return "single-property", parsed, 0, -1
    if unnamed:
        return "unnamed-property", parsed, 0, -1
    if parsed:
        return "no-clean-block", parsed, 0, -1
    return "no-block-parses", 0, 0, -1


def single_declared_block(pkg, mod, exp, declared, kinds=None):
    """The one-property block that ends on the payload's last byte, when the class declares its name.

    This is the shape a two-property floor withholds, and the one `--misses` used to call `incomplete`: a payload
    whose whole defaults block is a single tag -- a `*VoicePack` class's `VoiceSets` array, for eight of them. The
    tag has to tile the payload's tail exactly and its type must not contradict the declared kind, so "a parse that
    happens to end near the end" is not enough; what the check cannot decide is whether the tag is a block or a
    table tail, which is why the caller reports it as a reason rather than as a located block.
    """
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    for start in range(len(data)):
        try:
            props, end, reason = mod.read_block(data[start:], pkg, False)
        except Exception:
            continue
        if reason != "None" or len(props) != 1:
            continue
        prop = props[0]
        if prop["name"] not in declared or start + end != len(data):
            continue
        if contradictions(props, kinds):
            continue
        return start, prop["name"], prop["type"]
    return None


# ---------------------------------------------------------------- the closing group, as a rule in the chooser

# `tools/class-record-open-tail.py` parses the record's closing group (names array, reference, config name, list
# array) with no required end, and `tools/class-blockstart-guard.py` turned that into a rule: a candidate start a
# committed group covers is refused, and the group's end joins the candidate set. The rule belongs in the chooser --
# this file writes `inventory/class_defaults_per_class.csv`, whose `block_start` every count downstream inherits.
#
# Two things the guard left for measurement:
#
# * **A committed reference is one the package's own import table can hold.** The archive writes an import index
#   negative, and an `FCompactIndex` is five bytes wide, so a parse can carry a reference of -13,555,990,527 -- an
#   index no table holds. Measured on the 2,209 located rows: of the 1,654 groups whose end **is** the shipped start
#   (the control population, two witnesses), 1,653 carry a magnitude inside the import count and one does not;
#   the median is 6 and the largest 42. Of the groups that *cover* a shipped start, 18 rows' only covers are outside
#   the table, which is the guard refusing them on a parse that cannot be the writer's field.
# * **A group whose end reads as an empty block is followed by the block a few bytes later.** On the 13 refused rows
#   the guard left with nothing, the block opens 3, 4 or 11 bytes past the group's end on 7 (`Engine.Weapon` at 3,
#   the two `ZatGun_*` rows at 4, all four FORMATIONs at 11) and nowhere in the 12-byte window on the other 6.
GROUP_LOOKBACK = 48          # a closing group is at most this long (`class-record-open-tail.py` MAX_GROUP)
GROUP_TAIL = 12              # the block opens inside this many bytes past a group whose end reads as an empty block


def _load_otail():
    """`class-record-open-tail` loads this file, so it is loaded lazily (and only once)."""
    if "otail" not in _AUX:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "class-record-open-tail.py")
        spec = importlib.util.spec_from_file_location("class_record_open_tail", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _AUX["otail"] = mod
    return _AUX["otail"]


def committed_group(g, nimports):
    """Is this parse the writer's field, or bytes that tile as a group?

    The reference is what separates them: padding's reference is 0, and a parse inside a name run can carry any
    five-byte compact. The archive writes an import index negative and the class declares its import -- so a
    committed group's magnitude is one the package's import table can hold (`|ref| <= nimports`), which is the one
    test the control population supports (1,653 of 1,654 groups whose end is the shipped start pass it).
    """
    return g["ref"] < 0 and -g["ref"] <= nimports


def outermost(found):
    """Drop a group parse that begins inside another: its four fields are the enclosing group's bytes.

    Both parses cannot be the writer's, and the one that starts inside a field is the reading that invented
    itself -- `Engine.PSBumpBSP` is what it costs. Its record's tail run is `89..100` (`class_tail_run.csv`: names
    {1,2}, ref -3, cfg `System`, list {7,66,66}, ending exactly on the shipped start), and a second parse beginning
    at 99 read nine of those same bytes plus the block's own as names/ref/cfg/list, covering the start at 100 and
    making the guard refuse a block its own record's tail had just ended on. Keeping the outermost parse leaves the
    run and drops both the one-byte-offset twin and the nested one.
    """
    return [(q, e) for q, e in found if not any(q0 < q < e0 for q0, e0 in found)]


def group_parses(data, nnames, nimports):
    """Every offset where the writer's four-field group parses with a committed reference, as `(start, end)`."""
    otail = _load_otail()
    out = []
    for q in range(len(data)):
        g = otail.free_group(data, q, nnames)
        if g is not None and committed_group(g, nimports):
            out.append((q, q + g["len"]))
    return out


def group_intervals(data, nnames, nimports):
    """The writer's groups: `group_parses`, minus the parses that begin inside another one."""
    return outermost(group_parses(data, nnames, nimports))


def covered(intervals, start):
    """Is this candidate start inside a committed group? A group that *ends* on it is not a cover."""
    best = None
    for q, end in intervals:
        if q >= start:
            break
        if end > start and (best is None or end > best):
            best = end
    return best


def guarded(cands, data, pkg, decl, kinds, nimports):
    """The chooser's candidate set with the record's closing group as a rule, and where each start came from.

    A start inside a committed group is refused -- the group is a record field, so its bytes cannot be the defaults
    block's first byte. The group's **end** joins the set (often a one-property block the two-property floor
    withholds), and when that end reads as an *empty* block every offset in `[end, end + GROUP_TAIL)` that opens a
    block reaching the payload's last byte is offered as well: that is where the block sits past a record tail.
    """
    mod = load_prop_stream()
    nnames = len(pkg.names or [])
    parses = group_parses(data, nnames, nimports)
    intervals = outermost(parses)
    kept = [c for c in cands if covered(intervals, c[1]) is None]
    # The guard refused something: what remains has to be the block's own witness, so a candidate must run to the
    # payload's last byte (`trailer == 0`). A start that leaves bytes behind only won before because it carried more
    # declared names than the alternative; once the guard removes the alternative, keeping a start with no end witness
    # would be inventing a worse one. The floor is applied to the list this function returns, at the bottom -- filtering
    # `kept` here left it computed and dropped, and `tools/class-defaults-moved.py` measured 16 of the 1,972 refused
    # classes publishing a start with a nonzero trailer because of it.
    # The floor still triggers on a parse that begins inside another: it is not a boundary, but it is a sign that
    # these bytes read two ways, and the floor is what keeps a start with no end witness out of the set. Filtering
    # on it too is what withdrew the group-end candidate list and lifted the floor on six classes whose chosen start
    # then moved earlier to carry one more declared name and leave bytes behind.
    refused = any(covered(parses, c[1]) is not None for c in cands)
    origins = {c[1]: "candidate" for c in kept}
    for _q, end in intervals:
        if not (0 < end < len(data)) or end in origins:
            continue
        try:
            props, stop, reason = mod.read_block(data[end:], pkg, False)
        except Exception:
            continue
        if reason != "None":
            continue
        if len(props) == 1:
            # A one-property block that tiles the payload's tail, named by the class: `single-declared`, anchored
            # to the group's end instead of a scan over every offset.
            prop = props[0]
            if (prop["name"] in decl and end + stop == len(data)
                    and not contradictions(props, kinds)):
                cands.append((1, end, [prop["name"]], 0, 0))
                origins[end] = "group-end"
        if props:
            continue                                   # the group's end is a block in its own right
        for k in range(GROUP_TAIL):                    # an empty block: the record has a tail in front of the block
            off = end + k
            if off >= len(data) or off in origins:
                continue
            try:
                tape, stop2, reason2 = mod.read_block(data[off:], pkg, False)
            except Exception:
                continue
            if reason2 != "None" or not tape or off + stop2 != len(data):
                continue
            if any(p["name"] == "?" for p in tape) or contradictions(tape, kinds):
                continue
            if len(tape) == 1 and tape[0]["name"] not in decl:
                continue
            if all(p["name"] in decl for p in tape):
                cands.append((len(tape), off, [p["name"] for p in tape], 0, 0))
                origins[off] = "group-tail"
    out = [c for c in cands if covered(intervals, c[1]) is None]
    if refused:
        out = [c for c in out if c[3] == 0]
    return out, origins


def check_misses(outdir):
    """The two artifacts must agree: every not-fully-declared class explained once, no invented class."""
    per_class = os.path.join(outdir, "class_defaults_per_class.csv")
    misses_path = os.path.join(outdir, "class_defaults_misses.csv")
    try:
        wanted = {(r["package"], r["class"]) for r in csv.DictReader(open(per_class, newline="", encoding="utf-8"))
                  if r["fully_declared"] != "1"}
        rows = list(csv.DictReader(open(misses_path, newline="", encoding="utf-8")))
    except OSError as exc:
        print("FAIL class-defaults: %s" % exc)
        return 1
    got = {(r["package"], r["class"]) for r in rows}
    reasons = collections.Counter(r["reason"] for r in rows)
    blockless = sum(v for k, v in reasons.items()
                    if k in ("single-property", "trailer-over-64", "no-declared-property", "empty-block"))
    problems = []
    # A `single-declared` row claims two things, and both are checkable from the inventory: the block it names
    # holds exactly one property, and that property is one the class (or an ancestor) declares.
    supers, declared_all = super_map(), declared_properties()
    for r in rows:
        prop = r.get("property") or ""
        if r["reason"] != "single-declared":
            if prop:
                problems.append("%s.%s carries a property on a %s row"
                                % (r["package"], r["class"], r["reason"]))
            continue
        closure, cur, seen = set(), r["class"], set()
        while cur and cur not in seen:
            seen.add(cur)
            closure |= declared_all.get(cur, set())
            cur = supers.get(cur)
        if not prop:
            problems.append("%s.%s is single-declared without naming its property"
                            % (r["package"], r["class"]))
        elif prop not in closure:
            problems.append("%s.%s is single-declared on `%s`, which the class does not declare"
                            % (r["package"], r["class"], prop))
        elif r["best_properties"] != "1":
            problems.append("%s.%s is single-declared but holds %s properties"
                            % (r["package"], r["class"], r["best_properties"]))
    if got != wanted:
        problems.append("%d classes explained but %d not fully declared (%d missing, %d invented)"
                        % (len(got), len(wanted), len(wanted - got), len(got - wanted)))
    if reasons.get("no-block-parses"):
        problems.append("%d classes have no parseable block at all -- that contradicts the locator"
                        % reasons["no-block-parses"])
    if problems:
        print("FAIL class-defaults: " + "; ".join(problems))
        return 1
    print("ok   class-defaults: %d classes without a fully declared block (%d with no usable block: "
          "%d empty-block, %d single-property, %d past the trailer bound; %d single-declared)"
          % (len(rows), blockless, reasons.get("empty-block", 0),
             reasons.get("single-property", 0), reasons.get("trailer-over-64", 0),
             reasons.get("single-declared", 0)))
    return 0


def misses(system_dir, outdir):
    """Explain every class whose block is missing or incomplete, in the committed artifact's own terms.

    The selection is not re-invented here: `inventory/class_defaults_per_class.csv` already says which classes have no
    block (`defaults == 0`) and which have one that is not every declared property (`fully_declared == 0`). This walks
    the same packages only to say *why* the first group has none, so the two artifacts reconcile by construction.
    """
    mod = load_prop_stream()
    committed = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "inventory", "class_defaults_per_class.csv"),
                                 newline="", encoding="utf-8")):
        committed[(r["package"], r["class"])] = (int(r["defaults"]), r["fully_declared"])
    supers = super_map()
    declared_all = declared_properties()
    rows = []
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = fname[:-2]
        for exp in [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]:
            cls = pkg.name(exp["name"])
            state = committed.get((pkg_name, cls))
            if state is None:
                continue
            defaults, fully = state
            if defaults and fully == "1":
                continue                                    # nothing to explain
            decl, cur, seen = set(), cls, set()
            while cur and cur not in seen:
                seen.add(cur)
                decl |= declared_all.get(cur, set())
                cur = supers.get(cur)
            kinds = kinds_for_class(cls)
            if defaults:
                cands, data = candidates(pkg, mod, exp, kinds=kinds)
                # The chooser's own candidate set, not a re-derivation of it: without the record's closing group
                # a one-tag block at the group's end is invisible here while `all_packages` published its start,
                # and a parse *inside* the group wins the ranking -- which is how nine of the twelve container
                # panels were reported as `type-contradiction` on a block the sweep had already located.
                cands, _where = guarded(cands, data, pkg, decl, kinds, len(pkg.imports or []))
                best = choose(cands, decl, declared_all.get(cls, set()))
                bad = best[4] if best else 0
                if best and best[1] == 1 and best[3][0] in decl:
                    # The published block holds one tag and the class declares it: a `single-declared` row, the
                    # same verdict `check_misses` gives the shape when the scan finds it without the guard.
                    reason, props, trailer, prop = "single-declared", 1, 0, best[3][0]
                else:
                    reason, props, trailer, prop = "type-contradiction" if bad else "incomplete", \
                        defaults, fully, ""
                if reason == "incomplete":
                    # No parse holds two declared names: whatever block this payload has, the only block-shaped
                    # evidence is one tag. Name it instead of calling the class incomplete (a floor of one would
                    # promote the class here on that tag alone -- the measurement in MIN_PROPERTIES).
                    known = max([sum(1 for n in c[2] if n in decl) for c in cands], default=0)
                    found = single_declared_block(pkg, mod, exp, decl, kinds) if known < 2 else None
                    if found:
                        reason, props, trailer, prop = "single-declared", 1, 0, found[1]
                rows.append([pkg_name, cls, len(decl), reason, len(cands), props, trailer, prop])
                continue
            reason, parsed, props, trailer = diagnose(pkg, mod, exp, decl)
            rows.append([pkg_name, cls, len(decl), reason, parsed, props, trailer, ""])
    path = os.path.join(outdir, "class_defaults_misses.csv")
    os.makedirs(outdir, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["package", "class", "declared_properties", "reason", "candidates", "best_properties",
                    "detail", "property"])
        w.writerows(rows)
    counts = collections.Counter(r[3] for r in rows)
    print("%d classes explained against class_defaults_per_class.csv: %s"
          % (len(rows), ", ".join("%s %d" % (k, v) for k, v in counts.most_common())))
    print("wrote %s" % path)
    return 0


def choose(cands, declared, own=None):
    """The declared names first, then no contradicting tag type, then the length, the own names, the trailer.

    Type agreement is the *tie-break*, not the leading term: `KConeLimit`'s chosen block types `CollisionHeight`
    (a `Float` property) as a 16-byte `Float` and `PhysicsVolume` (an `Object`) as a `Float`, while a shorter
    candidate at a later offset agrees with both — so among candidates that explain the same names, prefer the one
    whose tag types the class's own property record allows. Ranking it *first* is measured in
    `docs/CLASS_DEFAULTS.md`: it promotes 535 withheld classes to clean blocks, which is a claim about those
    blocks that nothing here has checked yet.

    `own` is the set the class *itself* declares (not the ancestor closure), and it breaks a tie in the number
    of tags: a class's defaults block opens on its own declarations, so when two parses read the same count to
    the payload's end, the one that names more of them is the block. The term sits after the count and before
    the trailer, which is the narrowest of the three orderings the corpus allows: four rows move, and the leading
    `own` variant moves the same four (`docs/CLASS_DEFAULTS.md`, *The block opens on a class's own declaration*).
    """
    best = None
    for count, start, names, trailer, bad in cands:
        known = sum(1 for n in names if n in declared)
        own_known = sum(1 for n in names if n in own) if own else 0
        score = (known / float(count), bad == 0, count, own_known, -trailer)
        if best is None or score > best[0]:
            best = (score, count, start, names, bad)
    return best


def all_packages(system_dir, outdir, floor=MIN_PROPERTIES):
    """Sweep every package into one CSV: package, class, defaults, declared fraction, block start.

    Written as its own mode because `--sweep -o` writes `class_defaults.csv` per invocation and a loop over
    packages would leave only the last one -- the flag-shared artifact trap.
    """
    mod = load_prop_stream()
    rows = []
    origins = collections.Counter()
    supers = super_map()
    declared_all = declared_properties()
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = fname[:-2]
        for exp in [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]:
            cls = pkg.name(exp["name"])
            decl, cur, seen = set(), cls, set()
            while cur and cur not in seen:
                seen.add(cur)
                decl |= declared_all.get(cur, set())
                cur = supers.get(cur)
            cands, data = candidates(pkg, mod, exp, kinds=kinds_for_class(cls), floor=floor)
            cands, where = guarded(cands, data, pkg, decl, kinds_for_class(cls), len(pkg.imports or []))
            best = choose(cands, decl, declared_all.get(cls, set()))
            if best is None:
                # No start survives the group rule: the class sets no defaults after its record. That is the
                # honest reading of the rows the guard refused with no replacement, not a located start inside the
                # group (`class_defaults_misses.csv` explains each one).
                origins["no-block"] += 1
                rows.append([pkg_name, cls, 0, 0.0, "", 0])
                continue
            _score, count, start, names, bad = best
            known = sum(1 for n in names if n in decl)
            if known == 0:
                # A start whose parse names none of the class's properties is not a block, whatever its bytes look
                # like: on all 196 rows of this shape no offset of the payload names a declared property, so the
                # parse is the grammar's permissiveness plus the payload-end witness, not a located block
                # (`tools/class-defaults-silent.py`, `docs/CLASS_DEFAULTS.md`). The start is withheld, not moved,
                # and `--misses` explains the row with `diagnose()`'s `no-declared-property`.
                origins["no-declared-name"] += 1
                rows.append([pkg_name, cls, 0, 0.0, "", 0])
                continue
            origins[where.get(start, "candidate")] += 1
            # The two-property floor is a verdict about the *block*, not only about the candidate set: a
            # one-property parse at a group's end is where a table tail and a block look the same, and MIN_PROPERTIES
            # measured that promoting those turns 320 misses into "fully declared" on a single tag. The start is
            # published (`defaults = 1`), the verdict is withheld (`single-declared` in the misses table).
            rows.append([pkg_name, cls, count, round(known / float(count), 4), start,
                         1 if known == count and bad == 0 and count >= floor else 0])
    path = os.path.join(outdir, "class_defaults_per_class.csv")
    os.makedirs(outdir, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["package", "class", "defaults", "declared_fraction", "block_start", "fully_declared"])
        w.writerows(rows)
    total = len(rows)
    clean = sum(r[5] for r in rows)
    print("%d classes across %d packages; %d fully declared (%.1f%%)"
          % (total, len({r[0] for r in rows}), clean, 100.0 * clean / total if total else 0.0))
    print("chosen start origin: " + ", ".join("%s %d" % (k, v) for k, v in origins.most_common()))
    print("wrote %s" % path)
    return 0


def coverage(system_dir, outdir, floor=MIN_PROPERTIES):
    """Per-package rollup of the locator: classes, blocks found, fully declared, ambiguous, no block.

    This is the artifact `inventory/class_defaults_coverage.csv`, which until now had no generator: it was
    produced once by hand from `--sweep` output. Same definitions, one file per package.
    """
    mod = load_prop_stream()
    supers = super_map()
    declared = declared_properties()
    rows = []

    def known_for(cls):
        out, seen, cur = set(), set(), cls
        while cur and cur not in seen:
            seen.add(cur)
            out |= declared.get(cur, set())
            cur = supers.get(cur)
        return out

    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        classes = blocks = clean = ambiguous = none_found = 0
        for exp in [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]:
            classes += 1
            cls = pkg.name(exp["name"])
            cands, data = candidates(pkg, mod, exp, kinds=kinds_for_class(cls), floor=floor)
            cands, _origins = guarded(cands, data, pkg, known_for(cls), kinds_for_class(cls), len(pkg.imports or []))
            if not cands:
                none_found += 1
                continue
            best = choose(cands, known_for(cls), declared.get(cls, set()))
            if not any(n in known_for(cls) for n in best[3]):
                # The same withholding `all_packages` applies: a start that names none of the class's properties is
                # not a block, so it counts as no block here rather than as an ambiguous one.
                none_found += 1
                continue
            blocks += 1
            # same verdict as `all_packages`: the two-property floor withholds "fully declared" for a one-tag block
            if best[0][0] == 1.0 and best[4] == 0 and best[1] >= floor:
                clean += 1
            elif len(cands) > 1:
                ambiguous += 1
        rows.append([fname[:-2], classes, blocks, clean, ambiguous, none_found])
    path = os.path.join(outdir, "class_defaults_coverage.csv")
    os.makedirs(outdir, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["package", "classes", "blocks_found", "fully_declared", "ambiguous", "no_block"])
        w.writerows(rows)
    total = [sum(r[i] for r in rows) for i in range(1, 6)]
    print("%d packages: %d classes, %d blocks found, %d fully declared, %d ambiguous, %d no block"
          % (len(rows), *total))
    print("wrote %s" % path)
    return 0



# ---------------------------------------------------------------- the page's generated totals, and its gates

# `docs/CLASS_DEFAULTS.md` is hand-written prose whose headline numbers were three runs stale (it said 1,663 fully
# declared and 200 `incomplete` while the artifacts said 1,664 and 4), because nothing compared the page with the files
# it quotes. The totals are now rendered from the two committed artifacts between markers, and `--check` -- which the
# `tool-check-contract` gate in `make ci` runs -- fails on a byte difference or on a reason row whose count has moved.
DOC = os.path.join(ROOT, "docs", "CLASS_DEFAULTS.md")
DOC_START = "<!-- generated:defaults-totals -->"
DOC_END = "<!-- /generated:defaults-totals -->"


def artifact_totals():
    """The counts the page quotes, read from the two committed artifacts (no dump)."""
    per = os.path.join(ROOT, "inventory", "class_defaults_per_class.csv")
    misses = os.path.join(ROOT, "inventory", "class_defaults_misses.csv")
    total = full = no_block = 0
    with open(per, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            total += 1
            full += 1 if r["fully_declared"] == "1" else 0
            no_block += 1 if r["defaults"] == "0" else 0
    reasons = collections.Counter()
    with open(misses, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            reasons[r["reason"]] += 1
    return total, full, total - full, no_block, reasons


def render_totals():
    """The block, one sentence and its source line."""
    total, full, missing, no_block, _reasons = artifact_totals()
    return ("**%s of the %s classes are fully declared (%.1f%%), %s are not, and %s of those have no block at all**\n"
            "-- `inventory/class_defaults_per_class.csv` and `inventory/class_defaults_misses.csv`, rendered by\n"
            "`python3 tools/class-defaults.py --write-doc`"
            % ("{:,}".format(full), "{:,}".format(total), 100.0 * full / total, "{:,}".format(missing),
               "{:,}".format(no_block)))


def splice_doc(text, block, path=DOC):
    """`text` with whatever sits between the two markers replaced by `block`."""
    if DOC_START not in text or DOC_END not in text:
        raise ValueError("%s has no %s ... %s markers" % (path, DOC_START, DOC_END))
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def write_doc(path=DOC):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    new = splice_doc(text, render_totals(), path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("wrote %s" % path)
    return 0


def doc_check(path=DOC):
    """The page against the artifacts: the generated block byte for byte, and every reason row's count."""
    problems, checked = [], 0
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    total, full, missing, no_block, reasons = artifact_totals()
    try:
        want = splice_doc(text, render_totals(), path)
    except ValueError as exc:
        problems.append(str(exc))
        want = text
    if want != text:
        problems.append("the generated totals block differs from the artifacts (%d of %d fully declared, %d with no "
                        "block); run `--write-doc`" % (full, total, no_block))
    lines = text.splitlines()
    header = next((i for i, ln in enumerate(lines) if ln.strip() == "| reason | classes | what it means |"), None)
    if header is None:
        problems.append("the page has no reason table (| reason | classes | what it means |)")
    else:
        # Only the reason table is read: other tables in this page key on package and class names, and a scan over
        # every `| \`name\` | N |` row in the page reported six packages against a reason that is not theirs.
        for row in lines[header + 1:]:
            stripped = row.strip()
            if not stripped.startswith("|"):
                break
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) < 2:
                continue
            reason = cells[0].strip("`")
            try:
                got = int(cells[1].replace("**", "").replace(",", ""))
            except ValueError:
                continue
            # a reason no class carries is checked too: its row claims zero, which is true only while the artifact
            # has no row of that reason, so skipping it would leave the one count that can appear out of nowhere.
            if got != reasons.get(reason, 0):
                problems.append("the page says %s is %d, the artifact %d" % (reason, got, reasons.get(reason, 0)))
            checked += 1
    if problems:
        print("FAIL class-defaults: " + "; ".join(problems[:6]))
        return 1
    print("ok   class-defaults: %d of %d fully declared, %d with no block; the block and %d reason rows agree"
          % (full, total, no_block, checked))
    return 0


def selftest():
    """Synthetic candidate lists: the own-first tie-break, and that only a tie in the count can be broken.

    Dump-free and writes nothing, so `make ci`'s `tool-check-contract` can drive it. The first case is the rule
    the corpus measurement moved four rows on -- an earlier start that names only inherited properties against a
    later one of the same length whose first tag is the class's own -- and it fails on the pre-rule chooser.
    """
    # (count, start, names, trailer, bad), as `candidates()` yields them.
    inherited_first = (2, 83, ["__OnXControllerEvent__Delegate", "TitleString"], 0, 0)
    own_first = (2, 93, ["TempLB", "TitleString"], 0, 0)
    decl = {"__OnXControllerEvent__Delegate", "TitleString", "TempLB", "WinLeft", "WinTop", "bHidden"}
    own = {"TempLB"}
    best = choose([inherited_first, own_first], decl, own)
    assert best is not None and best[2] == 93, "the start opening on the class's own property wins a count tie"
    best = choose([inherited_first, own_first], decl)
    assert best is not None and best[2] == 83, "without an own set the tie still goes to the earlier start"
    # A longer parse that names no own property keeps winning: the own term sits after the count, not before it.
    longer = (3, 60, ["WinLeft", "WinTop", "bHidden"], 0, 0)     # every name declared, none of them the class's
    best = choose([longer, own_first], decl, own)
    assert best is not None and best[2] == 60, "the count still leads the own-first term"
    # The declared fraction and the type agreement lead both: a start with an own name that leaves a declared
    # property out loses to one that names every property it reads.
    mixed = (2, 70, ["TempLB", "NotDeclared"], 0, 0)
    best = choose([mixed, own_first], decl, own)
    assert best is not None and best[2] == 93, "the declared fraction leads the own-first term"
    typed = (2, 40, ["TempLB", "TitleString"], 0, 1)
    best = choose([typed, own_first], decl, own)
    assert best is not None and best[2] == 93, "a tag type the record contradicts still loses"
    print("selftest ok: own-first breaks a count tie, and nothing else moves")
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="a package file (not needed with --packages)")
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--sweep", action="store_true", help="every class in the package")
    ap.add_argument("--packages", metavar="DIR",
                    help="sweep every .u in DIR and write one per-class CSV (this is the mode that produces "
                         "inventory/class_defaults_per_class.csv; the per-package --sweep -o overwrites itself)")
    ap.add_argument("--misses", metavar="DIR",
                    help="classify every class without a fully declared block")
    ap.add_argument("--coverage", metavar="DIR",
                    help="roll every package up into inventory/class_defaults_coverage.csv")
    ap.add_argument("--walk", action="store_true",
                    help="print the raw byte walk of the chosen block (type byte, size, consumed bytes)")
    ap.add_argument("--check-misses", action="store_true",
                    help="verify class_defaults_misses.csv reconciles with class_defaults_per_class.csv")
    ap.add_argument("--floor", type=int, default=MIN_PROPERTIES,
                    help="minimum properties in a candidate block (default %d; a floor of one is measured and "
                         "rejected, see docs/CLASS_DEFAULTS.md)" % MIN_PROPERTIES)
    ap.add_argument("-o", "--out")
    ap.add_argument("--check", action="store_true",
                    help="synthetic candidate lists: the chooser's tie-break, no dump and no write")
    ap.add_argument("--write-doc", action="store_true",
                    help="rewrite the generated totals block in docs/CLASS_DEFAULTS.md from the artifacts")
    args = ap.parse_args()

    if args.write_doc:
        return write_doc()
    if args.check:
        rc = selftest()
        return doc_check() or rc
    if args.packages:
        return all_packages(args.packages, args.out or "inventory", args.floor)
    if args.check_misses:
        return check_misses(args.out or os.path.join(ROOT, "inventory"))
    if args.misses:
        return misses(args.misses, args.out or "inventory")
    if args.coverage:
        return coverage(args.coverage, args.out or "inventory", args.floor)

    mod = load_prop_stream()
    declared = declared_properties()
    pkg = Package(args.path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    # ancestor closure from the committed tree, so cross-package inheritance is followed
    supers = super_map()

    def known_for(cls):
        out, seen, cur = set(), set(), cls
        while cur and cur not in seen:
            seen.add(cur)
            out |= declared.get(cur, set())
            cur = supers.get(cur)
        return out

    targets = [e for e in pkg.exports
               if pkg.class_name_of(e) == "Class" and (args.sweep or pkg.name(e["name"]) == args.cls)]
    if not targets:
        sys.exit("no matching class")

    rows, clean, ambiguous, none_found = [], 0, 0, 0
    for exp in targets:
        cls = pkg.name(exp["name"])
        known = known_for(cls)
        cands, data = candidates(pkg, mod, exp, kinds=kinds_for_class(cls), floor=args.floor)
        cands, origins = guarded(cands, data, pkg, known, kinds_for_class(cls), len(pkg.imports or []))
        if not cands:
            none_found += 1
            continue
        score, count, start, names, bad = choose(cands, known, declared.get(cls, set()))
        if score[0] == 1.0 and bad == 0:
            clean += 1
        elif len(cands) > 1:
            ambiguous += 1
        rows.append({"class": cls, "properties": count, "start": "0x%x" % start,
                     "declared_fraction": "%.2f" % score[0]})
        if args.walk:
            # Byte-level walk: every property with its info byte and how many bytes its value consumed.
            # This is what showed that a Bool writes no size field: the byte after its info byte is the next
            # property's name index. Kept as a mode because it is the only way to settle an alignment question.
            off = start
            while off < len(data):
                name, after_name = mod.read_index(data, off)
                if name == 0:
                    break
                info = data[after_name]
                _entry, after_value = mod.read_value(data, after_name, pkg, False)
                print("   %-24s nameIdx=%-5d info=%#04x type=%-8s consumed=%d"
                      % (pkg.name(name), name, info, mod.TYPES.get(info & 0x0F),
                         after_value - after_name))
                off = after_value
        if args.cls:
            props, _end, _reason = mod.read_block(data[start:], pkg, True)
            print("%s: %d defaults at +0x%x, %.0f%% declared by the class"
                  % (cls, len(props), start, 100 * score[0]))
            for p in props:
                if p.get("value") is not None:
                    print("   %-24s %-8s %s" % (p["name"], p["type"], p["value"]))
            print("   candidates considered: %d" % len(cands))

    if args.sweep:
        print("classes: %d, blocks found: %d, none: %d" % (len(targets), len(rows), none_found))
        print("  every property declared by the class: %d" % clean)
        print("  partial match, more than one candidate: %d" % ambiguous)
    if args.out and rows:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "class_defaults.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["class", "properties", "start", "declared_fraction"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
