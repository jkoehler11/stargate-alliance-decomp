#!/usr/bin/env python3
"""Does a shipped checkpoint load back into its own level, and does play resume? (PLAN 5.4, `docs/systems/CHECKPOINTS.md`)

`docs/systems/CHECKPOINTS.md` reads the nine shipped `.sgs` files as containers and names what is not measured: the
**load** side. The one attempt on record typed `checkpointload c_m02a_drillsite.sgs` from a loaded `M00` -- a
checkpoint of a different level -- and the guest fell to the kernel's `Please insert an Xbox disc...` prompt, which
is a foreign-level load and also the route the shipped scripts do not use: `RestartCheckpoint` and `SavedGameList`
both go through `PlayerController.LoadGame` -> `LoadSaveGame`, while `CheckpointLoad` issues `LoadCheckPoint`.
`tools/xemu/checkpoint-load.sh` boots one map, waits for a gameplay frame, types a **control** command and then both
spellings at the same shipped file, and shoots a frame every 30-40 s. This reads those runs back.

The witness is `map-frame.py`'s, not a new one: `StargateHUD` draws a portrait and a name in the frame's top-left
corner on every gameplay frame, that corner reads **0.140-0.986** on 65 frames that are play and **0.000-0.027** on
the 7 that are not, and the threshold is 0.06. Three readings per shot are kept here:

* `verdict` -- `hud` / `no-hud`, the same corner test `docs/MAP_FRAMES.md` is built on;
* `repeat_of` -- the earlier shot in the same run with the **same frame bytes**. A guest whose picture is
  byte-identical 30 s and 100 s after a submit is not rendering: `docs/XEMU.md` draws that line for an idle guest,
  and it is the strongest cheap evidence that the picture stopped rather than the scene went quiet;
* the `cpu=` line `drive.py` prints next to it -- `vm running|paused`, the EIP samples and how many are in the
  kernel -- so "the frames repeat" can be read against "the CPU is still executing".

    python3 tools/xemu/checkpoint-load.py --run $WORK/ckpt1 --run $WORK/ckpt2 -o inventory/checkpoint_loads.csv
    python3 tools/xemu/checkpoint-load.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/xemu/checkpoint-load.py --check-doc docs/systems/CHECKPOINTS.md
    python3 tools/xemu/checkpoint-load.py --check          # synthetic logs and digests, no frames, no writes

`--write-doc` and `--check-doc` render and compare the page's generated block from the committed CSV alone, so
`make ci` holds the page to the table without a boot. The tool reports readings; it does not decide what a reading
means -- `CHECKPOINTS.md` carries the prose, and a round's `frames` column is the count of distinct pictures after
the submit, which is a number a reader can check against the table.
"""

import argparse
import csv
import hashlib
import importlib.util
import os
import re
import sys

COLUMNS = ["run", "kind", "step", "name", "portrait", "prompt", "verdict", "repeat_of", "detail"]
BLOCK_MARK = ("<!-- generated:checkpoint-load -->", "<!-- /generated:checkpoint-load -->")
SHOT_RE = re.compile(r"^step (\d+): shot (\S+) (?:differing pixels: (\d+) \(threshold \d+\) (?:PASS|FAIL)"
                     r"|\(first frame, nothing to compare\))$")
CPU_RE = re.compile(r"^step (\d+): vm (\w+); (\d+) eip samples, (\d+) in kernel: (.*)$")
ROUND_RE = re.compile(r"^r(\d+)([a-z])-")
TSV = "runs.tsv"


# ---------------------------------------------------------------- the corner measure

