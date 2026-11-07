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

    # who reads one address: a read watchpoint, and the register file at every stop
    python3 tools/xemu/mem.py --port 12345 --watch 0x6823b78:2 --stops 3

The tool stops the guest on connect (a consistent read needs a stopped CPU) and resumes it with `--resume`.
Throughput measured on the desktop: 512 KiB in 1.7 s in 16 KiB chunks (`PacketSize=0x20020`), so a 128 MiB dump is
about seven minutes — dump the regions you need, not the whole machine.

`--watch` is the third question: a byte found in a whole-RAM dump with nothing pointing at it (PLAN 7.3's typed
console line) is read by *some* instruction, and x86 debug registers catch that read. The stub stops, the watchpoint
is armed with `Z3` (a *read* watchpoint), the guest is resumed, and each stop is reported with the register file,
the stack words around `ESP` that fall inside a window of the address, and `EIP` — the instruction that did the
read. A stub that does not implement read watchpoints answers an empty packet instead of `OK`; that is reported as
a negative, not as a failure of the guest.
"""
import argparse
import json
import re
import socket
import struct
import sys
import threading
import time

REGISTERS = ("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi",
             "eip", "eflags", "cs", "ss", "ds", "es", "fs", "gs")


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

    def phys_mode(self, enable=True):
        return self.packet(b"Qqemu.PhyMemMode:%d" % (1 if enable else 0)) == b"OK"

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

    def wait_stop(self, timeout=20.0):
        """Read packets until a stop reply (`T`/`S`) arrives; acknowledgement bytes are skipped and sent."""
        deadline = time.time() + timeout
        self.sock.settimeout(max(0.5, timeout))
        try:
            while time.time() < deadline:
                byte = self._recv_exact(1)
                if byte == b"+":
                    continue
                if byte != b"$":
                    continue
                body = b""
                while True:
                    ch = self._recv_exact(1)
                    if ch == b"#":
                        break
                    body += ch
                self._recv_exact(2)
                self._ack()
                if body[:1] in (b"T", b"S", b"W", b"X"):
                    return body
        except (socket.timeout, TimeoutError):
            pass
        finally:
            self.sock.settimeout(10.0)
        raise IOError("no stop reply within %.1fs" % timeout)

    def stop(self, timeout=5.0):
        """Interrupt the guest and wait for the stop reply (an empty reply if the stub will not say)."""
        self.sock.sendall(b"\x03")
        try:
            return self.wait_stop(timeout)
        except (IOError, OSError):
            reply = self.packet(b"?")
            if reply and not reply.startswith(b"E"):
                return reply
            raise IOError("the stub did not report a stop after an interrupt")

    def registers(self):
        """The i386 register file, from the `g` packet's documented 16 x 4-byte little-endian layout."""
        reply = self.packet(b"g")
        if not reply or reply.startswith(b"E"):
            raise IOError("g -> %r" % reply[:8])
        raw = bytes.fromhex(reply.decode())
        out = {}
        for index, name in enumerate(REGISTERS):
            if 4 * (index + 1) <= len(raw):
                out[name] = struct.unpack_from("<I", raw, 4 * index)[0]
        if len(out) < 9:
            raise IOError("g returned %d registers, not the i386 layout" % len(out))
        return out

    def watch(self, addr, length=2, kind="read"):
        """Arm a watchpoint: `Z2` write, `Z3` read, `Z4` access. Empty reply = the stub does not implement it."""
        code = {"write": "Z2", "read": "Z3", "access": "Z4"}[kind]
        return self.packet(("%s,%x,%x" % (code, addr, length)).encode())

    def unwatch(self, addr, length=2, kind="read"):
        code = {"write": "z2", "read": "z3", "access": "z4"}[kind]
        return self.packet(("%s,%x,%x" % (code, addr, length)).encode())

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


