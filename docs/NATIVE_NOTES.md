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

## What this means for Phase 4

- The game modules are now 40/43 named from evidence; the rest are three functions with documented
bodies (plus `0x00318140`, outside the 43). "Zero unnamed functions in the game modules" is close, and
honest about the three.
- Two of the three shared helpers are named from their own strings (`appAssertFailed`,
  `appMallocBeforeInit`); `FUN_0003af60` stays `FUN_*` until its callers are named (PLAN 4.7). The
  remaining engine-side naming work is the `ProcessEvent`/property machinery (PLAN 4.3).
- The `ucpp` registration table doubles as a class inventory with layout sizes, which is what PLAN
  3.9/4.7 need to match script classes to their native copies.
