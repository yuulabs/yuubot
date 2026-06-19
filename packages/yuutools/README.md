# yuutools

Explicit, async-first tool framework for LLM agents.

## Install

```sh
uv add yuutools
```

## Usage

```python
import yuutools as yt

def get_z(ctx) -> int:
    return ctx.multiplier

@yt.tool(
    params={"x": "first operand", "y": "list of ints to sum"},
    name="goodtool",
    description="Add x + sum(y) + z",
)
async def goodtool(x: int, y: list[int], z: int = yt.depends(get_z)):
    return x + sum(y) + z

# Registry
manager = yt.ToolManager([goodtool])

tool = manager["goodtool"]
print(tool.spec.to("json_schema"))

# Bind context and run
result = await tool.bind(ctx).run(x=1, y=[2, 3])
```

### `@yt.tool`

Decorator that turns a function into a `Tool`. Parameters:

- `params` — `{name: description}` mapping for the LLM schema
- `name` — override tool name (default: `__name__`)
- `description` — tool description (default: first docstring line)

### `yt.depends(resolver)`

Marks a parameter as context-injected. The resolver is called with the
bound context at execution time. Hidden from the generated spec.

### `ToolSpec.to(fmt)`

Serialize to `"json_schema"` or `"yaml"` (requires `yuutools[yaml]`).

### `ToolManager`

Name-keyed registry with `[]` lookup, iteration, and `.specs()` for
bulk JSON schema export.

## License

MIT
