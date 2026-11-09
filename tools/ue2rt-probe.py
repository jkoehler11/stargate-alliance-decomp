#!/usr/bin/env python3
"""Probe a UE2 compiler against this build: what it emits, and whether it can read the dump's packages.

This answers one question and only one -- which compilers can consume version 130 / licensee 4 packages --
by running the compiler rather than reading about it. docs/COMPILER.md records what it measured.

    tools/ue2rt-probe.py --runtime ~/tools/ue2rt/x --work ~/sg1/work/ue2rt --dump ~/sg1/dump/Stargate_...-2005-12-15

The compiler is Epic's UE2 Runtime build 2226 (a 2003 engine runtime release, not game data): extract its
installer with `7z x` and pass the directory that holds `System/UCC.exe`. Nothing here downloads anything, and
nothing is written outside --work. The runtime copy is staged fresh each run, so the run is idempotent.

Stages:
  A  the compiler answers `help` under Wine, and reports its build number
  B  a scratch package compiles from source (this is the L1 input question, not a rebuild)
  C  the emitted package's header is read back: version and licensee
  D  the skip rule: an existing .u is not rebuilt, and removing it forces one
  E  with --dump: overlay the dump's packages and compile a class that extends a game class -- the answer to
     "can this compiler read this build" is whatever the linker does here
"""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys

VERSION_SIGNATURE = 0x9E2A83C1
PROBE_PACKAGE = "ProbePkg"
PROBE_CLASS = "ProbeThing"
PROBE_SOURCE = """class ProbeThing extends Object;

var int Probe;

function int Twice(int V)
{
    return V * 2;
}

defaultproperties
{
    Probe=7
}
"""


def log(msg):
    print(msg, flush=True)


def fail(msg):
    print("FAIL: " + msg, flush=True)
    return 1


def run_wine(env, cwd, args, timeout=300):
    return subprocess.run(["wine", "UCC.exe"] + args, cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=timeout)


def set_edit_packages(ini_path, packages):
    """Replace the EditPackages list inside [Editor.EditorEngine].

    The list must be inside that section: a line appended to the end of the file lands in whatever section is
    last and is silently ignored (measured; docs/COMPILER.md).
    """
    with open(ini_path, "rb") as handle:
        text = handle.read()
    text = re.sub(rb"EditPackages=[^\r\n]*\r?\n", b"", text)
    block = b"".join(b"EditPackages=%s\r\n" % name.encode() for name in packages)
    header = b"[Editor.EditorEngine]"
    if header not in text:
        raise SystemExit("no [Editor.EditorEngine] section in %s" % ini_path)
    text = text.replace(header, header + b"\r\n" + block.rstrip(b"\r\n"), 1)
    with open(ini_path, "wb") as handle:
        handle.write(text)


def read_header(path):
    with open(path, "rb") as handle:
        head = handle.read(8)
    if len(head) < 8:
        return None
    signature, version, licensee = struct.unpack("<IHH", head)
    return {"signature": signature, "version": version, "licensee": licensee,
            "size": os.path.getsize(path)}


def find_ini(system_dir):
    """ucc reads <exe>.ini; Default.ini is the base config and is not where EditPackages is read from."""
    candidate = os.path.join(system_dir, "UE2Runtime.ini")
    if os.path.exists(candidate):
        return candidate
    for name in sorted(os.listdir(system_dir)):
        if name.lower().endswith(".ini") and name.lower() != "default.ini":
            path = os.path.join(system_dir, name)
            with open(path, "rb") as handle:
                if b"EditPackages" in handle.read():
                    return path
    return None


def stage(runtime, work):
    stage_dir = os.path.join(work, "stage")
    if os.path.isdir(stage_dir):
        shutil.rmtree(stage_dir)
    shutil.copytree(runtime, stage_dir)
    return stage_dir


