#!/usr/bin/env python3
"""Verify every command in docs/VERO-8800-API.md against the live service.

The doc is a claim about what the service answers.  This script is the
evidence: it re-runs every read-only command in it against the DEPLOYED
service on 8800 and checks the answer matches what the doc says.

Writes are NOT run against 8800 -- 8800 drives a real device.  The write
claims were measured on a simulator instance (--simulate --port 8807);
this script starts its own simulator to re-check them.

Usage: python scripts/verify_api_doc.py
Exit 0 = every checked claim held.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
#: Where this script lives, and the root above it.
BUNDLE_ROOT = os.path.dirname(HERE)
#: Where the Vero PROJECT lives.  The verifier needs the project's own
#: tree to start a simulator for the write checks; point it at your
#: checkout with VERO_PROJECT_ROOT.  Left unset, the project is assumed to
#: be the tree this script was run from.
ROOT = os.environ.get("VERO_PROJECT_ROOT", BUNDLE_ROOT)


def _find_doc():
    """The Chinese quick-reference, wherever it lives.

    Two layouts are supported, because this script is BOTH a project
    script and a shipped file:

      * in the project tree:  <project>/docs/VERO-8800-API.md
      * in the bundle:        <bundle>/references/VERO-8800-API.md

    The bundle layout is tried FIRST, so an unpacked bundle checks its own
    copy -- which is the one the recipient is reading -- and the project
    layout is the fallback.
    """
    for cand in (os.path.join(BUNDLE_ROOT, "references", "VERO-8800-API.md"),
                 os.path.join(ROOT, "docs", "VERO-8800-API.md")):
        if os.path.exists(cand):
            return cand
    raise SystemExit("cannot find VERO-8800-API.md: looked in %s and %s"
                     % (os.path.join(BUNDLE_ROOT, "references"),
                        os.path.join(ROOT, "docs")))


DOC = _find_doc()

#: WHERE THE LIVE SERVICE IS.  A LAN address is private data -- the
#: project's own privacy gate (scripts/verify_no_private_data.py) refuses
#: any private-LAN address in the working tree or in any committed blob,
#: and the git history is frozen against new ones.  So the address is NOT
#: written into this file: it comes from the environment, and the default
#: is loopback.
#:
#:   VERO_LIVE_HOST=192.168.x.y .venv/bin/python3 scripts/verify_api_doc.py
#:
#: Point it at the deployed service; loopback is right when the service
#: runs on this machine.
LIVE_HOST = os.environ.get("VERO_LIVE_HOST", "127.0.0.1")
LIVE_PORT = int(os.environ.get("VERO_LIVE_PORT", "8800"))
LIVE = "http://%s:%d" % (LIVE_HOST, LIVE_PORT)
SIM_PORT = 8811
SIM = "http://127.0.0.1:%d" % SIM_PORT

#: THE INTERPRETER THAT RUNS THE SIMULATOR.  It was hardcoded as
#: ".venv/bin/python3", which exists on Linux and on macOS and NOT on
#: Windows -- where the venv layout is Scripts/python.exe.  A hardcoded
#: POSIX path is how the write checks (which start a simulator) would
#: have failed on the recipient's machine, and they are the checks that
#: make this verifier worth running.
#:
#: The order matters. The SYSTEM interpreter comes first: it is always
#: present and it is what is running this script. The venv is only used
#: when it exists AND carries the dependencies (fastapi, uvicorn), because
#: a venv without them cannot start the service either.
def _venv_candidates():
    """Interpreters that could start the service, best first."""
    out = []
    if os.name == "nt":
        out.append(os.path.join(ROOT, ".venv", "Scripts", "python.exe"))
    else:
        out.append(os.path.join(ROOT, ".venv", "bin", "python3"))
        out.append(os.path.join(ROOT, ".venv", "bin", "python"))
    out.append(sys.executable)
    return out


def _pick_python():
    """The interpreter that can start the service, or None."""
    for cand in _venv_candidates():
        if not os.path.exists(cand):
            continue
        if os.path.normcase(os.path.abspath(cand)) == \
                os.path.normcase(os.path.abspath(sys.executable)):
            # The interpreter running THIS script. It is the fallback, so
            # it is taken without testing -- it obviously can run itself.
            return cand
        r = subprocess.run(
            [cand, "-c", "import fastapi, uvicorn"],
            capture_output=True, text=True)
        if r.returncode == 0:
            return cand
    # The running interpreter is the fallback even if it lacks the
    # packages: the simulator then fails with a clear ImportError rather
    # than the verifier dying with a confusing one.
    return sys.executable


_VENV_PYTHON = _pick_python()

#: THE EXPECTED SIZE OF THE SURFACE.  One place, so the check's label
#: and its condition cannot disagree -- they DID once, and a reader
#: would have trusted the label.
#:
#: Measured on the deployed service: 50 paths, 52 routes.  The
#: families: 18 under /api/v1/, 27 under /api/ (the UI family), 5
#: serving the page itself.
EXPECT_PATHS = 50
EXPECT_ROUTES = 52
EXPECT_V1_PATHS = 18
EXPECT_UI_PATHS = 27

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", name,
                           ("  <- " + detail) if (detail and not ok) else ""))


def call(method, url, body=None, t=20):
    """Return (status, parsed-body-or-raw-text).

    The body is a dict whenever the server answered JSON, and a plain
    string otherwise (a non-JSON error page).  `as_dict` below narrows it
    so the checks never subscript a string by accident.
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=t) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        return "ERR", "%s: %s" % (type(e).__name__, e)


