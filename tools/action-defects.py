#!/usr/bin/env python3
"""The defect list 5.2 owed: every branch in an `Actions` array that its own placement does not decide (PLAN 5.2).

`docs/MAP_ACTION_PATHS.md` closed with three counts it reported but did not name -- 5 branches whose
`ProceedToSectionEnd` walks off the array, 3 that state no operand at all, and 11 conditions whose
`TriggeredCondition` no placed property ever fires -- and `docs/MAP_ACTION_REACH.md` closed the reach question. What
was never written down is the list itself: which placement, in which map, in which sequence, is the one that is
undecided or dead. That list is what a player-visible defect would come from, and it is four kinds:

* `skip_off_array` -- a branch whose section never closes, so the skip edge has no target and the run simply stops;
* `operand_unset` -- a branch that states no operand, so the class's own default decides every pass;
* `unfired_condition` -- an `ACTION_IfCondition` whose `TriggeredCondition` tag no placed property fires, so the
  placed `bEnabled` is the only value it can ever have;
* `random_decided` -- an `ACTION_IfRandomPct` whose `Probability` is at or below 0 (skips every time) or at or
  above 1 (never skips), which is a coin the placement has already called. What is left of `FRand()`'s draw is the
  population in between, and that is counted too.

A fourth kind is not a branch's: `slot_unreached` -- an action a sequence wires at a slot no path reaches, which
`docs/MAP_ACTION_PATHS.md` counted 6 of in the straight run's tail and which the branch edges leave there.

    python3 tools/action-defects.py DUMP --sweep -o inventory
    python3 tools/action-defects.py --report
    python3 tools/action-defects.py --write-doc docs/MAP_ACTION_DEFECTS.md
    python3 tools/action-defects.py --check-doc docs/MAP_ACTION_DEFECTS.md
    python3 tools/action-defects.py --check          # synthetic, no dump, writes nothing

Rows name a map, a class and a slot index. No placement name, no tag value and no class text is written.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/action_defects.csv")
DOC = os.path.join(ROOT, "docs/MAP_ACTION_DEFECTS.md")
PATHS = os.path.join(ROOT, "inventory/action_paths.csv")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
DOC_START = "<!-- generated:action-defects -->"
DOC_END = "<!-- /generated:action-defects -->"

FIELDS = ["kind", "map", "name", "holder", "slot", "detail", "count", "note"]
KINDS = ("skip_off_array", "operand_unset", "unfired_condition", "random_decided", "slot_unreached",
         "random_drawn")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ap = _load("action_paths", "action-paths.py")
ao = ap.ao
aw = ap.aw
et = ap.et


def branch_spans(refs, arr_classes, rules):
    """The slots a path reaches, and the slots a branch's skip edge jumps over on a reached branch slot."""
    seen = set()
    stack = [0] if refs else []
    skipped = set()
    while stack:
        index = stack.pop()
        if index in seen or not (0 <= index < len(refs)):
            continue
        seen.add(index)
        rule = rules.get(arr_classes[index]) or {}
        if refs[index] == 0 or rule.get("leaves"):
            continue
        if rule.get("branches"):
            skip = ap.skip_target(arr_classes, rules, index)
            if skip is None:
                skipped.update(range(index + 1, len(refs)))
                if index + 1 < len(refs):
                    stack.append(index + 1)
                continue
            if skip > index + 1:
                skipped.update(range(index + 1, skip))
            if index + 1 < len(refs):
                stack.append(index + 1)
            if skip != index + 1:
                stack.append(skip)
            continue
        if rule.get("goto"):
            continue
        if index + 1 < len(refs):
            stack.append(index + 1)
    return seen, skipped


