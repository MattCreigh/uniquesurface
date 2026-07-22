# Trinity GUI

A minimal, dependency-free Go HTTP GUI for the [Trinity](..) background manager.

## Overview

The GUI is a **thin wrapper** around the `trinity` CLI. It:

1. Serves a single-page dark-theme web UI from a local HTTP server.
2. Exposes REST endpoints that shell out to `trinity` subcommands via `exec.Command`.
3. Reads `~/.local/state/trinity/manifest.jsonl` to display history.

**The GUI never writes to Trinity state files directly.** All state changes go
through the `trinity` CLI.

## Prerequisites

- Go 1.22+ (standard library only — no external Go dependencies)
- `trinity` CLI installed and on `$PATH`

## Build

```bash
cd gui
go build -o trinity-gui .
```

Or with the Makefile:

```bash
make build
```

## Run

```bash
./trinity-gui
```

The server binds to `127.0.0.1` on a random port and opens the default browser
automatically.

## Test

```bash
make test
# or
go test ./...
```

## API Endpoints

| Endpoint            | Method | CLI command                              |
|---------------------|--------|------------------------------------------|
| `/api/status`       | POST   | `trinity status`                         |
| `/api/apply`        | POST   | `trinity apply`                          |
| `/api/cycle`        | POST   | `trinity cycle` or `trinity cycle --offset N` |
| `/api/restore`      | POST   | `trinity restore --yes`                  |
| `/api/doctor`       | POST   | `trinity doctor`                         |
| `/api/history`      | POST   | reads manifest.jsonl (no CLI call)       |

### Cycle offset

`GET /api/cycle?offset=2` runs `trinity cycle --offset 2`.
A negative or absent offset runs `trinity cycle` (next wallpaper).

## Architecture

```
gui/
├── go.mod              — module definition (no external deps)
├── main.go            — HTTP server, CLI wrapper endpoints, browser open
├── manifest.go        — JSONL manifest parser
├── manifest_test.go   — tests for the manifest parser
├── embed.go           — //go:embed frontend/index.html
├── frontend/
│   └── index.html     — dark-theme SPA with action buttons
├── Makefile           — build / test / clean / run
└── README.md          — this file
```

### Security notes

- All CLI invocations use `exec.Command(name, args...)` with separate
  arguments — never shell string construction.
- The server listens on `127.0.0.1` only (not exposed to the network).
- The manifest parser skips corrupt/empty lines, mirroring the Python
  implementation.