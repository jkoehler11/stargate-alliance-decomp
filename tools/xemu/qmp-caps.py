#!/usr/bin/env python3
"""Ask xemu's own QMP what it implements (PLAN 7.3, the frame source).

`drive.py` reads frames through X because xemu is not stock QEMU: its QMP is a reimplementation and the graphics
commands QEMU has are the ones most likely to be missing. That is a fact about a build, not about the project, so
it is measured rather than remembered:

    python3 tools/xemu/qmp-caps.py --sock ~/sg1/work/hudq1/try1/qmp.sock
    python3 tools/xemu/qmp-caps.py --launch --out /tmp/qmpcaps --bios ~/nfs/sg1-dump/bios

Measured 2026-11-09 on xemu 0.8.139: **228 commands, and `screendump` is not one of them** -- `CommandNotFound`
for `{"filename": …}`, for the QEMU 5 `{"filename": …, "format": "png"}` shape and for a `device` argument alike.
So the frame source question has an answer, and it is not `screendump`: a run that needs a frame has to come from
the X window, and a run whose verdict is a guest read has to survive without one (`drive.py --soft-shots`).

    python3 tools/xemu/qmp-caps.py --sock PATH --has screendump   # exit 0 present, 2 absent, 3 no connection
    python3 tools/xemu/qmp-caps.py --launch ... --call screendump the call's own reply, verbatim

`--selftest` starts a fake QMP socket that answers exactly as this build does (no `screendump`), so the tool's
verdicts are checked in `make ci` without an emulator or a dump.
"""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import smoke  # noqa: E402  -- the same QMP dialect drive.py and smoke.py speak

#: The commands a harness cares about. Anything else in the list is context.
WANTED = ("screendump", "query-status", "human-monitor-command", "pmemsave", "send-key", "device_add",
          "dump-guest-memory")


def caps(sock_path, deadline=40.0, command=None, arguments=None):
    """`{"connected": bool, "commands": [...], "call": reply}` from one QMP session.

    A missing socket is a reading (`connected: False`), not an exception: the caller's question is what this
    emulator answers, and an emulator that never opened its socket answers nothing.
    """
    sock = smoke.qmp_connect(sock_path, time.time() + deadline)
    if sock is None:
        return {"connected": False, "commands": [], "call": None}
    cmd = smoke.qmp_session(sock)
    reply = cmd("query-commands") or {}
    names = sorted(entry.get("name", "") for entry in (reply.get("return") or []))
    out = {"connected": True, "commands": names, "call": None}
    if command:
        out["call"] = cmd(command, arguments or {})
    return out


