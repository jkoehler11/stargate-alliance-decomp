#!/usr/bin/env python3
"""Minimal original-Xbox executable (XBE) reader: sections, VA mapping, assert-path mining.

Usage:
    xbe.py DUMP/default.xbe sections
    xbe.py DUMP/default.xbe asserts -o inventory
    xbe.py DUMP/default.xbe xrefs 0x4C6B20

Why hand-rolled: the only thing the inventory needs before Ghidra is the section table and
VA<->file-offset mapping, and every phase-2 task depends on getting addresses right. The loader
deliberately refuses to guess: magic, header size and every section are validated, so a wrong
file or a truncated dump fails loudly instead of producing plausible-looking garbage.
"""
import argparse
import csv
import os
import re
import struct
import sys

MAGIC = b"XBEH"

# The entry point field at 0x128 is stored XOR-encrypted. Retail builds use the first key, debug builds the
# second; which one applies is decided by whether the result lands inside a section.
ENTRY_XOR_KEYS = (0xA8FC57AB, 0x94859D4B)
ASSERT_PATH = re.compile(
    rb"(?:[A-Za-z]:)?(?:\\|/)[^\x00\"\r\n]{4,200}?\.(?:cpp|hpp|cxx|h)", re.IGNORECASE
)
# Perception's build tree: ...\Game\GameXBOX\<Module>\Src\<File>.cpp  (or \Inc\<File>.h)
MODULE_PATH = re.compile(
    rb"[\\/]([A-Za-z0-9_]+)[\\/]([Ss]rc|[Ii]nc)[\\/]([A-Za-z0-9_]+\.(?:cpp|hpp|cxx|h))$"
)


class Section:
    __slots__ = ("index", "flags", "va", "vsize", "foff", "fsize", "name")

    def __init__(self, index, flags, va, vsize, foff, fsize, name):
        self.index, self.flags = index, flags
        self.va, self.vsize, self.foff, self.fsize, self.name = va, vsize, foff, fsize, name

    def contains(self, va):
        return self.va <= va < self.va + self.vsize

    @property
    def executable(self):
        return bool(self.flags & 0x4)  # XBE section flag: executable


