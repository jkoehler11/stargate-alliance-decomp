# Nightly regression

PLAN 7.5 asks for a nightly that rebuilds and boots, failing on any new assert dialog or missing class.
Most of that needs a compile loop that does not exist yet (3.6), but the part that *can* run today is worth
running on a timer, because it is the part nobody remembers to do: the checks CI is forbidden to make.

    tools/nightly.sh                                        # the dump-free gate only
    SG1_DUMP=~/sg1/dump tools/nightly.sh                    # + manifest and the dump-side generators
    SG1_DUMP=~/sg1/dump tools/nightly.sh --xemu \
        --iso=$WORK/sg1.iso --bios=$SG1_DUMP/bios --hdd=$WORK/hdd.qcow2    # + the boot test

Each run writes `build/nightly/<UTC timestamp>/` with one log per stage plus `summary.txt`, keeps the last
30 runs, and exits non-zero if any stage failed. `build/` is git-ignored, so a run never dirties the tree.

## Why this exists when there is CI

`AGENTS.md` forbids CI from downloading the game, so `make ci` checks only the committed artifacts: the
symbol map regenerates, the docs' counts match, addresses land inside the image's sections, the status table
is current. Everything that needs the dump — manifest verification, the inventory generators, the Ghidra
run — is a local step, and the emulator boot cannot run in CI at all.

That leaves a real gap: a commit can be green in CI and break the local pipeline, and nobody notices until
someone runs the heavy stages by hand weeks later. The nightly closes it on whatever machine owns the dump.

## What it runs

| stage | command | needs |
|---|---|---|
| `dump-free-gate` | `make ci` | nothing |
| `verify-dump` | `make verify-dump` | `SG1_DUMP` |
| `class-tables` | `make class-tables` | `SG1_DUMP` |
| `libraries` | `make libraries` | `SG1_DUMP` |
| `xemu-boot` | `tools/xemu/smoke.py` | `--iso`, `--bios`, `--hdd`, a display |
| `missions` | `tools/xemu/missions.sh OUTDIR INDEX...` | the same, plus `--missions`; indices rotate by day of year (`SG1_MISSION_COUNT`, default 2), `SG1_MISSION_INDICES` overrides |

Stages that cannot run say `SKIP` and are not failures — an unset `SG1_DUMP` on a laptop is not a regression.

It reports `git status --porcelain` at the end rather than repairing anything: if a dump-side generator
rewrites a tracked CSV, that is drift a human has to see and commit, and the nightly's job is to surface it.

## Scheduling it

Any timer will do; the point is that it is not a person remembering.

```sh
# crontab -e  (04:15 local, weekdays)
15 4 * * 1-5 cd $HOME/Projects/stargate-alliance-decomp && SG1_DUMP=$HOME/sg1/dump tools/nightly.sh >> $HOME/sg1/nightly.log 2>&1

# or a systemd timer, if the machine already has one for the Ghidra box
```

The xemu stage needs a graphical session (`DISPLAY`/`XAUTHORITY`), so it belongs to the desktop timer rather
than a headless one — the same constraint `docs/XEMU.md` records for the boot test itself.

## What it does not do yet

* **No rebuild.** 3.6 does not exist, so the "rebuild then boot then fail on a new assert" loop is still
  the goal rather than the behaviour. When it lands, it slots in as a stage between `verify-dump` and
  `xemu-boot` without changing anything else here.
* **No assert-dialog detection, and there is no assert dialog to detect.** `appAssertFailed` logs and returns
  (`docs/NATIVE_NOTES.md`, U6), so a failed load does not put a dialog up -- it puts the guest back on the BIOS screen.
  That *is* detectable and is now checked: `tools/xemu/guest-halt.py` scores a frame (99.7% black, `p99` 13, against
  15-20% black and `p99` 192+ for a running game) and `missions.sh` fails an index whose mission frame is the BIOS
  screen (`docs/XEMU.md`, 2026-11-04).
* **No notification.** It writes a summary and exits; wiring it to mail or chat is one line the operator
  should own, not something this repository should assume.

## Failure triage

`summary.txt` names the failed stage and its log. In practice the three things that go wrong are: the dump
moved or was re-extracted (`verify-dump` fails on a hash), a generator's output changed because the code
changed (`git status` shows which CSV), or the emulator's environment lost its display (`xemu-boot` reports
`FAIL: xemu never opened its QMP socket` with the tail of `xemu.log`).

## A real run, and the five things it found

`tools/nightly.sh` had never been run on a machine that has the game, and the first run — `SG1_DUMP=~/sg1/dump`
against the full dump — failed five ways. All five were the harness or the tree, not the project:

