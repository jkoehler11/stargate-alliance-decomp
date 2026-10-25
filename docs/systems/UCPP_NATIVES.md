# ucpp natives: where they are, and where they are not

Six classes in the whole script tree declare ucpp natives — `SGOrder` (4), `Electrocutor` (2),
`FarClipChange` (2), `SGFontInfo` (1), `ShadowManager` (1), `StargateHUD` (1). PLAN 4.7/3.9 need the C++
behind those declarations. The obvious place to look is the class table, and the first result is a
negative that redirects the search.

    python3 tools/ucpp-vtables.py DUMP/default.xbe -o inventory

Reads the class table of each of the six from the image, resolves every slot against
`inventory/xbe_class_tables.csv`, `inventory/xbe_class_table_names.csv` and the symbol map, and counts how
many of the 492 tables share each target. Output: `inventory/ucpp_vtables.csv`.

## The vtable does not hold the natives

Every slot in all six tables resolves to executable code, but almost none of it is class-specific:

| target | tables sharing it | what it is |
|---|---:|---|
| `0x000171C0` | 2,297 slots | engine mechanism, shared by nearly every class |
| `0x00020F00` | 2,107 slots | second engine mechanism |
| slot 3 of each of the six | 1 (unique) | that class's constructor thunk |

The one non-engine slot that every class has is slot 3, and it is unique per class — which matches
`constructor_thunk_va` from the independently-built class-name table, so the two measurements agree.
Beyond that, four of the six classes have **no** class-specific slot at all: `SGOrder`, `FarClipChange`,
`SGFontInfo` and `StargateHUD` contribute only their constructor.

So a native function is *not* reachable by walking the vtable. In UE2 a `UFunction` object carries a
`Func` pointer to the native implementation, and that record lives in the class's function chain inside
the `.u` package, not in the class table. Reading it needs the package's export/property stream parsed —
which is why 4.7 is blocked on a `.u` reader (UELib under .NET, which this environment does not have, or a
Python function-object parser).

## Two classes do show native implementations

`Electrocutor` and `ShadowManager` have class-specific slots because their natives are *virtual* natives —
overrides of engine virtuals — and virtual overrides land in the table:

| class | slot | target | shared | first bytes |
|---|---:|---|---:|---|
| `Electrocutor` | 35 | `0x0018DB80` | 3 | `81ec00010000538b` |
| `Electrocutor` | 40 | `0x0018DA10` | 3 | `568bf18b066a016a` |
| `Electrocutor` | 51 | `0x0018DA70` | 2 | `578bf9f687100400` |
| `Electrocutor` | 70 | `0x001D9F20` | 3 | `8b44240c538b5c24` |
| `Electrocutor` | 123 | `0x0018E970` | 3 | `83ec3853558be98b` |
| `Electrocutor` | 124 | `0x0018D8A0` | 3 | `83ec1855568bf18b` |
| `Electrocutor` | 125 | `0x0018DAE0` | 3 | `8b919003000033c0` |
| `ShadowManager` | 35 | `0x0031E580` | 1 | `f30f10442404568b` |
| `StargateHUD` | 10 | `0x00210780` | 2 | `568bf18b86200400` |

Seven of `Electrocutor`'s slots cluster in `0x0018D8A0`–`0x0018E970`, and `shared=2..3` says those
addresses serve more than one class — so the cluster is a small shared effect/beam implementation, not one
class's private code. `ShadowManager`'s single slot at `0x0031E580` is shared only with itself, and its
first instruction reads a float from `[esp+4]` (`f3 0f 10 44 24 04` = `movss xmm0, [esp+4]`), which is a
plausible shadow-parameter setter.

## What this means for 4.7/4.9

1. **Virtual natives** (Electrocutor, ShadowManager, StargateHUD) can be located and reconstructed from the
   class table today — five of the six classes' C++ is at least partly identifiable.
2. **Non-virtual natives** (SGOrder's 4 of 10, SGFontInfo's 1, FarClipChange's 2) need the `UFunction`
   records from the packages. That is the actual blocker, and it is a `.u`-parsing task, not a Ghidra task.
3. `SGOrder` remains the interesting target for 4.9 precisely because 40% of it is native: its script half
   cannot run without the missing C++, so it is where a rebuild fails first.

## Not measured

The mapping from slot number to script function name. Slot order follows the function's declaration order in
the class, and the declaration list is in the script bodies under `$WORK` — which this repository does not
carry. The table here therefore says *where* the natives are, never *which function* each one is, except
where the shared-cluster behaviour lets a guess be stated as a guess.
