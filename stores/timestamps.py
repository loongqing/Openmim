"""统一时间戳辅助函数。

各 store 曾各自定义 _now()，且 UTC 与 CST 混用。统一收敛到此处：
- utc_now_iso(): UTC 时间的 ISO 字符串（数据库时间戳的默认格式）
- cst_now_iso(): 东八区时间的 ISO 字符串
- cst_now(): 东八区时间的 datetime 对象（用于时差/小时计算）
"""

from datetime import datetime, timezone, timedelta

_CST = timezone(timedelta(hours=8))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cst_now_iso() -> str:
    return datetime.now(_CST).isoformat()


def cst_now() -> datetime:
    return datetime.now(_CST)