def load_map_frame():
    """`map-frame.py`'s own decode and corner test, imported by path (the file name has a hyphen)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map-frame.py")
    spec = importlib.util.spec_from_file_location("map_frame", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- parsing one run

def parse_log(text):
    """[(kind, step, name-or-'', payload)] in the order `drive.py` printed them."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        match = SHOT_RE.match(line)
        if match:
            step, name, pixels = match.group(1), match.group(2), match.group(3)
            rows.append(("shot", int(step), name, pixels))
            continue
        match = CPU_RE.match(line)
        if match:
            step, status, samples, kernel, eips = match.groups()
            rows.append(("cpu", int(step), "",
                         {"vm": status, "samples": int(samples), "kernel": int(kernel), "eips": eips.split()}))
    return rows


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()[:12]


def build_run(directory):
    """One run directory's rows, and the digest each shot has, in print order."""
    text = open(os.path.join(directory, "run.log"), errors="replace").read()
    label = os.path.basename(os.path.normpath(directory))
    seen = {}          # digest -> first shot name that had it
    rows = []
    for kind, step, name, payload in parse_log(text):
        if kind == "cpu":
            detail = "%s; %d samples, %d distinct, %d in kernel: %s" % (
                payload["vm"], payload["samples"], len(set(payload["eips"])), payload["kernel"],
                " ".join(payload["eips"]))
            rows.append({"run": label, "kind": "cpu", "step": step, "name": "", "portrait": "",
                         "prompt": "", "verdict": payload["vm"], "repeat_of": "", "detail": detail})
            continue
        path = os.path.join(directory, name + ".png")
        if not os.path.exists(path):
            rows.append({"run": label, "kind": "shot", "step": step, "name": name, "portrait": "",
                         "prompt": "", "verdict": "missing", "repeat_of": "",
                         "detail": "frame gone"})
            continue
        portrait, prompt = MEASURE(path)
        again = seen.get(digest(path), "")
        if not again:
            seen[digest(path)] = name
        rows.append({"run": label, "kind": "shot", "step": step, "name": name,
                     "portrait": "%.4f" % portrait, "prompt": "%.4f" % prompt,
                     "verdict": verdict(portrait), "repeat_of": again,
                     "detail": "first frame" if payload is None else "repaint %s px" % payload})
    return rows


def verdict(portrait):
    return "hud" if portrait >= PORTRAIT_MIN else "no-hud"


# ---------------------------------------------------------------- rounds

def rounds(rows):
    """{round label: [shot rows]} for the shots the runner names `r<N><letter>-<label>`."""
    found = {}
    for row in rows:
        if row["kind"] != "shot":
            continue
        match = ROUND_RE.match(row["name"])
        if not match:
            continue
        found.setdefault("r" + match.group(1), []).append(row)
    return found


def summarise(rows):
    """Per round: the distinct pictures after the submit, and whether any of them draws the HUD.

    `r<N>a` is the frame taken with the command typed and not yet submitted; every later shot in the round is
    after the `ret`. The two numbers this returns are counts of readings, not a verdict.
    """
    out = []
    for label in sorted(rounds(rows)):
        shots = rounds(rows)[label]
        typed = [s for s in shots if ROUND_RE.match(s["name"]).group(2) == "a"]
        after = [s for s in shots if ROUND_RE.match(s["name"]).group(2) != "a"]
        pictures = {s["repeat_of"] or s["name"] for s in after}
        out.append({
            "round": label,
            "typed": typed[0]["name"] if typed else "",
            "after": len(after),
            "frames": len(pictures),
            "hud": sum(1 for s in after if s["verdict"] == "hud"),
            "cpus": [r for r in rows if r["kind"] == "cpu" and r["step"] > (typed[0]["step"] if typed else 0)],
        })
    return out


# ---------------------------------------------------------------- CSV and page

def write_csv(rows, path):
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in COLUMNS})


