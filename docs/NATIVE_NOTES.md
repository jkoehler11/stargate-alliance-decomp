# Native notes: `stargategame` and `stargatevehicles`

The two game-specific modules are 10 source files, 43 functions — the smallest of the twelve modules
and the only ones that are the game's own code rather than engine code. This note records what they
contain, which is not what the plan expected.

## Method

```
tools/ghidra/export-c.sh DUMP GHIDRA_HOME stargategame_,stargatevehicles_
  # imports default.xbe, carves sections, applies symbols/xbox_functions.csv (13,737 names),
  # decompiles the selected functions to $(WORK)/native/*.c and writes an index CSV
tools/ucpp-registrations.py $(WORK)/module-export/stargategame --xbe DUMP/default.xbe \
    --merge -o symbols/xbe_overrides.csv
tools/ucpp-registrations.py $(WORK)/module-export/stargatevehicles --xbe DUMP/default.xbe \
    --merge -o symbols/xbe_overrides.csv
make ghidra        # applies symbols/xbe_overrides.csv after the assert pass
```

`--xbe` reads a class literal from the call site's own pushed immediates when the decompiled text
renders a `&DAT_` instead of the string, and `--merge` is required because the override file holds rows
this tool does not produce (38 of 78 today): a plain write would drop them.

The C is game-derived: it stays under the git-ignored `work/` tree. What is committed is the name map
and the measurements below.

## Finding 1: most of these functions are generated class registration

40 of the 43 functions have the same shape:

```c
iVar2 = FUN_0003af60(0, 0xecc, 4, 0, 0, 0, 0, L"AlkeshBomber", ..., L"System", 0x4084004, ...);
if (iVar2 == 0)
  FUN_00058a30("ReturnClass", "...\\stargatevehicles\\src\\AlkeshBomber.cpp", 3);
```

They are the per-class registration stubs of Perception's `ucpp` script-to-C++ compiler: the class
name, the outer package (`System`), the object size and the class flags are the build's own literals,
and `ReturnClass` is the generated assert. That is evidence, not inference, so each one is named
`ucpp_Register_<Class>` in `symbols/xbe_overrides.csv` (40 rows, with object size, assert line and
source file). The fortieth is `0x003107b0` (`GoauldDrone`, object size 2,532): its literal is there --
`L"GoauldDrone"` at `0x53b42a` -- but the decompiler printed the argument as `&DAT_0053b42a`, so it is
read from the call site's pushes instead of from the text.

The object sizes are the class's serialised layout size in bytes — 3,788 for `AlkeshBomber`, 4,344 for
`StargatePlayer`, 3,344 for `StargateVehicle`, 72 for `SGScreenBlur`. That number is a free cross-check
for the `UObject` layout work (PLAN 4.3) and for the property counts in `inventory/classes.csv`.

| class | object size | source file | address |
|---|---|---|---|
| `SGScreenBlur` | 72 | `SGCameraEffects.cpp` | `0x00317380` |
| `SGScreenMotionBlur` | 72 | `SGCameraEffects.cpp` | `0x00317430` |
| `SGScreenEmissiveWithBloom` | 68 | `SGCameraEffects.cpp` | `0x003174e0` |
| `SGScreenMenuPause` | 84 | `SGCameraEffects.cpp` | `0x00317ba0` |
| `SGScreenSniperOverlay` | 84 | `SGCameraEffects.cpp` | `0x00317d70` |
| `SGScreenDOF` | 112 | `SGCameraEffects.cpp` | `0x00319620` |
| `SGScreenRadialBlur` | 80 | `SGCameraEffects.cpp` | `0x003196d0` |
| `SGScreenEmissive` | 104 | `SGCameraEffects.cpp` | `0x003197f0` |
| `SGScreenBloom` | 144 | `SGCameraEffects.cpp` | `0x0031abe0` |
| `SGFontInfo` | 1,064 | `StargateGame.cpp` | `0x0031e740` |
| `StargateHUD` | 2,252 | `StargateGame.cpp` | `0x0031e800` |
| `BotEnemy` | 2,252 | `StargateGame.cpp` | `0x0031e8c0` |
| `SquadAIBasic` | 1,440 | `StargateGame.cpp` | `0x0031e980` |
| `UnrealScriptedSequence` | 984 | `StargateGame.cpp` | `0x0031ea40` |
| `SGOrder` | 148 | `StargateGame.cpp` | `0x0031eb00` |
| `SGGenericOrderActor` | 908 | `StargateGame.cpp` | `0x0031ebc0` |
| `SGLevel` | 920 | `StargateGame.cpp` | `0x0031ec80` |
| `SGTriggeredCondition` | 920 | `StargateGame.cpp` | `0x0031ed40` |
| `Electrocutor` | 1,096 | `StargateGame.cpp` | `0x0031ee00` |
| `BeamArc` | 1,096 | `StargateGame.cpp` | `0x0031eec0` |
| `BotBase` | 1,772 | `StargateGame.cpp` | `0x0031ef80` |
| `FarClipChange` | 932 | `StargateGame.cpp` | `0x0031f040` |
| `IntelligentSpawner` | 996 | `StargateGame.cpp` | `0x0031f100` |
| `StargateVehicle` | 3,344 | `StargateGame.cpp` | `0x0031f1c0` |
| `CoverInfo` | 1,092 | `StargateGame.cpp` | `0x0031f280` |
| `ShadowManager` | 952 | `StargateGame.cpp` | `0x0031f340` |
| `UnrealPlayer` | 4,012 | `StargateGame.cpp` | `0x00322cd0` |
| `StargatePlayer` | 4,344 | `StargateGame.cpp` | `0x00322d90` |
| `SubActionDepthOfField` | 132 | `USubActionDepthOfField.cpp` | `0x003233b0` |
| `AlkeshBomber` | 3,788 | `AlkeshBomber.cpp` | `0x0030ca70` |
| `AlkeshBomberBot` | 1,868 | `AlkeshBomber.cpp` | `0x0030cb30` |
| `AlkeshLandingZone` | 924 | `AlkeshBomber.cpp` | `0x0030cbf0` |
| `DeathGlider` | 3,660 | `DeathGlider.cpp` | `0x0030e190` |
| `DeathGliderBot` | 1,792 | `DeathGlider.cpp` | `0x0030e250` |
| `StargateCar` | 3,812 | `StargateCar.cpp` | `0x00312910` |
| `StargateCopter` | 3,600 | `StargateCopter.cpp` | `0x00313d60` |
| `StargateHover` | 3,660 | `StargateHover.cpp` | `0x00314eb0` |
| `Teltak` | 3,664 | `Teltak.cpp` | `0x00315ec0` |
| `TeltakBot` | 1,812 | `Teltak.cpp` | `0x00315f80` |
| `GoauldDrone` | 2,532 | `GoauldDrone.cpp` | `0x003107b0` |

## Finding 2: the module's own logic is small

The other three functions are not registration stubs, and they are the only game code in the module
(a third `SGCameraEffects` body, `0x00318140`, sits outside the 43 and belongs to the same counter):

| address | name | what the decompiled body does |
|---|---|---|
| `0x00311520` | `stargatevehicles_StargateCar` | 541 bytes: projects a velocity onto an axis, clamps it, derives a speed, then asserts `Mesh` and `smesh` before reading mesh state. The vehicle-physics body. |
| `0x00316b10` | `stargategame_SGCameraEffects` | 88 bytes: depth-of-field resource reference counting, asserts `DOFResourceRefCount > 0`. |
| `0x00318060` | `stargategame_SGCameraEffects` | 217 bytes: camera effect setup; references the `Engine` package by name. |

So the game modules are generated glue plus a handful of bodies. The gameplay itself is in script
(Phase 3) and in engine modules — consistent with the earlier finding that Perception moved hot script
classes into C++ and left the rest as bytecode.

## Finding 3: three helpers carry the readability

Every one of the 43 functions calls `FUN_00058a30`, and 40 call `FUN_000340c0`/`FUN_0003af60`:

| helper | calls | evidence |
|---|---|---|
| `appAssertFailed` (`0x00058a30`) | 43 | called as `(expression, file, line)` from every function. Named from the wide literal it logs, `"Assertion failed: %s [File:%s] [Line: %i]"`, and measured to log through the engine's output device and return — no break, no abort (`docs/UNKNOWNS.md` U6). |
| `FUN_0003af60` | 40 | the class constructor used by the registration stubs: it installs the `Class` class table (`0x004d4870`, object size 1312), sets flags `\|0x12` and stores 13 arguments. Still `FUN_*`: naming it needs the caller-side evidence of PLAN 4.7. |
| `appMallocBeforeInit` (`0x000340c0`) | 40 | logs `"Called %s before memory init"` with `"appMalloc"` and returns 0; reached through a pointer from 961 sites, i.e. the allocation entry point before `GMalloc` exists. |

## Finding 4: the two packages' natives have no dispatch index

`inventory/natives.csv` lists 21 `native()` declarations across `StargateGame` and `StargateVehicles`
(`BotEnemy`'s cover search, `Electrocutor` arcing, `SGOrder` drawing, HUD order drawing,
`DeathGlider`/`StargateCopter` Karma updates). All 21 serialise `native_token` 0 — the same signature as
the 523 index-less declarations in U1 — so none of them is bound through a dispatch index. Their bodies are in
these modules or the engine, bound outside the dispatch table, which is the thread PLAN 4.7 picks up.

## The library code, by C++ class (2026-11-19)

The masked-body and call-edge passes named 2,855 bodies in the seven library sections, and nearly all of
them are MSVC-mangled members, so the name carries the class. `tools/native-code-classes.py`
(`make native-code-classes`, `inventory/native_code_classes.csv`) collects them: **205 groups, 3,971
methods**, 196 groups with library evidence and 9 that carry only an assert-pass name.

| namespace | class | methods | bytes | library |
|---|---|---:|---:|---|
| - | (C functions) | 1,291 | 275,088 | thirteen |
| - | `CXo` | 377 | 91,019 | xonline |
| - | `CXnIp` | 144 | 41,060 | xnet/xonline |
| - | `D3DX` | 143 | 40,941 | d3dx8 |
| - | `XGRAPHICS` | 98 | 23,932 | xgraphics |
| - | `CXnSock` | 90 | 18,881 | xnet/xonline |
| - | `D3D` | 82 | 18,216 | d3d8 |
| D3D | `CMiniport` | 52 | 11,855 | d3d8 |
| XGRAPHICS | `PreProcessor` | 49 | 9,847 | xgraphics |
| XGRAPHICS::VS_GRAPH_OPTIMIZER | `Instruction` | 49 | 9,661 | xgraphics |
| UIXNamespace | `CLiveEngine` | 42 | 3,315 | uix |
| - | `CXnNic` | 17 | 3,049 | xnet |
| - | `CXnEnet` | 16 | 2,474 | xnet/xonline |
| - | `CXnBase` | 15 | 1,717 | xnet |
| UIXNamespace | `CSkinFile` | 14 | 1,903 | uix |
| UIXNamespace | `CScreen` | 8 | 432 | uix |

Six readings the census supports:

1. **The networking stack is layered the way the SDK names it.** `CXnBase` (15 methods) is the base
   object; `CXnIp` (144), `CXnSock` (90), `CXnNic` (17) and `CXnEnet` (16) are the IP, socket, NIC and
   Ethernet layers above it — 282 methods of Kerberos-era Xbox Live plumbing, all from `xnet`/`xonline`.