def make(env, system_dir):
    r = run_wine(env, system_dir, ["make", "-log"])
    out = (r.stdout or "") + (r.stderr or "")
    return out.replace("\r", ""), r.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", help="directory holding System/UCC.exe from a UE2 runtime release")
    parser.add_argument("--work", help="scratch directory; everything this tool writes lands here")
    parser.add_argument("--dump", help="the dump root (holds System/*.u); enables the read test")
    parser.add_argument("--dump-class", default="StargatePlayer", help="game class the probe extends")
    parser.add_argument("--check", action="store_true", help="validate the arguments only; no run")
    args = parser.parse_args()

    if not args.runtime or not os.path.exists(os.path.join(args.runtime, "System", "UCC.exe")):
        print("ue2rt-probe: --runtime pointing at a directory with System/UCC.exe is required "
              "(Epic's UE2 Runtime build 2226, unpacked locally; not shipped, not downloaded)", flush=True)
        return 2
    if args.check:
        print("ue2rt-probe: runtime present (%s)" % os.path.join(args.runtime, "System"), flush=True)
        return 0
    if not args.work:
        print("ue2rt-probe: --work is required", flush=True)
        return 2

    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    env = dict(os.environ, WINEDEBUG="-all", WINEPREFIX=os.path.join(work, "wineprefix"))
    os.makedirs(env["WINEPREFIX"], exist_ok=True)

    problems = 0

    # A -- does it run, and what is it
    runtime_system = os.path.join(args.runtime, "System")
    banner = run_wine(env, runtime_system, ["help"])
    text = ((banner.stdout or "") + (banner.stderr or "")).replace("\r", "")
    log("A. compiler runs: %s" % ("yes" if "UnrealOS execution environment" in text else "NO"))
    if "Rebuild UnrealScript packages" not in text:
        problems += fail("this UCC.exe does not offer `make`")
    build = None
    log_file = os.path.join(runtime_system, "UCC.log")
    if os.path.exists(log_file):
        with open(log_file, errors="replace") as handle:
            match = re.search(r"Init: Version: (\d+)", handle.read())
        build = match.group(1) if match else None
    log("   build: %s" % (build or "not reported"))

    # B -- a scratch package compiles
    stage_dir = stage(os.path.abspath(args.runtime), work)
    system_dir = os.path.join(stage_dir, "System")
    ini = find_ini(system_dir)
    if not ini:
        return fail("no ini with EditPackages in %s" % system_dir)
    log("   ini: %s" % os.path.relpath(ini, stage_dir))
    classes = os.path.join(stage_dir, PROBE_PACKAGE, "Classes")
    os.makedirs(classes, exist_ok=True)
    with open(os.path.join(classes, PROBE_CLASS + ".uc"), "w") as handle:
        handle.write(PROBE_SOURCE)
    set_edit_packages(ini, [PROBE_PACKAGE])

    out, _ = make(env, system_dir)
    compiled = "Compiling %s" % PROBE_CLASS in out
    log("B. scratch package compiles: %s" % ("yes" if compiled else "NO"))
    if not compiled:
        problems += fail("no compile line; output was:\n" + out.strip())

    # C -- what it emitted
    emitted = os.path.join(system_dir, PROBE_PACKAGE + ".u")
    header = read_header(emitted) if os.path.exists(emitted) else None
    if not header:
        problems += fail("no %s.u was written" % PROBE_PACKAGE)
    else:
        log("C. emitted: %s  signature 0x%08X  version %d  licensee %d  %d bytes"
            % (os.path.relpath(emitted, stage_dir), header["signature"], header["version"],
               header["licensee"], header["size"]))
        if header["signature"] != VERSION_SIGNATURE:
            problems += fail("package signature is not 0x%08X" % VERSION_SIGNATURE)

    # D -- the skip rule
    out, _ = make(env, system_dir)
    skipped = "Compiling %s" % PROBE_CLASS not in out
    log("D. existing .u is skipped, not rebuilt: %s" % ("yes" if skipped else "NO"))
    if os.path.exists(emitted):
        os.remove(emitted)
    out, _ = make(env, system_dir)
    rebuilt = "Compiling %s" % PROBE_CLASS in out
    log("   removing the .u forces a rebuild: %s" % ("yes" if rebuilt else "NO"))
    if not (skipped and rebuilt):
        problems += fail("the skip rule did not behave as documented")

    # E -- can it read this build
    if args.dump:
        dump_system = os.path.join(args.dump, "System")
        packages = sorted(p for p in os.listdir(dump_system) if p.lower().endswith(".u")) \
            if os.path.isdir(dump_system) else []
        if not packages:
            problems += fail("no packages under %s" % dump_system)
        else:
            for name in packages:
                shutil.copy2(os.path.join(dump_system, name), os.path.join(system_dir, name))
            with open(os.path.join(classes, PROBE_CLASS + ".uc"), "w") as handle:
                handle.write("class %s extends %s;\n\nvar int Probe;\n" % (PROBE_CLASS, args.dump_class))
            if os.path.exists(emitted):
                os.remove(emitted)
            out, _ = make(env, system_dir)
            read_ok = "Compiling %s" % PROBE_CLASS in out
            log("E. reads this build's packages (%d overlaid): %s" % (len(packages), "yes" if read_ok else "NO"))
            if not read_ok:
                first = next((line for line in out.splitlines() if line.strip()), "")
                history = next((line for line in out.splitlines() if "History:" in line), "")
                log("   linker said: %s" % first)
                if history:
                    log("   %s" % history[:200])
                versions = []
                for name in packages:
                    head = read_header(os.path.join(system_dir, name))
                    versions.append("%d/%d" % (head["version"], head["licensee"]))
                log("   versions in the overlay: %s" % sorted(set(versions)))

    log("")
    log("%s: %d problem(s)" % ("OK" if problems == 0 else "FAILED", problems))
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
