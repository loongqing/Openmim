"""复读检测 — check_repetition()。

从 features/playables.py 拆分而来。
"""
import random
from typing import Optional

from features.playables import _play_list

_last_texts: dict[str, tuple[str, int, str]] = {}


def check_repetition(chat_id: str, text: str, user_id: str) -> Optional[str]:
    if not text or len(text) < 2:
        return None
    text_hash = __import__("hashlib").md5(text.strip().lower().encode()).hexdigest()[:16]
    key = f"{chat_id}:{text_hash}"
    entry = _last_texts.get(key)
    if entry:
        _, count, _last_user = entry
        new_count = count + 1
        _last_texts[key] = (text_hash, new_count, user_id)
        if 3 <= new_count <= 20 and new_count % 2 == 1:
            return random.choice(_play_list("repeater.responses", [
                "复读机成精了是吧？",
                "禁止复读！…好吧我也来一个",
                "你们搁这接龙呢？",
                "够了够了，咱耳朵都听出茧子啦！",
            ]))
    else:
        _last_texts[key] = (text_hash, 1, user_id)
        if len(_last_texts) > 100:
            old_keys = list(_last_texts.keys())[:-50]
            for k in old_keys:
                _last_texts.pop(k, None)
    return None


