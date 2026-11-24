#!/usr/bin/env python3
"""Pad a body to a target length with `Nothing;` statements.

The package rule is that a payload's size may not change (docs/L1_READINESS.md), so a body compiled from
edited source has to land *inside* its shipped payload. Widening a constant's encoding (tools/uscript-fit.py)
does that at coarse granularity; a `Nothing` statement is one byte and has no effect, so it fills any gap
exactly.

The insertion point is computed from the grammar, not guessed: the statements go immediately before the
top-level `Return`, where every preceding statement is complete. A body with no top-level `Return` is refused
rather than padded in a place that might split an expression.

    python3 tools/uscript-pad.py --body 040b47 --target 11      # 8 x Nothing + the shipped body
    python3 tools/uscript-pad.py --check
"""

import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NOTHING = 0x0B
RETURN = 0x04


def load_dis():
    spec = importlib.util.spec_from_file_location("sd", os.path.join(HERE, "uscript-dis.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sd = load_dis()
TABLE = sd.Table()


def walk(blob):
    """Return the top-level nodes, or raise with the reason the stream does not fit."""
    return sd.parse(blob, TABLE)


def pad(body, target):
    """Insert Nothing statements before the top-level Return until the body is `target` bytes."""
    if target < len(body):
        return None, "the body is already %d bytes, longer than the target %d (that is uscript-fit's job)" % (
            len(body), target)
    gap = target - len(body)
    if gap == 0:
        return bytes(body), None
    try:
        nodes = walk(body)
    except sd.Refused as why:
        return None, "the body does not walk: %s" % why
    returns = [n for n in nodes if n["opcode"] == RETURN]
    if not returns:
        return None, "no top-level Return, so there is no place to pad that cannot split an expression"
    at = returns[-1]["offset"]
    padded = bytes(body[:at]) + bytes([NOTHING]) * gap + bytes(body[at:])
    try:
        after = walk(padded)
    except sd.Refused as why:
        return None, "the padded body does not walk: %s" % why
    got = sum(n["end"] - n["offset"] for n in after)
    if got != len(padded) or len(padded) != target:
        return None, "the padded body is %d bytes walking %d, expected %d" % (len(padded), got, target)
    return padded, None


def check():
    """Cases that matter: an exact fill, the identity, a refusal, and a body with nothing to pad before."""
    cases = [
        # shipped shape -> 6 bytes = three Nothing statements before the Return
        (bytes.fromhex("040b47"), 6, bytes.fromhex("0b0b0b040b47")),
        # a full payload capacity: LogDropped's shipped payload holds 11 script bytes
        (bytes.fromhex("040b47"), 11, bytes.fromhex("0b" * 8 + "040b47")),
        # identity
        (bytes.fromhex("040b47"), 3, bytes.fromhex("040b47")),
    ]
    for body, target, want in cases:
        got, why = pad(body, target)
        if got is None:
            print("FAIL uscript-pad: %s -> %d refused: %s" % (body.hex(), target, why))
            return 1
        if got != want:
            print("FAIL uscript-pad: %s -> %d gave %s, expected %s" % (body.hex(), target, got.hex(), want.hex()))
            return 1
    # a shrink must be refused, not silently truncated
    got, why = pad(bytes.fromhex("0b0b0b040b47"), 3)
    if got is not None:
        print("FAIL uscript-pad: a shrink to 3 was accepted")
        return 1
    # a body that does not walk must be refused
    got, why = pad(bytes.fromhex("ffffffff"), 8)
    if got is not None:
        print("FAIL uscript-pad: garbage was padded")
        return 1
    print("ok uscript-pad: 3 paddings, 2 refusals")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--body", help="the body, hex")
    ap.add_argument("--target", type=int, help="the length to pad to")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.body or args.target is None:
        print("FAIL uscript-pad: --body and --target are required")
        return 1
    body = bytes.fromhex(args.body.replace(" ", ""))
    got, why = pad(body, args.target)
    if got is None:
        print("FAIL uscript-pad: %s" % why)
        return 1
    print(got.hex())
    print("   %d byte(s) -> %d, %d Nothing statement(s)" % (len(body), len(got), args.target - len(body)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
