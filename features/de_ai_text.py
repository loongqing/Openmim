"""
去 AI 味后处理 — 对 LLM 输出做最后一道打磨。

检测和修复 AI 写作常见的模式：
- 过度使用 em dash（——）
- 总结式结尾（"总的来说""综上所述"）
- 反问征求同意（"你觉得呢""需要我帮你吗"）
- 条列式逻辑连接词过度
- 反复使用相同的句式
"""
from __future__ import annotations

import re
import random


# ── 模式 → 替换 ──────────────────────────────

_REPLACEMENTS: list[tuple[str, str | callable]] = [
    # 1. 两三连 em dash → 逗号或删除
    (r'(?<!\n)——(?!\n)', lambda m: '，' if random.random() < 0.7 else '——'),
    
    # 2. 总结式结尾
    (r'总的来说[，,]?\s*', ''),
    (r'综上所述[，,]?\s*', ''),
    (r'总结一下[：:，,]?\s*', ''),
    (r'简而言之[，,]?\s*', ''),
    
    # 3. 反问征求同意（结尾）
    (r'(你觉得呢[？?]?|你要不要试试[？?]?|需要我[\u4e00-\u9fff]{1,5}吗[？?]?)\s*$', ''),
    
    # 4. "值得注意的是""需要指出的是" → 删除
    (r'值得注意的是[，,]?\s*', ''),
    (r'需要指出的是[，,]?\s*', ''),
    (r'值得一提的是[，,]?\s*', ''),
    
    # 5. "首先...其次...最后..." → 精简
    (r'^首先[，,]', lambda m: '' if random.random() < 0.5 else m.group()),
    (r'其次[，,]', lambda m: '然后' if random.random() < 0.5 else m.group()),
    (r'最后[，,]', lambda m: '' if random.random() < 0.5 else m.group()),
    
    # 6. "这不仅...而且..." → 简化
    (r'这不仅[仅只]?', lambda m: '这' if random.random() < 0.6 else m.group()),
    
    # 7. 过度"从而"
    (r'，从而', lambda m: '，' if random.random() < 0.5 else '，从而'),
    
    # 8. "基于上述" "根据以上"
    (r'基于上述[\u4e00-\u9fff]*[，,]\s*', ''),
    (r'根据以上[\u4e00-\u9fff]*[，,]\s*', ''),
    
    # 9. AI 高频词汇替换
    (r'至关重要', lambda m: random.choice(['很重要', '特别重要', '关键'])),
    (r'不可或缺', lambda m: random.choice(['少不了', '必需', '不能没有'])),
    (r'彰显', lambda m: random.choice(['展现', '体现', '显示'])),
    (r'瑰宝', lambda m: random.choice(['宝藏', '珍贵的东西'])),
    
    # 10. 过度波浪线和感叹号
    (r'！{2,}', '！'),
    (r'～{2,}', '～'),
    (r'喵{3,}', '喵喵'),
    
    # 11. 连续重复句子（去重）
    # 由 chat_handler 的 _dedupe_trailing_repeat 处理
]


def de_ai(text: str) -> str:
    """
    后处理 LLM 输出，去掉 AI 写作痕迹。
    
    不改变语义，只调整表达方式使其更自然。
    """
    if not text or len(text) < 5:
        return text
    
    result = text
    
    for pattern, replacement in _REPLACEMENTS:
        if callable(replacement):
            result = re.sub(pattern, replacement, result)
        else:
            result = re.sub(pattern, replacement, result)
    
    # 去首尾空白
    result = result.strip()
    
    # 如果处理后变成空字符串，返回原文
    if not result:
        return text.strip()
    
    return result


def de_ai_light(text: str) -> str:
    """
    轻量版：只处理最明显的 AI 痕迹，更保守。
    用于不想改变太多原始内容的场景。
    """
    if not text or len(text) < 5:
        return text
    
    result = text
    
    # 只处理最明显的模式
    light_patterns = [
        (r'总的来说[，,]?\s*', ''),
        (r'综上所述[，,]?\s*', ''),
        (r'(你觉得呢[？?]?)\s*$', ''),
        (r'！{2,}', '！'),
        (r'～{2,}', '～'),
        (r'喵{3,}', '喵喵'),
    ]
    
    for pattern, replacement in light_patterns:
        result = re.sub(pattern, replacement, result)
    
    result = result.strip()
    return result if result else text.strip()
