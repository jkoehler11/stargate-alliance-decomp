#!/usr/bin/env python3
"""The console's command surface: every `exec function` the shipped scripts declare, reconciled with the binary.

PLAN 7.3 asks what the console can actually run. `docs/XEMU.md` had a table of probes (`stat fps` drew a counter,
`stat game`, `stat physics`, `showdebug`, `behindview 1` and `walk` left 0 px) and read a zero as "the command never
entered the console". That inference needs the command *list* first: an UnrealScript `exec function` is what
`ConsoleCommand` can find, so a name the scripts do not declare can only be engine-side, and a name declared on
`CheatManager` needs cheats instantiated to exist at all.

    python3 tools/console-commands.py --system <dump>/System -o inventory    # one 4-second read, no boot

Two sources, deliberately kept apart in the artifact:

- **text** -- the `ScriptText` every class export carries (2,319 classes, 6.1 MB), read **from the packages**. The
  tool used to walk `$WORK/UnrealScript`, and that tree is not the shipped text: its header reads "Reconstructed
  from StargateGame.u", it reflows the declarations and drops or invents `exec function`s (336 declarations over
  37 scopes against the packages' 348 over 47, `StargatePlayer` 61 against 83, `GUIController` 8 against 2). A tool
  that walks a derived tree measures the tree (UNKNOWNS U24's reader rule), so this one takes `--system`.
- **binary** -- `inventory/function_flags.csv` carries `FunctionFlags` per `Function` payload, and `Exec` (0x00000400)
  is set by the compiler for exactly those declarations. It is an independent measurement of the same population,
  which is why the join is worth carrying: a row in one source and not the other is a fact about the build, not
  noise. **`function_flags.csv`'s `class` column names a *state* for a state-scoped function** (`Engine/Dead`,
  `Engine/GameEnded`), so a binary row joins to a declaration inside that state, not to a class of that name.

A declaration's state scope comes from `tools/class-text-bodies.py`'s `state_declarations` -- the one state-header
rule (UNKNOWNS U24) -- and the scan runs on that module's comment- and literal-masked text, so a declaration a block
comment disables is not a command (six `Engine.PlayerController` names the line rule used to count: `LoadGame`,
`NewSaveGame`, `SaveGame`, `ListSavedGames`, `LocalTravel`, `RestartLevel`, ... ).

The artifact is `inventory/console_commands.csv`, one row per (package, class, state, command), with a `sources`
column: `text`, `binary`, or `both`. It answers "is this a command and where does it live", and nothing about
arguments or the engine's own handlers (`stat`, `open`, ... are native, not script). `--write-doc`/`--check-doc`
render and gate the table `docs/XEMU.md` quotes, so its figures cannot drift from this artifact.
"""
import argparse
import collections
import csv
import glob
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "console_commands.csv")
COLUMNS = ["package", "class", "state", "command", "args", "modifiers", "sources", "declared_in"]
# A declaration, read on masked text: any run of modifier words, `exec`, then **another** run of modifiers, then
# `function name(` -- the parameter list may open on the line and continue on the next. The second run is not
# decoration: this corpus spells 15 commands `exec final function Foo(...)`, and a pattern that wants `exec function`
# adjacent (which both this tool and the extracted tree's read carried) cannot see any of them -- they are the
# population the artifact used to file as "Exec with no declaration". Anchored at a line start so a call inside a
# body cannot match.
EXEC_DECL = re.compile(r"(?m)^[ \t]*((?:[A-Za-z_]\w*[ \t]+)*?)exec[ \t]+((?:[A-Za-z_]\w*[ \t]+)*)"
                       r"function[ \t]+([A-Za-z_]\w*)[ \t]*\(")
MODIFIERS = ("simulated", "final", "static", "native", "latent", "singular", "event", "private", "protected",
             "public", "reliable", "unreliable")