def measure(entries, exports, rules, branch_of, cond_tags, placed_tags, sequence_cls, action_cls, fired):
    """One map -> (rows, counts). No package needed, so `--check` can drive it."""
    classes = [e["class"] for e in entries]
    rows, counts = [], collections.Counter()
    for idx in sorted(exports):
        rec = exports[idx]
        if rec.get("actions") is None:
            continue
        holder = classes[idx - 1] if 0 < idx <= len(classes) else ""
        if holder not in sequence_cls:
            continue
        _count, refs = rec["actions"]
        arr_classes = ap.slots_of(refs, classes)
        seen, skipped = branch_spans(refs, arr_classes, rules)
        for slot, ref in enumerate(refs):
            if ref <= 0 or ref > len(classes):
                continue
            cls = arr_classes[slot]
            is_action = (not action_cls) or cls in action_cls
            if is_action and slot not in seen:
                counts["slot_unreached"] += 1
                rows.append(dict(kind="defect", map="", name=cls, holder=holder, slot=slot,
                                 detail="slot_unreached", note="wired, on no path"))
            info = branch_of.get(cls) or {}
            kind = info.get("kind")
            if not kind or slot not in seen:
                continue
            counts["branches"] += 1
            state = ap.classify(kind, exports.get(ref) or {}, cond_tags, placed_tags)
            skip = ap.skip_target(arr_classes, rules, slot)
            if skip is None:
                counts["skip_off_array"] += 1
                rows.append(dict(kind="defect", map="", name=cls, holder=holder, slot=slot,
                                 detail="skip_off_array", note="the section never closes"))
                continue
            if kind == "random":
                prob = exports[ref].get("probability")
                if prob is None:
                    counts["operand_unset"] += 1
                    rows.append(dict(kind="defect", map="", name=cls, holder=holder, slot=slot,
                                     detail="operand_unset", note=info.get("operand", "")))
                elif prob <= 0.0:
                    counts["random_decided"] += 1
                    rows.append(dict(kind="defect", map="", name=cls, holder=holder, slot=slot,
                                     detail="random_decided", note="probability at or below 0: skips every pass"))
                elif prob >= 1.0:
                    counts["random_decided"] += 1
                    rows.append(dict(kind="defect", map="", name=cls, holder=holder, slot=slot,
                                     detail="random_decided", note="probability at or above 1: never skips"))
                else:
                    counts["random_drawn"] += 1
                continue
            if state == "unset":
                counts["operand_unset"] += 1
                rows.append(dict(kind="defect", map="", name=cls, holder=holder, slot=slot,
                                 detail="operand_unset", note=info.get("operand", "")))
                continue
            if kind == "condition" and state == "resolved":
                actor = (cond_tags.get((exports[ref].get("cond_tag") or "").lower()) or [None])[0]
                actor_tag = ((exports.get(actor) or {}).get("tag") or "").lower()
                if actor and (not actor_tag or actor_tag not in fired):
                    counts["unfired_condition"] += 1
                    rows.append(dict(kind="defect", map="", name=cls, holder=holder, slot=slot,
                                     detail="unfired_condition",
                                     note="placed bEnabled=%s" % (exports[actor].get("benabled"),)))
    return rows, counts


