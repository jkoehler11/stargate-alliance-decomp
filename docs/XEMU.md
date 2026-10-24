# Boot verification with xemu

Status: **xemu installed, blocked on firmware.**

## What is in place

- xemu `0.8.139` (debug AppImage) on the desktop at `~/tools/xemu.AppImage`; it starts and reads its
  settings from `~/.local/share/xemu/xemu/`, so it can run with `SDL_VIDEODRIVER=dummy` for a
  headless smoke test of the emulator itself.
- The dump is staged on the desktop's NAS mount at `~/nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15`
  (outside this repository, never committed): verified **4,006 files / 2.2 GB**, `default.xbe`
  present, so xemu can take the directory as its DVD image with no copying at test time.
- `tools/xemu/boot-test.sh` runs the emulator with the debug-kit configuration the plan requires
  (128 MB, serial log to a file, DVD path = the game directory) and reports whether the guest stayed
  alive for N seconds.

## What is missing, and why it is not downloadable

xemu needs the console's own firmware:

| File | Size | Why it cannot be fetched here |
|---|---|---|
| `mcpx_1.0.bin` | 512 B | Microsoft's MCPX boot ROM — proprietary firmware, no legal download |
| flash ROM (`complex_4627.bin`, `Xbox_4627_v1.0.bin`, …) | 256 KB / 512 KB | same: a dump of the console's flash, model-specific |
| `xbox_hdd.qcow2` | n/a | xemu creates a blank one on first boot, so this is not a blocker |

Searched for existing firmware on 2026-10-23, in this order:

| Where | Found |
|---|---|
| desktop `$HOME` (any `mcpx*`, 200–700 KB `.bin`, `.qcow2`) | nothing (only an unrelated Windows VM image) |
| `/volume1/nfs/Emulation/bios` (Batocera-style multi-system set) | PS2/Saturn/NeoCD/arcade BIOS only — **no Xbox firmware** |
| `/volume1/nfs/Emulation/storage/xemu` | `xbox_hdd.qcow2` (4.5 MB) and `eeprom.bin` (256 B) — evidence xemu was set up before, but not the boot ROMs |
| `/volume1/nfs/romm/library/bios` | arcade, dc, dreamcast, gba, n3ds, nds, ps2, switch — **no `xbox`** |
| `/volume1` (whole NAS, depth 7, `mcpx*` / `complex_*.bin` / `xbox*.bin`) | nothing |

So an Xbox ROM library and an xemu HDD image exist on the NAS (`roms/xbox/Jet Set Radio Future`) but
the two firmware files are not on it. They are either on the device that previously ran xemu (a Deck
or a Batocera box) or were removed.

To unblock, drop them into `~/nfs/sg1-dump/bios/` on the desktop — `boot-test.sh` takes that directory
as its second argument, so the run needs no other change.

This project will not download, mirror or commit firmware. Supplying it is the owner's call under
`docs/LEGAL.md` ("do not commit emulator BIOS files"), and the file has to come from a console the
user owns — or the user points at one they already have.

## Boot verification: PASSED (2026-10-23)

The prototype boots to its main menu in xemu. The working combination is:

| Setting | Value |
|---|---|
| BIOS / MCPX | **debug flash** `bios_debug_4627.bin` (1,048,576 B) + `mcpx_1.0.bin` |
| Memory | 128 MB (debug-kit size) |
| Disc | XISO with the **certificate media patch** applied (`media=DVD-X2`, `region=NA`, `version=1`) |
| HDD | `xbox_hdd.qcow2` present (empty image is enough) |

Frame evidence (captured, then deliberately **not** committed — it is game imagery):
the engine-rendered main menu with `MAIN MENU`, `Continue` highlighted, `Single Player`,
`Multi-Player`, `Options`, `Extras`, and the build stamp **`Build: Dec 15 2005 12:02:54`** in the
bottom corner, which matches the release date of this prototype. Screenshot lives outside the repo at
`~/sg1-xemu/shots/`.

Two things this settles:

1. **PLAN 7.1/7.2 first pass**: the dump is bootable, so the rebuild track has a working harness.
2. **The debug BIOS was the missing piece, not the disc**: with the retail flash the kernel refused the
   disc; with the debug flash and the media-patched certificate the same disc boots. Both parts are
   needed — the debug flash alone still showed the insert-disc prompt with the unpatched image, and the
   patched image alone did not boot under the retail flash.

