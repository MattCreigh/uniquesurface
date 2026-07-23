"""Pydantic models for QML patch descriptor data files.

The QML anchors that trinity's patching machinery depends on
(managed property names, the fadeout-timer regex, the wake-guard
regex) are no longer hard-coded in Python.  They live in TOML data
files under ``src/trinity/theme/descriptors/``, keyed by target file
and a Plasma version range.  This module is the schema the loader
validates those files against; a malformed descriptor is a bug and
must fail loudly at module load time.

Why this layer exists
=====================

The previous design compiled the regexes and the managed-property
list directly into ``qml_patch.py`` and ``drift.py``.  This made
adding support for a new vendor QML layout (e.g. a future Plasma
release that renames a property) a Python change.  With descriptors:

* Adding a new layout for a *new* vendor file = adding a new TOML.
* Dropping support for an *old* layout = removing a TOML.
* Changing an anchor regex = editing the TOML, with full version-range
  guardrails on which Plasma releases the change applies to.
* A "canary" CI job (see ``.github/workflows/upstream-canary.yml``)
  fetches the upstream QML files trinity patches and asserts the
  descriptors still match; the canary's failure is a red badge, not a
  release blocker, but it surfaces the upcoming breakage early.

Version-range semantics
=======================

``plasma = ">=6.0,<6.8"`` follows PEP 440 syntax (delegated to
:mod:`packaging.specifiers`).  A descriptor with no ``plasma`` key
applies to *all* Plasma versions (a fallback for hand-rolled QML).
``include`` / ``exclude`` lists further restrict the match: a
descriptor is selected if its ``plasma`` specifier matches the
detected Plasma version AND the version is in ``include`` (or
``include`` is empty) AND the version is not in ``exclude``.  This
lets a future release add a "deprecate in 6.6, remove in 6.7" path
without code changes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# PEP 440 version-range expression; ``packaging.specifiers`` parses
# it.  We allow a deliberate empty string ("applies to all Plasma
# versions") by typing it as ``str`` and treating ``""`` as "always".
_PLASMA_RANGE_TYPE = str


class FontProperty(BaseModel):
    """A QML ``property string <name>: "<value>"`` declaration trinity
    rewrites.  ``name`` is the property identifier; ``default`` is the
    vendor's shipped value (informational, used in ``provider info``
    output only); ``description`` is human-readable."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    default: str = Field(default="", max_length=256)
    description: str = Field(default="", max_length=512)


class Anchor(BaseModel):
    """A named anchor in a QML file: a regex pattern + a brief
    description.  The pattern is matched against the QML source with
    the standard :mod:`re` engine.  All patterns in this file are
    pure and side-effect free; we do not load arbitrary code from TOML.
    """

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(
        min_length=1,
        description="Python regular expression (no flags).",
    )
    description: str = Field(default="", max_length=512)
    # ``flags`` is a list of single-letter flag names (e.g. ``["DOTALL"]``)
    # that we translate to :mod:`re` flags.  Restricting to a known set
    # prevents descriptor authors from accidentally enabling ``VERBOSE``
    # (which changes the pattern syntax) or other surprising flags.
    flags: list[str] = Field(
        default_factory=list,
        description=(
            "Optional regex flags as a list of names: "
            "'DOTALL', 'MULTILINE', 'IGNORECASE', 'UNICODE'."
        ),
    )

    @field_validator("flags")
    @classmethod
    def _validate_flags(cls, v: list[str]) -> list[str]:
        allowed = {"DOTALL", "MULTILINE", "IGNORECASE", "UNICODE"}
        bad = [f for f in v if f not in allowed]
        if bad:
            raise ValueError(f"unknown regex flags {bad!r}; allowed: {sorted(allowed)}")
        return v

    def compile(self) -> Any:  # returns compiled re.Pattern
        """Compile the pattern with the configured flags."""
        flag = 0
        for name in self.flags:
            flag |= getattr(__import__("re"), name)
        return __import__("re").compile(self.pattern, flag)