2. **`CXo` is XOnline's object layer**, 377 methods and 91 KB, the single largest C++ class here. Its
   method names (`Cxo...`, property and enumeration helpers) are the COM-shaped surface the Live
   services are exposed through.
3. **The UIX front-end is small and self-contained**: `CLiveEngine` (42 methods, 3.3 KB), `CSkinFile`
   (14) and `CScreen` (8) in `uix` — a dashboard-style renderer, not a game UI framework.
4. **The graphics side carries its toolchain.** `PreProcessor` (49) and `VS_GRAPH_OPTIMIZER::Instruction`
   (49) in `xgraphics` are the shader assembler's lexer and instruction table; `D3DX` (143) is the
   helper library, `CMiniport` (52) the D3D miniport interface.
5. **The C functions are the biggest group and the least informative**: 1,291 functions, 275 KB, spread
   across thirteen libraries — CRT, XAPI entry points and each DLL's flat C API. They are named, and
   they are not one system.
6. **What the census does not say.** A class census is an index of *names*, not behaviour: `CXnIp` has
   144 methods and this table tells nobody what any of them do. PLAN 4.9's per-subsystem reading is
   still the work; this is what tells a reader which subsystem to read first.

The nine name-only groups (`?$set`, `?$basic_string`, `CIrql`, `PixelShaderVerifier`,
`VCWaveFileMediaObject`, and four more) come from the assert pass and carry no library attribution —
recorded as `evidence=name only` rather than guessed into a library.

## `CXnIp`, read from its own call edges (2026-11-19)

PLAN 4.9's first subsystem, and the reading is deliberately mechanical: every library member carries a
relocation list, a `REL32` is a call whose target the library names, and `tools/native-method-notes.py`
(`make native-method-notes CLASS=CXnIp`, `inventory/cxn_ip_methods.csv`) resolves those per method.
144 methods, 118 with at least one callee. **Two carry a string literal** — the release libraries are
release builds — so the evidence here is the call graph, not prose the compiler kept.

What the call edges say, in four flows:

| method | bytes | calls |
|---|---:|---|
| `IpInit` | 502 | `CXnEnet::EnetInit`, `CXnHal::SetInitFlag`, `CXnIp::IpConfigInit`, `_XcHMAC`, `_XcDESKeyParity`, `CXnBase::Rand` |
| `IpStop` | 80 | `SecRegShutdown`, `IpConfigResetPppoe`, `EnetPush`, `NicFlush`, `IpConfigReset`, `CXnHal::SetInitFlag`, `CXnEnet::EnetStop` |
| `IpXmitIp` | 564 | `RouteLookup`, `RouteRelease`, `_tcpipxsum`, `CPacket::GetEnetHdr`, `CXnEnet::EnetXmit` |
| `IpDecrypt` | 282 | `SecRegLookup`, `CPacket::GetIpHdrNext`, `CPacket::GetXspInfo`, `XnHmacVerify`, `IpCalcIvAtSeq`, `CryptDes` |
| `IpLogonGetStatus` | 731 | `XnSha`, `_XcVerifyPKCS1Signature`, `XnChalExec`, `CXnBase::TimerSet`, `IpXmitSecMsg` |
| `IpRegisterKey` | 252 | `_XcModExp`, `CXnBase::Rand`, `KeyRegLookup`, `KeyRegShaDesKeys` |

1. **Lifecycle is ordered and explicit.** `IpInit` brings up the Ethernet layer, sets the HAL init flag,
   initialises config and derives crypto state (`_XcHMAC`, `_XcDESKeyParity`, `CXnBase::Rand`) — the
   ordering is the evidence that configuration precedes any traffic. `IpStop` unwinds in the reverse
   spirit: security registry down, PPPoE and IP config reset, then the NIC flushed and the Ethernet
   layer stopped. 80 bytes and seven calls: teardown is a sequence, not a cleanup loop.
2. **Transmit has four distinct paths, all resolving a route first.** `IpXmitIp` (plain IP),
   `IpXmitXsp` (748 B, security payload), `IpXmitSecMsg` and its four wrappers
   (`Delete`, `ChangeUsers`, `NatDetect`, `XbToSgPulse`), and the three key-exchange variants
   `IpXmitKeyExXbToTs` / `XbToSg` / `XbToXb` — to the title server, the security gateway and a peer.
   The key-exchange paths are the only ones that call `CXoBase::XoKerbBuildApReq` and `CXoBase::XoKerbGetInfo`:
   Kerberos ticket exchange lives here, above IP, not in the socket layer.
3. **Receive verifies before it decrypts.** `IpDecrypt` calls `XnHmacVerify` and `IpCalcIvAtSeq`
   (IV derived from the sequence number) after fetching the XSP info — integrity first, then
   confidentiality, with the security registry consulted by `SecRegLookup`. `IpRecvTcp` computes
   `_tcpipxsum`, so the checksum sits in the receive path rather than in the caller.
4. **Logon is a state machine with an event queue.** `IpLogonGetQFlags`, `GetQVals`, `SetQVals`,
   `SetQEvent`, `ClearQEvent`, `SetBwLimit`, `NoteSystemTime`, `SetPState`, `GetStatus`, `IpLogoff` —
   ten methods over a queue of pending values, which is what a network layer does when the title's
   UI thread must not block on the network. `IpLogonGetStatus` (731 B) verifies a PKCS#1 signature
   over a challenge (`XnSha`, `XnChalExec`, `_XcVerifyPKCS1Signature`) and answers with
   `IpXmitSecMsg`, and `IpRegisterKey` does RSA modexp (`_XcModExp`) — the Xbox Live key registration
   handshake, in the IP layer.

Supporting cast the same artifact names: a route table with refcount discipline
(`RouteAdd` 250 B, `RouteLookup`, `RouteRelease`, `RouteRedirect`, `RouteEntryOrphan`, `RouteListOrphan`),
a packet-fragment pool (`FragFree`, `FragTerm` over `CPacketQueue::Dequeue` and `CXnBase::PoolFree`),
and a timer wheel (`IpSetEventTimer` over `CXnBase::TimerSet`).

**What this is not.** These are call edges and byte counts. `IpXmitXsp` calling `IpCalcIvAtSeq` proves
the IV comes from the sequence number; it does not prove the cipher mode, the key schedule, or what
`IpCheckChangeUsersComplete` waits for. The 26 methods with no callee are leaves — arithmetic, field
accessors and the entry points the image calls directly — and the strings that would explain them are
not in a release build. The next reading is `CXo` (377 methods), where the Kerberos bodies these calls
reach actually live.

## `CXo` and `CXoBase`: the Live API surface and its Kerberos core (2026-11-19)

`CXo` is the largest class in the binary — **377 methods, 91,019 bytes**, 320 of them with at least one
named callee (`inventory/cxo_methods.csv`, `make native-method-notes CLASS=CXo`). The prefix census says
what it is: 75 methods begin `XOnline`, 33 `Messaging`, 15 `Cache`, 10 `Configure`, 9 `Stats`, 9 `Xkerb`,
6 each `Match` and `Presence`. It is the whole Xbox Live client API, and 35 of its methods — 13,108 bytes,
14.4% of the class — are the Kerberos ticket machine.

**The Kerberos exchange is a triad of build/verify pairs, and it is linked in, not an OS service.**

| request | bytes | verify | bytes |
|---|---:|---|---:|
| `XkerbBuildAsRequest` | 826 | `XkerbVerifyAsReply` | 902 |
| `XkerbBuildTgsRequest` | 880 | `XkerbVerifyTgsReply` | 1148 |
| `XkerbBuildApRequest` | 273 | `XkerbVerifyApReply` | 364 |

They call a Kerberos implementation that the naming passes have already named inside the image —
`?KerbUnpackData@@YGJPAVCScratchMemoryAllocator@@PAEKKPAPAX@Z` sits at `0x004485b8` (104 bytes), and with it
`KerbConvertGeneralizedTimeToLargeInt`, `KerbUpdateCurrentTime`, `KerbGetCurrentTime`,
`KerbComputeCompoundIdentityKey`, `KerbAddCompoundIdentityPreAuth`, `KerbCreateApRequest` and
`KerbDecryptDataEx`. The AS request signs with a compound-identity key and adds pre-authentication: this is
the Xbox Live variant, not plain RFC 1510.

**Ticket acquisition reuses one async task model for three caches.** `GetSingleTicket` (640 B) drives the AS
exchange, `GetServiceTicket` (640 B, 2,003 B in its `Continue`) drives the TGS one — with `DNSLookupContinue`
/ `DNSLookupGetResults` ahead of it — and `GetCombinedTicket` (1,678 B in `Continue`) chains both. Each ends in
a cache with an identical 61-byte closer (`CloseSingle`/`CloseService`/`CloseCombinedTicketCache` →
`XOnlineTaskClose` + `CacheClose` → `CLocalCache::Delete`), plus `CloseLogonTimeCache` for ticket lifetimes.
`CacheOpen` (253 B) initialises `CLocalCache`, and the `CCacheTask` family (`DoIndexFlushBegin`,
`DoRetrieveBegin`, `DoUpdateMRUList…`) does the work as tasks, not as blocking calls: 34 methods of
`XOnlineTaskContinue` / `XOnlineTaskClose` / `SendRecvBegin` / `GetSendRecvResults` machinery.

**Two transports, and the split is the finding.** `CXo`'s own ticket requests send with raw Winsock —
`GetSingleTicketSendAsRequest` and `GetServiceTicketSendTgsRequest` both call `_WSASendTo`, and
`SendRecvBegin` (125 B) calls `_WSASend`/`_WSARecv`; 12 methods touch Winsock directly. That is a different
*entry point* from `CXnIp`'s own key exchange, where `IpXmitKeyExXbToTs`/`XbToSg`/`XbToXb` reach
`CXoBase::XoKerbBuildApReq` and transmit through `CXnEnet::EnetXmit` — but not a different stack:
`_WSASendTo@36` is a 15-byte import thunk at `0x00419355` into `?WSASendTo@CXnSock@@` (275 B), so the ticket
route travels `CXnSock` → `CXnIp::IpFillAndXmit` → `CXnEnet` while the key-exchange route enters at the
Ethernet layer. See the stack reading below.

**`CXoBase` is a 14-method veneer over `CXnIp`.** Fourteen methods, every one 11–15 bytes:
`XnLogonGetQFlags`, `XnLogonGetQVals`, `XnLogonSetQVals`, `XnLogonSetQEvent`, `XnLogonClearQEvent`,
`XnLogonNoteSystemTime`, `XnLogonSetPState` are one-call forwarders to the matching `CXnIp::IpLogon*`
(eleven bytes each, `XnLogonSetPState` fifteen — a `jmp` and its padding), `XnLogonGetStatus` (54 B) adds
`_XNetUpdateConfigParams`, and `XnConfigAtLogon` (55 B) is `_XNetLoadConfigParams` +
`_XNetConfig`. So the public `Xn*` API does not contain the logon state machine — `CXnIp` does, exactly
as its own reading showed, and `CXoBase` is the ABI-stable face of it.

**What this is not.** The `Kerb*` names prove a Kerberos implementation is linked and give its call shape;
they say nothing about the wire encoding of a ticket, the crypto inside `KerbDecryptDataEx`, or which
pre-authentication types the server accepts. And the two-transport split is *observed*, not explained:
nothing in the call graph says why ticket exchange bypasses the `CXnIp` path, and a later reader should
not assume either route is dead.

## The stack below `CXnIp`, end to end (2026-11-19)