## Reading guest memory: the GDB stub (used for `GNatives`)

xemu exposes a QEMU-style GDB stub with `-s` (port 1234), which is how the native dispatch table was
read (PLAN 4.3/4.4, `docs/NATIVE_VM.md`). The run needs the graphical session's environment, not
`DISPLAY=:0`: the desktop is Wayland, so

```sh
export XDG_RUNTIME_DIR=/run/user/$(id -u) WAYLAND_DISPLAY=wayland-1
APPIMAGE_EXTRACT_AND_RUN=1 ~/tools/xemu.AppImage -config_path ~/sg1-xemu/drive5.toml -s &
# wait for the stub, then let it boot to the main menu
for i in $(seq 60); do ss -ltn | grep -q ':1234 ' && break; sleep 1; done; sleep 55
gdb -batch -ex "set architecture i386" -ex "target remote localhost:1234" \
    -ex "dump binary memory runtime.bin 0x6313e8 0x6353e8" -ex detach
```

`drive5.toml` is the verified configuration from the table above: `bios_debug_4627.bin`, the
media-patched XISO and 128 MB. Two things that cost time and are worth knowing:

- `DISPLAY=:0` over ssh fails with "Authorization required, but no authorization protocol specified";
  the Wayland socket matters, not X11.
- Attaching stops the guest. Dump and `detach` in the same `gdb` invocation, so the run resumes and can
  be killed cleanly afterwards.

The dump is guest memory (game-derived): keep it under `$WORK`, commit only the derived address map
(`inventory/gnatives_slots.csv`, `symbols/xbe_dispatch.csv`).

## Input automation: open problem

Driving the menu (to reach `Continue` → mission load) is not working yet, and the failure mode is
informative: **host hotkeys work, guest input does not.** F12 (screenshot) is handled by xemu itself
and always fires, while nothing that should reach the Xbox reaches it.

Tried, all with the window focused (`xdotool getwindowfocus` confirms the xemu window):

| Attempt | Result |
|---|---|
| `xdotool key --window <id> a` (synthetic XSendEvent) | no change; F12 in the same batch still works |
| `xdotool key a` (XTEST, focused window) | no change |
| `[input.bindings] port1 = 'keyboard'` + `background_input_capture = true`, keys `a` / `arrow` / `Return` | no change |
| `uinput` virtual pad (`0x045e:0x028e`, full ABS axes + buttons), A / D-pad Down injected | no change |

Two hypotheses for the next attempt:

1. **Bind by device GUID rather than by the string `keyboard`.** `config_spec.yml` takes
   `[input.bindings] port1 = <string>`; xemu's own code uses the literal `"keyboard"` as the GUID for
   the SDL keyboard, and it logs no "Connected … to port N" line in any run here, so the bind may
   simply not be happening.
2. **`Continue` may legitimately do nothing on this setup.** The HDD image is empty, so there is no
   save to continue from; pressing A on an inert entry would look exactly like lost input. Testing
   this needs `Single Player` selected instead (one D-pad press), which is the path the release notes
   say is broken — so it is the *right* one to test for the chapter-list claim.

The runs also explain why the shortcuts above were doomed in this session: `python-evdev` as the
desktop user sees exactly **one** input device — a stale virtual "Microsoft X-Box 360 pad"
(`/dev/input/event11`, no ABS axes, no gamepad buttons) left over from an earlier injection attempt.
xemu opened that device (its `/proc/<pid>/fd` lists `event11` and the new `event26`), and a pad with
no axes and no recognised button map translates to no controller input at all. The real keyboard is
not visible to `evdev` from this user either, so xemu's keyboard-as-gamepad has nothing to read
beyond SDL window events — which is why F12 works and nothing else does.

**A real controller on the desktop is the cheapest way to settle it**, and `tools/xemu/play.sh`
launches the verified configuration interactively for exactly that: press `Continue`, press F12 for a
capture, and the screenshot lands in `$WORK/shots`. Until then, 7.2's menu-driven checks are blocked
on input, not on the emulator.

## Earlier runs (kept for the debugging trail)

Firmware arrived (`mcpx_1.0.bin`, `Complex_4627.bin`, `xbox_hdd.qcow2`) and the emulator boots:

