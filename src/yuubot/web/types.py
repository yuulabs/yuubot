from collections.abc import Awaitable, Callable
from pathlib import Path

from ..app import Yuubot

AppLoader = Callable[[Path], Awaitable[Yuubot]]
WSCommandSend = Callable[[dict[str, object]], Awaitable[None]]