Four readings close the layer `CXnIp` sits on: `CXnBase` (15 methods, 1,717 B), `CXnNic` (17, 3,049 B),
`CXnEnet` (16, 2,474 B) and `CXnSock` (90, 18,881 B) — `inventory/cxn_{base,nic,enet,sock}_methods.csv`.
Together with `CXnIp`'s 144 methods, the whole network stack is now accounted for by call edges.

**It is interrupt-driven from the bottom.** `CXnNic::NicRecvInterrupt` (345 B) calls `CXnEnet::EnetRecv`;
transmit does not: `NicXmit` (301 B) only fills a queue (`CPacketQueue::InsertTail`), and the queue is
drained from the interrupt side (`NicXmitInterrupt` 205 B → `RemoveHead` → `CXnEnet::EnetPush` → `NicXmit`
again). `NicInit` (992 B) — the largest method in these four classes — calls `CXnBase::BaseInit`,
`CXnHal::SetInitFlag` and `CXnNic::HalShutdownNotification`, so the NIC registers its own shutdown
notification at init.

**`CXnEnet` is a demultiplexer.** `EnetRecv` (206 B) dispatches to `CXnIp::IpRecv`, `CXnIp::IpRecvPppoe` and
its own `ArpRecv` (462 B); `ArpRecv` resolves through `ArpLookup` (232 B, `TimerSetRelative` + `ArpXmit`) and
hands ARP payloads to `CXnIp::IpRecvArp`. `EnetCapture` (234 B) exists beside them — a capture hook in the
receive path.

**`CXnSock` is a complete TCP/UDP stack, not a shim.** 90 methods, 18.9 KB: `TcpConnect` (585 B →
`SockBind`, `TcpEmitSYN`, `TcpTimer`, `SockClose`), `TcpRecv` (756 B), `TcpRecvData` (741 B),
`TcpUpdateSndUna` (637 B → `TcpUpdateRTO`, `SockReleaseSendBuf`), `UdpSend` (633 B → `CXnIp::IpFillAndXmit`,
`SockReadWsaBufs`, `CPacket::GetCbBpp`), and `select` (903 B → `SockLockSelectSockets`,
`SockCheckSelectEvents`). RTO computation, send-buffer release and a select lock: this is a socket layer with
its own timer and buffer discipline.

**The Winsock ABI is `CXnSock` itself.** `_WSASend@28` (`0x00419337`), `_WSASendTo@36` (`0x00419355`) and
`_WSARecv@28` (`0x0041930e`) are **15-byte import thunks** — a `jmp` through the import table — and what they
reach is `?WSASend@CXnSock@@` (155 B), `?WSASendTo@CXnSock@@` (275 B) and `?WSARecv@CXnSock@@` (157 B), all
in `xonline/obj\i386\sock.obj`, section `XNET`. So the socket API the title calls and the private stack are
the same code, and `_select@20` (`0x004192da`) is the same pattern.

**Which corrects the previous reading.** `CXo`'s ticket requests call `_WSASendTo` and `CXnIp`'s key exchange
calls `CXnEnet::EnetXmit` directly — two *entry* points, one stack: the socket route travels
`CXnSock` → `CXnIp::IpFillAndXmit` → `CXnEnet`, while `IpXmitKeyEx*` enters at the Ethernet layer and never
touches a socket. The earlier note said the ticket path was "not the path `CXnIp` uses"; measured now, the
difference is the entry point and whether the socket layer is traversed, not two implementations.

**Supporting cast.** `CXnBase` carries the pool (`PoolAlloc` 259 B, `PoolFree` 182 B, `PacketAlloc` 239 B),
the timer wheel (`TimerSet` 101 B, `TimerPush` 94 B → `NicTimer`, `SecRegProbe`, `IpConfigProbe`) and the
RNG: `RandInit` (115 B) seeds from `CXnHal::HalRandGather` and runs `XnSha` + `_XcRC4Key`/`_XcRC4Crypt`, so
the random stream is RC4 keyed by hashed hardware entropy.

**What this is not.** Ordering and demultiplexing are established; the wire behaviour is not. `TcpUpdateRTO`
proves an RTO computation exists, not which algorithm; `EnetCapture` proves a hook, not what consumes it; and
none of these bodies has been decompiled, so window handling, retransmission policy and the PPPoE branch
remain unread.

## The shader toolchain: `XGRAPHICS`, `PreProcessor`, `Instruction`, `D3DX` (2026-11-19)

Four readings, 339 methods: `XGRAPHICS` (98, 23,932 B), `D3DX` (143, 40,941 B), and the two classes the
shader assembler is built from, `PreProcessor` (49, 9,847 B) and `VS_GRAPH_OPTIMIZER::Instruction`
(49, 9,661 B) — `inventory/xgraphics_methods.csv`, `d3_dx_methods.csv`, `pre_processor_methods.csv`,
`instruction_methods.csv`. This is the one reading in which **the strings do most of the work**: 24 of the
49 preprocessor methods carry a literal, and they are a grammar.

**`PreProcessor` is a C preprocessor written for shader assembly.** Its `DoDirective` compares the word
after `#` against `define`, `undef`, `include`, `ifdef`, `ifndef`, `else`, `elif`, `endif`, `pragma`, `error`
and reports `Unknown preprocessor directive` otherwise — plus the non-standard `macro` … `endm` form
(`CheckForMacroDeclaration` requires `endm` "before end of file"). Two limits are stated by the code itself:
**`#if` is not supported** and **`#elif` is not supported**, and `#include` needs a resolver object
("Can't process #include because no resolver object was supplied"). Nesting is bounded — "Too many nested
levels of #ifdef/#ifndef in this file", "Too many levels of include directives" — and so is expansion
("Expanded macro too long"). Comments and line continuation are handled deliberately, including the
warning that a single-line comment ending in `\` continues onto the next line.

**`d3dxasm_parse` is yacc output.** 1,160 bytes calling `CD3DXAssembler::Token`, `CD3DXAssembler::Production`
and `CD3DXAssembler::YYError` — a generated parser driving a lexer object, so the grammar is a table rather
than readable code.

**`XGRAPHICS` turns tokens into microcode and optimises them.** `D3DTokensToUCode` (2,874 B, the largest
method in these four classes), `CompilePixelShaderToUCode` (1,693 B → `_XGBufferCreate`,
`_XGBuffer_GetBufferPointer`, `_XGBuffer_GetBufferSize`), `PrintPixelShaderDef` (2,321 and 2,243 B, two
overloads) and `InputOutputDependency` (868 B) sit above `PairableMulAdd` (804 B → `NullSwizzle`, `UsesVA`,
`UsesCA`) — the register-class tests a vertex-shader optimiser needs.

**`Instruction` is a graph optimiser, not an encoder.** `Combine` (836 B → `Combinable`, `ReadsInput`,
`Reader::SetSource`), `UnPair` (728 B), `NormalizeMove` (643 B → `IsSimpleMove`), `SwapResultComps` (470 B →
`MACReadsC`), `RedundantResults` (427 B) and `GetAllDependants` (396 B → `?$set::insert`, `find_node`) — the
dependency set is an STL `set`, and `GetUCode` (883 B → `GetA0x`, `GetSwizzle`, `CleanCompNones`) is the only
method that emits. So the pipeline is: preprocess → parse → tokenise to microcode → combine/unpair/normalise
the graph → encode.

**`D3DX` is the helper library around it** (143 methods, 67 with callees, 1 string): texture, mesh, math and
shader-assembly helpers, the largest single group in the graphics sections after the C functions.

**The pipeline is live, and the shaders are in the image.** A scan of the dump finds these tokens in
`default.xbe` and in no package, and the bytes around them are source, not a keyword table. The count is
**60 programs — 19 `vs.1.1`, 3 `xvs.1.1`, 9 `ps.1.1`, 12 `xps.1.1`, 17 `ps.1.4`** (`inventory/shader_programs.csv`,
`make shader-programs`). Two earlier figures in this page were wrong in opposite directions: 63 was a substring
count that included `#define` lines *containing* the token, and 45 was the same scan with an anchor that required
the version token at the start of the string — which silently dropped the fifteen `x`-prefixed variants. A runtime
stop at `_XGAssembleShader` was handed `0x00524d18`, whose bytes are `xps.1.1`, and that is what exposed it.

    vs.1.1
    m4x4 oPos, v0, c0
    mov oT0, v9
    dp4 oT0.x, v9.xyww, c24
    ...
    ps.1.4
    texld r0, t0
    texcrd r2.rgb, t0

with `#pragma screenspace` (3), one `#include` and two `#define` — the preprocessor above, used on shipped
data — and the assembler's own banner, `// Xbox Shader Assembler %s`.

So the title compiles its shaders at runtime from embedded assembly through this toolchain, and that is why
`PreProcessor` is in the image at all. For Phase 6 it is a requirement rather than an open question: a
rebuild either reproduces the compile path (preprocessor, yacc parser, `D3DTokensToUCode`, the `Instruction`
optimiser) or bakes the 60 programs' microcode once and ships it. **And all 60 are used.** Each program is referenced by an immediate whose value is its address: 56 by a
`push` inside a tiny per-program wrapper, 4 by `mov` immediates inside one function at `0x002b4600`. Every push
wrapper calls one of two dispatchers, and the split follows the program's type — and its `x` variant: 17 through
`ShaderCompileVertex` (15 `vs.1.1`, 2 `xvs.1.1`) and 38 through `ShaderCompilePixel` (9 `ps.1.1`, 12 `xps.1.1`,
17 `ps.1.4`), so the prefix does not change which dispatcher a program reaches:

| dispatcher | programs | calls | now named |
|---|---:|---|---|
| `0x002c01e0` | 17 — 15 `vs.1.1`, 2 `xvs.1.1` | `_XGAssembleShader@44`, `_D3DDevice_CreateVertexShader@16` | `ShaderCompileVertex` |
| `0x002c0910` | 38 — 9 `ps.1.1`, 12 `xps.1.1`, 17 `ps.1.4` | `_XGAssembleShader@44`, `_D3DDevice_CreatePixelShader@8`, plus `_CreateFileA`/`_ReadFile`/`_CloseHandle` and `_GlobalMemoryStatus` | `ShaderCompilePixel` |

So the runtime path is: per-program wrapper → `ShaderCompileVertex`/`ShaderCompilePixel` → `_XGAssembleShader`
→ `Create*Shader`. Both dispatchers were `FUN_*` until this reading, and naming them is the one place 4.9 has
produced names rather than prose. The pixel path also touches a file and the memory status, which reads as a
shader cache; that is observed, not explained.

## The UIX front-end: 27 classes, 286 methods (2026-11-19)

The class census put `CLiveEngine`/`CSkinFile`/`CScreen` at 42/14/8 methods; the namespace they live in is
much larger — **27 classes, 286 methods, 40,880 bytes** in `uix` — and it is the Xbox Live dashboard: logon,
players, friends, voice mail and popups, with each area built as a *feature* that owns *screens*.

