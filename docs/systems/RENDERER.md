# The renderer half: five engine translation units, and the fork's changes are decisions

PLAN 4.8 asks what Perception changed in the engine against stock UE2.5, and the native half was left as
"renderer, memory, compiler behaviour". The corpus census (`docs/MODULE_CENSUS.md`) sized it: `D3DDrv` is
**129 bodies**, 22 of them named, and the *population* is no longer the open question. This page is the read
of that module — what its bodies claim, what they log, and what they call — done with one instrument so the
numbers and the prose cannot drift:

    python3 tools/body-read.py --module D3DDrv --root $WORK/module-export -o inventory/d3ddrv_bodies.csv
    python3 tools/body-read.py --write-doc docs/systems/RENDERER.md

The bodies are game-derived and stay under `$WORK`; the committed artifact is `inventory/d3ddrv_bodies.csv`
(one row per body: its translation unit, its literal counts, its callees by module, its slot offsets) and this
page, whose tables are generated from it. `--check` reads two synthetic bodies, so the contract in `make ci`
needs neither the dump nor the export.

## What the module is

Every body that carries an assert names the file it was compiled from, and all of them sit in one tree —
`…\alienbrainWork\Stargate-sg1\Game\GameXBOX\D3DDrv\Src\` — under **five** translation units:
`D3DRenderDevice`, `D3DRenderInterface`, `D3DRenderState`, `D3DResource`, `D3DMaterialState`. The class
labels the same asserts produce are the engine renderer's own vocabulary — `D3DRenderDevice`,
`D3DRenderInterface`, `D3DRenderState`, `D3DResource`, `D3DMaterialState` — and **not one body in the module
carries a Stargate-specific class or function name**. So the renderer half is not a black box linked out of
the XDK: it is the engine's Direct3D 8 render device, compiled from the fork's own tree into the game image.
That is also why 4.8 could not answer it from the fork's script packages — `docs/systems/FORK_SURFACE.md`
counts 503 script classes, and none of them is a renderer.

## What it does, in its own words

The literal strings are the module's own narration, and they line up with a D3D8 render device's job:

| stage | what the bodies say |
|---|---|
| device creation | `Creating device`, `CreateDevice failed`, `GetAdapterDisplayMode failed.`, `CheckDeviceFormat failed`, `CheckDepthStencilMatch failed` |
| format selection | `Using back-buffer format %u(%u-bit)`, `Using depth-buffer format %u(%u-bit)`, `No fullscreen display modes found`, `No acceptable display modes found`, `Best-match display mode: %ix%ix%i@%i` |
| vertex processing | `hardware` / `software` / `mixed`, `D3D Driver: CreateDevice: will use %s vertex processing`, `D3D Driver: CreateDevice: will use %s transform and lighting.`, `D3D Device: Device doesn't support D3DVTXPCAPS_MATERIALSOURCE7` |
| resolution changes | `Enter SetRes: %dx%d Fullscreen %d`, `Can't change resolution while render device is locked!`, `Viewport->ResizeViewport( (Fullscreen ? BLIT_Fullscreen : 0) \| BLIT_Direct3D, NewX, NewY, bSaveSize )` |
| resource creation | `CreateVertexBuffer failed(%s).`, `CreateIndexBuffer failed(%s).`, `DestFormat == TEXF_RGBA8` |
| lighting / shaders | `LightID < VS_Lights_MAX`, `Engine_UnPixelShaderMaterial` called from three 85-byte sibling bodies, `D3DRI->HitStack.Num() == 0` |

Three things follow, none of which the corpus census could see. The device is **probed, not assumed**: this
build enumerates display modes, picks a back-buffer and a depth-buffer format, checks depth-stencil
compatibility and logs all three decisions. Vertex processing is a **decision with three answers**
(`hardware`, `software`, `mixed`), taken after testing `D3DVTXPCAPS_MATERIALSOURCE7` — which is the one place
in the module where a capability is named in the code rather than read from a table. And the render device
carries a **vertex-shader lighting path** (`VS_Lights_MAX`, a light-index bound, and calls into the engine's
`UnPixelShaderMaterial` constructors), which is the piece of the renderer a stock build of this era would
not have on the Xbox path.

## Where the Xbox-specific part is not

Two negatives, both from the same rows:

* **No body calls into `XboxDrv`.** The cross-module callee table is `Core` and `Engine` only; not one of the
  129 bodies makes a resolved call into the module that carries the console's own D3D pieces. Whatever the
  fork changed on the console side, it is not spliced into the render device's call graph at this level.
* **No body names an asset.** `docs/MODULE_CENSUS.md` counted 9 of 6,063 bodies export-wide that reference an
  asset path and 25 that reference a config string; none of the asset ones is in `D3DDrv`. The renderer gets
  its material state from the engine's objects, not from file names.

The device itself is reached the way a COM-style API is: through a table on an interface object —
`call [obj + off]` in 11 bodies at 20 distinct byte offsets, the largest `0xbc`. Naming that table is
4.2's signature work (U9): the offsets are recorded here as offsets, not as `IDirect3DDevice8` slots, because
no signature database in this repository can name them yet.

## What this settles, and what it does not

**Settled.** 4.8's renderer question has a shape: the renderer in this image is the engine's D3D8 render
device compiled from `GameXBOX\D3DDrv\Src`, five translation units and five classes, with device creation,
format probing, vertex-processing selection, resolution changes, resource creation and a vertex-shader
lighting path all readable in its own strings. The fork's footprint inside the module is a set of
**decisions** — which formats to probe, which vertex-processing mode to accept, whether the VS lighting path
is used — not a set of new classes.

