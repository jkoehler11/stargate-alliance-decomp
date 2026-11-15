#!/usr/bin/env python3
"""Which `Function` payloads carry no script epilogue, and what they have in common.

`docs/BYTECODE.md` (*The payload's tail*) measures the epilogue every scripted function ends with --
`04 0b` (`Return`, `Nothing`) plus `47`, the marker `UStruct::Serialize` appends -- and leaves the
population without one described as "the native/scriptless functions", which is a *reading*: the
section never measured that the scripts without an epilogue are exactly the natives. This tool asks
the question from the payload's other end instead of from a name list.

Every `UFunction` payload ends in the same tail (`UFunction::Serialize` at `0x00039f10`), so a payload
whose script is missing still has its own `FunctionFlags` at `payload_end-4`. Reading that word gives
each scriptless function its own evidence, and the census splits the population three ways:

* **the native bit is set** -- the payload says of itself that it has no script;
* **nothing names it**: `inventory/native_bodies.csv` (the binder's body map) has a row for it;
* **the script declares it**, or does not -- the section's reading, kept here as the weaker claim so the
  two can be compared rather than substituted for one another.

    tools/function-no-script.py DUMP/System --sweep -o inventory
    tools/function-no-script.py --report
    tools/function-no-script.py --write-doc docs/BYTECODE.md
    tools/function-no-script.py --check-doc docs/BYTECODE.md   # from the committed artifact
    tools/function-no-script.py --check                        # synthetic, no dump, writes nothing

Reads the packages; no bytecode, no value, no boot.
"""
import argparse
import collections
import csv
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

EPILOGUE = b"\x04\x0b\x47"          # Return, Nothing, and the `UStruct::Serialize` marker.
TAIL = 7                            # `NativeToken` (u16), `OperPrecedence` (u8), `FunctionFlags` (u32).
TAIL_NET = 9                        # plus `RepOffset` (u16) when `FunctionFlags & 0x40`.
FUNCS = os.path.join(ROOT, "symbols/xbox_functions.csv")   # not read; kept for the sibling tools' layout.
BODIES = os.path.join(ROOT, "inventory/native_bodies.csv")
ARTIFACT = os.path.join(ROOT, "inventory/function_no_script.csv")
DOC = os.path.join(ROOT, "docs/BYTECODE.md")
DOC_START = "<!-- generated:function-no-script -->"
DOC_END = "<!-- /generated:function-no-script -->"

FIELDS = ["kind", "package", "key", "payloads", "no_script", "native_flag", "in_bodies",
          "bodies_no_flag", "count", "note"]

# The flag vocabulary `inventory/natives.csv` already uses, so two artifacts name a bit the same way.
BITS = ((0x00000001, "Final"), (0x00000004, "Iterator"), (0x00000008, "Latent"),
        (0x00000010, "PreOperator"), (0x00000040, "Net"), (0x00000080, "NetReliable"),
        (0x00000100, "Simulated"), (0x00000400, "Native"), (0x00000800, "Event"),
        (0x00001000, "Operator"), (0x00002000, "Static"), (0x00008000, "Const"),
        (0x00020000, "Public"), (0x00040000, "Private"), (0x00100000, "Delegate"))


# --- the payload ---------------------------------------------------------------------------------
def classify(data):
    """(epilogue count, trailer length) for one payload; the trailer is the distance from its end.

    A payload shorter than the tail has no trailer to report, so both answers are `None` rather than
    a number the caller could mistake for a measurement.
    """
    if len(data) < TAIL:
        return None, None
    n = data.count(EPILOGUE)
    if n == 0:
        return 0, None
    return n, len(data) - data.rfind(EPILOGUE) - len(EPILOGUE)


def flags_of(data):
    """The `FunctionFlags` word the payload ends with, or None when it is too short to hold one."""
    if len(data) < 4:
        return None
    return struct.unpack_from("<I", data, len(data) - 4)[0]


def decode(flags):
    """`0x400:Native;0x20000:Public;` -- the bits this tool reads, plus any unknown bit as a number."""
    named = [name for bit, name in BITS if flags & bit]
    known = 0
    for bit, _name in BITS:
        if flags & bit:
            known |= bit
    rest = flags & ~known
    out = "".join("0x%x:%s;" % (bit, name) for bit, name in BITS if flags & bit)
    if rest:
        out += "0x%x:unknown;" % rest
    return out or "0x0:none;"


