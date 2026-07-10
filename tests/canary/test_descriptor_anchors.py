"""Canary tests for QML descriptor anchors against upstream QML files.

Run via the ``Upstream Canary`` GitHub Actions workflow; see
``.github/workflows/upstream-canary.yml``.  Skipped locally (no
upstream files available) so the normal ``pytest`` run ignores them.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trinity.theme.descriptors import _load_all, _reset_cache_for_tests

_UPSTREAM_DIR_ENV = "CANARY_UPSTREAM_DIR"


def _upstream_dir() -> Path | None:
    """Return the directory holding the fetched upstream QML files,
    or ``None`` when the env var is unset (local dev run → skip)."""
    raw = os.environ.get(_UPSTREAM_DIR_ENV)
    if not raw:
        return None
    p = Path(raw)
    if not p.is_dir():
        return None
    return p


def _upstream_file_path(name: str) -> Path | None:
    """Return the upstream file path for a descriptor ``name``.

    Convention: ``canary/upstream/<name>.qml``.  Returns ``None`` if
    the directory or file is absent (the canary workflow's fetch step
    failed for that file; we skip rather than fail so the canary
    workflow doesn't report false alarms when the upstream URL
    itself changed).
    """
    d = _upstream_dir()
    if d is None:
        return None
    # The fetch step writes ``sddm_login.qml``, ``plasma_lockscreen_ui.qml``,
    # etc. — the descriptor ``name`` directly maps to the file name.
    f = d / f"{name}.qml"
    return f if f.is_file() else None


pytestmark = pytest.mark.skipif(
    _upstream_dir() is None,
    reason=(
        "canary tests require upstream QML files; run via the "
        "Upstream Canary GitHub Actions workflow"
    ),
)


@pytest.fixture(autouse=True)
def _reset_descriptor_cache() -> None:
    """Each canary test sees a freshly-cached descriptor set."""
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


def test_all_descriptors_have_upstream_files() -> None:
    """Every packaged descriptor has a corresponding upstream QML file."""
    d = _upstream_dir()
    assert d is not None, "canary env var not set"
    missing: list[str] = []
    for descriptor in _load_all():
        path = _upstream_file_path(descriptor.name)
        if path is None:
            missing.append(descriptor.name)
    assert not missing, (
        f"descriptors without upstream files: {missing}; the canary "
        "fetch step needs updating"
    )


def _descriptor_by_name(name: str):
    for d in _load_all():
        if d.name == name:
            return d
    raise AssertionError(f"no descriptor named {name!r}")


@pytest.mark.parametrize(
    "name",
    [
        "sddm_login",
        "plasma_lockscreen_mainblock",
        "plasma_lockscreen_ui",
    ],
)
def test_descriptor_anchors_match_upstream(name: str) -> None:
    """Each descriptor's anchor regex matches the upstream QML file."""
    path = _upstream_file_path(name)
    if path is None:
        pytest.skip(f"upstream file missing for {name!r}")
    text = path.read_text(encoding="utf-8", errors="replace")
    descriptor = _descriptor_by_name(name)
    for patch in descriptor.patches:
        if patch.anchor is None:
            continue
        pattern = patch.anchor.compile()
        m = pattern.search(text)
        assert m is not None, (
            f"anchor pattern for patch {patch.kind!r} in {name!r} did "
            f"not match upstream {path.name}; the upstream QML layout "
            "has changed — add a new descriptor file with a higher "
            "plasma version floor"
        )


def test_wake_guard_remove_anchor_matches_inserted_block() -> None:
    """The wake_guard remove_anchor matches a guard block built from
    the descriptor's insert_block, so a future re-apply with
    ``enable=false`` can still find and remove the block.

    This guards against an update that breaks the symmetry between
    the insert template and the remove pattern.
    """
    name = "plasma_lockscreen_mainblock"
    descriptor = _descriptor_by_name(name)
    path = _upstream_file_path(name)
    if path is None:
        pytest.skip(f"upstream file missing for {name!r}")
    text = path.read_text(encoding="utf-8", errors="replace")
    for patch in descriptor.patches:
        if patch.kind != "wake_guard" or patch.anchor is None:
            continue
        anchor_pat = patch.anchor.compile()
        match = anchor_pat.search(text)
        if match is None:
            pytest.skip("anchor absent upstream; covered by other tests")
        # group(2) is the indent capture from the descriptor's anchor
        # pattern.  If the pattern doesn't have a group 2, fall back
        # to empty-string indentation so the test still runs.
        indent = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
        block = patch.insert_block.replace("{indent}", indent)
        assert patch.remove_anchor is not None
        remove_pat = patch.remove_anchor.compile()
        inserted = text[: match.end(1)] + block + text[match.end(1) :]
        assert remove_pat.search(inserted) is not None, (
            "remove_anchor pattern failed to match a guard block built "
            "from insert_block — the descriptor is internally "
            "inconsistent"
        )