| class | methods | bytes | what the calls say |
|---|---:|---:|---|
| `CLiveEngine` | 42 | 3,315 | `CreateEngine` → `CSkinFile::Initialize`, `CArray::Initialize`, `UpdateLocalUserList`; `DoWork` → `GetTopScreenWithInput`, `CInput::GetInput`, `CFeatureContext::DoWork` |
| `CLogonFeature` | 28 | 3,777 | the logon feature, largest after the engine |
| `CPlayersScreen` | 22 | 5,302 | largest single screen |
| `CUIObject` | 16 | 2,639 | base object for the widget tree |
| `CLogonPane` | 15 | 3,139 | `Input` (the logon pane takes `_UIX_INPUT_TYPE`), `UpdateUsersList`, `SetState` |
| `CFriendsScreen` | 15 | 3,231 | `CreateScreen` (17 call sites), `UpdateStatusBox` (14) |
| `CSkinFile` | 14 | 1,903 | `Initialize` → `CopyStringA`, `Open`, `Read`; `LoadBundlerResource`/`LoadSection` → `_D3D_AllocContiguousMemory` |
| `CVoiceMailScreen` | 13 | 2,202 | `CreateScreen` (7 sites) |
| `CAlertFeature` | 12 | 1,368 | `ActivateFeature`, `HideMessageScreen` |
| `CUIPlugin` | 10 | 1,137 | the plug-in seam (`CPlugIn_*`-style entry points) |
| `CFeatureContext` | 10 | 417 | `DoWork`, `SetVoiceMailToSpeakers`, `SetVoiceMailEngine` |
| `CScreen` | 8 | 432 | `SetText` → `CSkinFile::GetString` + `CLiveEngine::SeparateTextAndIcons`; `CreateScreen` → `CSkinFile::Load` |

Five readings the edges support:

1. **The frame loop is in the engine, not in a screen.** `CLiveEngine::DoWork` (202 B) asks the engine which
   screen has input, reads `CInput`, then calls `CFeatureContext::DoWork` — so features are pumped, screens are
   passive, and input is routed by the engine.
2. **Features own screens.** `StartFeature` (206 B) hides the previous top screen and activates the new
   feature's; `CAlertFeature` does the same for message screens. That is a stack, not a state machine.
3. **Skins are bundled resources with sections and IDs, loaded into D3D-visible memory.**
   `CSkinFile::Load` → `Open`/`LoadSection`/`CloseHandle`, `GetResource` → `LoadedSection::GetValueForID`, and
   `LoadBundlerResource`/`LoadSection` allocate through `_D3D_AllocContiguousMemory` — the skin's images live in
   memory the GPU can read.
4. **A screen is a skin section plus widgets.** `CScreen::CreateScreen` calls `CSkinFile::Load` and `Destroy`
   calls `CSkinFile::Unload`; `SetText` pulls the string from the skin and splits it into text and icons; list
   items come in plain and "grey" variants (`AddListItem`, `AddGreyListItem`) — enabled and disabled entries.
5. **There is a reboot path.** `CLiveEngine::Reboot` (157 B) calls `_XOnlineCleanup`, `_XOnlineTitleUpdate` and
   `_XLaunchNewImageA` — a Live screen can relaunch the title, which is what a dashboard does after changing a
   Live setting.

**Is it used?** Every one of the 104 methods in the four artifacts has a direct call site, and the subsystem's
408 call sites are **all inside `.text`** — UIX is linked there, not into the `XONLINE` section its names suggest.
But 132 distinct functions do the calling and most of them are UIX's own classes (`CFriendsScreen::CreateScreen`
is the busiest at 17 sites), with 89 sites inside `.text` functions that have no map row. So the honest answer
is **linked and internally live, with the title's own entry point not yet identified** — this reading does not
show game or engine code opening a UIX screen, and it does not show that none does.

**What this is not.** No strings in any of these four artifacts (release build) and no decompiled bodies. The
open question this section raised — how a Live screen ever opens — is answered below: UIX is reached through one
native, `UIXInteraction.InitUIX`, and no shipped script calls it.

`CUIPlugin` is *not* that seam, which the class's own callees settle (10 methods, 5 with callees,
`inventory/cuiplugin_methods.csv`): `CreateObject` (236 B) calls `CArray::Append`, `_PluginSupport_GetLayout` and
`CUIObject::Create`; `InsertItem` (134 B) adds `CArray::Insert` and its own `GetLBItemHeight`; `SetPluginSupport`
(53 B) calls `StdImage::Retrieve`; and it carries font helpers (`FontDrawText`, `FontGetTextSize`). It is a list
widget whose layout comes from a `PluginSupport` module — "plug-in" names the layout, not a registration API.

## The audio stack: `DirectSound`, the MCPX, and Xbox Live voice (2026-11-19)

Five readings of the API stack, **303 methods, 34,328 bytes**, plus the three media decoders below --
**345 methods, 41,923 bytes** in all: the DirectSound API object (59), the MCPX APU driver (44), the voice
path under it (74), Xbox Live voice chat (104), the positional layer (22) and the decoders (42) --
`inventory/dsound_methods.csv`, `dsound_object_methods.csv`, `dsound_voice_methods.csv`,
`mcpx_apu_methods.csv`, `mcpx_core_methods.csv`, `mcpx_buffer_methods.csv`, `mcpx_stream_methods.csv`,
`mcpx_voice_client_methods.csv`, `xhv_engine_methods.csv`, `voice_chat_mode_methods.csv`,
`local_talker_methods.csv`, `remote_talker_methods.csv`, `i3dl2_listener_methods.csv`,
`hrtf_source_methods.csv`.

| reading | classes | methods | bytes | library |
|---|---|---:|---:|---|
| the API layer | `DirectSound` 35, `CDirectSound` 14, `CDirectSoundVoice` 10 | 59 | 4,674 | dsound |
| the APU driver | `CMcpxAPU` 26, `CMcpxCore` 18 | 44 | 5,353 | dsound |
| the voice path | `CMcpxVoiceClient` 27, `CMcpxStream` 24, `CMcpxBuffer` 23 | 74 | 11,165 | dsound |
| Xbox Live voice chat | `CLocalTalker` 32, `CXHVEngine` 26, `CVoiceChatMode` 23, `CRemoteTalker` 23 | 104 | 9,056 | xvoice |
| positional audio | `CI3DL2Listener` 12, `CFullHRTFSource` 10 | 22 | 4,080 | dsound |

**The attribution is the archive member.** Each artifact row now carries `lib`/`object`, taken from the COFF
member the symbol matched: **199 methods from `dsound.lib` (14 objects) and 104 from `xvoice.lib` (5)** —
`dsapi.obj` 37, `mcpvoice.obj` 28, `mcpapu.obj` 27, `mcpstrm.obj` 26, `mcpbuf.obj` 25, `mcpxcore.obj` 20,
`hrtf.obj` 10, `i3dl2.obj` 12, `heap.obj` 4, `ac97.obj` 2, plus one or two each in `dspdma.obj`,
`epdsp.obj`, `gpdsp.obj`, `wavexmo.obj`, `xhvengine.obj` 25, `voicechatmode.obj` 23,
`voicemailmode.obj` 1. They sit at `0x00344318`..`0x004aa5bb` — xvoice in its own section, dsound in the
next one — so this is the SDK's audio stack linked into the image, not a reading of names.

**The instrument changed twice, and both changes are visible in the artifacts.** The `lib`/`object` pair is
new, and `callee_classes` — the column the earlier readings printed and never filled — was **empty on all
1,260 rows of the twelve artifacts then committed**, because the class was taken from a display name
(`CXnSock::TcpConnect`) instead of the raw reloc symbol. Fixed at the source, every earlier reading
re-renders with **zero fields moved** (144 `CXnIp` rows, 377 `CXo`, 98 `XGRAPHICS`, 49 `Instruction` … all
compared field by field), so the corpus is the control for the fix. `--check` now validates **all 25
artifacts** (1,315 methods, 1,315 with attribution) rather than the one class the tool defaulted to.

**No string anywhere.** 0 of the 303 methods carries a literal, against 24 of `PreProcessor`'s 49: these
libraries ship without assert text, so no method in this stack can be confirmed by its own message. The
control that this zero is the library's and not the reader's: a byte scan of the 248 bodies finds 120
printable runs of four or more characters, and every one sampled is an encoding, not text — `SUVW` is
`53 55 56 57`, four register pushes.

### The API layer: one critical section, one commit

`DirectSound` is a namespace of nested classes (`CDirectSound`, `CDirectSoundVoice`, `CDirectSoundBuffer`,
`CDirectSoundStream` and their `…Settings`), and **13 of `CDirectSound`'s 14 entry points open with
`DirectSoundEnterCriticalSection`** — while the nested `CDirectSoundVoice` never takes it (0 of 10): the lock
belongs to the object the game holds, not to the pieces under it. Every setter
(`SetPosition`, `SetVelocity`, `SetOrientation`, `SetDistanceFactor`, `SetRolloffFactor`) does nothing but
funnel into `CommitDeferredSettings`, which is where `CDirectSound3DCalculator::Calculate3D` and
`CMcpxAPU::CommitI3DL2Listener` actually run — so 3D state commits once per frame, not once per setter.
`Initialize` (313 B, the largest here) allocates the settings object, constructs the APU and calls
`CMcpxAPU::Initialize`; `CreateSoundBuffer` / `CreateSoundStream` construct and initialise their nested
object. `DoWork` → `CMcpxAPU::ServiceDeferredCommandsLow`: the deferred queue is pumped from the caller's
own thread, which is why the game has to call `_DirectSoundDoWork`.

### The APU driver: an ISR/DPC pair, two command queues, and a hardware handshake

`CMcpxAPU::Initialize` installs the interrupt pair (`ApuInterruptServiceRoutine`,
`ApuInterruptDpcRoutine`), a deferred-command DPC, and both command queues
(`ScheduleDeferredCommandHigh`/`Low` with 214/37 B bodies, `ServiceDeferredCommandsHigh`/`Low`,
`RemoveDeferredCommand`/`High`). Voice slots are a pool (`AllocateVoices`, `FreeVoices`), and
`WaitForMagicWrite` (99 B) polls a register through `CMcpxCore::SetPrivLockState` — a handshake with the
DSP rather than a wait on a driver object. The two error paths are handlers of their own: `HandleFETrap`
(front-end trap → `CMcpxCore::SetFrontEndState`) and `HandleDeltaPanic`, which resets the AC97 channels
(`CAc97Channel::Reset`, `Run`), the global counters and the encode processor.

`CMcpxCore` is the bring-up: `Initialize` (108 B) calls `Reset`, `AllocateApuMemory`,
`SetupFrontEndProcessor`, `SetupVoiceProcessor`, `SetupAc97`, `SetupDSPs`, `SetSetupEngineState` and
`CAc97Channel::Run` — one method per hardware block, and the last two put the codec into its engine state
and start the channel. `AllocateApuMemory` (384 B) sizes the scatter-gather and slop heaps
(`CMcpxBufferSgeHeap`, `CMcpxSlopMemoryHeap`) from the encode processor's own scratch request;
`SetupAc97` (185 B) creates the codec device and channels and attaches a packet; `SetupEncodeProcessor`
(420 B) builds the encode DSP (`CMcpxEPDspManager`, AC3 analog/digital output). **14 of these 44 methods
take the interrupt lock.**

### The voice path: a voice object, and a buffer or a stream on top of it

`CMcpxVoiceClient` (27 methods, 5,595 B — the largest class in the stack) is the voice:
`AllocateVoiceResources` → `CMcpxAPU::AllocateVoices`, `ReleaseVoiceResources` → `FreeVoices`, and
`PauseVoice` moves the voice between the APU's synchronous and free lists (`AddSynchVoice` /
`RemoveSynchVoice`). `CMcpxBuffer` (23) drives one voice from a buffer: `PlayFromPosition` (383 B) and
`PlayFromCurrent` (321 B) convert bytes↔samples through the client and activate it, `GetCurrentPosition`
reads `CMcpxVoiceClient::GetSslPosition`; `MapBuffer`/`UnmapBuffer`/`MapEffectsBuffer` place the data.
`CMcpxStream` (24) drives one from a packet queue: `SubmitPacket` takes an `XMOAcceptPacket` payload,
`MapPacket` (355 B) maps it into the voice's scatter-gather list, `CommitSsl` activates the voice and
`Process`/`CompletePackets` retire what the DSP finished, so **a DirectSound stream is a queue of XMO
packets decoded by the APU, not by the CPU**. Streaming and static buffers are the same voice object with
a different producer. **26 of the 74 take the interrupt lock**, `CMcpxVoiceClient` 15 of them.