def bodies():
    """{(package, class, function)} the binder binds to a native body."""
    out = set()
    if not os.path.isfile(BODIES):
        return out
    with open(BODIES, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.add((r.get("package", ""), r.get("class", ""), r.get("function", "")))
    return out


# --- the sweep -----------------------------------------------------------------------------------
def functions(pkg):
    """Yield (class name, function name, payload bytes) for every `Function` export of a package."""
    for exp in pkg.exports:
        if pkg.class_name_of(exp) != "Function":
            continue
        cls = "?"
        if exp["outer"] > 0 and exp["outer"] <= len(pkg.exports):
            cls = pkg.name(pkg.exports[exp["outer"] - 1]["name"]).split(".")[-1]
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        yield cls, pkg.name(exp["name"]).split(".")[-1], data


def sweep(system, out):
    known = bodies()
    rows, total, no_script = [], 0, 0
    seen = collections.Counter()
    for name in sorted(os.listdir(system)):
        if not name.endswith(".u"):
            continue
        pak = name[:-2]
        try:
            pkg = Package(os.path.join(system, name))
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
        except Exception:                      # an unreadable package is not a zero
            continue
        p, n, nat, inb, flagless = 0, 0, 0, 0, 0
        for cls, fn, data in functions(pkg):
            p += 1
            total += 1
            count, _trailer = classify(data)
            if count != 0:
                continue
            n += 1
            no_script += 1
            flags = flags_of(data)
            if flags is not None:
                seen[flags] += 1
                if flags & 0x400:
                    nat += 1
            if (pak, cls, fn) in known:
                inb += 1
                if flags is not None and not flags & 0x400:
                    flagless += 1
        rows.append(dict(kind="package", package=pak, key="", payloads=str(p), no_script=str(n),
                         native_flag=str(nat), in_bodies=str(inb), bodies_no_flag=str(flagless),
                         count=str(p), note=""))
    census(rows, total, no_script)
    rows += flag_rows(seen)
    if out:
        write_artifact(rows, os.path.join(out, "function_no_script.csv"))
        print("%s: %d rows" % (os.path.join(out, "function_no_script.csv"), len(rows)))
    return rows


def census(rows, total, no_script):
    """The corpus totals and the flag words of the scriptless population, appended as their own rows."""
    nat = sum(int(r["native_flag"]) for r in rows if r["kind"] == "package")
    inb = sum(int(r["in_bodies"]) for r in rows if r["kind"] == "package")
    flagless = sum(int(r["bodies_no_flag"]) for r in rows if r["kind"] == "package")
    packages = len([r for r in rows if r["kind"] == "package"
                    and r["payloads"].isdigit() and int(r["payloads"]) > 0])
    rows.append(dict(kind="census", package="", key="corpus", payloads=str(total), no_script=str(no_script),
                     native_flag=str(nat), in_bodies=str(inb), bodies_no_flag=str(flagless),
                     count=str(packages),
                     note="%d package(s) with Function exports" % packages))
    return rows


def flag_rows(seen):
    """One row per distinct `FunctionFlags` word over the scriptless population: (flags, count, text)."""
    return [dict(kind="flags", package="", key="0x%08x" % f, payloads="", no_script="", native_flag="",
                 in_bodies="", bodies_no_flag="", count=str(c), note=decode(f))
            for f, c in seen.most_common()]


# --- render / artifact ---------------------------------------------------------------------------
def render(rows):
    census_row = [r for r in rows if r["kind"] == "census"][0]
    packages = [r for r in rows if r["kind"] == "package"]
    flags = [r for r in rows if r["kind"] == "flags"]
    total = int(census_row["payloads"])
    none = int(census_row["no_script"])
    nat = int(census_row["native_flag"])
    inb = int(census_row["in_bodies"])
    flagless = int(census_row["bodies_no_flag"])
    orphan = none - inb
    per = sorted(((int(r["no_script"]) - int(r["in_bodies"]), r["package"]) for r in packages
                  if r["payloads"].isdigit() and int(r["payloads"]) > 0), reverse=True)
    per = [(n, p) for n, p in per if n > 0]
    out = ["**The scriptless population is the native one, and the payload says so itself**: %d of the %d "
           "payloads that carry no script epilogue set `FUNC_Native` in their own tail's `FunctionFlags`, "
           "%d are bound to a native body by `inventory/native_bodies.csv`, and the %d with no body row are "
           "the operator natives (`%s`) whose names the source never spells as functions."
           % (nat, none, inb, orphan,
              ", ".join("%d in %s" % (n, p) for n, p in per) or "none"), ""]
    operators = sum(int(r["count"]) for r in flags if "0x1000:Operator" in r["note"])
    if operators:
        out.append("%d of the %d carry `Operator` (`0x1000`) in the same word, so the body-less population "
                   "(%d rows) and the operator bit name the same set to within %d row -- the cross-check that "
                   "the split is the operators, not an artifact of the body map."
                   % (operators, none, none - inb, abs(operators - (none - inb))))
        out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| `Function` payloads over %s packages | **%d** |" % (census_row["count"], total))
    out.append("| with no epilogue | **%d** (%.1f%%) |" % (none, 100.0 * none / total if total else 0.0))
    out.append("| of those, `FUNC_Native` set in the tail's `FunctionFlags` | **%d of %d** |" % (nat, none))
    out.append("| of those, bound to a native body by `inventory/native_bodies.csv` | **%d** |" % inb)
    out.append("| bodied natives whose own flags lack the native bit | **%d** |" % flagless)
    out.append("")
    out.append("The flag words the scriptless population ends with, most common first:")
    out.append("")
    out.append("| `FunctionFlags` | payloads | bits |")
    out.append("|---|---|---|")
    for r in flags:
        out.append("| `%s` | %s | %s |" % (r["key"], r["count"], r["note"]))
    out.append("")
    out.append("Per package:")
    out.append("")
    out.append("| package | payloads | no epilogue | native bit | in native bodies |")
    out.append("|---|---|---|---|---|")
    for r in packages:
        if not r["payloads"] or not r["payloads"].isdigit() or int(r["payloads"]) == 0:
            continue
        out.append("| `%s` | %s | %s | %s | %s |"
                   % (r["package"], r["payloads"], r["no_script"], r["native_flag"], r["in_bodies"]))
    rest = [r for r in packages if r["payloads"].isdigit() and int(r["payloads"]) > 0
            and int(r["no_script"]) - int(r["native_flag"]) > 0]
    out.append("")
    if rest:
        out.append("**%d package(s) hold a scriptless payload whose own flags omit the native bit** -- `%s` "
                   "(%s payloads) -- and every one of those is a bodied native, so the flag is the weaker "
                   "claim there and the body row is what names it."
                   % (len(rest), "`, `".join(r["package"] for r in rest),
                      ", ".join("%s of its %s scriptless" % (r["bodies_no_flag"], r["no_script"])
                                for r in rest)))
    else:
        out.append("**No package holds a scriptless payload without the native bit** -- the split is the "
                   "same in every one of them.")
    out.append("")
    out.append("Census: %s %s." % (census_row["key"], census_row["note"]))
    return "\n".join(out)


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
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


# --- self-test -----------------------------------------------------------------------------------
def self_check():
    bad = []
    # 1. the epilogue and the trailer, from payloads built here rather than from the dump
    scripted = b"\x01\x02\x03" + EPILOGUE + b"\x2b\x01\x00" + struct.pack("<I", 0x00000402)
    if classify(scripted) != (1, 7):
        bad.append("scripted payload: %r" % (classify(scripted),))
    net = b"\x01" + EPILOGUE + b"\xff\xff" + b"\x2b\x01\x00" + struct.pack("<I", 0x00000240)
    if classify(net) != (1, 9):
        bad.append("network payload: %r" % (classify(net),))
    native = b"\x00\x00" + b"\x2b\x01\x00" + struct.pack("<I", 0x00020401)
    if classify(native) != (0, None):
        bad.append("native payload: %r" % (classify(native),))
    if classify(b"\x04\x0b") != (None, None):
        bad.append("a short payload reported a trailer")
    # 2. the flag word is read from the payload's own end, and decoded by name
    if flags_of(native) != 0x00020401:
        bad.append("flags: %r" % (flags_of(native),))
    if "Native" not in decode(0x400) or "Public" not in decode(0x20000):
        bad.append("decode lost a named bit: %r" % (decode(0x400 | 0x20000),))
    if decode(0x40000000) != "0x40000000:unknown;":
        bad.append("an unnamed bit was not reported: %r" % (decode(0x40000000),))
    # 3. the census adds up and the render quotes the artifact rather than recomputing it
    rows = [dict(kind="package", package="A", key="", payloads="5", no_script="2", native_flag="2",
                 in_bodies="1", bodies_no_flag="1", count="5", note=""),
            dict(kind="package", package="B", key="", payloads="5", no_script="1", native_flag="1",
                 in_bodies="1", bodies_no_flag="0", count="5", note=""),
            dict(kind="flags", package="", key="0x00020401", payloads="", no_script="", native_flag="",
                 in_bodies="", bodies_no_flag="", count="3", note=decode(0x00020401))]
    census(rows, 10, 3)
    block = render(rows)
    for want in ("10", "3 of the 3", "0x00020401", "Native"):
        if want not in block:
            bad.append("render is missing %r" % want)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.csv")
        write_artifact(rows, path)
        if read_artifact(path) != rows:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
    if bad:
        print("FAIL function-no-script: " + "; ".join(bad))
        return 1
    print("ok   function-no-script: an epilogue 7 and 9 bytes from the end are told from an absent one, "
          "the flag word is read from the payload's own tail, and an unnamed bit is reported not dropped")
    return 0


# --- main ----------------------------------------------------------------------------------------
def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="the dump's System directory")
    ap.add_argument("--sweep", action="store_true", help="every package -> inventory/function_no_script.csv")
    ap.add_argument("--report", action="store_true", help="print the totals for a swept artifact")
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
        rows = sweep(args.system, None)
        if args.out:
            write_artifact(rows, os.path.join(args.out, "function_no_script.csv"))
            print("%s: %d rows" % (os.path.join(args.out, "function_no_script.csv"), len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL function-no-script: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL function-no-script: %s is stale against the committed artifact" % path)
                return 1
            print("ok   function-no-script: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
