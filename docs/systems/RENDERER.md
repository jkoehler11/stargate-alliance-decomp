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

## What the device reports (measured)

U11's other half is not what the code *asks* for but what the device *reports*: the caps the render device tests
once `CreateDevice` has returned. That is a struct the render device keeps, so the same instrument can stop on
the test and dump the window around it:

    python3 tools/xemu/bp-args.py --port 12348 --force --break 0x2b27f0 --break 0x2b3008 \
        --dump 'esi+0x4000:0x300' --stops 6 --json $WORK/bp1.json > $WORK/bp1.log
    python3 tools/xemu/d3d-caps.py --log $WORK/bp1.log --base 0x02384000 --caps 0x19c

`0x002b3008` is `test BYTE PTR [esi+0x4238],0x2`. The byte it tests is the first byte of field 39 of a
**53-dword `D3DCAPS8` that starts at `esi+0x419c`**, and every field boundary falls where the SDK puts it —
`MaxTextureWidth`/`MaxTextureHeight` at `+0x58`/`+0x5c`, the five guard-band floats at `+0x74..+0x84`,
`MaxTextureBlendStages`/`MaxSimultaneousTextures` at `+0x94`/`+0x98`, `MaxPointSize` at `+0xb0`,
`MaxVertexIndex`/`MaxStreams`/`MaxStreamStride` at `+0xb8..+0xc0`, the two shader-version words at
`+0xc4`/`+0xcc` — with the runtime's own driver name, **`"Xbox driver"`**, written at `+0xd4`. Fifty-three
positional fields do not line up by accident, so this is the reported caps and not a coincidence in a heap
window. `tools/xemu/d3d-caps.py` decodes it and refuses a window that is not caps-shaped (`DeviceType` must be a
`D3DDEVTYPE`, the two texture limits must be powers of two).

| offset | field | value |
|---|---|---|
| `+0x000` | `DeviceType` | `0x00000001` (1) |
| `+0x004` | `AdapterOrdinal` | `0x00000000` (0) |
| `+0x008` | `Caps` | `0x00020000` (131072) |
| `+0x00c` | `Caps2` | `0x00000000` (0) |
| `+0x010` | `Caps3` | `0x00000000` (0) |
| `+0x014` | `PresentationIntervals` | `0x80000007` (2147483655) |
| `+0x018` | `CursorCaps` | `0x00000000` (0) |
| `+0x01c` | `DevCaps` | `0x007bbef0` (8109808) |
| `+0x020` | `PrimitiveMiscCaps` | `0x00000cf2` (3314) |
| `+0x024` | `RasterCaps` | `0x00377101` (3633409) |
| `+0x028` | `ZCmpCaps` | `0x000000ff` (255) |
| `+0x02c` | `SrcBlendCaps` | `0x00001fff` (8191) |
| `+0x030` | `DestBlendCaps` | `0x00001fff` (8191) |
| `+0x034` | `AlphaCmpCaps` | `0x000000ff` (255) |
| `+0x038` | `ShadeCaps` | `0x00084208` (541192) |
| `+0x03c` | `TextureCaps` | `0x0007ec87` (519303) |
| `+0x040` | `TextureFilterCaps` | `0x1f030700` (520292096) |
| `+0x044` | `CubeTextureFilterCaps` | `0x1f030700` (520292096) |
| `+0x048` | `VolumeTextureFilterCaps` | `0x03030300` (50529024) |
| `+0x04c` | `TextureAddressCaps` | `0x00000017` (23) |
| `+0x050` | `VolumeTextureAddressCaps` | `0x00000017` (23) |
| `+0x054` | `LineCaps` | `0x0000001f` (31) |
| `+0x058` | `MaxTextureWidth` | `0x00001000` (4096) |
| `+0x05c` | `MaxTextureHeight` | `0x00001000` (4096) |
| `+0x060` | `MaxVolumeExtent` | `0x00000200` (512) |
| `+0x064` | `MaxTextureRepeat` | `0x00002000` (8192) |
| `+0x068` | `MaxTextureAspectRatio` | `0x00000000` (0) |
| `+0x06c` | `MaxAnisotropy` | `0x00000004` (4) |
| `+0x070` | `MaxVertexW` | `0x501502f9` = 1e+10 |
| `+0x074` | `GuardBandLeft` | `0xccbebc20` = -1e+08 |
| `+0x078` | `GuardBandTop` | `0xccbebc20` = -1e+08 |
| `+0x07c` | `GuardBandRight` | `0x4cbebc20` = 1e+08 |
| `+0x080` | `GuardBandBottom` | `0x4cbebc20` = 1e+08 |
| `+0x084` | `ExtentsAdjust` | `0x00000000` = 0 |
| `+0x088` | `StencilCaps` | `0x000000ff` (255) |
| `+0x08c` | `FVFCaps` | `0x00080004` (524292) |
| `+0x090` | `TextureOpCaps` | `0x00ffffff` (16777215) |
| `+0x094` | `MaxTextureBlendStages` | `0x00000004` (4) |
| `+0x098` | `MaxSimultaneousTextures` | `0x00000004` (4) |
| `+0x09c` | `VertexProcessingCaps` | `0x000000bb` = TEXGEN, MATERIALSOURCE7, DIRECTIONALLIGHTS, POSITIONALLIGHTS, LOCALVIEWER, `0x80` unnamed |
| `+0x0a0` | `MaxActiveLights` | `0x00000008` (8) |
| `+0x0a4` | `MaxUserClipPlanes` | `0x00000000` (0) |
| `+0x0a8` | `MaxVertexBlendMatrices` | `0x00000004` (4) |
| `+0x0ac` | `MaxVertexBlendMatrixIndex` | `0x00000000` (0) |
| `+0x0b0` | `MaxPointSize` | `0x42800000` = 64 |
| `+0x0b4` | `MaxPrimitiveCount` | `0x0000ffff` (65535) |
| `+0x0b8` | `MaxVertexIndex` | `0x0000ffff` (65535) |
| `+0x0bc` | `MaxStreams` | `0x00000010` (16) |
| `+0x0c0` | `MaxStreamStride` | `0x000000ff` (255) |
| `+0x0c4` | `VertexShaderVersion` | `0xffff0101` (4294902017) |
| `+0x0c8` | `MaxVertexShaderConst` | `0x00000060` (96) |
| `+0x0cc` | `PixelShaderVersion` | `0xffff0101` (4294902017) |
| `+0x0d0` | `MaxPixelShaderValue` | `0x3f800000` = 1 |