| failure | cause | fix |
|---|---|---|
| `tools/nightly.sh: Permission denied` | every script under `tools/` was committed mode `100644` | they are `100755` now; the documented `tools/nightly.sh` invocation works |
| `verify-dump` failed after 224 s | the unlisted-file check counted `bios/*.bin` and `bios/xbox_hdd.qcow2` — emulator files kept beside the dump — as part of the dump | `MANIFEST_ROOTS` scopes the check to the dump's own directory; they print `IGNORED` and the stage runs in 19 s |
| `savegame-paths` differed at line 2 | the committed artifact predated the tool's current row order | regenerated (16 rows, same content) |
| `content-census` crashed on `maps.csv` | the stage passed an empty scratch directory; the census reads `maps.csv`, `map_actors.csv` and `classes.csv` *from its output directory* | the stage seeds them from the committed inventory and compares the product — a freshness check, as intended |
| `token-map` killed the run with `ORACLE_DIR: unbound variable` | `set -u` plus an optional variable | defaults declared at the top |
| `rebuild` exited 127 | `stage rebuild SG1_DUMP=… tools/build.sh` ran `SG1_DUMP=…` as a command | `env` introduces the environment |

With those in, the same run is **19 of 19 stages PASS** (`RESULT PASS`), including `rebuild`, which walks the pipeline
in `--dry-run` and reports what each stage would do.

The lesson worth keeping: a harness nobody runs is a harness that does not work. Every one of the five was in the
harness's own plumbing, and each had been documented as working in `PLAN` 7.5 since it was written.

## The boot test in a nightly run

`--xemu` adds the emulator boot, and it works — **20 of 20 stages PASS**, `xemu-boot` in 140 s — but only with two
prerequisites that cost a run each to find:

* **A display.** xemu needs X11 or Wayland; over plain ssh it dies with `Failed to initialize SDL video subsystem:
  wayland,x11 not available` and the smoke test reports that it never opened its QMP socket. Run it from the
  logged-in session:

      DISPLAY=:0 XAUTHORITY=/run/user/1000/xauth_XXXX SG1_DUMP=~/sg1/dump \
          tools/nightly.sh --xemu --iso=$WORK/sg1.iso --bios=$SG1_DUMP/bios --hdd=$WORK/hdd.qcow2

* **A free HDD image.** A second xemu holding the qcow2 makes the boot stage fail with
  `Failed to get "write" lock ... Is another process using the image?` — the guest never starts, so the run reports a
  boot failure that is really a lock conflict. `pkill -f "[x]emu"` (the bracket keeps the pattern from matching the
  pkill's own command line) before a run.

What the boot stage proves is unchanged and now measured in a full run: the guest executes game code — six EIP samples,
100% in user space, six distinct addresses — which is the evidence channel described in `docs/XEMU.md`.

## A full run with the mission stage, measured (2026-11-04)

`tools/nightly.sh --xemu --missions --mission-indices="10 11"`, from the logged-in session, `SG1_DUMP` at the dump
root:

    RESULT PASS
    23 of 23 stages PASS
    rebuild      38 s
    xemu-boot   141 s
    missions   1129 s      index 10 PASS after 2 attempts, index 11 PASS after 1
    tracked files modified by this run: 0

The last line is the property that makes the nightly usable as a gate: the run writes only into `build/nightly/<stamp>/`
and the git-ignored work tree, so a pass means the repository is unchanged as well as correct.

**The mission stage is the expensive one** — 1,129 s for two indices, because each boot waits 135 s for the title and
60 s to settle, and a swallowed press costs a whole second attempt. It therefore stays a deliberate subset, but the
subset now **rotates**: with no `SG1_MISSION_INDICES` the stage asks `tools/xemu/map-index.py --rotate 2` for the day's
pair, which walks the campaign range **10-70** with stride 2 from a day-of-year start. Two indices a night covers all 61
campaign entries in 31 nights, and because 2 is coprime with 61 the wrap skips nothing (checked: 31 nights, 61 unique
indices, 10 through 70). The range stops at 70 because 71 and 72 are `MainMenu` and `SGCTF-AbydosVsSGC` -- list entries,
not missions -- and `map-index.py --verify` refuses a table where those two look like campaign maps. `SG1_MISSION_COUNT`
changes the per-night count and `SG1_MISSION_INDICES` still pins an exact list; `tools/xemu/missions.sh --all` remains
the full 61-index sweep to run by hand (about four hours at the measured ~4 min per index).
Pixel-level assertion now exists in that stage: `tools/xemu/guest-halt.py` fails an index whose mission frame is the
BIOS screen, which is what a failed load looks like in this build (`docs/XEMU.md`, 2026-11-04).

