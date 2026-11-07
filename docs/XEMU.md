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

Five things in these scripts did not survive the pinned emulator, and each one cost a boot to find:

| symptom | cause | fix |
|---|---|---|
| `AppRun: -mcpx_path: invalid option`, no guest | xemu 0.8.139 forwards everything after `-display xemu` to QEMU, which has no `-mcpx_path` | `tools/xemu/smoke.py` writes the TOML form `play.sh` uses; `boot-test.sh` is marked stale |
| `-serial <path>: not a valid char driver` | QEMU's `-serial` wants a protocol | pass `file:<path>` |
| `extract-xiso produced no image`, exit 0 | extract-xiso 2.7.1 takes `-c <dir> [name]`, not `-o <path>`; the old form is silently ignored | `build-xiso.sh` tries the positional form first |
| `screendump` → `CommandNotFound` over QMP | this QEMU build carries no display commands; xemu's renderer is not a QEMU console | not available — do not plan around host screenshots |
| machine boots but the CPU sits at `EIP=0x8001d1ea`, `HLT=1`, forever | a **zero-filled `eeprom_path`**; a freshly created HDD image does the same | **omit `eeprom_path`** so xemu writes its own default, and boot from a prepared HDD image |

The last one is the important one: both the XISO and a directory mount idle in the kernel with a blank
EEPROM, and both come alive without it, so the EEPROM — not the media form — was the blocker. Verified
with `tools/xemu/smoke.py` on 2026-10-24:

```
vm status: running
waiting 110s for the title to come up
eip samples: 8, in-kernel: 1, user-space: 7 (88%), distinct user: 6
   addresses: 0x0012ce9c, 0x00463864, 0x0004704c, 0x00457d57, 0x001eaab0, 0x8001ce8a, 0x00457a67, 0x00463864
PASS: the guest is executing game code
```

## The evidence channel is the CPU, not a screen

Neither channel one would reach for first exists here: the serial log is created and stays **0 bytes**,
and `screendump` is missing from QMP. So the smoke test asserts on execution instead — it samples `EIP`
over the boot window and requires that the CPU leaves the kernel's address space, lands in the image's
own range, and varies between samples:

* `in-kernel` = `EIP >= 0x80000000`; a run that never leaves it is a machine that booted and did
  nothing (exit 2, with the EEPROM and HDD hints printed).
* PASS needs three distinct user-space addresses and at least a quarter of the samples out of kernel.

That is weaker than "the menu is on screen": it cannot tell the title screen from a frame-buffer loop.
It does prove the difference between "xemu is alive" and "the title is running", which is the regression
this test exists to catch. Anything stronger needs pixel capture from inside xemu (F12 writes to
`screenshot_dir` when a window is focused, which needs X input automation) or a guest-side hook such as
the GDB stub.

Loading a mission is still a button press nothing here can make, so PLAN 7.2 covers boot only; the
mission-load half needs input automation and stays open.

## Tracing guest execution

QEMU's `-d in_asm -D <file>` is accepted by xemu 0.8.139 — unknown options are forwarded to QEMU — and would
give the first translated block of every region, which is the direct way to prove an entry point. It is unusable
on a host without a live display, because xemu's SDL init runs before QEMU's tracing: with no session it fails
with `wayland,x11 not available` (and with an Xauthority we cannot authorise, `Authorization required`), and no
log is written at all. Run it from a desktop session, not over a detached SSH.

## Running it unattended: three things measured the hard way

Everything below was observed on the desktop (Arch, KDE on Wayland with Xwayland) while trying to extend the boot
assertion from "the CPU left kernel space" to "this screen is up".

**1. The GL context needs the session's Xwayland cookie, not `$DISPLAY` alone.** An ssh command has no `DISPLAY`,
and exporting `DISPLAY=:0` on its own fails with `Authorization required, but no authorization protocol specified` /
`Failed to initialize SDL video subsystem`. The working set is read out of the live session:

```
ps aux | grep '[X]wayland'      # e.g. Xwayland :0 -auth /run/user/1000/xauth_DQpnsX
export DISPLAY=:0 XAUTHORITY=/run/user/1000/xauth_DQpnsX
export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000
export APPIMAGE_EXTRACT_AND_RUN=1
```

With that, xemu starts headless-with-a-session and reports `GL_VERSION: 4.6 (Core Profile) Mesa …`.

**2. `dvd_path` must be a regular file.** Pointing it at the dump *directory* does not mount it: xemu builds
`-drive index=1,media=cdrom,file=<dir>` and QEMU answers `warning: 'file' driver requires '<dir>' to be a regular
file`, so the guest boots with no disc and idles at the shell — which reads exactly like the EEPROM trap in the
pitfalls below. A directory only works through `tools/xemu/smoke.py --dvd-dir` if a XISO is built first
(`tools/xemu/build-xiso.sh GAME_DIR OUT.iso`, needs `extract-xiso`, which the script builds). No XISO exists on the
desktop today, so a title-screen run needs that step first.

**3. The memory-dump route is unproven on this build.** `human-monitor-command` works for `info registers` (that is
what `smoke.py` samples), but `pmemsave 0 0x4000000 FILE` and `memsave 0 0x4000000 FILE` both answer
`invalid char 't' in expression` — the command parses the path as part of the expression. Whether a quoted form, a
different monitor command, or QMP's `human-monitor-command` in another form can dump guest RAM is **not established**;
until it is, the CPU remains the only assertion this harness can make, and "which screen is up" stays unmeasured.

## Reading guest memory over the GDB stub

The CPU is not the only evidence channel after all. `xemu -gdb tcp::PORT` opens a QEMU GDB stub, and
`tools/xemu/mem.py` uses it to read guest **physical** memory — which is what a boot assertion needs in order to say
*which screen is up* rather than only that the CPU left kernel space.

**The trap, measured:** every memory read fails with `E14` until the stub is told to use physical addresses.

    >>> m,0x100000,0x40            # without Qqemu.PhyMemMode:1
    E14
    >>> Qqemu.PhyMemMode:1
    OK
    >>> m,0x100000,0x40
    000000ff000000ff...

Without the packet the stub answers against whatever *virtual* context the CPU happens to be in, so a read of a
physical address returns an error that reads exactly like a broken stub. The client also interrupts first (`0x03`):
a consistent read needs a stopped CPU.

    python3 tools/xemu/mem.py --port 12345 --dump 0x1000:0x8000 -o $WORK/boot.bin
    python3 tools/xemu/mem.py --port 12345 --scan --region 0x80000:0x200000        # counts only
    python3 tools/xemu/mem.py --port 12345 --find "Continue" --region 0x80000:0x400000

Measured on the desktop against a booted guest (`PacketSize=0x20020`): 512 KiB in 1.7 s in 16 KiB chunks from the
Python client, 2 MiB scanned in 14 s including string extraction, so a full 128 MiB dump is about seven minutes and
a targeted region is seconds. `--find` reports offsets only — the strings are guest data and stay out of the
repository unless `--show` is given.

**What this does not solve.** The stub reaches memory, not pixels: `screendump` is still missing and the serial log is
still empty, so "the title screen is up" becomes "the title's label exists in RAM", which is a *proxy* assertion and
should be described as one. And a title-screen run still needs the disc as a regular file (see the pitfall above) —
the dump directory cannot be mounted, so step one is a XISO from `tools/xemu/build-xiso.sh`.

## The XISO, and what the guest actually did with it (measured 2026-10-26)

`tools/xemu/build-xiso.sh $DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15 ~/sg1-xiso/sg1.iso` builds the disc image
from the dump on the desktop: **2,258,370,560 bytes**, the size `docs/XEMU.md`’s own table predicted, in about three
minutes including the `extract-xiso` build. Mounted as `dvd_path` it is accepted (no more *“file driver requires a
regular file”*), and the kernel loads the title’s `default.xbe` from it.

**What the memory channel shows, with the disc mounted.** A 6 MiB scan of physical `0x10000..0x610000` finds the
UTF-16 title string **once, at `0x000e0190`** — so the image is resident near `0xE0000`, not at the `0x10000` base
its header declares. The CPU, sampled five times over ~180 s, is at `EIP=0x8001b030`, `CPL=0` every time: the
**kernel**, not the title.

**That contradicts the earlier note in this document** (“boot a prepared HDD image, and the title runs”). Two things
could explain it and neither is measured: the earlier run may have used a different HDD/EEPROM state, or the title
starts and returns early here and the sampling window (3 minutes) missed it. What is *not* in doubt is the state
this configuration reaches: image loaded, CPU in the kernel, no sign of the title executing.

`tools/xemu/bootcheck.py` turns exactly that distinction into one command and three verdicts — *executing*
(`CPL=3` and `EIP` inside the image), *resident but not running*, *no image at all* — so the next session does not
have to reconstruct it from `info registers` output. Its first end-to-end run died with xemu's own
`GLib: g_source_destroy: assertion … failed` after a `pkill -9` of a previous instance, which is a harness flake
worth knowing about rather than a logic error; the two measurements it composes (CPL/EIP sampling over QMP, title
string over the GDB stub) were each taken by hand and are what the paragraph above reports.

## Two more attempts at getting the title to run (both negative, both measured)

