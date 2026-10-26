#!/usr/bin/env python3
"""Read guest physical memory out of a running xemu over its GDB stub (PLAN 7.2).

The boot smoke test (`tools/xemu/smoke.py`) can only assert on the CPU: this build of xemu has no `screendump`,
the serial log stays empty, and the human monitor's `pmemsave`/`memsave` are unusable (`invalid char 't' in
expression`). The GDB stub is a second channel, and it works — but only after one non-obvious step.

**Every read fails with `E14` until the stub is switched to physical-memory mode.** Without
`Qqemu.PhyMemMode:1` the stub answers GDB's `m` requests against the *virtual* address space of whatever context
the CPU happens to be in, so a read of physical `0x100000` during boot returns `E14` (error), which reads exactly
like a broken stub. With the packet, `m` reads physical RAM and returns data.

    # one region to a file (no game data in the repository: write under $WORK)
    python3 tools/xemu/mem.py --port 12345 --dump 0x1000:0x10000 -o $WORK/boot.bin

    # what is in RAM, without printing it
    python3 tools/xemu/mem.py --port 12345 --scan --show

The tool stops the guest on connect (a consistent read needs a stopped CPU) and resumes it with `--resume`.
Throughput measured on the desktop: 512 KiB in 1.7 s in 16 KiB chunks (`PacketSize=0x20020`), so a 128 MiB dump is
about seven minutes — dump the regions you need, not the whole machine.
"""
import argparse
import re
import socket
import struct
import sys
import time


