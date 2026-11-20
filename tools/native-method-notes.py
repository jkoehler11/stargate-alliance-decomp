#!/usr/bin/env python3
"""Per-method evidence for one library class (PLAN 4.9).

Reading 144 decompiled bodies is not a deliverable anyone can check. Each library member's own
relocation list is: a `REL32` is a call whose target the library names, and a `DIR32` into `.rdata`
is a literal whose *text* the image holds. That gives, per method, its callees and its strings —
enough to describe a subsystem's structure without claiming what its logic does.

    python3 tools/native-method-notes.py --class CXnIp --libs ~/sg1/work/xdk/libs-all \\
        --xbe ~/sg1/work/dump-local/default.xbe -o inventory/cxn_ip_methods.csv

The `lib`/`object` columns say which static library and COFF member the symbol came from, so a
row's attribution is the match's own claim rather than a guess.

`--check` validates every reading's artifact without the dump -- not just one class's -- and reports
how many rows carry that attribution. `--class` reads one demangled class; with no `--class` the tool
regenerates the whole set (`LIBRARY_READINGS`).
"""

import argparse
import collections
import csv
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from cofflib import CoffError, CoffObject, parse_archive  # noqa: E402

COLUMNS = ["address", "method", "args", "size", "lib", "object", "callees",
           "callee_classes", "strings"]

# Every reading this tool has made: (demangled class, artifact stem). `--all` (the default with no
# `--class`) regenerates all of them, and `--check` with no arguments validates all of them, so an
# artifact cannot sit unvalidated because it is not the one class the tool happens to default to.
LIBRARY_READINGS = [
    ("CXnIp", "cxn_ip"), ("CXnBase", "cxn_base"), ("CXnNic", "cxn_nic"),
    ("CXnEnet", "cxn_enet"), ("CXnSock", "cxn_sock"),
    ("CXo", "cxo"), ("CXoBase", "cxo_base"),
    ("XGRAPHICS", "xgraphics"), ("D3DX", "d3_dx"), ("PreProcessor", "pre_processor"),
    ("Instruction", "instruction"),
    ("DirectSound", "dsound"), ("CDirectSound", "dsound_object"),
    ("CDirectSoundVoice", "dsound_voice"),
    ("CMcpxAPU", "mcpx_apu"), ("CMcpxCore", "mcpx_core"), ("CMcpxBuffer", "mcpx_buffer"),
    ("CMcpxStream", "mcpx_stream"), ("CMcpxVoiceClient", "mcpx_voice_client"),
    ("CXHVEngine", "xhv_engine"), ("CVoiceChatMode", "voice_chat_mode"),
    ("CLocalTalker", "local_talker"), ("CRemoteTalker", "remote_talker"),
    ("CI3DL2Listener", "i3dl2_listener"), ("CFullHRTFSource", "hrtf_source"),
    ("CWaveFile", "wave_file"), ("CQueueXMO", "queue_xmo"), ("XHawkMediaObject", "hawk_media_object"),
    ("CVoiceMailMode", "voice_mail_mode"), ("CLoopbackMode", "loopback_mode"),
    ("CBaseMode", "base_mode"), ("CInactiveMode", "inactive_mode"),
]
NAMES = os.path.join("symbols", "xbox_functions.csv")
IMAGE_REL_I386_REL32 = 0x20


def split_mangled(name):
    """('CXnIp', 'IpTerm', 'IAEXXZ') for '?IpTerm@CXnIp@@IAEXXZ', else (None, name, '')."""
    if not name.startswith("?") or "@@" not in name:
        return None, name, ""
    head, args = name[1:].split("@@", 1)
    parts = [p for p in head.split("@") if p]
    if len(parts) < 2:
        return None, parts[0] if parts else name, args
    return parts[1], parts[0], args


def class_of(sym):
    _cls, _method, _args = split_mangled(sym)
    return _cls or ""


def printable(buf):
    out = []
    for b in buf:
        if b == 0:
            break
        if 32 <= b < 127 or b in (9,):
            out.append(chr(b))
        else:
            return None
    text = "".join(out).strip()
    return text if len(text) >= 4 else None


def load_libs(lib_dir):
    table = {}
    for fn in sorted(os.listdir(lib_dir)):
        if not fn.lower().endswith(".lib"):
            continue
        lib = os.path.splitext(fn)[0]
        try:
            members = parse_archive(open(os.path.join(lib_dir, fn), "rb").read())
        except (CoffError, OSError):
            continue
        for member, blob in members:
            try:
                obj = CoffObject(member, blob)
                funcs = obj.functions_full()
            except CoffError:
                continue
            for name, _body, rels in funcs:
                table.setdefault((lib, member, name), rels)
    return table


