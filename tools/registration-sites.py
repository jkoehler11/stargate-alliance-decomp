#!/usr/bin/env python3
"""Every call to the class constructor, and the class literal each one pushes.

`FUN_0003af60` is the engine's `Class` constructor: `tools/ucpp-registrations.py` names the generated
`ucpp` stubs that call it from the *exported* bodies of a module, and `tools/class-table-names.py`
resolves a class table per registration site from the constructor's `C7 /0` store. Both start from a
different end, so neither says how many call sites there are.

This reads the call sites themselves out of `.text`: every `E8 rel32` whose target is the constructor
(**493** of them), and for each site the `68 imm32` pushes in the 96 bytes before it, decoded as a wide
or ASCII literal. Three names come out of that window on a normal site -- `"new"`, the outer package
`"System"`, and the class -- so the class is the literal that `inventory/classes.csv` knows as a class,
and the rest are reported rather than guessed.

The class-table artifact is the check, not the input: `inventory/xbe_class_table_names.csv` already
names 466 sites, and the scan must reproduce every one of them or it says so and exits nonzero -- except a
site whose row is marked `ancestor-derived`, where the artifact names the table for the group's nearest
common ancestor rather than for the literal this site pushes (one site, `0x002e8468`, which pushes
`FireTexture` for `FractalTexture`'s table). What it adds is the residue -- the sites that table leaves
unnamed, because its evidence is a store inside the constructor rather than the literal at the call.

Usage:
    registration-sites.py DUMP/default.xbe -o inventory/registration_sites.csv
    registration-sites.py --check          # synthetic, no dump
"""
import argparse
import csv
import os
import re
import struct
import sys

CTOR = 0x0003af60
BACK = 96          # bytes before the call scanned for pushes; the three pushes sit within ~48
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PACKAGES = ("System", "Engine", "Core", "Editor", "UPreview", "UnrealEd", "IpDrv", "IpDrv",
            "XboxLive", "XboxAudio", "XboxDrv", "D3DDrv", "Gui", "Gameplay", "Fire",
            "stargategame", "stargatevehicles")
# The constructor's own site pushes a keyword literal beside the class and the outer package.
EXCLUDE = set(PACKAGES) | {"new"}


def decode_literal(raw, least=3):
    """A literal at a VA: UTF-16LE when the high bytes are zero up to the terminator, else ASCII."""
    out = []
    for i in range(0, len(raw) - 1, 2):
        if raw[i] == 0 and raw[i + 1] == 0:
            break
        if raw[i + 1] != 0 or not 32 <= raw[i] < 127:
            out = None
            break
        out.append(chr(raw[i]))
    if out and len(out) >= least:
        return "".join(out)
    out = []
    for b in raw:
        if b == 0:
            break
        if not 32 <= b < 127:
            return None
        out.append(chr(b))
    return "".join(out) if len(out) >= least else None


def reader(image):
    return getattr(image, "read", None) or image.read_va


def literal_at(image, va):
    try:
        return decode_literal(reader(image)(va, 64))
    except Exception:
        return None


def call_sites(image, text_va, text_size):
    data = reader(image)(text_va, text_size)
    out = []
    for i in range(len(data) - 4):
        if data[i] != 0xE8:
            continue
        rel = struct.unpack_from("<i", data, i + 1)[0]
        if text_va + i + 5 + rel == CTOR:
            out.append(text_va + i)
    return out


def literals_before(image, addr, back=BACK):
    """Every `push imm32` in the window whose immediate points at a string."""
    try:
        raw = reader(image)(addr - back, back)
    except Exception:
        return []
    out = []
    for j in range(len(raw) - 4):
        if raw[j] != 0x68:
            continue
        va = struct.unpack_from("<I", raw, j + 1)[0]
        text = literal_at(image, va)
        if text:
            out.append((addr - back + j, va, text))
    return out


def pick(names, classes):
    """(class, outer, how) from a site's literals: a known class name wins, then a lone identifier."""
    found = [t for _, _, t in names]
    outer = next((t for t in found if t in PACKAGES), "")
    known = [t for t in found if t in classes]
    if known:
        return known[0], outer, "class name in the site's pushes"
    idents = [t for t in found if IDENT.match(t) and t not in EXCLUDE]
    if len(idents) == 1:
        return idents[0], outer, "the site's only identifier literal (not in classes.csv)"
    return "", outer, ""


