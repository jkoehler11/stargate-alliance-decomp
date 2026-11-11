#!/usr/bin/env python3
"""Which seat fires which weapon: the `DriverWeapons`/`PassengerWeapons` arrays, decoded (PLAN 3.10).

`docs/systems/VEHICLES.md` closed with a negative: *"Seat and turret wiring: `VehicleTurret` (6 classes) and
`StargateVehicleSeat` (5) exist and are saved, but which seat index maps to which turret is configuration, not
visible in the class graph."* The configuration is in the class's **default property block**, and the two
properties are arrays of structs:

    StargateGame,StargateVehicle,DriverWeapons,UArrayProperty,...,array<DriverWeaponStruct>
    StargateGame,StargateVehicle,PassengerWeapons,UArrayProperty,...,array<PassengerWeaponStruct>

`tools/defaults-emit.py` decodes those blocks and its array path tiles a tagged struct per element exactly, so
the wiring is a read rather than a guess. This tool reads the emitted blocks and turns each array into rows.

    python3 tools/vehicle-seats.py --system DUMP/System -o inventory/vehicle_seats.csv   # drive the emitter
    python3 tools/vehicle-seats.py --tree $WORK/defveh -o /tmp/seats.csv                 # an emitted tree
    python3 tools/vehicle-seats.py --tree $WORK/defveh --write-doc docs/systems/VEHICLES.md
    python3 tools/vehicle-seats.py --check                                               # synthetic, no dump

Columns: `package,class,slot,index,pawn,attach,role`.

* `slot` is `driver` or `passenger`, and the **field name is not the same in the two structs**: a
  `DriverWeaponStruct` carries `WeaponClass`, a `PassengerWeaponStruct` carries `WeaponPawnClass`. Both are
  read into `pawn`; a name in the cross-package `Package.Class` form is kept whole because it says which tree
  the pawn belongs to.
* `index` is the array index, i.e. the seat number the game assigns in order.
* `attach` is `named` when the slot names an attachment bone and `none` when it does not -- **the bone's
  name is a property value, so it is game data and stays out of the artifact** (AGENTS.md). What the flag
  carries is that a seat is placed by bone, not by index, and that two slots can name the same bone.
* `role` is derived from the **class tree**, not from the name: a pawn whose super chain passes through
  `VehicleTurret` is a `turret`, one that passes through `StargateVehicleSeat` is a `seat` (a ride, not a
  gun), `None` is `empty`, and a driver row whose value is not a pawn is `weapon`. `inventory/classes.csv`
  supplies the supers, so the classification is checkable rather than a naming heuristic, and the same chain
  decides which concrete vehicles the summary names as having no slot row at all.

An array that is not set is an empty array, not an unknown: the property is declared once on `StargateVehicle`
(and again on `StargateWeaponEmplacement`), so a class that sets no value inherits no seats and the tool's
summary names it rather than printing a row for it.
"""
import argparse
import csv
import importlib.util
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["package", "class", "slot", "index", "pawn", "attach", "role"]
DOC_MARK = ("<!-- generated:vehicle-seats -->", "<!-- /generated:vehicle-seats -->")
SLOT = re.compile(r"^\s*(Driver|Passenger)Weapons\((\d+)\)=\((.*)\)\s*$")
FIELD = re.compile(r"(\w+)=([^,()]*)")
REF = re.compile(r"^Class'(.*)'$")


def clean_ref(value):
    """`Class'HummveeMachineGun'` -> `HummveeMachineGun`; a bare name is kept as it is."""
    value = value.strip()
    hit = REF.match(value)
    return hit.group(1) if hit else value


def supers(path=os.path.join(ROOT, "inventory", "classes.csv")):
    """{class: super} from the committed class table (no dump)."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["class"]] = row["super"]
    return out


def role_for(pawn, slot, table):
    """turret / seat / empty / weapon, from the pawn's own super chain."""
    seen, name = set(), (pawn or "").split(".")[-1].strip()
    if not name or name == "None":
        return "empty"
    while name and name not in seen:
        seen.add(name)
        if name == "VehicleTurret":
            return "turret"
        if name == "StargateVehicleSeat":
            return "seat"
        name = table.get(name, "")
    return "weapon" if slot == "driver" else "seat_pawn"


