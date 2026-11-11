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

A reference is a **setting**, not a value: a weapon that sets no `AmmoName` in its own block still carries one
when an ancestor sets it. `--effective-out` resolves the same graph through the super chain and writes one row per
weapon in the tree, which is the number a rebuild needs:

    python3 tools/weapon-links.py --system DUMP/System -o inventory/weapon_links.csv \
        --effective-out inventory/weapon_effective.csv --write-doc docs/systems/WEAPONS.md

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
DOC_MARK_EFF = ("<!-- generated:weapon-effective -->", "<!-- /generated:weapon-effective -->")
EFF_COLUMNS = ["package", "class", "ammo", "ammo_set_by", "projectile", "projectile_set_by",
               "damage", "damage_via", "damage_set_by"]
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


def setmap(rows):
    """{(class, property): ref} -- what each class's *own* block sets."""
    out = {}
    for r in rows:
        if r[5] != "unresolved" and r[6]:
            out[(r[1], r[2])] = r[6]
    return out


def resolve(rows_map, cls, prop, table):
    """Effective value of `prop` on `cls`, and the class whose own block sets it.

    A block is a setting, so the value a class *has* is the nearest one on its super chain -- nine weapons
    carry an `AmmoName` they never set, which is the distinction this function exists for.
    """
    for name in ancestry(cls, table):
        ref = rows_map.get((name, prop))
        if ref:
            return ref, name
    return "", ""


def effective_rows(rows, table):
    """One row per weapon in the tree: the bullet, the shot, and the damage type it ends at."""
    rows_map = setmap(rows)
    pkg_of = packages()
    out = []
    for cls in sorted(c for c in tree_classes(table) if "StargateWeapon" in ancestry(c, table)):
        ammo, ammo_by = resolve(rows_map, cls, "AmmoName", table)
        proj = proj_by = dmg = dmg_by = ""
        via = ""
        if ammo:
            proj, proj_by = resolve(rows_map, ammo, "ProjectileClass", table)
            if proj:
                dmg, dmg_by = resolve(rows_map, proj, "MyDamageType", table)
                via = "projectile" if dmg else ""
            if not dmg:
                # `Engine.Ammunition.MyDamageType` is applied to the projectile it spawns, so an ammo class
                # that names a damage type but no projectile (a hitscan) still has one.
                dmg, dmg_by = resolve(rows_map, ammo, "MyDamageType", table)
                via = "ammo" if dmg else ""
        out.append((pkg_of.get(cls, ""), cls, ammo, ammo_by, proj, proj_by, dmg, via, dmg_by))
    return out


def eff_summary(rows, table):
    eff = effective_rows(rows, table)
    def names(sel):
        return sorted(r[1] for r in eff if sel(r))
    return {
        "weapons": len(eff),
        "sets_ammo": names(lambda r: r[2] and r[3] == r[1]),
        "inherits_ammo": names(lambda r: r[2] and r[3] != r[1]),
        "no_ammo": names(lambda r: not r[2]),
        "has_ammo": names(lambda r: r[2]),
        "has_projectile": names(lambda r: r[4]),
        "projectile_set": names(lambda r: r[4] and r[5] == r[2]),
        "projectile_inherited": names(lambda r: r[4] and r[5] != r[2]),
        "has_damage": names(lambda r: r[6]),
        "damage_from_projectile": names(lambda r: r[7] == "projectile"),
        "damage_from_ammo": names(lambda r: r[7] == "ammo"),
        "no_damage": names(lambda r: not r[6]),
        "ammo_classes": sorted({r[2] for r in eff if r[2]}),
        "damage_classes": sorted({r[6] for r in eff if r[6]}),
    }


