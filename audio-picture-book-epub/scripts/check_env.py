#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe the environment this skill relies on, and say what still works without
each missing piece.

Run this BEFORE building. Nothing here is installed automatically: report the
findings to the user, explain the consequence of each gap, and let them decide.

Exit codes:
    0  everything is available
    2  only optional pieces are missing - the book can still be built
    1  a required piece is missing - no book can be produced

Usage:
    python check_env.py
    python check_env.py --json      # machine-readable, for an agent to parse
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import shutil
import subprocess
import sys

MIN_PYTHON = (3, 8)
SYSTEM = platform.system()          # 'Windows' | 'Darwin' | 'Linux'
IS_WIN = SYSTEM == "Windows"


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------
def run(cmd, timeout=25):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, shell=False)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception:
        return None, ""


def check_python():
    ok = sys.version_info >= MIN_PYTHON
    return {
        "id": "python",
        "label": "Python",
        "required": True,
        "ok": ok,
        "detail": platform.python_version(),
        "hint": "Install Python %d.%d or newer from https://python.org"
                % MIN_PYTHON,
    }


def check_pillow():
    try:
        import PIL
        version = getattr(PIL, "__version__", "?")
        return {"id": "pillow", "label": "Pillow (image library)",
                "required": True, "ok": True, "detail": version,
                "hint": pip_install("Pillow")}
    except Exception:
        return {"id": "pillow", "label": "Pillow (image library)",
                "required": True, "ok": False, "detail": "not installed",
                "hint": pip_install("Pillow")}


def check_java():
    exe = shutil.which("java")
    if not exe:
        return {"id": "java", "label": "Java runtime (JRE 11+)",
                "required": False, "ok": False, "detail": "java not on PATH",
                "hint": install_hint("java")}
    code, out = run([exe, "-version"])
    first = out.splitlines()[0] if out else "?"
    return {"id": "java", "label": "Java runtime (JRE 11+)",
            "required": False, "ok": code == 0, "detail": first,
            "hint": install_hint("java")}


def find_epubcheck():
    """Return (kind, value) where kind is 'jar', 'script' or None."""
    env = os.environ.get("EPUBCHECK_JAR")
    if env and os.path.isfile(env):
        return "jar", env

    exe = shutil.which("epubcheck")
    if exe:
        return "script", exe

    patterns = [
        os.path.join(os.path.expanduser("~"), "epubcheck*", "**", "epubcheck.jar"),
        os.path.join(os.path.expanduser("~"), ".local", "epubcheck*", "**",
                     "epubcheck.jar"),
        "/usr/local/opt/epubcheck/**/epubcheck.jar",
        "/opt/homebrew/opt/epubcheck/**/epubcheck.jar",
        "C:/epubcheck*/**/epubcheck.jar",
        "C:/Program Files/epubcheck*/**/epubcheck.jar",
        os.path.join(os.path.expanduser("~"), "Downloads", "epubcheck*", "**",
                     "epubcheck.jar"),
    ]
    for pat in patterns:
        for hit in glob.glob(pat, recursive=True):
            if os.path.isfile(hit):
                return "jar", hit
    return None, None


def check_epubcheck():
    kind, value = find_epubcheck()
    if kind:
        return {"id": "epubcheck", "label": "epubcheck (official validator)",
                "required": False, "ok": True,
                "detail": "%s (%s)" % (value, kind),
                "hint": install_hint("epubcheck")}
    return {"id": "epubcheck", "label": "epubcheck (official validator)",
            "required": False, "ok": False,
            "detail": "not found on PATH, in EPUBCHECK_JAR, or in usual install "
                      "locations",
            "hint": install_hint("epubcheck")}


# ---------------------------------------------------------------------------
# install guidance
# ---------------------------------------------------------------------------
def pip_install(pkg):
    py = "python" if IS_WIN else "python3"
    return "%s -m pip install %s" % (py, pkg)


def install_hint(what):
    if what == "java":
        if IS_WIN:
            return "winget install Microsoft.OpenJDK.17"
        if SYSTEM == "Darwin":
            return "brew install openjdk@17"
        return "sudo apt install default-jre    # or: sudo dnf install java-17-openjdk"
    if what == "epubcheck":
        base = "download from https://github.com/w3c/epubcheck/releases"
        if SYSTEM == "Darwin":
            return "brew install epubcheck    # or " + base
        return base + " and unzip it (Java is required)"
    return ""


# ---------------------------------------------------------------------------
# capability matrix
# ---------------------------------------------------------------------------
CAPABILITIES = [
    ("build", "Build the EPUB",
     ["python", "pillow"],
     "Without Python 3.8+ and Pillow the book cannot be produced at all. "
     "Nothing else in this skill can run."),
    ("selfcheck", "Run this project's own structural validator",
     ["python"],
     "Needs only Python; no third-party packages. Checks mimetype placement, "
     "manifest/spine integrity, the autoplay ban and controller presence, and "
     "the known Apple Books regression guards."),
    ("epubcheck", "Run the official epubcheck validator",
     ["python", "java", "epubcheck"],
     "Without Java and epubcheck you can still build a complete book and run "
     "the self-check, but nothing gives an authoritative verdict. epubcheck is "
     "the only tool that catches spec violations this project does not encode "
     "- so treat the result as 'not officially verified', and do not tell the "
     "user the file passed validation."),
]


def evaluate(checks):
    have = {c["id"]: c["ok"] for c in checks}
    rows = []
    for key, label, needs, consequence in CAPABILITIES:
        missing = [n for n in needs if not have.get(n)]
        rows.append({
            "id": key,
            "label": label,
            "ok": not missing,
            "needs": needs,
            "missing": missing,
            "if_missing": consequence,
        })
    return rows


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def render(checks, caps):
    lines = []
    lines.append("Environment check")
    lines.append("=" * 66)
    for c in checks:
        mark = "OK     " if c["ok"] else ("MISSING" if c["required"] else "absent ")
        tag = "required" if c["required"] else "optional"
        lines.append("  [%s] %-30s %-8s  %s"
                     % (mark, c["label"], tag, c["detail"]))
        if not c["ok"] and c["hint"]:
            lines.append("            install: %s" % c["hint"])

    lines.append("")
    lines.append("What you can do with this environment")
    lines.append("-" * 66)
    for row in caps:
        lines.append("  [%s] %s" % ("x" if row["ok"] else " ", row["label"]))
        if not row["ok"]:
            lines.append("        needs: %s" % ", ".join(row["missing"]))
            lines.append("        impact: %s" % row["if_missing"])

    blocked = not caps[0]["ok"]
    degraded = [r for r in caps if not r["ok"]]
    lines.append("")
    lines.append("=" * 66)
    if blocked:
        lines.append("VERDICT: cannot build. Install the required pieces above "
                     "first.")
    elif degraded:
        lines.append("VERDICT: can build. Optional pieces are missing - see the "
                     "impact notes above.")
    else:
        lines.append("VERDICT: fully equipped, including official validation.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true",
                    help="emit a machine-readable report")
    args = ap.parse_args(argv)

    checks = [check_python(), check_pillow(), check_java(), check_epubcheck()]
    caps = evaluate(checks)

    if args.json:
        print(json.dumps({"platform": SYSTEM,
                          "python": platform.python_version(),
                          "checks": checks,
                          "capabilities": caps},
                         ensure_ascii=False, indent=2))
    else:
        print(render(checks, caps))

    if not caps[0]["ok"]:
        return 1
    if any(not c["ok"] for c in caps):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