def watchpoint(gdb, addr, window=0x200, stack=0x400, stops=3, length=2, timeout=20.0):
    """Arm a read watchpoint on ADDR and report who reads it.

    Returns (registers at the request, supported, the Z3 reply, records). A record holds the stop's register file,
    the stop packet, the stack bytes around ESP, and every register or stack word inside `window` of the address --
    the last is what names the owner when the buffer has no pointer anywhere in RAM.
    """
    try:
        gdb.stop(3.0)
    except (IOError, OSError):
        pass
    start = gdb.registers()
    # The stack is addressed the way the guest addresses it, so reads have to be virtual: if an earlier client left
    # the stub in physical mode, `ESP + n` would be read from the wrong bytes.
    try:
        gdb.phys_mode(False)
    except (IOError, OSError):
        pass
    reply = gdb.watch(addr, length, "read")
    records = []
    if reply != b"OK":
        return start, False, reply, records
    try:
        for _ in range(stops):
            gdb.resume()
            stop = gdb.wait_stop(timeout)
            regs = gdb.registers()
            names = [("register %s" % name, value) for name, value in regs.items()
                     if name != "eip" and abs(value - addr) <= window]
            blob, base = b"", 0
            esp = regs.get("esp")
            if esp and stack:
                base = max(0, esp - 0x40)
                try:
                    blob = gdb.read(base, stack)
                except (IOError, OSError):
                    blob, base = b"", 0
                for off in range(0, max(0, len(blob) - 3), 4):
                    value = struct.unpack_from("<I", blob, off)[0]
                    if abs(value - addr) <= window:
                        names.append(("stack %#010x" % (base + off), value))
            records.append({"regs": regs, "stop": stop.decode("latin-1"), "names": names,
                            "stack": blob, "stack_base": base})
    finally:
        try:
            gdb.unwatch(addr, length, "read")
        except (IOError, OSError):
            pass
    return start, True, reply, records


def format_report(addr, start, supported, reply, records, window=0x200):
    """The lines a watchpoint run prints: the register file, then each stop with what names the address."""
    lines = []
    near = [(name, value) for name, value in start.items() if abs(value - addr) <= window]
    lines.append("registers at the stop request: eip=%#010x esp=%#010x ebp=%#010x, %d within %#x of %#010x"
                 % (start.get("eip", 0), start.get("esp", 0), start.get("ebp", 0), len(near), window, addr))
    for name, value in near:
        lines.append("   %s = %#010x" % (name, value))
    if not supported:
        lines.append("the stub answered %r to Z3,%#x,%d: no read watchpoint here" % (reply, addr, 2))
        return lines
    lines.append("read watchpoint Z3,%#x,2 accepted; %d stop(s)" % (addr, len(records)))
    for index, record in enumerate(records, 1):
        regs = record["regs"]
        lines.append("stop %d: eip=%#010x esp=%#010x ebp=%#010x esi=%#010x edi=%#010x"
                     % (index, regs.get("eip", 0), regs.get("esp", 0), regs.get("ebp", 0),
                        regs.get("esi", 0), regs.get("edi", 0)))
        if record["names"]:
            for where, value in record["names"]:
                lines.append("   %s = %#010x" % (where, value))
        else:
            lines.append("   no register or stack word within %#x names it at this stop" % window)
    return lines