def sweep(dump, out, classes_path=CLASSES, only=None):
    maps_dir = os.path.join(dump, "Maps")
    system_dir = os.path.join(dump, "System")
    if not os.path.isdir(maps_dir):
        sys.exit("no Maps/ under %s" % dump)
    supers = ao.class_supers(classes_path)
    rules = ao.class_rules(system_dir, supers)
    branch_of = ap.branch_classes(system_dir, rules)
    sequence_cls = ao.family(supers, "ScriptedSequence") | {"ScriptedSequence"}
    action_cls = aw.family(supers, "ScriptedAction") - et.ACTION_BASES
    marks = {c for c, r in rules.items() if r["role"] == "holder" and r.get("gate")}
    gated = set(marks)
    for cls in supers:
        cur, trail = cls, set()
        while cur in supers and cur not in trail:
            trail.add(cur)
            if cur in marks:
                gated.add(cls)
                break
            cur = supers[cur]
    values = {c: r.get("gate_value") for c, r in rules.items() if r["role"] == "action"}
    branch_rows = [dict(kind="class", name=c, holder="", slot="", detail=b.get("kind", ""), count="",
                        note=b.get("operand", "")) for c, b in sorted(branch_of.items())]
    rows, per_map = [], []
    for name in sorted(os.listdir(maps_dir)):
        if not name.lower().endswith(".unr"):
            continue
        label = name[:-4]
        if only and only.lower() not in label.lower():
            continue
        pkg = Package(os.path.join(maps_dir, name))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        entries = [{"class": pkg.class_name_of(e), "name": pkg.name(e["name"])} for e in pkg.exports]
        exports = ap.read_exports(pkg)
        fired = {n.lower() for rec in exports.values() for n in rec.get("fires") or []}
        cond_tags = ap.tag_index(exports, entries, "TriggeredCondition")
        placed_tags = ap.tag_index(exports, entries, "")
        found, counts = measure(entries, exports, rules, branch_of, cond_tags, placed_tags, sequence_cls,
                                action_cls, fired)
        for r in found:
            r["map"] = label
        rows.extend(found)
        per_map.append(dict(counts, map=label))
        print("%-26s %s" % (label, " ".join("%s=%d" % (k, counts[k]) for k in
                                            ("branches", "skip_off_array", "operand_unset", "unfired_condition",
                                             "random_decided", "random_drawn", "slot_unreached"))), flush=True)
    out_rows = [dict(r) for r in rows]
    out_rows.extend(branch_rows)
    census(out_rows, per_map=per_map)
    if out:
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "action_defects.csv")
        write_artifact(out_rows, path)
        print("%s: %d rows" % (path, len(out_rows)))
    return out_rows


def census(rows, per_map=None):
    """One `census` row per defect kind plus a `maps` row, so the page reads totals from the artifact."""
    per_map = per_map or []
    out = []
    for c in per_map:
        out.append(dict(kind="map", map=c.get("map", ""), name="", holder="", slot="", detail="",
                        count="", note=";".join("%s=%d" % (k, c[k]) for k in sorted(c) if k != "map")))
    totals = collections.Counter()
    for c in per_map:
        for key, value in c.items():
            if key != "map":
                totals[key] += value
    for key in KINDS + ("branches",):
        out.append(dict(kind="census", map="corpus", name=key, holder="", slot="", detail="", count=totals[key],
                        note=""))
    out.append(dict(kind="census", map="corpus", name="maps", holder="", slot="", detail="",
                    count=len(per_map), note=""))
    rows.extend(out)
    return out


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def num(row, key):
    try:
        return int(row.get(key, 0))
    except (TypeError, ValueError):
        return 0