def report(rows, table):
    classes = {(r[0], r[1]) for r in rows}
    trees = collections.Counter(r[5] for r in rows)
    print("ok   weapon-links: %d class reference(s) over %d class(es) of the %d in the four trees; %s"
          % (len(rows), len(classes), len(tree_classes(table)),
             ", ".join("%s %d" % (k, trees[k]) for k in sorted(trees))))
    unresolved = [r for r in rows if r[7] == "no"]
    eff = eff_summary(rows, table)
    print("ok   weapon-links: %d weapon(s): %d carry an ammo class (%d set it, %d inherit it), %d carry none"
          % (eff["weapons"], len(eff["has_ammo"]), len(eff["sets_ammo"]), len(eff["inherits_ammo"]),
             len(eff["no_ammo"])))
    print("ok   weapon-links: %d reach a projectile class (%d set on the ammo, %d inherited), %d reach a damage "
          "type (%d from the projectile, %d from the ammo), %d reach none"
          % (len(eff["has_projectile"]), len(eff["projectile_set"]), len(eff["projectile_inherited"]),
             len(eff["has_damage"]), len(eff["damage_from_projectile"]), len(eff["damage_from_ammo"]),
             len(eff["no_damage"])))
    print("ok   weapon-links: %d distinct ammo class(es) and %d distinct damage class(es) over %d weapon(s)"
          % (len(eff["ammo_classes"]), len(eff["damage_classes"]), eff["weapons"]))
    if eff["no_ammo"]:
        print("ok   weapon-links: no ammo class at all: %s" % ", ".join(eff["no_ammo"]))
    if eff["no_damage"]:
        print("ok   weapon-links: no damage type reached: %s" % ", ".join(eff["no_damage"]))
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
    eff = eff_summary(rows, table)
    lines += ["",
              "A reference here is a **setting, not the value a class carries**: %d weapons set an ammo class in "
              "their own block, but **%d of the %d weapons in the tree end up with one** (%d inherit theirs) and "
              "%d carry none (%s). `ProjectileClass` is not a `Weapon` property on this build at all -- a weapon "
              "names a bullet and the bullet names the shot -- so the resolved chain is the next block's "
              "measurement, and this table is the settings it starts from."
              % (len([r for r in rows if r[2] == "AmmoName"]), len(eff["has_ammo"]), eff["weapons"],
                 len(eff["inherits_ammo"]), len(eff["no_ammo"]),
                 ", ".join("`%s`" % n for n in eff["no_ammo"]) or "none")]
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


def doc_block_eff(rows, table):
    """The resolved chain: what each weapon ends up firing, not what its own block sets."""
    eff = effective_rows(rows, table)
    s = eff_summary(rows, table)
    total = s["weapons"]
    lines = ["",
             "**%d weapons, resolved through their class chains** (`inventory/weapon_effective.csv`, one row per "
             "weapon, names only): **%d carry an ammo class** -- %d set it in their own block and %d inherit it -- "
             "and **%d carry none** (%s). %d of the %d reach a projectile class and %d reach a damage type."
             % (total, len(s["has_ammo"]), len(s["sets_ammo"]), len(s["inherits_ammo"]), len(s["no_ammo"]),
                ", ".join("`%s`" % n for n in s["no_ammo"]) or "none",
                len(s["has_projectile"]), total, len(s["has_damage"])),
             "",
             "| hop | weapons |",
             "|---|---:|",
             "| sets an ammo class in its own block | %d |" % len(s["sets_ammo"]),
             "| carries an ammo class (set or inherited) | %d |" % len(s["has_ammo"]),
             "| carries a projectile class | %d |" % len(s["has_projectile"]),
             "| damage type from the projectile's `MyDamageType` | %d |" % len(s["damage_from_projectile"]),
             "| damage type from the ammo's `MyDamageType` | %d |" % len(s["damage_from_ammo"]),
             "| resolves a damage type | %d |" % len(s["has_damage"])]
    if s["ammo_classes"]:
        counts = collections.Counter(r[2] for r in eff if r[2])
        top, n = counts.most_common(1)[0]
        lines += ["",
                  "The %d weapons that carry a bullet share **%d ammo classes** (the most shared, `%s`, is named by "
                  "%d weapons) and reach **%d damage classes**." % (len(s["has_ammo"]), len(s["ammo_classes"]), top,
                                                                   n, len(s["damage_classes"]))]
    if s["no_damage"]:
        lines += ["", "%d weapon(s) reach no damage type at all: %s."
                  % (len(s["no_damage"]), ", ".join("`%s`" % n for n in s["no_damage"]))]
    return "\n".join(lines)


def replace_block(text, block, mark=DOC_MARK):
    begin, end = mark
    i, j = text.find(begin), text.find(end)
    if i < 0 or j <= i:
        sys.exit("document has no %s ... %s block" % mark)
    return text[: i + len(begin)] + block + "\n" + text[j:]