**What the numbers say.** One HAL adapter, and the PC feature masks are nearly empty — `Caps` names only
scan-line reads (`0x00020000`) and `Caps2`/`Caps3` are zero, so the renderer is not deciding from a PC feature
mask at all. The fixed-function budget is the Xbox's: **4** blend stages and **4** simultaneous textures, all 24
texture ops (`0xffffff`), **8** active lights, 4 vertex-blend matrices and no user clip planes. Textures go to
4096x4096, volumes to 512, anisotropy 4x, points to 64 px, with guard bands at +/-1e8 and `MaxVertexW` 1e10 —
i.e. a guard band large enough that the driver never has to clip a triangle against it. `MaxVertexIndex` is
**65535**, the 16-bit index buffer the Xbox draw path uses, over 16 streams of up to 255-byte stride. The two
shader words are `0xffff0101` (a 1.1-style version with this build's `0xffff` prefix rather than the PC header's
`0xfffe`), and the vertex-shader constant file is **96** floats: that is the budget `VS_Lights_MAX` and the
lighting path (`docs/systems/RENDERER.md`, above) have to fit in.

`VertexProcessingCaps = 0xbb` is the field the create path tests, and MATERIALSOURCE7 (`0x2`) is set — so the
`test BYTE PTR [esi+0x4238],0x2` at `0x002b3008` **passes** and the fall-through that logs against
`D3DVTXPCAPS_MATERIALSOURCE7` is never reached in this build's first device creation. The `hardware` decision and
the `BehaviorFlags = 0x40` request agree because the device really reports the capability; the "mixed"/"software"
answers that string offers are dead code on this hardware.

**Where the struct comes from.** It is filled by a caps query, not by a copy from a table:
`GetDeviceCaps(Adapter=0, DeviceType=1 (HAL), pCaps=this+0x419c)` at **`0x002b1b8a`**, into the statically linked
D3D8 at `0x004523a0` in the `D3D` section. The `lea ecx,[ebx+0x419c]` at `0x002b1b76` is the **only** place in
an executable section that names that offset, and the third (last-pushed) argument of a three-argument call is
exactly the `pCaps` slot — `tools/xemu/d3d-caps.py --fill-site` walks the image and prints the three pushes, so
the claim is checkable rather than asserted. The same body stores adapter `0` into `this+0x46d8` two instructions
before the call, and `[esi+0x46d8]` is the adapter the later `CreateDevice` passes — one adapter field, queried
and then used.

The module then **logs six of the caps by name** at file:`0x2fa`, reading `this+0x4230`, `+0x4234`, `+0x423c`,
`+0x4250`, `+0x4254` and `+0x4258`: `D3D Driver: MaxTextureBlendStages=%i`, `D3D Driver:
MaxSimultaneousTextures=%i`, `D3D Driver: MaxActiveLights=%u`, `D3D Driver: MaxPrimitiveCount=%u`, `D3D Driver:
MaxVertexIndex=%u`, `D3D Driver: MaxStreams=%u`. Mapped through the struct base those are `caps+0x94`, `+0x98`,
`+0xa0`, `+0xb4`, `+0xb8` and `+0xbc` — the SDK's own slots — so the field map in the table rests on the game's
names as well as on the SDK's order. Six queries about the fixed-function budget and the index limit, and not one
about a shader: the module checks the caps it is going to *depend* on and nothing else.

**Still unnamed.** The `MultiSampleType` value `0x11` in the present-params block stays a literal for the same
reason as `D3DFORMAT` 30/42: the header that would name it is not in this repository.

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