### Xbox Live voice chat: a codec and a stream per remote player

`CXHVEngine` (26, `xvoice.lib`) is the engine: `InitEngine` (238 B) builds a custom stack, a
`CLocalTalker` and a `VoiceFrameDpc`; `CheckDeviceChanges` (165 B) polls `_XPeekDevices` and calls
`OnDPCRemoval`/`OnDPCInsertion`; processing modes are switched by name
(`EnableProcessingMode`, `ShutdownProcessingModes`, `GetProcessingMode`); voice mail lives here too
(`VoiceMailRecord`, `VoiceMailPlay`). `CVoiceChatMode` (23) is the mode: `AdditionalInitialize` (259 B)
constructs a `CRemoteTalker`, the mode's own `VoiceChatDpc` and its allocator; `DoDPCWork` compresses the
local microphone packet (`CLocalTalker::CompressVoiceChatPacketForNetwork`) and decompresses each remote
one (`CRemoteTalker::DecompressVoiceChatPacketForPlayback`); `RearrangePriorities`/`SetPlaybackPriority`
reorder the talkers.

**It creates its own DirectSound device.** `CVoiceChatMode::DSoundInitialize` calls `_DirectSoundCreate`
and `_IDirectSound_SetMixBinHeadroom` — the only `_DirectSoundCreate` in these 303 methods — and the
routing is a mix bin per talker (`_IDirectSoundStream_SetMixBins`, `_IDirectSoundStream_SetMixBinVolumes`,
`_IDirectSoundStream_SetHeadroom`). `CLocalTalker` (32) is the microphone half: `OnInsertion` calls
`_XVoiceCreateMediaObjectEx` and installs `MicrophoneCallback`/`HeadphoneCallback` (the USB media object),
`CompressVoiceChatPacketForNetwork` → `CMicrophoneProxy::EncodeOutputPacket`, and recording writes a WAV
(`VoiceMailRecord` → `GenerateWAVHeader`, `EndRecording` → `UpdateWAVFileSize`). `CRemoteTalker` (23) is
the playback half: `InitializeStream` (320 B) calls `_DirectSoundCreateStream` with a trigger-packet
callback and a double-buffering callback, `StreamCallbackTriggerPacketsWork` runs
`CWMAVoiceDecoder::Process` and then `_IDirectSoundStream_FlushEx`, and `InitializeLoopbackMode` /
`InitializeVoiceMailMode` are the other two modes. Voice chat is therefore **WMA encode/decode plus one
DirectSound stream per speaker, with mix bins as the mixer**, and it needs no interrupt-level code of its
own: **0 of these 104 methods take the lock.**

### Positional audio: an I3DL2 parameter set and a real HRTF filter pair

`CI3DL2Listener` (12, 2,236 B) turns listener properties into an I3DL2 parameter set: `CalculateI3DL2`
(904 B, the largest single method in the audio set) drives `SetReflectionsGain`, `SetReflectionsDelay`,
`SetDecayTimes`, `SetDecayFilter`, `SetDiffusion`, `SetReverbDelay`, `SetSize` and `SetInputFilter`, with
`mBToLinear` conversions and a one-pole low-pass (`Get1PoleLoPass`) per filter. `CFullHRTFSource` (10)
holds the per-source 3D maths: `CalcPolarCoords` (355 B), `CalcNormPos`, `CalcConeAngle`,
`CalcRelativeVelocity`, `GetFilterPair` (284 B — the pair `CMcpxVoiceClient::LoadHRTFFilter` loads), and the
gain reductions `GetDistanceVolume`, `GetFrontRearVolume`, `GetCenterVolume`, `GetConeVolume`,
`GetDopplerPitch`. Both sit on the SDK's shared `Math::` helpers (17 methods here), so the 3D path is float
arithmetic in user code and the APU receives parameters, not geometry.

### The media decoders: the SDK's WAV/AIFF reader, a timed queue, and a USB endpoint

Three more readings, **42 methods, 7,595 bytes**: `WaveLoaderFoo::CWaveFile` (11, 2,098 B — `dsound.lib`,
`dscommon.obj`), `CQueueXMO` (18, 2,705 B — `xvoice.lib`, `queuexmo.obj`) and `XHawkMediaObject` (13,
2,792 B — `xvoice.lib`, `hawk2.obj`). With them the stack is **345 methods, 41,923 bytes: 210 from
`dsound.lib` (15 objects), 135 from `xvoice.lib` (7)**, and `make native-method-notes` regenerates all 28
readings in one pass (`--check` validates 1,357 methods, 1,357 attributed). None of the 42 carries a string,
the same zero as the other 303.

**`CWaveFile` is the file reader, and it decodes two containers.** `Open` (520 B) is the whole sniff: a
`CStdFileStream::Open`, then a `CRiffChunk::Read`, then `GetWaveFormat` (142 B) or `GetAiffFormat` (341 B) —
so RIFF/WAV and AIFF are both accepted, the second through the `EndianSwapWord`/`EndianSwapDword` calls that
`ConvertAiffPcm` (two overloads, 60 + 24 B) and `ReadSample` (73 B) apply on the way out. Each container has
its own loop-region reader (`GetWaveDLSLoopRegion` 158 B, `GetWaveMIDILoopRegion` 155 B,
`GetAiffLoopRegion` 463 B), joined by `GetLoopRegion` (135 B); `Close` gives the buffers back to
`CMemoryManager`. The reader is the library's, not the game's: `_XWaveFileCreateMediaObject@12` wraps it, and
the body that calls that is the audio subsystem's own stream virtual (`0x0002e3dd`, below).

**`CQueueXMO` paces packets on a millisecond clock, and its own time source is the performance counter.**
`GetTickCount` (57 B) is `_QueryPerformanceCounter` with `__allmul`/`__alldiv`, not the Win32 call of the same
name; `SetActivationTime` (48 B) and `GetRealOutputTime` (100 B) convert between a packet's own timestamp and
that clock; `GetTimestamp` (160 B) answers a `StateOfPacket` for a packet time, with `IsTimeStampInList` (57 B)
as its membership half; `RecomputeQueueParameters` (419 B) recomputes the queue window from those stamps. The
buffers are two lists — `AddFreeBuffer` (26 B), `AddInUseBuffer` (145 B), `IsHWMReached` (44 B) as the
high-water-mark test and `GetOutputWorkingBuffer` (80 B) as the buffer under the mark. Both ends are
`XMEDIAPACKET` calls that end by signalling an event (`_SetEvent`): `InputProcess` (382 B) and `OutputProcess`
(275 B), which is what makes the queue the producer/consumer boundary of a stream. `Initialize` (513 B) sizes
the buffers through `XVoiceSysAlloc` and builds a nested `BackupBuffer` (30 + 22 B).

**`XHawkMediaObject` is a USB isochronous endpoint, so the microphone path is not an APU voice.**
`ProgramTransfer` (562 B) submits one URB per queued packet through `IUsbDevice::SubmitRequest`;
`TransferComplete` (486 B) takes a `_USBD_ISOCH_TRANSFER_STATUS`, re-queues the packet and calls
`ProgramTransfer` again; `AllocateStreamingResources` (338 B) / `FreeStreamingResources` (199 B) open and
close the pipe; `CloseEndpoint` (91 B) and `CloseEndpointComplete` (124 B) tear it down; `AbortMediaPackets`
(129 B) drains what is in flight. Format is two control requests, `SetSampleRate` (271 B) and `SetAGC`
(261 B) — `SubmitRequest` → `SetExtension` → `RemoveComplete` in both. `Process` (149 B) is the XMO entry:
`XMOAcceptPacket` → `CHawkPacketQueue::InsertTail` → `ProgramTransfer`. The queue's single constructor
caller confirms the arrangement: `CQueueXMO::Initialize` has exactly one, `_XVoiceQueueCreateMediaObject@8`
(inside `xvoice.lib`), and `CQueueXMO::Process` has none because it is a virtual.

### Who calls the three audio entry points

The image exports **three** `_DirectSound*` entry points, not four, and a direct call is `E8 rel32` — invisible
to a dword scan, which is why `module_export_census.csv` could hold none of them. `tools/xdk-entry-callers.py`
(new) scans every executable section for a call to each, keeps a site only where a forward decode lands
exactly on it (the same boundary rule as `tools/record-writer-calls.py`), names the containing body from
`symbols/xbox_functions.csv`, and then says how that body is itself reached: a class-table slot, a dword
reference, or its own direct callers. **11 sites** (`inventory/xdk_entry_callers.csv`):

| entry point | sites | caller, and how that caller is reached |
|---|---:|---|
| `_DirectSoundCreate@12` `0x004a20e0` | 4 | `FUN_0002fd70` (`XBoxAudioSubsystem` +0x7c, slot 31 of 55); `CVoiceMailMode::DSoundInitialize`, `CVoiceChatMode::DSoundInitialize`, `CLoopbackMode::DSoundInitialize` |
| `_DirectSoundCreateStream@8` `0x004a2127` | 3 | `FUN_0002ae00` (the voice stream factory); `XboxAudio_XboxAudioSubsystem` `0x0002e210` (+0xc8, slot 50); `CRemoteTalker::InitializeStream` |
| `_DirectSoundDoWork@0` `0x004a0552` | 4 | `XboxAudio_XboxAudioSubsystem` `0x0002feb0` (+0x88, slot 34); `CRemoteTalker::StartStream` twice and `ShutdownStream` once |

**The game's own device is `FUN_0002fd70`, and nothing calls it directly.** Its address appears once in the
image — `0x004d2274`, inside `XBoxAudioSubsystem`'s class table `0x004d21f8`, **slot 31 of 55** (dispatch
offset `+0x7c`) — so device creation is a virtual method of the game's audio subsystem: not a UE2 export, not
one of the 345 library methods, and not findable by scanning for a stored address. Its 272 bytes are
`_DirectSoundCreate`, then `_XAudioDownloadEffectsImage`, then seven XboxAudio bodies
(`0x0002cfd0`, `0x0002c800` twice, `0x00032040`, `0x0002e900`, `0x00057440`, `0x00055ed0`) — bring-up, not a
getter. The other three sites are the voice modes, each creating its own device, so the image holds more than
one `CDirectSound` by design (which is what the voice-chat reading predicted).

**The stream half is three more virtuals of the same class, and the voice factory chain is named.**
`0x0002e210` (+0xc8, slot 50) is the file stream: `_XWaveFileCreateMediaObject@12`, then
`_DirectSoundCreateStream@8`, then `_CreateThread` — a WAV played on its own thread, which is how music and
voice mail arrive. `0x0002feb0` (+0x88, slot 34, 5,277 B) is the per-frame pump: it calls
`_DirectSoundDoWork`. `0x0002dff0` (+0xd0, slot 52) sets the next stream — its own `SetNextStream` and
`New Pending File` literals beside `_XWaveFileCreateMediaObject@12` — and `0x0002ae00` (210 B) is
`XboxAudio_CreateVoiceStream`, called twice from `XboxAudio_SetUpVoiceStreams`. The chain those bodies
belong to is the next subsection: 31 bodies, every one named.