def launch_probe(args):
    """Start xemu for a few seconds with a QMP socket, ask it, and kill it.

    The TOML deliberately mirrors `drive.py`'s: a config path xemu cannot parse comes up as an emulator with no
    QMP socket, which reads exactly like a QMP with no `screendump`.
    """
    out = os.path.abspath(args.out)
    os.makedirs(os.path.join(out, "xdg", "xemu", "xemu"), exist_ok=True)
    toml = os.path.join(out, "xemu.toml")
    sock_path = os.path.join(out, "qmp.sock")
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    mcpx = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                 if f.lower().startswith("mcpx")), None)
    flash = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                  if f.lower().endswith(".bin") and "debug" in f.lower()), None)
    if not mcpx or not flash:
        return "FAIL: need mcpx*.bin and a debug flash in %s" % args.bios, 3
    with open(toml, "w") as f:
        f.write("[general]\nshow_welcome = false\n[sys]\nmem_limit = '128'\n")
        f.write("[sys.files]\nbootrom_path = '%s'\nflashrom_path = '%s'\n" % (mcpx, flash))
        for key, path in (("hdd_path", args.hdd), ("dvd_path", args.iso)):
            if path:
                f.write("%s = '%s'\n" % (key, path))
    env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1", XDG_DATA_HOME=os.path.join(out, "xdg"))
    log = open(os.path.join(out, "xemu.log"), "wb")
    proc = subprocess.Popen([args.xemu, "-config_path", toml, "-qmp", "unix:%s,server,nowait" % sock_path],
                            stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    try:
        got = caps(sock_path, deadline=time.time() + args.seconds, command=args.call,
                   arguments={"filename": os.path.join(out, "cap.ppm")} if args.call else None)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    return got, proc.returncode


def report(got, wanted=None):
    """Print the census the way a human reads it: the count first, then the commands this harness would use."""
    wanted = wanted or WANTED
    if not got.get("connected"):
        print("qmp: no socket -- the emulator never opened one, which answers nothing")
        return
    names = got.get("commands") or []
    print("qmp: %d command(s)" % len(names))
    for name in wanted:
        print("  %-22s %s" % (name, "present" if name in names else "ABSENT"))
    if got.get("call") is not None:
        print("call: %s" % json.dumps(got["call"], sort_keys=True))


def selftest():
    """A fake QMP that answers like this build: a command list with no `screendump`, and its refusal."""
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, ok, detail))

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "qmp.sock")
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(path)
        srv.listen(4)
        names = ("query-status", "human-monitor-command", "pmemsave", "send-key", "device_add")

        def serve():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                f = conn.makefile("rwb")
                f.write(json.dumps({"QMP": {"version": {"qemu": "xemu"}, "capabilities": []}}).encode() + b"\n")
                f.flush()
                for line in f:
                    try:
                        msg = json.loads(line.decode())
                    except ValueError:
                        break
                    execute = msg.get("execute")
                    if execute == "query-commands":
                        reply = {"return": [{"name": n} for n in names]}
                    elif execute == "screendump":
                        reply = {"error": {"class": "CommandNotFound",
                                           "desc": "The command screendump has not been found"}}
                    else:
                        reply = {"return": {}}
                    f.write(json.dumps(reply).encode() + b"\n")
                    f.flush()
                conn.close()

        threading.Thread(target=serve, daemon=True).start()
        got = caps(path, 10.0)
        case("a socket that answers gives its command list", got["connected"] and "pmemsave" in got["commands"],
             str(got.get("commands")))
        case("screendump is absent from that list", "screendump" not in got["commands"])
        call = caps(path, 10.0, command="screendump", arguments={"filename": os.path.join(tmp, "x.ppm")})
        case("a missing command is refused by name",
             call["call"].get("error", {}).get("class") == "CommandNotFound", json.dumps(call["call"]))
        case("the refusal is what 'absent' means", "screendump has not been found"
             in call["call"].get("error", {}).get("desc", ""))
        missing = caps(os.path.join(tmp, "absent.sock"), 1.0)
        case("no socket is a reading, not an exception", missing == {"connected": False, "commands": [], "call": None})
        srv.close()

    bad = [c for c in cases if not c[1]]
    for name, ok, detail in cases:
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, ": " + detail if detail else ""))
    print("%s: %d case(s), %d failed" % ("ok  " if not bad else "FAIL", len(cases), len(bad)))
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sock", help="a QMP unix socket that is already up (a run's --out holds one)")
    ap.add_argument("--launch", action="store_true", help="start xemu for --seconds, probe it, kill it")
    ap.add_argument("--selftest", action="store_true", help="a fake QMP socket, no emulator and no dump")
    ap.add_argument("--out", default=os.path.expanduser("~/sg1/work/qmpcaps"), help="--launch's scratch directory")
    ap.add_argument("--bios", default=os.environ.get("SG1_BIOS", os.path.expanduser("~/nfs/sg1-dump/bios")))
    ap.add_argument("--hdd", default="", help="HDD image for --launch (optional: QMP comes up without a guest)")
    ap.add_argument("--iso", default="", help="disc image for --launch (optional)")
    ap.add_argument("--xemu", default=os.environ.get("XEMU", smoke.DEFAULT_XEMU))
    ap.add_argument("--seconds", type=float, default=40.0, help="how long --launch waits for the socket")
    ap.add_argument("--has", metavar="NAME", help="exit 0 when NAME is implemented, 2 when it is not")
    ap.add_argument("--call", metavar="NAME", help="also send NAME and print its reply (a filename is passed)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.sock and not args.launch:
        ap.error("give --sock, --launch or --selftest")

    if args.launch:
        got, _rc = launch_probe(args)
    else:
        got = caps(args.sock, deadline=time.time() + 40.0, command=args.call,
                   arguments={"filename": os.path.join("/tmp", "qmpcap.ppm")} if args.call else None)
    report(got)
    if not got.get("connected"):
        return 3
    if args.has:
        return 0 if args.has in got.get("commands", []) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
