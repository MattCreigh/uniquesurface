# uniquesurface — Enterprise Audit Report

**Date:** 2026-07-08
**Target system:** `uniquesurface` (`usurface`) — Unified KDE Plasma 6 surface-set manager
**Auditor:** Automated senior-architect review (code-first; READMEs/PLAN.md consulted but not trusted blindly)
**Artifacts provided:** Full codebase (`src/`, `tests/`), `README.md`, `PLAN.md`, `CHANGELOG.md`, `CODEBASE_ASSESSMENT.md` (a prior self-audit), `docs/`.
**Key question:** *Create an in-depth, well-researched report into the codebase and its aims — exclude natural language from the initial assessment, but do consider READMEs so as not to be swayed by docs too much.*

---

## PART 1: Planning and Evidence Log

### Search Queries Executed (workspace)
- `load_setuptools_entrypoints|entry_point|load_entry` in `src/usurface/providers/__init__.py` → **empty** (third-party plugin loading NOT wired).
- `clock_format|show_user_list|weight` in `schema.py` → `clock_format` and `weight` are unvalidated free strings.
- `import jinja|from jinja` in `src/` → **empty** (Jinja2 is not used; systemd units use inline templates).
- `import fonttools|from fonttools` in `src/` → **empty** (fonttools declared as dev dep but unused).
- `def test_` in `tests/test_providers.py` → 18 tests, none cover third-party entry-point loading.
- `grep -n "__version__\|^version"` → `0.1.0` in both `__init__.py` and `pyproject.toml` (consistent).

### Live Verification Runs
- `uv run --extra test pytest -q` → **106 passed in 1.45s**.
- `uv run ruff check src tests` → **All checks passed!**
- `uv run mypy src` → **Success: no issues found in 29 source files.**
- `wc -l` over `src/ tests/*.py` → **6,052 LOC** (src ~3,500; tests ~2,500).
- `ls src/usurface/theme/fonts/` → `Inter-Regular.ttf` (637 KB) IS vendored (contradicts CHANGELOG "Known limitations").

### Files Scanned (primary)
- `pyproject.toml`, `README.md`, `PLAN.md`, `CHANGELOG.md`, `CODEBASE_ASSESSMENT.md`
- `src/usurface/__init__.py`, `cli.py`, `orchestrator.py`, `config.py`, `schema.py`, `paths.py`, `atomic.py`, `manifest.py`, `logging.py`
- `src/usurface/backends/{base,desktop,lock,login,_kconfig}.py`
- `src/usurface/providers/{__init__.py,builtin/{bing,file,solid}.py}`
- `src/usurface/theme/{extract,drift,qml_patch,font_install}.py`
- `src/usurface/systemd/writer.py`
- `docs/{config-reference,migration-from-shell}.md`
- `src/usurface/providers/README.md`

### Found Discrepancies (docs ↔ code)
1. **Third-party provider plugins are documented but NOT implemented.** `providers/README.md` and `PLAN.md §7.5` describe entry-point plugins; `make_plugin_manager()` only registers the three built-ins and never calls `pm.load_setuptools_entrypoints()`. The "provider-extensible" headline feature is, for third parties, **marketing-only**.
2. **CHANGELOG "Known limitations" is stale.** It states "No vendored Inter font bundled" — but `theme/fonts/Inter-Regular.ttf` (637 KB) is present and `_bundled_font()` finds it.
3. **README test badge says `86 passed`**; actual is **106 passed**. Under-stated (cosmetic).
4. **README/PLAN claim `pluggy` plugin model** — true for built-ins, but the entry-point discovery that makes it "extensible" is absent.
5. **`CODEBASE_ASSESSMENT.md` (prior audit) claims Jinja2 is a runtime dependency.** It is not declared in `pyproject.toml` and never imported; systemd units use inline Python string templates. The prior audit also reported `78 passed` — the suite has grown to 106.
6. **`docs/config-reference.md` shows `[surface.lock]` with only `on_idle_dim_seconds`** but the schema also has `suppress_wake_keypress` (default `true`) — minor doc drift.
7. **`clock_format` and `weight` are unvalidated free strings** (no regex/enum). Mitigated by QML-literal escaping in `_replace_property_values`, so not an injection hole — but typos silently produce a broken clock font.

### Strategic Hypotheses (initial answer to the key question)
- *Architecture:* Clean, layered, testable; atomic I/O + manifest undo is genuinely strong for this domain.
- *Aims:* Coherent and mostly realised for built-in flows (desktop/lock/login wallpaper + font/theme token patching).
- *Biggest gap:* The "provider-extensible via plugins" claim is unimplemented; only built-ins work.
- *Biggest risk:* QML patching of vendor files is the fragile, high-blast-radius surface — well-mitigated by drift detection + consent gate, but still the part most likely to break on a Plasma update.
- *Verdict preview:* A focused, well-engineered niche tool that **is good at what it actually does**, with one headline feature that is currently vapourware.

