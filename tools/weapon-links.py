#!/usr/bin/env python3
"""Which class each weapon-tree class names: the default properties' class references (PLAN 8.3).

`docs/systems/WEAPONS.md` closed with three open items, and two of them are a class reference:

> * Effects wiring. `Sharpeffects` holds muzzle/impact emitters named after weapons
>   (`SE_3rd_P90MuzzleEmitter`, ...), but which emitter a weapon selects is a default property, not a class
>   relationship, so the graph cannot link them.
> * Ballistics and damage numbers. Those are class defaults inside the script bodies.

The first is a read once the defaults block is decoded (`tools/defaults-emit.py`): a weapon's emitters are
`class'...'` values in its block. The second is only half a value question -- which *property* carries the
damage, the projectile and the damage type is a reference graph, and that graph is what this tool measures.
The numbers themselves stay in `$WORK`: a class name is vocabulary this repository already publishes
(`inventory/classes.csv`, `inventory/vehicle_seats.csv`), a float is game data (AGENTS.md).

    python3 tools/weapon-links.py --system DUMP/System -o inventory/weapon_links.csv   # drive the emitter
    python3 tools/weapon-links.py --tree $WORK/defwep -o /tmp/links.csv               # an emitted tree
    python3 tools/weapon-links.py --tree $WORK/defwep --write-doc docs/systems/WEAPONS.md
    python3 tools/weapon-links.py --check                                             # synthetic, no dump

Columns: `package,class,property,declared,declared_type,target,ref,ref_package`.

* `property` is the tag the block sets, with any array index dropped; `class` is the class whose block sets it.
* `declared` is `<package>.<class>` of the class in the super chain that *declares* the property, and
  `declared_type` is its friendly type (`Class<Emitter>`) -- so an edge can be scored against what the
  property promises, not against its name.
* `target` is the referenced class's own tree, taken from its super chain: `weapon`, `ammo`, `damage`,
  `projectile`, `emitter` (anywhere under `Engine.Emitter`), or `other`. **Never from the property name:**
  `ProjectileClass` on an ammo class lands in the projectile tree, while `LowDetailEffect` on a damage type
  lands in the emitter tree, and `ImpactClass` lands wherever the target's chain says.
* `ref` is the class name the block names, without the package prefix; `ref_package` is the package that
  class actually lives in per `inventory/classes.csv`, or `no` when the name is not a class in this dump.

An array of class references is not emitted (`tools/defaults-emit.py` leaves arrays out unless they tile),
so this is the scalar half of the vocabulary; the tool says nothing about an array-typed class property
rather than counting it as unset.
"""
import argparse
import collections
import csv
import importlib.util
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["package", "class", "property", "declared", "declared_type", "target", "ref", "ref_package"]
DOC_MARK = ("<!-- generated:weapon-links -->", "<!-- /generated:weapon-links -->")
ROOTS = ("StargateWeapon", "StargateAmmo", "StargateDamageType", "StargateProjectile")
CLASS_REF = re.compile(r"^\s*(\w+)(?:\((\d+)\))?=\s*[cC]lass['\"]([^'\"]*)['\"]\s*$")


