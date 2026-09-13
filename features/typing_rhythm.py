"""
真人打字节奏模型 — 模拟真人阅读消息、思考、打字的节奏。

不要用固定的 random delay。真人的打字节奏取决于：
- 消息长度（长消息需要更久阅读和打字）
- 情绪状态（困了打得慢，兴奋打得快）
- 思考停顿（复杂问题中间会有停顿）
- 反应时间（看到消息后不是马上回复）
"""
from __future__ import annotations

import random


def human_reaction_delay(
    text: str,
    energy: float = 1.0,
    is_complex: bool = False,
) -> float:
    """
    计算从"看到消息"到"发出回复"的总延迟。
    
    人在聊天时不是打字机，而是：
    1. 看到消息 → 反应时间（0.5-3秒）
    2. 思考措辞 → 思考时间（随复杂度变化）
    3. 打字 → 字数 × 每字时间
    4. 检查 → 发送前停顿（0.2-0.8秒）
    
    返回总秒数，最大不超过 6 秒（太长反而像卡了）。
    """
    char_count = len(text) if text else 0

    # 1. 反应时间：看到消息后的"啊让我想想"时间
    reaction_base = 0.6
    if is_complex:
        reaction_base += random.uniform(0.5, 1.5)
    reaction = reaction_base + random.uniform(0.2, 0.8)

    # 2. 思考时间：复杂问题需要更多思考停顿
    think_pauses = 0
    if is_complex or char_count > 60:
        think_pauses = random.uniform(0.5, 1.5)
    if "？" in text or "?" in text:
        think_pauses += random.uniform(0.2, 0.5)

    # 3. 打字时间：中文打字约 50 字 / 分钟，但我们模拟稍微快一点
    # 约 80ms/字 = 750 字/分钟（手机打字偏快水平）
    base_speed = 0.08  # 每字 80ms

    # 精力修正
    if energy < 0.3:
        base_speed *= 2.5   # 困的时候打字慢
    elif energy < 0.5:
        base_speed *= 1.8
    elif energy < 0.7:
        base_speed *= 1.3

    typing_time = char_count * base_speed

    # 长消息中间有自然停顿（每 25 字可能停下来想一下）
    natural_pauses = char_count // 25
    typing_time += natural_pauses * random.uniform(0.3, 0.8)

    # 4. 发送前检查：看了一眼再按发送
    final_check = random.uniform(0.2, 0.6)

    total = reaction + think_pauses + typing_time + final_check

    # 上限：太长的延迟反而不自然
    return min(total, 6.0)


def segment_delay(segment_index: int, segment_count: int, segment_text: str) -> float:
    """
    Bot 分条发送时，每条之间的间隔。

    人发多条消息时：
    - 第1条和第2条之间间隔最长（"想到更多要说的"）
    - 后面的越来越短（思路连贯了）
    - 短消息间隔短，长消息间隔长
    """
    if segment_count <= 1:
        return 0.0

    # 基础间隔
    if segment_index == 0:
        # 第1条和第2条之间：1-3秒
        base = random.uniform(1.0, 3.0)
    elif segment_index >= segment_count - 1:
        # 最后一条不需要等
        return 0.0
    else:
        # 中间：0.5-1.5秒
        base = random.uniform(0.5, 1.5)

    # 上一条消息越长，间隔越短（因为打完了才发）
    char_count = len(segment_text) if segment_text else 0
    if char_count > 50:
        base *= 0.6

    # 加一点随机抖动
    jitter = random.uniform(-0.2, 0.3)

    return max(0.3, base + jitter)