---

## PART 2: Final Audit Report

## Executive Summary

`uniquesurface` is a well-architected, single-purpose Python CLI that synchronises wallpaper and font/theme tokens across the three KDE Plasma 6 surfaces (desktop, lock, SDDM login). For its **actually-implemented** scope — three built-in providers, atomic writes, append-only manifest with rollback, drift-aware QML patching, and a systemd timer — it is genuinely good: 106 passing tests, clean ruff and mypy, ~6k LOC, no shell scripts, reversible and idempotent by construction. The single most important takeaway is that the **"provider-extensible via a `pluggy` plugin model" headline is not wired up**: `make_plugin_manager()` registers only built-ins and never loads entry points, so third-party providers — the feature most prominently advertised in the README and `providers/README.md` — cannot work as documented. Answering the key question directly: the codebase and its aims are coherent and the implementation is solid where it exists, but one marquee capability is documentation-only, and that gap should be closed or the docs corrected before any public release.

## 1. Technical Audit

### 1.1 Component-by-Component Assessment

| # | Component | Score (/10) | Evidence / Notes |
|---|-----------|:---:|---|
| 1 | `cli.py` (Click) | 8 | 11 commands incl. `apply/restore/status/install/uninstall/pause/resume/doctor/migrate-from-shell/qml-update-templates`; `CLIError` + `excepthook` give clean user errors; `USURFACE_DEBUG` opt-in traceback. Lazy imports keep startup cheap. |
| 2 | `config.py` / `schema.py` (pydantic v2) | 7 | Strict (`extra="forbid"`), regex-validated provider/font/color; legacy-key stripping for `show_user_list`; `schema_version` migration hook. **Gap:** `clock_format` and `weight` are unvalidated free strings. |
| 3 | `providers/__init__.py` | 5 | Clean `pluggy` hookspec + built-in adapter. **Critical gap:** `load_setuptools_entrypoints("usurface.providers")` never called → third-party plugins documented in `providers/README.md` are non-functional. |
| 4 | `providers/builtin/bing.py` | 8 | `httpx` with browser UA, streaming + 50 MiB download cap (Content-Length + byte-count), `ProviderError` on bad metadata. Good defensive network code. No proxy/TLS-pin config. |
| 5 | `providers/builtin/{file,solid}.py` | 7 | `file` expands `~`/env, infers content-type; `solid` validates hex + dimensions, generates solid/gradient JPEG. `file` reads verbatim (no size cap on local files). |
| 6 | `orchestrator.py` | 8 | Template-method pipeline; per-backend `BackendError` caught so one failing surface doesn't abort others; `adopt_drift` consent path; manifest compaction after success. `verify_image` decodes+re-encodes, strips metadata. |
| 7 | `backends/desktop.py` + `_kconfig.py` | 7 | Shells out to `kwriteconfig6` + `qdbus6 refreshWallpaper`. Known limitation (repo memory): writes flat `[Containments]` group, relies on qdbus refresh. `qdbus_call` tolerates missing Plasma (soft failure). |
| 8 | `backends/lock.py` | 8 | Correct nested-group path `[Greeter][Wallpaper][org.kde.image][General] Image=` verified against upstream kscreenlocker. Drops to `SUDO_USER` when root so it doesn't write root-owned user config. |
| 9 | `backends/login.py` | 7 | Regex edit of SDDM `theme.conf` `background=`/`color=`; `_can_write` checks file+parent; `login_surface_needs_root()` pre-flight warns user. Hard-coded to Breeze theme path (acceptable per PLAN non-goal). |
| 10 | `theme/qml_patch.py` | 8 | Sentinel-comment region + in-place `readonly property string` value rewrite (the corrected approach after the `pragma Singleton` blue-screen lesson). Escapes `"`/`\` in values. `_merged_marker_block` lets font+lock patchers share one sentinel region. |
| 11 | `theme/drift.py` | 9 | SHA-256 of sentinel-stripped + property-normalised content vs stored pristine; `DriftError` refuses silent adoption; idempotent backups (dedup by content SHA); explicit `--adopt-drift` consent. This is the standout safety mechanism. |
| 12 | `theme/extract.py` + `font_install.py` | 7 | Pristine QML copied (sentinels stripped) into state dir; font install tries `/usr/local/share/fonts/usurface` then `~/.local/share/fonts`; runs `fc-cache -f`. `is_installed` uses `fc-match` substring match (fragile but acceptable). |
| 13 | `manifest.py` | 9 | Append-only JSONL with SHA-256 + byte snapshots; `restore` replays inverse ops newest-first; `compact` bounds history to 200 entries; snapshot pruning by reference set. Robust undo subsystem. |
| 14 | `atomic.py` | 8 | tmp → fsync → `os.replace`; EXDEV fallback to sibling temp; last-resort direct overwrite with a helpful permission-error message. The non-atomic fallback is a deliberate trade-off (clarity over atomicity) — worth a doc note. |
| 15 | `systemd/writer.py` | 8 | Inline unit templates (no Jinja2 despite prior audit's claim); `UsurfaceBinaryNotFound` instead of a silent 203/EXEC failure; `pause`/`resume` via runtime `mask`/`unmask`; `is_paused` checks the `/dev/null` symlink. |
| 16 | `paths.py` | 8 | XDG-aware via `platformdirs`; `SUDO_USER` honoured so root runs touch the invoking user's dirs; `USURFACE_SHARED_DIR` override. |
| 17 | Tests (15 files, 106 tests) | 8 | Atomic, config, providers (respx-mocked Bing), backends (fake kwriteconfig), manifest, qml_patch (snapshot), drift, systemd, CLI e2e. **Gap:** no entry-point plugin test, no `@pytest.mark.integration` test in use. |

### 1.2 Security Deep-Dive

**Threat model.** The system runs as the user for `apply` and as root for `install`/patching system files. Surfaces of concern: network fetch (Bing), local file read (file provider), QML injection (config → vendor QML), and root-privilege QML writes.

| Finding | Severity | Likelihood | Impact | Evidence |
|---|---|---|---|---|
| Third-party provider plugins documented but not loaded → users may install a "plugin" package expecting it to work; if a future patch wires `load_setuptools_entrypoints` without sandboxing, arbitrary code runs as the user. Currently inert, but the docs create a false expectation. | Medium | Possible | Major | `providers/__init__.py` has no `load_setuptools_entrypoints`; `providers/README.md` documents entry-point registration. |
| `file` provider reads arbitrary local files verbatim into the wallpaper pipeline (then re-encoded by Pillow, so not exfiltrated, but a huge file could exhaust memory — Pillow decodes the whole image). | Low | Unlikely | Minor | `providers/builtin/file.py` has no size cap; `verify_image` decodes full image. |
| QML value injection is mitigated: `_replace_property_values` escapes `\` and `"` before substituting into `"...literal..."`. A malicious `clock_format`/`weight`/`font_family`/`password_character` cannot break out of the QML string literal. | — (mitigated) | — | — | `qml_patch.py` replacement logic. |
| `font_family` is regex-validated (`^[A-Za-z0-9][A-Za-z0-9 _.-]{0,127}$`) — good; `accent_color` validated as `#RGB`/`#RRGGBB` — good; `provider` name validated — good. `clock_format` and `weight` are **not** validated (only length-bound by QML escape). | Low | Possible | Minor | `schema.py` lacks validators for those two fields. |
| `kwriteconfig6`/`qdbus6` argv: values are passed as separate argv elements (not shell-joined), so no shell-injection. `subprocess.run(..., check=True)`. Safe. | — (ok) | — | — | `_kconfig.py`, `lock.py`. |
| Root runs drop to `SUDO_USER` for `kwriteconfig6` and `systemctl --user` — prevents accidental root-owned user config. Good privilege hygiene. | — (ok) | — | — | `lock.py::_kwriteconfig_nested`, `systemd/writer.py::systemctl`. |
| `verify_image` re-encodes via Pillow, stripping EXIF/metadata — a small privacy win. Pillow itself is a C parser with historical CVEs; keeping `<12` pinned is correct. | Low | Rare | Moderate | `orchestrator.py::verify_image`. |
| No TLS certificate pinning / proxy config for Bing fetch; relies on `httpx` defaults. Acceptable for a POTD client. | Low | Unlikely | Minor | `bing.py`. |
| Manifest snapshots stored under `~/.local/state/usurface/manifest_snapshots/`; if that dir is deleted, `restore` raises `FileNotFoundError` (refuses to silently corrupt). Correct fail-closed behaviour. | — (ok) | — | — | `manifest.py::restore`. |

