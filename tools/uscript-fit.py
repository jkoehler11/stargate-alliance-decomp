#!/usr/bin/env python3
"""Fit a constant-return body to a target length by choosing the constant's encoding.

Why this exists: a body may not change the package's length on disk. Growing one payload pushes every later payload to a
new offset, and the guest page-faults on that (`CR2=0xfff0007b`, kernel idle) while this project's reader accepts the file
— `docs/L1_READINESS.md` has the six-build matrix. So a recompiled body has to come out at its shipped length, and the
cheapest honest way to arrange that is the encoding the compiler itself varies: the same integer can be written four ways.

    IntZero      `04 25`            2 bytes   value 0
    IntOne       `04 26`            2 bytes   value 1
    IntConstByte `04 2c XX`         3 bytes   0..255
    IntConst     `04 1d XX XX XX XX` 6 bytes  any int32

A value that needs fewer bytes than the shipped body had can be widened to fill; a value that needs more cannot be
shrunk, and the tool refuses rather than inventing a no-op this bytecode does not have.

    python3 tools/uscript-fit.py --check
    python3 tools/uscript-fit.py --body 0425040b47 --target 6
"""

import argparse
import struct
import sys

# The epilogue every walked body in this build ends with (Return Nothing; EndOfScript).
EPILOGUE = bytes.fromhex("040b47")
RETURN = 0x04


def parse_constant_return(body):
    """Return (value, is_float) if `body` is `04 <const> 04 0b 47`, else None."""

    if len(body) < 5 or body[0] != RETURN or body[-3:] != EPILOGUE:
        return None
    token = body[1]
    if token == 0x25:
        return (0, False) if len(body) == 5 else None
    if token == 0x26:
        return (1, False) if len(body) == 5 else None
    if token == 0x2C:
        return (body[2], False) if len(body) == 6 else None
    if token == 0x1D:
        return (struct.unpack("<i", body[2:6])[0], False) if len(body) == 9 else None
    if token == 0x1E:
        return (struct.unpack("<f", body[2:6])[0], True) if len(body) == 9 else None
    return None


def encodings(value, is_float=False):
    """Every byte sequence that returns `value`, shortest first."""

    out = []
    if not is_float:
        if value == 0:
            out.append(bytes([RETURN, 0x25]))
        if value == 1:
            out.append(bytes([RETURN, 0x26]))
        if 0 <= value <= 255:
            out.append(bytes([RETURN, 0x2C, value]))
        if -(2 ** 31) <= value < 2 ** 31:
            out.append(bytes([RETURN, 0x1D]) + struct.pack("<i", value))
    else:
        out.append(bytes([RETURN, 0x1E]) + struct.pack("<f", value))
    return out


def fit(body, target):
    """Return (fitted_body, note) or (None, reason)."""

    parsed = parse_constant_return(body)
    if parsed is None:
        return None, "not a constant-return body (only that family can be re-encoded)"
    value, is_float = parsed
    options = [e + EPILOGUE for e in encodings(value, is_float)]
    exact = [o for o in options if len(o) == target]
    if exact:
        return exact[0], "value %r at %d bytes" % (value, target)
    widths = sorted({len(o) for o in options})
    if all(w < target for w in widths):
        return None, ("value %r cannot reach %d bytes: its widest encoding is %d "
                      "(this bytecode has no no-op to pad with)" % (value, target, max(widths)))
    return None, "value %r cannot be written in %d bytes (available: %s)" % (value, target, widths)


def check(_args):
    cases = [
        ("042cff040b47", 6, "042cff040b47"),      # 255 already at 6
        ("0425040b47", 6, "042c00040b47"),        # 0 widened to IntConstByte
        ("0425040b47", 9, "041d00000000040b47"),  # 0 widened to IntConst (04 1d + four zero bytes)
        ("0426040b47", 6, "042c01040b47"),        # 1 widened to IntConstByte
        ("042cff040b47", 9, "041dff000000040b47"),  # 255 widened to IntConst
    ]
    for src, target, want in cases:
        got, note = fit(bytes.fromhex(src), target)
        if got is None or got.hex() != want:
            print("FAIL uscript-fit: %s -> %d gave %s (%s), expected %s"
                  % (src, target, got.hex() if got else None, note, want))
            return 1
    # the refusals are part of the contract: a value that needs more bytes than the target cannot be fitted
    for src, target in (("041d90010000040b47", 6), ("042cff040b47", 5)):
        got, note = fit(bytes.fromhex(src), target)
        if got is not None:
            print("FAIL uscript-fit: %s -> %d should refuse, gave %s" % (src, target, got.hex()))
            return 1
    print("ok uscript-fit: 5 fittings, 2 refusals")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--body", help="the body to fit, as hex")
    ap.add_argument("--target", type=int, help="the length it must come out at")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not (args.body and args.target):
        print("FAIL uscript-fit: --body and --target are required")
        return 2
    got, note = fit(bytes.fromhex(args.body), args.target)
    if got is None:
        print("FAIL uscript-fit: %s" % note)
        return 1
    print(got.hex())
    print("   %s" % note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
