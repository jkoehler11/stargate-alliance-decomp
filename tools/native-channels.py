#!/usr/bin/env python3
"""Which channel registers each index-less `native`, for all 523 of them (U7's last residue).

`tools/gnatives-name-stubs.py` found a `push &body; push -1; call 0x46c60` stub for **467 of the 523** index-less
declarations. The other 56 were printed as a count and left there, which read like a hole in the registration
file. It is not: the build has three registration channels, and each of the 56 uses one of them or belongs to a
package the image does not carry at all.

The three channels, all measured already and joined here per declaration:

* **stub** -- `inventory/gnatives_name_stubs.csv`: a `push &body; push -1` call into the registrar, which returns
  without storing for `-1` (`docs/NATIVE_VM.md`).
* **record** -- `inventory/native_targets.csv`: the `{pool name, body}` records in `.data`, which is the table the
  `int%sexec%s` name lookup binds from (U1).
* **store** -- `inventory/gnatives_records.csv`: the class's live slots filled by `mov [slot], reg`
  (`tools/gnatives-writes.py`).

    native-channels.py --xbe DUMP/default.xbe -o inventory    # the artifact
    native-channels.py --check                                # dump-free, in `make ci`

What it measures, and what it corrected: **41 of the 56 are `Object`**, and every one of `Object`'s **230** live
slots is store-registered -- the class is not in the stub set at all (0 indexed stubs and 1 `-1` stub among its 202
declarations), so its index-less half having no stub is the same fact, not a second one. Those 41 carry a
`{name, body}` record and a binder literal, so they register by record like the 113 storeless slots of the runtime
dump. The remaining **15** are `BrushBuilder` (13, package `Editor`), `MaterialFactory` (1, `Editor`) and
`UPreviewSkinnedButton` (1, `UPreview`), and the image contains **no** occurrence of those class names, their
functions, or any `int<prefix><Class>exec<Function>` literal for them: `Editor` and `UPreview` are the only two
packages with 0 of their declarations bound by name, and neither is in the Xbox build's runtime packages
(`Default.ini` lists them under `EditPackages` only). Nothing to register, so nothing registers.

**A binder column that invented a literal.** `tools/native-reconcile.py`'s `load_binder` filled an empty binder
with `"int%s%s" % (prefix_expected, function)` -- a name the image does not carry -- and then read its own
fabrication as evidence that the image binds the function. That is how two `UPreview` records came to be listed as
`text-missing` in `inventory/native_residual.csv` instead of `declaration-unbound`: `intUSetRenderInfo` and
`intUCreated` occur 0 times in the image. The rule here is the same one that tool's own self-test states and its
fabrication defeated: an empty binder is *no binder*, and "no binder" is a verdict, not a gap to fill.
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

COLUMNS = ["package", "class", "function", "binder", "binder_va", "stub", "record_va", "record_body",
           "channel", "class_store_slots", "class_stub_slots", "in_image"]
# What a declaration's registration amounts to. `stub-only` would be a body pushed with no record to name it by,
# and is reported rather than folded into `unbound`, because it is a different defect.
CHANNELS = ("stub+record", "record-only", "stub-only", "unbound")


def read_csv(name, columns=None):
    path = os.path.join(ROOT, "inventory", name)
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        missing = [c for c in (columns or []) if c not in (r.fieldnames or [])]
        if missing:
            raise SystemExit("native-channels: %s has no %s column" % (name, ", ".join(missing)))
        return list(r)


def binder_module():
    """`tools/native-binder.py`: the one definition of the `int<prefix><Class>exec<Function>` pool."""
    path = os.path.join(ROOT, "tools", "native-binder.py")
    spec = importlib.util.spec_from_file_location("native_binder", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def class_of(binder):
    """`intUObjectexecGetEnum` -> `Object`; the pool's own spelling of the class, not the CSV's."""
    if not binder or "exec" not in binder:
        return ""
    # `[4:]` drops `int` and the one prefix character: `intAActor` -> `Actor`, `intUUIXInteraction` ->
    # `UIXInteraction`. A bare `lstrip("AU")` eats both of those whole.
    return binder.split("exec")[0][4:]


def indexless(natives):
    return [d for d in natives if (d.get("native_token") or "").strip() in ("", "0", "0x0")]


def channel(stub, record):
    """The verdict for one declaration, from the two channels that can carry it.

    Deliberately takes presence flags rather than a binder string: a binder the image does not carry is not a
    channel, which is the distinction `load_binder`'s fabricated literal used to blur.
    """
    if stub and record:
        return "stub+record"
    if stub:
        return "stub-only"
    if record:
        return "record-only"
    return "unbound"


def tables():
    """Every committed table this join needs, read once."""
    natives = read_csv("natives.csv", ["package", "class", "function", "native_token"])
    binder = {(r["class"], r["function"]): r for r in read_csv(
        "native_binder_names.csv", ["class", "function", "binder", "binder_va"])}
    stub = {r["name"]: r for r in read_csv("gnatives_name_stubs.csv", ["stub", "name"])
            if r.get("name")}
    record = {r["name"]: r for r in read_csv("native_targets.csv", ["name", "record_va", "body"])}
    slots = read_csv("gnatives_records.csv", ["slot", "record_name", "source"])
    return natives, binder, stub, record, slots


def class_counts(slots):
    """{class: [store-registered slots, record-registered slots]} from the runtime dump's join."""
    out = {}
    for r in slots:
        cls = class_of(r.get("record_name") or "")
        row = out.setdefault(cls, [0, 0])
        if r.get("source") == "store":
            row[0] += 1
        elif r.get("source") == "record":
            row[1] += 1
    return out


