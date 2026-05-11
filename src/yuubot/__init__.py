"""yuubot v2 core package.

Import concrete entrypoints from their owning modules. Keeping package import
side-effect free prevents low-level resource imports from loading process
assembly code.
"""

__all__: list[str] = []
