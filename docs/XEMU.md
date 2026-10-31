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

**The list is 41 entries, ten per page**, and the campaign maps are inside it. Pages read off the frames: `0`–`9`
`CTF-Abydos`, `CTF-LostCity`, the seven `DM-*` maps and `Entry`; `10`–`19` `M00` … `M07C`; `20`–`29` `M07D`, `M07E`,
`M10A`–`M10D`, `M12A`–`M12D`; `31`–`40` `M20A` … `M21D`, `MainMenu`, `SGCTF-AbydosVsSGC`. So `M20A` sits at **index 31**
— one past a page boundary — and both routes to it stop one short: `home` then three `pgdn` lands on index 30, `end`
then one `pgup` lands on index 30. **A single-step key is the missing piece**, which is the d-pad (the list's own
`InternalOnXControllerEvent` handles `XC_PadUp`/`XC_PadDown` as `Up()`/`Down()`), and that is U14's virtual pad.

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