| Step | Result |
|---|---|
| xemu starts with MCPX + flash + HDD at 128 MB | yes — window `xemu \| v0.8.139 Debug`, guest reaches the shell |
| Guest framebuffer (F12 capture) | **"Please insert an Xbox disc…"** — the kernel runs, no disc mounted |
| XISO built from the dump (`tools/xemu/build-xiso.sh`) | 2,258,370,560 bytes, attachable as `dvd_path` |
| Boot with the disc attached | **still "Please insert an Xbox disc…"** |

Why the disc is refused — the XBE certificate answers it:

```
titleid=0x4a570006  name='Stargate SG-1'  media=0x00000000  region=0x00000000  version=0x00000000
```

Media types, region and version are all zero. That is a **debug-kit build**: it was never licensed
for a retail disc, and a retail flash ROM checks exactly those fields before booting. `extract-xiso`'s
automatic media patching does not touch an all-zero certificate — verified by re-reading the XBE
inside the produced ISO (still `media=0x0`).

A manual certificate patch (`media=DVD-X2`, `region=NA`, `version=1`) was applied to a copy of the ISO
and did not reach a title screen in two attempts. That is expected: the certificate sits inside the
RSA-signed region, so a retail kernel rejects the modified XBE for the same reason it rejects the
original. **The clean fix is a debug BIOS** (the dump came from a debug kit, and that is what it ran
on), not a patched certificate.

## Running it

```sh
# on the desktop
~/nfs/Projects/stargate-alliance-decomp/tools/xemu/boot-test.sh \
    ~/nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15 \
    ~/xbox-bios \
    120
```

`boot-test.sh` fails fast with a clear message if `mcpx*.bin` or a flash ROM is absent, so a wrong
path never looks like a crash.

## What the boot test can and cannot prove

- **Can**: the emulator reaches the game (title screen survives N seconds), the serial log shows the
  engine's own output, and a crash/assert surfaces as the process exiting early.
- **Cannot (yet)**: a screenshot. xemu's screenshot path is a UI action, and a headless run has no
  window; adding `-display none` style capture or a KVM frame capture is a follow-up under 7.2.
- The specific claim this test should settle first is the chapter-list one from
  `docs/CUT_CONTENT.md`: the campaign menu should fail to load its maps because
  `ChapterList.ini` names `M02AX.unr` etc. while the build ships `M02A.unr`.

## What the harness actually does (measured 2026-10-24)

Three things in the scripts above did not survive the pinned emulator, and each cost a boot to find:

| symptom | cause | fix now in the repo |
|---|---|---|
| `AppRun: -mcpx_path: invalid option`, then no guest | xemu 0.8.139 forwards everything after `-display xemu` to QEMU, which has no `-mcpx_path` | `tools/xemu/smoke.py` writes the TOML form `play.sh` uses; `boot-test.sh` is marked stale |
| `AppRun: -serial <path>: '<path>' is not a valid char driver` | QEMU's `-serial` wants a protocol: `file:<path>` | `smoke.py` passes `file:…` |
| `extract-xiso produced no image`, exit 0 | extract-xiso 2.7.1 takes `-c <dir> [name]`, not `-o <path>`; the old form is ignored silently | `build-xiso.sh` tries the positional form first |

With those three fixed the boot gets all the way to a live guest: the ISO builds (2,258,370,560
bytes from the 4,081-entry dump), xemu opens a GL context (Mesa 26.2.5) under `DISPLAY=:0`, starts the
machine with `-bios bios_debug_4627.bin -m 128` and the disc mounted, and the process stays alive for
the whole window.

**What is still missing is an evidence channel, not a boot.** The serial log is created and stays
**0 bytes**: this build does not write engine output to the debug channel that xemu exposes. So the
smoke test can prove "xemu started and kept running", but not "the guest reached the menu". The next
step is a framebuffer capture instead of a log: run with `-monitor unix:<sock>` and issue
`screendump <file.ppm>` (or F12 under the GL window), then assert on the pixels/text. Until that
exists, PLAN 7.2 stays half-done, and the "reaches the main menu" claim earlier in this file rests on
a screenshot taken by hand, not on a harness.

`tools/xemu/smoke.py` is the harness that got that far, and it reports exactly what it saw:

```
serial: 0 bytes, process was alive after 100s
   Log:                         absent
   Build:\s*\w{3}\s+\d+\s+\d{4} absent
FAIL: serial log has no engine output
```