**Not settled.** Which of those decisions *differ* from stock: a diff needs a stock UE2.5 build of the same
compiler, which this dump does not contain (the same wall 4.8 hit on the script side). The D3D8 caps it asks for are no longer open: `docs/UNKNOWNS.md` U11 was measured at the create
call (`0x002b2dcd`) -- `BehaviorFlags` 0x40 (hardware vertex processing) and a present-params block read
out of a live guest (640x480, back-buffer format 30, depth format 42, one back buffer, `Flags` 0x121).

## The measurement

<!-- generated:renderer-body-read -->
`D3DDrv`: 129 bodies, 37786 bytes, 194 distinct literals (172 of them not a build path).

| translation unit | bodies | bytes | slot calls | cross-module callees |
|---|---:|---:|---:|---:|
| `(none)` | 107 | 22246 | 10 | 71 |
| `D3DRenderInterface.cpp` | 7 | 3516 | 0 | 2 |
| `D3DResource.cpp` | 6 | 6898 | 21 | 15 |
| `D3DRenderState.cpp` | 4 | 1107 | 0 | 1 |
| `D3DRenderDevice.cpp` | 3 | 3127 | 7 | 3 |
| `D3DMaterialState.cpp` | 1 | 770 | 0 | 0 |
| `D3DRenderInterface.h` | 1 | 122 | 0 | 1 |
| **total** | **129** | **37786** | **38** | **93** |

| callee module | bodies calling it |
|---|---:|
| `Core` | 29 |
| `Engine` | 18 |

Slot calls: **11** of 129 bodies reach an interface object through `call [obj + off]`, **20** distinct offsets, all <= `0xbc` (`0xc`, `0x10`, `0x14`, `0x18`, `0x1c`, `0x20`, `0x24`, `0x28`, `0x2c`, `0x38`, `0x3c`, `0x44`, `0x88`, `0x90`, `0x94`, `0xa8`, `0xb0`, `0xb4`, `0xb8`, `0xbc`).

| address | name | translation unit | size | literals | slots | callee modules |
|---|---|---|---:|---:|---:|---|
| `0x002aa740` | `D3DDrv_D3DMaterialState` | `D3DMaterialState.cpp` | 770 | 2 | 0 | - |
| `0x002aefb0` | `D3DDrv_D3DRenderDevice` | `D3DRenderDevice.cpp` | 434 | 2 | 2 | - |
| `0x002b27f0` | `D3DDrv_D3DRenderDevice` | `D3DRenderDevice.cpp` | 2513 | 20 | 5 | `Core` |
| `0x002b3c80` | `D3DDrv_D3DRenderDevice` | `D3DRenderDevice.cpp` | 180 | 4 | 0 | `Core` |
| `0x002b5250` | `D3DDrv_D3DRenderInterface` | `D3DRenderInterface.cpp` | 212 | 2 | 0 | - |
| `0x002b5330` | `D3DDrv_D3DRenderInterface` | `D3DRenderInterface.cpp` | 96 | 2 | 0 | - |
| `0x002b59b0` | `D3DDrv_D3DRenderInterface` | `D3DRenderInterface.cpp` | 174 | 3 | 0 | - |
| `0x002b5b10` | `D3DDrv_D3DRenderInterface` | `D3DRenderInterface.cpp` | 266 | 2 | 0 | - |
| `0x002b7560` | `D3DDrv_D3DRenderInterface` | `D3DRenderInterface.cpp` | 1324 | 3 | 0 | - |
| `0x002b8505` | `D3DDrv_D3DRenderInterface` | `D3DRenderInterface.cpp` | 615 | 3 | 0 | `Core` |
| `0x002b8780` | `D3DDrv_D3DRenderInterface` | `D3DRenderInterface.cpp` | 829 | 2 | 0 | `Core` |
| `0x002ba5f0` | `D3DDrv_D3DRenderInterface` | `D3DRenderInterface.h` | 122 | 2 | 0 | `Core` |
| `0x002bb5a0` | `D3DDrv_D3DRenderState` | `D3DRenderState.cpp` | 198 | 3 | 0 | - |
| `0x002bb670` | `D3DDrv_D3DRenderState` | `D3DRenderState.cpp` | 140 | 3 | 0 | - |
| `0x002bb708` | `D3DDrv_D3DRenderState` | `D3DRenderState.cpp` | 56 | 3 | 0 | - |
| `0x002bd6f0` | `D3DDrv_D3DRenderState` | `D3DRenderState.cpp` | 713 | 1 | 0 | `Core` |
| `0x002be570` | `D3DDrv_D3DResource` | `D3DResource.cpp` | 4889 | 18 | 12 | `Core`, `Engine` |
| `0x002bf960` | `D3DDrv_D3DResource` | `D3DResource.cpp` | 349 | 4 | 3 | `Core` |
| `0x002bfbc0` | `D3DDrv_D3DResource` | `D3DResource.cpp` | 733 | 5 | 2 | `Core` |
| `0x002c0530` | `D3DDrv_D3DResource` | `D3DResource.cpp` | 279 | 2 | 2 | `Core` |
| `0x002c0650` | `D3DDrv_D3DResource` | `D3DResource.cpp` | 239 | 3 | 0 | `Core` |
| `0x002c0740` | `D3DDrv_D3DResource` | `D3DResource.cpp` | 409 | 2 | 2 | `Core` |
<!-- /generated:renderer-body-read -->
