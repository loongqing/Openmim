"""可玩性共享工具 — 问候语与文案 helpers。

猜图游戏见 features/history_guess.py，每日运势见 features/fortune.py，
复读检测见 features/repetition.py。
"""
import logging
import random

from app_config.customization import get_dict, get_list, get_text

logger = logging.getLogger(__name__)


def _play_text(key: str, default: str) -> str:
    return get_text(f"playables.{key}", default)


def _play_list(key: str, default: list[str]) -> list[str]:
    values = get_list(f"playables.{key}", default)
    strings = [str(v) for v in values if str(v)]
    return strings or list(default)


def _play_dict(key: str, default: dict) -> dict:
    return get_dict(f"playables.{key}", default)


async def send_greeting(bot, chat_id: int, is_morning: bool = True):
    morning_defaults = [
        "早安喵～太阳公公都晒到尾巴尖了，快起床伸个懒腰，新的一天喵呜！",
        "早哇～先喝口水润润嗓子，别一睁眼就扎进屏幕里把自己卷晕了喵～",
        "早安喵～今天也慢慢来，像猫猫晒太阳一样悠悠闲闲，别把自己折腾坏啦！",
        "唔…睁开眼睛啦喵？新的一天小鱼干在等你呢，快打起精神喵～（揉揉眼睛）",
        "早安安喵～窗户外面小鸟都在唱歌了，咱也抖抖耳朵开始元气满满的一天吧！",
    ]
    evening_defaults = [
        "晚安喵～差不多该让脑袋和尾巴一起歇着啦，蜷成毛茸茸一团睡觉觉～",
        "夜深了喵～别再跟屏幕大眼瞪小眼了，对眼睛不好，对猫猫也不好！",
        "晚安喵～今天辛苦啦，快去洗个香香、钻进软乎乎的被窝，呼噜呼噜～",
        "呜…眼皮打架了喵…月亮都挂老高了，一起闭上眼睛数小羊好不好喵～",
        "晚安安喵～今天不管顺不顺，都值得好好休息，咱用尾巴给你盖被子！（轻轻蹭蹭）",
    ]
    text = random.choice(_play_list("greetings.morning", morning_defaults) if is_morning else _play_list("greetings.evening", evening_defaults))
    await bot.send_message(chat_id=chat_id, text=text)