def read_table(name):
    path = os.path.join(ROOT, "inventory", name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def supers():
    return {r["class"]: r["super"] for r in read_table("classes.csv")}


def packages():
    return {r["class"]: r["package"] for r in read_table("classes.csv")}


def declared():
    """{(class, property): (kind, friendly_type)} -- only the class that declares it."""
    return {(r["class"], r["property"]): (r["kind"], r["friendly_type"]) for r in read_table("class_properties.csv")}


def ancestry(name, table):
    chain, seen = [], set()
    while name and name not in seen:
        seen.add(name)
        chain.append(name)
        name = table.get(name, "")
    return chain


def tree_classes(table):
    """Every class whose super chain passes through one of the four roots."""
    return {c for c in table if set(ancestry(c, table)) & set(ROOTS)}


def target_of(ref, table):
    """The referenced class's own tree, by super chain -- never by the property name."""
    chain = set(ancestry(ref, table))
    for root, label in (("StargateWeapon", "weapon"), ("StargateAmmo", "ammo"),
                        ("StargateDamageType", "damage"), ("StargateProjectile", "projectile"),
                        ("Emitter", "emitter")):
        if root in chain and (root != "Emitter" or ref != "Emitter"):
            return label
    return "other"


def declaring(class_name, prop, table, table_props, pkg_of):
    """`Pkg.Class` of the nearest ancestor that declares `prop`, and that property's friendly type."""
    for name in ancestry(class_name, table):
        got = table_props.get((name, prop))
        if got is not None:
            pkg = pkg_of.get(name, "")
            return ("%s.%s" % (pkg, name)) if pkg else name, got[1]
    return "", ""


def parse_block(text, package, cls, table, table_props, pkg_of):
    """Rows for one emitted `defaultproperties` block."""
    out = []
    for line in text.splitlines():
        hit = CLASS_REF.match(line)
        if not hit:
            continue
        prop, ref = hit.group(1), hit.group(3).split(".")[-1].strip()
        owner, ftype = declaring(cls, prop, table, table_props, pkg_of)
        out.append((package, cls, prop, owner, ftype,
                    target_of(ref, table) if ref in table else "unresolved", ref, pkg_of.get(ref, "no")))
    return out


def walk(tree, table, table_props, pkg_of):
    rows = []
    wanted = tree_classes(table)
    for package in sorted(os.listdir(tree)):
        directory = os.path.join(tree, package)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".defaultproperties.uc"):
                continue
            cls = name[: -len(".defaultproperties.uc")]
            if cls not in wanted:
                continue
            with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as fh:
                rows += parse_block(fh.read(), package, cls, table, table_props, pkg_of)
    return sorted(rows)