class FakeStub:
    """A stand-in GDB stub: enough of RSP to test `--watch` without an emulator or a dump.

    It answers the packets the watchpoint path sends -- an interrupt (0x03), `?`, `g`, `Z3`, `c`, `m`, `z3` --
    and puts the target address in a stack slot at `ESP + 0x1c`, which is what a real stop looks like when the
    reader kept the pointer in a local. It is deliberately not a general RSP server.
    """

    def __init__(self, target):
        self.target = target
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.armed = False
        self.running = True
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def registers(self):
        regs = dict((name, 0) for name in REGISTERS)
        regs.update({"eax": 0x11111111, "ecx": 0x22222222, "edx": 0x33333333, "ebx": 0x44444444,
                     "esp": 0x2000, "ebp": 0x2100, "esi": 0x55555555, "edi": 0x66666666,
                     "eip": 0x00123456, "eflags": 0x200})
        return b"".join(struct.pack("<I", regs[name]) for name in REGISTERS)

    def stop_packet(self):
        return b"T05thread:1;"

    def send_packet(self, conn, body):
        conn.sendall(b"$" + body + b"#" + ("%02x" % (sum(body) & 0xFF)).encode())

    def memory(self, addr, size):
        out = bytearray(size)
        off = 0x201c - addr
        if 0 <= off <= size - 4:
            struct.pack_into("<I", out, off, self.target)
        return bytes(out)

    def serve(self):
        try:
            self._serve()
        except (OSError, ValueError):
            pass

    def _serve(self):
        conn, _ = self.sock.accept()
        conn.settimeout(5.0)
        buf = b""
        try:
            while self.running:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while True:
                    while buf.startswith(b"+"):
                        buf = buf[1:]
                    if buf.startswith(b"\x03"):
                        buf = buf[1:]
                        self.send_packet(conn, self.stop_packet())
                        continue
                    if not buf.startswith(b"$") or b"#" not in buf:
                        break
                    end = buf.index(b"#")
                    body = buf[1:end]
                    buf = buf[end + 3:]
                    if self.answer(conn, body) is False:
                        return
        finally:
            conn.close()

    def answer(self, conn, body):
        conn.sendall(b"+")
        if body == b"?":
            self.send_packet(conn, self.stop_packet())
        elif body == b"g":
            self.send_packet(conn, self.registers().hex().encode())
        elif body.startswith(b"Z3,"):
            self.armed = True
            self.send_packet(conn, b"OK")
        elif body.startswith(b"z3,"):
            self.armed = False
            self.send_packet(conn, b"OK")
        elif body.startswith(b"m"):
            addr, _, size = body[1:].partition(b",")
            self.send_packet(conn, self.memory(int(addr, 16), int(size, 16)).hex().encode())
        elif body == b"c":
            self.send_packet(conn, self.stop_packet())
        else:
            self.send_packet(conn, b"")
        return True

    def close(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


def selftest():
    """`make ci` cannot have an emulator or a dump: prove the watchpoint path on the fake stub instead."""
    target = 0x06823b78
    stub = FakeStub(target)
    stub.thread.start()
    gdb = Gdb("127.0.0.1", stub.port)
    try:
        start, supported, reply, records = watchpoint(gdb, target, window=0x200, stack=0x100, stops=2, timeout=5.0)
    finally:
        gdb.resume()
        gdb.close()
        stub.close()
    assert supported, "the fake stub accepted Z3: %r" % reply
    assert start["esp"] == 0x2000, "the register file decodes: %#x" % start["esp"]
    assert len(records) == 2, "%d stops" % len(records)
    for record in records:
        assert record["regs"]["eip"] == 0x00123456, hex(record["regs"]["eip"])
        named = [value for where, value in record["names"] if where.startswith("stack")]
        assert named == [target], "the stack slot names the address: %r" % record["names"]
    text = "\n".join(format_report(target, start, supported, reply, records))
    assert "stop 2:" in text and "0x6823b78" in text, text
    print("selftest: the register file decodes, Z3 is accepted, and a stack slot names the watched address")


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
    ap.add_argument("--watch", metavar="ADDR[:LEN]", help="arm a read watchpoint and report who reads it")
    ap.add_argument("--stops", type=int, default=3, help="how many watchpoint stops to take")
    ap.add_argument("--stack", type=lambda v: int(v, 0), default=0x400, help="stack bytes to read around ESP")
    ap.add_argument("--window", type=lambda v: int(v, 0), default=0x200,
                    help="how close a register or stack word has to be to the address to count as naming it")
    ap.add_argument("--timeout", type=float, default=20.0, help="seconds to wait for one watchpoint stop")
    ap.add_argument("--json", help="write the stops as JSON (addresses only: it is guest state)")
    ap.add_argument("--selftest", action="store_true", help="prove the watchpoint path on a fake stub, no guest")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return 0

    if args.watch:
        target, _, size = args.watch.partition(":")
        addr, length = int(target, 0), int(size, 0) if size else 2
        gdb = Gdb(args.host, args.port)
        try:
            start, supported, reply, records = watchpoint(
                gdb, addr, window=args.window, stack=args.stack, stops=args.stops, length=length,
                timeout=args.timeout)
            for line in format_report(addr, start, supported, reply, records, args.window):
                print(line)
            if args.json:
                with open(args.json, "w", encoding="utf-8") as handle:
                    json.dump({"address": addr, "length": length, "supported": supported,
                               "reply": reply.decode("latin-1"), "start": start,
                               "stops": [{"regs": record["regs"], "names": record["names"],
                                          "stack_base": record["stack_base"],
                                          "stack": record["stack"].hex()} for record in records]}, handle)
                print("wrote %s" % args.json)
        finally:
            try:
                gdb.resume()
            except (IOError, OSError):
                pass
            gdb.close()
        return 0 if supported else 3

    if not args.dump and not args.scan and not args.find:
        sys.exit("nothing to do: give --dump ADDR:SIZE, --scan or --watch ADDR[:LEN]")

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