### The voice modes: four singletons with a 27-slot vtable (2026-11-20)

The stack above stops at the engine: `CXHVEngine` owns a *mode*, and the four modes are what opens a
`CDirectSound` for voice. They are measured now — the last audio classes without a reading:
`CVoiceMailMode` 12 methods, `CLoopbackMode` 4, `CBaseMode` 4, `CInactiveMode` 1 (plus `CVoiceChatMode`'s 23
from the earlier bundle) — **44 methods, 21 in this bundle**, all attributed to `xvoice.lib`
(`voicemailmode.obj`, `loopbackmode.obj`, `inactivemode.obj`, which also carries `CBaseMode`). Artifacts:
`inventory/{voice_mail,loopback,base,inactive}_mode_methods.csv`; `LIBRARY_READINGS` now names all four, so
`make native-method-notes` regenerates and `--check` validates 36 readings.

The class reading is the vtable reading: `tools/xvoice-modes.py` scans `.rdata` for runs of code pointers and
attributes each to the class whose `ValidateSet` it installs, which is what separates a base table from a
derived one. Four tables, **27 slots each**, contiguous at `0x00540570` (`CInactiveMode`), `0x005405e0`
(`CVoiceMailMode`), `0x00540658` (`CVoiceChatMode`) and `0x005406c8` (`CLoopbackMode`), each installed in a
zero-filled singleton in `.data` (`0x005ca294`, `0x005ca2a8`, `0x005ca348`, `0x005ca450`) at the object's own
size (20 / 160 / 264 bytes). **34 of the 108 slots are inherited from the inactive table's same slot**, so the
family's shape is "one base implementation, three overrides of `ValidateSet`/`AdditionalInitialize`/
`ProcessFrame`".

`DSoundInitialize` is `QAE` — public, not virtual — so it appears in no table and is the one audio method with
a caller that is not a vtable slot: each mode's own `AdditionalInitialize` calls it (`0x00345828`, `0x00344f62`,
`0x003460b2`). The artifact also records the four instructions before each `_DirectSoundCreate` call, which is
how the page says the mailbox and loopback modes pass a null GUID and store the device at `+0x10` while chat
stores at `+0x24`.

### The module chain: 31 bodies, every one named from its own bytes (2026-11-19, closed 2026-11-20)

The entry-point census ended with "the module census places none of the bodies around them because they call
only unnamed bodies". That is a population defined by calls, so it can be walked: `tools/xboxaudio-chain.py`
seeds from every unnamed `.text` body with a direct call into the audio SDK (26 such names — `_DirectSound*`,
`_IDirectSound*`, `_XVoice*`, `_XAudio*`, `_XWave*`, `_XMedia*`, `_XHV*`), follows unnamed callers and callees
to a fixpoint, and stops at a callee any body outside the chain also calls. **31 bodies, 240 call edges, 12
library edges, 16 stops** (`inventory/xboxaudio_chain.csv`), each decoded by `objdump`, so a call site is an
instruction boundary and not a byte pattern. **No member is `FUN_*` any more**: the first ten were named from
their edges, and the other 21 were read body by body and named from their own structure — the table is the
whole module chain in address order.

| body | name | size | what its own bytes say |
|---|---|---:|---|
| `0x0002a250` | `XboxAudio_CreateVoiceEncoder` | 89 | `operator new` twice and `_XVoiceCreateOneToOneEncoder@4` |
| `0x0002a2f0` | `XboxAudio_CreateVoiceCodecs` | 104 | two `_XVoiceCreateMediaObjectEx@28` with two callbacks into one slot object |
| `0x0002a410` | `XboxAudio_ReleaseSlotObjects` | 97 | releases the interfaces at `+0x8`/`+0xc`/`+0x10` through vtable slot 4 and frees `+0x1c`/`+0x20` |
| `0x0002a480` | `XboxAudio_SubmitSlotBuffers` | 115 | two 24-byte descriptors over the buffer at `+0x1c`, submitted through vtable slot 0x10 of the interface at `+0x8`; `E_FAIL` if either call fails |
| `0x0002a500` | `XboxAudio_SubmitMixBuffers` | 152 | slot index from `+0x4`, three fields copied through `0x2ac50`, two buffers zeroed and submitted to the second interface (`+0xc`) |
| `0x0002a6f0` | `XboxAudio_InitVoiceSlot` | 112 | per index (`this+2*index+0x74`): codecs, the two slot stages, a bit in `this+0x60` |
| `0x0002a770` | `XboxAudio_FindSlotByKey` | 72 | scans the `+0x4` entries of the 0x34-byte table at `+0x54` for a 12-byte key (third dword masked to two bits); returns the index or the count |
| `0x0002a7c0` | `XboxAudio_ApplyBufferParams` | 165 | writes the key at `rec[index*4+0x20]`, then a seven-dword parameter block (four floats floored at -10000 centibels) to the import at `0x4a0530` |
| `0x0002a9b0` | `XboxAudio_InitBufferDesc` | 80 | zeroes a six-dword descriptor and fills pointer/length/index from `[this+0x38]`, then memsets the length |
| `0x0002ac50` | `XboxAudio_GetSlotFields` | 67 | indexes the 0x20-byte records at `[this+0x14]` and writes three fields through three out-pointers |
| `0x0002ae00` | `XboxAudio_CreateVoiceStream` | 210 | `_XVoiceQueueCreateMediaObject@8`, `_XVoiceCreateOneToOneDecoder@4`, `_DirectSoundCreateStream@8`, then `this+0x3c`-byte buffers |
| `0x0002af50` | `XboxAudio_HasPendingKey` | 89 | membership test over the 12-byte records at `[this + channel*16 + 0x158]`; returns 0 or 1 |
| `0x0002b140` | `XboxAudio_RemoveLiveKey` | 228 | removes a 12-byte key from the list at `[this + channel*16 + 0x118]` (shift down, count -= 12), writing it back through `0x2a7c0` when the other list does not hold it |
| `0x0002b230` | `XboxAudio_RemovePendingKey` | 228 | the mirror removal on the `+0x158` list, writing the key back when the `+0x118` list does not hold it |
| `0x0002b320` | `XboxAudio_StopSoundByKey` | 230 | over four channels: remove the key from both lists, find the slot, call vtable slot 0x18 on its object (stop), clear the key and the slot's `+0x18` |
| `0x0002b410` | `XboxAudio_CloseSoundDevice` | 229 | clears the channel's bit in `+0x60`, zeroes its 0x24-byte block at `+0x74`, stops the channel's object and re-registers every key of both lists |
| `0x0002b500` | `XboxAudio_StopAllSounds` | 91 | walks the 0x34-byte slot table (`+0x4` count, `+0x54` base) calling the keyed stop for every slot that holds a key |
| `0x0002b5c0` | `XboxAudio_ShutdownSlots` | 358 | stops every registered sound, then eight vtable-slot-4 releases and six `operator delete` frees |
| `0x0002b730` | `XboxAudio_PollDeviceChanges` | 178 | `_XGetDeviceChanges@12` on two masks, merged into `+0x64`/`+0x68`; per channel close on a change and initialise when the bit is present, armed at `+0x70` and not yet started in `+0x60` |
| `0x0002b8c0` | `XboxAudio_SetUpVoiceStreams` | 640 | buffer geometry from the sample rate (/48000, /10), `_IDirectSound_SetMixBinHeadroom@12` per bin, two `CreateVoiceStream` calls |
| `0x0002d120` | `XboxAudio_FindFreeableSource` | 364 | the source with the smallest float at `+0x84` among the 0x28-byte entries at `+0xd8`, skipping nulls and the `+0x74`/`+0x75` flags; a `1e9` seed, so the first candidate always wins |
| `0x0002d3f0` | `XboxAudio_StopStreamingSound` | 167 | literal `SF_StreamingMode`; `_IDirectSoundBuffer_Stop@4`, `_Release@4` |
| `0x0002db10` | `XboxAudio_UnregisterSoundBytes` | 198 | literals `Sound Data Freed`, `UnRegSoundBytesAlloc`; calls the stop above |
| `0x0002dff0` | `XboxAudio_SetNextStream` | 531 | slot 52 (`+0xd0`); its literal `SetNextStream`, `_XWaveFileCreateMediaObject@12` |
| `0x0002e900` | `XboxAudio_RegisterCounters` | 691 | eight group/name registrations (`Audio` + `PlaySound` twice, `Occlusion` twice, `Update`, `Streaming`, `Regular`, `StoppedSounds`), returns kept at `+0x10`–`+0x20` |
| `0x0002ed20` | `XboxAudio_FlushSounds` | 137 | literal `[RA] : Flushing`; frees through `0x2d120`/`0x2db10` while a pending read plus `+0x180` would pass **0x700000** |
| `0x0002edb0` | `XboxAudio_OpenStreamRead` | 301 | `Read %s from hard disk`; `_CreateFileA@28`, `_CreateEventA@16`, `_SetFilePointer@16`, `_ReadFile@20` into the overlapped block at `+0x134` |
| `0x0002fa90` | `XboxAudio_SetSoundDevice` | 278 | slot 32 (`+0x80`); stops the old device's streaming sounds, stores the new device at `+0x98` and re-applies two mix settings |
| `0x0002fbb0` | `XboxAudio_CompleteStreamRead` | 203 | `_GetOverlappedResult@16`, accumulates `+0x13c`, and at `+0x124` closes both handles and flags the stream (`+0x74`: -0x1000, +0x2000) |
| `0x0002fd70` | `XboxAudio_CreateDevice` | 272 | slot 31 (`+0x7c`), no direct caller; `_DirectSoundCreate@12`, `_XAudioDownloadEffectsImage@16`, six bring-up calls |
| `0x00032040` | `XboxAudio_ResetStreamArray` | 65 | walks the 0x50-byte records (`+0x4` count) calling `0x31df0` on each, then sets the count to 0 and reallocates one record |

**What the 21 readings establish is one data structure, not 21 functions.** A slot record is 0x34 bytes,
indexed by a 12-byte key (three dwords, the third masked to two bits) in a table of `+0x4` entries at `+0x54`;
each slot owns two keyed lists per channel — 12-byte records at `+channel*16+0x118` and at
`+channel*16+0x158` — over **four** channels, plus an 0x24-byte block at `+0x74+channel*0x24` and a bit per
channel in `+0x60`. The two list removers (`0x2b140`, `0x2b230`) are mirrors: each removes a key from its own
list and writes it back through `0x2a7c0` when the *other* list does not hold it, so a key is tracked in
exactly one of the two at a time. `0x2b410` is the device-close path and re-registers every key afterwards,
which is what makes the pair a state machine rather than a cache.

**The device poll is the one body that explains the four channels.** `0x2b730` asks `_XGetDeviceChanges@12`
twice (two device masks, `0x4bdd68` and `0x4bdd74` — the same two globals `CreateVoiceCodecs` hands to its
media objects), merges them into `+0x64`/`+0x68`, and then, per channel, closes it on a change and starts it
when it is present, armed at `+0x70` and not yet started. So the four channels are four voice devices and the
module polls for a headset being plugged in.

**The streaming read is a pair, and its memory policy is a number.** `0x2edb0` opens the file named by the
queued stream object (`Read %s from hard disk`), sets up an event and an overlapped read at `+0x134`;
`0x2fbb0` completes it, accumulates `+0x13c` until the requested `+0x124` is in, then closes both handles and
sets the stream's flags. `0x2ed20` (the body the pump calls when `+0x160` is set) only frees when the pending
read plus `+0x180` would pass **7 MB**, and it frees the source with the *smallest* float at `+0x84` — a size
cap with an oldest-or-quietest eviction, not a time-sliced stream pool.