def read_csv(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def render(rows):
    """The page's block: one line per run, one table of every shot, one table of the CPU samples."""
    runs = []
    for row in rows:
        if row["run"] not in runs:
            runs.append(row["run"])
    lines = []
    lines.append("| run | round | frames after the submit | of those, pictures | drawing the HUD |")
    lines.append("|---|---|---:|---:|---:|")
    for label in runs:
        group = [r for r in rows if r["run"] == label]
        for row in summarise(group):
            lines.append("| `%s` | `%s` | %d | %d | %d |"
                         % (label, row["round"], row["after"], row["frames"], row["hud"]))
    lines.append("")
    lines.append("| run | shot | corner | prompt | read | note |")
    lines.append("|---|---|---:|---:|---|---|")
    for row in rows:
        if row["kind"] != "shot":
            continue
        note = row["detail"]
        if row["repeat_of"]:
            note = "same bytes as `%s`" % row["repeat_of"]
        lines.append("| `%s` | `%s` | %s | %s | %s | %s |"
                     % (row["run"], row["name"], row["portrait"] or "-", row["prompt"] or "-",
                        row["verdict"], note))
    cpu = [r for r in rows if r["kind"] == "cpu"]
    if cpu:
        lines.append("")
        lines.append("| run | step | vCPU | samples | distinct | in kernel |")
        lines.append("|---|---:|---|---:|---:|---:|")
        for row in cpu:
            bits = row["detail"].split("; ")
            counts = bits[1].split(", ") if len(bits) > 1 else ["", "", ""]
            lines.append("| `%s` | %s | %s | %s | %s | %s |"
                         % (row["run"], row["step"], bits[0],
                            counts[0].split(" ")[0], counts[1].split(" ")[0],
                            counts[2].split(" ")[0]))
    return "\n".join(lines) + "\n"


def splice(text, block):
    """Replace what sits between the block markers, and refuse a page that has lost one of them."""
    start, end = BLOCK_MARK
    head, sep, rest = text.partition(start)
    if not sep:
        sys.exit("no %s in the page" % start)
    _old, sep, tail = rest.partition(end)
    if not sep:
        sys.exit("no %s in the page" % end)
    return head + start + "\n" + block.rstrip("\n") + "\n" + end + tail


def write_doc(path, rows):
    """Render first, write second: `splice` exits when a marker is missing, and opening the page for writing before
    calling it truncated a page to zero bytes when this tool was first driven by hand."""
    text = open(path, errors="replace").read()
    body = splice(text, render(rows))
    with open(path, "w") as handle:
        handle.write(body)
    print("wrote %s from %d rows" % (path, len(rows)))


def check_doc(path, rows):
    text = open(path, errors="replace").read()
    want = splice(text, render(rows))
    if want != text:
        print("FAIL --check-doc: %s does not render from the CSV" % path)
        return 1
    print("ok   checkpoint-load: %s matches the table" % path)
    return 0


# ---------------------------------------------------------------- self-test

def selftest():
    """Two synthetic logs that differ only in what the frames did, plus the round arithmetic on them."""
    failures = []

    def log(pixels, repeated):
        lines = [
            "step 40: shot 03-gameplay differing pixels: 595 (threshold 318) PASS",
            "step 90: shot r1a-typed differing pixels: 27 (threshold 318) FAIL",
        ]
        for index, value in enumerate(pixels):
            lines.append("step %d: shot r1b-%d differing pixels: %d (threshold 318) %s"
                         % (100 + index, 30 * (index + 1), value,
                            "PASS" if value >= 318 else "FAIL"))
        lines.append("step 200: vm running; 6 eip samples, 2 in kernel: 0x0001b300 0x0001b300 "
                     "0x0001b300 0x0001b300 0x0001b300 0x0001b300")
        return "\n".join(lines) + "\n"

    parsed = parse_log(log([3204, 0, 0], True))
    if [row[0] for row in parsed] != ["shot", "shot", "shot", "shot", "shot", "cpu"]:
        failures.append("parse_log: kinds %s" % [row[0] for row in parsed])
    shots = [row for row in parsed if row[0] == "shot"]
    if shots[3][3] != "0":
        failures.append("parse_log: pixels %r" % (shots[3][3],))
    cpu = [row for row in parsed if row[0] == "cpu"][0]
    if cpu[3]["kernel"] != 2 or len(cpu[3]["eips"]) != 6:
        failures.append("parse_log: cpu payload %r" % (cpu[3],))

    # round arithmetic: a round with three post-submit shots and one picture versus three pictures are different
    def rounds_from(digests, verdicts):
        rows = [{"run": "t", "kind": "shot", "step": 1, "name": "r1a-typed", "portrait": "0.2171",
                 "prompt": "0.0000", "verdict": "hud", "repeat_of": "", "detail": ""}]
        for index, (digest_, verdict_) in enumerate(zip(digests, verdicts)):
            rows.append({"run": "t", "kind": "shot", "step": 10 + index, "name": "r1" + "bcd"[index] + "-x",
                         "portrait": "0.2000" if verdict_ == "hud" else "0.0000", "prompt": "0.0000",
                         "verdict": verdict_, "repeat_of": "" if digest_ is None else "r1b-30", "detail": ""})
        return rows

    still = summarise(rounds_from(["a", "a"], ["no-hud", "no-hud"]))
    moving = summarise(rounds_from([None, None], ["hud", "hud"]))
    if (still[0]["after"], still[0]["frames"], still[0]["hud"]) != (2, 1, 0):
        failures.append("summarise: still round %r" % (still[0],))
    if (moving[0]["after"], moving[0]["frames"], moving[0]["hud"]) != (2, 2, 2):
        failures.append("summarise: moving round %r" % (moving[0],))

    # the page splice keeps everything outside the markers and refuses a page without them
    page = "before\n" + BLOCK_MARK[0] + "\nold\n" + BLOCK_MARK[1] + "\nafter\n"
    if splice(page, "new\n") != "before\n" + BLOCK_MARK[0] + "\nnew\n" + BLOCK_MARK[1] + "\nafter\n":
        failures.append("splice: text outside the markers did not survive")

    if failures:
        for line in failures:
            print("FAIL --check: " + line)
        return 1
    print("ok   checkpoint-load: %d log rows parsed, two round shapes told apart, the page splice tested"
          % len(parsed))
    return 0


# ---------------------------------------------------------------- main

MEASURE = None
PORTRAIT_MIN = None


def main():
    global MEASURE, PORTRAIT_MIN
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="append", default=[], help="a checkpoint-load.sh run directory")
    parser.add_argument("-o", "--output", help="write the CSV here")
    parser.add_argument("--write-doc", help="render this page's generated block from the CSV")
    parser.add_argument("--check-doc", help="refuse a page whose generated block does not match the CSV")
    parser.add_argument("--check", action="store_true", help="synthetic self-test: no frames, no writes")
    args = parser.parse_args()

    if args.check:
        return selftest()

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    source = args.output or os.path.join(root, "inventory", "checkpoint_loads.csv")

    if args.write_doc or args.check_doc:
        if not os.path.exists(source):
            sys.exit("%s: no such table" % source)
        rows = read_csv(source)
        if args.check_doc:
            return check_doc(args.check_doc, rows)
        write_doc(args.write_doc, rows)
        return 0

    if not args.run:
        parser.error("one of --run, --write-doc, --check-doc or --check is required")

    module = load_map_frame()
    MEASURE = module.measure
    PORTRAIT_MIN = module.PORTRAIT_MIN
    rows = []
    for directory in args.run:
        if not os.path.isdir(directory):
            sys.exit("%s: no such run directory" % directory)
        rows.extend(build_run(directory))
    write_csv(rows, args.output or os.path.join(root, "inventory", "checkpoint_loads.csv"))
    print("wrote %s: %d rows over %d run(s)" % (source, len(rows), len(args.run)))
    for row in summarise(rows):
        print("  %s: %d frames after the submit, %d picture(s), %d drawing the HUD"
              % (row["round"], row["after"], row["frames"], row["hud"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