def render(rows):
    census_rows = {r["name"]: num(r, "count") for r in rows if r["kind"] == "census"}
    defects = [r for r in rows if r["kind"] == "defect"]
    classes = [r for r in rows if r["kind"] == "class"]
    out = []
    out.append("**%d branch placements in the 73 maps are decided by nothing but their own default, and %d "
               "`ACTION_IfRandomPct` slots are a coin the placement already called -- so `FRand()` decides the %d "
               "that are left.**"
               % (census_rows.get("skip_off_array", 0) + census_rows.get("operand_unset", 0)
                  + census_rows.get("unfired_condition", 0), census_rows.get("random_decided", 0),
                  census_rows.get("random_drawn", 0)))
    out.append("")
    out.append("`docs/MAP_ACTION_PATHS.md` reported these as counts and `docs/MAP_ACTION_REACH.md` closed the reach "
               "question beside them; this page is the list those counts are made of. Each row names a map, the "
               "class of the element and the slot index inside its sequence, because what a defect is depends on "
               "where it sits: a branch that skips past the end of its array stops a run, while the same branch "
               "one slot earlier would only skip a body.")
    out.append("")
    out.append("| defect | placements | what it means |")
    out.append("|---|---:|---|")
    out.append("| `skip_off_array` | **%d** | the branch's section never closes, so `ProceedToSectionEnd` walks "
               "off the array and the run ends there |" % census_rows.get("skip_off_array", 0))
    out.append("| `operand_unset` | **%d** | the branch states no operand, so the class default decides every "
               "pass |" % census_rows.get("operand_unset", 0))
    out.append("| `unfired_condition` | **%d** | the `TriggeredCondition` it names is fired by no placed property, "
               "so the placed `bEnabled` is its value for the whole play |"
               % census_rows.get("unfired_condition", 0))
    out.append("| `random_decided` | **%d** | `Probability` at or below 0 (skips every pass) or at or above 1 "
               "(never skips): placed data, not a draw |" % census_rows.get("random_decided", 0))
    out.append("| `slot_unreached` | **%d** | an action wired at a slot no path reaches |"
               % census_rows.get("slot_unreached", 0))
    out.append("")
    out.append("| control | this walk | `action_paths.csv` |")
    out.append("|---|---:|---:|")
    out.append("| branch slots | **%d** | 260 |" % census_rows.get("branches", 0))
    out.append("| skip that walks off the array | **%d** | 5 |" % census_rows.get("skip_off_array", 0))
    out.append("| branch with no operand stated | **%d** | 3 |" % census_rows.get("operand_unset", 0))
    out.append("")
    out.append("**`FRand()`'s draw is %d slots, not a schedule.** Of the %d `ACTION_IfRandomPct` placements that "
               "walk a path, %d state a probability strictly between 0 and 1 -- the population a play's randomness "
               "actually decides -- and %d state one the placement has already resolved. That is the whole of what "
               "5.2 could not settle, and it is now a count rather than a caveat."
               % (census_rows.get("random_drawn", 0),
                  census_rows.get("random_drawn", 0) + census_rows.get("random_decided", 0),
                  census_rows.get("random_drawn", 0), census_rows.get("random_decided", 0)))
    out.append("")
    if defects:
        out.append("The first rows of `inventory/action_defects.csv` are the placements themselves -- one row per "
                   "defect, `kind=defect`, with its map, element class, holding sequence class and slot index -- so "
                   "a claim about a map can be checked against the artifact instead of re-derived.")
    out.append("")
    out.append("### The defect placements by kind and class")
    out.append("")
    out.append("| defect | element class | placements |")
    out.append("|---|---|---:|")
    by = collections.Counter((r["detail"], r["name"]) for r in defects)
    for (detail, name), count in sorted(by.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1]))[:20]:
        out.append("| `%s` | `%s` | %s |" % (detail, name, count))
    out.append("")
    out.append("| branch class | operand it reads | checks |")
    out.append("|---|---|---|")
    for r in classes:
        out.append("| `%s` | `%s` | %s |" % (r["name"], r["note"], r["detail"]))
    out.append("")
    out.append("Census: maps=%d;branches=%d;skip_off_array=%d;operand_unset=%d;unfired_condition=%d;"
               "random_decided=%d;random_drawn=%d;slot_unreached=%d."
               % (census_rows.get("maps", 0), census_rows.get("branches", 0),
                  census_rows.get("skip_off_array", 0), census_rows.get("operand_unset", 0),
                  census_rows.get("unfired_condition", 0), census_rows.get("random_decided", 0),
                  census_rows.get("random_drawn", 0), census_rows.get("slot_unreached", 0)))
    return "\n".join(out)


