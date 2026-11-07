#!/usr/bin/env python3
"""The console's command surface: every `exec function` the shipped scripts declare, reconciled with the binary.

PLAN 7.3 asks what the console can actually run. `docs/XEMU.md` had a table of probes (`stat fps` drew a counter,
`stat game`, `stat physics`, `showdebug`, `behindview 1` and `walk` left 0 px) and read a zero as "the command never
entered the console". That inference needs the command *list* first: an UnrealScript `exec function` is what
`ConsoleCommand` can find, so a name the scripts do not declare can only be engine-side, and a name declared on
`CheatManager` needs cheats instantiated to exist at all.

    python3 tools/pkg-script-text.py <pkg.u> --all --write $WORK/UnrealScript   # extract first, once
    python3 tools/console-commands.py --src $WORK/UnrealScript -o inventory    # then measure

Two sources, deliberately kept apart in the artifact:

- **text** — the `ScriptText` every class export carries (2,319 classes, 6.1 MB). An `exec function` declaration is
  the author's claim that the name is a console command; `simulated`/`final`/`static` modifiers and a `state`
  wrapper are recorded because both change who can answer.
- **binary** — `inventory/function_flags.csv` carries `FunctionFlags` per `Function` payload, and `Exec` (0x00000400)
  is set by the compiler for exactly those declarations. It is an independent measurement of the same population,
  which is why the join is worth carrying: a row in one source and not the other is a fact about the build, not
  noise. **`function_flags.csv`'s `class` column names a *state* for a state-scoped function** (`Engine/Dead`,
  `Engine/GameEnded`), so a binary row joins to a declaration inside that state, not to a class of that name.

The artifact is `inventory/console_commands.csv`, one row per (package, class, state, command), with a `sources`
column: `text`, `binary`, or `both`. It answers "is this a command and where does it live", and nothing about
arguments or the engine's own handlers (`stat`, `open`, … are native, not script).
"""
import argparse
import csv
import os
import re
import sys

COLUMNS = ["package", "class", "state", "command", "args", "modifiers", "sources"]
EXEC_RE = re.compile(r"^[ \t]*((?:[A-Za-z_]\w*\s+)*?)exec\s+function\s+(\w+)\s*\(([^)]*)\)", re.I)
STATE_RE = re.compile(r"^[ \t]*(?:auto\s+|simulated\s+|final\s+)*state\s+(\w+)", re.I)
CLASS_RE = re.compile(r"^[ \t]*class\s+(\w+)", re.I)


def parse_tree(src):
    """(package, class, state, command, args, modifiers) for every `exec function` in the tree."""
    out = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames.sort()
        for name in sorted(filenames):
            if not name.endswith(".uc"):
                continue
            path = os.path.join(dirpath, name)
            package = os.path.relpath(dirpath, src).split(os.sep)[0]
            cls = name[:-3]
            state = ""
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.rstrip("\r\n")
                    if state and line[:1] == "}":
                        state = ""          # a state block closes on a column-0 brace (UnrealScript convention)
                    m = STATE_RE.match(line)
                    if m:
                        state = m.group(1)
                        continue
                    if line.lstrip().startswith("//"):
                        continue
                    m = EXEC_RE.match(line)
                    if not m:
                        continue
                    modifiers = sorted({t.lower() for t in m.group(1).split()
                                        if t.lower() in ("simulated", "final", "static", "native")})
                    out.append((package, cls, state, m.group(2), " ".join(m.group(3).split()),
                                ";".join(modifiers)))
    return out


