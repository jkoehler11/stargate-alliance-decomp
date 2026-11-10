# The console layer: `XboxDrv` is a window and a viewport, not a renderer

4.8's native half has two questions, and the first one is answered on `docs/systems/RENDERER.md`: `D3DDrv` is the
engine's Direct3D 8 render device. The other is where the console-specific code lives — and the module whose name
suggests it, `XboxDrv`, is not a renderer at all. Read with the same instrument, one pass:

    python3 tools/body-read.py --module XboxDrv --root $WORK/module-export -o inventory/xboxdrv_bodies.csv
    python3 tools/body-read.py --write-doc docs/systems/XBOX_DRIVER.md

## Too small to be a driver, and named like a window

`XboxDrv` is **22 bodies, 4,349 bytes**. `D3DDrv` is 129 bodies and 37,786 bytes for the same job description, and
the statically linked `D3D` section that actually holds the Direct3D functions is 302 functions (`docs/IMPORTS.md`).
Twenty-two bodies is a class or two, not a driver.

The five bodies that carry an assert name **two** translation units, both under
`…\GameXBOX\XboxDrv\Src\`: `XboxViewport.cpp` (3 bodies) and `XboxClient.cpp` (2). The class labels they produce are
`XboxClient` and `XboxViewport` — the engine's client/window object and its viewport — and the strings around them
are the window layer's, not a device's:

* `0x0032c180` logs `Client initialized` — a `UClient` coming up.
* `0x0032d6c0` names `RenDev` and asserts `Couldn't create render device!`. The viewport is the object that
  **creates the render device** the config names, which is exactly the wiring a platform driver provides.

`System/Default.ini` names the pair outright, with the Windows driver commented out:

    ViewportManager=XboxDrv.XboxClient
    ;ViewportManager=WinDrv.WindowsClient
    RenderDevice=D3DDrv.D3DRenderDevice

So `XboxClient` is this build's viewport manager and `D3DDrv.D3DRenderDevice` is what it instantiates — the two
modules on `docs/systems/RENDERER.md` and this page are the two halves of one driver pair, and the Xbox build's
"driver" is the engine's own D3D device plus a 22-body platform shim. Both classes are registered in the image's
class tables (`XboxClient` at `0x004c9560`, 196 bytes; `XboxViewport` at `0x004c9618`, 464 bytes).
* `0x0032d860` asserts `ButtonIndex < 8`, so the viewport is also where the **gamepad's eight buttons** are read:
  input arrives through the platform viewport, there is no separate input driver module.
* `0x0032dff0` is the XDK's own media-path helper — `XBUtil_FindMediaFile(): Could not find file [%s]` — compiled
  into this module, so `XboxDrv` carries XDK utility code as well as the game's window classes.

## Two small tables, and neither is the device

Four of the 22 bodies reach an object through `call [obj + off]`, at three distinct offsets, all `<= 0x5c`. That is a
narrow interface — a window or a viewport's method table — and it is **not** the render device's: the device is the
20-offset table at `<= 0xbc` that `D3DDrv` calls (`docs/systems/RENDERER.md`). The two modules therefore sit on
opposite sides of the same boundary, which is the useful part of the measurement: the console layer hands `D3DDrv` a
device to render into, and does not call it.

The negatives match. No `XboxDrv` body names an asset path, none calls `D3DDrv`, `XboxAudio` or `XboxLive`, and its
cross-module callees are the ordinary ones — `Engine` in 8 bodies, `Core` in 6. Nothing in it is a graphics
primitive, a push buffer or a shader.

## What this settles

PLAN 4.8's "renderer for Xbox" is now three layers, each measured and none of them where the module names suggest:

| layer | module | size | evidence |
|---|---|---|---|
| render device (D3D8) | `D3DDrv` | 129 bodies, 37,786 B | five engine translation units, device creation and format probing in its own strings |
| window + viewport | `XboxDrv` | 22 bodies, 4,349 B | `XboxClient`/`XboxViewport`, creates the `RenDev`, reads the pad |
| the D3D8 library itself | (unnamed) | 302 functions | no import table, no assert names (`docs/IMPORTS.md`, U9) |

So the fork's Xbox work is where the fork packages are: the engine's renderer compiled in and a platform layer that
creates the device and pumps the pad. Reconstructing "how the console renders" means reading `D3DDrv`'s bodies, and
naming the 302 D3D functions is 4.2's signature work — not a missing module.

## The measurement

<!-- generated:renderer-body-read -->
`XboxDrv`: 22 bodies, 4349 bytes, 24 distinct literals (19 of them not a build path).

| translation unit | bodies | bytes | slot calls | cross-module callees |
|---|---:|---:|---:|---:|
| `(none)` | 17 | 3574 | 3 | 23 |
| `XboxViewport.cpp` | 3 | 483 | 0 | 1 |
| `XboxClient.cpp` | 2 | 292 | 1 | 3 |
| **total** | **22** | **4349** | **4** | **27** |

| callee module | bodies calling it |
|---|---:|
| `Engine` | 8 |
| `Core` | 6 |

Slot calls: **4** of 22 bodies reach an interface object through `call [obj + off]`, **3** distinct offsets, all <= `0x5c` (`0x10`, `0x48`, `0x5c`).

| address | name | translation unit | size | literals | slots | callee modules |
|---|---|---|---:|---:|---:|---|
| `0x0032c6e0` | `XboxDrv_XboxClient` | `XboxClient.cpp` | 112 | 2 | 1 | `Core` |
| `0x0032cbf0` | `XboxDrv_XboxClient` | `XboxClient.cpp` | 180 | 4 | 0 | `Core` |
| `0x0032d6c0` | `XboxDrv_XboxViewport` | `XboxViewport.cpp` | 207 | 4 | 0 | - |
| `0x0032d860` | `XboxDrv_XboxViewport` | `XboxViewport.cpp` | 96 | 2 | 0 | - |
| `0x0032d960` | `XboxDrv_XboxViewport` | `XboxViewport.cpp` | 180 | 3 | 0 | `Core` |
<!-- /generated:renderer-body-read -->