def as_dict(body):
    """The body as a dict, or {} when it is not one."""
    return body if isinstance(body, dict) else {}


def status_only(url, t=3):
    """The HTTP status of a URL, reading nothing but the status line.

    A readiness probe must not depend on the BODY being a particular
    format.  `call` JSON-parses its answer, and the root page is HTML, so
    a readiness probe built on `call` never sees 200 -- it sees a parse
    error.  That is exactly what happened here: the check reported "the
    simulator did not come up" on an instance that was up and answering.
    """
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=t) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return "ERR"


def wait_for(url, seconds=30):
    """Wait until a URL answers with a status, and return whether it did."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        c = status_only(url, t=3)
        if c == 200:
            return True
        time.sleep(0.5)
    return False


def main():
    doc = open(DOC, encoding="utf-8").read()

    print("\n== 1. the doc exists and every route it names is real ==")
    check("docs/VERO-8800-API.md exists", os.path.exists(DOC))
    code, spec_raw = call("GET", LIVE + "/api/v1/openapi.json", t=30)
    check("live openapi.json is 200", code == 200, str(code))
    spec = as_dict(spec_raw)
    live_paths = set(spec.get("paths", {}))
    M = ("get", "post", "put", "delete", "patch")
    live_routes = {(m.upper(), p) for p, v in spec.get("paths", {}).items()
                    for m in v if m in M}
    # The numbers live in a constant, not inline, so the label above and
    # the condition here cannot drift apart.  They DID drift once: the
    # label was updated to 49/51 when the video routes were removed and
    # the condition stayed at 73/75, so the check failed while its own
    # name said the right thing -- and a reader would have looked at the
    # label and believed it.
    check("live surface is %d paths / %d routes" % (EXPECT_PATHS, EXPECT_ROUTES),
          len(live_paths) == EXPECT_PATHS and len(live_routes) == EXPECT_ROUTES,
          "%d paths / %d routes" % (len(live_paths), len(live_routes)))

    # Every path the doc names, in its tables, must exist live.
    #
    # The doc writes the video routes with the placeholder the doc ITSELF
    # explains -- "VID is the id a library entry carries" -- while the live
    # spec writes `{video_id}`.  So the two are reconciled through the
    # placeholder name, not by string equality: the doc's `VID` and the
    # spec's `{video_id}` are the same slot.
    #
    # `/api/...` and `/api/v1/...` are the doc's own FAMILY markers in
    # prose, not routes; they are excluded explicitly rather than by a
    # regex that would also swallow a real path.
    PLACEHOLDERS = {"VID": "video_id", "NAME": "name", "N": "percent"}
    named = set(re.findall(r"`(/api/[A-Za-z0-9_/{}.\-]+)`", doc))
    named = {n.split("?")[0] for n in named}
    named.discard("/api/...")
    named.discard("/api/v1/...")
    missing = []
    for n in sorted(named):
        cand = n
        for doc_ph, live_ph in PLACEHOLDERS.items():
            cand = cand.replace("/" + doc_ph, "/{%s}" % live_ph)
        if "{" in cand:
            # a placeholder: the prefix must exist live
            prefix = cand.split("{")[0]
            if any(p.startswith(prefix) for p in live_paths):
                continue
            missing.append(n)
            continue
        if cand in live_paths:
            continue
        if any(p.startswith(cand.rstrip("/")) for p in live_paths):
            continue
        missing.append(n)
    check("every path the doc names exists on the live service",
          not missing, "not found: %s" % ", ".join(missing))

    print("\n== 2. the doc's own numbers ==")
    m = re.search(r"\*\*(\d+) 条 path / (\d+) 条路由\*\*", doc)
    check("the doc states %d paths / %d routes" % (EXPECT_PATHS, EXPECT_ROUTES),
          m is not None and m.group(1) == str(EXPECT_PATHS)
          and m.group(2) == str(EXPECT_ROUTES),
          "the doc says %s paths / %s routes"
          % (m.group(1) if m else "?", m.group(2) if m else "?"))
    for label, pat, want in [
            ("%d paths under /api/v1/" % EXPECT_V1_PATHS,
             r"/api/v1/` — (\d+) 条 path", str(EXPECT_V1_PATHS)),
            ("%d paths under /api/" % EXPECT_UI_PATHS,
             r"`/api/`（UI 家族）— (\d+) 条 path", str(EXPECT_UI_PATHS)),
            ("5 page paths",
             r"页面自身.*?— (\d+) 条", "5")]:
        mm = re.search(pat, doc)
        check("the doc states %s" % label,
              mm is not None and mm.group(1) == want,
              "found %s" % (mm.group(1) if mm else None))

    print("\n== 3. read-only claims, against the DEPLOYED service (8800) ==")
    for path, key, want in [
            ("/api/v1/connection", "simulated", False),
            ("/api/state", "simulate", False)]:
        c, b = call("GET", LIVE + path)
        check("GET %s -> 200 and reports the real device" % path,
              c == 200 and b.get(key) is want,
              "%s %s=%r" % (c, key, as_dict(b).get(key)))

    c, b = call("GET", LIVE + "/api/v1/battery")
    check("GET /api/v1/battery -> 200 with the documented fields",
          c == 200 and all(k in as_dict(b) for k in
                           ("ok", "percent", "percent_shown", "samples",
                            "filter_reason", "pack_volts", "cell_count",
                            "connected")),
          str(c))

    c, b = call("GET", LIVE + "/api/v1/battery/curve")
    check("GET /api/v1/battery/curve -> 200 with 122 points",
          c == 200 and as_dict(b).get("cell_count") == 4 and as_dict(b).get("full_at") == 95
          and len(as_dict(b).get("points", [])) == 122,
          "%s points=%s" % (c, len(as_dict(b).get("points", []))))

    c, b = call("GET", LIVE + "/api/v1/pressure")
    check("GET /api/v1/pressure -> 200 and has NO 'armed' field",
          c == 200 and "armed" not in as_dict(b) and "streaming" in as_dict(b), str(c))

    c, b = call("GET", LIVE + "/api/v1/pressure/series?seconds=15")
    got = as_dict(b).get("detail")
    check("pressure/series?seconds=15 -> 422 with the documented string",
          c == 422 and got == "seconds must be one of 10, 30, 60",
          "%s %r" % (c, b))
    check("the DOC quotes that exact refusal string",
          got is not None and got in doc,
          "the service answered %r, and that string is not in the doc"
          % (got,))
    c, b = call("GET", LIVE + "/api/v1/pressure/series?channel=2")
    got = as_dict(b).get("detail")
    check("pressure/series?channel=2 -> 422 with the documented string",
          c == 422 and got == "channel must be 0 or 1",
          "%s %r" % (c, b))
    check("the DOC quotes that exact refusal string too",
          got is not None and got in doc,
          "the service answered %r, and that string is not in the doc"
          % (got,))

    c, b = call("GET", LIVE + "/api/v1/stroke")
    check("GET /api/v1/stroke is 409 while the link state allows a read",
          c in (200, 409), "%s %r" % (c, as_dict(b).get("detail")))

    c, b = call("GET", LIVE + "/api/devices")
    check("GET /api/devices -> 200 with the three counts",
          c == 200 and all(k in as_dict(b) for k in
                           ("devices", "count", "hidden_count", "hidden",
                            "scan_seconds")),
          str(c))
    scan = as_dict(b).get("scan_seconds")
    # EVERY value the doc states must equal the measured one.  A check
    # that only looks for the measured value somewhere in the doc passes
    # when the doc states it once correctly and once wrongly -- which is
    # exactly what a mutation that edits one of the two occurrences did.
    stated = re.findall(r'"scan_seconds"\s*:\s*([0-9.]+)', doc)
    check("the DOC states the measured scan_seconds (%s)" % scan,
          scan is not None and stated
          and all(float(v) == float(scan) for v in stated),
          "the service answered scan_seconds=%r; the doc states %r"
          % (scan, stated))

    c, b = call("GET", LIVE + "/api/tick")
    check("GET /api/tick -> 200 with the 8-30 ms band",
          c == 200 and as_dict(b).get("min_tick_ms") == 8 and as_dict(b).get("max_tick_ms") == 30,
          str(c))
    check("the DOC states the measured tick band (8-30 ms)",
          "8–30 ms" in doc or "8-30 ms" in doc,
          "the doc does not carry the measured band")

    c, b = call("GET", LIVE + "/api/frame/50")
    hexval = as_dict(b).get("hex")
    check("GET /api/frame/50 -> 200 with the documented hex frame",
          c == 200 and hexval == "A5 5A 0D A0 B0 00 A0 01 0F 88 13 B0 E6",
          "%s %r" % (c, hexval))
    check("the DOC quotes that exact frame",
          hexval is not None and hexval in doc,
          "the service answered %r, and that hex is not in the doc" % (hexval,))

    c, b = call("GET", LIVE + "/api/auto")
    check("GET /api/auto (UI family) -> 200 and reports params.preset",
          c == 200 and "preset" in as_dict(b).get("params", {}), str(c))

    print("\n== 4. the LAN gate claim, against the DEPLOYED service ==")
    gate_open = True
    for p in ("/api/state", "/api/records", "/api/v1/connection"):
        c, _ = call("GET", LIVE + p)
        gate_open = gate_open and c == 200
        check("no-token GET %s -> 200 (the gate is OFF)" % p, c == 200, str(c))
    # The doc's own sentence must agree with what was measured.  A doc that
    # says the gate is ON while the service says OFF is worse than no doc.
    check("the DOC says the gate is OFF, and the service agrees",
          gate_open and "访问令牌门是关的" in doc,
          "measured gate open=%s; the doc contains the phrase %r: %s"
          % (gate_open, "访问令牌门是关的",
             "访问令牌门是关的" in doc))

    print("\n== 5. the SSE claim, against the DEPLOYED service ==")
    import socket
    s = socket.create_connection((LIVE_HOST, LIVE_PORT), timeout=10)
    s.sendall(b"GET /api/events HTTP/1.1\r\nHost: x\r\n"
              b"Accept: text/event-stream\r\nConnection: close\r\n\r\n")
    s.settimeout(10)
    buf = b""
    try:
        while len(buf) < 400:
            d = s.recv(4096)
            if not d:
                break
            buf += d
    except socket.timeout:
        pass
    s.close()
    head = buf.decode("utf-8", "replace")
    check("GET /api/events is text/event-stream",
          "text/event-stream" in head, head[:120])
    check("GET /api/events carries a data: line", "data: {" in head,
          head[:200])

    print("\n== 6. the write claims, on a SIMULATOR (8800 drives a real device) ==")
    env = dict(os.environ)
    proc = subprocess.Popen(
        [_VENV_PYTHON, "start_web.py", "--simulate",
         "--host", "127.0.0.1", "--port", str(SIM_PORT)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env)
    try:
        ready = wait_for(SIM + "/", seconds=30)
        check("the simulator instance came up on %d" % SIM_PORT, ready,
              "the child process is still starting; the probe gave up after "
              "30 s")
        if ready:
            B = SIM + "/api/v1"
            c, b = call("POST", B + "/connection/connect")
            check("POST /api/v1/connection/connect -> 200 connected",
                  c == 200 and as_dict(b).get("connected") is True, str(c))
            c, b = call("POST", B + "/connection/connect")
            check("connecting twice is idempotent (already:true)",
                  c == 200 and as_dict(b).get("already") is True, "%s %r" % (c, b))
            c, b = call("POST", SIM + "/api/arm")
            check("POST /api/arm -> 200 armed",
                  c == 200 and as_dict(b).get("armed") is True, str(c))
            c, b = call("POST", B + "/stroke", {"percent": 45, "vibration": 90})
            check("POST /api/v1/stroke {percent:45,vibration:90} -> 200",
                  c == 200 and as_dict(b).get("percent") == 45.0
                  and as_dict(b).get("vibration") == 90, "%s %r" % (c, b))
            c, b = call("POST", B + "/stroke", {"percent": 50})
            check("an OMITTED vibration KEEPS the level (90 stays 90)",
                  c == 200 and as_dict(b).get("vibration") == 90, "%s %r" % (c, b))
            c, b = call("GET", B + "/stroke")
            check("and the level is still 90 on the next read",
                  c == 200 and as_dict(b).get("vibration") == 90, "%s %r" % (c, b))
            c, b = call("POST", B + "/intensity", {"level": 2})
            check("POST /api/v1/intensity {level:2} -> 200 level 2",
                  c == 200 and as_dict(b).get("level") == 2, "%s %r" % (c, b))
            c, b = call("POST", B + "/intensity", {"level": 5})
            check("POST /api/v1/intensity {level:5} -> 422",
                  c == 422, "%s %r" % (c, b))
            c, b = call("POST", B + "/auto/start",
                        {"min_percent": 25, "max_percent": 75,
                         "traverse_seconds": 0.6})
            check("POST /api/v1/auto/start -> 200 running",
                  c == 200 and as_dict(b).get("running") is True, "%s %r" % (c, b))
            c, b = call("POST", B + "/auto/params", {"max_percent": 60})
            check("POST /api/v1/auto/params -> 200 max 60",
                  c == 200 and as_dict(b).get("max_percent") == 60.0, "%s %r" % (c, b))
            c, b = call("POST", B + "/auto/params",
                        {"min_percent": 90, "max_percent": 10})
            check("an inverted range -> 422 naming BOTH numbers",
                  c == 422 and "10" in str(as_dict(b).get("detail"))
                  and "90" in str(as_dict(b).get("detail")), "%s %r" % (c, b))
            c, b = call("POST", B + "/gait/start", {"steps": "30:2,70"})
            check("POST /api/v1/gait/start -> 200 with the parsed steps",
                  c == 200 and len(as_dict(b).get("steps", [])) == 2, "%s %r" % (c, b))
            c, b = call("POST", SIM + "/api/auto/params", {"preset": "triangle"})
            check("an unknown preset -> 422 naming the three valid ones",
                  c == 422 and "sine" in str(as_dict(b).get("detail"))
                  and "surge" in str(as_dict(b).get("detail"))
                  and "sag" in str(as_dict(b).get("detail")), "%s %r" % (c, b))
            c, b = call("POST", SIM + "/api/tick", {"tick_ms": 5})
            check("tick_ms=5 -> 422 refused, not clamped",
                  c == 422 and "REFUSED" in str(as_dict(b).get("detail")),
                  "%s %r" % (c, b))
            c, b = call("POST", SIM + "/api/target", {"percent": 40})
            check("POST /api/target -> 200 with target_percent 40",
                  c == 200 and as_dict(b).get("target_percent") == 40.0, str(c))
            c, b = call("POST", SIM + "/api/stop")
            check("POST /api/stop -> 200 and releases to 0",
                  c == 200 and as_dict(b).get("target_percent") == 0.0, str(c))
            c, b = call("POST", B + "/connection/disconnect")
            check("POST /api/v1/connection/disconnect -> 200 disconnected",
                  c == 200 and as_dict(b).get("connected") is False, "%s %r" % (c, b))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\n== 7. the 409 messages, per route ==")
    proc = subprocess.Popen(
        [_VENV_PYTHON, "start_web.py", "--simulate",
         "--host", "127.0.0.1", "--port", str(SIM_PORT)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ready = wait_for(SIM + "/", seconds=30)
        check("the second simulator instance came up on %d" % SIM_PORT, ready,
              "the child process is still starting; the probe gave up after "
              "30 s")
        B = SIM + "/api/v1"
        for path, body, want in [
                ("/stroke", {"percent": 50},
                 "not connected; POST /api/v1/connection/connect first"),
                ("/stroke/release", None,
                 "not connected; POST /api/v1/connection/connect first"),
                ("/auto/start", {"min_percent": 10, "max_percent": 70},
                 "not connected; POST /api/v1/connection/connect first"),
                ("/intensity", {"level": 1},
                 "not connected; POST /api/v1/connection/connect first"),
                ("/gait/start", {"steps": "30:2"},
                 "not connected")]:
            c, b = call("POST", B + path, body)
            check("POST /api/v1%s -> 409 %r" % (path, want),
                  c == 409 and as_dict(b).get("detail") == want,
                  "%s %r" % (c, as_dict(b).get("detail")))
        # THE BODY IS VALIDATED BEFORE THE LINK IS CHECKED.  A malformed
        # body is a 422 whether or not a device is connected; only a VALID
        # body reaches the 409.  These live in the DISCONNECTED section
        # because that is the claim they make -- a caller in a disconnected
        # state sees the 422 first and must not read it as "not ready".
        c, b = call("POST", B + "/stroke", None)
        check("a malformed body is 422 even while DISCONNECTED",
              c == 422 and "missing" in str(as_dict(b).get("detail")),
              "%s %r" % (c, b))
        c, b = call("POST", B + "/stroke", {"percent": 150})
        check("an out-of-range body is 422 even while DISCONNECTED",
              c == 422 and "less_than_equal" in str(as_dict(b).get("detail")),
              "%s %r" % (c, b))
        c, b = call("POST", B + "/intensity", {"level": 5})
        check("an out-of-range intensity is 422 even while DISCONNECTED",
              c == 422 and "less_than_equal" in str(as_dict(b).get("detail")),
              "%s %r" % (c, b))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\n== 8. the doc's text claims about the DEPLOYED service ==")
    # WHERE THE SERVICE LIVES.  The reference deployment ran it from a
    # systemd unit on this host; a recipient's deployment will differ, so
    # the unit is READ (when there is one) rather than assumed.
    # `systemctl` DOES NOT EXIST on Windows, and subprocess.run raises
    # FileNotFoundError for a missing executable rather than returning a
    # non-zero code -- so an unguarded call here CRASHES the whole
    # verifier on Windows, at the very last section, after every other
    # check has already run and passed.
    #
    # The check describes the REFERENCE deployment (a systemd unit on the
    # build host). On a recipient's machine there is no such unit, and
    # the section is skipped -- which is what it already did for a host
    # with no unit. So the guard is not a weakening: it makes Windows
    # take the same path a machine without a unit takes.
    unit = ""
    if shutil.which("systemctl"):
        unit = subprocess.run(["systemctl", "cat", "vera-preview.service"],
                              capture_output=True, text=True).stdout
    if unit.strip():
        check("the service unit runs a DEPLOYED COPY, not a working tree",
              "vera-station-preview" in unit, unit[:200])
        check("the unit's ExecStart has no --simulate (real hardware)",
              "--simulate" not in unit and "8800" in unit)
    else:
        print("  --      no vera-preview.service here; the deployment "
              "checks are skipped (they describe the reference deployment)")

    c, b = call("GET", LIVE + "/api/records")
    recs = as_dict(b).get("records") or []
    # `current` is the file being written RIGHT NOW, so it is null between
    # recordings -- it is a transient field, and a check keyed on it goes
    # red every time the device disconnects.  The DURABLE fact is in
    # `records[].path`, which names the tree the recording lives in.
    paths = [r.get("path") for r in recs if isinstance(r, dict)]
    if paths:
        # The claim is that the recordings live in the DEPLOYED tree and
        # not in the working tree the service was started from.  That is
        # checked by COMPARING the two paths, not by matching a literal
        # directory name -- so it still holds on a recipient's machine,
        # where both trees have different names.
        deploy_root = None
        for u in [unit]:
            if u.strip():
                for line in u.splitlines():
                    if line.startswith("WorkingDirectory="):
                        deploy_root = line.split("=", 1)[1].strip()
                        break
        if deploy_root:
            check("the recordings live in the DEPLOYED tree, not the "
                  "working tree",
                  all(str(x).startswith(deploy_root) for x in paths),
                  "WorkingDirectory=%s, recorded paths are %r"
                  % (deploy_root, paths))
        else:
            check("every recorded path names a tree",
                  all(str(x).startswith("/") for x in paths),
                  "the recorded paths are %r" % (paths,))
    else:
        print("  --      no recordings on disk yet; the path checks are "
              "skipped")

    print("\n" + "=" * 70)
    print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAILED: %s" % f)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