def check_shape(path):
    """Dump-free `--check`: the committed artifact's shape, order and key uniqueness."""
    if not os.path.exists(path):
        print("console-commands --check: %s is missing" % path)
        return 2
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0] != COLUMNS:
        print("console-commands --check: %s has columns %s, expected %s" % (path, rows[0] if rows else [], COLUMNS))
        return 2
    body = rows[1:]
    keys = [(r[0], r[1], r[2], r[3]) for r in body]   # the artifact is sorted by the row itself
    problems = []
    if keys != sorted(keys):
        problems.append("rows are not in sorted order")
    if len(set(keys)) != len(keys):
        problems.append("a (package, class, state, command) key repeats")
    for r in body:
        if len(r) != len(COLUMNS):
            problems.append("a row has %d fields" % len(r))
            break
        if r[6] not in ("text", "binary", "both"):
            problems.append("%s has sources=%r" % (r[3], r[6]))
            break
        if not r[1] or not r[3]:
            problems.append("a row has an empty class or command")
            break
    if problems:
        print("console-commands --check: %s: %s" % (path, "; ".join(problems)))
        return 2
    print("console-commands --check: %s, %d rows, shape ok (the declaration count needs --src)"
          % (os.path.basename(path), len(body)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", help="extracted UnrealScript tree (tools/pkg-script-text.py --all --write)")
    ap.add_argument("--flags", default="inventory/function_flags.csv", help="FunctionFlags artifact")
    ap.add_argument("-o", "--out", default=None, help="directory to write console_commands.csv into")
    ap.add_argument("--check", action="store_true",
                    help="compare against the committed artifact instead of writing (-o required with it)")
    ap.add_argument("--walk", metavar="NAME", help="print every declaration and binary row whose command is NAME")
    a = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flags_path = a.flags if os.path.isabs(a.flags) else os.path.join(root, a.flags)

    text = []
    if a.check and not a.src:
        # `make ci` runs every tool's --check with no dump and no arguments, so the check has to answer from the
        # committed artifact alone: shape and self-consistency, never the declaration count (that needs --src).
        return check_shape(os.path.join(root, a.out or "inventory", "console_commands.csv"))
    if a.src:
        if not os.path.isdir(a.src):
            raise SystemExit("console-commands: no script tree at %s (extract it first)" % a.src)
        text = parse_tree(a.src)
    elif not a.walk:
        raise SystemExit("console-commands: pass --src (and -o to write the artifact)")

    binary = []
    if os.path.exists(flags_path):
        with open(flags_path, newline="") as fh:
            for row in csv.DictReader(fh):
                if "Exec" in row["flag_names"].split(";"):
                    binary.append((row["package"], row["class"], row["function"]))

    if a.walk:
        want = a.walk.lower()
        seen = 0
        for (pkg, cls, state, cmd, args, mods) in text:
            if cmd.lower() == want:
                seen += 1
                print("text   %s.%s%s  %s(%s) %s" % (pkg, cls, ("." + state) if state else "", cmd, args, mods))
        for (pkg, cls, fn) in binary:
            if fn.lower() == want:
                seen += 1
                print("binary %s.%s.%s  Exec" % (pkg, cls, fn))
        if not seen:
            print("%s is declared nowhere: no `exec function` in the shipped text and no Exec flag in the binary"
                  % a.walk)
        return 0

    # Join. The text side is (package, class, state, command); the binary side is (package, class-or-state, command),
    # and a state-scoped binary row names the state in its class column.
    text_keys = {}
    for (pkg, cls, state, cmd, args, mods) in text:
        text_keys.setdefault((pkg.lower(), cls.lower(), state.lower(), cmd.lower()), []).append(
            (pkg, cls, state, cmd, args, mods))
    binary_keys = {(p.lower(), c.lower(), f.lower()) for (p, c, f) in binary}
    rows, matched = [], set()
    for key, decls in sorted(text_keys.items()):
        pkg, cls, state, cmd = key
        sources = "text"
        if (pkg, cls, cmd) in binary_keys:
            sources = "both"
            matched.add((pkg, cls, cmd))
        elif state and (pkg, state, cmd) in binary_keys:
            sources = "both"                       # the state's own function record
            matched.add((pkg, state, cmd))
        first = decls[0]
        rows.append([first[0], first[1], first[2], first[3], first[4], first[5], sources])
    # A binary Exec row with no declaration: a native or state-scoped function whose text is not in this tree.
    for (pkg, cls, fn) in sorted(binary_keys):
        if (pkg, cls, fn) in matched:
            continue
        if (pkg, cls, "", fn) in text_keys:
            continue
        rows.append([pkg, cls, "", fn, "", "", "binary"])

    rows.sort()
    classes = {(r[0], r[1], r[2]) for r in text}
    both = sum(1 for r in rows if r[6] == "both")
    only_bin = sum(1 for r in rows if r[6] == "binary")
    only_txt = sum(1 for r in rows if r[6] == "text")
    print("console-commands: %d declarations over %d class/state scopes, %d distinct names"
          % (len(text), len(classes), len({r[3].lower() for r in rows})))
    print("  both sources %d, text only %d, binary only %d" % (both, only_txt, only_bin))
    owner = {}
    for r in rows:
        owner.setdefault(r[1], 0)
        owner[r[1]] += 1
    print("  top owners: " + ", ".join("%s %d" % kv for kv in sorted(owner.items(), key=lambda kv: -kv[1])[:6]))

    if a.check:
        want = os.path.join(root, a.out or "inventory", "console_commands.csv")
        if not os.path.exists(want):
            print("console-commands --check: %s is missing" % want)
            return 2
        with open(want, newline="") as fh:
            have = list(csv.reader(fh))
        if have[0] != COLUMNS:
            print("console-commands --check: %s has columns %s, expected %s" % (want, have[0], COLUMNS))
            return 2
        if have[1:] == rows:
            print("console-commands --check: %s reproduces byte for byte" % want)
            return 0
        diff = sum(1 for x, y in zip(have[1:], rows) if x != y) + abs(len(have) - 1 - len(rows))
        print("console-commands --check: %s is stale, %d rows differ (have %d, measured %d)"
              % (want, diff, len(have) - 1, len(rows)))
        return 2

    if a.out:
        os.makedirs(a.out, exist_ok=True)
        target = os.path.join(a.out, "console_commands.csv")
        with open(target, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(COLUMNS)
            w.writerows(rows)
        print("wrote %s (%d rows)" % (target, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