**The debug flash is not the discriminator.** The XISO's own builder warns that a debug-kit build carries a zeroed
XBE certificate the retail kernel refuses, so the obvious next test was the debug flash
(`bios_debug_4627.bin`, present in the dump's `bios/`):

| flash | title string in RAM | CPU samples | verdict |
|---|---|---|---|
| `Complex_4627.bin` (retail) | 1 hit at `0x000e0190` | 5 of 5 at `EIP=0x8001b03x`, `CPL=0` | image resident, not running |
| `bios_debug_4627.bin` | 1 hit at `0x001be190` | 10 of 10 at `CPL=0`, `EIP` range `0x1b030..0x80024a98` | image resident, not running |

The image lands at a different physical address under the debug flash — so the load path differs — but in both cases
the CPU sits in the kernel. Nothing about the signature check is the visible blocker.

**A framebuffer hunt found nothing to look at.** With no `screendump`, the next idea was to read the framebuffer out of
RAM ourselves and decode it. A 16 MiB dump of `0x3000000..0x4000000` was scanned for a 1 MiB-aligned block with
UI-like structure (sampled distinct colours, non-zero byte fraction): every block scored the same (300–373 distinct
sampled colours, ~93k/128k non-zero bytes), which is heap, not a 640×480 screen. That is consistent with the CPU
measurement — a kernel that never started the title has nothing to draw — but it does **not** establish that the
screen is blank, because this dump cannot distinguish "no framebuffer here" from "framebuffer is elsewhere and black".
Where it *is* has not been found: the NV2A surface registers at `0xFD000000` would say, and the mem channel can read
them.

**Where that leaves 7.2.** The disc mounts, the image loads, and the title does not execute under either flash with
the current HDD image (the 4.5 MB one from the NAS). The remaining candidates, in the order they are worth trying:
a fresh formattable HDD image (the kernel may refuse to run a title with no dashboard/save state), the NV2A surface
registers (to get the framebuffer address, then decode a real screen), and a longer window with sampling inside the
first 60 s rather than after it.

## A fresh HDD image changes nothing either, and why that is probably the answer

The remaining candidate from the list above was the HDD: the 4.5 MB image from the NAS is blank, and a kernel with no
dashboard or save state may refuse to launch a title. xemu creates its own image when the configured one is missing,
so the test is cheap — remove the path, boot, sample the CPU **from t+0** rather than after three minutes:

| configuration | CPU samples | image in RAM |
|---|---|---|
| blank NAS HDD, retail flash, XISO | 5 of 5 at `EIP=0x8001b03x`, `CPL=0` (t+120 s … t+180 s) | yes, `0x000e0190` |
| blank NAS HDD, debug flash, XISO | 10 of 10 at `CPL=0` | yes, `0x001be190` |
| **xemu-created HDD**, retail flash, XISO | **30 of 30 at `EIP=0x8001b02f..0x8001b043`, `CPL=0` (t+12 s … t+102 s)** | scan aborted: the stub closed mid-read |

Three configurations, three flashes-and-images, and the CPU never leaves `0x8001b000` — the kernel's idle loop. The
`EIP` bucket is worth remembering: **`0x8001b03x` with `CPL=0` is the kernel idling**, so a sample there is not
evidence of progress however long it is sampled for.

**The hypothesis that fits all three**, and the reason this line stops here: the kernel's launch path is
HDD-oriented. With no dashboard on HDD it has nothing to boot, and it does not fall back to launching the disc's
`default.xbe` — which is consistent with what the memory channel sees, an image that is *loaded* (the loader read the
XBE from the disc) but never entered. Testing that needs a HDD image with a dashboard or a game installed to it, which
this project does not have; nothing else about the setup is in question any more (disc mounts, XBE loads, both flashes
behave the same).

**So 7.2 is blocked on an asset, not on a tool.** The harness can now say *image resident, not running* on its own
(`tools/xemu/bootcheck.py`), and it can read the guest's memory (`tools/xemu/mem.py`); what it cannot do is make the
kernel launch the title without a HDD the kernel considers bootable. The next session should start from a dashboard or
game-installed `xbox_hdd.qcow2` — not from another xemu flag.

One flake, recorded so it is not mistaken for the finding: the bootcheck run in the third row died with the GDB stub
closing mid-read (the earlier GLib `g_source_destroy` assertion is the same family). Both crashes follow a `pkill -9`
of a previous instance; starting from a clean display session, as the first two rows did, has been reliable.

## Driving the game: a sequence of inputs, and an assertion that is not animation (PLAN 7.2)

Sampling `EIP` proves the guest executes code; it says nothing about whether the display is alive or whether input
reaches the game. xemu has no `screendump` in its QMP, so the window is the channel. `tools/xemu/drive.py` boots with
the same TOML `smoke.py` writes and then runs a scripted **sequence**, one step at a time:

    DISPLAY=:0 XAUTHORITY=/run/user/1000/xauth_XXXX python3 tools/xemu/drive.py \
        --iso=$WORK/sg1.iso --bios=$SG1_DUMP/bios --hdd=$WORK/hdd.qcow2 --out=$WORK/drive \
        --crop=640x480+620+140 --fuzz=15 \
        --step=shot=00-title --step=noise=4 --step=cpu=2 \
        --step=xkey=Down --step=wait=3 --step=shot=01-down \
        --step=xkey=a --step=wait=10 --step=shot=02-padA

`shot=NAME` captures and asserts against the previous shot; `key=`/`type=` target the window, `xkey=`/`xtype=` go
through XTEST; `wait=SECS`, `activate`; `noise=SECS` measures the screen's own animation; `cpu=COUNT` samples `EIP`
over QMP; and `monitor=CMD` runs one QMP `human-monitor-command` (`info usb`, `device_add …`). Exit codes: 0 every shot repainted, 2 a step did not, 3 the emulator never came up.

### The title screen animates, so a raw pixel diff proves nothing

**The measurement this section used to carry — `pressed Tab`, `differing pixels: 4178`, `PASS` — was the background
animating.** The title is a live scene behind a translucent panel, so two frames with *no input at all* differ:

| frames, no input sent | crop | metric | differing pixels |
|---|---|---:|---:|
| 5 s apart | 680x620+560+60 | AE | 3,525 |
| 4 s apart | 680x620+560+60 | AE | 3,629 |
| 4 s apart | 640x480+620+140 | AE, `-fuzz 15%` | 591 |
| 4 s apart, a later boot | 640x480+620+140 | AE, `-fuzz 15%` | 389 |

The floor is not a constant — it moves with the scene — so it is measured per run, never carried over as a number.

The harness therefore measures the floor first (`noise=`) and raises its threshold to twice it, and `--crop` restricts
the comparison to the region the input is supposed to change. Without both, any keypress "passes".

### The keyboard is mapped onto the pad, and it is not reaching the guest

xemu maps the host keyboard onto the emulated pad itself — `keyboard_controller_scancode_map` in its `config_spec.yml`,
documented on xemu.app: letters `a`/`b`/`x`/`y` are the pad's face buttons, the arrow keys are the d-pad, `Return` is
START, `Backspace` is BACK. So a scripted run does not need keyboard support in the game; it needs xemu to receive the
key. Measured on the title screen with its five-item menu (`Continue` highlighted), the guest executing throughout
(QMP `EIP` samples in user space at every check, 0 of 2 in kernel):

| input path | measured |
|---|---|
| `xdotool key Down` targeted at the window (`--window`, i.e. `XSendEvent`) | highlight did not move; the frame differs by the noise floor |
| `xdotool key Down` through XTEST, to the X input focus | the same |
| `xdotool windowfocus --sync` first, then XTEST `Down` (`getwindowfocus` reports the xemu window) | the same: the highlight stays on `Continue` |
| `[input] background_input_capture = true` in the run's TOML | the same |
| XTEST `a` — the pad's A button, which would activate `Continue` | no change, so no screen transition |

`xdotool windowactivate --sync` does nothing here (the compositor does not hand activation to an X client), and the
window id is stable across runs, so a stale id is not the explanation. **SDL ignores synthetic `XSendEvent` keys**, which
is why the targeted form cannot work. For XTEST, the answer turned out to be the focus owner — see below.

### Giving the guest a keyboard (and where it does and does not help)

xemu's documented way to attach a keyboard is a runtime command, not a config key — its own docs say there is no
graphical interface for it yet: open the Monitor and run `device_add usb-kbd,port=1.x,id=kbd`, "where the x after the
`port` should be replaced with one of 3,4,1,2 for player 1,2,3,4". `drive.py` can now issue those through QMP
(`--step=monitor=…`), so a run attaches it itself instead of depending on a GUI session that leaves nothing behind.

Measured on this build, the port rule is not what it looks like: **`port=1.3` is occupied by player 1's hub**, so the
documented spelling fails and the keyboard goes on the hub, one slot past the pad:

```
step 3: monitor info usb -> Device 1.1, Port 1, Speed 12 Mb/s, Product QEMU USB Hub
  Device 1.2, Port 1.3, Speed 12 Mb/s, Product QEMU USB Hub
  Device 1.3, Port 1.3.1, Speed 12 Mb/s, Product Microsoft Xbox Controller, ID: gamepad_0
step 4: monitor device_add usb-kbd,port=1.3,id=kbd -> Error: usb port 1.3 (bus usb-bus.1) not found (in use?)
step 4: monitor device_add usb-kbd,port=1.3.2,id=kbd -> (no output)
step 5: monitor info usb -> … Device 1.0, Port 1.3.2, Speed 12 Mb/s, Product QEMU USB Keyboard, ID: kbd
```

**What that buys is the guest's side of typed input, not the host's.** Attaching the device gives the game a keyboard to
read, which is what scripted console commands (7.3) will need; it does not change how a host key reaches xemu, which is
still the focus problem above and still U14. Read the two as separate halves: the peripheral is now known to attach, and
the host-to-guest key path is still unmeasured.

### QMP `sendkey` reaches the guest, so input needs no focus at all (measured 2026-10-30)

The keyboard device above is the input channel. QEMU's `sendkey` (HMP — `--step=gkey=…` in the harness) injects a
keypress into the *guest's* attached keyboard, which is a different path from SDL entirely: no X window, no focus, no
`xdotool`. Measured on the title screen with the X input focus on a running game rather than the emulator:

| sent | pixels changed | screen afterwards |
|---|---:|---|
| `sendkey ret` **without** a keyboard attached | 0 | the title, unchanged — a silent no-op that reports success |
| attach `usb-kbd` at 1.3.2, then `sendkey ret` | 18,790 | `SELECT PROFILE` (`A Select`, `Y Create New`, `B Back`) |
| `sendkey y` — the pad's Y = Create New | 2,867 then 9,753 | `QUICK SELECT MAP`, the in-game map list |
| `sendkey ret` — the pad's A = Select | 10,434 | the map list again, scrolled to `M20A`…`M21D` |
| `sendkey esc` — the pad's B = Back | 13,872 | back out of the page |
| `sendkey down` / `up` / `left` / `right` on those pages | 0–246, i.e. the noise floor | the selection did not move |
| `sendkey tab` | 6,610, then 0 | no console; this build shows none on the menus |

Two things this settles. **The input path is solved**: a sequence can drive the game while the machine is in somebody
else's hands, which `xdotool` could not do and which is what 7.2 needed. And **`ret`, `y` and `esc` are the pad's A, Y
and B** on these menus — the game reads them as buttons, so the menus are navigable by scripted key.

Two things it does not. The map list shows the campaign maps by name (`M20A`…`M21D`, beside the `CTF-*`/`DM-*`
multiplayer maps and `MainMenu`), but **nothing yet moves its highlight** — arrows, `tab` and letter keys left it where it
was — and `ret` on that page changed the list without loading a level. And **`sendkey` with no keyboard attached does
nothing at all and still reports success** (empty HMP reply), so a sequence that looks frozen after its first steps is
usually missing `device_add usb-kbd`.

### XTEST keys go to whoever owns the focus, and on a desktop somebody is using that is not the emulator

Measured after these runs (`xdotool getwindowfocus` / `getwindowname`): the focused window was a **running Steam game**,
`Northgard Battlegrounds` (`steam_app_4487060`) — not xemu. So every XTEST `Down` and `a` was delivered to the game the
owner of the machine was playing and never reached the emulator, and the desktop rang **KDE's "input not accepted"
sound** during the runs, which is how the owner noticed. `xdotool windowfocus --sync` *does* move the focus to xemu when
it is called, which is worse than useless here: it takes the keyboard away from whoever is using the machine.

Three rules follow, and the harness now enforces all three:

- **Check the focus owner before an XTEST step and refuse when it is not the emulator.** `drive.py` prints the owner at
  startup (`note: X input focus is … , not the emulator`) and exits **2** naming the window instead of typing into it.
- **Never move focus implicitly.** Only an explicit `--step=activate` does, and it says so: the harness is a guest on
  somebody's desktop.
- **Give the focus back.** On the way out, the previous owner gets it back (`focus returned to …`) before the emulator
  is killed.
- **Wait for the focus when the machine is in use.** `--step=focus=SECS` polls until the emulator owns it and refuses the
  sequence if it never does — measured: a boot on a desktop whose focus was a running game waited the full 240 s and
  exited 2 with `REFUSED: the emulator never got the focus (owner is Northgard Battlegrounds (104857601)); nothing was
  sent`. `--step=raise` brings the window to the top without touching the focus, so the person at the keyboard can hand
  it over by clicking, which is the only polite way to run this on a machine somebody is using.

This is the same reason the virtual pad below is the route worth building: an evdev gamepad is read without any window
focus, so it cannot type into anybody's application. Recorded as U14.

**The route that does not depend on focus is a virtual pad.** `/dev/uinput` is writable by the desktop user (an ACL
grants `james` rw) and `python-evdev` is installed, so `UInput` can create a device — measured: `uinput ok:
/dev/input/event27 sg1-vpad`. SDL reads an evdev gamepad without any window focus, so a sequence driven from one works
under ssh. Not yet measured, and the whole of what U14 needs: an SDL mapping string so xemu treats the device as a
controller (`gamecontrollerdb_path`, or `SDL_GAMECONTROLLERCONFIG`, keyed by its GUID), and the matching
`[input] gamepad_mappings` / `[input.bindings] port1` entry in the run's TOML.

Two traps the sequence work turned up. **`proc.terminate()` leaks the emulator**: the AppImage wrapper exits while
`AppRun` keeps the HDD image locked, and the next run then dies with `Failed to get "write" lock` — which reads like a
config error, so the harness now starts xemu in its own session and kills the process group. And **the window id can die
mid-run**, so a failed capture re-finds the window once instead of failing the sequence.

**What this measures today**: the title boots to its real menu, frames are capturable and comparable against a measured
noise floor, the guest keeps executing while the sequence runs, and scripted keys now move the game's own menus (the
next section is the measured key vocabulary). **What it does not**: a loaded mission — the map list's highlight moves
by the page, and the step from `M20A`'s neighbour onto `M20A` is the one thing the keyboard will not give.

## The map list's navigation keys, and why `A` does not load (measured 2026-10-31)

`QUICK SELECT MAP` is reachable with one scripted key and it is navigable by *page*. Measured on one live instance
(`sendkey` only, X input focus on another window throughout, guest executing at every check — `EIP` `0x002b98a0`,
`0x002007e3`, `0x0004c66f` from three samples). Deltas are pixels in the list's own crop (`480x520+760+210`), so the
animated background behind the panel cannot contribute.

| guest key | effect | pixels |
|---|---|---:|
| `ret` on `MAIN MENU` | straight to `QUICK SELECT MAP` (this EEPROM holds a profile, so `Continue` skips `SELECT PROFILE`) | 18,790 in the earlier run |
| `home` | index 0, `CTF-Abydos` highlighted | 16,760 |
| `pgdn` | **+10 entries**, highlight lands on the top row | 14,203 / 14,803 |
| `end` | the last entry, view jumps to the tail page | 14,134 |
| `pgup` | −10 in the list's code, but one press from the tail landed on index 0 | 14,289 |
| `down` / `up` | nothing | 0–351 |
| `left` / `right`, `kp_8`, `kp_2`, `ins`, `del`, `spc`, `tab` | nothing | 0–55 |
| `ret` with a map highlighted | the list handles it; **no load** | 13 over 65 s |
| `ret` twice, 0.35 s apart | no load either | 0 |

**The list has 73 entries, ten per page**, and every campaign map is inside it (`tools/xemu/map-index.py` derives the
whole table from the disc). Page one is `CTF-Abydos`, `CTF-LostCity`, the seven `DM-*` maps and `Entry`; index 10 is
`M00`; `M07C` is 19 and `M07D` is 20, so a page boundary falls between two campaign maps; `M20A` is **63**, `M20D` 66,
`M21D` 70, `MainMenu` 71 and `SGCTF-AbydosVsSGC` 72. An earlier version of this paragraph put `M20A` at index 31 and
gave a five-page table; that table was read off *scrolled* frames and was wrong -- the count is 73, not 41, and
`pgdn`-by-`pgdn` counting is what corrected it (`QUICK SELECT MAP lists every map on the disc`, below). The single-step
key the old text wanted is the d-pad: `InternalOnXControllerEvent` maps `XC_PadUp`/`XC_PadDown` to `Up()`/`Down()`,
which is U14's virtual pad.

**Why `A` does not load the highlighted map, from the shipped script.** `QuickLoadMapTempPage extends
SinglePlayerChapterSelect`, whose `InternalOnXControllerEvent` is `case XC_A: if (bButtonA) return LoadMap();` — and its
defaults do declare `bButtonA=True`, with `URL = "?Game=StargateGame.StoryModeGame"` (read with
`tools/class-defaults.py StargateMenus.u --class SinglePlayerChapterSelect`). The page never gets that event, because
the list consumes it first: `GUIVertList.InternalOnXControllerEvent` returns `true` for `XC_A` after `OnClick(self)`, and
`ChapterList extends SGListBox extends GUIListBox` puts that list on the page. The earlier session's `ret` on this page
"re-rendered the list" by 10,434 pixels — that is the click, not a load. Two candidate explanations are left, and either
is settled by the same next run: the list swallowing `XC_A` (a virtual pad does not change that), or `LoadMap()` running
and `Travel` refusing a `StoryModeGame` URL for a map that is not a Story Mode map (an assert dialog the `EIP` channel
cannot see). Do not write "the emulator cannot load a mission" from this — the honest statement is that no scripted
input has made `Travel` happen yet.

**The harness bug this cost: the window's pixmap lags a keypress.** A grab taken immediately after `sendkey` comes back
**byte-identical** to the pre-key frame (measured on the title: `ret` → immediate grab 0 pixels, next grab 18,790), so
an assertion made on the first grab reports "no change" for a key that worked. `drive.py` now takes every asserting
capture twice, 1.5 s apart (`--regrab`), which is what turned "nothing moves the list" into the table above. The same
lag made a *second* identical key look ignored in one run (19 pixels) — the guest coalesces fast repeats, so a sequence
spaces its keys.

**Reproduced by the harness, not only by the probe.** The same sequence through `drive.py` with `--regrab` on and
`shot` steps only: `ret` 34,890, `home` 4,500 (already at index 0, so the list did not move — the delta is the page's own
redraw), `pgdn` 17,443, `end` 2,252, four of four shot steps PASS, `EIP` `0x00252ab5` and `0x001f6620` with 0 of 2
samples in kernel. The table's values come from isolated probes on a long-lived instance, where each key was pressed from
a known position; run-to-run values differ because a delta measures the *transition*, not the key. Two runs are also
what show the boot window matters: at `--seconds=110` the menu is still fading in and `ret` moves 1,476 pixels, at 135 s
the same key moves 34,890.

## The virtual pad drives the menus, and a campaign map loads (measured 2026-11-03)