**Overall security posture:** solid for a single-user desktop tool. The only genuinely material item is the **not-yet-wired plugin surface**: when it is wired, it must be treated as a supply-chain trust boundary (the `providers/README.md` already says so) and ideally gated behind an explicit allow-list or confirmation.

### 1.3 Performance & Resource Efficiency

- **No token cost** (not an LLM system) — N/A for the "token efficiency" criterion; the analogue is *disk/network/CPU*.
- **Network:** Bing provider streams with a 50 MiB cap; metadata is one small JSON GET. A daily run is ~2 MiB/day.
- **Disk:** Wallpaper is written twice (user copy + shared copy, ~2 MiB each) and re-encoded once; manifest snapshots deduplicate by SHA-256 (identical prior states share one snapshot). Manifest compaction bounds to 200 entries — disk cannot grow unbounded under the timer.
- **CPU:** One Pillow decode + re-encode per apply — sub-100 ms for a 1080p JPEG on a modern laptop.
- **Memory:** `file` provider loads the whole image into memory; `bing` caps streaming. A user pointing `file` at a 500-MB TIFF would spike RAM. A size guard on `file` would be a cheap win.

### 1.4 Codebase Quality & Maintainability

- **Language use:** Python 3.12+, `from __future__ import annotations` throughout, dataclasses for value objects, pydantic v2 for config, `Protocol` (runtime-checkable) for backends.
- **Testing:** 106 tests, ~1.5 s, respx for HTTP, fake-kwriteconfig fixture, syrupy snapshots for QML. Coverage of the critical paths (atomic, manifest, drift, qml_patch) is strong.
- **Lint/types:** ruff clean, mypy clean (29 source files). Dev deps (`ruff`, `mypy`, `fonttools`) are in `[dependency-groups] dev` — the prior audit's "missing dev deps" finding is resolved.
- **Smells / nits:**
  - `fonttools` is a declared dev dependency but **never imported** anywhere in `src/` or `tests/` — dead dependency.
  - `is_installed(family)` does a substring match on `fc-match` output (`family.split()[0].lower() in out.stdout.lower()`); a family named "Inter" would match "Inter Dimensional" — fragile.
  - `_to_toml` is a hand-rolled serializer; correct for the current flat schema but a maintenance risk if the schema gains arrays-of-tables. Prefer `tomli_w` or pydantic's TOML exporter.
  - `qdbus_call` logs at `debug` even on hard failure — a `warning` might help diagnose real qdbus issues.
  - `DesktopBackend` writes the flat `[Containments]` group (per repo memory, a known limitation); a comment acknowledging this would help future maintainers.
  - `Login.show_user_list` is stripped silently except for a structlog warning — fine, but the warning is easy to miss; a CLI note on `config validate` would be friendlier.