def run(xbe_path, out):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import xbe
    image = xbe.Xbe(xbe_path)
    codes = [s for s in image.sections if s.executable]
    sites = []
    for section in codes:
        sites.extend(call_sites(image, section.va, section.vsize))
    sites.sort()

    classes = {r["class"] for r in csv.DictReader(open("inventory/classes.csv", newline=""))}
    table, derived = {}, set()
    if os.path.exists("inventory/xbe_class_table_names.csv"):
        for r in csv.DictReader(open("inventory/xbe_class_table_names.csv", newline="")):
            if r.get("registration_va"):
                site_va = int(r["registration_va"], 16)
                table[site_va] = r["class"]
                if r.get("name_rule") == "ancestor-derived":
                    derived.add(site_va)

    rows, named_from_table, derived_ok, missed, unknown = [], 0, 0, [], []
    for site in sites:
        names = literals_before(image, site)
        cls, outer, how = pick(names, classes)
        expected = table.get(site, "")
        if expected:
            if any(t == expected for _, _, t in names):
                named_from_table += 1
                how = "class table agrees"
            elif site in derived:
                derived_ok += 1
                how = "class table names the group's nearest common ancestor, not this literal"
            else:
                missed.append((site, expected, [t for _, _, t in names]))
        elif cls:
            unknown.append((site, cls, outer, how))
        rows.append({"site_va": f"0x{site:08x}", "class": cls or expected, "outer": outer,
                     "class_table": expected, "evidence": how,
                     "literals": "|".join(t for _, _, t in names)})

    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["site_va", "class", "outer", "class_table", "evidence",
                                           "literals"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(sites)} call sites of 0x{CTOR:08x} -> {out}")
    print(f"  {named_from_table} confirmed against inventory/xbe_class_table_names.csv")
    print(f"  {len(table)} sites carry a class there, {derived_ok} name their group's nearest common "
          f"ancestor, {len(missed)} disagree with the literal")
    for site, expected, found in missed:
        print(f"    CONFLICT 0x{site:08x} table={expected} literals={found}")
    print(f"  {len(unknown)} sites the class table leaves unnamed, "
          f"{sum(1 for r in rows if r['class'] and not r['class_table'])} now named from the literal")
    for site, cls, outer, how in unknown:
        print(f"    NEW 0x{site:08x} {cls} ({outer or 'no outer'}) -- {how}")
    return 1 if missed else 0


class Code:
    """A stand-in image for `--check`: code bytes at one VA, literals at others."""

    def __init__(self, code_va, code, blobs):
        self.code_va, self.code, self.blobs = code_va, code, blobs

    def read(self, va, n=200):
        if self.code_va <= va < self.code_va + len(self.code):
            off = va - self.code_va
            return self.code[off:off + n]
        for base, body in self.blobs.items():
            if base <= va < base + len(body):
                return body[va - base:va - base + n]
        raise KeyError(hex(va))


def check():
    wide = lambda s: s.encode("utf-16-le") + b"\x00\x00"
    start = 0x0f00                                  # padding first: the scan reaches 96 bytes back
    code = bytearray(b"\x90" * 0xc0)
    code += b"\x68" + struct.pack("<I", 0x5000)      # push L"AlkeshBomber"
    code += b"\x68" + struct.pack("<I", 0x5100)      # push L"System"
    site = start + len(code)
    code += b"\xe8" + struct.pack("<i", CTOR - (site + 5))
    image = Code(start, bytes(code), {
        0x5000: wide("AlkeshBomber") + b"\x00" * 4,
        0x5100: wide("System") + b"\x00" * 8,
        0x5200: b"Mesh\x00",
    })
    found = call_sites(image, start, len(code))
    assert found == [site], found
    names = literals_before(image, site)
    assert [t for _, _, t in names] == ["AlkeshBomber", "System"], names
    assert pick(names, {"AlkeshBomber"})[0] == "AlkeshBomber"
    alone = pick(names, set())                    # one identifier, unknown to classes.csv
    assert alone[0] == "AlkeshBomber" and "not in classes.csv" in alone[2], alone
    assert decode_literal(wide("Teltak")) == "Teltak"
    assert decode_literal(b"Mesh\x00") == "Mesh"
    assert decode_literal(b"\x01\x02\x03") is None
    assert literal_at(image, 0x5200) == "Mesh"
    print("ok")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("-o", "--out", default="inventory/registration_sites.csv")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.xbe:
        ap.error("xbe is required unless --check")
    return run(args.xbe, args.out)


if __name__ == "__main__":
    sys.exit(main())