def load_tool(name, filename):
    """A sibling tool by path: the package reader and the one state-header rule."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def paren_args(masked, at):
    """The parameter text of a declaration whose `(` sits at `at`, whitespace-normalised."""
    depth, j = 0, at
    while j < len(masked):
        if masked[j] == "(":
            depth += 1
        elif masked[j] == ")":
            depth -= 1
            if depth == 0:
                return " ".join(masked[at + 1:j].split())
        j += 1
    return ""


def declarations(system):
    """(rows, stats) for every `exec function` in the packages' `ScriptText`.

    A row is (package, class, state, command, args, modifiers). `state` is the enclosing state block's name, or ''
    for a class-level declaration, and it comes from `class-text-bodies.state_declarations` applied to the same
    unmasked text -- the scan itself walks the masked copy, so every offset is the unmasked one.
    """
    pst = load_tool("pst_console", "pkg-script-text.py")
    ctb = load_tool("ctb_console", "class-text-bodies.py")
    ue = load_tool("ue_console", "ue2pkg.py")
    rows, stats = [], collections.Counter()
    for path in sorted(glob.glob(os.path.join(system, "*.u"))):
        package = os.path.splitext(os.path.basename(path))[0]
        pkg = ue.Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            text = pst.script_text(pkg, exp)
            if not text:
                stats["classes_without_text"] += 1
                continue
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            stats["classes"] += 1
            masked = ctb.mask(text)
            spans = ctb.state_declarations(text)
            stats["states"] += len(spans)
            stats["masked_bytes"] += sum(1 for a, b in zip(text, masked) if a != b)
            found = 0
            for m in EXEC_DECL.finditer(masked):
                found += 1
                at = m.start(3)
                state = ""
                for d in spans:
                    if d["start"] < at < d["end"]:
                        state = d["name"]
                        break
                words = (m.group(1) + " " + m.group(2)).split()
                mods = sorted({t.lower() for t in words if t.lower() in MODIFIERS})
                rows.append((package, cls, state, m.group(3), paren_args(masked, m.end() - 1), ";".join(mods)))
            # The line rule the tool carried counted a declaration inside a `/* ... */` block; the mask does not,
            # and that difference is a number rather than a claim.
            for m in re.finditer(r"(?m)^[ \t]*(?:[A-Za-z_]\w*[ \t]+)*exec[ \t]+(?:[A-Za-z_]\w*[ \t]+)*"
                                 r"function[ \t]+([A-Za-z_]\w*)", text):
                if not masked[m.start(1):].startswith(m.group(1)):
                    stats["declarations_in_comments"] += 1
            stats["declarations_seen_unmasked"] += found
    return rows, stats


def read_binary(flags_path):
    """(package, class, function) for every `Function` payload carrying `Exec`."""
    out = []
    if not os.path.exists(flags_path):
        return out
    with open(flags_path, newline="") as fh:
        for row in csv.DictReader(fh):
            if "Exec" in row["flag_names"].split(";"):
                out.append((row["package"], row["class"], row["function"]))
    return out


def join(text, binary):
    """The artifact's rows: text declarations joined to the binary, plus the binary's own surplus."""
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
        rows.append([first[0], first[1], first[2], first[3], first[4], first[5], sources, ""])
    # A binary Exec row with no declaration of its own. Most are a *second record for a command the text declares
    # elsewhere*: a state's function table carries the commands it inherits, so `PlayerGrabbed` has nine Exec
    # records while its own text declares two. `declared_in` names where the shipped text declares that command
    # (same package first, then another -- a state can inherit from a class in a parent package) and is empty only
    # when the text declares the name nowhere, which is an engine-side handler or a reconstruction-only name.
    origin = {}
    for (pkg, cls, state, cmd, _a, _m) in text:
        origin.setdefault((pkg.lower(), cmd.lower()), []).append((pkg, cls, state))
    anywhere = {}
    for (pkg, cmd), hits in origin.items():
        anywhere.setdefault(cmd.lower(), []).extend(hits)
    for (pkg, cls, fn) in sorted(binary_keys):
        if (pkg, cls, fn) in matched:
            continue
        if (pkg, cls, "", fn) in text_keys:
            continue
        hits = origin.get((pkg, fn)) or anywhere.get(fn) or []
        where = ""
        if hits:
            where = ".".join([hits[0][0], hits[0][1]] + ([hits[0][2]] if hits[0][2] else []))
        rows.append([pkg, cls, "", fn, "", "", "binary", where])
    rows.sort()
    return rows


def summarise(rows):
    """The counts the page quotes, computed from the artifact's own rows."""
    text_rows = [r for r in rows if r[6] in ("both", "text")]
    binary_rows = [r for r in rows if r[6] == "binary"]
    out = {
        "rows": len(rows),
        "declarations": len(text_rows),
        "scopes": len({(r[0], r[1], r[2]) for r in text_rows}),
        "state_scopes": len({(r[0], r[1], r[2]) for r in text_rows if r[2]}),
        "state_declarations": len([r for r in text_rows if r[2]]),
        "names": len({r[3].lower() for r in rows}),
        "both": len([r for r in rows if r[6] == "both"]),
        "text_only": len([r for r in rows if r[6] == "text"]),
        "binary_only": len(binary_rows),
        "binary_elsewhere": len([r for r in binary_rows if r[7]]),
        "binary_nowhere": len([r for r in binary_rows if not r[7]]),
        "binary_same_state": len([r for r in binary_rows
                                  if r[7] and r[7].lower().endswith("." + r[1].lower())]),
        "top": collections.Counter(r[1] for r in text_rows).most_common(6),
    }
    return out