def stub_counts(stub, indexed):
    """{class: stubs} over both kinds of registration stub, keyed by the class in the stub's own name."""
    out = {}
    for name in list(stub) + [r.get("name", "") for r in indexed]:
        cls = class_of(name)
        if cls:
            out[cls] = out.get(cls, 0) + 1
    return out


def bounded(text, word):
    """Whether `word` occurs in `text` as a whole identifier (the pool packs names back to back)."""
    if not word:
        return False
    return re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(word), text) is not None


def rows_for(natives, binder, stub, record, slots, text=None):
    """One row per index-less declaration, in `natives.csv` order."""
    cc = class_counts(slots)
    sc = stub_counts(stub, read_csv("gnatives_stubs.csv", ["name"]))
    rows = []
    for d in indexless(natives):
        b = binder.get((d["class"], d["function"]), {})
        name = (b.get("binder") or "").strip()
        s = stub.get(name, {}) if name else {}
        rec = record.get(name, {}) if name else {}
        # The image is the authority for "this name exists"; a CSV row with no binder is no binder.
        if text is None:
            in_image = "-"
        else:
            in_image = "yes" if (name and name in text) or (not name and bounded(text, d["class"])) else "no"
        rows.append({"package": d["package"], "class": d["class"], "function": d["function"],
                     "binder": name, "binder_va": b.get("binder_va", "") if name else "",
                     "stub": s.get("stub", ""), "record_va": rec.get("record_va", ""),
                     "record_body": rec.get("body", ""),
                     "channel": channel(bool(s), bool(rec)),
                     "class_store_slots": cc.get(d["class"], [0, 0])[0],
                     "class_stub_slots": sc.get(d["class"], 0),
                     "in_image": in_image})
    return rows


