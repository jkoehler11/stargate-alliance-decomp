"""Minimal MS COFF .lib (ar archive) reader: sections, symbols, relocations, function bodies.

No external deps. Used to build a byte-exact index of a static library so its
functions can be matched against a linked image that has no symbol table.

COFF details that matter here:
  * a .lib is an `ar` archive with two extra linker members: "/" (first) and
    "/<digits>" (long names are indexed into the "//" member);
  * an object's symbol table follows the section data; names longer than 8
    bytes live in the string table that follows the symbols;
  * a relocation names (section, offset, type) and the operand width -- that
    offset is the *only* part of the body a linker rewrites, which is exactly
    what a masked compare has to wildcard.
"""

import hashlib
import struct

AR_MAGIC = b"!<arch>\n"

# IMAGE_REL_I386_* -> operand width in bytes. 0 means "no fixup".
REL_WIDTH = {
    0x0000: 0,  # ABSOLUTE
    0x0001: 2,  # DIR16
    0x0002: 2,  # REL16
    0x0006: 4,  # DIR32
    0x0007: 4,  # DIR32NB
    0x0009: 2,  # SEG12
    0x000A: 2,  # SECTION
    0x000B: 4,  # SECREL
    0x000C: 4,  # TOKEN
    0x000D: 4,  # SECREL7
    0x0014: 4,  # REL32
}

SCN_CNT_CODE = 0x00000020
SYM_CLASS_EXTERNAL = 2
SYM_CLASS_STATIC = 3
SYM_TYPE_FUNCTION = 0x20  # DT_FCN in the low nibble


class CoffError(Exception):
    pass


def parse_archive(data):
    """Yield (member_name, member_bytes) for every COFF object in a .lib."""
    if data[:8] != AR_MAGIC:
        raise CoffError("not an ar archive")
    off = 8
    longnames = b""
    members = []
    while off + 60 <= len(data):
        hdr = data[off:off + 60]
        if hdr[58:60] != b"`\n":
            raise CoffError("bad member header at %d" % off)
        name = hdr[0:16].decode("latin-1").rstrip()
        size = int(hdr[48:58].decode("latin-1").strip() or 0)
        body = data[off + 60:off + 60 + size]
        off += 60 + size + (size & 1)
        if name in ("/", "/SYM64/"):
            continue  # first/second linker member: symbol index, not code
        if name == "//":
            longnames = body
            continue
        if name.startswith("/") and name[1:].isdigit():
            # long name: /<offset> into the // member. MSVC terminates these with NUL, GNU ar with "/\n";
            # cut at whichever comes first, or the whole table is returned as one name.
            start = int(name[1:])
            ends = [e for e in (longnames.find(b"\x00", start), longnames.find(b"/\n", start)) if e >= 0]
            end = min(ends) if ends else len(longnames)
            real = longnames[start:end].decode("latin-1")
        else:
            real = name[:-1] if name.endswith("/") else name
        members.append((real, body))
    return members


def _cstr(buf, off):
    end = buf.find(b"\x00", off)
    return buf[off:end if end >= 0 else len(buf)].decode("latin-1", "replace")


