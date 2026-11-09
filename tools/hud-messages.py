#!/usr/bin/env python3
"""Which HUD class draws which message, read from the shipped `ScriptText` (PLAN 7.3, U15).

The console's `say` and `god` are delivered -- `History[]` proves it -- and only `stat fps` draws. The reason is
not the console: `PlayerController.TeamMessage` hands the string to `myHUD.Message`, and each HUD subclass decides
what `Message` does with it. On this disc there are three such subclasses and they do not agree, so the question
"does a message command draw" has three answers and a boot can only see the one HUD it happens to run.

This tool answers all three statically, from the extracted scripts (`tools/pkg-script-text.py --all --write
$WORK/UnrealScript`): for every class whose lineage reaches `Engine.HUD`, every message-point function it declares,
the calls that body makes in order (with `SetSubtitle`'s own duration), whether it calls `Super`, and whether it
reaches `TextMessages[]` -- the engine's eight-slot buffer, which only `HUD.AddTextMessage` fills and only
`HUD.DisplayMessages` draws.

    python3 tools/hud-messages.py --src $WORK/UnrealScript
    python3 tools/hud-messages.py --src $WORK/UnrealScript --class StargateHUD
    python3 tools/hud-messages.py --src $WORK/UnrealScript --sink-callers

`--sink-callers` answers the other half of U15: *which condition refuses a message that reached no window*. The
chain is measured rather than guessed -- for each sink (`SetSubtitle`, `AddNotification`, `SetNotifications`,
`DisplayTextBox`, `DisplayMessages`) it lists the classes that declare it and every body in the tree that calls it.
A sink with exactly one caller is a closed chain: nothing between that caller and the window can drop the string, so
a message that never drew did not arrive, and the question moves upstream to the console that was supposed to send
it. The same tree carries the answer for this disc: `GameHud.SetNotifications` is called only by
`NotificationManager.DisplayMessages`, and `NotificationManager.DisplayMessages` only by
`StargateHUD.DisplayMessages`.

The scripts are game-derived: this prints class, function and call names and line numbers only, never source text,
and writes no files (`-o` is deliberately absent -- the report is small and belongs in a doc, not an artifact).
`--selftest` needs no dump and is in `make ci`.
"""
import argparse
import os
import re
import sys

CLASS_DECL = re.compile(r"^\s*class\s+(\w+)(?:\s+extends\s+(\w+))?", re.IGNORECASE | re.MULTILINE)
FUNC_DECL = re.compile(r"^\s*(?:(?:simulated|static|final|latent|native|event|exec|private|protected|simulated)\s+)*"
                       r"(?:function|event)\s+(?:[\w<>\[\]]+\s+)?(\w+)\s*\(", re.IGNORECASE)
CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

# The functions a message can arrive at, and the calls that decide what happens to it. A call outside this
# vocabulary is noise (`Log`, `ClassIsChildOf`, `PlayBeepSound`) and is not reported -- the question is the sink.
POINTS = ("Message", "ClientMessage", "TeamMessage", "LocalizedMessage", "SetSubtitle", "DisplayMessages")
SINKS = ("AddTextMessage", "SetSubtitle", "AddNotification", "LocalizedMessage", "DisplayMessages",
         "DrawText", "DrawTextMessages", "DisplayTextBox")

DECLARING = "Engine.HUD"

# The message chain, in the order a string travels it. `--sink-callers` counts the bodies that call each one.
SINK_CHAIN = ("AddNotification", "SetNotifications", "DisplayTextBox", "SetSubtitle", "DisplayMessages")
SINK_CALL = re.compile(r"\b(?:\w+\s*\.\s*)?(AddNotification|SetNotifications|DisplayTextBox|SetSubtitle|"
                       r"DisplayMessages)\s*\(")