def render_measures(rows):
    """The measures table for `docs/XEMU.md`, from the artifact alone (`--check-doc` needs no dump)."""
    s = summarise(rows)
    return "\n".join([
        "| measure | value |", "|---|---|",
        "| `exec function` declarations in the shipped text | **%d** |" % s["declarations"],
        "| of those, inside a state body | **%d** over **%d** states |" % (s["state_declarations"],
                                                                         s["state_scopes"]),
        "| class or state scopes that declare one | **%d** |" % s["scopes"],
        "| distinct command names in the artifact | **%d** |" % s["names"],
        "| declarations the binary also flags `Exec` | **%d of %d** |" % (s["both"], s["declarations"]),
        "| declarations with no function record at all (`sources=text`) | **%d** |" % s["text_only"],
        "| `Exec`-flagged records with no declaration of their own | **%d** |" % s["binary_only"],
        "| of those, naming a command the text declares elsewhere | **%d** |" % s["binary_elsewhere"],
        "| of those, naming a command the text declares nowhere | **%d** |" % s["binary_nowhere"],
        "| artifact rows | **%d** |" % s["rows"]])


def render_owners(rows):
    """The owner table for `docs/XEMU.md`: declarations per class, with the state half beside it."""
    text_rows = [r for r in rows if r[6] in ("both", "text")]
    owners = collections.Counter("%s.%s" % (r[0], r[1]) for r in text_rows)
    states = collections.Counter("%s.%s" % (r[0], r[1]) for r in text_rows if r[2])
    seen = collections.defaultdict(set)
    for r in text_rows:
        seen[r[3].lower()].add("%s.%s" % (r[0], r[1]))
    lines = ["| owner | declarations | of those in a state |", "|---|---|---|"]
    big = [(o, n) for o, n in owners.most_common() if n >= 8]
    rest = [(o, n) for o, n in owners.most_common() if n < 8]
    for owner, n in big:
        lines.append("| `%s` | %d | %d |" % (owner, n, states.get(owner, 0)))
    if rest:
        lines.append("| the other %d classes | %d | %d |"
                     % (len(rest), sum(n for _o, n in rest), sum(states.get(o, 0) for o, _n in rest)))
    lines += ["", "%d command names are declared by more than one owner, so \"which object answers\" is a"
                  " class-chain question and not a name-to-handler table."
              % len([1 for owners_of in seen.values() if len(owners_of) > 1])]
    return "\n".join(lines)


RENDER = {"console-commands": render_measures, "console-commands-owners": render_owners}


