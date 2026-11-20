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
**45 programs — 19 `vs.1.1`, 9 `ps.1.1`, 17 `ps.1.4`** (`inventory/shader_programs.csv`,
`make shader-programs`); an earlier count of 63 in this page was a substring count that included `#define`
lines *containing* the token, and the artifact is the authority.

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
optimiser) or bakes the 45 programs' microcode once and ships it. **And all 45 are used.** Each program is referenced by an immediate whose value is its address: 41 by a
`push` inside a tiny per-program wrapper — 15 wrappers of 41 bytes for the `vs.1.1` programs, 26 of 28 bytes
for the pixel ones — and 4 `vs.1.1` programs by `mov` immediates inside one function at `0x002b4600`. Every
push wrapper then calls one of two dispatchers, and the split follows the program's type exactly:

| dispatcher | programs | calls | now named |
|---|---:|---|---|
| `0x002c01e0` | 15 `vs.1.1` | `_XGAssembleShader@44`, `_D3DDevice_CreateVertexShader@16` | `ShaderCompileVertex` |
| `0x002c0910` | 26 `ps.1.1`/`ps.1.4` | `_XGAssembleShader@44`, `_D3DDevice_CreatePixelShader@8`, plus `_CreateFileA`/`_ReadFile`/`_CloseHandle` and `_GlobalMemoryStatus` | `ShaderCompilePixel` |

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

Five readings, **303 methods, 34,328 bytes**: the DirectSound API object (59), the MCPX APU driver (44),
the voice path under it (74), Xbox Live voice chat (104) and the positional layer (22) —
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

### What this reading does not say

- **Callers.** `inventory/module_export_census.csv` indexes UE2 exports, and **0 of these 303 addresses has
  a row in it** — so which engine or game body calls `_DirectSoundCreate` first, and whether the game's own
  device is a `CDirectSound` at all, is not measured here. The next instrument is a caller census on the
  four `_DirectSound*` entry points, not another class read.
- **Ordering.** Every claim above is a call edge. The APU's register writes are inside bodies that have not
  been decompiled, so "the ISR runs before the DPC" is the SDK's structure, not this build's timing.
- **The media decoders are next, not here.** `CWaveFile` (11), `CQueueXMO` (18) and `XHawkMediaObject` (13)
  — RIFF/AIFF parsing, the XMO timestamp queue and the USB media object — read 42 more methods; they were
  measured with the same tool and left out of this bundle.

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

## What this means for Phase 4

- The game modules are now 40/43 named from evidence; the rest are three functions with documented
bodies (plus `0x00318140`, outside the 43). "Zero unnamed functions in the game modules" is close, and
honest about the three.
- Two of the three shared helpers are named from their own strings (`appAssertFailed`,
  `appMallocBeforeInit`); `FUN_0003af60` stays `FUN_*` until its callers are named (PLAN 4.7). The
  remaining engine-side naming work is the `ProcessEvent`/property machinery (PLAN 4.3).
- The `ucpp` registration table doubles as a class inventory with layout sizes, which is what PLAN
  3.9/4.7 need to match script classes to their native copies.