def read_text(path):
    """The file as text: the extractor writes UTF-16 with a BOM for some packages and UTF-8 for others."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace").replace("\r\n", "\n")
    return raw.decode("utf-8", "replace").replace("\r\n", "\n")


def functions(text):
    """[(name, line_start, body)] for every function/event declaration, with its brace-matched body.

    A body is matched by counting braces from the declaration's own `{`, so the one-line `if (...) { ... }` the
    decompiler emits, and the braces inside string literals, do not end it at the wrong place.
    """
    out = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        match = FUNC_DECL.match(line)
        if not match:
            continue
        at = line.find("{", match.end())
        i = index
        while at < 0 and i + 1 < len(lines):
            i += 1
            at = lines[i].find("{")
        if at < 0:
            continue
        depth, body, first = 0, [], True
        while i < len(lines):
            chunk = lines[i][at:] if first else lines[i]
            first = False
            body.append(chunk)
            depth += chunk.count("{") - chunk.count("}")
            i += 1
            if depth <= 0:
                break
        out.append((match.group(1), index + 1, "\n".join(body)))
    return out


def calls_in(body):
    """([sink calls in order], `Super` called?, touches `TextMessages`?) for one function body.

    Line comments are removed first: the shipped text carries commented-out calls (a `// AddTextMessage(...)` is
    not a message that draws) and the decompiler's `// End:0x18` annotations, so a scan over the raw body would
    report a sink the class deliberately does not reach.
    """
    code = re.sub(r"//[^\n]*", "", body)
    seen = []
    for match in CALL.finditer(code):
        name = match.group(1)
        if name in SINKS and name not in [c.split("(")[0] for c in seen]:
            seen.append(name + (subtitle_arg(code) if name == "SetSubtitle" else ""))
    sup = bool(re.search(r"\bSuper\s*[.(]", code))
    return seen, sup, bool(re.search(r"\bTextMessages\b", code))


def subtitle_arg(body):
    """`(2.0s)` for `SetSubtitle(Msg, 2.0000000, ...)` -- the duration is the whole difference between a message
    that draws for two seconds and one that never draws at all."""
    match = re.search(r"\bSetSubtitle\s*\([^,]*,\s*([0-9.]+)", body)
    return "(%ss)" % match.group(1) if match else ""


def census(src):
    """{class: (file, parent, [funcs])} over the whole extracted tree, plus the package each class came from."""
    classes = {}
    for root, _dirs, files in os.walk(src):
        for name in sorted(files):
            if not name.endswith(".uc"):
                continue
            path = os.path.join(root, name)
            text = read_text(path)
            match = CLASS_DECL.search(text)
            if not match:
                continue
            cls = match.group(1)
            classes.setdefault(cls, (os.path.relpath(path, src), match.group(2), functions(text)))
    return classes


def lineage(classes, leaf):
    """The class chain from `leaf` up to its root, by `extends` name, over the classes this tree declares.

    A parent outside the tree (`Actor`, `Object`) ends the walk rather than being listed: the tree is what the
    caller can look up, and `Engine.Actor` is not where a message route changes.
    """
    chain, seen = [], set()
    while leaf and leaf in classes and leaf not in seen:
        seen.add(leaf)
        chain.append(leaf)
        leaf = classes.get(leaf, (None, None, None))[1]
    return chain


def report(classes, only=None, out=print):
    hud = sorted(c for c in classes if DECLARING.split(".")[-1] in lineage(classes, c))
    if only:
        hud = [c for c in hud if c.lower() == only.lower()]
    out("hud-messages: %d class(es) under `%s` of %d named classes in the tree"
        % (len(hud), DECLARING, len(classes)))
    fills, draws, overrides, supers = set(), set(), [], []
    for cls in hud:
        path, parent, funcs = classes[cls]
        out("")
        out("%s  (%s extends %s)" % (cls, path, parent))
        found = [(n, line, body) for (n, line, body) in funcs if n in POINTS]
        if not found:
            out("  declares none of %s" % ", ".join(POINTS))
        for name, line, body in found:
            sinks, sup, text_messages = calls_in(body)
            out("  %-16s line %-5d %s%s%s"
                % (name, line, " -> ".join(sinks) if sinks else "(no sink call)",
                   "  + Super" if sup else "  no Super",
                   "  TextMessages[]" if text_messages else ""))
            if name == "Message":
                overrides.append((cls, sinks))
                if "AddTextMessage" in sinks:
                    fills.add(cls)
            if name == "DisplayMessages":
                draws.add(cls)
                if sup:
                    supers.append(cls)
    out("")
    out("Message overrides: %d (%s)" % (len(overrides), ", ".join(c for c, _ in overrides)))
    for cls, sinks in overrides:
        out("  %-18s %s" % (cls, " -> ".join(sinks) if sinks else "no sink call"))
    out("fills `TextMessages[]` (AddTextMessage): %s" % (", ".join(sorted(fills)) or "none"))
    out("overrides `DisplayMessages`: %s" % (", ".join(sorted(draws)) or "none"))
    out("of those, calls `Super.DisplayMessages`: %s" % (", ".join(sorted(supers)) or "none"))
    # The feed: every body that hands a string to the HUD. Without it the table says what each HUD does with a
    # message, not who gives it one -- the engine's `PlayerController.TeamMessage` is the one the console's `say`
    # arrives on, and it is in the tree, so it is measured rather than assumed.
    feeds = []
    for cls in sorted(classes):
        for name, line, body in classes[cls][2]:
            if re.search(r"\bmyHUD\s*\.\s*Message\s*\(", re.sub(r"//[^\n]*", "", body)):
                feeds.append((cls, name, line))
    out("call sites of `myHUD.Message(...)`: %d" % len(feeds))
    for cls, name, line in feeds:
        out("  %s.%s  line %d" % (cls, name, line))
    return 0


def sink_callers(classes, out=print):
    """Every call site of each message sink in the tree, and the classes that declare it.

    U15's last question was "which guard refuses the message". A guard can only refuse a string that arrived, so the
    chain's shape answers it: count the callers. A sink with one caller is closed -- nothing else in the tree can
    drop the string between that caller and the window -- and a sink with no script caller can only be driven from
    native code, which is a different measurement.

    Comments are stripped before the scan for the same reason `calls_in` strips them: the shipped text carries
    commented-out calls, and `Super.DisplayMessages(...)` counts as a caller of `DisplayMessages`.
    """
    declared = dict((name, []) for name in SINK_CHAIN)
    calls = dict((name, []) for name in SINK_CHAIN)
    for cls in sorted(classes):
        for name, line, body in classes[cls][2]:
            if name in declared:
                declared[name].append((cls, line))
            for match in SINK_CALL.finditer(re.sub(r"//[^\n]*", "", body)):
                calls[match.group(1)].append((cls, name, line))
    out("hud-messages: %d class(es) in the tree, %d sink function(s) in the chain" % (len(classes), len(SINK_CHAIN)))
    for name in SINK_CHAIN:
        sites = sorted(set(calls[name]))
        out("")
        out("%-16s declared by %s" % (name, ", ".join("%s:%d" % (c, l) for c, l in declared[name])
                                      or "no class in the tree"))
        if not sites:
            out("  no call site in the tree: only native code can call it")
            continue
        out("  %d call site(s)" % len(sites))
        for cls, fn, line in sites:
            out("    %s.%s  line %d" % (cls, fn, line))
    return 0


def selftest():
    """A synthetic tree: two HUD subclasses that disagree, one class that is not a HUD, and brace traps."""
    import tempfile
    src = tempfile.mkdtemp(prefix="hudmsg-")
    hud = "\n".join([
        "class HUD extends Actor",
        "    native;",
        "",
        "simulated function Message(PlayerReplicationInfo PRI, coerce string Msg, name msgType)",
        "{",
        "    if (true) { AddTextMessage(Msg, Class'Engine.LocalMessage', PRI); }",
        "}",
        "",
        "function DisplayMessages(Canvas C)",
        "{",
        "    local int i;",
        "    C.DrawText(TextMessages[i].Text, false);",
        "}",
        "",
        "function AddTextMessage(string M, Class<LocalMessage> MessageClass, PlayerReplicationInfo PRI)",
        "{",
        "    TextMessages[0].Text = M;",
        "}",
    ])
    plain = "\n".join([
        "class PlainHUD extends HUD",
        "    native;",
        "",
        "simulated function Message(PlayerReplicationInfo PRI, coerce string Msg, name msgType)",
        "{",
        "    if (msgType == 'Say')",
        "    {",
        "        SetSubtitle(Msg, 2.0000000, \"\", none);",
        "    }",
        "    else",
        "    {",
        "        AddNotification(Msg);",
        "    }",
        "}",
        "",
        "function DisplayMessages(Canvas C)",
        "{",
        "    local NotificationManager NM;",
        "    NM.DisplayMessages(none);",
        "}",
    ])
    other = "\n".join([
        "class NotAHud extends Actor",
        "    native;",
        "",
        "event ClientMessage(coerce string S, optional name Type)",
        "{",
        "    myHUD.Message(none, S, Type);",
        "}",
    ])
    for name, text in (("HUD", hud), ("PlainHUD", plain), ("NotAHud", other)):
        with open(os.path.join(src, name + ".uc"), "w") as fh:
            fh.write(text)
    classes = census(src)
    assert set(classes) == {"HUD", "PlainHUD", "NotAHud"}, classes
    # The lineage is `extends` names, not the file tree, and a class outside it is not reported.
    assert lineage(classes, "PlainHUD") == ["PlainHUD", "HUD"], lineage(classes, "PlainHUD")
    assert lineage(classes, "NotAHud") == ["NotAHud"], lineage(classes, "NotAHud")
    # A body is brace-matched, not line-matched: the one-line `if` block does not end `HUD.Message`.
    body = dict((n, b) for (n, _l, b) in classes["HUD"][2])["Message"]
    assert "AddTextMessage" in body and "DisplayMessages" not in body, body
    sinks, sup, text_messages = calls_in(body)
    assert sinks == ["AddTextMessage"] and not sup and not text_messages, (sinks, sup, text_messages)
    # The buffer itself is named by the *filler*, not by the message body that calls it.
    assert calls_in(dict((n, b) for (n, _l, b) in classes["HUD"][2])["AddTextMessage"])[2] is True
    # `SetSubtitle`'s duration is the fact that separates a 2 s flash from a message that never draws.
    sinks, sup, text_messages = calls_in(dict((n, b) for (n, _l, b) in classes["PlainHUD"][2])["Message"])
    assert sinks == ["SetSubtitle(2.0000000s)", "AddNotification"], sinks
    assert not sup and not text_messages, (sup, text_messages)
    # `Super` is a call on the parent, and only the shapes `Super.X(` and `Super(` count as one.
    assert calls_in("Super.DisplayMessages(C);")[1] is True
    assert calls_in("// Super.DisplayMessages is not called here")[1] is False
    # A commented-out sink is not a sink: the shipped `StargateHUD.Message` carries exactly that shape.
    sinks, sup, text_messages = calls_in("// AddTextMessage(Msg, Class'X', PRI);\nAddNotification(Msg);")
    assert sinks == ["AddNotification"], sinks
    report_out = []
    assert report(classes, out=report_out.append) == 0
    text = "\n".join(report_out)
    assert "fills `TextMessages[]` (AddTextMessage): HUD" in text, text
    assert "calls `Super.DisplayMessages`: none" in text, text
    assert "NotAHud  (" not in text, text          # not a HUD, so no table entry -- but it is a feeder
    assert "call sites of `myHUD.Message(...)`: 1" in text and "NotAHud.ClientMessage" in text, text
    # The sink chain: the declaration is not a call site, a commented-out call is not one, and a qualified call
    # (`NM.DisplayMessages`) is. `SetNotifications` is declared nowhere in this tree, so it is reported as such.
    call_out = []
    assert sink_callers(classes, out=call_out.append) == 0
    chain = "\n".join(call_out)
    assert "AddNotification  declared by no class in the tree" in chain, chain
    assert "SetNotifications declared by no class in the tree" in chain, chain
    assert "  no call site in the tree: only native code can call it" in chain, chain
    assert "PlainHUD.DisplayMessages" in chain, chain            # a qualified call is a call site
    assert chain.count("PlainHUD.Message") == 2, chain          # `SetSubtitle` and `AddNotification`, one each
    assert "declared by HUD:9, PlainHUD:16" in chain, chain     # declarations, in both HUD classes
    print("ok   hud-messages: the lineage is `extends` names, a body is brace-matched, `SetSubtitle`'s duration is "
          "read, `Super` is a call on the parent, and the class that fills `TextMessages[]` is named")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="which HUD class draws which message (shipped ScriptText)")
    parser.add_argument("--src", help="the extracted script tree (`pkg-script-text.py --all --write ...`)")
    parser.add_argument("--class", dest="only", help="report one HUD class by name")
    parser.add_argument("--sink-callers", action="store_true",
                        help="every call site of each message sink in the tree -- a sink with one caller is a "
                             "closed chain, so nothing between it and the window can drop the string")
    parser.add_argument("--selftest", action="store_true", help="synthetic tree; needs no dump")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.src:
        parser.error("--src is required (or --selftest)")
    if not os.path.isdir(args.src):
        print("hud-messages: no script tree at %s (run `pkg-script-text.py --all --write %s`)"
              % (args.src, args.src), file=sys.stderr)
        return 2
    if args.sink_callers:
        return sink_callers(census(args.src))
    return report(census(args.src), only=args.only)


if __name__ == "__main__":
    sys.exit(main())
