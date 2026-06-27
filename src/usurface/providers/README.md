# Adding a third-party wallpaper provider

A provider is a Python package that exposes a ``usurface.providers``
entry point. The entry point object must implement the three hooks
declared in ``usurface.providers.ProviderHooks``.

## Minimal example

```python
# my_provider/__init__.py
from usurface.providers import (
    FetchedImage, ProviderInfo, hookimpl,
)

class MyProvider:
    @hookimpl
    def usurface_provider_name(self) -> str:
        return "my-provider"

    @hookimpl
    def usurface_provider_info(self) -> ProviderInfo:
        return ProviderInfo(
            name="my-provider",
            description="My custom wallpaper source.",
            builtin=False,
        )

    @hookimpl
    def usurface_provider_fetch(self, options):
        # Return JPEG/PNG bytes.
        data = ...
        return FetchedImage(
            data=data,
            content_type="image/jpeg",
            suggested_extension=".jpg",
        )

plugin = MyProvider()
```

And in ``pyproject.toml``:

```toml
[project.entry-points."usurface.providers"]
my-provider = "my_provider:plugin"
```

The user then selects your provider via ``[surface.source] provider =
"my-provider"`` in their ``config.toml``.

## Security

Third-party providers run as the invoking user and have full access to
network resources. Treat the ``usurface.providers`` entry-point group
as a supply-chain surface and only install providers you trust.