def splice(path, block):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("markers not found in %s" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def write_doc(path, rows):
    new = splice(path, render(rows))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("wrote %s" % path)


def check_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("markers not found in %s" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    if head + DOC_START + "\n" + render(rows) + "\n" + DOC_END + tail != text:
        sys.exit("%s is stale: run --write-doc" % path)
    print("%s matches the artifact" % path)


def report(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    sys.stdout.write(render(rows))


def check():
    """Synthetic maps only: the four defect kinds, the drawn random slot and the unreached slot."""
    rules = {"ACTION_Move": dict(moves=False), "ACTION_IfCondition": dict(opens=True, branches=True),
             "ACTION_IfRandomPct": dict(opens=True, branches=True), "ACTION_EndSection": dict(closes=True),
             "ACTION_LeaveSequence": dict(leaves=True)}
    branch_of = {"ACTION_IfCondition": dict(kind="condition", operand="TriggeredConditionTag"),
                 "ACTION_IfRandomPct": dict(kind="random", operand="Probability")}
    sequence_cls = {"ScriptedTrigger"}
    action_cls = set(rules)
    # 1 Level, 2 holder A (unfired condition), 3 cond unfired, 4 move, 5 end,
    # 6 holder B (unset condition), 7 cond with no tag, 8 holder C (probability 0), 9 holder D (0.4),
    # 10 random action, 11 random with no probability
    entries = [{"class": "Level", "name": "L"},
               {"class": "ScriptedTrigger", "name": "SA"},
               {"class": "ACTION_IfCondition", "name": "C1"},
               {"class": "ACTION_Move", "name": "M"},
               {"class": "ACTION_EndSection", "name": "E"},
               {"class": "ScriptedTrigger", "name": "SB"},
               {"class": "ACTION_IfCondition", "name": "C2"},
               {"class": "ScriptedTrigger", "name": "SC"},
               {"class": "ScriptedTrigger", "name": "SD"},
               {"class": "ACTION_IfRandomPct", "name": "R0"},
               {"class": "ACTION_IfRandomPct", "name": "R9"}]
    exports = {2: dict(actions=(3, [3, 4, 5]), tag="EV_A", fires=[]),        # C1: tag nothing fires
               6: dict(actions=(3, [7, 4, 5]), tag="EV_B", fires=[]),         # C2: no tag at all
               8: dict(actions=(3, [10, 4, 5]), tag="EV_C", fires=[]),        # R0: probability 0.4
               9: dict(actions=(3, [11, 4, 5]), tag="EV_D", fires=[]),        # R9: no probability
               3: dict(actions=None, tag="", fires=[], cond_tag="COND_A"),
               7: dict(actions=None, tag="", fires=[], cond_tag="None"),
               10: dict(actions=None, tag="", fires=[], probability=0.4),
               11: dict(actions=None, tag="", fires=[], probability=None)}
    rows, counts = measure(entries, exports, rules, branch_of, {"cond_a": [3]}, {}, sequence_cls, action_cls,
                           set())
    want = dict(branches=4, skip_off_array=0, operand_unset=2, unfired_condition=1, random_decided=0,
                random_drawn=1, slot_unreached=0)
    for key, value in want.items():
        if counts[key] != value:
            print("FAIL action-defects: synthetic %s=%r, want %r" % (key, counts[key], value))
            return 1
    # a probability at zero is a coin the placement already called
    exports_zero = dict(exports)
    exports_zero[9] = dict(actions=(3, [9, 4, 5]), tag="EV_D", fires=[])
    exports_zero[12] = dict(actions=None, tag="", fires=[], probability=0.0)
    exports_zero[9] = dict(actions=(3, [12, 4, 5]), tag="EV_D", fires=[])
    entries_zero = entries + [{"class": "ACTION_IfRandomPct", "name": "R0b"}]
    _r0, counts0 = measure(entries_zero, exports_zero, rules, branch_of, {"cond_a": [3]}, {}, sequence_cls,
                           action_cls, set())
    if (counts0["random_decided"], counts0["random_drawn"], counts0["operand_unset"]) != (1, 1, 1):
        print("FAIL action-defects: probability zero %r" % (dict(counts0),))
        return 1
    # a branch whose section never closes, and a slot behind a LeaveSequence
    exports2 = {2: dict(actions=(2, [3, 4]), tag="EV_A", fires=[]),
                3: dict(actions=None, tag="", fires=[], cond_tag="COND_A")}
    _r2, counts2 = measure(entries, exports2, rules, branch_of, {"cond_a": [3]}, {}, sequence_cls, action_cls,
                           set())
    if (counts2["skip_off_array"], counts2["branches"]) != (1, 1):
        print("FAIL action-defects: open section %r" % (dict(counts2),))
        return 1
    exports3 = {2: dict(actions=(3, [4, 5, 7]), tag="EV_A", fires=[]),
                7: dict(actions=None, tag="", fires=[], cond_tag="None")}
    rules3 = dict(rules)
    rules3["ACTION_Move"] = dict(leaves=True)
    _r3, counts3 = measure(entries, exports3, rules3, branch_of, {"cond_a": [3]}, {}, sequence_cls, action_cls,
                           set())
    if counts3["slot_unreached"] != 2:
        print("FAIL action-defects: unreached slot %r" % (dict(counts3),))
        return 1
    # a fired condition is not a defect
    exports4 = {2: dict(actions=(3, [3, 4, 5]), tag="EV_A", fires=[]),
                3: dict(actions=None, tag="COND_A", fires=[], cond_tag="COND_A")}
    _r4, counts4 = measure(entries, exports4, rules, branch_of, {"cond_a": [3]}, {}, sequence_cls, action_cls,
                           {"cond_a"})
    if counts4["unfired_condition"] != 0:
        print("FAIL action-defects: fired condition %r" % (dict(counts4),))
        return 1
    # the census writes one row per kind, and the map rows carry their own counts
    out = [dict(r, map="A") for r in rows]
    out.extend([dict(r, map="A") for r in rows])
    total = census(list(out), per_map=[dict(branches=4, skip_off_array=0, operand_unset=1, unfired_condition=1,
                                            random_decided=0, random_drawn=1, slot_unreached=0, map="A")])
    got = {r["name"]: num(r, "count") for r in total if r["kind"] == "census"}
    if (got["branches"], got["operand_unset"], got["unfired_condition"], got["random_drawn"], got["maps"]) != \
            (4, 1, 1, 1, 1):
        print("FAIL action-defects: census %r" % (got,))
        return 1
    block = render(list(rows) + total)
    for want_text in ("FRand()", "skip_off_array", "Census: maps=1"):
        if want_text not in block:
            print("FAIL action-defects: render is missing %r" % want_text)
            return 1
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.csv")
        write_artifact(total, path)
        with open(path, newline="", encoding="utf-8") as fh:
            back = list(csv.DictReader(fh))
        if len(back) != len(total) or set(back[0]) != set(FIELDS):
            print("FAIL action-defects: artifact round trip %d/%d" % (len(back), len(total)))
            return 1
    print("ok   action-defects: the four defect kinds, the drawn random slot and the unreached slot are told "
          "apart, and the census totals come from the map rows")
    return 0


def main():
    cli = argparse.ArgumentParser(description="the branch placements their own data does not decide (PLAN 5.2)")
    cli.add_argument("dump", nargs="?", help="game directory (.../Stargate_SG-1_-_The_Alliance-2005-12-15)")
    cli.add_argument("--sweep", action="store_true", help="read every map and write the artifact")
    cli.add_argument("-o", "--out", help="artifact directory (inventory)")
    cli.add_argument("--only", help="one map, by name fragment")
    cli.add_argument("--report", action="store_true", help="render the committed artifact")
    cli.add_argument("--write-doc", metavar="PATH", help="splice the generated block into a page")
    cli.add_argument("--check-doc", metavar="PATH", help="fail when the page does not match the artifact")
    cli.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = cli.parse_args()
    if args.check:
        return check()
    if args.report:
        report()
        return 0
    if args.sweep:
        if not args.dump:
            sys.exit("--sweep needs the game directory")
        rows = sweep(args.dump, args.out, only=args.only)
        if args.write_doc:
            write_doc(args.write_doc, rows)
        if args.check_doc:
            check_doc(args.check_doc, rows)
        return 0
    if args.write_doc or args.check_doc:
        with open(ARTIFACT, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if args.write_doc:
            write_doc(args.write_doc, rows)
        if args.check_doc:
            check_doc(args.check_doc, rows)
        return 0
    cli.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
