from dataclasses import dataclass
from typing import Any

from app.runtime_config import RuntimeConfig


@dataclass
class AppContext:
    """轻量应用上下文。

    第一阶段先只承载关键共享依赖，避免继续使用分散的模块级全局状态。
    后续可以逐步扩展 repository / service 实例。
    """

    runtime_config: RuntimeConfig
    context_mgr: Any
    sticker_mgr: Any
    whitelist: set
    build_info: Any | None = None
    plugin_manager: Any | None = None
    application: Any | None = None
