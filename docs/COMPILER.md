# The compiler: one runs, and it cannot read this build's packages

PLAN 3.6 has been recorded as blocked on "a matching `ucc`" since the first session, and `docs/PATH.md` states the
position plainly: *"a UE2 compiler (`ucc`) — **absent**. Wine is installed on the desktop; no UE2 runtime anywhere."*
This page narrows that. A UE2 compiler now runs here, compiles a scratch package, and is **measured to be unable to
read the dump's packages at all** — so the gate is not "any `ucc`", it is a compiler whose linker knows this build's
format. That is a much smaller thing to search for and a much harder one to find.

    tools/ue2rt-probe.py --runtime <extracted runtime>/ --work <scratch>/ --dump <dump root>

Reproduce with `python3 tools/ue2rt-probe.py ...`; every number below is that run's output.

## What was tried

Epic's **UE2 Runtime, build 2226** (compiled 2003-09-17) is a public engine-runtime release, not game data: it
ships `System/UCC.exe`, the engine packages and no game content. Its installer is an InstallShield 5 self-extractor
and `7z x` unpacks it without running it (120 files); no `wine` installation step is needed. The probe stages a
fresh copy of that tree under `--work` and drives `UCC.exe` through it, so the run is idempotent and writes nothing
outside `--work`.

| stage | result |
|---|---|
| A. runs under Wine 11.21 (desktop) | yes — `ucc.exe: UnrealOS execution environment`, `ucc help` lists `make` |
| A. build reported in `System/UCC.log` | `Init: Version: 2226` |
| B. a scratch package compiles from source | yes — `Parsing ProbeThing`, `Compiling ProbeThing`, `Importing Defaults`, `Success - 0 error(s), 0 warning(s)` |
| C. the emitted package's header | signature `0x9E2A83C1`, **version 126, licensee 0**, 682 bytes |
| D. an existing `.u` is rebuilt | **no** — it is skipped, and deleting the `.u` forces the rebuild |
| E. reads the dump's 18 packages and compiles a class extending a game class | **no** — `Bad expr token 03` in `SerializeExpr` |

The build's own package version is **130 / licensee 4**, so the gap is four file versions and one licensee.

## Three behaviours of `ucc make` that are not in its help

All three cost a session each to find, and all three are measured with the probe:

1. **`EditPackages` is read from `System/<Game>.ini`, not `Default.ini`.** Here that is `System/UE2Runtime.ini`.
   Editing `Default.ini` (the file that looks like the config) has no effect at all.
2. **An entry appended to the end of the ini is ignored**, because it lands in whatever section is last rather than
   in `[Editor.EditorEngine]`. The entry has to sit inside that section — the probe inserts it directly after the
   section header for this reason.
3. **A package whose `.u` already exists is not rebuilt**, even when its source is newer; deleting the `.u` is what
   forces a rebuild. Four consecutive runs show it: compile, skip, skip (after touching the source), compile (after
   removing the `.u`). Two consequences follow, and the second one matters:
   - a rebuild has to move the old package aside before it can write, and
   - `Core` cannot be rebuilt from source by this tool at all, because `UCC.exe` boots on `System/Core.u` and cannot
     start without it.

Sources go in `<root>/<Package>/Classes/*.uc` and output lands in `System/<Package>.u`; that part is conventional.

## The read test, and what it settles

Overlaying the dump's 18 packages into the staged tree and compiling a class that `extends StargatePlayer` fails
before a single line of script is parsed:

```
Bad expr token 03
History: SerializeExpr <- (03) <- SerializeExpr <- (04) <- SerializeExpr <- (6C) <- ...
         <- UStruct::Serialize <- (Class Core.Object) <- UClass::Serialize <- LoadObject
         <- ... <- UMakeCommandlet::Main
```

`SerializeExpr` is `UStruct`'s default-property expression stream, and version 126's reader does not know this
build's token number is 3. So the failure is in the **token grammar**, not in a version guard: patching the version
constant in the binary cannot fix it, and neither can a newer-but-still-UE2.5 compiler — this build is 130, every
public UE2.5 release is 129 or earlier (UT2004 is 129), and **nothing newer is obtainable**.

One detail from the dump is worth recording because it points the other way: `UPreview.u` is **128/4** while the
other seventeen packages are **130/4**. The build's own compiler wrote a 128 package two versions before its
current output, and the engine loads it — so this engine's linker reads older packages of its own lineage. The
missing direction is older toolchain, newer package, which is exactly the direction a rebuild needs.

## What follows for 3.6 and 6.1

The packages are the compiler's input: a script that references `Engine`, `GamePlay`, `StargateGame` and the rest
cannot be compiled by a tool that cannot load them, and *every* Stargate package does. So the options are, in the
order they deserve:

1. **Build the fork's own compiler.** Perception's toolchain is what wrote these packages, so it is the only
   compiler guaranteed to agree with them, and the privately held 2006 studio tree is where it would be if it is
   anywhere. That is the next thing to check, and it is a question about what the tree contains rather than a
   measurement: whether the UnrealScript compiler's *sources* are in it. Not measured; nothing here claims they are.
2. **Write the compiler.** `docs/BYTECODE.md` already has the opcode table (35 of 256 opcodes still refused), the
   class serializer is identified (3.7: slot 11 of every class table, `UStruct::Serialize` at `0x000392b0`), and
   the writer reproduces all 18 packages byte for byte (6.1). What does not exist is the front end: parser, type
   checker, code generator. That is months, and unlike option 1 it does not depend on data this repository may not
   carry.
3. **Anything else.** Ruled out by measurement, not by opinion: a stock UE2 compiler from any public release is
   older than this build, and this page's test shows what happens when one is pointed at it.

## A hypothesis the read test suggests (not measured)

The same `SerializeExpr` stream may be what `docs/CLASS_DEFAULTS.md` has been calling the "withheld" residue: 545
class payloads whose candidate block parses **package** names as property names and carries hash-like 4-byte values
that repeat across classes. A tag stream would not do that; an expression stream referencing imports would. If the
residue is `FExpr`, then the token set is the grammar that decodes those defaults, and the function to read is the
one the linker just refused — `UStruct::Serialize` at `0x000392b0` (PLAN 3.7). Recorded as `docs/UNKNOWNS.md` U17
and **not** claimed: no block has been decoded this way.

## Reproducing

```
7z x UE2Runtime-22261903.exe -o<runtime>            # Epic's UE2 Runtime build 2226; unpacked locally, never committed
python3 tools/ue2rt-probe.py --runtime <runtime> --work <scratch>
python3 tools/ue2rt-probe.py --runtime <runtime> --work <scratch> --dump <dump root>   # adds the read test
```

`--check` validates the arguments without running anything, so `make ci` includes the tool without needing Wine, a
runtime or the dump.