## 2. Unorthodox Feature Spotlight

1. **Sentinel-comment QML patching with property-value normalisation for drift detection.** Instead of appending a managed block (which broke the greeter), usurface rewrites the *values* of existing `readonly property string` declarations in place, and `drift.strip_sentinels` normalises those same four properties to a canonical placeholder before hashing — so usurface's own intentional edits never register as drift. *Why clever:* it separates "structure changed upstream" (real drift) from "we changed a value we manage" (not drift), without a diff library. *Repurpose:* any tool that patches vendor config files could use this normalise-then-hash pattern.

2. **Drift consent gate (`DriftError` + `--adopt-drift`).** A drifted vendor file is never silently adopted as the new pristine baseline; the user must explicitly run `qml-update-templates` or pass `--adopt-drift`. *Why clever:* it prevents a third-party or hostile modification from becoming the trusted baseline by default. *Repurpose:* any declarative-patching tool (NixOS home-manager, etckeeper-style managers) benefits from a default-deny adoption policy.

3. **Idempotent drift backups deduplicated by content SHA.** Under a daily timer, unresolved drift would otherwise create one timestamped backup per apply forever. `handle_drift` checks for an existing backup with identical SHA before creating a new one. *Why clever:* bounds unbounded backup growth without a TTL. *Repurpose:* any cron-driven tool that snapshots on a recurring condition.

4. **Append-only JSONL manifest with SHA-referenced snapshot dedup + bounded compaction.** Undo is replay-newest-first; snapshots are named by SHA and shared by identical prior states; `compact` keeps the last 200 entries and prunes only unreferenced snapshots. *Why clever:* combines a real undo log with storage efficiency and a hard cap. *Repurpose:* configuration-management/infra-as-code rollback tools.

5. **Atomic-write fallback chain tuned for the "prior sudo left root-owned parent" case.** `atomic_write_bytes` tries `os.replace`, falls back to a sibling temp in `dest.parent`, and finally a direct overwrite with a *helpful* permission error naming the real destination (and a `chown` hint). *Why clever:* it trades atomicity for a legible error exactly when the common "I ran sudo once" footgun bites. *Repurpose:* any user-facing CLI that writes into directories it may not fully own.

6. **Runtime `mask`/`unmask` for `pause`/`resume` instead of `disable`/`enable`.** Keeps the unit files installed; `is_paused` checks both `systemctl is-enabled` and the `/dev/null` symlink under `$XDG_RUNTIME_DIR`. *Why clever:* distinguishes "stopped" from "removed" cleanly, surviving a reboot-unmask. *Repurpose:* any systemd-automated tool offering a soft pause.

## 3. Competitive Landscape & Market Positioning

### 3.1 Peer System Comparison