def render(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for row in rows:
        w.writerow(row)
    return buf.getvalue()


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [tuple(r[c] for c in COLUMNS) for r in csv.DictReader(fh)]


def chain(rows):
    """Weapon -> AmmoName -> ammo's ProjectileClass -> projectile's MyDamageType, counted per hop."""
    def one(cls, prop):
        return [r[6] for r in rows if r[1] == cls and r[2] == prop]

    weapons = {r[1] for r in rows if r[5] == "weapon"} | {r[1] for r in rows if r[2] in ("AmmoName", "MuzzleEmitter")}
    ammo_named, ammo_with_proj, proj_with_damage = 0, 0, 0
    for weapon in sorted(weapons):
        ammo = one(weapon, "AmmoName")
        if not ammo:
            continue
        ammo_named += 1
        proj = [p for a in ammo for p in one(a, "ProjectileClass")]
        if proj:
            ammo_with_proj += 1
            if any(one(p, "MyDamageType") for p in proj):
                proj_with_damage += 1
    return ammo_named, ammo_with_proj, proj_with_damage


def report(rows, table):
    classes = {(r[0], r[1]) for r in rows}
    trees = collections.Counter(r[5] for r in rows)
    print("ok   weapon-links: %d class reference(s) over %d class(es) of the %d in the four trees; %s"
          % (len(rows), len(classes), len(tree_classes(table)),
             ", ".join("%s %d" % (k, trees[k]) for k in sorted(trees))))
    unresolved = [r for r in rows if r[7] == "no"]
    ammo, proj, dmg = chain(rows)
    print("ok   weapon-links: %d weapon(s) name an ammo class, %d of those ammo classes name a projectile, "
          "%d of those projectiles name a damage type" % (ammo, proj, dmg))
    if unresolved:
        print("warn weapon-links: %d reference(s) name no class in inventory/classes.csv: %s"
              % (len(unresolved), ", ".join(sorted({r[6] for r in unresolved}))))
    return 0


def doc_block(rows, table):
    props = collections.Counter(r[2] for r in rows)
    trees = collections.Counter(r[5] for r in rows)
    classes = {(r[0], r[1]) for r in rows}
    total = len(tree_classes(table))
    unresolved = sorted({r[6] for r in rows if r[7] == "no"})
    lines = ["",
             "**%d class references over %d of the %d classes in the four trees** (%s). %s"
             % (len(rows), len(classes), total,
                ", ".join("%s %d" % (k, trees[k]) for k in sorted(trees)),
                ("Every one names a class this dump carries." if not unresolved else
                 "%d name no class in `inventory/classes.csv`: %s."
                 % (len(unresolved), ", ".join("`%s`" % u for u in unresolved)))),
             "",
             "| property | declared | edges | targets |",
             "|---|---|---:|---|"]
    for prop, count in sorted(props.items(), key=lambda kv: (-kv[1], kv[0])):
        declared_types = sorted({r[4] for r in rows if r[2] == prop and r[4]})
        targets = collections.Counter(r[5] for r in rows if r[2] == prop)
        lines.append("| `%s` | %s | %d | %s |"
                     % (prop, ", ".join("`%s`" % t for t in declared_types) or "—", count,
                        ", ".join("%s %d" % (k, targets[k]) for k in sorted(targets))))
    ammo, proj, dmg = chain(rows)
    lines += ["",
              "The firing chain is two hops of the same kind of value: **%d weapons name an ammo class, %d of "
              "those ammo classes name a projectile class, and %d of those projectiles name a damage type.** "
              "A weapon that names no ammo class spawns its shot in script -- `ProjectileClass` is not a "
              "`Weapon` property on this build at all." % (ammo, proj, dmg)]
    emitters = sorted({r[6] for r in rows if r[5] == "emitter"})
    muzzle = sorted({r[1] for r in rows if r[2] == "MuzzleEmitter"})
    impact = sorted({r[1] for r in rows if r[2] == "ImpactClass"})
    low = sorted({r[6] for r in rows if r[2] == "LowDetailEffect"})
    if not low:
        low_text = "none"
    elif len(low) == 1:
        low_text = "one class (`%s`), from %d row(s)" % (
            low[0], len([r for r in rows if r[2] == "LowDetailEffect"]))
    else:
        low_text = "%d classes (%s)" % (len(low), ", ".join("`%s`" % l for l in low[:6]))
    lines += ["",
              "**Effects wiring, the item this page listed as not measured:** %d distinct emitter classes are "
              "named, %d weapons name a muzzle emitter and %d a projectile impact emitter; the damage side's "
              "`LowDetailEffect` names %s." % (len(emitters), len(muzzle), len(impact), low_text)]
    return "\n".join(lines)


def replace_block(text, block):
    begin, end = DOC_MARK
    i, j = text.find(begin), text.find(end)
    if i < 0 or j <= i:
        sys.exit("document has no %s ... %s block" % DOC_MARK)
    return text[: i + len(begin)] + block + "\n" + text[j:]


def emit(rows, args, table):
    path = args.write_doc or args.check_doc
    if path:
        block = doc_block(rows, table)
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
        fresh = replace_block(current, block)
        if args.check_doc:
            if fresh != current:
                print("FAIL %s: the generated block is stale" % path)
                return 1
            print("ok   weapon-links-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   weapon-links-doc: wrote %s" % path)
    if args.out:
        with open(args.out, "w", newline="") as fh:
            fh.write(render(rows))
    elif not path:
        sys.stdout.write(render(rows))
    return report(rows, table)


def check():
    """Synthetic: no dump, nothing written to the tree."""
    table = {"FooWeapon": "StargateWeapon", "StargateWeapon": "Weapon", "Weapon": "Inventory",
             "FooAmmo": "StargateAmmo", "StargateAmmo": "Ammunition", "Ammunition": "Inventory",
             "FooProjectile": "StargateProjectile", "StargateProjectile": "Projectile",
             "Projectile": "Actor", "FooDamage": "StargateDamageType", "StargateDamageType": "DamageType",
             "Shockwave": "Emitter", "Emitter": "Actor", "Bystander": "Actor"}
    pkg_of = {"FooAmmo": "FakeContent", "FooProjectile": "FakeContent", "FooDamage": "FakeContent",
              "Shockwave": "FakeEffects", "Bystander": "FakeContent"}
    props = {("StargateWeapon", "AmmoName"): ("UClassProperty", "Class<Ammunition>"),
             ("StargateAmmo", "ProjectileClass"): ("UClassProperty", "Class<Projectile>"),
             ("StargateProjectile", "MyDamageType"): ("UClassProperty", "Class<DamageType>"),
             ("StargateWeapon", "MuzzleEmitter"): ("UClassProperty", "Class<Emitter>"),
             ("Engine.Weapon", "TracerProjectileClass"): ("UClassProperty", "Class<Actor>")}
    block = """// FakeContent.FooWeapon
defaultproperties
{
\tAmmoName=class'FakeContent.FooAmmo'
\tMuzzleEmitter=class'FakeEffects.Shockwave'
\tTracerProjectileClass=class'Ghost'
}
"""
    rows = parse_block(block, "FakeContent", "FooWeapon", table, props, pkg_of)
    got = [(r[2], r[3], r[4], r[5], r[7]) for r in rows]
    # `StargateWeapon` is not in the synthetic package table, so its attribution is the bare class name --
    # the tool does not invent a package for a class it was not given one for.
    want = [("AmmoName", "StargateWeapon", "Class<Ammunition>", "ammo", "FakeContent"),
            ("MuzzleEmitter", "StargateWeapon", "Class<Emitter>", "emitter", "FakeEffects"),
            ("TracerProjectileClass", "", "", "unresolved", "no")]
    if got != want:
        print("FAIL parse_block:\n got  %r\n want %r" % (got, want))
        return 1
    # A name that says what a class is must not decide it: `Shockwave` has no `Projectile` in its name and
    # `StargateProjectile` has no `Emitter`, so the chain is the only reading that separates them.
    if target_of("Shockwave", table) != "emitter" or target_of("StargateProjectile", table) != "projectile":
        print("FAIL target_of reads the name instead of the super chain")
        return 1
    if target_of("StargateProjectile", table) == target_of("Shockwave", table):
        print("FAIL target_of cannot tell an emitter from a projectile")
        return 1
    chained = [("FakeContent", "FooWeapon", "AmmoName", "", "", "ammo", "FooAmmo", "FakeContent"),
               ("FakeContent", "FooAmmo", "ProjectileClass", "", "", "projectile", "FooProjectile", "FakeContent"),
               ("FakeContent", "FooProjectile", "MyDamageType", "", "", "damage", "FooDamage", "FakeContent")]
    if chain(chained) != (1, 1, 1):
        print("FAIL chain: got %r, want (1, 1, 1)" % (chain(chained),))
        return 1
    if chain(chained[:1]) != (1, 0, 0):
        print("FAIL chain: a weapon whose ammo names no projectile must count as an unfinished chain")
        return 1
    doc = doc_block(rows, table)
    for needle in ("| `AmmoName` | `Class<Ammunition>` | 1 | ammo 1 |",
                   "**Effects wiring"):
        if needle not in doc:
            print("FAIL doc_block is missing %r:\n%s" % (needle, doc))
            return 1
    print("ok   weapon-links: a class reference is classified by its super chain, its declaring class is "
          "found through the chain, an unknown name is reported and the firing chain counts per hop")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory (drives defaults-emit into a temp tree)")
    ap.add_argument("--tree", help="an already emitted tree (defaults-emit --emit DIR)")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's table between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's table is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=os.path.join(ROOT, "inventory", "weapon_links.csv"),
                    help="with --check-doc/--write-doc and no tree: read this artifact instead")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump")
    args = ap.parse_args()
    if args.check:
        return check()
    table = supers()
    if (args.write_doc or args.check_doc) and not (args.tree or args.system):
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("weapon-links: %s holds no rows" % args.from_csv)
        return emit(rows, args, table)
    tree = args.tree
    if tree is None:
        if not args.system:
            sys.exit("weapon-links: pass --system <DUMP/System> or --tree <emitted dir>")
        spec = importlib.util.spec_from_file_location(
            "sg1_defaults_emit", os.path.join(ROOT, "tools", "defaults-emit.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tree = tempfile.mkdtemp(prefix="weapon-links-")
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            module.sweep(args.system, None, emit_dir=tree)
    rows = walk(tree, table, declared(), packages())
    if not rows:
        sys.exit("weapon-links: no class references under %s" % tree)
    return emit(rows, args, table)


if __name__ == "__main__":
    sys.exit(main())