class ManagedPatch(BaseModel):
    """A category of managed edit.  Three sub-types share this shape:

    * ``font_property`` — rewrite a QML property value in place.
      ``value_pattern`` is the regex used to find the declaration; the
      loader builds a single combined regex that matches *any* of the
      managed properties so the apply path stays simple.
    * ``fadeout_timer`` — rewrite a numeric interval in a Timer block.
    * ``wake_guard`` — insert/remove a keypress guard in a handler.

    ``kind`` discriminates.  We keep this on one model (rather than
    separate subclasses) because the apply path in ``qml_patch.py``
    branches on kind and reads the relevant fields; splitting would
    force the apply path to do ``isinstance`` checks that mirror the
    schema.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        description=("One of 'font_property', 'fadeout_timer', 'wake_guard'."),
    )
    description: str = Field(default="", max_length=512)
    # font_property-only: the properties that count as "managed" for
    # drift normalisation.  Ignored for other kinds.
    font_properties: list[FontProperty] = Field(default_factory=list)
    # fadeout_timer-only: the regex that locates the anchor.  Ignored
    # for font_property.
    anchor: Anchor | None = None
    # fadeout_timer-only: the replacement template (``\g<1>{value}``
    # style).  The apply path substitutes the actual ms value at apply
    # time.
    value_template: str = Field(default="", max_length=256)
    # wake_guard-only: the regex that matches the *inserted* guard
    # block, so a re-apply with ``enable=False`` can remove it.
    remove_anchor: Anchor | None = None
    # wake_guard-only: the literal block (no interpolation needed) that
    # the apply path inserts.  Kept as a string because the indent is
    # significant and authoring it as a TOML literal is the cleanest
    # way to preserve whitespace.  ``{indent}`` in the block is
    # substituted at apply time with the indentation of the anchor
    # line.
    insert_block: str = Field(default="", max_length=4096)

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        if v not in ("font_property", "fadeout_timer", "wake_guard"):
            raise ValueError(
                f"unknown managed-patch kind {v!r}; expected "
                f"'font_property', 'fadeout_timer', or 'wake_guard'"
            )
        return v

    def kind_specific_validate(self) -> None:
        """Cross-field validation: each kind requires its own fields."""
        if self.kind == "font_property":
            if not self.font_properties:
                raise ValueError(
                    "font_property patch requires at least one entry "
                    "in 'font_properties'"
                )
        elif self.kind == "fadeout_timer":
            if self.anchor is None:
                raise ValueError("fadeout_timer patch requires an 'anchor' regex")
            if not self.value_template:
                raise ValueError("fadeout_timer patch requires a 'value_template'")
        elif self.kind == "wake_guard":
            if self.anchor is None:
                raise ValueError("wake_guard patch requires an 'anchor' regex")
            if self.remove_anchor is None:
                raise ValueError("wake_guard patch requires a 'remove_anchor' regex")
            if not self.insert_block:
                raise ValueError("wake_guard patch requires an 'insert_block'")


class QmlDescriptor(BaseModel):
    """A single QML patch descriptor: target file + Plasma range +
    managed patches.

    The loader picks the *first* descriptor for a given ``name`` whose
    Plasma range matches the detected runtime version, so authors
    should list most-specific-first.  A descriptor with no ``plasma``
    key (or an empty string) is treated as a wildcard fallback.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=128,
        description=("Logical name matching the entry in extract.DEFAULT_TARGETS."),
    )
    description: str = Field(default="", max_length=512)
    plasma: _PLASMA_RANGE_TYPE = Field(
        default="",
        description=(
            "PEP 440 version-range expression, e.g. '>=6.0,<6.8'.  "
            "Empty means 'applies to all Plasma versions'."
        ),
    )
    include: list[str] = Field(
        default_factory=list,
        description=(
            "Optional explicit list of exact Plasma versions to include "
            "(applied in addition to 'plasma')."
        ),
    )
    exclude: list[str] = Field(
        default_factory=list,
        description=("Optional list of exact Plasma versions to exclude."),
    )
    clock_id: str = Field(
        default="clock",
        description=("The QML id of the clock item to apply position edits to."),
    )
    patches: list[ManagedPatch] = Field(
        default_factory=list,
        description=(
            "The managed edits for this target file.  An empty list "
            "means 'no managed edits apply' — drift detection still "
            "runs against the stored pristine, but the apply path is "
            "a no-op."
        ),
    )

    def post_validate(self) -> None:
        """Cross-field validation: enforce kind-specific requirements
        after the model is constructed (Pydantic doesn't call
        ``model_validators`` per-sub-model, so we expose this and the
        loader calls it)."""
        for p in self.patches:
            p.kind_specific_validate()