class CoffObject:
    def __init__(self, name, data):
        self.name = name
        self.data = data
        if len(data) < 20:
            raise CoffError("short COFF header")
        (self.machine, self.nsec, _ts, self.ptr_sym, self.nsym,
         self.opt_size, self.characteristics) = struct.unpack("<HHIIIHH", data[:20])
        if self.machine not in (0x014C, 0x8664):
            raise CoffError("machine %#x is not i386" % self.machine)
        self.sections = []
        base = 20 + self.opt_size
        for i in range(self.nsec):
            sh = data[base + 40 * i:base + 40 * (i + 1)]
            if len(sh) < 40:
                break
            nm = sh[0:8].rstrip(b"\x00").decode("latin-1")
            raw_size, raw_ptr, rel_ptr, _ln_ptr, nrel, _nln, chars = struct.unpack(
                "<IIIIHHI", sh[16:40])
            self.sections.append({
                "index": i + 1, "name": nm, "size": raw_size,
                "raw": data[raw_ptr:raw_ptr + raw_size] if raw_ptr else b"",
                "rel_ptr": rel_ptr, "nrel": nrel, "chars": chars,
                "relocs": [],
            })
        self.symbols = []
        self._read_symbols()

    def _read_symbols(self):
        if not self.ptr_sym or not self.nsym:
            return
        symtab = self.data[self.ptr_sym:self.ptr_sym + 18 * self.nsym]
        strtab_off = self.ptr_sym + 18 * self.nsym
        strtab = self.data[strtab_off:] if strtab_off < len(self.data) else b""
        i = 0
        while i < self.nsym:
            rec = symtab[18 * i:18 * (i + 1)]
            if len(rec) < 18:
                break
            raw_name = rec[0:8]
            if raw_name[:4] == b"\x00\x00\x00\x00":
                off = struct.unpack("<I", raw_name[4:8])[0]
                name = _cstr(strtab, off) if off < len(strtab) else ""
            else:
                name = raw_name.rstrip(b"\x00").decode("latin-1", "replace")
            value, secno, typ, sclass, naux = struct.unpack("<IhHBB", rec[8:18])
            self.symbols.append({
                "name": name, "value": value, "section": secno,
                "type": typ, "class": sclass,
            })
            i += 1 + naux

        for sec in self.sections:
            if not sec["rel_ptr"] or not sec["nrel"]:
                continue
            raw = self.data[sec["rel_ptr"]:sec["rel_ptr"] + 10 * sec["nrel"]]
            for k in range(sec["nrel"]):
                va, _symidx, rtype = struct.unpack("<IIH", raw[10 * k:10 * k + 10])
                width = REL_WIDTH.get(rtype, 0)
                if width:
                    sec["relocs"].append((va, width))
            sec["relocs"].sort()

    def functions_full(self):
        """[(name, body, [(off, width, sym_name, sym_type)])] with resolved reloc symbols."""
        names = {}
        # symbol index -> name, for relocation targets
        idx = 0
        raw_syms = []
        if self.ptr_sym and self.nsym:
            symtab = self.data[self.ptr_sym:self.ptr_sym + 18 * self.nsym]
            strtab_off = self.ptr_sym + 18 * self.nsym
            strtab = self.data[strtab_off:] if strtab_off < len(self.data) else b""
            i = 0
            while i < self.nsym:
                rec = symtab[18 * i:18 * (i + 1)]
                if len(rec) < 18:
                    break
                rn = rec[0:8]
                if rn[:4] == b"\x00\x00\x00\x00":
                    off = struct.unpack("<I", rn[4:8])[0]
                    nm = _cstr(strtab, off) if off < len(strtab) else ""
                else:
                    nm = rn.rstrip(b"\x00").decode("latin-1", "replace")
                val, secno, typ, sclass, naux = struct.unpack("<IhHBB", rec[8:18])
                names[i] = (nm, typ, secno, val)
                i += 1 + naux
        out = []
        for sec in self.sections:
            if not (sec["chars"] & SCN_CNT_CODE) or not sec["raw"]:
                continue
            funcs = [s for s in self.symbols
                     if s["section"] == sec["index"]
                     and (s["type"] & SYM_TYPE_FUNCTION)
                     and s["class"] in (SYM_CLASS_EXTERNAL, SYM_CLASS_STATIC)]
            if not funcs:
                continue
            funcs.sort(key=lambda s: s["value"])
            starts, seen = [], set()
            for s in funcs:
                if s["value"] in seen:
                    continue
                seen.add(s["value"])
                starts.append(s)
            # reloc offsets with their symbol index and type
            rel_by_off = []
            if sec["rel_ptr"] and sec["nrel"]:
                raw = self.data[sec["rel_ptr"]:sec["rel_ptr"] + 10 * sec["nrel"]]
                for k in range(sec["nrel"]):
                    va, symidx, rtype = struct.unpack("<IIH", raw[10 * k:10 * k + 10])
                    rel_by_off.append((va, rtype, symidx))
            for j, sym in enumerate(starts):
                start = sym["value"]
                end = starts[j + 1]["value"] if j + 1 < len(starts) else sec["size"]
                end = min(end, sec["size"])
                if end <= start:
                    continue
                body = sec["raw"][start:end]
                rels = []
                for va, rtype, symidx in rel_by_off:
                    if not (start <= va < end):
                        continue
                    w = REL_WIDTH.get(rtype, 0)
                    nm, typ, secno, val = names.get(symidx, ("?", 0, 0, 0))
                    rels.append((va - start, w, nm, typ))
                out.append((sym["name"], body, rels))
        return out

    def functions(self):
        """[(name, body_bytes, mask_offsets)] -- mask offsets are absolute in body."""
        out = []
        for sec in self.sections:
            if not (sec["chars"] & SCN_CNT_CODE) or not sec["raw"]:
                continue
            funcs = [s for s in self.symbols
                     if s["section"] == sec["index"]
                     and (s["type"] & SYM_TYPE_FUNCTION)
                     and s["class"] in (SYM_CLASS_EXTERNAL, SYM_CLASS_STATIC)]
            if not funcs:
                continue
            funcs.sort(key=lambda s: s["value"])
            # de-duplicate aliases at the same offset (keep the first name)
            starts = []
            seen = set()
            for s in funcs:
                if s["value"] in seen:
                    continue
                seen.add(s["value"])
                starts.append(s)
            for idx, sym in enumerate(starts):
                start = sym["value"]
                end = starts[idx + 1]["value"] if idx + 1 < len(starts) else sec["size"]
                end = min(end, sec["size"])
                if end <= start:
                    continue
                body = sec["raw"][start:end]
                masks = [(va - start, w) for (va, w) in sec["relocs"]
                         if start <= va < end and va - start + w <= len(body)]
                out.append((sym["name"], body, masks))
        return out


def masked(body, masks):
    """Body with every relocation operand zeroed -- the only linker-invariant form."""
    b = bytearray(body)
    for off, width in masks:
        for k in range(off, min(off + width, len(b))):
            b[k] = 0
    return bytes(b)


PAD = (0xCC, 0x90, 0x00)


def trim_padding(body):
    end = len(body)
    while end > 0 and body[end - 1] in PAD:
        end -= 1
    # keep at least the last 4 bytes so a body of pure padding cannot vanish
    return body[:end] if end >= 4 else body


def signature(body, masks):
    """(length, sha1 of masked bytes) -- the index key."""
    m = masked(body, masks)
    return (len(m), hashlib.sha1(m).hexdigest())
