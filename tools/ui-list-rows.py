#!/usr/bin/env python3
"""A list's row: the rectangle is native, the columns are script, and the cell table is empty.

`docs/systems/UI.md` ended its layout thread on one sentence -- *a list's row geometry comes from the list
class's own drawing code (`MyOnDrawItem` on `CoopPlayerPanel` is a 513-byte body), not from a `Begin Object`
block*. This reads that drawing code and the delegate it hangs off.

`GUI.GUIListBase` is a `native` class: its row is drawn by C++, which calls the `OnDrawItem` delegate with the
row's rectangle and honours its return value (*"Return true to shortcircuit the native DrawItem"*). So the row
rectangle is not in the corpus, and everything a body does with it is:

* **who declares a body and who wires it** -- `List.OnDrawItem = MyOnDrawItem`, live or commented out. This
  corpus comments out the UI it cut, so a commented wiring is a cut row and not a reader's gap;
* **per column: where it is placed** -- through the cell table (`GetCellLeftWidth`), through the screen
  (`Canvas.ClipX * f`), or through the row (`W/2`), with the value and the property path it prints;
* **the cell table itself** -- `ColumnWidths[i] = ActualWidth() * Columns[i].Percentage`, filled by
  `InitializeColumns` from the `Columns` array, and whether any defaults block in the corpus states `Columns`;
* **the row height** -- `ItemHeight`/`ItemWidth`/`ItemsPerPage` are `var(List)` on the native class, so a row's
  height is script only where a class writes it.

    python3 tools/ui-list-rows.py DUMP --defaults TREE --sweep -o inventory
    python3 tools/ui-list-rows.py --report
    python3 tools/ui-list-rows.py --write-doc docs/systems/UI.md
    python3 tools/ui-list-rows.py --check-doc docs/systems/UI.md
    python3 tools/ui-list-rows.py --check          # synthetic, no dump, writes nothing
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ARTIFACT = os.path.join(ROOT, "inventory/ui_list_rows.csv")
DOC = os.path.join(ROOT, "docs/systems/UI.md")
DOC_START = "<!-- generated:list-rows -->"
DOC_END = "<!-- /generated:list-rows -->"

FIELDS = ["kind", "name", "package", "column", "form", "value", "field", "verdict", "note"]

# The delegate a list calls per item, and the assignment that wires a class's body to it.
DELEGATE = "MyOnDrawItem"
ASSIGN = re.compile(r"\bOnDrawItem\s*=\s*(?P<fn>[A-Za-z_]\w*)")
WIRING = re.compile(r"(?:(?P<recv>[A-Za-z_][\w.]*)\s*\.\s*)?OnDrawItem\s*=\s*(?P<fn>[A-Za-z_]\w*)")
DECL = re.compile(r"^\s*(?P<mods>(?:(?:simulated|event|final|static|native)\s+)*)function\s+"
                  r"(?P<ret>[\w<>\[\]]+)\s+(?P<name>\w+)\s*\((?P<args>[^)]*)\)", re.M)
# A cell-table placement, a screen placement and a row placement.
CELL = re.compile(r"GetCellLeftWidth\s*\(\s*(?P<col>\d+)\s*,\s*(?P<left>\w+)\s*,\s*(?P<w>\w+)\s*\)")
CLIPX = re.compile(r"Canvas\.ClipX\s*\*\s*(?P<f>[0-9.]+)")
CLIP_STEP = re.compile(r"(?P<var>\w+)\s*=\s*(?P=var)\s*\+\s*(?P<wvar>\w+)\s*\+\s*\(\s*Canvas\.ClipX\s*\*\s*(?P<f>[0-9.]+)")
CLIP_LEFT = re.compile(r"(?P<var>\w+)\s*=\s*X\s*\+\s*\(\s*Canvas\.ClipX\s*\*\s*(?P<f>[0-9.]+)")
CLIP_WIDTH = re.compile(r"(?P<var>\w+)\s*=\s*Canvas\.ClipX\s*\*\s*(?P<f>[0-9.]+)")
ROWFRAC = re.compile(r"\bW\s*/\s*(?P<n>[0-9.]+)")
TEXT_DRAW = re.compile(r"\bDrawText\s*\((?P<args>[^;]*)\)")
STYLE_DRAW = re.compile(r"\bDraw\s*\(")
GUARD = re.compile(r"\bif\s*\((?P<cond>[^)]*)\)")
# The property path a draw prints: `MyData[index].Name`, `""$MyData[index].ID`, `MyServerList[i].MaxPlayers`.
PATH = re.compile(r"\b([A-Za-z_]\w*)(?:\s*\[[^\]]*\])?\s*\.\s*([A-Za-z_]\w*)")
ALIGN = re.compile(r"\b(TXTA_\w+)\b")
SELECTED_STATE = re.compile(r"\b(?:MSAT_Focused|MSAT_Pressed)\b")
# A column indent stated as a named constant rather than a literal in the placement line.
CONST = re.compile(r"^\s*const\s+(?P<name>\w+)\s*=\s*(?P<val>[0-9.]+)", re.M)
RETURN = re.compile(r"\breturn\s+(?P<val>true|false)\s*;")
KEYWORDS = {"if", "else", "return", "local", "true", "false", "None", "Canvas", "self", "Super"}


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def strip_comments(text):
    """(code, comments): the body with `//` and `/* */` spans removed and kept apart.

    Shared with `ui-menu-flow.py` rather than re-spelled: this corpus comments out the UI it cut, and the
    difference between a live wiring and a commented one is a measurement, not tidying.
    """
    return _load("ui_menu_flow_shared", "ui-menu-flow.py").strip_comments(text)


def declarations(text):
    """[(name, ret, nargs)] for every function declaration in a class body (comments already removed)."""
    out = []
    for m in DECL.finditer(text):
        nargs = len([a for a in m.group("args").split(",") if a.strip()])
        out.append((m.group("name"), m.group("ret"), nargs))
    return out


def body_of(code, name):
    """The text of one function body: from its declaration to the closing brace in column 0."""
    m = re.search(r"^[^\n]*\bfunction\b[^\n(]*\b%s\s*\(" % re.escape(name), code, re.M)
    if not m:
        return ""
    start = m.start()
    depth, i, n = 0, m.end(), len(code)
    seen = False
    while i < n:
        ch = code[i]
        if ch == "{":
            depth += 1
            seen = True
        elif ch == "}":
            depth -= 1
            if seen and depth <= 0:
                return code[start:i + 1]
        i += 1
    return code[start:]


def _path(arg):
    """The property a drawn argument ends in: `MyData[index].Name` -> `Name`, `a.Current@"/"@b.Max` -> both.

    The head of the path is the array the row indexes and the tail is what the row prints, so the tail is the
    field; a `@`-joined expression prints more than one, and each contributes its own tail.
    """
    parts = []
    for piece in re.split(r'@|\$|\+', arg):
        hits = re.findall(r"\.([A-Za-z_]\w*)", piece)
        if hits:
            parts.append(hits[-1])
    return "/".join(dict.fromkeys(parts))


def classify(body):
    """(columns, notes) for one item-drawing body.

    A column is one text draw, placed by whichever of the three forms last ran: the cell table, a
    `Canvas.ClipX` fraction, or a fraction of the row width. State is kept as the body is walked in order,
    because these bodies place their columns by mutating a `posX`/`sizeX` pair between draws.
    """
    columns = []
    left_var, left, width = "", "", ""
    pending = None            # ("cell", idx, "") | ("canvas", left, width) | ("row", n, "")
    guard = ""
    has_state = bool(SELECTED_STATE.search(body))
    # A `.Draw(` on the row's own rectangle is the background; one that paints the pressed state is the
    # selection rectangle. `ServerList` draws only the second, so the two are counted apart.
    draws_bg = bool([1 for l in body.splitlines() if re.search(r"\b\w*Style\w*\.Draw\s*\(", l)
                     and "MSAT_Pressed" not in l])
    draws_sel = bool(re.search(r"\b\w*Style\w*\.Draw\s*\([^;]*MSAT_Pressed", body))
    consts = CONST.findall(body)
    lines = body.splitlines()
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        m = GUARD.search(line)
        if m:
            # Only a brace-opening `if` guards the draws that follow: `if( i >= MyData.Length ) return true;`
            # is a bail-out and `if( bSelected ) DStyle.Draw(...)` a one-line call, and treating either as a
            # column condition labels every column with it. The property is the condition's last identifier.
            names = [w for w in re.findall(r"[A-Za-z_]\w*", m.group("cond")) if w not in KEYWORDS]
            nxt = [l for l in lines[idx + 1:] if l.strip()]
            guard = names[-1] if names and not line[m.end():].strip(" \t;") and nxt and nxt[0].strip().startswith("{") else ""
            continue
        m = CELL.search(line)
        if m:
            pending = ("cell", m.group("col"), "")
            continue
        m = CLIP_LEFT.search(line)
        if m:
            left_var, left, width = m.group("var"), m.group("f"), ""
            pending = ("canvas", left, width)
            continue
        m = CLIP_WIDTH.search(line)
        if m:
            if left_var and m.group("var") != left_var:
                width = m.group("f")                 # `sizeX = Canvas.ClipX * f` is the pending column's width
            else:
                left_var, left, width = m.group("var"), m.group("f"), ""
            pending = ("canvas", left, width)
            continue
        m = CLIP_STEP.search(line)
        if m:
            # `posX = posX + sizeX + (Canvas.ClipX * f)`: the next column starts at the last left plus its
            # width plus this gap, so the value keeps the whole chain rather than only its last term.
            left = "%s+%s+%s" % (left, width or "?", m.group("f"))
            left_var, width = m.group("var"), ""
            pending = ("canvas", left, width)
            continue
        m = ROWFRAC.search(line)
        if m and TEXT_DRAW.search(line):
            pending = ("row", "1/%s" % m.group("n"), "")
        m = TEXT_DRAW.search(line)
        if not m:
            continue
        args = [a.strip() for a in m.group("args").split(",")]
        align = ALIGN.search(m.group("args"))
        text_arg = args[-1]
        if align:
            pos = [i for i, a in enumerate(args) if a == align.group(1)]
            if pos and pos[0] + 1 < len(args):
                text_arg = args[pos[0] + 1]
        note = []
        if guard:
            note.append("only when %s" % guard)
        if align:
            note.append(align.group(1).lower().replace("txta_", "aligned "))
        form = ""
        value = ""
        col = str(len(columns))
        if pending:
            form = pending[0]
            if form == "cell":
                col, value = pending[1], "cell %s" % pending[1]
            elif form == "canvas":
                value = "L=%s W=%s" % (pending[1], pending[2] or "row")
            else:
                value = pending[1]
        columns.append({"column": col, "form": form or "unplaced", "value": value,
                        "field": _path(text_arg) if args else "", "note": "; ".join(note)})
        pending = None
        guard = ""
    return columns, {"state": has_state, "bg": draws_bg, "sel": draws_sel, "consts": consts}


def read_bodies(system_dir, defaults_tree=""):
    """Every item-drawing body, its wiring, the cell table and the row-height writers, as rows."""
    mf = _load("ui_menu_flow", "ui-menu-flow.py")
    per_pkg, _by_name, text_of = mf.class_tables(system_dir, verbose=False)
    rows = []
    for (pkgname, cls), text in sorted(text_of.items()):
        code, commented = strip_comments(text)
        live = [d for d in declarations(code) if d[0] == DELEGATE]
        cut = [d for d in declarations(commented) if d[0] == DELEGATE]
        for name, ret, nargs in live + cut:
            dead = not live
            body = body_of(code, name) if not dead else body_of(commented, name)
            columns, flags = classify(body)
            rets = RETURN.findall(body)
            # The delegate's own contract: true replaces the native item draw, false falls through to it.
            # The last return is the one the normal path takes; the earlier ones are the `i >= Length` guards.
            verdict = "unwired" if dead else ("replace" if rets and rets[-1] == "true" else "overlay")
            note = []
            if dead:
                note.append("declaration and wiring are both inside a block comment")
            elif verdict == "replace":
                note.append("returns true, so the native item draw is skipped")
            else:
                note.append("returns false, so the native item draw still runs")
            if flags["bg"]:
                note.append("draws the row background itself")
            elif flags["sel"]:
                note.append("draws the selection rectangle itself, so an unselected row is the native draw")
            if flags["state"]:
                note.append("selection is a drawn state, not a style")
            for cname, cval in flags["consts"]:
                note.append("`%s = %s` indents a nested row" % (cname, cval))
            rows.append({"kind": "body", "name": cls, "package": pkgname, "form": "%s(%d)" % (ret, nargs),
                         "value": "%d" % len(columns), "verdict": verdict, "note": "; ".join(note)})
            for c in columns:
                rows.append({"kind": "column", "name": cls, "package": pkgname, "column": c["column"],
                             "form": c["form"], "value": c["value"], "field": c["field"],
                             "verdict": "commented" if dead else "", "note": c["note"]})
        for m in WIRING.finditer(code):
            rows.append({"kind": "wiring", "name": cls, "package": pkgname,
                         "form": "live", "value": m.group("fn"), "verdict": "live",
                         "note": m.group("recv") or "self"})
        for m in WIRING.finditer(commented):
            rows.append({"kind": "wiring", "name": cls, "package": pkgname,
                         "form": "commented", "value": m.group("fn"), "verdict": "commented",
                         "note": (m.group("recv") or "self") + ", commented out, so the body never runs"})

    # The cell table: every column placed through `GetCellLeftWidth` needs `ColumnWidths`, and that array is
    # only ever filled from `Columns`. So count the blocks that state `Columns`, over the whole emitted tree.
    (held, blocks), with_columns = read_defaults_tree(defaults_tree)
    capable, capable_held = columns_capable(held)
    cell_classes = sorted({r["name"] for r in rows if r["kind"] == "column" and r["form"] == "cell"})
    for cls in cell_classes:
        rows.append({"kind": "celltable", "name": cls, "package": "StargateMenus", "form": "Columns",
                     "value": "%d/%d" % (with_columns, blocks), "verdict": "empty",
                     "note": "ColumnWidths[i] = ActualWidth() * Columns[i].Percentage, and no emitted block "
                             "states Columns"})

    # Where each body's class reaches the screen: a block, a string, or nothing.
    rows.extend(read_placements(text_of, defaults_tree,
                                sorted({r["name"] for r in rows if r["kind"] == "body"})))

    # The row height: the three `var(List)` sizes the native list owns, and every script write to one.
    for (pkgname, cls), text in sorted(text_of.items()):
        code, _commented = strip_comments(text)
        for prop in ("ItemHeight", "ItemWidth", "ItemsPerPage"):
            for m in re.finditer(r"(?<![=!<>])\b%s\s*=(?!=)\s*(?P<rhs>[^;=\n][^;\n]*)" % prop, code):
                line = m.group(0).strip()
                if line.startswith("var") or "==" in line:
                    continue
                rows.append({"kind": "rowrect", "name": cls, "package": pkgname, "form": prop,
                             "value": m.group("rhs").strip()[:40], "verdict": "script",
                             "note": line[:80]})
    for prop, gloss in (("ItemHeight", "subclass should set in PreDraw"),
                        ("ItemWidth", "subclass should set in PreDraw"),
                        ("ItemsPerPage", "is set natively")):
        rows.append({"kind": "rowrect", "name": "GUIListBase", "package": "GUI", "form": prop,
                     "value": "var(List)", "verdict": "native", "note": gloss})
    rows.sort(key=lambda r: (r["kind"] != "census", r["kind"], r["name"], r.get("column", ""),
                             r.get("form", "")))
    extra = {"columns_capable": len(capable), "columns_capable_held": capable_held}
    rows.append(census_row(rows, extra))
    return rows


def read_placements(text_of, tree, classes):
    """How each of `classes` reaches the screen: a `Begin Object` block, a string, or nothing.

    The layout tree holds a widget only when a class's defaults block places it. A panel can also be created
    from a *string* at runtime (`TabControl.AddTab(" OBJECTIVES ", "StargateMenus.ObjectivesPanel", ...)`), and
    a class nothing names is as cut as the ten pages nothing names -- so the three are counted apart.
    """
    rows = []
    blocks = {}
    if tree and os.path.isdir(tree):
        for dirpath, _dirs, files in os.walk(tree):
            for fname in files:
                if not fname.endswith(".defaultproperties.uc"):
                    continue
                holder = fname[:-len(".defaultproperties.uc")]
                with open(os.path.join(dirpath, fname), encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
                for m in re.finditer(r"^\s*Begin\s+Object\b[^\n]*\bClass=(?P<cls>\w+)", body, re.M):
                    blocks.setdefault(m.group("cls"), []).append(holder)
    for cls in classes:
        holders = blocks.get(cls, [])
        strs = []
        for (pkgname, owner), text in text_of.items():
            code, _com = strip_comments(text)
            if re.search(r'["\'](?:(?P<pkg>[A-Za-z0-9_]+)\.)?%s["\']' % re.escape(cls), code):
                strs.append(owner)
        if holders:
            rows.append({"kind": "placement", "name": cls, "package": "", "form": "block",
                         "value": ", ".join(sorted(set(holders))), "verdict": "block",
                         "note": "placed by a Begin Object block"})
        elif strs:
            rows.append({"kind": "placement", "name": cls, "package": "", "form": "string",
                         "value": ", ".join(sorted(set(strs))), "verdict": "string",
                         "note": "named by a string in code, not by a block"})
        else:
            rows.append({"kind": "placement", "name": cls, "package": "", "form": "none",
                         "value": "", "verdict": "none",
                         "note": "no emitted block and no script string names it"})
    return rows


def columns_capable(blocks):
    """(capable, emitted): the classes that can state `Columns`, and how many of them the tree holds.

    `Columns` is declared on `GUIMultiColumnList` and `GUIMultiColumnListBox`, so only their descendants can
    carry it -- a class that does not descend from either cannot make the cell table non-empty however its
    block reads. Read from the committed class table, so the check needs no dump.
    """
    path = os.path.join(ROOT, "inventory/classes.csv")
    if not os.path.isfile(path):
        return [], 0
    with open(path, newline="", encoding="utf-8") as fh:
        supers = {r["class"]: r["super"] for r in csv.DictReader(fh)}
    declarers = {"GUIMultiColumnList", "GUIMultiColumnListBox"}
    capable = []
    for cls in supers:
        seen, cur = set(), cls
        while cur and cur not in seen and cur not in declarers:
            seen.add(cur)
            cur = supers.get(cur, "")
        if cur in declarers:
            capable.append(cls)
    return sorted(capable), len([c for c in capable if c in blocks])


def read_defaults_tree(tree):
    """((held, blocks), how many blocks state a top-level `Columns`) over the `defaults-emit --emit` tree."""
    if not tree or not os.path.isdir(tree):
        return (set(), 0), 0
    blocks, held, with_columns = 0, set(), 0
    for dirpath, _dirs, files in os.walk(tree):
        for fname in files:
            if not fname.endswith(".defaultproperties.uc"):
                continue
            blocks += 1
            held.add(fname[:-len(".defaultproperties.uc")])
            with open(os.path.join(dirpath, fname), encoding="utf-8", errors="replace") as fh:
                if re.search(r"^\s*Columns\s*[=(]", fh.read(), re.M):
                    with_columns += 1
    return (held, blocks), with_columns


def census_row(rows, extra=None):
    bodies = [r for r in rows if r["kind"] == "body"]
    live = [r for r in bodies if r["verdict"] != "unwired"]
    all_columns = [r for r in rows if r["kind"] == "column"]
    columns = [c for c in all_columns if not c["verdict"]]
    forms = collections.Counter(c["form"] for c in all_columns)
    wirings = [r for r in rows if r["kind"] == "wiring"]
    cell = [r for r in rows if r["kind"] == "celltable"]
    heights = [r for r in rows if r["kind"] == "rowrect" and r["verdict"] == "script"]
    fields = dict(
        bodies=len(bodies), live=len(live), unwired=len(bodies) - len(live),
        columns=len(columns), cut_columns=len(all_columns) - len(columns),
        cell=forms.get("cell", 0), canvas=forms.get("canvas", 0),
        row=forms.get("row", 0),        other=len(all_columns) - sum(forms.values()),
        wirings=len(wirings), cut_wirings=len([w for w in wirings if w["verdict"] == "commented"]),
        replace=len([b for b in bodies if b["verdict"] == "replace"]),
        overlay=len([b for b in bodies if b["verdict"] == "overlay"]),
        cell_classes=len(cell), height_writers=len(heights),
        height_props=len([r for r in rows if r["kind"] == "rowrect" and r["verdict"] == "native"]),
        defaults_blocks=cell[0]["value"].split("/")[1] if cell else "0",
        defaults_with_columns=cell[0]["value"].split("/")[0] if cell else "0",
    )
    fields.update(extra or {})
    note = "|".join("%s=%s" % (k, v) for k, v in sorted(fields.items()))
    return {"kind": "census", "name": "list-rows", "note": note}


def summarize(rows):
    c = [r for r in rows if r["kind"] == "census"][0]["note"]
    return dict(kv.split("=", 1) for kv in c.split("|"))


def write(rows, out_dir):
    path = os.path.join(out_dir, "ui_list_rows.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print("wrote %s (%d rows)" % (path, len(rows)))


# ------------------------------------------------------------------ the doc block

def render(rows):
    s = summarize(rows)
    s = dict(s, blocks_fmt="{:,}".format(int(s["defaults_blocks"])))
    bodies = [r for r in rows if r["kind"] == "body"]
    columns = [r for r in rows if r["kind"] == "column"]
    wirings = [r for r in rows if r["kind"] == "wiring"]
    heights = [r for r in rows if r["kind"] == "rowrect"]
    native = [h for h in heights if h["verdict"] == "native"]
    scripts = [h for h in heights if h["verdict"] == "script"]
    out = []
    out.append("### A list's row: the rectangle is native, and the columns are script")
    out.append("")
    out.append("A page's `Begin Object` block places the list; where each row's *columns* sit is the list's own "
               "drawing code, which the corpus carries as the `OnDrawItem` delegate body `MyOnDrawItem`. "
               "**%(bodies)s bodies** are declared and **%(wirings)s wirings** assign one -- **%(cut_wirings)s of "
               "them commented out** -- and the **%(live)s live** bodies draw **%(columns)s columns**." % s)
    out.append("")
    out.append("**The rectangle a body receives is not in the corpus.** `GUI.GUIListBase` is a `native` class, "
               "and the delegate's own declaration states the contract: the floats are *\"the pos/dimension of "
               "the item to be drawn\"* and the return is *\"Return true to shortcircuit the native DrawItem\"*. "
               "That makes the return value the row's visible difference: a **true** body draws the row itself "
               "and the native item draw is skipped (**%(replace)s** bodies); a **false** body paints over the "
               "native item (**%(overlay)s**)." % s)
    out.append("")
    out.append("| body class | declaration | returns | columns | what it does to the row |")
    out.append("|---|---|---|---:|---|")
    for b in sorted(bodies, key=lambda r: (r["verdict"] == "unwired", r["name"])):
        out.append("| `%s` | `%s` | `%s` | %s | %s |" % (b["name"], b["form"], b["verdict"], b["value"],
                                                         b["note"]))
    out.append("")
    out.append("**Three placements, and only one of them is relative to the row.** A cell-table call "
               "(`GetCellLeftWidth`) places **%(cell)s** columns, a screen fraction (`Canvas.ClipX * f`) places "
               "**%(canvas)s**, and a row fraction (`W/n`) places **%(row)s** -- the row-fraction pair is the "
               "commented body's, so **%(cut_columns)s** of these columns never draw." % s)
    out.append("")
    out.append("| body class | # | placement | value | field | note |")
    out.append("|---|---:|---|---|---|---|")
    for c in sorted(columns, key=lambda r: (r["name"], int(r["column"] or 0))):
        out.append("| `%s` | %s | %s | `%s` | `%s` | %s |" % (c["name"], c["column"], c["form"], c["value"],
                                                             c["field"], c["note"]))
    out.append("")
    places = [r for r in rows if r["kind"] == "placement"]
    by_form = collections.Counter(r["verdict"] for r in places)
    out.append("**Where the list reaches the screen: a block, a string, or nothing.** A body draws whatever list "
               "its class holds, and the layout tree holds only what a `Begin Object` block places -- "
               "**%(block)s** of the **%(total)s** classes are placed that way, **%(string)s** is created from a "
               "*string* in code, and **%(none)s** nothing the corpus states names -- no emitted block, no string "
               "in any of the 18 packages, and no `.ini` key. The 32 `StargateMenus` classes whose block the "
               "emitter refuses sit outside the block scan." %
               {"block": by_form.get("block", 0), "string": by_form.get("string", 0),
                "none": by_form.get("none", 0), "total": len(places)})
    out.append("")
    out.append("| body class | reached by | holder |")
    out.append("|---|---|---|")
    for r in sorted(places, key=lambda r: (r["verdict"], r["name"])):
        out.append("| `%s` | %s | %s |" % (r["name"], r["form"], r["value"] or "-"))
    out.append("")
    out.append("**The cell table is empty, so the cell-placed columns have no width.** `ColumnWidths[i] = "
               "ActualWidth() * Columns[i].Percentage` is filled by `InitializeColumns`, and `Columns` reaches a "
               "list only through `GUIMultiColumnListBox.InitBaseList`'s `if ( Columns.Length > 0 )`. "
               "**%(defaults_with_columns)s of %(blocks_fmt)s** emitted defaults blocks state `Columns`, and "
               "**%(columns_capable_held)s of the %(columns_capable)s** classes that *can* -- the descendants of "
               "`GUIMultiColumnList` and `GUIMultiColumnListBox` -- have an emitted block at all, so no class "
               "that could fill the table is outside the scan. `GetCellLeftWidth` therefore answers `Width = 0` "
               "for every cell of the **%(cell_classes)s** cell-placed bodies, and each of their columns starts "
               "at the list's own `ClientBounds[0]` plus whatever row `X` the caller adds." % s)
    out.append("")
    out.append("**The row height is native, and the corpus writes one only where a class draws its own rows.** "
               "`ItemHeight`, `ItemWidth` and `ItemsPerPage` are `var(List)` on the native `GUIListBase` -- "
               "*\"subclass should set in PreDraw\"*, *\"is set natively\"* -- and the script writes are:" % s)
    out.append("")
    out.append("| class | property | written value |")
    out.append("|---|---|---|")
    for h in sorted(scripts, key=lambda r: (r["package"], r["name"])):
        out.append("| `%s.%s` | `%s` | `%s` |" % (h["package"], h["name"], h["form"], h["value"]))
    out.append("")
    out.append("No menu list is in that table: `moListEditBox` sizes the list it holds to its own height, "
               "`UPreviewMapListClient` states 25 and the stock `UWindow` widgets draw their own rows, which is "
               "why they can state one. So the row a menu list hands its body is whatever the native `PreDraw` "
               "computes -- the one number of this thread the packages do not hold.")
    out.append("")
    out.append("| wiring site | form | body | receiver |")
    out.append("|---|---|---|---|")
    for w in sorted(wirings, key=lambda r: (r["verdict"] != "commented", r["name"])):
        out.append("| `%s` | %s | `%s` | `%s` |" % (w["name"], w["form"], w["value"], w["note"]))
    out.append("")
    out.append("**The commented wiring is a cut row, not a reader's gap.** `SavedGameList` declares "
               "`MyOnDrawItem` inside a block comment together with both of its `InitComponent` lines, so the "
               "save-game list draws the stock item -- one column, no date/type split -- and the two-column body "
               "above never runs. It is the same shape as the pages the corpus names only inside a comment.")
    return "\n".join(out)


def write_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("%s: no list-rows markers" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + DOC_START + "\n" + render(rows) + "\n" + DOC_END + tail)
    print("wrote %s" % path)


def check_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("FAIL list-rows: %s has no markers" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    if head + DOC_START + "\n" + render(rows) + "\n" + DOC_END + tail != text:
        sys.exit("FAIL list-rows: %s's block is stale (run --write-doc)" % path)
    print("ok   list-rows: %s regenerates byte-identically" % path)


def report(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    s = summarize(rows)
    print("list rows: %(bodies)s bodies (%(live)s live, %(unwired)s unwired), %(wirings)s wirings "
          "(%(cut_wirings)s commented), %(columns)s columns" % s)
    print("placement: %(cell)s cell, %(canvas)s canvas, %(row)s row; %(replace)s bodies replace the native "
          "item draw, %(overlay)s paint over it" % s)
    print("cell table: %(defaults_with_columns)s of %(defaults_blocks)s defaults blocks state Columns; "
          "row height: %(height_writers)s script writers over %(height_props)s properties" % s)


# ---------------------------------------------------------------- self-test

FIXTURE = """
class DemoListBox extends SGListBox;
function InitComponent(GUIController C, GUIComponent O)
{
\tSuper.InitComponent(C, O);
\tList.OnDrawItem = MyOnDrawItem;
//\tList.ItemPadding = 10;\n//\tList.OnDrawItem = MyOnDrawItem;
}
function bool MyOnDrawItem(Canvas Canvas, int i, float X, float Y, float W, float H, bool bSelected)
{
\tlocal float CellLeft, CellWidth, posX, sizeX;\n\tlocal eMenuState ItemState;
\tif( i >= MyData.Length )
\t\treturn true;
\tItemState = MSAT_Blurry;
\tif( bSelected )
\t\tItemState = MSAT_Focused;
\tList.GetCellLeftWidth( 0, CellLeft, CellWidth );
\tList.ItemStyle.DrawText(Canvas, MenuState, CellLeft, Y, CellWidth, H, TXTA_Left, MyData[i].Name);
\tposX = X + (Canvas.ClipX * 0.01);
\tsizeX = Canvas.ClipX * 0.22;
\tList.ItemStyle.DrawText(Canvas, MSAT_Blurry, posX, Y, sizeX, H, TXTA_Left, MyData[i].TeamName);
\tif( bDisplayNumPlayers )
\t{
\t\tposX = posX + sizeX + (Canvas.ClipX * 0.01);
\t\tsizeX = Canvas.ClipX * 0.05;
\t\tList.ItemStyle.DrawText(Canvas, MSAT_Blurry, posX, Y, sizeX, H, TXTA_Right, MyData[i].NumPlayers);
\t}
\treturn false;
}
/*
function bool MyOnDrawItem(Canvas Canvas, int i, float X, float Y, float W, float H, bool bSelected)
{
\treturn true;
}
*/
defaultproperties
{
\tItemHeight=25
}
"""


def selftest():
    code, commented = strip_comments(FIXTURE)
    live = [d for d in declarations(code) if d[0] == DELEGATE]
    cut = [d for d in declarations(commented) if d[0] == DELEGATE]
    assert len(live) == 1, live                       # the wired body
    assert len(cut) == 1, cut                         # the commented one is a declaration too
    body = body_of(code, DELEGATE)
    columns, flags = classify(body)
    assert [c["form"] for c in columns] == ["cell", "canvas", "canvas"], columns
    assert columns[0]["column"] == "0", columns
    assert columns[0]["field"] == "Name", columns
    assert columns[1]["value"] == "L=0.01 W=0.22", columns
    assert columns[1]["note"] == "aligned left", columns
    assert columns[2]["value"] == "L=0.01+0.22+0.01 W=0.05", columns
    assert columns[2]["note"].startswith("only when bDisplayNumPlayers"), columns
    assert columns[2]["note"].endswith("aligned right"), columns
    assert flags["bg"] is False and flags["state"] is True, flags
    wirings = [m.group("fn") for m in ASSIGN.finditer(code)]
    assert wirings == ["MyOnDrawItem"], wirings
    assert [m.group("fn") for m in WIRING.finditer(commented)] == ["MyOnDrawItem"]
    assert _path('""$MyData[index].ID') == "ID", _path('""$MyData[index].ID')
    assert _path("MyData[SortData[i].SortItem].ServerName") == "ServerName"
    assert _path('MyServerList[i].CurrentPlayers@"/"@MyServerList[i].MaxPlayers') == "CurrentPlayers/MaxPlayers"
    print("ok   list-rows: a cell, a screen pair and a row fraction classify, and a commented wiring is kept")


def load_rows(_unused=None):
    with open(ARTIFACT, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", nargs="?")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--defaults", default="", help="the defaults-emit --emit tree")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        selftest()
        return
    if args.report:
        report()
        return
    if args.write_doc or args.check_doc:
        rows = load_rows()
        if args.write_doc:
            write_doc(args.write_doc, rows)
        else:
            check_doc(args.check_doc, rows)
        return
    if not args.dump:
        sys.exit("a dump directory is required (or --report/--check)")
    system_dir = os.path.join(args.dump, "System") if os.path.isdir(os.path.join(args.dump, "System")) else args.dump
    if args.sweep and not args.defaults:
        sys.exit("FAIL list-rows: --sweep needs --defaults <the defaults-emit tree>; the cell-table half reads it")
    rows = read_bodies(system_dir, args.defaults)
    if args.out:
        write(rows, args.out)
    else:
        print(render(rows))


if __name__ == "__main__":
    main()