class Gdb:
    """The minimal RSP client a memory dump needs: interrupt, physical mode, read, resume."""

    def __init__(self, host, port, timeout=10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""

    def _recv_exact(self, n):
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise IOError("stub closed the connection")
            out += chunk
        return out

    def _ack(self):
        self.sock.sendall(b"+")

    def packet(self, payload, retry=True):
        data = b"$" + payload + b"#" + ("%02x" % (sum(payload) & 0xFF)).encode()
        self.sock.sendall(data)
        deadline = time.time() + 5
        while time.time() < deadline:
            c = self._recv_exact(1)
            if c == b"+":
                continue
            if c == b"-":
                if retry:
                    return self.packet(payload, retry=False)
                raise IOError("stub rejected packet %r" % payload[:20])
            if c == b"$":
                body = b""
                while True:
                    ch = self._recv_exact(1)
                    if ch == b"#":
                        break
                    body += ch
                self._recv_exact(2)
                self._ack()
                return body
        raise IOError("no reply to %r" % payload[:20])

    def interrupt(self):
        self.sock.sendall(b"\x03")
        time.sleep(0.3)
        try:
            self.packet(b"?")
        except IOError:
            pass

    def phys_mode(self):
        return self.packet(b"Qqemu.PhyMemMode:1") == b"OK"

    def read(self, addr, size, chunk=0x4000):
        out = bytearray()
        while len(out) < size:
            want = min(chunk, size - len(out))
            reply = self.packet(b"m%x,%x" % (addr + len(out), want))
            if not reply or reply.startswith(b"E"):
                raise IOError("read %#x+%#x failed: %r" % (addr + len(out), want, reply[:8]))
            if len(reply) % 2:
                raise IOError("odd hex length from stub")
            out += bytes.fromhex(reply.decode())
        return bytes(out)

    def resume(self):
        self.sock.sendall(b"$c#63")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


ASCII_RUN = re.compile(rb"[\x20-\x7e]{6,}")
UTF16_RUN = re.compile(rb"(?:[\x20-\x7e]\x00){6,}")


def strings_in(blob):
    """[(offset, kind, text)] for printable ASCII and UTF-16LE runs of at least six characters."""
    out = []
    for m in ASCII_RUN.finditer(blob):
        out.append((m.start(), "ascii", m.group().decode("ascii")))
    for m in UTF16_RUN.finditer(blob):
        out.append((m.start(), "utf16", m.group().decode("utf-16-le")))
    return out


def find_text(blob, needle):
    """Offsets where `needle` occurs as ASCII or UTF-16LE: the assertion a boot test needs."""
    out = []
    for encoding in ("ascii", "utf-16-le"):
        try:
            pat = needle.encode(encoding)
        except UnicodeEncodeError:
            continue
        start = blob.find(pat)
        while start >= 0:
            out.append(start)
            start = blob.find(pat, start + 1)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=12345, help="xemu's -gdb tcp::PORT")
    ap.add_argument("--dump", metavar="ADDR:SIZE", help="region to write out (e.g. 0x1000:0x10000)")
    ap.add_argument("-o", "--out", help="file for --dump (use $WORK)")
    ap.add_argument("--scan", action="store_true", help="list the strings in RAM, counts by default")
    ap.add_argument("--find", metavar="TEXT", action="append", default=[],
                    help="report where TEXT appears in the region (match only: no text is printed)")
    ap.add_argument("--region", metavar="ADDR:SIZE", default="0x0:0x8000000", help="region for --scan")
    ap.add_argument("--show", action="store_true", help="print the strings themselves (guest data)")
    ap.add_argument("--limit", type=int, default=20, help="how many strings to print with --show")
    ap.add_argument("--resume", action="store_true", help="let the guest run again before disconnecting")
    ap.add_argument("--chunk", type=int, default=0x4000)
    args = ap.parse_args()

    if not args.dump and not args.scan and not args.find:
        sys.exit("nothing to do: give --dump ADDR:SIZE or --scan")

    gdb = Gdb(args.host, args.port)
    try:
        gdb.interrupt()
        if not gdb.phys_mode():
            sys.exit("the stub refused Qqemu.PhyMemMode:1 — reads would come back E14")
        if args.dump:
            addr, size = (int(x, 0) for x in args.dump.split(":"))
            blob = gdb.read(addr, size, args.chunk)
            target = args.out or "-"
            if target == "-":
                sys.stdout.buffer.write(blob)
            else:
                with open(target, "wb") as fh:
                    fh.write(blob)
                print("wrote %d bytes from %#x to %s" % (len(blob), addr, target))
            print("region %#x..%#x: %d bytes, %d string runs"
                  % (addr, addr + size, len(blob), len(strings_in(blob))))
        if args.find and not args.scan:
            addr, size = (int(x, 0) for x in args.region.split(":"))
            blob = gdb.read(addr, size, args.chunk)
            print("scanned %#x..%#x: %d bytes" % (addr, addr + size, len(blob)))
            args.scan = False
            for needle in args.find:
                hits = find_text(blob, needle)
                print("   %-24r %d hit(s)%s" % (needle, len(hits),
                                                "" if not hits else
                                                ", first at " + ", ".join("%#010x" % (addr + h) for h in hits[:4])))
            return 0
        if args.scan:
            addr, size = (int(x, 0) for x in args.region.split(":"))
            t0 = time.time()
            blob = gdb.read(addr, size, args.chunk)
            elapsed = time.time() - t0
            found = strings_in(blob)
            kinds = {}
            for _off, kind, _text in found:
                kinds[kind] = kinds.get(kind, 0) + 1
            print("scanned %#x..%#x: %d bytes in %.1fs (%d KiB/s), %d ascii + %d utf16 strings"
                  % (addr, addr + size, len(blob), elapsed, len(blob) / 1024.0 / max(elapsed, 1e-6),
                     kinds.get("ascii", 0), kinds.get("utf16", 0)))
            for needle in args.find:
                hits = find_text(blob, needle)
                print("   %-24r %d hit(s)%s" % (needle, len(hits),
                                                "" if not hits else
                                                ", first at " + ", ".join("%#010x" % (addr + h) for h in hits[:4])))
            if args.show:
                for off, kind, text in found[:args.limit]:
                    print("   %#010x %-5s %s" % (addr + off, kind, text))
            else:
                print("   (pass --show to print them: they are guest data)")
    finally:
        if args.resume:
            gdb.resume()
        gdb.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