**The slot-31 census, and why the dispatcher is not module code.** Every `call [reg+0x7c]` in an executable
section, boundary-validated, is **115 sites**: 27 in `Engine`, 13 in `Core`, one each in `XboxDrv`
(`XboxDrv_XboxViewport`) and `Gui`, and 72 in bodies the symbol map does not name — **none in `XboxAudio`**.
**17** of the 115 dispatch a second audio-subsystem slot (`+0x88`, the pump) from the same body. So a rebuild
supplies the **five** virtuals the table installs from the module — device (31, `+0x7c`), set-device (32,
`+0x80`), pump (34, `+0x88`), file stream (50, `+0xc8`) and next stream (52, `+0xd0`) — and engine code calls
them: the module never dispatches its own device virtual. The receiver's *type* at those 17 sites was settled
by the field rather than by a guest pass: `Engine.Audio` is `+0x34` (`audio_engine_init.csv`), and **no**
`call [reg+0x7c]` site in the image loads its receiver from that field.

**A displacement is not a type, and `+0x80` is the proof.** `Engine_UnEngine_InitAudio` dispatches `+0x80` on
the object it built from `XboxAudio.XHVVoiceSubsystem`, and that class has its **own** table (`0x004d2ad8`,
whose slot 32 is `0x00032480`) — a different body from `XBoxAudioSubsystem`'s slot 32 (`0x2fa90`). Two classes
that share a displacement are two classes; only the table says which body runs.

### What this reading does not say

- **Ordering.** Every claim above is a call edge. The APU's register writes are inside bodies that have not
  been decompiled, so "the ISR runs before the DPC" is the SDK's structure, not this build's timing.
- **Who re-dispatches the device slot.** Answered for the creation path only: `Engine_UnEngine_InitAudio`
  dispatches slot 31 at `0x001213c4` on the object it just built, and no site in the image loads a receiver
  from `+0x34`, so nobody reaches the device through the engine's field again. The 78 untyped receiver sites
  stay untyped, and the field group among them cannot be `Engine.Audio`.

## The UIX entry point: a native that nothing calls (2026-11-19)

The UIX reading ended with a question — who opens a Live screen — and the answer is a chain with exactly
one link at each step:

    UUIXInteraction_InitUIX   0x00307cd0   652 B   the script-callable native
      -> _UIXCreateLiveEngine@12   0x003319fc       C-linkage entry, 1 call site in the image
        -> CLiveEngine::CreateEngine  0x003315d4    1 call site, the one above

`UUIXInteraction_InitUIX` is the body for a `native` function, and the script tree says which: `GUI` declares
**`UIXInteraction`** (`native`, `Transient|NoExport`, super `Engine.AbstractUIXInteraction`) with `InitUIX` as
`Native; Public` (`inventory/class_table_*.csv`, `inventory/function_flags.csv`). So the dashboard is reachable
from UnrealScript by exactly one door, and the door is named.

**No shipped script goes through it.** A UE2 package that calls a function from another package imports the
function's *name*, so the name table answers "who calls this" without disassembling a byte:

| token | packages whose name table holds it |
|---|---|
| `InitUIX` | 2 — `Engine.u`, `GUI.u` (the two that declare it) |
| `UIXInteraction` | 4 — those two plus **`StargateGame.u`, `StargateMenus.u`** |
| `ConsoleCommand` (control) | 11 |
| `ClientMessage` (control) | 4 |

The controls are what make the shape meaningful: a widely used native appears in eleven packages and a
moderately used one in four, so a token found in only its two declaring packages is not being called from
outside. The game's own packages name the *class* but never the *method* — and there is no game subclass of
`UIXInteraction` in `inventory/classes.csv`, only the two SDK rows.

**So the dashboard is present, internally live, and never opened in this build.** For Phase 6 that is a
simplification rather than a task: a rebuild can stub the UIX classes, because nothing in the shipped script
reaches them. It also explains the usage scan that found 408 call sites all inside UIX's own code — the
subsystem is self-consistent and unreferenced from above.

**What this does not prove.** A call from inside `GUI.u` itself needs no import and would leave no trace in
another package's name table; this check cannot rule that out. A native called through the `GNatives` index
rather than by name is a different case — but for a native declared in *another* package the compiler must
still import it, which is what makes the absence meaningful here. The bytecode of `GUI.u`'s own classes is the
place to close the gap, and `tools/uscript-dis.py --resolve` is the tool for it.

## The D3D8 driver and its miniport: state, push buffer, FIFO (2026-11-19)

`D3D` (82 methods, 18,216 B) and `CMiniport` (52, 11,855 B) are not a helper library — they are the Xbox's
**D3D8 driver** inside the image, and the callee graph makes its shape plain
(`inventory/d3_d_methods.csv`, `cminiport_methods.csv`):

| method | bytes | calls |
|---|---:|---|
| `CMiniport::HalGrInit3d` | 2,527 | the graphics HAL's init — largest method in either class, and a leaf here |
| `D3D::Inverse4x4` | 1,348 | `D3D::JBInvSqrt` |
| `D3D::LazySetTextureTransform` | 896 | `_D3DDevice_MakeSpace`, `D3D::DbgRecordPushStart`, `_XMETAL_Push1`, `CDevice::EndPush` |
| `D3D::LazySetShaderStageProgram` | 892 | `D3D::ValidTextureModes`, `_D3DDevice_MakeSpace` |
| `D3D::CommonSetMultiSampleModeAndScale` | 853 | `FloatToLong`, `DXGRIP`, `_D3DDevice_MakeSpace`, `_XMETAL_Push…` |
| `D3D::LazySetLights` | 846 | `_D3DDevice_MakeSpace`, `SetSpecularParameters`, `_XMETAL_Push…` |
| `CMiniport::HalGrControlLoad` | 629 | `HalGrIdle`, `HalGrLoadChannelContext` |
| `CMiniport::SetVideoMode` | 613 | `MapToLinearD3DFORMAT`, `VideoBitsPerPixelOfD3DFORMAT` |

1. **State is lazy and the buffer is the interface.** Every `LazySet*` takes `_D3DDevice_MakeSpace` before it
   writes, and writes through `_XMETAL_Push*` — a command stream, not registers. `LazySetTransform` additionally
   composes (`MatrixProduct4x4`, `PushMatrix`), so a transform set is deferred and then multiplied into the
   buffer. `DbgRecordPushStart` in the setters is a debug-record hook: the shipping driver still carries the
   instrumentation the debug build used.
2. **There is a software path beside the hardware one.** `CMiniport::SoftwareMethod` (511 B) calls `GetTime`,
   `ServiceQueuedFlips`, `D3D::FixupPushBuffer` and `D3D::FlushWC` — the same command stream, built and flushed
   without the GPU consuming it. It exists in the retail image.
3. **The HAL is a real miniport.** `HalGrInit3d` (2,527 B), `HalGrControlLoad`/`HalGrLoadChannelContext` (channel
   context switching), `HalFifoContextSwitch` (509 B), `HalFifoAllocDMA` (397 B), `CreateTile` → `TilingUpdateIdle`
   (tile memory), and `ShutdownEngines` → `DestroyTile`, `ServiceFifoInterrupt`, `ServiceGrInterrupt`. Push-buffer
   FIFO, DMA allocation, tiles and interrupts — the pieces a driver owns and a game never sees.
4. **Surfaces are swapped by copying here.** `SwapCopy` (485 B) → `SwapSaveSurfaces`, `_D3DDevice_SetRenderTarget`,
   `_D3DDevice_SetTexture`; `CommonSetViewport` (485 B) → `GetViewportOffsetAndScale` + `_XMETAL_Push4f`/`Push2f`.
   Presentation is a copy the driver performs, which is why frame timing on this title is what it is under xemu.

**Why this is documentation and not a task.** Unlike UIX (present and unreachable) and the shader toolchain (used
by the title and required by a rebuild), the driver is **platform code the rebuild gets back by linking the same
D3D8 libraries**: the game calls the D3D8 API, the API is this driver, and on xemu the driver talks to an emulated
NV2A. Nothing here needs rewriting — but the *semantics* matter to anyone reasoning about the rebuilt build's
behaviour: state only reaches the GPU when the buffer is pushed, presentation is a copy, and the software path is
live in retail, so "the driver did it" is a possible explanation for a frame that looks wrong in an emulator.

## The C libraries: a heap, a FAT driver, a speech codec and two image codecs (2026-11-19)

The class readings leave one population untouched: **939 C-linkage functions**, the flat APIs inside `xapilib`,
`xvoice`, `d3dx8`, `libc` and the D3D entry points. They do not group by class, so the survey is a prefix census
over `inventory/xdk_lib_names.csv` — crude, and enough to see what the clusters are.

| library | C functions | bytes | what the largest bodies are |
|---|---:|---:|---|
| `xvoice` | 382 | 137,457 | `_SearchTrackTwoPulsesSearchSignsIn` (5,742), `_wmavencInit` (3,758), `_prvLSPQuantization` (3,304), `_prvExEBlockHypProcess` (2,953), `_prvExcDecSpeechFrame` (2,701) |
| `xapilib` | 205 | 31,953 | `_RtlpGrowBlockInPlace` (1,022), `_XapiMapLetterToDirectory` (820), `_XapiFormatFATVolumeEx` (804), `@OHCD_fProgramTransfer` (766) |
| `libc` | 90 | 18,114 | `__woutput` (1,956), `__output` (1,895), `___strgtold12` (1,095) |
| `d3dx8` | 89 | 19,471 | `_png_read_row` (935), `_D3DXCreateTextureFromFileInMemory` (924), `_png_do_expand` (854), `_png_build_gamma_table` (779) |

Four readings:

1. **`xapilib` is a filesystem and storage stack, not a shim.** A heap allocator (`_RtlpGrowBlockInPlace`,
   `_RtlpDeCommitFreeBlock`), a FAT formatter (`_XapiFormatFATVolumeEx`), drive-letter mapping
   (`_XapiMapLetterToDirectory`) and a USB host-controller driver (`OHCD`/`HCD`/`USBD`/`MU` prefixes — OHCI
   programming, memory units). The title's saves and its HDD layout run through this, which is why savegame
   formats in this project have been readable at all.
2. **`xvoice` is a speech codec, and the largest named C cluster in the image** (137 KB): an encoder
   (`_wmavencInit`), LSP quantisation (`_prvLSPQuantization`), excitation processing
   (`_prvExEBlockHypProcess`, `_prvExcDecSpeechFrame`) and a two-pulse search
   (`_SearchTrackTwoPulsesSearchSignsIn`, the single largest C body here at 5,742 B). Xbox Live voice chat is
   WMA-encoded, and this is the implementation.
3. **`d3dx8` embeds libpng and libjpeg** — 65 `png*` and 12 `jpeg*` symbols beside
   `_D3DXCreateTextureFromFileInMemory`. Textures can be loaded from PNG/JPEG at runtime, which is worth knowing
   before assuming every texture in the dump is a `.utx` DXT surface.
4. **`libc` is the CRT's formatting and float-parsing core** (`__output`, `__woutput`, `___strgtold12`) — the part
   that `printf`-style code in the game reaches.

