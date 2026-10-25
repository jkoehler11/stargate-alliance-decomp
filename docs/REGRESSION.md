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
* **No assert-dialog detection.** The emulator evidence channel today is `EIP` sampling
  (`docs/XEMU.md`), which cannot read an assert dialog off the screen.
* **No notification.** It writes a summary and exits; wiring it to mail or chat is one line the operator
  should own, not something this repository should assume.

## Failure triage

`summary.txt` names the failed stage and its log. In practice the three things that go wrong are: the dump
moved or was re-extracted (`verify-dump` fails on a hash), a generator's output changed because the code
changed (`git status` shows which CSV), or the emulator's environment lost its display (`xemu-boot` reports
`FAIL: xemu never opened its QMP socket` with the tail of `xemu.log`).