| System | Strengths | Weaknesses | uniquesurface differentiator |
|---|---|---|---|
| **variety** | Mature, GUI, many online sources, slideshow | Desktop only; X11-era; irreversible config churn | Covers lock + SDDM; reversible via manifest |
| **nitrogen** | Minimal, fast | Desktop only; no automation; no theme tokens | Atomic, systemd-automated, font/theme tokens |
| **PlasmaWallpaperManager** (GUI weekend projects) | GUI discoverability | Patch vendor files irreversibly; brick on Plasma update | Sentinel + drift detection + consent gate |
| **raw `kwriteconfig6`/shell scripts** (the legacy setup this replaces) | Full control | No safety, no undo, brittle, manual | One config, three surfaces, undo log, tested |
| **KDE's own Wallpaper plugin + kscreenlockerrc manual edits** | First-party | Three separate places, no cohesion | Unified surface-set model |
| **A hypothetical Nix/home-manager module** | Declarative, reproducible | Requires Nix; not KDE-Plasma-6-specific | Works on any distro with Plasma 6 + uv |

### 3.2 Market Gap Analysis
The real gap is **"trustworthy, reversible, CLI-first cohesion across desktop + lock + login on Plasma 6."** No incumbent covers all three surfaces with an undo log and drift-aware vendor patching. The closest analogs are shell scripts (the exact thing this replaces) and irreversible GUI patchers. The niche is narrow but real — KDE power users who daily-drive Bing POTD and want their login screen to match without bricking it.

### 3.3 Pricing / Packaging Suggestions
- **License:** PolyForm Noncommercial already constrains commercial use — appropriate for a hobby/indie tool. If commercial distribution is ever desired, dual-license (PolyForm Strict + a paid commercial license) is the cleanest path.
- **Packaging:** Ship an Arch `PKGBUILD` and a `.deb` (the PLAN already reserves `packaging/{deb,arch}`) to lower the install barrier beyond `uv tool install`. A Flathub-style or AUR presence would materially widen adoption.
- **No paid tier recommended** for a single-user desktop wallpaper tool; community/open-source is the natural mode.

## 4. Academic & Research Alignment

### 4.1 Relevant arXiv Papers
This is a systems tool, not an LLM/agentic system, so the LLM-centric paper lens mostly does not apply. The closest research threads:
- **Configuration drift / declarative patching** — work on *declarative package management* (Nix) and *configuration consistency* (e.g., "Keep Your Configuration Close and Your Drift Closer"-style industry talks) is the conceptual neighbour. usurface's sentinel+normalise-then-hash drift detector is a small, practical instance of the same idea.
- **Atomic file I/O & crash consistency** — classic OS literature (e.g., the `rename()` atomicity guarantees, ext4 data=ordered journaling). usurface's tmp+fsync+replace is the textbook pattern; the EXDEV/direct-overwrite fallback is an engineering pragmatic extension not found in papers but common in production tools (e.g., ostree, rpm-ostree).
- **Undo logs / event sourcing** — the append-only JSONL manifest with snapshot dedup is a miniature event-sourcing store; the bounded compaction mirrors log-structured storage compaction.

### 4.2 Novelty Assessment
Nothing here rises to a publishable research contribution — and that is appropriate. The **drift-normalisation-by-managed-property** trick (Section 2.1) is the most original engineering idea and could make a short practitioner blog post / KDE-Visions talk, but not a paper. The combination (atomic + undo + drift-gated vendor patching for a DE) is novel as a *product* but not as *research*.

## 5. Actionable Roadmap

### Quick Wins (S)
| # | Description | Effort | Expected Impact | Risk |
|---|---|---|---|---|
| Q1 | **Fix the README test badge** `86 passed` → `106 passed` (or auto-generate it in CI). | S | Trust | Low |
| Q2 | **Remove `fonttools` from dev deps** (unused) or actually use it (e.g., to subset the bundled Inter for size). | S | Smaller dep tree | Low |
| Q3 | **Update `CHANGELOG.md` "Known limitations"** — Inter font is now bundled; remove the stale line. | S | Doc accuracy | Low |
| Q4 | **Add `clock_format` and `weight` validators** (regex for Qt date tokens; an enum/regex for CSS weight tokens) in `schema.py`. | S | Catches typos; matches the strict-config ethos | Low |
| Q5 | **Document the non-atomic direct-overwrite fallback** in `atomic.py` with a one-line caveat in the README "How it works" section. | S | Honest expectation-setting | Low |
| Q6 | **Fix `is_installed` substring match** — parse `fc-match` output precisely or use `fc-match --format` to avoid "Inter" matching "Inter Dimensional". | S | Correctness | Low |