**Bucket: platform — reuse.** All four clusters are code a rebuild links again from the same libraries, and none
of them is a reproduction task. The one conditional is `xvoice`: if a rebuild ships voice chat it links it, and if
it does not, the 137 KB is dead weight a stub can replace. That is the same decision UIX got, for the opposite
reason — UIX is unreachable from script; `xvoice` is reachable from anywhere with a socket.

**Limit.** The grouping is by symbol prefix, so a cluster can hide a second one (the `MU`/`USBD` split inside
xapilib is a guess from four and six names), and the byte totals are the body-match sizes rather than the
libs' own. It is a survey, not a reading; the four clusters that matter are named, and no conclusion here rests
on a prefix that the artifact does not show.

## Runtime sampling: what runs, and what samples cannot see (2026-11-19)

The static readings end with a population they cannot name: 7,819 `FUN_*` engine bodies with no in-image
evidence. `tools/xemu/eip-functions.py` is the first instrument that reads them at runtime — it boots the title
under xemu, samples `EIP` through QMP's monitor at 4–12 Hz, and maps each sample into
`symbols/xbox_functions.csv`.

**The harness, as measured** (xemu 0.8.139, debug flash, this desktop):

- The guest needs a **regular file** for its DVD: xemu's QEMU backend rejects a directory mount
  (`'file' driver requires ... to be a regular file`), so `tools/xemu/build-xiso.sh` has to build the 2.25 GB
  XISO first. The build also reports that this is a **debug-kit** XBE (media types zero), which is why the debug
  flash is required.
- From ssh the session is **KDE Wayland**: `XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1` launches
  xemu with GL up. `DISPLAY=:0` is refused and this SDL build has no `dummy` driver, so neither works headless.
- The AppImage spawns `AppRun` → `qemu-system` as children, so terminating the wrapper leaves a qemu holding
  `xbox_hdd.qcow2`; the next boot then dies with **`Failed to get "write" lock`** and no QMP socket. The tool now
  launches with `start_new_session=True` and kills the whole process group.

**Two runs, both after the title came up:**

| window | samples | distinct EIP | inside a named function | hottest |
|---|---:|---:|---:|---|
| 170 s → 230 s | 240 | 180 | 207 (86%) | `FUN_00463864` 26, `FUN_0022b0a0` 12, `Core_UnTemplate` 9, `FUN_002b5c20` 8, `_D3DDevice_SetStreamSource@12` 6 |
| 6 s → 102 s | 1,200 | 585 | 785 (65%) | `FUN_00463864` 62, `FUN_002b5c20` 38, `Core_UnTemplate` 36, `FUN_00229390` 30, `_D3DDevice_SetStreamSource@12` 29, `_D3DDevice_DrawIndexedVertices@12` 12, `D3DDrv_D3DRenderInterface` 12 |

Four things this establishes:

1. **The instrument works.** Two thirds to five sixths of samples land inside a named function, and the hot list is
   recognisable engine work — `Core_UnTemplate`, `Core_UnClass`, `Engine_UnRenderVisibility`, the D3D device calls.
   So the map is good enough to interpret a sample, which is the precondition for everything below.
2. **The hot `FUN_*` are now ranked by measurement rather than by guess.** `FUN_00463864` is the busiest unnamed
   body in both runs (5.2% of the boot window), then `FUN_002b5c20`, `FUN_00229390`, `FUN_002bc350`. That is the
   reading order for 4.9's engine half, and it costs one boot instead of a session of guessing.
3. **The shader prediction failed, informatively.** Neither `ShaderCompileVertex` nor `ShaderCompilePixel` nor
   `_XGAssembleShader@44` was sampled in either window, while rendering was demonstrably active
   (`_D3DDevice_DrawIndexedVertices`, `D3DDrv_D3DRenderInterface`, `?BlockOnTime@D3D@@`). If all 45 programs were
   compiled inside the sampled window and the work took ~0.5 s in total, 1,200 samples at 0.08 s would expect
   about six hits; zero hits means the compile is **not** in the boot window. It belongs to map load or first use,
   so a rebuild exercises the shader path when it loads a map, not when it starts — and "shaders compile at
   startup" is now retired from `docs/REBUILD.md`'s wording of the requirement.
4. **Sampling cannot see short work, and that is a property of the instrument.** One-shot init bodies (a few
   milliseconds), anything under ~80 ms at this rate, and code that runs before the first sample are all invisible.
   Confirming a one-shot needs a breakpoint or a much higher rate during a narrower window, not a longer run.

**What the residue means.** In the boot-window run 415 of 1,200 samples fell in no map row (35%). Some are
Ghidra-merged bodies and some are the `FUN_*` gaps the map itself marks; either way that fraction is a measure of
the map's runtime coverage, and it is the next thing to push down rather than a defect in the sampler.

## The four hottest unnamed bodies (2026-11-19)

The sampler ranked them; `tools/native-body-notes.py` reads them from the image (no library, so no
relocations — a byte scan for calls, import jumps and immediates, with out-of-section targets dropped because
inside an instruction's encoding an `E8` is not a call). None of these is named: the evidence says what they
*contain*, and a name would be a claim about behaviour that the bytes do not make.

| body | bytes | what the bytes show |
|---|---:|---|
| `FUN_00463864` | 909 | `55 8B EC 83 EC 54` prologue, then dense x87 (`D9`, `D8`, `DC`) and one call to `FUN_003e8542` (21 B) |
| `FUN_002b5c20` | 1,676 | `0F 31` (**RDTSC**) early in the body; calls `FUN_002af230` (2,711 B) twice plus 13–15-byte helpers |
| `FUN_00229390` | 7,428 | MSVC SEH prologue (`6A FF`, `64 A1 00 00 00 00`), a second `0F 31`, and repeated calls to `FUN_000574d0` (112 B), `FUN_002419e0` (229 B), `FUN_00055ed0` (37 B) |
| `FUN_002bc350` | 5,000 | D3D state calls: `_D3DDevice_SetRenderState_FillMode`, `_ZEnable`, `_CullMode`, and `@D3DDevice_SetRenderState_Simple` many times |

Four readings:

1. **`FUN_00463864` is floating-point work, and that is all that can be said.** 909 bytes of x87 with a single
   call is a numeric routine — the hot 5% during rendering fits per-vertex or per-matrix math, but the bytes do
   not say which, and the one callee is 21 bytes.
2. **Three of the four read the timestamp counter.** `0F 31` appears within the first 48 bytes of
   `FUN_002b5c20`, `FUN_00229390` and `FUN_002bc350` (and may appear later in the fourth, which the shown
   window does not cover), so the engine instruments timing in the hot path — worth knowing before treating the
   sampler's own numbers as pure workload.
3. **`FUN_00229390` is the largest body the map leaves unnamed** (7,428 B) and it has an SEH frame, so it is not a
   helper: something with a `__try` around a loop that calls three small functions repeatedly.
4. **`FUN_002bc350` is render-state setup**, and its callees make that certain rather than plausible: four
   distinct `SetRenderState_*` entry points in the first two hundred bytes.

**Why none is renamed.** The project's naming rule is that a name comes from evidence the build itself provides —
an assert string, a native table slot, a class registration, a library body. "It does x87 maths" is not that, and
`FUN_002bc350` being render-state setup narrows the field without distinguishing *which* state block it owns.
The bodies are documented, ranked and located; naming them stays open, and a breakpoint that captures their
arguments is the instrument that would close it.

## The shader compile, caught at a map load (2026-11-19)

The EIP sampler could not see the shader compile and the page above says why: it is one-shot work. A breakpoint
sees it, and now has. The run: `drive.py` with `--gdb 12345` boots the title and presses ten d-pad steps plus `A`
to load `M00`; `bp-args.py --wait-user 300 --break 0x2c01e0 --break 0x2c0910 --window 500` samples the CPU until
it is inside the image, arms, then holds the window open across the load.

**Three stops, all at the armed body:**

| stop | `this` (`ecx`/`esi`) | `edi` | return address | stack after the frame |
|---:|---|---:|---|---|
| 0 | `0x03cab900` | 1 | `0x002aedb7` | `0x02380000`, `0x02387948` |
| 1 | `0x03cab900` | 1 | `0x002aedb7` | same |
| 2 | `0x03cab400` | 4 | `0x002aedb7` | `0x02380000`, `0x023865d0`, `0x03334f80` |

**The caller is `FUN_002ae4e0`** (2,301 B) — `0x002aedb7` lands inside it — and that function is the one
`FUN_002b5c20`, the sampler's second-hottest body (and an RDTSC reader), calls. So the runtime chain is:

    FUN_002b5c20 (hot, timed)
      -> FUN_002ae4e0 (2,301 B)
        -> 41-byte per-program wrappers (FUN_002b3f60, FUN_002b3fd0, ...)
          -> ShaderCompileVertex (0x002c01e0)
            -> _XGAssembleShader@44 -> _D3DDevice_CreateVertexShader@16

which is the static chain from the shader reading, with the middle link named by a stop instead of inferred from a
`push`. The `this` pointers are **heap** addresses (`0x03cab9xx`, above every section), so the dispatcher is working
on a runtime object, not on static state, and `edi` varies (1, 1, 4) — a count or a shader-kind argument, not a
constant.

**Two corrections to the previous page.** `drive.py` already accepts `--gdb PORT` (`docs/XEMU.md` said it did not) —
what was missing was nobody having written the invocation down. And `drive.py` needs **X11**: with
`WAYLAND_DISPLAY` set, xemu renders Wayland-native and `drive.py`'s `xdotool` window check fails with
`no xemu window on :0`, which terminates the guest mid-run. The working environment is
`DISPLAY=:0 XAUTHORITY=/run/user/1000/xauth_*` with `WAYLAND_DISPLAY` unset, while the bare sampler is happy on
either.

**A longer run, with the assembler armed.** Fourteen stops (the run's cap) across three breakpoints: **10 at
`ShaderCompileVertex`, 2 at `ShaderCompilePixel`, 2 at `_XGAssembleShader@44`**. The assembler stops are the ones
that name their argument — `edx = 0x00524d18`, which the stack walk resolves to `xps.1.1`, with `0x5a` (90) beside
it as the program length, and the return address `0x002c09ff` inside `ShaderCompilePixel`. The vertex dispatcher's
stop carries `0x00524b00`, an `vs.1.1` program of 217 bytes. So the dispatcher passes (program pointer, length) to
the assembler, and both are readable off the frame.

**What this settles and what it does not.** Settled: the shaders are compiled during a map load, through
`FUN_002ae4e0` and the per-program wrappers, on a heap object; the assembler receives the program pointer and its
length; and a single map load reaches the vertex dispatcher at least ten times. Not settled: which of the 60
programs a map compiles in total — the cap truncated that — and what the `x` prefix means (it does not change the
dispatcher, and the assembler accepts the string as given). A `--stops 60` run would count the rest, and the
`x`-prefixed text itself is the evidence for what the prefix is: compare one against its un-prefixed twin.

## What this means for Phase 4

- The game modules are now 40/43 named from evidence; the rest are three functions with documented
bodies (plus `0x00318140`, outside the 43). "Zero unnamed functions in the game modules" is close, and
honest about the three.
- Two of the three shared helpers are named from their own strings (`appAssertFailed`,
  `appMallocBeforeInit`); `FUN_0003af60` stays `FUN_*` until its callers are named (PLAN 4.7). The
  remaining engine-side naming work is the `ProcessEvent`/property machinery (PLAN 4.3).
- The `ucpp` registration table doubles as a class inventory with layout sizes, which is what PLAN
  3.9/4.7 need to match script classes to their native copies.