def parse_block(text, package, cls, table):
    """[(package, class, slot, index, pawn, bone, role)] from one emitted defaultproperties block."""
    out, seen = [], {}
    for line in text.splitlines():
        hit = SLOT.match(line)
        if not hit:
            continue
        slot, index = hit.group(1).lower(), int(hit.group(2))
        fields = {k: clean_ref(v) for k, v in FIELD.findall(hit.group(3))}
        pawn = fields.get("WeaponPawnClass", fields.get("WeaponClass", ""))
        # a bone name is a property value, i.e. game data under AGENTS.md: the artifact records only whether
        # the slot names an attach point, and the name never leaves the block.
        bone = "named" if fields.get("WeaponBone", "None") not in ("", "None") else "none"
        key = (slot, index)
        if key in seen:
            sys.exit("%s.%s: %s slot %d appears twice" % (package, cls, slot, index))
        seen[key] = True
        out.append((package, cls, slot, index, pawn, bone, role_for(pawn, slot, table)))
    return out


def walk(tree, table):
    """Every `Pkg/Class.defaultproperties.uc` in an emitted tree, in package/class order."""
    rows = []
    for pkg in sorted(os.listdir(tree)):
        directory = os.path.join(tree, pkg)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".defaultproperties.uc"):
                continue
            cls = name[: -len(".defaultproperties.uc")]
            with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as fh:
                rows += parse_block(fh.read(), pkg, cls, table)
    return sorted(rows, key=lambda r: (r[0], r[1], r[2], r[3]))


def render(rows):
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for row in rows:
        w.writerow(row)
    return buf.getvalue()


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [(r["package"], r["class"], r["slot"], int(r["index"]), r["pawn"], r.get("attach") or r["bone"], r["role"])
                for r in csv.DictReader(fh)]


def report(rows, table):
    classes = sorted({(r[0], r[1]) for r in rows})
    roles = {}
    for row in rows:
        roles[row[6]] = roles.get(row[6], 0) + 1
    print("ok   vehicle-seats: %d slot row(s) over %d class(es); %s"
          % (len(rows), len(classes),
             ", ".join("%s %d" % (k, roles[k]) for k in sorted(roles))))
    return 0


def doc_block(rows, vehicles):
    counts = {}
    for row in rows:
        counts[row[6]] = counts.get(row[6], 0) + 1
    lines = ["",
             "**%d slots over %d classes:** %s."
             % (len(rows), len({(r[0], r[1]) for r in rows}),
                ", ".join("%s %d" % (k, counts[k]) for k in sorted(counts))),
             "",
             "| class | slot | # | pawn | attach | role |",
             "|---|---|---:|---|---|---|"]
    for _pkg, cls, slot, index, pawn, bone, role in rows:
        lines.append("| `%s` | %s | %d | %s | %s | %s |"
                     % (cls, slot, index, ("`%s`" % pawn) if pawn and pawn != "None" else "—", bone, role))
    if vehicles:
        lines += ["", "Concrete vehicles under `StargateVehicle` with no slot row at all — an unset array is an "
                      "empty array, and the property is declared once on the base: %s."
                  % ", ".join("`%s`" % v for v in vehicles)]
    return "\n".join(lines)


def replace_block(text, block):
    begin, end = DOC_MARK
    i, j = text.find(begin), text.find(end)
    if i < 0 or j <= i:
        sys.exit("document has no %s ... %s block" % DOC_MARK)
    return text[: i + len(begin)] + block + "\n" + text[j:]


def emit(rows, args, vehicles):
    path = args.write_doc or args.check_doc
    if path:
        block = doc_block(rows, vehicles)
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
        fresh = replace_block(current, block)
        if args.check_doc:
            if fresh != current:
                print("FAIL %s: the generated block is stale" % path)
                return 1
            print("ok   vehicle-seats-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   vehicle-seats-doc: wrote %s" % path)
    if args.out:
        with open(args.out, "w", newline="") as fh:
            fh.write(render(rows))
    elif not path:
        sys.stdout.write(render(rows))
    return report(rows, None)


FORK_BASES = ("StargateVehicle", "StargateCar", "StargateHover", "StargateCopter")


def vehicles_without_slots(rows, table):
    """Concrete vehicles under `StargateVehicle` that emit no slot row at all.

    The list comes from `inventory/classes.csv`'s super chains, not from `inventory/class_graph.csv`: that
    artifact is regenerated per root and currently carries the objectives graph, so reading it listed 32
    objective classes as vehicles. Seats, turret mounts and emplacements are not vehicles, and the same chain
    test `role_for` uses skips them here.
    """
    if not table:
        return []
    named = {row[1] for row in rows}
    out = []
    for cls in sorted(table):
        chain, name, seen = [], cls, set()
        while name and name not in seen:
            seen.add(name)
            chain.append(name)
            name = table.get(name, "")
        if "StargateVehicle" not in chain or cls in FORK_BASES:
            continue
        if "VehicleTurret" in chain or "StargateVehicleSeat" in chain:
            continue
        if cls not in named:
            out.append(cls)
    return out

