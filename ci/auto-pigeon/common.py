"""Shared helpers of the Auto-Pigeon fork's release scripts. Standard library only."""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
PLATFORMS = ("linux-amd64", "windows-amd64", "macos-amd64", "macos-arm64")
REQUIRED_PLATFORMS = ("linux-amd64", "windows-amd64")
PROGRAMS = ("qbsp", "vis", "light", "bspinfo", "bsputil", "maputil")
ASSET_PREFIX = "auto-pigeon-ericw-tools"
TAG_PREFIX = "auto-pigeon-ericw-v"


class Failure(Exception):
    """A gate that did not hold. Never caught to turn a failure into a pass."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def exe(platform, name):
    return name + ".exe" if platform.startswith("windows") else name


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def log(msg):
    print(msg, flush=True)


def run(argv, cwd=None, env=None, check=True, capture=False, timeout=None):
    """Run a native command, wait for it, and return (exit, output, seconds).

    Every caller either passes check=True (a non-zero exit raises) or reads the exit status
    itself; nothing here swallows one.
    """
    argv = [str(a) for a in argv]
    log("+ " + " ".join(('"%s"' % a) if " " in a else a for a in argv) + (("   (cwd %s)" % cwd) if cwd else ""))
    start = time.monotonic()
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        timeout=timeout,
    )
    seconds = round(time.monotonic() - start, 2)
    out = proc.stdout.decode("utf-8", "replace") if capture else ""
    if check and proc.returncode != 0:
        if capture:
            sys.stdout.write(out[-8000:])
        raise Failure("command failed with exit %d: %s" % (proc.returncode, " ".join(argv)))
    return proc.returncode, out, seconds


def host_platform():
    import platform as _p

    machine = _p.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    system = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}.get(_p.system(), _p.system().lower())
    return "%s-%s" % (system, arch)


def require_native(platform):
    """A target is only ever built and accepted on its own operating system and CPU."""
    host = host_platform()
    if host != platform:
        raise Failure("this host is %s; refusing to stand in for %s" % (host, platform))
    if platform.startswith("macos"):
        # Rosetta reports the translated architecture to uname; ask the kernel.
        code, out, _ = run(["sysctl", "-n", "sysctl.proc_translated"], check=False, capture=True)
        if code == 0 and out.strip() == "1":
            raise Failure("this process is translated by Rosetta; that is not a native %s host" % platform)
    return host