### Medium Effort (M)
| # | Description | Effort | Expected Impact | Risk |
|---|---|---|---|---|
| M1 ★ | **Wire third-party provider entry-point loading OR remove it from the docs.** Add `pm.load_setuptools_entrypoints("usurface.providers")` in `make_plugin_manager()` (behind an opt-in to preserve the trust boundary) and add a test; alternatively, rewrite `providers/README.md` to say "built-in providers; plugin API is a roadmap item." **(Directly addresses the key-question gap.)** | M | Closes the headline feature gap / honesty | Medium |
| M2 | **Add a `file` provider size cap** (e.g., refuse > 100 MiB before decode) to avoid memory spikes on huge local images. | M | Resilience | Low |
| M3 | **Add an `@pytest.mark.integration` skeleton** (skipped without `DISPLAY`/`WAYLAND_DISPLAY`) that runs `usurface apply --dry-run` end-to-end against a real session. The marker already exists but is unused. | M | Catches real-Plasma regressions | Low |
| M4 | **Make SDDM/QML paths configurable** (override via config/env) so non-Breeze SDDM themes work. Currently hard-coded to `/usr/share/sddm/themes/breeze/`. | M | Portability | Low |
| M5 | **Replace hand-rolled `_to_toml` with `tomli_w`** (or pydantic-toml) to remove the serializer maintenance risk. | M | Maintainability | Low |
| M6 | **`usurface doctor` should print a clear note when third-party plugins are NOT loaded** (i.e., surface the M1 gap to users). | M | Discoverability | Low |

### Long-Term Bets (L)
| # | Description | Effort | Expected Impact | Risk |
|---|---|---|---|---|
| L1 | **Per-Plasma-minor template versioning** (deferred to v2 per CHANGELOG): ship pristine templates per Plasma release tag and auto-select, removing the dependence on re-extracting from the live system. | L | Survives Plasma upgrades with no manual consent | Medium |
| L2 | **A GUI** (explicitly a non-goal today): a tiny KRunner/plasmoid wrapper around the CLI for non-CLI users. | L | Wider audience | Medium |
| L3 | **Distribution packaging** (AUR + `.deb`) and a `systemd` sytem unit option for multi-user boxes. | L | Adoption | Low |
| L4 | **A provider allow-list config** so when M1 lands, users must explicitly opt into a third-party provider by name, keeping the supply-chain surface opt-in. | L | Security | Medium |

★ = directly addresses the key question.

## 6. Supplementary Documentation Verification

### 6.1 Feature-Implementation Gap Analysis

| Feature | Claimed in doc? | Found in code? | Score (/10) | Notes |
|---|---|---|:---:|---|
| Unified apply to desktop + lock + login | README, PLAN | ✅ `orchestrator.apply_to_surfaces` + 3 backends | 10 | Fully implemented and tested. |
| Atomic writes (tmp+fsync+replace) | README, PLAN C5 | ✅ `atomic.py` | 10 | Plus EXDEV/direct-overwrite fallback. |
| Append-only manifest + `restore` | README, PLAN C8 | ✅ `manifest.py` | 10 | With compaction + snapshot pruning. |
| Provider registry (bing/file/solid) | README, PLAN §7.5 | ✅ `providers/builtin/*` | 10 | All three work and are tested. |
| **Provider-extensible via `pluggy` plugins** | README headline, `providers/README.md`, PLAN §7.5 | ❌ entry-point loading NOT wired | **2** | Built-ins work; third-party plugins cannot load. Docs are misleading. |
| Safe QML patching with drift detection | README, PLAN | ✅ `theme/{qml_patch,drift,extract}.py` | 9 | Sentinel-comment + normalise-then-hash + consent gate. |
| `usurface install` (font + shared dir + timer) | README, migration doc | ✅ `cli.install` | 9 | Works; shared-dir creation can fail without sudo (handled with a message). |
| `usurface doctor` | README | ✅ `cli.doctor` | 9 | Reports drift, font, timer, config; exit code reflects hard problems. |
| `usurface pause`/`resume` | (added post-prior-audit) | ✅ `cli.pause/resume` + `systemd.pause/resume` | 9 | Runtime mask/unmask; `is_paused` symlink-aware. |
| `usurface migrate-from-shell` | docs/migration-from-shell.md | ✅ `cli.migrate_from_shell` | 6 | Only detects `bing-potd.sh` + the old timer; doesn't read the old theme.conf path or backup files. Minimal but functional. |
| `usurface apply --adopt-drift` | CHANGELOG Appendix B | ✅ `orchestrator` + `cli.apply` | 9 | Explicit consent path; re-runs patch after adopting stripped content. |
| Vendored Inter font | PLAN §3 (DECIDED) | ✅ `theme/fonts/Inter-Regular.ttf` (637 KB) | 8 | Present and found by `_bundled_font`; CHANGELOG still says it's missing. |
| `usurface qml-update-templates` | CHANGELOG, README | ✅ `cli.qml_update_templates` + `extract.extract` | 9 | Re-extracts stripped pristine from live vendor files. |
| Template versioning per Plasma minor release | CHANGELOG "deferred to v2" | ❌ (intentionally) | — | Honest deferral. |
| Jinja2-based systemd unit rendering | (prior `CODEBASE_ASSESSMENT.md` claim) | ❌ never used | — | Inline Python templates instead; prior audit was wrong. |