class Xbe:
    def __init__(self, path):
        with open(path, "rb") as fh:
            self.data = fh.read()
        d = self.data
        if d[:4] != MAGIC:
            sys.exit(f"{path}: not an XBE (magic {d[:4]!r})")
        u = lambda o: struct.unpack_from("<I", d, o)[0]
        self.base = u(0x104)
        self.header_size = u(0x108)
        self.image_size = u(0x10C)
        # 0x118 is the *certificate* address, not the entry point: at that offset the file holds a
        # 0x1EC-byte certificate with the UTF-16 title "Stargate SG-1".
        self.certificate = u(0x118)
        # 0x128 is the entry point and is stored XOR-encrypted. Two keys exist: 0xA8FC57AB for a retail
        # build, 0x94859D4B for a debug build. Retail lands outside every section here; debug lands at
        # 0x0033F317, which begins a function (the bytes before it are another function's
        # `xor eax,eax; ret 4` tail) whose body is CRT start-up reading the XBE header back. Resolved
        # below, once the section table is available (docs/XBE_LAYOUT.md).
        self.entry_field = u(0x128)
        self.entry = None
        self.entry_key = None
        self.entry_candidates = [(k, self.entry_field ^ k) for k in ENTRY_XOR_KEYS]
        self.entry_xor_candidate = self.entry_field ^ ENTRY_XOR_KEYS[0]
        self.kernel_thunk = u(0x150) if len(d) > 0x150 else 0
        count = u(0x11C)
        sh_va = u(0x120)
        sh_off = self.va_to_offset(sh_va, sections=None)
        self.sections = []
        for i in range(count):
            o = sh_off + i * 0x38
            flags, va, vsize, foff, fsize, name_va = struct.unpack_from("<6I", d, o)
            name = self._cstr_at_va(name_va) if name_va else f"<section {i}>"
            self.sections.append(Section(i, flags, va, vsize, foff, fsize, name))
        for key, va in self.entry_candidates:
            if self.section_for(va) is not None:
                self.entry_key, self.entry = key, va
                break

    # -- mapping ---------------------------------------------------------
    def section_for(self, va, executable=None):
        for s in self.sections:
            if s.contains(va) and (executable is None or s.executable == executable):
                if executable is None or s.executable:
                    return s
        return None

    def va_to_offset(self, va, sections="auto"):
        if sections is None:  # header region (section table itself)
            if self.base <= va < self.base + self.header_size:
                return va - self.base
            raise KeyError(hex(va))
        s = self.section_for(va)
        if s is None:
            raise KeyError(f"VA {va:#x} is not mapped by any section")
        return s.foff + (va - s.va)

    def offset_to_va(self, off):
        for s in self.sections:
            if s.foff <= off < s.foff + min(s.fsize, s.vsize):
                return s.va + (off - s.foff)
        return None

    def _cstr_at_va(self, va, limit=64):
        # Section names live in the header region, which is mapped 1:1 from the image base and is
        # consulted while self.sections is still being built -- so try that mapping first.
        if self.base <= va < self.base + self.header_size:
            off = va - self.base
        else:
            try:
                off = self.va_to_offset(va)
            except KeyError:
                return ""
        raw = self.data[off:off + limit].split(b"\0")[0]
        return raw.decode("latin1", "replace")

    def read_va(self, va, size):
        off = self.va_to_offset(va)
        return self.data[off:off + size]

    # -- analysis --------------------------------------------------------
    def cstrings(self, min_len=6):
        """Yield (file_offset, bytes) for every printable run in the image."""
        for m in re.finditer(rb"[ -~]{%d,}" % min_len, self.data):
            yield m.start(), m.group()

    def assert_paths(self):
        """Distinct build-tree source paths embedded by __FILE__ asserts.

        Returns dict: normalised path -> {"va": first VA, "modules": set, "file": basename}.
        """
        found = {}
        for off, s in self.cstrings():
            for m in ASSERT_PATH.finditer(s):
                raw = m.group()
                norm = raw.replace(b"\\", b"/").decode("latin1")
                if "/" not in norm:
                    continue
                va = self.offset_to_va(off + m.start())
                if va is None:
                    continue
                entry = found.setdefault(norm, {"va": va, "modules": set(), "kind": "other"})
                mm = MODULE_PATH.search(raw)
                if mm:
                    entry["modules"].add(mm.group(1).decode("latin1"))
                    # Classify by extension, not directory: some headers live under Src\.
                    ext = mm.group(3).rsplit(b".", 1)[-1].lower()
                    entry["kind"] = "src" if ext in (b"cpp", b"cxx") else "inc"
        return found

    def xrefs_to(self, va, executable_only=True):
        """Code sites holding the 32-bit VA (typical `push offset string` operand)."""
        needle = struct.pack("<I", va)
        hits = []
        for s in self.sections:
            if executable_only and not s.executable:
                continue
            blob = self.data[s.foff:s.foff + min(s.fsize, s.vsize)]
            start = 0
            while True:
                i = blob.find(needle, start)
                if i < 0:
                    break
                hits.append(s.va + i)
                start = i + 1
        return hits


def cmd_sections(xbe, args):
    cands = ", ".join(f"{k:#010x}->{va:#010x}" for k, va in xbe.entry_candidates)
    entry = (f"entry={xbe.entry:#010x} (key {xbe.entry_key:#010x}, {xbe.section_for(xbe.entry).name})"
             if xbe.entry is not None else "entry=unresolved")
    print(f"base={xbe.base:#x} headers={xbe.header_size:#x} image={xbe.image_size:#x} "
          f"certificate={xbe.certificate:#x} entry_field={xbe.entry_field:#010x} [{cands}] {entry}")
    print(f"{'#':>2} {'name':<12} {'flags':>8} {'va':>10} {'vsize':>10} {'foff':>9} {'fsize':>10} ex")
    for s in xbe.sections:
        print(f"{s.index:>2} {s.name:<12} {s.flags:#08x} {s.va:#010x} {s.vsize:#010x} "
              f"{s.foff:#09x} {s.fsize:#010x} {'Y' if s.executable else '-'}")


