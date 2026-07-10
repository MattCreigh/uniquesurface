"""Canary tests: assert every QML patch descriptor still matches upstream.

This module is excluded from the normal ``pytest`` run (it requires
upstream files fetched by the canary CI workflow) and runs only in
the ``Upstream Canary`` GitHub Actions workflow.  A failure here is
a red badge, not a release blocker — it surfaces an upcoming QML
breakage so the maintainers can add a new descriptor file with a
higher Plasma version floor before the release lands.

Test scope
==========

* For each ``descriptor`` in ``src/trinity/theme/descriptors/*.toml``
  we fetch the upstream raw QML file (KDE invent for plasma-workspace
  and the SDDM breeze theme) and assert:
    - every ``anchor`` pattern in the descriptor matches the upstream
      file content (i.e. the anchor we patch against still exists);
    - every ``remove_anchor`` pattern matches an *inserted* guard
      block when one is constructed from the descriptor's
      ``insert_block`` template, so a future re-apply with
      ``enable=false`` can still find the block to remove.

The tests do NOT run if the upstream files are missing (the local
``pytest`` run skips this directory — see ``pyproject.toml``'s
``testpaths``).
"""