def splice(path, rows):
    """`path` with each generated block replaced; every splice is computed before the page is opened."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    out = text
    for mark, render in RENDER.items():
        a, b = "<!-- generated:%s -->" % mark, "<!-- /generated:%s -->" % mark
        i, j = out.find(a), out.find(b)
        if i < 0 or j < i:
            raise SystemExit("console-commands: markers for %s not found in %s" % (mark, path))
        out = out[:i + len(a)] + "\n" + render(rows) + "\n" + out[j:]
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
    print("console-commands --check: %s, %d rows, shape ok (the declaration count needs --system)"
          % (os.path.basename(path), len(body)))
    return 0


def check_doc(rows, path):
    """`make ci`: each block on the page must equal a render off the committed artifact."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    stale = []
    for mark, render in RENDER.items():
        a, b = "<!-- generated:%s -->" % mark, "<!-- /generated:%s -->" % mark
        i, j = text.find(a), text.find(b)
        if i < 0 or j < i:
            print("console-commands --check-doc: markers for %s not found in %s" % (mark, path))
            return 2
        if text[i + len(a):j].strip("\n") != render(rows):
            stale.append(mark)
    if stale:
        print("console-commands --check-doc: %s is stale (%s)" % (path, ", ".join(stale)))
        return 2
    print("console-commands --check-doc: %s matches its artifact" % path)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory (18 .u packages)")
    ap.add_argument("--flags", default="inventory/function_flags.csv", help="FunctionFlags artifact")
    ap.add_argument("-o", "--out", default=None, help="directory to write console_commands.csv into")
    ap.add_argument("--check", action="store_true",
                    help="compare against the committed artifact instead of writing (needs --system; with no "
                         "arguments it answers from the artifact's shape alone, which is what `make ci` runs)")
    ap.add_argument("--walk", metavar="NAME", help="print every declaration and binary row whose command is NAME")
    ap.add_argument("--write-doc", metavar="PAGE", help="render the generated block into this page")
    ap.add_argument("--check-doc", metavar="PAGE", help="fail if the page's block is stale (in `make ci`)")
    a = ap.parse_args()

    flags_path = a.flags if os.path.isabs(a.flags) else os.path.join(ROOT, a.flags)
    have = None
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="") as fh:
            have = list(csv.reader(fh))[1:]

    if a.check and not a.system:
        rc = check_shape(os.path.join(ROOT, a.out or "inventory", "console_commands.csv"))
        return rc
    if a.check_doc and not a.system:
        if have is None:
            print("console-commands --check-doc: %s is missing" % CSV_PATH)
            return 2
        return check_doc(have, a.check_doc)
    if not a.system:
        raise SystemExit("console-commands: pass --system (the dump's System directory)")

    text, stats = declarations(a.system)
    binary = read_binary(flags_path)

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

    rows = join(text, binary)
    s = summarise(rows)
    print("console-commands: %d declarations over %d class/state scopes (%d of them states), %d distinct names"
          % (s["declarations"], s["scopes"], s["state_scopes"], s["names"]))
    print("  both sources %d, text only %d, binary only %d (%d name a command the text declares elsewhere, "
          "%d nowhere)" % (s["both"], s["text_only"], s["binary_only"], s["binary_elsewhere"],
                           s["binary_nowhere"]))
    print("  read %d classes in %d packages, %d state blocks; %d declaration(s) inside a comment are not commands"
          % (stats["classes"], len(glob.glob(os.path.join(a.system, "*.u"))), stats["states"],
             stats["declarations_in_comments"]))
    print("  top owners: " + ", ".join("%s %d" % kv for kv in s["top"]))

    if a.check:
        want = os.path.join(ROOT, a.out or "inventory", "console_commands.csv")
        if not os.path.exists(want):
            print("console-commands --check: %s is missing" % want)
            return 2
        with open(want, newline="") as fh:
            disk = list(csv.reader(fh))
        if disk[0] != COLUMNS:
            print("console-commands --check: %s has columns %s, expected %s" % (want, disk[0], COLUMNS))
            return 2
        if disk[1:] == rows:
            print("console-commands --check: %s reproduces byte for byte" % want)
            return 0
        diff = sum(1 for x, y in zip(disk[1:], rows) if x != y) + abs(len(disk) - 1 - len(rows))
        print("console-commands --check: %s is stale, %d rows differ (have %d, measured %d)"
              % (want, diff, len(disk) - 1, len(rows)))
        return 2

    if a.out:
        os.makedirs(a.out, exist_ok=True)
        target = os.path.join(a.out, "console_commands.csv")
        with open(target, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(COLUMNS)
            w.writerows(rows)
        print("wrote %s (%d rows)" % (target, len(rows)))
    if a.write_doc:
        new = splice(a.write_doc, rows)              # compute before opening the page for writing
        with open(a.write_doc, "w", encoding="utf-8") as fh:
            fh.write(new)
        print("wrote %s" % a.write_doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