def run(xbe_path, out_dir):
    if not os.path.isdir(out_dir):
        raise SystemExit("native-channels: no such directory %s" % out_dir)
    natives, binder, stub, record, slots = tables()
    nb = binder_module()
    text = nb.wide_text(open(xbe_path, "rb").read())
    rows = rows_for(natives, binder, stub, record, slots, text)

    path = os.path.join(out_dir, "native_channels.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    by = {}
    for r in rows:
        by[r["channel"]] = by.get(r["channel"], 0) + 1
    print("%d index-less declarations -> %s" % (len(rows), path))
    for c in CHANNELS:
        if by.get(c):
            print("  %-12s %d" % (c, by[c]))
    for c in ("record-only", "stub-only", "unbound"):
        sub = [r for r in rows if r["channel"] == c]
        if sub:
            print("  %s by class: %s" % (c, ", ".join("%s %d" % kv for kv in
                                                      collections.Counter(r["class"] for r in sub).most_common())))
    # The class split, measured per class rather than asserted: a class that registers its live slots by store
    # ships almost no stubs, and that is the whole explanation for the record-only half.
    for cls in sorted({r["class"] for r in rows if r["channel"] in ("record-only", "unbound")}):
        sub = [r for r in rows if r["class"] == cls]
        print("  %-22s %3d index-less, %2d with a -1 stub, %3d store-registered slots  %s" % (
            cls, len(sub), sum(1 for r in sub if r["stub"]), sub[0]["class_store_slots"],
            ", ".join("%s %d" % kv for kv in collections.Counter(r["channel"] for r in sub).most_common())))
    # The unbound half: packages the image carries no name for.
    unbound = [r for r in rows if r["channel"] == "unbound"]
    if unbound:
        pkgs = sorted({r["package"] for r in unbound})
        print("  unbound packages: %s -- binder names image-wide for %s" % (
            ", ".join(pkgs),
            ", ".join("%s %d" % (p, sum(1 for r in binder.values()
                                        if r["package"] == p and (r.get("binder") or "").strip()))
                      for p in pkgs)))
    print("  the image carries the binder literal for %d of %d named rows"
          % (sum(1 for r in rows if r["in_image"] == "yes"), sum(1 for r in rows if r["binder"])))
    return 0


def check():
    """Synthetic: the verdict rule, the class counts, the pool rule. No dump, no writes."""
    # The verdict is presence of the two channels, and an empty binder is neither.
    assert channel(True, True) == "stub+record"
    assert channel(False, True) == "record-only"
    assert channel(True, False) == "stub-only"
    assert channel(False, False) == "unbound"
    assert channel("", "") == "unbound", "an empty address must not read as a channel"
    # `class_of` reads the class out of the pool's own spelling, both prefixes.
    assert class_of("intUObjectexecGetEnum") == "Object"
    assert class_of("intAActorexecKWake") == "Actor"
    # The literal `load_binder` used to invent has no `exec` in it, so it is not even shaped like a binder name.
    assert class_of("intUSetRenderInfo") == "" and class_of("") == ""
    # A class's slots are counted by the class its record names, and store/record stay apart.
    slots = [{"slot": "0", "record_name": "intUObjectexecLocalVariable", "source": "store"},
             {"slot": "1", "record_name": "intUObjectexecGetEnum", "source": "store"},
             {"slot": "2", "record_name": "intAShadowManagerexecGetShadowLights", "source": "record"},
             {"slot": "3", "record_name": "", "source": "none"}]
    cc = class_counts(slots)
    assert cc["Object"] == [2, 0] and cc["ShadowManager"] == [0, 1] and cc[""] == [0, 0], cc
    # The pool rule: a whole identifier matches, a fragment inside a longer name does not.
    assert bounded("\x00BrushBuilder\x00", "BrushBuilder") is True
    assert bounded("intUObjectexecGetEnum", "Object") is False
    assert bounded("intUPreviewSkinnedButtonexecSetRenderInfo", "UPreviewSkinnedButton") is False
    # And the join itself, on two declarations that differ only in which channel carries them.
    natives = [{"package": "Core", "class": "Object", "function": "GetEnum", "native_token": "0"},
               {"package": "Editor", "class": "BrushBuilder", "function": "EndBrush", "native_token": ""},
               {"package": "Engine", "class": "Actor", "function": "Touch", "native_token": "7"}]
    binder = {("Object", "GetEnum"): {"class": "Object", "function": "GetEnum",
                                      "binder": "intUObjectexecGetEnum", "binder_va": "0x4d5700"},
              ("BrushBuilder", "EndBrush"): {"class": "BrushBuilder", "function": "EndBrush",
                                             "binder": "", "binder_va": ""}}
    stub = {"intUObjectexecGetEnum": {"stub": "0x413db0", "name": "intUObjectexecGetEnum"}}
    record = {"intUObjectexecGetEnum": {"name": "intUObjectexecGetEnum", "record_va": "0x5c2380",
                                       "body": "0x4dcf0"}}
    rows = rows_for(natives, binder, stub, record, slots, text="\x00intUObjectexecGetEnum\x00")
    assert len(rows) == 2, "only the index-less declarations are rows (Actor.Touch has an index)"
    assert rows[0]["channel"] == "stub+record" and rows[0]["in_image"] == "yes", rows[0]
    assert rows[1]["channel"] == "unbound" and rows[1]["binder"] == "" and rows[1]["in_image"] == "no", rows[1]
    print("ok native-channels: %d channels, class slot split, pool rule, 2-of-3 rows" % len(CHANNELS))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", help="the dump's default.xbe")
    ap.add_argument("-o", "--out", default="inventory", help="directory to write native_channels.csv into")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump and no write")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.xbe:
        ap.error("--xbe is required unless --check")
    return run(args.xbe, args.out)


if __name__ == "__main__":
    sys.exit(main())
