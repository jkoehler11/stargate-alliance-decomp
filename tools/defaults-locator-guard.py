#!/usr/bin/env python3
"""What the emitter's class-level locator gains from the record-group rule -- the record that settled it.

`tools/class-defaults.py` chooses a class's defaults block with the record's closing group as a rule
(`guarded()`), and `tools/defaults-emit.py` did not, on the reading that the rule's 313 starts are one-tag blocks
the two-property floor refuses a verdict on. This tool measured both sides before the decision: it is what showed
that 194 of the 227 moved starts sat inside the record's closing group, i.e. what the unguarded chooser emitted
for them was the record's own bytes. The emitter now **takes** the rule and reports the same starts
(`inventory/class_defaults_emit_classes.csv`, whose `--check` gates them against `class_defaults_per_class.csv`),
so this artifact stays as the per-class trade record rather than as a forecast.
That comment counts the gain and not the cost, and it is the whole record of a trade that decides
whether the twelve UI container panels get a file. This measures both sides over the same packages,
class by class, and writes the difference:

* **moved**  -- both locators publish a start and they disagree (the guard's is usually the group's end,
  which is where a one-tag panel block sits);
* **gained** -- only the guard publishes one (no unguarded start survived the floor);
* **lost**   -- only the unguarded chooser does, and its start is inside a closing group: that parse is
  of the record's own bytes, so "lost" is a correction, not a regression.

Every row also carries what the block is worth: the fraction of its tags the class declares, the bytes
left after it (`trailer`, 0 for a block that ends on the payload's last byte) and, for a one-tag block,
the tag itself -- `RightLockingMenuPanel`'s is `HintLB:GUILabel`.

    python3 tools/defaults-locator-guard.py DUMP/System -o inventory   # writes defaults_locator_guard.csv
    python3 tools/defaults-locator-guard.py --report
    python3 tools/defaults-locator-guard.py --check    # the artifact's own arithmetic, no dump, no writes

The guarded side is reconciled against `inventory/class_defaults_per_class.csv` on every sweep: those
are the same starts `tools/class-defaults.py` publishes, so a sweep that disagrees with them fails
rather than writing a diff nobody can trace.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/defaults_locator_guard.csv")
PER_CLASS = os.path.join(ROOT, "inventory/class_defaults_per_class.csv")
COLUMNS = ["package", "class", "kind", "origin", "from_start", "from_count", "from_declared",
           "from_covered", "from_reason", "to_start", "to_count", "to_declared", "trailer", "tag"]
KINDS = ("moved", "gained", "lost")
ORIGINS = ("candidate", "group-end", "group-tail", "no-block", "no-declared-name")

# The twelve container panels `docs/systems/UI.md` names as the classes whose block the emitter writes
# no file for, plus `GUIListBox` -- the widget they all reach through. Their blocks are one tag each.
PANELS = ("GUIListBox", "ChapterSelectionPanel", "CharacterSelectionPanel", "CoopPlayerPanel",
          "FullSlidingPanel", "GameModeSelectionPanel", "MultiplayerServerPanel", "ProfilePanel",
          "RightLockingMenuPanel", "SelectPawnPanel", "SelectTeamPanel", "XBoxLiveServerSelectionPanel")


def load_tool(name):
    path = os.path.join(HERE, name + ".py")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pick(cd, cands, decl, own):
    """The emitter's rule: `cd.choose`, then a block has to name one property the class declares.

    `tools/defaults-emit.py`'s `choose_scored` and `block_of` are this pair of lines; keeping the copy
    here is what lets one sweep run both locators over one candidate set.
    """
    best = cd.choose(cands, decl, own)
    if best is None:
        return None, "no-block"
    _score, count, start, names, bad = best
    if not any(n in decl for n in names):
        return None, "no-declared-name"
    return best, None


def block_at(mod, pkg, data, start):
    """`(props, trailer)` for the block at `start`, or `(None, None)` when it does not parse."""
    try:
        props, end, reason = mod.read_block(data[start:], pkg, False)
    except Exception:                                   # noqa: BLE001 - a short payload is a skip
        return None, None
    if reason != "None":
        return None, None
    return props, len(data) - (start + end)


def sweep(system_dir, outdir):
    cd = load_tool("class-defaults")
    mod = cd.load_prop_stream()
    supers, declared_all = cd.super_map(), cd.declared_properties()
    committed = {}
    if os.path.isfile(PER_CLASS):
        with open(PER_CLASS, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                committed[(r["package"], r["class"])] = r
    rows, origins, mismatch = [], collections.Counter(), []
    total = 0
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pname = fname[:-2]
        for exp in [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]:
            cls = pkg.name(exp["name"])
            total += 1
            decl, cur, seen = set(), cls, set()
            while cur and cur not in seen:
                seen.add(cur)
                decl |= declared_all.get(cur, set())
                cur = supers.get(cur)
            own, kinds = declared_all.get(cls, set()), cd.kinds_for_class(cls)
            cands, data = cd.candidates(pkg, mod, exp, kinds=kinds)
            before, before_reason = pick(cd, list(cands), decl, own)
            covered = 0
            if before:
                intervals = cd.group_intervals(data, len(pkg.names or []), len(pkg.imports or []))
                covered = 1 if cd.covered(intervals, before[2]) is not None else 0
            after_c, where = cd.guarded([tuple(c) for c in cands], data, pkg, decl, kinds,
                                        len(pkg.imports or []))
            after, reason = pick(cd, after_c, decl, own)
            origin = where.get(after[2], "candidate") if after else (reason or "no-block")
            origins[origin] += 1
            key = (pname, cls)
            row = committed.get(key)
            if row is not None:
                want = int(row["block_start"]) if row["block_start"] else None
                got = after[2] if after else None
                if want != got:
                    mismatch.append("%s.%s: class-defaults says %r, guarded here says %r"
                                    % (pname, cls, want, got))
            if (before and before[2]) == (after and after[2]):
                continue
            kind = "moved" if (before and after) else ("gained" if after else "lost")
            props, trailer = (block_at(mod, pkg, data, after[2]) if after else (None, None))
            tag = ""
            if props and len(props) == 1:
                p = props[0]
                tag = "%s:%s" % (p.get("name") or "?", p.get("type") or "?")
            def declared_fraction(best):
                if not best:
                    return ""
                count, names = best[1], best[3]
                return "%.2f" % (sum(1 for n in names if n in decl) / float(count)) if count else ""
            rows.append([pname, cls, kind, origin,
                         before[2] if before else "", before[1] if before else "",
                         declared_fraction(before), covered, before_reason or "",
                         after[2] if after else "", after[1] if after else "",
                         declared_fraction(after),
                         "" if trailer is None else trailer, tag])
    if mismatch:
        print("FAIL defaults-locator-guard: the guarded side disagrees with class_defaults_per_class.csv "
              "on %d class(es): %s" % (len(mismatch), "; ".join(mismatch[:4])))
        return 1
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "defaults_locator_guard.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(COLUMNS)
        w.writerows(sorted(rows))
    print("wrote %s (%d rows of %d classes)" % (path, len(rows), total))
    print("guard origin: " + ", ".join("%s %d" % (k, v) for k, v in origins.most_common()))
    return 0


def load_rows(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def summarize(rows):
    kinds = collections.Counter(r["kind"] for r in rows)
    origins = collections.Counter(r["origin"] for r in rows)
    covered = sum(1 for r in rows if r["kind"] == "moved" and r["from_covered"] == "1")
    reasons = collections.Counter(r["from_reason"] for r in rows if r["kind"] == "gained")
    ended = [r for r in rows if r["kind"] in ("moved", "gained") and r["trailer"] == "0"]
    tagged = [r for r in rows if r["kind"] in ("moved", "gained") and r["tag"]]
    panels = sorted([r for r in rows if r["class"] in PANELS and r["kind"] in ("moved", "gained")],
                    key=lambda r: r["class"])
    return dict(rows=len(rows), kinds=kinds, origins=origins, ended=len(ended), tagged=len(tagged),
                panels=panels, one_tag=[r for r in tagged], covered=covered, reasons=reasons,
                missing=[p for p in PANELS if p not in [r["class"] for r in panels]])


def report(path=ARTIFACT):
    s = summarize(load_rows(path))
    k = s["kinds"]
    print("locator guard: %d classes change (%d moved, %d gained, %d lost)" %
          (s["rows"], k.get("moved", 0), k.get("gained", 0), k.get("lost", 0)))
    print("origin of the guarded start: " +
          ", ".join("%s %d" % (a, b) for a, b in sorted(s["origins"].items())))
    print("%d of the %d published starts end on the payload's last byte; %d blocks hold one tag"
          % (s["ended"], k.get("moved", 0) + k.get("gained", 0), len(s["one_tag"])))
    print("%d of the %d moved rows had their unguarded start inside a closing group; the %d gained "
          "rows come from %s" % (s["covered"], k.get("moved", 0), k.get("gained", 0),
                                 ", ".join("%s %d" % (a or "-", b) for a, b in sorted(s["reasons"].items()))))
    if s["missing"]:
        print("panels in no diff: %s" % ", ".join(s["missing"]))
    print("panels: " + ", ".join("%s %s" % (r["class"], r["tag"] or r["kind"]) for r in s["panels"]))
    for r in s["panels"]:
        print("  %-28s %-8s %-10s %s -> %s  %s"
              % (r["class"], r["kind"], r["origin"], r["from_start"] or "-", r["to_start"], r["tag"]))


def check(path=ARTIFACT):
    """The artifact's own arithmetic: no dump, no writes.

    Every claim a row makes is checkable without the packages -- a `moved` row has a start on both
    sides and they differ, a `gained` row has none before, a `lost` row has none after, the origin is
    one of the five the chooser can report, and a one-tag block names its tag as `name:type`. The twelve
    panels have to be in the diff at all: they are the reason the page names the trade.
    """
    rows = load_rows(path)
    if not rows:
        return "FAIL defaults-locator-guard: %s is empty" % path
    problems = []
    for r in rows:
        if r["kind"] not in KINDS:
            problems.append("unknown kind %r" % r["kind"])
        if r["origin"] not in ORIGINS:
            problems.append("unknown origin %r" % r["origin"])
        if r["kind"] == "moved" and (not r["from_start"] or not r["to_start"]
                                     or r["from_start"] == r["to_start"]):
            problems.append("%s.%s is moved without two different starts" % (r["package"], r["class"]))
        if r["kind"] == "gained" and (r["from_start"] or not r["to_start"]):
            problems.append("%s.%s is gained with a start on both sides" % (r["package"], r["class"]))
        if r["kind"] == "lost" and (not r["from_start"] or r["to_start"]):
            problems.append("%s.%s is lost with a start after" % (r["package"], r["class"]))
        if r["tag"] and ":" not in r["tag"]:
            problems.append("%s.%s carries a tag %r that is not name:type"
                            % (r["package"], r["class"], r["tag"]))
        if r["kind"] in ("moved", "gained") and r["trailer"] == "":
            problems.append("%s.%s publishes a block with no trailer" % (r["package"], r["class"]))
    if not any(r["class"] in PANELS for r in rows):
        problems.append("none of the twelve panels is in the diff")
    if problems:
        print("FAIL defaults-locator-guard: " + "; ".join(problems[:6]))
        return 1
    kinds = collections.Counter(r["kind"] for r in rows)
    print("ok   defaults-locator-guard: %d rows (%d moved, %d gained, %d lost), %d panels"
          % (len(rows), kinds.get("moved", 0), kinds.get("gained", 0), kinds.get("lost", 0),
             len([r for r in rows if r["class"] in PANELS])))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.report:
        report()
        return 0
    if not args.system:
        print("a dump System directory is required (or --report/--check)")
        return 2
    if not os.path.isdir(args.system):
        print("FAIL defaults-locator-guard: %s is not a directory" % args.system)
        return 1
    return sweep(args.system, args.out)


if __name__ == "__main__":
    sys.exit(main())