def generate(args):
    from xbe import Xbe  # noqa: PLC0415

    xbe = Xbe(args.xbe)
    libs = load_libs(args.libs)
    rows = [r for r in csv.DictReader(open(NAMES, encoding="utf-8"))
            if split_mangled(r["name"])[0] == args.target]
    # body evidence by symbol name: the body-match artifact knows (lib, member); the call-edge names
    # do not, so index the libraries by symbol instead
    by_name = {}
    for (lib, member, name), rels in libs.items():
        by_name.setdefault(name, (rels, lib, member))
    out = []
    for r in sorted(rows, key=lambda r: int(r["address"], 16)):
        cls, method, margs = split_mangled(r["name"])
        addr, size = int(r["address"], 16), int(r["size"])
        body = xbe.read_va(addr, size)
        rels, lib, member = by_name.get(r["name"], (None, "", ""))
        callees, strings, callee_syms = [], [], []
        if body and rels:
            for off, width, sym, typ in rels:
                if off + 4 > len(body):
                    continue
                if typ & IMAGE_REL_I386_REL32:
                    disp = struct.unpack("<i", body[off:off + 4])[0]
                    va = addr + off + 4 + disp
                    callees.append((vy_to_name(sym), va))
                    callee_syms.append(sym)
                else:
                    va = struct.unpack("<I", body[off:off + 4])[0]
                    if xbe.section_for(va) is None:
                        continue
                    text = printable(xbe.read_va(va, 120) or b"")
                    if text:
                        strings.append(text)
        seen = []
        for name, _va in callees:
            if name and name not in seen:
                seen.append(name)
        out.append({"address": r["address"], "method": method, "args": margs, "size": size,
                    "lib": lib, "object": member,
                    "callees": "; ".join(seen),
                    "callee_classes": "; ".join(sorted({c for c in map(class_of, callee_syms)
                                                         if c})),
                    "strings": "; ".join(dict.fromkeys(strings))})
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    print("%s: %d methods, %d with callees, %d with strings"
          % (args.target, len(out), sum(1 for r in out if r["callees"]),
             sum(1 for r in out if r["strings"])))
    cc = collections.Counter()
    for r in out:
        for c in r["callee_classes"].split("; "):
            if c:
                cc[c] += 1
    for name, n in cc.most_common(8):
        print("  callee class %-24s in %d methods" % (name, n))
    return 0


def vy_to_name(sym):
    """Trim MSVC decoration for display: keep Class::Method when the symbol names one."""
    if not sym.startswith("?"):
        return sym.split("@")[0]
    cls, method, _args = split_mangled(sym)
    return "%s::%s" % (cls, method) if cls else method


def artifact(stem):
    return os.path.join("inventory", "%s_methods.csv" % stem)


def check_one(path):
    if not os.path.isfile(path):
        return ["%s missing -- run the generator" % path]
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != COLUMNS:
            return ["%s columns are %r" % (path, rd.fieldnames)]
        rows = list(rd)
    problems = []
    if not rows:
        problems.append("%s has no rows" % path)
    addrs = collections.Counter(r["address"].lower() for r in rows)
    problems.extend("%s: %s appears twice" % (path, a) for a, n in addrs.items() if n > 1)
    for r in rows:
        if not r["method"]:
            problems.append("%s: %s has no method name" % (path, r["address"]))
        if int(r["size"]) < 1:
            problems.append("%s: %s has size 0" % (path, r["address"]))
    return problems


def check(args):
    """Validate every reading's artifact -- or just --class's -- without the dump."""
    wanted = [(args.target, snake(args.target))] if args.target else LIBRARY_READINGS
    problems, methods, attributed = [], 0, 0
    for _cls, stem in wanted:
        path = artifact(stem)
        problems.extend(check_one(path))
        if os.path.isfile(path):
            with open(path, newline="") as fh:
                rows = list(csv.DictReader(fh))
            methods += len(rows)
            attributed += sum(1 for r in rows if r.get("lib"))
    if problems:
        print("native-method-notes: %d problem(s)" % len(problems))
        for p in problems[:10]:
            print("  " + p)
        return 1
    print("ok   native-method-notes: %d methods over %d classes, %d with library attribution"
          % (methods, len(wanted), attributed))
    return 0


def snake(target):
    """CXnIp -> cxn_ip: snake-case at the lower->upper boundary, not lowercased flat."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", target).lower()


def generate_all(args):
    if not (args.libs and args.xbe):
        raise SystemExit("missing --libs/--xbe")
    rc = 0
    for cls, stem in LIBRARY_READINGS:
        rc |= generate(argparse.Namespace(target=cls, libs=args.libs, xbe=args.xbe,
                                          out=artifact(stem)))
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--class", dest="target",
                    help="demangled class name; omit to regenerate (or --check) every reading")
    ap.add_argument("--libs")
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list-artifacts", action="store_true",
                    help="print the artifact files this tool writes, one per line (tool index can credit them)")
    args = ap.parse_args()
    if args.list_artifacts:
        print("\n".join("%s_methods.csv" % stem for _cls, stem in LIBRARY_READINGS))
        return 0
    if args.check:
        return check(args)
    if not args.target and not args.out:
        return generate_all(args)
    if not args.target:
        raise SystemExit("--out needs --class")
    if not args.out:
        args.out = artifact(snake(args.target))
    missing = [f for f in ("libs", "xbe") if not getattr(args, f)]
    if missing:
        raise SystemExit("missing --%s" % missing[0])
    return generate(args)


if __name__ == "__main__":
    sys.exit(main())