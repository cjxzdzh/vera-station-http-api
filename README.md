# vera-station-http-api

The HTTP API of **Vero Station** — a cross-platform controller for **AIKE**'s
**Vero** and **Mira** devices — packaged as a **skill** for an agent runtime.

This repository is a companion to
**[cjxzdzh/vero-station](https://github.com/cjxzdzh/vero-station)**, which is
the service itself. Each repository points at the other.

| | |
|---|---|
| **[cjxzdzh/vero-station](https://github.com/cjxzdzh/vero-station)** | The service: the BLE layer, the web UI, the CLI, the protocol documentation. |
| **[cjxzdzh/vera-station-http-api](https://github.com/cjxzdzh/vera-station-http-api)** (this one) | The skill: the HTTP API reference for an agent, plus a verifier that checks every documented claim against a running service. |

---

## What this is

A **skill** is a folder with a `SKILL.md` that an agent runtime loads, plus
the scripts that prove its claims. It is not an application and it has no
daemon of its own.

This one contains:

* **`SKILL.md`** — the complete API reference: every route, what each one
  needs first, the exact error shapes, and the ordering rules. It was
  produced by **measuring a running service**, not by reading the source.
* **`INSTALL.md`** — how to install the skill, and the one check you must do
  before writing any command that moves a device.
* **`references/VERO-8800-API.md`** — the Chinese quick-reference.
* **`scripts/verify_api_doc.py`** — the verifier. Point it at a running
  service and it re-checks every documented claim against it.
* **`BUILD_INFO.json`** — provenance: what was measured, when, and how the
  verifier itself was tested.

**The skill is documentation plus a verifier.** It does not install a
service, does not talk to a device on its own, and does not need to be
running for the documentation to be useful.

---

## Install

```bash
git clone https://github.com/cjxzdzh/vera-station-http-api.git
cp -r vera-station-http-api ~/.hermes/skills/     # Hermes
cp -r vera-station-http-api ~/.claude/skills/     # Claude Code
cp -r vera-station-http-api ~/.codex/skills/      # Codex
```

**One copy, one name.** Two directories with the same skill name make the
name ambiguous and the skill **unloadable** — a runtime that finds two
candidates refuses to guess which one to load. If you install it in more
than one place, rename the directory in one of them.

**Do not install it into a directory that contains other `SKILL.md` files**
if your runtime walks that tree recursively: it will register those too.

---

## Before you write a command

You are about to write commands that **move a physical device**. Check what
you are pointing at first:

```bash
# Is the service there?
curl -s -o /dev/null -w '%{http_code}\n' http://<lan-ip>:8800/

# Is it a real device or a simulator?  THIS IS THE ONE THAT MATTERS.
curl -s http://<lan-ip>:8800/api/v1/connection \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("simulated:", d.get("simulated"), "| connected:", d.get("connected"))'
```

| You see | What it means | What you may do |
|---|---|---|
| `simulated: False` | **A real device.** A write moves it. | Read-only calls only, until you have a simulator. |
| `simulated: True` | A simulator. | Anything. |

`INSTALL.md` has the full walkthrough, including how to start your own
simulator for testing writes.

---

## The verifier

The bundle ships the script that produced the measurements in `SKILL.md` and
in `references/VERO-8800-API.md`. Run it against **your** service and see
the checks pass:

```bash
VERO_LIVE_HOST=<lan-ip> .venv/bin/python3 scripts/verify_api_doc.py
```

On Windows:

```bat
set VERO_LIVE_HOST=<lan-ip>
.venv\Scripts\python.exe scripts\verify_api_doc.py
```

Expected: `60 passed, 0 failed`, and exit code 0. (The count is 60 rather
than 62 on a machine that has no `vera-preview.service` unit: the two
deployment checks describe the reference deployment and are skipped where
there is no unit.)

The verifier enumerates the live OpenAPI spec, checks that every path the
documentation names really exists, re-runs every **read-only** claim against
the deployed service, and starts its **own** simulator to re-run every
**write** claim there — it never writes to your real device.

If the service you point it at is older than this documentation, some checks
will fail and the output will say which claim is out of date — **that is the
script working**, not a broken bundle. Read the failing check against the
live service before trusting either side.

**The verifier needs the project's own tree** for `start_web.py` and the
`vero` package. If you only took the skill folder, run only the read-only
part against a live service; the write checks need the tree.

---

## The service

This skill documents, but does not install, the service. Get it from
**<https://github.com/cjxzdzh/vero-station>**:

```bash
git clone https://github.com/cjxzdzh/vero-station.git
cd vera-station
./install-ubuntu.sh          # or install-macos.sh, or install-windows.bat
./run.sh
```

One tree runs on Windows, Linux and macOS, and the UI comes up on
<http://127.0.0.1:8800>.

---

## License

MIT. See [LICENSE](LICENSE).