### 6.2 Version Drift Analysis
- `pyproject.toml`: `version = "0.1.0"`.
- `src/usurface/__init__.py`: `__version__ = "0.1.0"`.
- README badge: `python-3.12+`, `KDE-Plasma-6`, license `PolyForm Noncommercial`, tests `86 passed`.
- `CODEBASE_ASSESSMENT.md` (prior audit, dated 2026-07-01): version `0.0.0` (pre-release), `78 passed`, Jinja2 listed as a runtime dep, "No vendored Inter font bundled."

**Drift findings:**
- `0.1.0` is consistent across `pyproject.toml` and `__init__.py`. ✅
- README test badge (`86`) is **stale** vs. actual `106` — cosmetic but visible.
- `CODEBASE_ASSESSMENT.md` is significantly out of date (version `0.0.0`, `78 passed`, missing-dev-deps and missing-font findings that are since resolved). It should either be refreshed or marked as a historical snapshot to avoid confusing future readers.
- No version markers in the QML templates or systemd units (fine — they're generated).

## 7. Closing Remarks

This is a genuinely well-engineered little tool. For a codebase partly AI-assisted, the discipline on display is above par: atomic I/O with thoughtful fallbacks, an append-only undo log with dedup and bounded compaction, drift detection that refuses to silently adopt modified vendor files, and a consent gate (`--adopt-drift`) that respects the user as the trust root. The test suite is fast (1.5 s), green, and covers the dangerous paths; ruff and mypy are clean. The architecture is layered, the abstractions leak very little, and the `Backend`/`Provider` protocols make the seams obvious.

The one thing that would make me hesitate to call it "release-ready" as advertised is the **third-party provider plugin system**: the README and `providers/README.md` sell `pluggy` entry-point extensibility as a headline feature, and `make_plugin_manager()` never calls `load_setuptools_entrypoints`. That is the single highest-leverage fix — either wire it (behind an opt-in allow-list, since it's a supply-chain surface) or correct the docs to say built-ins only. Everything else is polish: stale CHANGELOG/test badge, two unvalidated config fields, a dead `fonttools` dep, and a fragile `fc-match` substring check.

Directly answering the key question: the codebase and its aims are coherent and the implementation is solid and trustworthy **where it exists** — but one marquee capability is currently documentation-only, and that gap should be closed or the marketing corrected before public release. Fix that, refresh the stale docs, and this is a tool I'd recommend to a KDE power user without apology.

## 9. Appendix A.4 — Remediation Audit (performed 2026-07-08)

### 9.1 Trigger
User report: desktop background remained the default KDE Neon image instead of the configured Bing Picture of the Day; the SDDM login screen showed the previous day's image, while the new image "flashed up after password correct". This pointed to a failure in the live desktop-wallpaper apply path and/or stale installed binary.

### 9.2 Root-cause findings
1. **Installed `uv tool` binary was stale.** The source tree contained the new `evaluateScript` / nested-containment code, but `/home/matt/.local/bin/usurface` (the binary the systemd user timer and the shell run) was an older `uv tool` install that still called the non-existent `org.kde.plasma.desktop /PlasmaShell refreshWallpaper` and did not write nested `[Containments][<id>][Wallpaper][org.kde.image][General] Image=` groups. This matches the known lesson in user memory about frozen `uv tool` copies vs. editable venvs.
2. **State files were root-owned from earlier `sudo` runs.** `/home/matt/.local/state/usurface/manifest.jsonl` and `/usr/local/share/wallpapers/last_wallpaper.jpg` were owned by `root`, causing the user-mode `usurface apply` to crash with `Permission denied` before completing.
3. **`sudo usurface apply` used `/root/.local/state/usurface/` instead of the invoking user's state dir.** `os.path.expanduser('~')` in `config.expand_behaviour_paths` expanded to `/root` under `sudo`, so a separate root-only manifest and canonical wallpaper were created.
4. **Live D-Bus calls from `sudo` targeted the root session bus, missing the user's Plasma services.** `qdbus6` therefore failed to find `org.kde.plasmashell` / `org.freedesktop.ScreenSaver`, so even after the nested config writes the wallpaper was not refreshed live.
5. **Dry-run plan printed syntactically invalid `--group Containments][1]...` arguments.** The actual `kwriteconfig6` invocation was correct, but the human-readable plan was misleading.
6. **Image extension was taken from the provider's suggestion, not the re-encoded bytes.** `verify_image` re-encodes to JPEG (or PNG for transparency), but `orchestrator.py` still named the file `last_wallpaper.webp` if the source was WebP, producing a mismatched file.

### 9.3 Remediation implemented
All changes were committed in `932212b`:
- `src/usurface/manifest.py`: `Manifest.append` now atomically rewrites the whole JSONL log via `atomic_write_bytes`, surviving root-owned files and giving a clear permission error on the real path.
- `src/usurface/backends/_kconfig.py`: added `_run_as_invoking_user()` helper that drops `qdbus6` calls to the `SUDO_USER` with the correct `XDG_RUNTIME_DIR` + `DBUS_SESSION_BUS_ADDRESS`, so live updates work from `sudo`.
- `src/usurface/backends/desktop.py`, `lock.py`: fixed `dry_run_plan` output to show the real repeated `--group` arguments.
- `src/usurface/orchestrator.py`:
  - Pre-flight check that `shared_dir` is writable, with an actionable hint.
  - Derives the output extension from the re-encoded bytes (JPEG/PNG), not the provider suggestion.
  - Restores ownership of the shared wallpaper file (and its parent directory) to the invoking user after a `sudo` write.
- `src/usurface/config.py`: `expand_behaviour_paths` now expands `~` to the invoking user's home when running under `sudo`, not `/root`.
- `src/usurface/paths.py`: added `invoking_user_uid_gid()` helper used by the orchestrator ownership restoration.
- `src/usurface/theme/qml_patch.py`: made the `fadeoutTimer` interval matcher tolerant of other properties between `id: fadeoutTimer` and `interval:`.
- `tests/test_providers.py`: fixed a type-error in `Source(options=...)` that `mypy` flagged.
- `CHANGELOG.md`: documented the A.4 fixes.

### 9.4 Operational steps executed
- Fixed ownership: `chown -R matt:matt /home/matt/.local/state/usurface /usr/local/share/wallpapers`.
- Re-installed the tool: `uv tool install . --force`.
- Ran `sudo /home/matt/.local/bin/usurface apply` once to update the SDDM login `theme.conf`; the new D-Bus drop-to-user logic allowed the live desktop and lock reload to succeed, and ownership was restored to `matt:matt` on the shared wallpaper file.

### 9.5 Verification
- `uv run pytest -q` → **109 passed**.
- `uv run ruff check src tests` → **All checks passed**.
- `uv run mypy src tests --ignore-missing-imports` → **Success: no issues found**.
- `usurface apply --dry-run` now prints correct nested `--group` arguments.
- `~/.config/plasma-org.kde.plasma.desktop-appletsrc` shows `[Containments][1][Wallpaper][org.kde.image][General] Image=file:///usr/local/share/wallpapers/last_wallpaper.jpg` and `[Containments][2][Wallpaper][org.kde.image][General] Image=...` correctly.

### 9.6 Residual recommendations
- Mark the old `CODEBASE_ASSESSMENT.md` as historical or refresh it; it contains stale version/test/dependency claims.
- Update the README test badge from `86 passed` to `109 passed` (or generate it in CI).
- Decide whether to wire third-party provider entry-point loading or update `providers/README.md` to say the feature is not yet available.

## 8. Template Self-Audit & Feedback

**What worked well:**
- The `key_question` directive kept the audit focused and gave a sharp edge (the plugin-gap discovery is the headline finding, exactly because the key question asked for a code-vs-aims reconciliation).
- The lightweight-audit option in `quick_start` is a useful escape hatch; I ran the full version because the codebase is small enough.
- The `planning_and_evidence_log` discipline caught the prior-audit's Jinja2 error before I repeated it.

**What was unclear / could improve:**
- The template assumes an LLM/agentic/edge-AI system in several criteria (token efficiency, arXiv multi-agent papers, "EU AI Act" compliance). For a non-LLM systems tool like this, those sections are forced fits. A `system_type` field at the top would let the template swap criterion sets (e.g., "systems tool" vs "LLM agent") instead of the auditor hand-waving "N/A".
- "Ethical & Legal Compliance" for a wallpaper manager is essentially nil-risk; the template should allow a one-line "not applicable — no PII, no inference, no dual-use" rather than forcing a subsection.
- `evaluation_criteria` lists `Cost Efficiency (Token & Hardware)` — splitting token-cost (LLM) from hardware-cost (systems) would help non-LLM audits.
- Step 7's "completeness score 0-10" is good; a rubric for the score (e.g., 0=stub, 5=partial, 10=fully tested) would make scoring more reproducible across auditors.
- The `quick_start` says "steps 1, 2, 6, 8" — but step 6 is "improvements" and step 8 is "synthesise"; a lightweight audit that skips the competitive/academic deep-dive (steps 3-5) would be even faster and is the more natural lightweight cut.

**Suggested template v2.2 changes:**
1. Add a `system_type` enum (`llm-agent` | `systems-tool` | `library` | `other`) to tailor criteria.
2. Make the arXiv/competitive sections conditional on `system_type != systems-tool`.
3. Provide a completeness-score rubric.
4. Add a `docs_freshness` sub-step to Step 9 (check whether supplementary docs are stale relative to code).