`tools/xemu/vpad.py` creates a `uinput` gamepad before xemu starts and `drive.py --step=pad=BUTTON` presses it, so a
sequence can drive the game while the machine is in somebody's hands: no window focus, no XTEST and no guest keyboard.
One run on the title screen, crop `480x520+760+210` (the map list's own region), `-fuzz 15`, floor measured inside the
same run:

| step | differing pixels | screen afterwards |
|---|---:|---|
| `noise=3`, no input | 405, so the threshold becomes 810 | the title, animating |
| `pad=a` | 20,606 | `MAIN MENU` to `SELECT PROFILE` |
| `pad=y` | 1,197 | `SELECT PROFILE` to `QUICK SELECT MAP` |
| `pad=down`, 31 times | 13,800 | the highlight walks the list, `CTF-Abydos` onward to `M12F` |
| `pad=a` | 31,718 | **the map loads**: in-game HUD, Carter, `P90` 250 rounds, objective chevrons |
| `wait=45`, then a shot | 3,366 | still animating, **no assert dialog** |

`EIP` `0x00048cda` and `0x00224a51`, 0 of 2 samples in kernel, exit 0, five of five shot steps PASS. The d-pad's single
step exists, a pad's `XC_A` selects a map, and a mission runs.

Four things had to be right before any of it moved, and every one of them looked like "the pad is created and nothing
happens":

- **The identity was a collision.** A fake pad claiming 045e:028e:0114 gets SDL GUID
  `030081b85e0400008e02000014010000` -- which is the GUID of the *physical* pad plugged into this desktop, and the one
  xemu's own settings bind port 1 to. Two devices under one GUID is a pad the run cannot address. The pad claims
  1209:0001 (pid.codes vendor) instead, GUID `030015c6091200000100000001000000`, read back from SDL
  (`SDL_GetJoystickGUIDForID`) rather than computed.
- **The mapping has to be the device's own.** The string in `vpad.py` is what `SDL_GetGamepadMappingForID` returns for
  the live device, and it reaches xemu through `SDL_GAMECONTROLLERCONFIG`. A mapping keyed to another GUID is silently
  unused, and a device SDL cannot map is opened and then ignored.
- **`BTN_DPAD_*` on the device breaks the d-pad.** With those keys declared, SDL maps `dpup`/`dpdown` to buttons b11 to
  b14 and never reads the hats, so `pad=down` (which writes `ABS_HAT0Y`) changed 38 and 128 pixels, i.e. nothing. Without
  them the hats are the d-pad, which is what a real pad has.
- **xemu's base-path settings override `-config_path`, and auto-bind takes port 1.** The run's TOML asked for the virtual
  pad on port 1; xemu came back with the physical pad there and the virtual one on **port 2**, where the game never
  looks. `[input] auto_bind = false` with the explicit `port1` binding, and a private `XDG_DATA_HOME` per run, is what
  makes the run's own binding the only one; `SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1` rides along for the unfocused case.

**Not measured yet, and named so it is not mistaken for done:** the step count. The list opens on `CTF-Abydos` (index 0)
and 31 `pad=down` presses landed on `M12F`, not on the `M20A` the page map puts at index 31 -- a coalesced tap and a
skipped row look identical from the pixels, so the list's index maths needs its own run. And 7.2's printed criterion is
`M00` from `Continue`: this EEPROM copy holds no save, so `Continue` opens `SELECT PROFILE` and the load above came from
the in-game list.

## `M00` from `Continue`, and the list's real index (measured 2026-11-03)

7.2's printed criterion is the title screen, `Continue`, a mission load, a screenshot and a log tail. One run:

| step | differing pixels | screen |
|---|---:|---|
| `pad=a` on `MAIN MENU` | 19,475 | `QUICK SELECT MAP` -- `Continue`, no `SELECT PROFILE` |
| `pad=down`, ten times | 11,459 | the highlight walks to **`M00`** |
| `pad=a` | 23,850 | **`M00` loads**: O'Neill's HUD, the tutorial box "Press the X button to open the door", "New Objective: Open the door for the doctor" |
| `wait=40`, shot | 6,675 | still animating, **no assert dialog**, `A Continue` prompt on screen |

`EIP` `0x80024a97` and `0x80024a98`, both in kernel at sample time -- a paused tutorial frame is the guest idling in
the kernel, not a stopped one: the frame changed by 6,675 pixels across the 40 s wait.

**`Continue` is an EEPROM property, not a menu property.** Two 256-byte EEPROM images are on this machine and they
behave differently: the one in `$HOME/.local/share/xemu/xemu/` (`d0f50547…`) holds no profile, so `Continue` opens
`SELECT PROFILE` (measured, seq2 in this session's runs); `~/sg1/work/xemu/smoke1/eeprom.bin` (`aaa061bd…`, Oct 24)
does hold one and goes straight to the map list. `drive.py --eeprom PATH` now names the image a run starts from and
keeps its own copy, so the difference is a flag in the command instead of a file somebody has to remember to place.

**The list's index is the press count, and the page table was wrong.** Ten presses from `CTF-Abydos` land on `M00`;
fifteen land on `M04B`; thirty-one on `M12F`; thirty-five on `M13D`. All four agree with a list that advances exactly
one entry per press, so `index == presses` and the campaign maps sit at: `M00` 10, `M02A` 11, `M02B` 12, `M02C` 13,
`M02D` 14, `M04B` 15, `M12E` 30, `M12F` 31, `M13A` 32, `M13D` 35. (An earlier revision of this paragraph read `M01A 12,
M02A 16` off the same frames: a transcription slip that contradicted itself, since `M02A` cannot sit past `M04B`. Indices
10 and 11 are now *loaded* and photographed, which is what settled it -- `tools/xemu/missions.sh`.) The earlier page table (`0`-`9`, `10`-`19`,
`20`-`29`, `31`-`40`) was read off *scrolled windows* in `home`/`pgdn` probes and mistook the visible page for the
index range: it put `M20A` at 31, and 31 is `M12F`. `home` still means index 0 and each `pgdn` still moves ten; what
was wrong was the mapping from a screenshot's rows to the list's indices, not the page keys.

## The Unreal console does not open in this build (measured 2026-11-03, PLAN 7.3)

**Corrected the same day: it does open, and it runs commands -- it draws no window.** The negative below is
real (no overlay appears, and `tab` moves 0 to 1,501 pixels) but the conclusion drawn from it was wrong: the
console is open and executing, and what defeated the first attempts was `bIgnoreKeys` swallowing the first
press. See the section after this one for the measurement that settles it.

7.3 assumed `Tab` opens the Unreal console once the game is running rather than sitting on a menu. It does not, and the
shipped configuration says why the assumption was reasonable in the first place.

**What the packages and the dump say.** `System/Default.ini` sets `[Engine.Engine] Console=Engine.Console`, so the class
in play is the engine's own `Engine.Console`, and `System/DefUser.ini:50` binds `Tab=Type` -- `Engine/Console.uc:205` is
`exec function Type()`, the console's open path. `[Engine.Console] ConsoleKey=9` in `Default.ini` is dead config: no
script in the 2,319 classes declares `ConsoleKey`, and the class's own declared defaults are five entries
(`HistoryBot`, `TimePerDemo`, `TimeTooIdle`, `TimeBeforeReboot`, `bRequiresTick`) with no `ConsoleHotKey` among them, so
that `config byte` is zero. The one live branch in `Engine/Console.uc:149-159` is therefore the fallback:

    if ((Key==ConsoleHotKey && Action==IST_Release) || (Key==IK_NumPad7)) { GotoState('Typing'); return true; }

and the first arm cannot fire: the function returns `false` for anything that is not `IST_Press` three lines earlier, so
`Action==IST_Release` is unreachable and `ConsoleHotKey` is 0 anyway.

**What the guest does.** Three runs, a guest keyboard attached over QMP (`device_add usb-kbd,port=1.3.2,id=kbd`) in each,
in live gameplay after the tutorial modal was dismissed, frames kept under `~/sg1/work/xemu/seq8`, `seq10`, `seq12`:

| key sent | differing pixels | console on screen |
|---|---:|---|
| `tab` (the `Tab=Type` binding) | 683, then 1,501 in a second run | no (vision-checked frame) |
| `grave_accent` (`~`) | 0 | no |
| `kp_7` (`IK_NumPad7`, the only live branch) | 30 | no |
| `tab` then `stat fps` and `ret`, 0.3 s apart | 1,213 | no, and no FPS counter |

**One thing did reach the game.** In a run where ten keys were sent in a burst with no spacing, the frame afterwards
carried a chat-shaped line -- `Jack O'Neill( DefaultProfile): at fps` -- so the guest keyboard is delivered, the text
entry path exists, and `Enter` executed nothing (the command was missing its first two characters, which is what
`bIgnoreKeys` does: it swallows presses until the next `KeyDown`). A keyboard that types into a message box and a console
that never opens are different failures, and this build has the second.

**What would settle it**, in the order worth trying: attach the keyboard before the guest enumerates its input devices
(the `device_add` above happens ~75 s into the boot, long after `XID` has built its device list, and a device that
arrives later may never be registered with the game's input system); or trace the scancode the Xbox kernel delivers for
`sendkey tab`, since a kernel-level key that never becomes `IK_Tab` explains every row above at once.

**Until then 7.3 is unreachable as written** -- not "not yet tried": the binding exists in the shipped config, the class
`Console` is the one configured, and nothing that arrives from the emulated keyboard opens it.

## The console executes commands and draws nothing (measured 2026-11-03, PLAN 7.3 corrected)

The console is open after `Tab` and it runs what is typed. Two runs, both with the tutorial modal cleared, one with the
guest keyboard attached ~10 s into the boot and one attached after the boot window, both sent `Tab`, then a leading
`space` (to absorb the press `bIgnoreKeys` swallows), then `stat fps` and `Enter`:

| run | keyboard attached | frame after the command |
|---|---|---|
| `seq13` | during the boot (10 s in) | **`60 cur FPS` / `59 avg FPS`** in the upper left |
| `seq14` | after the boot window (`monitor=device_add`) | **`64 cur FPS` / `60 avg FPS`** |

So the attach timing makes no difference, and `Tab=Type` does what `DefUser.ini:50` says it does. Two things hid it:

- **The console draws no window in this build.** `tab` changes 0 to 1,501 pixels -- the animation floor -- and no overlay,
  prompt or scrollback appears in the frame, so "the console did not open" was an inference from an absent picture.
  A command that has no on-screen effect leaves no trace at all.
- **`bIgnoreKeys` eats the first keypress.** `Console.uc` sets it when the console opens and clears it on the next
  `IST_Press`, so `stat fps` sent cold becomes `at fps` -- an unknown command whose error text is equally invisible.
  The earlier run's `Jack O'Neill( DefaultProfile): at fps` line was that, not a chat box: the leading `s` and `t` went
  into opening the console.

**What this changes for 7.3**: the scripted-`exec` half has a working channel today. What it does not have is *output*:
commands whose result is drawn (`stat fps`, `showdebug`, `stat game`, `slomo`) are readable in a frame, and commands
whose result is text are not. The next step is therefore a readable sink -- the console keeps its scrollback in guest
memory (`Console.Scrollback` / `History`), and reading it is the same class of work as `tools/xemu/mem.py`, which already
reads guest addresses over the QMP channel.

## Loading missions by index, and the console refuses to travel (measured 2026-11-03)

`tools/xemu/missions.sh OUTDIR INDEX...` loads missions out of the in-game list, one boot per index, and keeps one frame
per mission: `Continue`, N d-pad presses, `A`, a settle, a screenshot, one row per index in `OUTDIR/missions.tsv`.
`--name M15C` is the better argument than an index: `tools/xemu/map-index.py` derives the index from the disc, because
the page is the disc's `.unr` set in ASCII order (73 entries). The six anchors are the ones measured from the guest --
0 `CTF-Abydos`, 10 `M00`, 31 `M12F`, 35 `M13D`, 40 `M15B`, 72 `SGCTF-AbydosVsSGC` -- and `--verify` re-derives them,
so a name resolves without a boot and a table that drifts fails loudly. `--all` covers 10-72.

Two indices, run back to back:

| index | list crop, highlight -> load | frame |
|---|---|---|
| 10 | 26,393 -> 24,505 pixels | `M00`: the SGC lab, O'Neill's HUD, "Press the X button to open the door" |
| 11 | PASS | **`M02A`**: an off-world jungle ruin, a crashed vehicle, three soldiers, letterboxed cutscene, `Press A` |

Both exited 0, one row each, both frames kept. So "load each mission" is now a loop over indices rather than a menu
walk, and the remaining work in 7.2 is executing it over the campaign set rather than proving it can be done.

**The console will not change levels.** Before the loop existed, the obvious shortcut was `open` from the console that
7.3 now reaches. Measured twice, in live gameplay, with the same input path that makes `stat fps` work:

| sent | result |
|---|---|
| `open m02a` then `Enter`, 50 s | the same SGC lab, no load |
| `open m02a?game=stargategame.storymodegame` then `Enter`, 55 s | the same SGC lab, no load |

So console travel is refused in this build (a shipping build commonly gates `Travel`), and the map list stays the only
route into a mission. That also means the console is good for *state* commands (`stat`, `showdebug`, `slomo`) and not for
level control, which is worth knowing before writing a probe that assumes otherwise.

## Why the console draws nothing: the configured class has no renderer (measured 2026-11-03)

**Corrected 2026-11-06: the heading is wrong in one word, and the measurement below stands.** The stock console
*does* draw -- it draws its input line, `(>_` when the prompt is empty and `(> stat fps_` when text is in it, on nine
frames over three sessions (see "The console's submit is `ret`" at the end of this file). What it does not draw is
command *output*: the class's own function table (`inventory/function_flags.csv`, 23 functions on `Engine.Console`)
has no `PostRender`, and its properties carry `TypedStr` and `History` but no scrollback field, so a command's text has
nowhere to go. Everything below about the swap is still measured; "renders nothing" is not.

`System/Default.ini` sets `[Engine.Engine] Console=Engine.Console`, and `Engine.Console` is the one console class in the
tree that has **no** `PostRender`: `Engine/Interaction.uc:143` declares it as a stub, `UWindow/WindowConsole.uc:133`,
`GamePlay/TournamentConsole.uc` and `GUI/ExtendedConsole.uc:392` all override it (ExtendedConsole's state even draws its
scrollback through `Canvas.DrawText`). The configured console therefore accepts input and renders nothing -- which is what
three sessions of "the console did not open" were seeing.

**The class can be swapped, and the swap draws.** Rebuilding the disc with that one line changed is enough:

    cp -a "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15" /tmp/console-iso/game
    # System/Default.ini: Console=Engine.Console -> Console=GUI.ExtendedConsole
    tools/xemu/build-xiso.sh /tmp/console-iso/game /tmp/console-iso/game.iso

The rebuilt image boots (title id `0x4a570006`, same 2,258,370,560 bytes as the original) and, in gameplay, `Tab` now
draws an input line: **`(> Say: at fps_`**. So the renderer is real and the shipped line is what suppresses it.

**The swap is not yet a command console.** `ExtendedConsole` opens in `Say` mode on this build: the typed text renders as
a chat message (`Say: …`) and `Enter` sends it, so `stat fps` typed there produced no FPS counter in two runs
(`~/sg1/work/xemu/seq18`, `seq19`) exactly as it does with the stock console. Clearing the `Say: ` prefix with backspaces
was attempted and did not clear it -- the press that opens the console sets `bIgnoreKeys`, which eats the first key after
it, so four backspaces arrived as three. **What would settle it**: send a spare key first (the trick that makes `stat fps`
work on the stock console), then the backspaces, then the command.

This is a debug artefact, not a claim about the shipped build: the disc under `~/sg1/work/console-iso/` is a copy with one
configuration line changed, and nothing about the original build's behaviour changes because of it.

## Asking a frame whether the console is open (`tools/xemu/console-line.py`, 2026-11-03)

With `Console=GUI.ExtendedConsole` in the rebuilt disc, the console draws exactly one cyan line of text in the bottom
left. Against a level that animates by tens of thousands of pixels per second, that is invisible to `drive.py`'s
whole-frame diff -- which is how two runs with the same keystrokes read as "nothing happened" and one read as a console.

`tools/xemu/console-line.py FRAME.png` counts cyan pixels in that corner and answers the question as a number: exit 0
present, exit 2 absent. Measured on three frames from this session, default crop `576x268+0+691` at 1280x960, threshold
20:

| frame | cyan pixels | verdict |
|---|---:|---|
| `seq18/03-typed.png` (console open, `Say: at fps` typed) | 128 | present |
| `seq18/04-executed.png` (after `Enter`) | 0 | absent -- `Enter` closes the console |
| `seq20/02-cleared.png` (same keys, one run later) | 0 | absent -- the console never opened |

The third row is the finding this tool was built for: the console's opening is **not yet deterministic**. `seq20` sent
`Tab` and then the same keys as `seq18` and drew nothing, so the next question is what state makes `Tab` reach the
console -- the tutorial modal is the standing suspect, since `seq18` had already dismissed it and `seq20` had not
verified that it had.

The tool needs ImageMagick, which the harness already requires; nothing new is installed. `make ci` now compiles it with
the other xemu tools.

## What the console's prompt actually says, and when it draws (measured 2026-11-04)

One run with the rebuilt disc (`Console=GUI.ExtendedConsole`), one shot per keystroke group, `console-line.py` over every
frame (`~/sg1/work/xemu/seq21`):

| frame | keys sent since the level loaded | cyan pixels | verdict |
|---|---|---:|---|
| `a-play` | none | 0 | nothing drawn |
| `b-tab` | `Tab` | 0 | nothing drawn -- **`Tab` alone is invisible** |
| `c-one-letter` | `Tab`, `s` | 0 | below the threshold, or not drawn |
| `d-stat` | `Tab`, `stat` | 104 | prompt drawn |
| `e-four-bs` | ... four `Backspace` | 75 | still drawn, shorter |
| `f-six-bs` | ... two more | 57 | still drawn, shorter |
| `g-letter-after` | ... `z` | 61 | still drawn |

Cropping the drawn line at 3x and reading it gives **`(>Say at_`** -- the `(>` prompt, `Say ` pre-filled by the class, then
`at` (the `st` of `stat` eaten by `bIgnoreKeys`) and the caret. So the console *is* open, it opens in **say mode**, and it
draws its prompt only once there is text in it.

Two corrections follow, both to claims this session made earlier. `Tab` opening the console **is** confirmed here -- it
was invisible, not absent, and the earlier "the swap is not yet a command console" stands only in the say-mode sense.
And `console-line.py`'s `absent` means "no prompt text drawn", not "console closed": an empty prompt measures 0, which is
why `seq20` looked like a console that never opened when it was most likely open and empty.

What that leaves for 7.3 is one concrete step: clear the pre-filled `Say ` (four backspaces, with one spare key sent
first to absorb `bIgnoreKeys`) and type a command, then read the FPS counter that `stat fps` draws. If the class keeps
re-prefixing `Say `, the console cannot run commands in this build and the probes have to be screen-drawing commands only.

## The say prefix survives backspacing, and the stock console is the one that runs commands (2026-11-04)

The swapped disc's prompt reads `(>Say …`, so the next step was to clear the prefix and type a command. One run
(`~/sg1/work/xemu/seq22`): `Tab`, one spare key to absorb `bIgnoreKeys`, four `Backspace`, `stat fps`, `Enter`.

| frame | text in the prompt | cyan pixels |
|---|---|---:|
| `p1-typed` after the backspaces and the command | **`(>Say at fps_`** -- the prefix is still there, `st` eaten again | 125 |
| `p2-executed` after `Enter` | line gone | 0 |

No FPS counter appeared. So on the `GUI.ExtendedConsole` disc the console is a **chat** console: the `Say ` prefix is
re-applied (or never removed) and `Enter` sends a message rather than running a command. Four backspaces and a command's
worth of typing did not get it out of that mode.

**The stock disc's console runs commands.** The same command, sent to `Engine.Console` -- the class that draws nothing --
produced a visible FPS overlay twice: `60 cur FPS` / `59 avg FPS` in `seq13` and `64 cur FPS` / `60 avg FPS` in `seq14`.
Invisible, but executing.

**That makes the class swap a dead end for probing.** It trades a console that runs commands and draws nothing for one
that draws and chats. For 7.3 the stock disc stays the tool of record: a command executes, and only its *drawn* effect is
readable (`stat fps`, `showdebug`, `stat game`). Text output is unavailable in both configurations, so `exec`-style
dumps are not a route to actor or objective data -- `tools/xemu/mem.py` and the package tools are.

## Which console commands draw, and why that table is a lower bound (measured 2026-11-04)

With 7.3 routed to the stock disc -- invisible console, executing commands -- the useful question is which commands leave
a *drawn* trace. Two runs, each command typed into the console after the tutorial modal was dismissed, one frame per
command:

| command | differing pixels | overlay in the frame |
|---|---:|---|
| `stat fps` (earlier runs, `seq13`/`seq14`) | 20,606 and 23,850 (the load) -> counter drawn | **`60 cur FPS` / `59 avg FPS`**, then `64`/`60` |
| `stat game` | 554 | none (vision-checked) |
| `stat physics` | 2,293 | none (vision-checked) |
| `showdebug` | 0 | none |
| `behindview 1` | 0 | none -- a camera change would be unmistakable |
| `walk` | 0 | none |

**Only `stat fps` is confirmed to execute.** The rest are either inert in this build or never entered the console, and the
frames cannot tell those apart: `behindview 1` changes the camera when it runs, so 0 pixels means it did not run -- and if
that one did not run, neither did the neighbours in the same sequence. `stat fps` is the one command that has drawn on
every attempt, and it is also the one that is easy to confirm, which makes it a poor witness for the rest.

**The instrument this needs is not a better crop.** On the stock disc the console draws nothing, so `bTyping` is the only
honest "is it open" signal, and it lives in the guest's `Console` object. Reading it over QMP is the same class of work as
`tools/xemu/mem.py` already does for other addresses, and it would turn every future command from a guess into a
measurement. Until then 7.3's command survey is a table of what has been *seen* to work, not of what works.

## The mission loop over six indices, and the boot window it needed (measured 2026-11-04)

`tools/xemu/missions.sh` over indices 10-15, one boot each. The first run used the 75-second boot window the earlier
probes had used, and two of six failed; raising it to 135 seconds -- the value `docs/XEMU.md` already measured for this
menu, where the same key moves 34,890 pixels at 135 s and 1,476 at 110 s while the panel is still fading in -- fixed one
of them:

| index | 75 s window | 135 s window | frame |
|---|---|---|---|
| 10 | PASS | -- | `M00`, the SGC lab |
| 11 | PASS | -- | `M02A`, the off-world jungle |
| 12 | FAIL (`01-highlight` 0 px: the list never opened) | **FAIL** -- the highlight frame is `MAIN MENU`, so the first `A` did nothing | none |
| 13 | PASS | -- | kept |
| 14 | FAIL (`01-highlight`) | **PASS** | kept |
| 15 | PASS | -- | kept |

Five of six load; the sixth is not a map problem. `index-12/01-highlight.png` is the main menu with `Continue`
highlighted, so the run never reached the list and the twelve d-pad presses that followed were pressed into a menu that
was already there -- the same "input arrives and the game does nothing with it" behaviour that has dogged the console
work, and the reason the harness asserts after *every* step rather than at the end. `missions.sh` now defaults to a
135-second boot window (`--boot=SECS`) rather than the 75 the probes use.

The per-index frames and the TSV stay under `~/sg1/work/xemu/`; they are frames of the game and are not committed.

## A swallowed press is now retried, and the retry path is proven by failing on purpose (2026-11-04)

One index in six failed in the six-index run, and the failure was input rather than a map: the highlight frame was still
the main menu. `missions.sh` therefore takes `--retries N` (default 2) and records the attempt count in its TSV -- each
attempt is a fresh boot, because the state a swallowed press leaves behind is not worth trusting.

Two runs:

| run | command | result |
|---|---|---|
| index 12, which failed in the six-index run | `--retries=3` | **PASS after 1 attempt** -- the flake is not deterministic, which is exactly why a retry is the right answer and a diagnosis is not |
| index 10, boot window cut to 25 s so the presses land during the fade | `--boot=25 --retries=3` | **FAIL after 3 attempt(s)**, three boots, one TSV row |

The second is the manufactured failure: it proves the loop iterates, that a failed attempt does not poison the next one
(fresh HDD copy, fresh boot), and that the TSV says how many attempts were spent. `missions.tsv` rows now end in
`<n> attempts`.

## The campaign set loads: 31 of 31 list entries, and the second half loads too (measured 2026-11-04, extended
2026-11-05)

`tools/xemu/missions.sh --all` over indices 10-40, one boot each, `--retries=2`. Every index loaded at least once:

| range | result | notes |
|---|---|---|
| 10-15 | 6 of 6 | 12 and 14 failed at the old 75-second boot window and passed once it was 135 s |
| 16-33 | 18 of 18 | 20, 23, 28 and 32 needed a second attempt; **21 failed both** in that run and passed first time in a later one |
| 34-40 | 7 of 7 | **38 failed both** attempts and passed first time in a later run |
| 41, 70 | 2 of 2 | the first second-half maps ever loaded: `--name M15C` (41) and `--name M21D` (70), both on the first attempt |
| 71, 72 | 2 of 2, but not missions | `MainMenu` loads the MAIN MENU page itself and `SGCTF-AbydosVsSGC` a CTF map; the harness passes both |

**27 indices loaded on the first attempt, 3 needed a second, 1 needed a third.** The retry is doing the work it was added
for: four of 31 (~13%) hit a failure, all of them input.

**Two failure signatures, both input.** The common one is the opening `A` doing nothing: the highlight frame is still
`MAIN MENU`, so the list never opened and the d-pad presses that follow went nowhere. The other is the `A` **on** the list
doing nothing: index 38's highlight frame is `QUICK MAP` with `M14C` selected, `M14C.unr` is on the disc, and the frame
after a 60 s settle is identical to the one before it. Neither is a missing map, and the title frames of a passing and a
failing run are indistinguishable (mean luminance 46.7 vs 46.6), so the press is lost rather than arriving early.

**The two entries after the campaign are not missions, and the harness passes them anyway (measured 2026-11-05).**
`--name MainMenu` (index 71) and `--name SGCTF-AbydosVsSGC` (index 72) both PASS, and index 71's "mission" frame is the
MAIN MENU page with `Continue` highlighted. Neither has a HUD and neither trips `guest-halt.py`, because a menu repaints
and is not the BIOS screen -- so the pixel-and-halt pair asserts *a load happened*, not that a mission did. That is why
the campaign rotation stops at 70. `missions.sh` now also compares the post-load frame with the boot frame
(`tools/xemu/frame-diff.py`: 2.5% of the frame must differ) and records `FAIL ... main menu`; measured, that is 1.07%
for the menu that came back against 4.94%, 7.70% and 13.04% for a cutscene, a CTF map and a first-person mission.

**What "each mission" means here, corrected 2026-11-05: the list offers every map, so "each" is all 73.** The
31-of-31 reading counted the pages this session had scrolled, not the entries the page holds -- the array is 73 entries
(see *`QUICK SELECT MAP` lists every map on the disc* below). Two of the second half were then loaded rather than
assumed: index 41 (`M15C`) and index 70 (`M21D`), one boot each, both passing on the first attempt -- one a
first-person HUD scene with `CARTER`'s portrait and a shotgun, the other a cutscene over a panelled floor. 41-72 as a
set is 32 boots (about two hours at the measured cost), so 7.2's full sweep is now a matter of harness time, not of
access.

## `QUICK SELECT MAP` lists every map on the disc; the 41-entry table was a paging artefact (measured 2026-11-04,
corrected 2026-11-05)

The page holds **73** entries, not 41 -- the controller's own array is `count=73 max=76` after the page opens (the read
is in *The call below `GetMapList`* at the end of this file). The first ten entries are not campaign maps, so indices
10 onwards are the campaign; the table below is correct as far as it goes and simply stops at the last row that session
read, not at the last row the page has. Read off the highlight frames (`01-highlight.png`, one per index):

| index | map | index | map | index | map |
|---|---|---|---|---|---|
| 10 | M00 | 21 | M07E | 32 | M13A |
| 11 | M02A | 22 | M10A | 33 | M13B |
| 12 | M02B | 23 | M10B | 34 | M13C |
| 13 | M02C | 24 | M10C | 35 | M13D |
| 14 | M02D | 25 | M10D | 36 | M14A |
| 15 | M04B | 26 | M12A | 37 | M14B |
| 16 | M04a | 27 | M12B | 38 | M14C |
| 17 | M07A | 28 | M12C | 39 | M15A |
| 18 | M07B | 29 | M12D | 40 | **M15B** |
| 19 | M07C | 30 | M12E | | |
| 20 | M07D | 31 | M12F | | |

Four rows (12, 13, 14, 20) are placed by the list's own ordering rather than read, because their frames were the ones
whose highlight sat against the panel edge; every other row was read off a frame, and the two anchors that pin the
ordering are index 10 (`M00`, the first campaign entry) and index 40 (`M15B`, with empty panel below it and the
scrollbar at the bottom).

**The order is ASCII, not alphabetical**: `M04B` at 15 comes before `M04a` at 16 because `B` sorts before `a`. The
first ten entries are `CTF-Abydos`, `CTF-LostCity`, `DM-Ha'tak`, `DM-KelownanTrain`, `DM-Langara`, `DM-LostCity`,
`DM-Subterranean`, `DM-Tomb`, `DM-WorstCaseScenario`, `Entry`.

**Indices 41-72 are the rest of the disc, and they were never missing**: `M15C`-`M15F`, `M16A`-`M16G`, `M17A`, `M17B`,
`M17D`, `M18A`, `M18B`, `M18D`, `M19A`-`M19E`, `M20A`-`M20D`, `M21A`-`M21D`, `MainMenu`, `SGCTF-AbydosVsSGC`. The
earlier reading stopped on a fifth page that held a **single** item, with empty panel below it and the scrollbar at the
bottom -- a page that looks exactly like the end and is not; the same section's `end` measurement lands on
`SGCTF-AbydosVsSGC`, the last of the 73, which is the observation that agrees with the array. So every campaign map is
reachable from this page by paging, and none of them needs a save, a checkpoint or a `Travel` call.

## Where `QUICK SELECT MAP` gets its entries, and four routes that do not reach the rest (2026-11-04)

The list is **not** built from `System/tweak/Misc/ChapterList.ini`. The class chain is:

    QuickLoadMapTempPage          (StargateMenus, TitleString="Quick Select Map")
      -> SinglePlayerChapterSelect
        -> MapSelectionPanel     (overrides the chapter list with a map-file list)
          -> MapFileList         (config Tweak_Misc_ChapterList, but it zeroes Chapters and ignores the config)
             Controller.GetMapList("", List)      <-- GUI/GUIController.uc:430, `native`

`GetMapList` is a **native** function, so the 41 entries are whatever the engine enumerates and the cap or filter behind
it lives in the XBE, not in UnrealScript and not in any ini. That is why the ini is a dead end: `ChapterList.ini` has 37
chapters with display titles ("Mission 1 Replicator Attack") and file ids (`M02AX.unr`), and none of those strings appear
in the list at all.

What the GUI does on selection, for reference (`SinglePlayerChapterSelect.LoadMap`):

    URL = map $ URL $ "?Name=" $ PlayerOwner().GetCurrentProfile().GetPlayerName() $ "?LAN";
    Controller.Master.Travel(URL);

So the engine's own travel takes a URL of the form `M20A?Name=<profile>?LAN`.

**Four routes tried against the back half, all negative:**

| route | what was done | result |
|---|---|---|
| boot map | rebuilt the ISO with `[URL] LocalMap=M20A.unr` in `System/Default.ini`, booted it | main menu; the ini value is ignored for a normal boot |
| filter box | `MapSelectionPanel` declares a `FilterBox` at `WinTop=0.9` that calls `GetMapList(S, List)` | the box is not drawn in the quick page; typing `M20` after `Tab` changed nothing but animation |
| console from the title | `Tab`, five `BackSpace`, `open M20A`, `Return` at the main menu | no console line drawn (whole-frame change 0 px on the command shot), and the menu was unchanged 60 s after `Return` |
| map list itself | `--all`, 31 entries | ends at `M15B`; 30 campaign maps are not offered |

The console *does* run commands once a level is loaded (`stat fps`, #142), so the fourth row is not evidence that the
console is broken -- only that it is not available from the main menu. The next attempt at the back half belongs
in-game, with the URL shape the GUI uses.

## The console does not travel, and the tutorial modal is what blocks the console (measured 2026-11-04)

The earlier `open` attempts (#147) had two confounders: the console opens with `Say ` already typed, and a tutorial modal
can hold the focus. Both were removed for this run:

1. boot, `pad=a`, ten `pad=down`, `pad=a` -> `M00` loads with the tutorial modal up ("Press the X button to open the
   door", "New Objective: Open the door for the doctor", green **A** Continue);
2. `pad=a` dismisses the modal (the frame changes to the plain lab with the `O'NEILL` HUD and no objective text);
3. `Tab`, five `BackSpace` (clears `Say `), `open M20A?LAN`, `Return`, then 75 s.

The console line **did** draw (3,184 pixels changed on the command shot), so the keystrokes reached the guest and the
command was submitted. Seventy-five seconds later the frame is still `M00`: the same lab, the same `O'NEILL` HUD, no
loading screen. The console accepts `stat fps` in the same session (#142) and refuses `open` -- with a bare map id
(#147) and with the GUI's own `map?LAN` shape here.

**The modal is a harness trap worth knowing.** While it is up, every key goes to it: `Tab` does not open the console,
typing does not appear, and the run reads as "the console is broken" when it is only covered. Any sequence that types
into the console must dismiss the tutorial first with one `pad=a`.

So travel is closed off as a route to the second half of the campaign. What is left is a save or checkpoint that starts
inside those maps, or patching `GUIController.GetMapList` in the XBE (U16).

## A map that will not load resets the guest to the BIOS screen, and that passes a repaint test (measured 2026-11-04)

`appAssertFailed` logs and returns -- no break, no abort (`docs/NATIVE_NOTES.md`, U6) -- so a failed load leaves no
dialog to find. What it leaves is worse for a harness that only compares frames: **the guest falls back to the BIOS
screen**, and a black screen is a perfectly good "the frame changed".

Reproduction: rebuilt the test ISO with `Maps/M00.unr` truncated to 1,000 bytes (the name stays in the list, the package
is invalid), booted it, opened `QUICK SELECT MAP`, ten `pad=down`, `pad=a`. Twenty seconds later the frame is black with
"Please insert an Xbox disc..." in the top left, and both `EIP` samples are in the kernel idle loop (`0x80024a97`,
`0x80024a98`). The shot step that captured it **passed** (176,832 pixels changed, threshold 200).

**The measurement that separates the two states**, over four real frames:

| frame | black (<= 20) | p99 | text pixels, top-left |
|---|---|---|---|
| BIOS prompt | **0.9972** | **13** | 1,808 |
| in-game `M00` | 0.1488 | 192 | 16,037 |
| title menu | 0.1984 | 255 | 20,201 |
| map list | 0.1074 | 218 | 4,394 |

Xbox output is limited range, so black is 16, not 0 -- which is why the tool's threshold is 20 and not 0. `p99` is the
value at the 99th percentile: on the prompt nothing but the glyphs is brighter than 13.

`tools/xemu/guest-halt.py FRAME [FRAME ...]` returns 0 when any frame is the BIOS screen, 2 when all are running, 3 when
a frame cannot be read. `missions.sh` now asks it about every `02-mission.png` and records `FAIL ... BIOS screen` in the
TSV instead of a pass. End-to-end, on the truncated-map ISO:

    10  FAIL  .../index-10/halt.log  1 attempts  BIOS screen

**One limit worth knowing.** With the default `--settle=60` the crash is caught one step earlier: `drive.py` cannot
capture `02-mission.png` at all ("could not capture"), because by then the emulator's window is no longer the one the
harness grabbed. That is still a failure, but it is a *capture* failure; the frame check covers the case where a frame
does arrive, which is what `--settle=20` produces and what any shorter settle will hit.

## The routine behind `QUICK SELECT MAP`: `Filter` is never read, and there is no cap anywhere (measured 2026-11-05)

U16 said the cap lives in the XBE because `GetMapList` is `native`. That is right, and the routine needs no native
index: `inventory/natives.csv` records this declaration with token 0, which is the *name*-binding case U1 settled. Two
wide literals identify it without an index at all --

| what | VA | why it is the routine |
|---|---|---|
| `intUGUIControllerexecGetMapList` | `0x00534a4a` | the name the binder formats and looks up for an index-less native |
| `[VL:GetMapList]:%s` | `0x00538d28` | its own log format; exactly one dword reference in the image, `0x002fe7e4` |

That reference lands inside a 1,256-byte function at **`0x002fe5f0`**: `UGUIController::execGetMapList`, exported by
`ExportAt.java` in one run. The body, in order: two `FFrame::Step` calls off the call's expression stream (`Filter`,
then `List`) with the `EX_EndFunctionParms` (`0x42`) skip every native's argument reader needs; the wildcard built from
the engine's `MapExt` global (`0x007b6c80`, `unr` in `System/Default.ini:9`) into `*.unr`; a walk of the engine's
enumeration source with a wildcard test at `0x00036cc0`, a `[VL:GetMapList]` log line per hit, and one 0x1c-byte item
appended to the list widget's array (offsets `0x27c`, `0x280`, `0x284` are that array's data, count and max); and the
whole walk run twice, the second pass with a base path plus `System\` in front.

Two facts are in the body itself, and both are checks the outside could not make:

- **`Filter` is stepped and never read.** The buffer the first `Step` fills is passed by address and then reused as a
  scratch local for the rest of the function; nothing compares against it. `StargateMenus.MapFileList.Filter(S)` hands
  `S` straight to this native, so the filter box cannot filter -- what #157 measured from the outside (typing `M20`
  after `Tab` changed nothing but animation) now has its cause.
- **There is no cap in this routine.** A linear sweep of all 1,256 bytes finds no comparison against a constant bound,
  so no loop limit here can shorten the list.

The disc explains the *shape* of the list, and it also corrects the assumption that its order is the disc's.
`Maps/` holds **73** `.unr` in one 2048-byte directory sector, and the enumerator hands them back in **storage** order --
`M12F.unr` first, `SGCTF-AbydosVsSGC.unr` last (measured at the call's return, below). `GetMapList` then sorts them
(`0x000f8ab0`) before appending, which is why the page reads `CTF-Abydos`, `CTF-LostCity`, ... in ASCII order, and why
`M04B` sits before `M04a`. A prefix in the *page* is therefore a property of the page, not of the enumeration.

**The enumeration source, measured in the guest -- and the instrument that did not finish the job.** The global the loop
dereferences is `0x005fbee4`. Read over the GDB stub in a booted guest, in **virtual** mode (`mem.py`'s documented
physical mode answers zero for this image global at every offset tried, because the XBE's sections are not
identity-mapped), it holds two 8-entry `FString` arrays at `+0x7c` and `+0x11c` whose strings are exactly
`[Core.System] Paths` from `System/Default.ini:49-56` (`..\System\*.u`, `..\Maps\*.unr`, ... `..\Animations\*.ukx`).
So the routine walks the engine's path globs and whatever the file layer caches behind them -- the same conclusion #158
reached from the package side, now with the structure in hand.

What is still open is the *count* behind those globs, and the failed instrument is worth recording: the virtual read
works at t+13 s (that is where the `Paths` strings above came from) and returns 0 for the same address at the title,
where the interrupted CPU context does not map the image; a physical scan for the map names then answered `E22` to every
request, and the retry with working chunking died with the emulator itself (`amdgpu: The CS has cancelled because the
context is lost`) 150 s into the boot. **The next instrument is one read at the title in the game's own context** --
stop the guest inside the title loop, or read the file-array offset out of a second `ExportAt` of `FPackageFileCache`'s
own `Add`/`Find` bodies -- and it answers one number: how many `.unr` entries the cache holds. 41 means the fill stops
and the cap sits above this routine; 73 means the cache is complete and the truncation is in the widget's `Add`.

## The same global read again: the image *is* mapped at the title, and `0x5fbee4` is only `Paths` (measured 2026-11-05)

The section above records a virtual read that returned 0 at the title and concluded that the interrupted context does
not map the image. That was wrong, and the correction matters because it removes a blocker that was never there:

| t | `0x2fe5f0` (`.text`, 8 bytes) | `0x7b6c80` (`MapExt`) | `[0x5fbee4]` |
|---|---|---|---|
| +20 s | `558bec83e4f86aff` (the prologue of `execGetMapList`) | `0xaddaf0`, 4 | `0xb4f6e0` |
| +120 s | same | same | `0xb4f6e0` |
| +160 s (title) | same | same | `0xb4f6e0` |

The XBE is mapped in whatever context the stub interrupts, at every stage, and the global is stable. The zeros were the
stub's own intermittent failure: roughly every other read raises `odd hex length from stub`, and a loop that treats the
exception as "nothing there" records 0. Retrying on a fresh connection (`Gdb.close()`, reconnect, `interrupt()`) returns
the value -- that is what the table is built from, and it is the difference between two sessions answering the same
question differently.

**And `0x5fbee4` is not the package file cache.** At the title it resolves to the same object as at t+20 s, and a
`{ptr,count}` sweep of its first `0x600` bytes finds only the two 8-entry `Paths` copies at `+0x7c` / `+0x11c`: no array
of file entries anywhere in it. So the loop in `execGetMapList` iterates the `Paths` globs, and the file names have to
come from the callee it calls per glob -- `0x00036cc0`, with `0x00036ce0` and `0x00036c90` next to it building and
concatenating the strings -- not from an array the routine walks itself. Reading those three bodies is the next step,
and a cheaper one than another memory instrument.

## The three helpers `GetMapList` calls: string functions, and one call that is not (measured 2026-11-05)

The previous section named `0x00036cc0`, `0x00036ce0` and `0x00036c90` as "building and concatenating the strings". Exported,
they are the CRT: **`_wcscat` (`0x36ce0`), `_wcsstr` (`0x36cc0`) and `_vsnwprintf` (`0x36c90`, into a fixed `0x400`
wchar buffer)**. A fourth body settles what the loop is doing -- `0x300410` is the widget's array clear: it walks the
0x1c-byte elements, frees them, sets the count to 0 and then reallocates. So `this + 0x27c` is the list the routine owns,
and the routine clears it before filling it.

That corrects the mechanism in two places:

- **The `*.unr` test is a literal substring search, not a wildcard.** `_wcsstr(glob, L"*.unr")` is run against each
  `[Core.System] Paths` glob, where `*` is just a character, so the entries that qualify are the ones that *contain* the
  text `*.unr` -- `..\Maps\*.unr` and `..\TestMaps\*.unr`. The routine is collecting **paths**, not map names.
- **The map names come from one virtual call.** For each qualifying path it formats/concatenates the string, logs it via
  `[VL:GetMapList]:%s` (`0x574d0` is the log wrapper) and then calls `[[0x005bf514] + 0x30]` at the site `0x2fe806` with
  four arguments (path, 1, 0), taking the result and appending it to its own array (`0x1fda0` / `0xc19a0`). A call shaped
  like that, handed a glob path and returning something the caller appends, is a file enumerator -- so **the count of 41 is
  produced one level below `GetMapList`**, in the engine's glob/file layer, and not by the GUI and not by this routine.

**The read is done, and the file layer returns everything (measured 2026-11-05).** The call is one indirection deeper
than the paragraph above assumed, and resolving it from the image alone is a decoy; see *The call below `GetMapList`*
at the end of this file.

## The call below `GetMapList` is `FFileManagerGeneric`'s find loop, and it hands back all 73 (measured 2026-11-05)

`0x2fe7ee` is `mov 0x5bf514,%ecx; mov (%ecx),%edx; call *0x30(%edx)`, so the callee is slot 12 of the vtable of
`[[0x005bf514]]` -- a double dereference, not the single one the previous section assumed. Every hop is image-resident,
and the image's answer is wrong, which is the part worth recording first:

| hop | image (`default.xbe`) | guest at the title |
|---|---|---|
| `[0x005bf514]` | `0x005c1a0c` (`.data`) | `0xd0174428` |
| `[[0x005bf514]]` | `0x004d2fd0` (`.rdata`) | `0x004c8db8` (`.rdata`) |
| `[[[0x005bf514]] + 0x30]` | `0x00034570` | `0x00024e70` |

`0x00034570` is 22 bytes: it zeroes three dwords at its first argument and does `RET 0x10` -- an empty 12-byte
`TArray`, i.e. the base class's no-op override. Exporting the image's chain therefore answers the question with a stub.
The `.data` slots are runtime object handles: of the six globals at `0x005bf500..0x005bf514`, two still match the image
at the title and four do not.

**At the title the object's table is real and image-resident.** `0x004c8db8` reads back identical to `default.xbe` in
all 24 slots dumped, so this is not a corrupted read; its slot 12 is **`0x00024e70`**. Two artifacts name the class
without needing a symbol: `symbols/xbox_functions.csv` has `0x000246d0 = Core_FFileManagerGeneric`, and
`inventory/xbe_modules.csv` puts `FFileManagerGeneric.h`'s assert literal at `0x004c8b40`, immediately in front of the
`0x4c8db8` table.

**The body is a find loop, and it has no cap.** It formats the pattern (the caller's `..\Maps\*.unr`-shaped string) into
an `_alloca` buffer, calls a `FindFirstFileW`-shaped routine at `0x0033e596` with a find-data struct whose `cFileName`
starts at `+0x2c`; per hit it copies at most `0x104` wchars of the name, skips `.` and `..` (`0x004cfb98`,
`0x004cfb90`), and skips directories through the caller's two flags (`local_190[0] & 0x10` picks parameter 3 or 4, and
the call passes `1, 0` -- files yes, directories no). Each name is appended to a `TArray` that grows `count*1.375 + 4`
at a time, `0x0033e6a3` (`FindNextFile`-shaped) drives the loop until it answers 0, and `0x0033d437` closes it. There
is no comparison against a bound anywhere in the 718 bytes.

**Measured, not inferred.** With a breakpoint at `0x2fe809` (the instruction after the call) and `ret`, `y` sent from
the title, four hits each returned a `TArray` with **`count=73 max=73`** holding every `.unr` on the disc, in
*directory* order -- `M12F.unr` first, `SGCTF-AbydosVsSGC.unr` last -- not the sorted order the page shows.

**And the page keeps all 73.** After `ret`, `y` from the title, a breakpoint at `0x2fe8e0` -- the head of the loop that
appends the collected names -- gives the controller (`[esp+0x20]`), and the array at `this+0x27c` then reads
`data=0x07438c00 count=73 max=76`, ASCII-sorted: `CTF-Abydos` at 0, `M00` at 10, `M15B` at 40, `M21D` at 71,
`M21D` at 70, `MainMenu` at 71 and `SGCTF-AbydosVsSGC` at 72. So nothing is capped and nothing is dropped: the 41-entry table
above measured a page, not the list, and U16 closes with no cap to patch.

A mechanical note for that run: the guest's user stack sits at `0xD016xxxx` at the title, so a pointer into `0xD0...` is
ordinary on this title and the "impossible address" reading of `[0x005bf514]` was wrong twice. And a `mem.py` read is
only retried when it raises: read each address three times and compare it with the same bytes in the image before
believing a zero or an odd value.

## The engine's object array is located, and objects are addressable by class (measured 2026-11-06)

`UObject::GObjObjects` — the engine's list of every live object — is a `TArray<UObject*>` at **`0x006c21b0`** in
`.data`, holding **42,152 objects at the title screen** (`data=0x041e0000`, `num=42152`, `max=51886`).

The address cannot come from the image. `.data` is `0x00226bd4` bytes of virtual size against `0x00034b9c` of file
bytes, so the array is zeros in `default.xbe` and exists only in the guest. It was found by shape and confirmed by
content: a triple is a candidate when `Num` is in the thousands, `Max` is at or above it and `Data` points into the
heap, and a candidate is **validated** only when the objects it points at carry vtables from the image's own
class-table set (`inventory/xbe_class_tables.csv`). The winner scored **64 of 64** sampled objects; the other three
heap-pointer candidates in `.data` scored 0, so the check is what separates the array from its neighbours.

    python3 tools/xemu/drive.py ... --gdb=12345 --linger=1500 --step=shot=00-title --step=cpu=2
    python3 tools/xemu/objects.py --port 12345 --xbe $SG1_DUMP/default.xbe --find
    python3 tools/xemu/objects.py --port 12345 --walk 0x6c21b0 --top 25 --class Player

**The walk is a page read, not a sweep.** 42,143 objects live on **255** 64 KiB pages, one RSP packet per page at
the stub's own `PacketSize=0x20020`: **2m21s** for the whole array. Reading the `min..max` span (the first attempt)
or the same pages at 16 KiB chunks (the second) is what made the walk look hung — the span includes tens of
megabytes of unrelated allocation, and one packet per 16 KiB multiplies the latency by four.

The histogram is the engine's own shape at the title:

| objects | class | | objects | class |
|---:|---|---|---:|---|
| 8,510 | `Function` | | 1,004 | `Class` |
| 5,758 | `ObjectProperty` | | 332 | `Package` |
| 4,402 | `FloatProperty` | | 320 | `State` |
| 4,173 | `BoolProperty` | | 188 | `LinkerLoad` |
| 3,388 | `StructProperty` | | 171 | `StaticMeshActor` |
| 3,193 | `IntProperty` | | 168 | `PackageCheckInfo` |
| 2,424 | `StrProperty` | | 128 | `ReachSpec` |
| 1,409 | `Sound` | | 104 | `StaticMesh` |

`--class Player` resolves the live objects a regression run can assert on at the title: **one `PlayerController`**,
one `PlayerInput`, one `PlayerProfile`, six `PlayerReplicationInfo`, two `PlayerChatManager` and two `PlayerStart`.
That is the object-level channel: ask for an object by class instead of inferring one from pixels.

**What it does not prove, stated so it is not read as more.** `--class Console` returns **one** object, and its
bytes are a default-properties block (`FontNames(0)="SmallFont"`, `Images(0)=Shader'SG1_MainMenuTex…'`,
`KeyName="SGMenuButton"`) — read as the class default object rather than a live console, because a `UConsole`
instance carries `TypedStr`/`History`, not style defaults. So at the title there is no console to read a command
back from, and U15's `History[16]` check still needs a run with a level loaded. A class-table name is also a name
for the *vtable*, not proof of an instance's class: 179 distinct unnamed vtables appear in this walk, and every
instance of a class whose table was named by its registration literal inherits that label.

## Three input failures on the way to a live console, and the instrument that reads its strings (measured 2026-11-06)

7.3 needs the live `Console`'s `History[16]`, which needs a console instance, which needs a loaded level and a `Tab`
that lands. This session got the instrument and the three reasons the run before it did not: none of them is the
console.

**A keyboard attached after the level loads is never read.** With `device_add usb-kbd` issued once the mission was
up, `Tab`, `space`, `stat fps` and `Return` produced **0 pixels** and no FPS overlay; the object walk at M00 then
found **48,647 objects over 295 pages** with `StaticMeshActor` 927 (against 42,152 / 255 / 171 at the title) and
`--class Console` returned only the class-default object, because no `Tab` had ever reached the game. Attach the
keyboard *before* the title — 75 s works, which is why every earlier console measurement used that timing.

**Attached early, the keys do reach the game — and the press that matters is the one that gets swallowed.** With the
keyboard at 75 s, letters moved the map list (**17,780 px**), but the select press was lost and the level never
loaded. `missions.sh` retries a whole *boot*; `drive.py` has no retry inside a sequence, so a console run needs its
select press repeated rather than the boot repeated.

**A killed `drive.py` leaves its virtual pad behind, and the next run binds the stale one.** After a run whose
`drive.py` survived its first kill, `/proc/bus/input/devices` carried **two** `SG1 virtual pad` devices
(`input56`/`input57`); xemu bound the dead one and every `pad=` step was a no-op while the run still looked healthy.
Kill the emulator by its real process name — the AppImage wrapper runs as `appimage_extracted…`, not `xemu.AppImage`
— and remove leftover pad devices before the next start.

**The instrument: `--fstrings`.** A UE2 `string` is a `(Data, Count)` pair, so the characters are not inside the
object and `--strings` cannot see a console's `History` — the field reads as sixteen integers. `objects.py
--fstrings` resolves each plausible pair, keeps only the ones whose target really is NUL-terminated text, tries both
widths, and spends a bounded number of reads. Validated on a live `PlayerProfile` at `0x06ad8a00` at the title:
**21 pairs read, 6 carried text** — `DefaultProfile`, `StargateContent.Oneill`, `Jack O'neill`,
`SG1_MenusTex.HUD_Oneill`, and two more of the same shape.

**What is left is one clean run**, not another instrument: a single vpad, the keyboard at 75 s, three select taps,
then `objects.py --walk 0x6c21b0 --class Console --fstrings --object-size 0x800`. That closes U15's `History` read
and 7.3's output channel.

## A loaded level still has no live `Console`: the level was never the variable, `Tab` was (measured 2026-11-06)

The previous section left one run to do: a single pad, the keyboard at 75 s, a repeated select, then
`objects.py --class Console --fstrings` on the instance a level creates. The run was made and the instance does not
exist. What it did produce is a better order for the next attempt.

**The run.** One virtual pad, no leftover pad device, `device_add usb-kbd` at 75 s, presses from 137 s: `pad=a`
opened `QUICK SELECT MAP` (**21,940 px**), the ten `pad=down` taps that follow moved **0 px**, the select `pad=a`
after them started a level (**31,222 px**). The highlight never repainting is not the pad failing — the loaded level
is **48,647 objects over 295 pages** with `StaticMeshActor` **927**, the M00 census measured in the previous section
to the object, and M00 is ten presses down the list. **A frame is not a way to index that list in a keyboard-attached
run**; the object census is, and it costs the same walk.

**The walk.** `objects.py --walk 0x6c21b0 --class Console --fstrings --object-size 0x800` resolves exactly one
object, `0x02344b00`. Its 0x800-byte window is a `defaultproperties` block — `FontNames(0)="SmallFont"`,
`KeyName="SGMenuButton"`, `Images(0)=Shader'SG1_MainMenuTex.MM_high_mouseover_S'`,
`FontColors(4)=(B=120,G=120,R=120,A=200)` — and the FString scan reads **1 pair, 0 carrying text**. At the title the
same command returned the same shape. A live level does not, on its own, create the console object.

**Why it was empty, and the precondition the next run needs.** `Tab` in that session produced **0 px**, and
`console-line.py` counted **0 cyan pixels** on the frame after it, so no console was open when the walk ran. The
`History[16]` read therefore needs a frame that shows the prompt (`(>Say at_`, the shape `console-line.py` measures)
*before* the walk: `absent` means "no prompt text", not "closed", and walking without it spends 2m21s to re-measure
the class default object. The order is `Tab`, prompt frame, walk — and if the prompt never appears, the run has
produced a negative about input, which is what three of the last four runs produced.

**One more input rule.** Keys cannot be added from a second shell: `xdotool key`/`type` sent over ssh after
`drive.py` had exited went to whatever owned focus (`TotK Recomp`, with `windowfocus --sync` on the xemu window not
taking) and three frames came back byte-identical. `drive.py` refuses that case by design (`FocusElsewhere`); a
console run carries its keys in its own `--step` list.

## A second clean run: the console is still only a class default, and the d-pad step is the unreliable part (measured 2026-11-06)

One run, built to remove every confounder the previous one named: a single vpad, no stale pad device, `device_add
usb-kbd` at 75 s, presses from 137 s, `Tab` sent **twice** (the first keypress after a load is swallowed), then
`stat fps` and `Return`.

| step | result |
|---|---|
| `pad=a` opens `QUICK SELECT MAP` | 21,937 px |
| ten `pad=down` | **0 px** |
| `pad=a` selects | level loads (21,789 px) |
| `Tab`, `Tab`, `stat fps`, `Return` | 0 px |
| object walk | **45,802 objects** over 295 pages; `--class Console` = **1 object, the CDO**; FString scan 1 pair, 0 text |

**The loaded level was not M00.** M00's census is 48,647 objects, measured twice; this run's is 45,802, so the ten
d-pad presses did not step the list and the select took its opening entry. That is the finding: in a
keyboard-attached run `pad=down` steps the list only sometimes, and the highlight crop reads 0 px either way, so
**the census is the only evidence of which map loaded** — compare it with the M00 number before drawing any
conclusion about what happened *inside* the level.

**What this does and does not establish.** On M00, the previous run's walk found only the CDO after a `Tab`
(#176), and that stands. This run adds a second CDO-only walk, but on a level reached by a select press whose
stepping was never verified, so it cannot be used to say `Tab` failed — it can only say the console object is not
in the array when the walk runs. A live `UConsole` is created from `Engine.Console` when the viewport is made, so
its absence after a load is the thing to explain, and the next instrument is the `PlayerController` the walk
already resolves: read its members for a pointer on the `Console` class table rather than trusting the class-list
lookup, which only matches objects whose *own* vtable is that table.

## The console opens and draws its input line; the tutorial modal was eating the keys (measured 2026-11-06)

Three runs read "0 px after `Tab`" as "no console". A frame read with eyes says otherwise.

**M00's tutorial modal is a modal, not an overlay.** The frame at `02-mission` shows O'Neill's HUD, the lab, and a
panel reading *"Press the X button to open the door" / "New Objective: Open the door for the doctor" / A Continue* —
and four captures taken over the next seven seconds are **byte-identical** (`00c0b078…` four times), so the game is
frozen while it is up and keyboard input goes nowhere. Dismissing it is the `A` the panel itself asks for; after
that the frames move again (41,294 px on the next shot).

**With the modal gone the console opens, and its input line is drawn.** `Tab`, `Tab`, then `stat fps` typed as guest
keys gives a **cyan `> stat fps_` line at the bottom-left** of the frame — in a first-person view of the lab with no
modal. So U15's "the console renders no window" is true only of an *empty* console: `Tab` alone draws 0, and text
draws, as `console-line.py`'s own docstring says. The screen is 1,280×720 and the line is roughly 300 px wide and
20 tall — a few thousand pixels, which is why a crop aimed at the menu region missed it.

**What is still missing is the Return.** `Enter` was sent after the text and the next frame is **identical** to the
one before it (0 px) with no FPS readout anywhere, so the command is typed but not submitted. The obvious next test
is the other Return scancode (`kp_enter`, or `ret` twice), not another search for the console.

**Correction to the previous note in this file.** It read a 45,802-object census as "the level was not M00", because
M00 had measured 48,647. The `02-mission` frame from that very run is M00's lab with the same modal — the object
count moves with level state (a modal up, actors spawned), so **the census is not a map fingerprint**. The frame is.

## `Tab` is a toggle, and neither Return submits the command (measured 2026-11-06)

Two runs, one boot each, same map and modal handling, differ only in how many times `Tab` is sent — and the
difference identifies what the last note got wrong.

| run | sequence after the modal is dismissed | typed frame |
|---|---|---|
| conhist8 | `Tab`, `Tab`, then `stat fps` | **byte-identical to the `Tab` frame** (`cd1802d9…`) — the text was never drawn |
| conhist9 | `Tab` once, then `stat fps` | **3,394 px** — the text is drawn |

So `Tab` **toggles** the console: one press opens it, a second closes it. The earlier "send `Tab` twice" recipe was
only correct while M00's tutorial modal was up, because the first press went to the modal. After the modal is gone,
one press is the whole sequence. `Tab` alone draws ~300 px (inside noise), so "is the console open" can only be
answered by typing a character or by the object array — not by the `Tab` frame.

**Typed text stays typed, and neither Return submits it.** After `stat fps` is on screen, `kp_enter` and then `ret`
each change the frame by only 2,121 and 3,053 px (the caret and the echo), and the frame after both still reads
**`(> stat fps_`** — the same prompt with the same text and no FPS readout anywhere. So the console receives
characters and does not receive an execute key from either scancode. The next keys to try are the pad's `start`
(what xemu's keyboard map turns Return into) and `gkey=enter`, before anything more elaborate: the console is open,
the command is typed, and the only missing event is the submit.

## The console's submit is `IK_Enter`, read from its own source, and four keys do not produce it (measured 2026-11-06)

Three runs had established that the console opens, holds `stat fps`, and closes again on a second `Tab`. What no run
settled was *why* no key submits it. The class's shipped script does (`Engine/Console.uc`, in the package's own
`ScriptText`):

    else if( Key==IK_Enter )                      // Event bool KeyEvent(EInputKey Key, EInputAction Action, FLOAT Delta)
        if( TypedStr!="" ) {
            History[HistoryTop] = TypedStr;
            HistoryTop = (HistoryTop+1) % ArrayCount(History);
            Temp = TypedStr;  TypedStr="";  TypedStrPos=0;
            if( !ConsoleCommand( Temp ) ) Message( Localize("Errors","Exec","Core"), 6.0 );
        }
        TypingClose();
        return true;

So a submit has three observable effects: `History` gains the command, `TypedStr` clears, and the console closes.
Measured against a frame after each key — `kp_enter`, `ret`, `pad=start` and `enter` — the line still reads
**`(> stat fps_`**, the console stays open, and no FPS readout appears anywhere: none of the four produces an
`IK_Enter` press the console sees.

**Correction (2026-11-06): `ret` does submit, and the frame that said otherwise was lagged.** In `conhist9` the
frame after `ret` has no input line at all -- 0 cyan pixels where `kp_enter` left it drawn -- which is exactly what
`TypingClose()` in the branch quoted above produces. In `submit1` the same key left the frame byte-identical to the one
before it and the line still drawn: the same key, not arriving. The two sessions, their shot-by-shot pixel counts and
what a frame can still not say about the command's effect are in "The console's submit is `ret`, and the line it clears
is the proof" at the end of this file.

**What that leaves, precisely.** The keyboard reaches the console (letters echo, and `KeyType` is what echoes them),
so the gap is one event: the guest's translation of a Return scancode to `IK_Enter`. Either its keyboard driver does
not map either QEMU Return (`ret` 0x1C, `kp_enter` 0xE0 0x1C) to `IK_Enter`, or the game consumes `Enter` first —
`DefUser.ini` binds `Enter=Use`, and a binding is evaluated by `PlayerInput` before the console's interaction sees
the key. The instrument that separates those is the input map itself, not another boot.

## The console's submit is `ret`, and the line it clears is the proof (measured 2026-11-06)

The note above read four frames after four keys and concluded that no key submits. Two of those frames were of the
state *before* the press: a capture taken 1--4 s after a key can predate the guest's render of it, and in one session
every shot lagged its own keys by exactly one shot. Re-read with each shot's own diff count and a classifier that
separates an empty prompt from a full one, the same corpus says what the class's source says it should.

`tools/xemu/console-line.py` now answers with three states instead of a pixel count -- `absent`, `prompt` (the empty
`(>_`) and `typed` -- by the trimmed extent of the drawn band, because the count alone cannot tell an empty prompt
(46--62 px) from the noise floor of a menu (22 px on the title screen, 87 px in one 18x8 button glyph, both called
`present` by the old 20-pixel threshold). Over the frames that exist on disk:

| session / frame | keys since the previous shot | state | cyan px | extent | diff vs previous shot |
|---|---|---|---:|---|---:|
| `conhist9/00-title` | -- (title screen) | not a line | 22 | 554x246 | -- |
| `conhist9/01-list` | `A` (map list) | not a line | 87 | 18x8 | -- |
| `conhist9/03-dismissed` | `A` (tutorial modal) | absent | 0 | -- | -- |
| `conhist9/04-tab` | `Tab`, 2 s | **prompt** | 46 | 50x32 | 294 |
| `conhist9/05-typed` | `stat fps` as 8 guest keys, 1 s | **typed** | 113 | 176x32 | 3,394 |
| `conhist9/06-kpenter` | `kp_enter`, 4 s | typed | 113 | 176x32 | 2,121 |
| `conhist9/07-ret` | `ret`, 4 s | **absent** | 0 | -- | 3,053 |
| `submit1/04-tab` | `Tab`, 2 s | absent | 0 | -- | -- |
| `submit1/05-typed` | `stat fps` as 8 guest keys, 1 s | **prompt** | 56 | 64x32 | 1,311 |
| `submit1/06-enter` | `gkey=enter` (invalid key name, so nothing sent), 3 s | **typed** | 138 | 176x32 | 1,312 |
| `submit1/07-kpenter` | `kp_enter`, 3 s | typed | 138 | 176x32 | 800 |
| `submit1/08-ret` | `ret`, 3 s | typed | 138 | 176x32 | **0** |
| `submit1/09-start` | `pad=start`, 3 s | typed | 138 | 176x32 | 1,470 |
| `conhist10/04-tab` | `Tab` | **prompt** | 56 | 64x32 | -- |
| `conhist10/05-typed` | `stat fps` | **typed** | 138 | 176x32 | -- |
| `seq18/03-typed` (rebuilt disc) | `Say: at fps` | **typed** | 128 | 210x32 | -- |
| `seq18/04-executed` | `Enter` | absent | 0 | -- | -- |

Transcribed at 4x from the crop, the two states read **`(>_`** (an empty prompt with its caret) and
**`(> stat fps_`**. So the stock build draws the prompt as soon as the console opens and the text as it is typed, and
the token `(> ` is not a script literal: the byte pair `(> ` occurs in **none** of the 18 packages and not in
`default.xbe`, in ASCII or UTF-16, while `Engine.Console`'s 23-function table has no `PostRender` -- so the prompt is
composed at run time and no package string can name the class that draws it.

**The lag is one shot, and it is why the earlier readings contradicted each other.** In `submit1` the frame labelled
`04-tab` is empty, the *next* frame holds the empty prompt, and the frame after that holds all eight characters --
although the key step between them sent nothing valid. Each shot shows what the previous step rendered: an eight-key
burst plus a one-second wait is not enough to see the text, and three seconds is. `conhist9` renders faster (its `Tab`
frame already holds the prompt), which is why the same sequence produced opposite-looking frames in the two sessions.

**`ret` closed the console in `conhist9`.** 113 cyan px of `(> stat fps_`, unchanged through `kp_enter` (2,121 px of
frame diff: the caret), then **0 px** and 3,053 px of frame diff after `ret` -- the line gone and the world moving
again, which is precisely what `TypingClose()` does in the `IK_Enter` branch quoted above. The submit therefore exists,
and it is the main Return; `kp_enter` does not produce it.

**The same key did nothing at all in `submit1`.** `08-ret` is **byte-identical** to `07-kpenter` (0 differing pixels)
with the line still drawn, and `pad=start` after it changed 1,470 px and left the line drawn. Two sessions, one disc,
one key, opposite outcomes: the submit works and its delivery is not reliable, so a single frame cannot settle it and
the console object's `History`/`TypedStr` remain the oracle.

**What the frame still cannot say is whether the command ran.** No FPS counter is drawn in `conhist9/07-ret` -- a
420x120 top-left crop and a whole-frame read find only O'Neill's nameplate -- although this disc drew `60 cur FPS` /
`59 avg FPS` for the same command twice (`seq13`, `seq14`). Closing the line is the submit; the command's *effect* is
not in the picture, and `History[HistoryTop]` is where it would be.

**The next instrument is therefore not another boot of the sequence.** It is the console object read *in the frame
that the read is taken from*: `Tab`, `stat fps`, `ret`, then one `objects.py` walk that resolves the object the
`PlayerController` points at instead of filtering by class name -- the class filter has now been measured to return
only the class default object in every session, and what changed between `kp_enter` and `ret` is a state the frame
shows and the array does not name.

## The console is not in the object array once it closes (measured 2026-11-06)

The read U15 has been waiting on -- the live `Console`'s `TypedStr`/`History` -- needs the object to exist, and the
object array says it does not exist in the state every run so far has read it in. One boot: `M00` loaded, `Tab`,
`stat fps`, one `ret`, then a GDB stub left up and the array walked twice.

**The array is `0x6c21b0` in every state, and only `num` moves.** At this level `data=0x041e0000 num=48646
max=51886`; the title screen read `num=42152` with the same `data` and `max`, so the triple names the engine's array
and not a per-boot allocation.

**45,802 live objects over 295 pages** (`0x970000..0x7eb0000`; the whole walk is 3 m 29 s at this level, against 2 m 41 s
without the follow step). 45,794 resolved to a class table, 2,844 slots are null or implausible, and four 64 KiB pages were unreadable.
The histogram's head is `Function` 8,610, `ObjectProperty` 5,849, `FloatProperty` 4,430, `BoolProperty` 4,208,
`StructProperty` 3,403, `IntProperty` 3,208, `StrProperty` 2,472, then content (`Sound` 1,436, `StaticMeshActor` 927,
`Light` 579) -- a loaded level, not the title menu.

**Exactly one `PlayerController`, at `0x01c31000`, and none of its first 0x800 bytes points at a `Console`.** Its
object-address fields are:

| field | target | class |
|---|---|---|
| `+0x008` | `0x019c9ae0` | `ObjectProperty` |
| `+0x018` | `0x01f61068` | `Package` |
| `+0x024` | `0x00d9ae00` | `Class` |
| `+0x0a0`, `+0x0ac` | `0x02031400` | `LevelInfo` |
| `+0x150` | `0x02347300` | `DefaultPhysicsVolume` |
| `+0x370`, `+0x4bc` | `0x00d96600`, `0x00d94e00` | `Class` |
| `+0x3b8` | `0x0237d200` | `ScriptedController` |
| `+0x4c0` | `0x02345a00` | `PlayerReplicationInfo` |
| `+0x5e8` | `0x01c31000` | `PlayerController` (itself) |

The field exists in the layout -- `inventory/class_properties.csv` has
`Engine,Player,Console,UObjectProperty,0x2000:Transient;,Console`, and `PlayerController` extends `Player` -- and the
self-reference at `+0x5e8` shows the read window covers the whole object, so a null `Console` is a null field, not a
window that stops short. `--class Console` in the same state returns **one** object: the class default at
`0x02344b00`, a `defaultproperties` block (`FontNames(0)="SmallFont"`, `Images(0)=Shader'SG1_MainMenuTex...'`).

**That is the submit's other half.** `TypingClose()` closes the console when the text is submitted, and this state is
three to four minutes past that: the frame the run saved after `ret` is `absent` (0 cyan px) where `05-typed` held
109 cyan px in a 176x30 band, so the console really did close *and* the object went with it. A walk taken while the
prompt is up is the only walk that can see a `Console`; every previous one, including the two that reported "only the
class default object", was taken after the console had already gone.

**Reading one object no longer costs a walk.** `objects.py --object ADDR` reads an address directly and names its
guest pointers from each target's own vtable (`--refs`) and its UE2 `(Data, Count)` pairs (`--fstrings`), in seconds;
`--follow CLASS` does the same one step past a `--refs` field. The remaining experiment is a sequence that *ends with
the console open* -- a non-character submit key, or no submit at all -- followed by that read. Injecting keys after
`drive.py` has exited is not available: a QMP client opened on the run's `qmp.sock` connected and then timed out
waiting for the greeting, at 6 s and again at 60 s.

## A drawn prompt is a stock disc's, and its object is not in the array (measured 2026-11-06)

A drawn input line used to be the tell that the boot was the `Console=GUI.ExtendedConsole` swap, and a console object
was supposed to be reachable from the player. One run measured both: the disc's own `System/Default.ini`, and the
object array with the prompt open and text in it.

**The booted image is stock.** `$WORK/xemu/sg1.iso` (md5 `109fff2a5a0091932f4262eefda41855`) carries
`Console=Engine.Console`; `$WORK/console-iso/game.iso` (md5 `5c47159b3e4ec558d2532a177b4e6992`) carries
`Console=GUI.ExtendedConsole`. Same byte size, different file: identify the disc from the file, never from the size.

**`grep` on the image answers wrong.** The stock ISO still contains a stray `Console=GUI.ExtendedConsole` at offset
1,881,354,812, so a string hit -- or its absence -- says nothing about the config the engine reads. Read the file out of
the XDVDFS tree instead:

    python3 tools/xemu/xiso-cat.py --iso $WORK/xemu/sg1.iso System/Default.ini     # Console=Engine.Console
    python3 tools/xemu/xiso-cat.py --iso $WORK/xemu/sg1.iso --list System

`extract-xiso` is not installed on the desktop any more and a `-c` extract would be 2.2 GB; this reads one file in
about a second. The tool's walk is the same one `map-index.py` does: a directory entry's `left`/`right` are offsets
into the directory table in **4-byte words** (words, not bytes -- the first version read them as bytes and found no
file at all).

**That stock disc draws the prompt.** The sequence was `Tab`, `spc`, `stat fps`, `ret`, then `Tab`, `spc`, `hist` and
nothing else. `console-line.py` reads `absent` (0 cyan px) on the post-submit frame and `typed` -- 82 cyan pixels in a
128x32 band -- on the frame with `hist` typed, where the crop reads `(> hist_` and `stat fps`'s counter
(`59 cur FPS` / `58 avg FPS`) is still drawing above it. So a drawn line does not mean the swap, and a submit executed a
command on a stock disc.

**And the prompt still has no object.** With that frame on screen, one walk of `0x6c21b0` reads
`data=0x41e0000 num=48646 max=51886` and finds 45,802 objects over 295 pages; `--class Console` returns **one**
object, `0x02344b00`, whose first 0x800 bytes are the class's `defaultproperties` text and whose FString pairs carry
no text; `ExtendedConsole` -- a substring match, so a subclass instance would be caught -- returns none; and the
`PlayerController` at `0x01c31000` has no `Console` pointer anywhere in 0xc00 bytes. Its object fields are the same
eleven the closed-prompt walk named: `ObjectProperty`, `Package`, three `Class` objects, `LevelInfo`, `Level`,
`DefaultPhysicsVolume`, `ScriptedController`, `PlayerReplicationInfo`, itself at `+0x5e8`, `SceneReplicationInfo`,
`CheatManager`, `PlayerChatManager`.

**Where the console default does point is the interaction block.** `0x02344b00` holds `XboxViewport` at `+0x02c` and
`InteractionMaster` `0x02406720` at `+0x030`; that object's `+0x028`..`+0x034` name `XboxClient` `0x01fb9bc0`,
`GUIController` `0x02345500`, `Console` `0x02344b00` (the class default again) and `UIXInteraction` `0x05b10a80`. So
the console's registration is a list of interactions, one slot of which is the class default rather than an instance:
whatever draws and executes the prompt is in that list, and `Player.Console` is not where U15's `History` will come
from.

**What this changes.** `Tab` + `stat fps` + `ret` remain a working console on the shipped disc; the drawn line is no
longer evidence of a swapped image; and the next read is `objects.py --object 0x2406720 --refs --object-size 0x200`,
one step past the console default's `InteractionMaster`.


## The typed command is not in the pages the object array covers (measured 2026-11-06)

`objects.py --find-text/--find-ascii/--find-dword` answer the other half of a memory read: the walk says which
objects exist, this says **which address holds these bytes**. It is what U15's last paragraph asked for -- an
FString's text lives in its own allocation, so a typed command is findable even when no object the interaction
graph names carries the `(Data, Count)` pair -- and the answer for the console's input line is a negative.

**The state, proven from the frame.** `findtext1`: keyboard attached at 75 s, `Continue` -> ten d-pad presses ->
`M00`, one `a` for the intro card, one `a` for the tutorial modal, one `Tab`, one spent `spc` for `bIgnoreKeys`,
then `h i s t` and **no** submit. `console-line.py` reads `05-typed` as `typed` (70 cyan px, 128x28 at +34+213),
the same three-state shape the earlier runs recorded. `drive.py` **failed** that step's repaint check (1,570 px
against its 1,720 threshold) on the very frame that carries the line: a 70-pixel line cannot move a whole-frame
delta, so the line counter is the witness here and the delta is not.

**The scan.** `--array-pages` reads the array at `0x6c21b0` (`data=0x041e0000 num=48,646`) for its pointers and
scans only the 64 KiB pages they occupy -- the same page set the walk reads, rather than the span between the
lowest pointer and the highest. Measured: **295 pages in 150 contiguous runs, 18,880 KiB**, scanned in 145 s.

- **0 hits for the payload.** UTF-16LE `hist` plus its NUL terminator (10 bytes) appears in none of the
  18,624 KiB that were readable. Matching the terminator is what makes that a statement about the command line:
  the bare four characters would also match `History`, `HistoryTop` and `Histogram` in the class defaults.
- **The 256 KiB hole is named, not swallowed.** Four pages answer nothing after four attempts each (`0x22b0000`,
  `0x70c0000`, `0x7230000`, `0x7570000`); each is printed as `unreadable`, and the summary counts only bytes that
  were read. A negative over a scan with holes is a negative over the read part, which is why the addresses are
  in the output.
- **The stub reads at 130 KiB/s and refuses a 128 KiB request.** One 64 KiB page costs 0.49 s: 130 KiB/s measured
  in `0x980000` and 129 KiB/s repeated in `0x2000000`, a region that fails elsewhere, so the rate is the stub's
  and not the region's. A `--scan-chunk 0x20000` data request is refused every time -- four attempts, then
  `unreadable` -- in *both* regions, so the ceiling is one 64 KiB page per request (the stub's packet is
  `0x20020` including its header). The 123 MB span is therefore ~16 minutes of stub time, which is what makes
  the page set worth computing: 2.5 minutes buys the 19 MB that holds every object.
- **What the negative leaves open.** The cheap hypothesis -- the `TypedStr` buffer is allocated in the same pages
  as the objects -- is dead, so the buffer is outside them, and the span scan is the expensive way to find it.
  Only the UTF-16 form was searched: the two probes that followed the scan used `zzzz`, a pattern that cannot
  occur, to measure the read rate. An 8-bit copy (`--find-ascii`, added after this scan) is still unexcluded.
- **Guest RAM cannot be dumped after the harness exits.** xemu's `qmp.sock` outlives `drive.py`, but a second
  client connects and never reads a greeting: `info status` was silent for 30 s and
  `pmemsave 0 0x8000000 <file>` silent for 300 s with no file written. That repeats the recorded post-run
  `sendkey` negative, now for the monitor as a whole, and it moves any RAM dump inside the boot:
  `--step='monitor=pmemsave 0 0x8000000 <file>'` is one step and one argv, and is untested.

## The whole guest dumps inside the boot and scans in 0.1 s, and the typed line is in it (measured 2026-11-06)

The section above closes the *after-the-fact* route and leaves one step untested. It works, and it makes every
coarse memory question cheap: `--step="monitor=pmemsave 0 0x8000000 \"<path>\""` writes all 128 MiB of guest RAM
to a host file **inside** the same boot. Two boots produced byte-identical sizes, 134,217,728 bytes each
(`$WORK/xemu/findtext3/ram.bin` at 17:07 and `findtext4/ram.bin` at 17:18), and the step itself prints nothing.
Two details, both measured: `help pmemsave` answers, so the command is in xemu's monitor; and the filename has to
arrive **quoted**, because a bare path is parsed as an expression and the monitor rejects it
(`invalid char 'h' in expression`) -- the quotes therefore live inside the step value.

**Cost, measured against the stub.** `tools/xemu/ramscan.py` searches the whole image for one UTF-16 pattern in
**0.1 s** (0.19 s including the interpreter). The same 128 MiB over the GDB stub is ~17 minutes at the recorded
7.9 s/MiB (145 s for 18,880 KiB), so the dump is worth the two seconds it costs to write. `--refs` is the
expensive form -- it searches a pointer for each of the 33 even addresses in the 0x40 before the hit -- and still
costs 2.3 s.

**The pairing is what makes the hit mean anything.** `findtext3` and `findtext4` are the same script with only
`$L` changed, so the state is the only variable:

- `findtext3`: `01-list` was **0 px** -- the pad press never took and the game stayed on the title -- `console-line.py`
  reads `absent` on `05-typed`, and the dump has **0 hits** for UTF-16 `hist` plus its NUL terminator.
- `findtext4`: `01-list` **37,878 px PASS**, `05-typed` PASS with a whole-frame change, `console-line.py` reads
  `typed` (95 cyan pixels, 128x33 at +34+208), and the dump written one step later has **exactly one hit**.

So the payload appears exactly in the boot whose frame says the line is drawn, and nowhere in the boot that never
left the title. This is the read the stub needed 2.5 minutes and 295 pages for, and it comes back in a tenth of a
second.

**What the bytes are.** At `0x06823b78` the ten bytes are `20 00 68 00 69 00 73 00 74 00 00 00` = `" hist"`, and
the pattern matched at `0x06823b7a` -- two bytes in, on the `h`. The leading space is the `spc` step that opens the
sequence, so in this boot the space was **not** the swallowed keystroke; and because a text pattern matches inside
a string, a pointer to the string is a pointer to an address *before* the hit, which is what `--ref-window` is for.

**Nobody points at it.** With the window, no 4-byte-aligned dword anywhere in 128 MiB equals the match address or
any even address in the 0x40 before it, and no known class table sits within 0x8000 before the bytes, so the
buffer is not a field of any object the vtable walk can name either. The one dword that does point into the
surrounding 16 KiB is at `0x022a67bc` -> `0x06823a34e`, 0x830 bytes before the text, and no class table precedes
that one either. This is a negative about the *image*, not a failure of the walk: a `Data` pointer that is absent
from 128 MiB of RAM can only be live in a register or in the stack frame of whatever draws the line, and
`pmemsave` covers neither. The next instrument is one more step in the same boot --
`monitor=info registers`, or a GDB stack read at that step -- and it needs no new data.

**The 8-bit copy is excluded in this state.** `--ascii hist` returns 0 hits in the image that holds the UTF-16
copy, which closes the "an 8-bit `char*` copy is still unexcluded" caveat for the typed line.

A dump is guest data: it stays in `$WORK` and is never committed. `ramscan.py` prints addresses, counts and class
names, the same rule as `objects.py`'s string readers.

## The whole image holds two mentions of `Console`, and one of them is the class default (measured 2026-11-06)

`objects.py` can only see what `GObjObjects` points at -- 295 pages, 18,880 KiB in the states measured so far -- so
"there is no live `Console`" was a statement about the array's pages. `ramscan.py --objects` asks the image instead:
every class table in `inventory/xbe_class_tables.csv` is searched for as a 4-byte-aligned dword, and each hit is
screened by the words after it (`object_shape`: a small positive `ObjectInternalInteger`, then two words that are
each 0 or an address inside the image).

**The census, over all 131,072 KiB**: 492 class tables produce **1,606** four-aligned occurrences in 32.8 s (one
pass per table; counts stop at `--limit`, so "15" means "at least 15"). The screen matters more than the total: the
`Console` table `0x0050cb80` occurs **twice in the whole image**, and a raw hit count would call both of them
objects.

| address | shape | the words after the table |
|---|---|---|
| `0x00291bbc` | `not-object` | `0xf6d1068 0xb5206800 0x106a0001` -- instruction bytes, a `mov` that mentions the table |
| `0x02661b00` | `object` | `0x8d34 0x230eaf0 0x1ffc480 0x0 0xffffffff` -- an object header |

`0x02661b00` is the object the array walk called the class default, and it has the same signature here: from
`+0x440` its fields run into inline UTF-16 default-property text (`CPUSPEED=`, `Artist: %s`, `Title: %s`), which is
what a class-defaults blob looks like and what a live console does not. It also does not point at the typed line:
the only `hist` in this image is at `0x06823b78` and nothing names it (see the section above). So in the state whose
frame draws `(> hist_`, the image contains the console's *default* and no live console instance -- 7.3's negative is
now about all of RAM, not about 295 pages.

**What the census is not.** It is a lower bound over the 492 classes whose tables are in the inventory (the dump's
18 packages declare 2,318 classes, `tools/pkg-script-text.py`), so 1,606 is not an object count and a class with no
hit is only as strong as the table inventory. What it does answer is where a *given* class appears: `--class SUBSTR`
prints each hit with its header words and the screen's verdict, which is how the two `Console` mentions above were
separated.

## The dump is physical memory, the CPU's addresses are linear — and with the translation the typed line has an owner (measured 2026-11-06)

`pmemsave` writes the guest's *physical* memory, and every address in this project's notes is a *linear* one: the
XBE's own section table, `objects.py`'s walk, a class-table VA, a breakpoint we set. On this build the two spaces are
not the same, so an address read out of a dump is neither a value a pointer can hold nor an address a watchpoint can
watch. `tools/xemu/vamap.py` closes the gap out of the dump itself: `info registers` gives `CR3` (`0x0000f000` at the
points measured), the page tables are ordinary 4 KiB RAM, and i386 two-level paging (PAE off — `CR4=0x000610` sets
PSE, not PAE) turns either address into the other, in both directions.

    python3 tools/xemu/vamap.py $WORK/watch-ram.bin --cr3 0xf000 --pa 0x6bfcb78 --va 0x11000 --stats

**The walk is right, and that is checked against content rather than against itself.** `--va 0x11000` answers physical
`0x613000`, and those 32 bytes are `.text`'s prologue at file offset 0x1000 of `default.xbe`
(`8b 44 24 04 56 57 8b 79 04 …`); `--va 0x4d1cc2`, the VA of an assert path in `.rdata`, answers physical `0x67dcc2`,
where the bytes read `…\XboxAudio\Src\XboxAudioSubsystem.cpp`. The map's shape: **137 present PDEs, 63 of them 4 MiB
pages, 93,495 mapped pages over 93,399 distinct physical pages** — the image is spread across physical pages, so no
constant offset relates the two spaces (`.text`'s first page is at `0x613000`, an assert string's page at `0x67dcc2`,
and the same code bytes also appear at `0x36e8c0`, a second mapping of the same page).

**This flips U15's central negative.** The typed console line in `findtext5` is at physical `0x06bfcb78`
(`ramscan.py --text hist`, one copy): its linear address is **`0x0802db78`**. Searched as a dword over all 131,072 KiB,
that value occurs **exactly once**, at physical `0x02661b44` — `+0x44` inside the object at `0x02661b00` whose first
word is the `Console` class table `0x0050cb80`. The earlier dump agrees: `findtext4`'s line is physical `0x06823b78`,
linear `0x07fedb78`, and the pointer is at the same `+0x44` of the same object address — the heap moved `0x3d9000`
between those two boots, the object did not.

| offset | value | field |
|---|---|---|
| `+0x44` | the string's linear address | `FString.Data` |
| `+0x48` | 6 | `FString.Count` |
| `+0x4c` | 6 | `FString.Max` |
| `+0x50` … `+0x74` | all zero | `History[0..3]`, empty |

Six bytes is `" hist"` — five characters and the terminator — so this is `Console.TypedStr`, the field the class
declares first, and `History[16]` behind it is empty because the run typed and never submitted. The object is the one
`objects.py --class Console` reports; its `+0x440` runs into `defaultproperties` text, which is why earlier notes
called it the class default. Whether it is the CDO or a live instance is not settled by this read, and does not change
the pointer.

**So the earlier search was right and its target was wrong.** "Nothing in 128 MiB names the buffer" was a search for
the physical *value* in a linear *space*: the owner is one dword away once the address is translated, and `--refs`
finds no class table behind `0x02661b44` for the same reason a class default has none — a class names it, not the
game's arrays.

**The register file at the dump step is a ring-0 frame, and that is the answer `monitor=info registers` earned.**
`findtext5`, step 47, with the console drawn (`console-line.py` reads `typed`, 67 cyan pixels):

| register | value |
|---|---|
| `EIP` | `0x00020669` (inside `.text`) |
| `CPL` | **0** |
| `ESP` / `EBP` | `0xd0169d38` / `0x0979d700` |
| `EAX` / `ESI` / `EDI` | `0xd016a140` / `0xd016a158` / `0xd0169d48` |

A single stopped sample is whatever the CPU was doing: here ring 0 on a kernel stack, with no game pointer in the
file. The earlier plan — "the pointer can only be live in a register or in the stack frame of whatever draws the
line" — was also wrong on its own terms, because the stack *is* inside a `pmemsave`. What was missing was the address
space, not the channel.

**The stub does implement read watchpoints, and they have to be armed on the linear address.**
`tools/xemu/mem.py --watch ADDR` arms `Z3` and reports each stop's register file, the stack words around `ESP` within
`--window` of the address, and `EIP`; `--selftest` proves the RSP half on a fake stub, which is why `make ci` can run
it with no emulator. Measured: `Z3,<addr>,8` answers `OK` on xemu's stub, and a watchpoint armed on the *physical*
address never stops in 25 s (`findtext6`) — which is what a page mapped somewhere else should do. `drive.py`'s
`watch=TEXT` step now does the whole chain inside one boot (`pmemsave`, find the UTF-16 text with its terminator, back
up to the string's first character, `CR3` from `info registers`, translate, arm `Z3` over 8 bytes); that translation
step compiles and its halves are each measured, but **the boot that would exercise the pair end to end is the next
run's first job**, so no claim is made here about which instruction reads the line.