# Assert paths appear in several spellings (absolute, relative, mixed case). Canonicalise the
# module name to the most frequent spelling so one module never shows up twice in the inventory.
CANONICAL_MODULE = {
    "engine": "Engine", "core": "Core", "ipdrv": "IpDrv", "gui": "Gui", "d3ddrv": "D3DDrv",
    "xboxdrv": "XboxDrv", "xboxaudio": "XboxAudio", "xboxlive": "XboxLive", "gameplay": "Gameplay",
    "fire": "Fire", "stargategame": "stargategame", "stargatevehicles": "stargatevehicles",
}


def canonical_module(name):
    if not name:
        return "?"
    return CANONICAL_MODULE.get(name.lower(), name)


def cmd_asserts(xbe, args):
    paths = xbe.assert_paths()
    rows = []
    for path in sorted(paths):
        info = paths[path]
        module = sorted(m for m in info["modules"] if m)
        module = canonical_module(module[0]) if module else "?"
        refs = xbe.xrefs_to(info["va"])
        rows.append({
            "module": module,
            "kind": info["kind"],
            "source_file": os.path.basename(path),
            "path": path,
            "string_va": f"{info['va']:#010x}",
            "xref_count": len(refs),
            "xref_vas": " ".join(f"{r:#010x}" for r in refs),
        })
    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "xbe_modules.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["module", "kind", "source_file", "path", "string_va",
                                           "xref_count", "xref_vas"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["module"].lower(), r["source_file"].lower())))
    cpp_path = os.path.join(args.out, "cpps.txt")
    with open(cpp_path, "w", encoding="utf-8", newline="\n") as fh:
        for r in sorted(rows, key=lambda r: (r["module"].lower(), r["source_file"].lower())):
            fh.write(f"{r['module']}/{r['source_file']}\n")

    # Per-module summary over translation units only: headers are shared and would double-count.
    per_module = {}
    for r in rows:
        d = per_module.setdefault(r["module"], {"src_files": 0, "src_refs": 0, "inc_files": 0})
        if r["kind"] == "src":
            d["src_files"] += 1
            d["src_refs"] += r["xref_count"]
        elif r["kind"] == "inc":
            d["inc_files"] += 1
    summary = os.path.join(args.out, "xbe_module_summary.csv")
    with open(summary, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["module", "src_files", "src_refs", "inc_files"])
        w.writeheader()
        for mod, d in sorted(per_module.items(), key=lambda kv: -kv[1]["src_files"]):
            w.writerow({"module": mod, **d})
    total_src = sum(d["src_files"] for d in per_module.values())
    print(f"{len(rows)} assert paths ({total_src} translation units), "
          f"{sum(r['xref_count'] for r in rows)} code references")
    for mod, d in sorted(per_module.items(), key=lambda kv: -kv[1]["src_files"]):
        print(f"  {mod:<18} {d['src_files']:>3} src files {d['src_refs']:>4} refs "
              f"{d['inc_files']:>3} headers")
    print(f"wrote {csv_path}, {summary} and {cpp_path}")


def cmd_xrefs(xbe, args):
    va = int(args.va, 0)
    print(f"{va:#x} in section {xbe.section_for(va).name if xbe.section_for(va) else '?'}")
    for hit in xbe.xrefs_to(va):
        print(f"  xref at {hit:#010x}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sections")
    p = sub.add_parser("asserts")
    p.add_argument("-o", "--out", default="inventory")
    p = sub.add_parser("xrefs")
    p.add_argument("va")
    args = ap.parse_args()
    xbe = Xbe(args.xbe)
    {"sections": cmd_sections, "asserts": cmd_asserts, "xrefs": cmd_xrefs}[args.cmd](xbe, args)


if __name__ == "__main__":
    main()
