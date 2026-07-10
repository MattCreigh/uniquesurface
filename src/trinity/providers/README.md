# Adding a third-party wallpaper provider

A provider is a Python package that exposes a ``trinity.providers``
entry point. The entry point object must implement the hooks declared
in ``trinity.providers.ProviderHooks``.

## Hooks

| Hook | Required | Description |
|------|----------|-------------|
| `trinity_provider_name` | Yes | Short provider name (matches `[surface.source].provider`). |
| `trinity_provider_info` | Yes | `ProviderInfo` metadata (name, description, builtin). |
| `trinity_provider_fetch` | Yes | Fetch/generate the image; return `FetchedImage`. |
| `trinity_provider_options_schema` | No | Return a pydantic `BaseModel` class validating the provider's options. If omitted, options are not validated (permissive fallback). |

## Option schema (recommended)

Declare a pydantic model with `extra="forbid"` so option typos are
caught at `config validate` time rather than at fetch time:

```python
from pydantic import BaseModel, ConfigDict, Field

class MyProviderOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(description="The image URL to fetch.")
    timeout: float = Field(default=30, gt=0, le=300)
```

Return it from the hook:

```python
@hookimpl
def trinity_provider_options_schema(self):
    return MyProviderOptions
```

`trinity provider info <name>` will auto-render an option table from the
model's fields. If the hook is not implemented, a warning is logged and
options are passed through unvalidated (backward compatible).

## Minimal example

```python
# my_provider/__init__.py
from pydantic import BaseModel, ConfigDict, Field

from trinity.providers import (
    FetchedImage, ProviderInfo, hookimpl,
)

class MyProviderOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(description="The image URL to fetch.")

class MyProvider:
    @hookimpl
    def trinity_provider_name(self) -> str:
        return "my-provider"

    @hookimpl
    def trinity_provider_info(self) -> ProviderInfo:
        return ProviderInfo(
            name="my-provider",
            description="My custom wallpaper source.",
            builtin=False,
        )

    @hookimpl
    def trinity_provider_options_schema(self):
        return MyProviderOptions

    @hookimpl
    def trinity_provider_fetch(self, options):
        # options is pre-validated against MyProviderOptions.
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
[project.entry-points."trinity.providers"]
my-provider = "my_provider:plugin"
```

The user then selects your provider via ``[surface.source] provider =
"my-provider"`` in their ``config.toml``.

## Security

Third-party providers run as the invoking user and have full access to
network resources. Treat the ``trinity.providers`` entry-point group
as a supply-chain surface and only install providers you trust.