def check():
    """Synthetic: no dump, nothing written to the tree."""
    table = {"HummveeMachineGun": "VehicleTurret", "VehicleTurret": "StargateVehicle",
             "HummveeInteriorSeat": "StargateVehicleSeat", "StargateVehicleSeat": "StargateVehicle",
             "StargateVehicle": "SVehicle"}
    block = """// StargateVehicles.TestVehicle
defaultproperties
{
\tPassengerWeapons(0)=(WeaponPawnClass=Class'HummveeMachineGun',WeaponBone=Turret)
\tPassengerWeapons(1)=(WeaponPawnClass=Class'HummveeInteriorSeat',WeaponBone=veh_hummer)
\tPassengerWeapons(2)=(WeaponPawnClass=None,WeaponBone=None)
\tDriverWeapons(0)=(WeaponClass=Class'StargateGame.StaffCannonGun',WeaponBone=turret_pivot)
\tbTurretActive=True
}
"""
    got = parse_block(block, "StargateVehicles", "TestVehicle", table)
    want = [("StargateVehicles", "TestVehicle", "passenger", 0, "HummveeMachineGun", "named", "turret"),
            ("StargateVehicles", "TestVehicle", "passenger", 1, "HummveeInteriorSeat", "named", "seat"),
            ("StargateVehicles", "TestVehicle", "passenger", 2, "None", "none", "empty"),
            ("StargateVehicles", "TestVehicle", "driver", 0, "StargateGame.StaffCannonGun",
             "named", "weapon")]
    if got != want:
        print("FAIL parse_block:\n got  %r\n want %r" % (got, want))
        return 1
    dup = block.replace("\tPassengerWeapons(1)=", "\tPassengerWeapons(0)=(WeaponPawnClass=None,WeaponBone=None)\n\tPassengerWeapons(1)=")
    try:
        parse_block(dup, "StargateVehicles", "TestVehicle", table)
    except SystemExit:
        pass
    else:
        print("FAIL a slot index declared twice was accepted")
        return 1
    if role_for("StargateGame.None", "driver", table) != "empty":
        print("FAIL a None value is not 'empty'")
        return 1
    doc = doc_block(got, ["Jeep"])
    for needle in ("| `TestVehicle` | passenger | 1 | `HummveeInteriorSeat` | named | seat |",
                   "`Jeep`"):
        if needle not in doc:
            print("FAIL doc_block is missing %r:\n%s" % (needle, doc))
            return 1
    tree = {"StargateVehicle": "SVehicle", "StargateCar": "StargateVehicle",
            "StargateHover": "StargateVehicle", "StargateCopter": "StargateVehicle",
            "HoverCraft": "StargateHover", "DeathGlider": "StargateVehicle", "Hummvee": "StargateCar",
            "HummveeMachineGun": "VehicleTurret", "VehicleTurret": "StargateVehicle",
            "HummveeInteriorSeat": "StargateVehicleSeat", "StargateVehicleSeat": "StargateVehicle",
            "SVehicle": "Vehicle"}
    silent = vehicles_without_slots(got, tree)
    if silent != ["DeathGlider", "HoverCraft", "Hummvee"]:
        print("FAIL vehicles_without_slots: got %r, want ['DeathGlider', 'HoverCraft', 'Hummvee']" % (silent,))
        return 1
    print("ok   vehicle-seats: the two structs' field names both parse, a seat pawn is told from a turret by "
          "its super chain, a duplicate index and a None value are refused")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory (drives defaults-emit into a temp tree)")
    ap.add_argument("--tree", help="an already emitted tree (defaults-emit --emit DIR)")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's table between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's table is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=os.path.join(ROOT, "inventory", "vehicle_seats.csv"),
                    help="with --check-doc/--write-doc and no tree: read this artifact instead")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump")
    args = ap.parse_args()
    table = supers()

    if args.check:
        return check()
    if (args.write_doc or args.check_doc) and not (args.tree or args.system):
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("vehicle-seats: %s holds no rows" % args.from_csv)
        return emit(rows, args, vehicles_without_slots(rows, table))
    tmp = None
    tree = args.tree
    if tree is None:
        if not args.system:
            sys.exit("vehicle-seats: pass --system <DUMP/System> or --tree <emitted dir>")
        spec = importlib.util.spec_from_file_location(
            "sg1_defaults_emit", os.path.join(ROOT, "tools", "defaults-emit.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tmp = tempfile.mkdtemp(prefix="vehicle-seats-")
        tree = tmp
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            module.sweep(args.system, None, emit_dir=tree)
    rows = walk(tree, table)
    if not rows:
        sys.exit("vehicle-seats: no slot arrays under %s" % tree)
    return emit(rows, args, vehicles_without_slots(rows, table))


if __name__ == "__main__":
    sys.exit(main())