def render_eff(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(EFF_COLUMNS)
    for row in rows:
        w.writerow(row)
    return buf.getvalue()


def emit(rows, args, table):
    path = args.write_doc or args.check_doc
    effective = effective_rows(rows, table)
    if path:
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
        fresh = replace_block(current, doc_block(rows, table), DOC_MARK)
        fresh = replace_block(fresh, doc_block_eff(rows, table), DOC_MARK_EFF)
        if args.check_doc:
            if fresh != current:
                print("FAIL %s: a generated block is stale" % path)
                return 1
            print("ok   weapon-links-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   weapon-links-doc: wrote %s" % path)
    if args.effective_out:
        with open(args.effective_out, "w", newline="") as fh:
            fh.write(render_eff(effective))
        print("ok   weapon-links: wrote %s (%d row(s))" % (args.effective_out, len(effective)))
    elif args.check_doc:
        # The artifact and the page are two claims about one measurement; a page checked against a stale CSV
        # would agree with itself and disagree with the dump.
        committed = os.path.join(ROOT, "inventory", "weapon_effective.csv")
        if os.path.exists(committed):
            with open(committed, encoding="utf-8") as fh:
                if fh.read() != render_eff(effective):
                    print("FAIL %s: the effective table is stale" % committed)
                    return 1
            print("ok   weapon-links: %s matches the reference graph" % committed)
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
    # A setting is not a value: `ChildWeapon` sets no `AmmoName` and `ChildAmmo` no `ProjectileClass`, and
    # `BigProjectile` inherits its damage type from `FooProjectile` -- each hop has to be resolved through the
    # chain or a rebuild reads three of these four weapons as carrying nothing.
    tree = {"FooWeapon": "StargateWeapon", "ChildWeapon": "FooWeapon", "BareWeapon": "StargateWeapon",
            "StargateWeapon": "Weapon", "Weapon": "Inventory", "HitscanWeapon": "StargateWeapon",
            "FooAmmo": "StargateAmmo", "ChildAmmo": "FooAmmo", "HitscanAmmo": "StargateAmmo",
            "StargateAmmo": "Ammunition", "Ammunition": "Inventory", "FooProjectile": "StargateProjectile",
            "BigProjectile": "FooProjectile", "StargateProjectile": "Projectile", "Projectile": "Actor",
            "FooDamage": "StargateDamageType", "BarDamage": "StargateDamageType",
            "StargateDamageType": "DamageType"}
    chained = [("FakeContent", "FooWeapon", "AmmoName", "", "", "ammo", "ChildAmmo", "FakeContent"),
               ("FakeContent", "HitscanWeapon", "AmmoName", "", "", "ammo", "HitscanAmmo", "FakeContent"),
               ("FakeContent", "FooAmmo", "ProjectileClass", "", "", "projectile", "BigProjectile",
                "FakeContent"),
               ("FakeContent", "FooProjectile", "MyDamageType", "", "", "damage", "FooDamage", "FakeContent"),
               ("FakeContent", "HitscanAmmo", "MyDamageType", "", "", "damage", "BarDamage", "FakeContent")]
    got = {r[1]: r[2:] for r in effective_rows(chained, tree)}
    want = {"BareWeapon": ("", "", "", "", "", "", ""),
            "ChildWeapon": ("ChildAmmo", "FooWeapon", "BigProjectile", "FooAmmo", "FooDamage", "projectile",
                            "FooProjectile"),
            "FooWeapon": ("ChildAmmo", "FooWeapon", "BigProjectile", "FooAmmo", "FooDamage", "projectile",
                          "FooProjectile"),
            "HitscanWeapon": ("HitscanAmmo", "HitscanWeapon", "", "", "BarDamage", "ammo", "HitscanAmmo"),
            # the root is one of the tree's weapons and carries nothing, as `StargateWeapon` does in the dump
            "StargateWeapon": ("", "", "", "", "", "", "")}
    if got != want:
        print("FAIL effective_rows:\n got  %r\n want %r" % (got, want))
        return 1
    summary = eff_summary(chained, tree)
    if summary["weapons"] != 5:
        print("FAIL eff_summary[weapons]: got %r, want 5" % (summary["weapons"],))
        return 1
    for key, count in (("sets_ammo", 2), ("inherits_ammo", 1), ("no_ammo", 2),
                       ("has_projectile", 2), ("damage_from_projectile", 2), ("damage_from_ammo", 1),
                       ("no_damage", 2)):
        if len(summary[key]) != count:
            print("FAIL eff_summary[%s]: got %d, want %d" % (key, len(summary[key]), count))
            return 1
    block_eff = doc_block_eff(chained, tree)
    for needle in ("| carries an ammo class (set or inherited) | 3 |",
                   "| damage type from the ammo's `MyDamageType` | 1 |", "`BareWeapon`"):
        if needle not in block_eff:
            print("FAIL doc_block_eff is missing %r:\n%s" % (needle, block_eff))
            return 1
    doc = doc_block(rows, table)
    for needle in ("| `AmmoName` | `Class<Ammunition>` | 1 | ammo 1 |",
                   "**Effects wiring"):
        if needle not in doc:
            print("FAIL doc_block is missing %r:\n%s" % (needle, doc))
            return 1
    print("ok   weapon-links: a class reference is classified by its super chain, its declaring class is "
          "found through the chain, an unknown name is reported, and each firing-chain hop resolves through "
          "the class chain rather than the class's own block")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory (drives defaults-emit into a temp tree)")
    ap.add_argument("--tree", help="an already emitted tree (defaults-emit --emit DIR)")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--effective-out", metavar="PATH",
                    help="write the resolved one-row-per-weapon table here (inventory/weapon_effective.csv)")
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
