# The path to a build, and what the estimates rest on

Two different goals get called "builds and runs", they are three orders of magnitude apart in effort, and conflating
them is why estimates for this kind of project are usually worthless. This page separates them, states the measured
position of each, and gives ranges with their reasoning rather than a number to hold me to.

Everything below is measured from committed artifacts; the command that regenerates each is in the file named.

## Goal A — a hybrid rebuild: our scripts, the shipped engine and assets

Compile the 2,319 classes' `ScriptText` with a UE2 compiler, drop the resulting `.u` packages next to the original
assets, and boot the game in xemu.

| what it needs | measured state | source |
|---|---|---|
| shipped source for every class | **2,319 of 2,319** | `inventory/package_script_text.csv` |
| every input a rebuild needs, per class | **1,712 of 2,319 (73.8%)** | `inventory/compile_readiness.csv`, `docs/COMPILE.md` |
| defaults readable, or nothing to set | 843 read + 869 not-applicable; **607 blocked** | same |
| a UE2 compiler (`ucc`) | **absent**. Wine is installed on the desktop; no UE2 runtime anywhere | this session, measured |
| package integrity check (`Packages.md5`) | not reproducible; **bypass is the plan** | `docs/PACKAGE_CHECK.md` |
| a boot harness | **works**: the title boots in xemu and executes game code | `docs/XEMU.md` |
| mission-load automation | absent — no input injection; `screendump` unavailable | `docs/XEMU.md`, PLAN 7.2 |

**Estimate: 2–4 months of steady work, and the range is dominated by one unknown.** If a compiler build matching
this package version (130, licensee 4) works under Wine, the remaining work is mechanical: 607 default blocks, a
package writer, the check bypass, and a boot-and-diff loop. If no matching compiler exists — the version/licensee
pair is from a December 2005 Xbox prototype — then the hybrid goal has no path at all short of writing a compiler,
and no estimate is honest.

Confidence: moderate on the work *after* the compiler, low on obtaining it. That is why the next actionable step is
not more measuring but acquiring `ucc` and trying to compile one trivial class.

## Goal B — a full reconstruction: C++ that compiles and links

Rebuild the engine and game code from the decompilation. This is the project's stated long-term aim, and it is where
the numbers are least encouraging:

| what it needs | measured state |
|---|---|
| functions to reconstruct | **14,957** |
| functions carrying any name at all | **1,988 (13.29%)** (`symbols/xbox_functions.csv`) |
| functions with reconstructed C | **0** — `src/Native/` is empty |
| class tables identified | 466 of 492 named (`inventory/xbe_class_tables.csv`) |
| classes agreeing with the 2006 source | 204 of 291 (`inventory/oracle_diff.csv`) |
| assets indexed | 73 maps, 1,923 music cues, 55 `.uax` packages / 3,047 sounds |

At this project's rate — a handful of measured chunks per session, ~19 new native names per session across the last
stretch — naming all 14,957 functions is on the order of **600+ sessions**, before a single line of reconstructed C.
Reconstruction is slower than naming. A realistic statement is **years, not months**, and the honest framing is
that Goal B does not get there by continuing this pace function by function.

What would change it: a different *method* rather than more effort. Reconstructing from a matching UE2.5 source
lineage (the engine portions are largely stock) and treating the fork's own classes — the ~500 insertion points in
`inventory/fork_surface.csv` — as the real target, is a tractable project. Reconstructing 15,000 functions one at a
time is not.

## The critical path, in order

1. **Get `ucc` running.** One trivial class compiled and byte-compared against the shipped package decides whether
   Goal A is months or never. Nothing else should be scheduled ahead of it.
2. **Close the 607 default blocks** (`docs/CLASS_DEFAULTS.md`) — the only remaining data gap for a rebuild, and the
   one thing more measuring can fix without outside inputs.
3. **Write the package writer + check bypass** (`docs/PACKAGE_CHECK.md`, PLAN 6.1/6.2) — the bypass is decided;
   the writer is unbuilt.
4. **Boot-and-compare loop** — recompile, boot in xemu, compare behaviour. Mission-load automation (7.2) is the
   part that does not exist yet, and without it "it runs" means "the menu comes up".
5. **Goal B, restructured** — fork classes first, stock engine by lineage, not by address order.

## What is not being claimed

* That the shipped source compiles at all: `L1` in `docs/STATUS.md` is `--`, because no compiler has been run.
* That a hybrid build would *behave* identically: bytecode equality is unmeasured and needs the 3.7 comparator.
* Any date. The only defensible statement is the shape: Goal A is bounded by one acquisition, Goal B is bounded by
  a change of method.
