"""
Business 已读乱回模式 —— 集成 synonym-bot 的流水线逻辑。

流水线：输入文本 → LLM 写3句短文 → 分词 → 盲批近义词 → 拼接 → 输出
支持可选的失语症后处理（音素替换、删虚词、颠倒、重复）。

与独立 synonym-bot 的区别：
  - 使用 per-user 自定义 LLM 配置（而非全局 DeepSeek Key）
  - 去掉 "via @xxxbot" 后缀
  - 不需要 inline mode / command 模式
"""
import asyncio
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app_config.customization import get_text
from llm.provider_protocols import build_request, normalize_provider, parse_response, provider_endpoint, provider_headers

logger = logging.getLogger("BusinessSynonym")

# ── 失语症：音素替换映射 ──────────────────────
_PHONETIC_MAP: dict[str, str] = {
    "春": "村", "天": "田", "花": "华", "风": "封", "水": "谁", "山": "三",
    "日": "四", "月": "越", "星": "心", "云": "运", "雨": "于", "雪": "写",
    "草": "早", "树": "数", "鸟": "你", "鱼": "于", "猫": "毛", "狗": "够",
    "人": "仁", "我": "窝", "你": "泥", "他": "它", "她": "塔",
    "大": "达", "小": "晓", "高": "糕", "低": "滴", "快": "块", "慢": "满",
    "好": "号", "坏": "怀", "美": "没", "丑": "愁", "新": "心", "旧": "九",
    "走": "奏", "跑": "泡", "飞": "非", "跳": "条", "坐": "做", "站": "战",
    "看": "砍", "听": "停", "说": "缩", "吃": "迟", "喝": "河", "睡": "水",
    "想": "像", "爱": "矮", "恨": "很", "笑": "小", "哭": "苦",
    "红": "宏", "绿": "律", "蓝": "兰", "白": "百", "黑": "嘿",
    "一": "衣", "二": "耳", "三": "伞", "四": "丝", "五": "无",
    "是": "四", "的": "得", "了": "啦", "在": "再", "和": "合",
    "有": "又", "不": "步", "这": "者", "那": "拿", "就": "旧",
    "会": "回", "能": "嫩", "要": "摇", "可": "科", "以": "一",
    "生": "声", "活": "火", "来": "赖", "去": "取", "上": "尚", "下": "夏",
    "开": "凯", "关": "管", "进": "近", "出": "初", "回": "会", "过": "国",
}

_PARTICLE_SET: set[str] = {
    "的", "了", "着", "在", "和", "与", "地", "得",
    "很", "都", "也", "就", "才", "又", "还",
    "把", "被", "让", "从", "对",
}

# ── Prompt 模板 ──────────────────────────────

PROMPT_GENERATE_PARAGRAPH = """请以「{keyword}」为主题，写三句话的中文短文。
要求：
- 每句话尽量简短，整体控制在 80 字以内
- 语言自然流畅，不要写得像 AI 生成的
- 只输出三句话，不要加任何前缀、后缀、标题或解释"""

PROMPT_TOKENIZE_ONLY = """请将以下中文文本逐词切分，保留标点符号。

要求：
- 按顺序切分每个词和标点符号
- 用逗号分隔，输出一行
- 不要添加任何解释

示例：
输入："我喜欢春天。"
输出：我,喜欢,春天,。

文本：
{paragraph}"""

PROMPT_BLIND_SYNONYMS = """请为以下每个词给出一个近义词。

要求：
- 忽略这些词之间的关系，独立为每个词选近义词
- 不要考虑这些词是否来自同一句话
- 每行格式：原词 -> 近义词
- 不要添加编号、不要解释

词列表：
{word_list}"""

# ── 数据结构 ──────────────────────────────────

@dataclass
class TokenWithSynonym:
    token: str
    synonym: Optional[str] = None

    @property
    def is_punctuation(self) -> bool:
        return self.synonym is None


@dataclass
class SynonymResult:
    keyword: str
    paragraph: str = ""
    final_text: str = ""


# ── 已读乱回流水线 ──────────────────────────────

class SynonymPipeline:
    """已读乱回流水线：关键词 → LLM 短文 → 分词 → 近义词 → 拼接。"""

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str,
        provider: str = "openai_compatible",
        timeout: int = 60,
    ):
        self._api_key = api_key
        self._api_base = api_base
        self._model = model
        self._provider = normalize_provider(provider)
        self._timeout = timeout

    def _make_headers(self) -> dict:
        return provider_headers(self._provider, self._api_key)

    async def _llm_call(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 256,
    ) -> str:
        """调用 LLM，返回纯文本。"""
        async with httpx.AsyncClient(
            base_url=self._api_base,
            timeout=httpx.Timeout(self._timeout),
            headers=self._make_headers(),
        ) as client:
            resp = await client.post(
                provider_endpoint(self._provider),
                json=build_request(
                    self._provider,
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                ),
            )
            resp.raise_for_status()
            data = resp.json()
            return parse_response(self._provider, data).text.strip()

    async def process(self, keyword: str) -> SynonymResult:
        """完整流水线。"""
        result = SynonymResult(keyword=keyword)

        # Step 1: 生成段落
        result.paragraph = await self._llm_call(
            get_text("business.synonym_generate_paragraph", PROMPT_GENERATE_PARAGRAPH).format(keyword=keyword),
            temperature=0.7,
            max_tokens=256,
        )
        logger.info(f"📝 已读乱回段落: {result.paragraph[:60]}...")

        # Step 2: 分词
        words = await self._tokenize(result.paragraph)
        logger.info(f"🔤 分词: {len(words)} 个元素")

        # Step 3: 盲批近义词
        tokens = await self._blind_synonyms(words)
        logger.info(f"🔤 近义词完成: {len(tokens)} 个 token")

        # Step 4: 拼接
        parts: list[str] = []
        for t in tokens:
            parts.append(t.synonym if t.synonym else t.token)
        raw_text = "".join(parts)

        # Step 5: 失语症后处理（已读乱回模式固定开启）
        aphasia_tokens = self._apply_aphasia(tokens)
        result.final_text = "".join(t.token for t in aphasia_tokens)

        logger.info(f"🎉 已读乱回完成: {result.final_text[:60]}...")
        return result

    async def _tokenize(self, paragraph: str) -> list[str]:
        """分词。"""
        prompt = get_text("business.synonym_tokenize", PROMPT_TOKENIZE_ONLY).format(paragraph=paragraph)
        raw = await self._llm_call(prompt, temperature=0.3, max_tokens=256)
        return [w.strip() for w in raw.split(",") if w.strip()]

    async def _blind_synonyms(self, words: list[str]) -> list[TokenWithSynonym]:
        """盲批近义词。"""
        punct_chars = "，。！？；：、" + "\u201c\u201d\u2018\u2019" + "（）《》【】…—～,.;:!?\"'()[]{}<>"
        punctuation_set = set(punct_chars)

        content_words = [w for w in words if not all(ch in punctuation_set for ch in w)]
        random.shuffle(content_words)

        word_list_str = ", ".join(content_words)
        prompt = get_text("business.synonym_blind_synonyms", PROMPT_BLIND_SYNONYMS).format(word_list=word_list_str)
        raw = await self._llm_call(prompt, temperature=0.5, max_tokens=512)
        synonym_map = self._parse_synonym_output(raw)

        tokens: list[TokenWithSynonym] = []
        for w in words:
            if w in synonym_map:
                tokens.append(TokenWithSynonym(token=w, synonym=synonym_map[w]))
            else:
                is_punc = all(ch in punctuation_set for ch in w) if w else False
                tokens.append(TokenWithSynonym(token=w, synonym=None if is_punc else w))
        return tokens

    @staticmethod
    def _parse_synonym_output(raw: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^[\d]+[\.\)、]\s*", "", line)
            line = re.sub(r"^[-·•]\s*", "", line)
            if "->" in line or "→" in line:
                parts = re.split(r"\s*->\s*|\s*→\s*", line, maxsplit=1)
                if len(parts) == 2:
                    result[parts[0].strip()] = parts[1].strip()
        return result

    @staticmethod
    def _apply_aphasia(tokens: list[TokenWithSynonym]) -> list[TokenWithSynonym]:
        """失语症后处理。"""
        result: list[TokenWithSynonym] = []
        skip_next = False

        for i, t in enumerate(tokens):
            if skip_next:
                skip_next = False
                continue
            if t.is_punctuation:
                result.append(t)
                continue

            word = t.synonym if t.synonym else t.token

            # 音素替换
            if random.random() < 0.10:
                chars = list(word)
                for j, ch in enumerate(chars):
                    if ch in _PHONETIC_MAP and random.random() < 0.5:
                        chars[j] = _PHONETIC_MAP[ch]
                word = "".join(chars)

            # 删除虚词
            if random.random() < 0.15 and word in _PARTICLE_SET:
                continue

            # 相邻颠倒
            if random.random() < 0.05:
                next_idx = i + 1
                while next_idx < len(tokens) and tokens[next_idx].is_punctuation:
                    next_idx += 1
                if next_idx < len(tokens):
                    next_word = tokens[next_idx].synonym or tokens[next_idx].token
                    result.append(TokenWithSynonym(token=next_word, synonym=None))
                    skip_next = True

            # 重复
            if random.random() < 0.03:
                result.append(TokenWithSynonym(token=word, synonym=None))
                result.append(TokenWithSynonym(token=word, synonym=None))
                continue

            result.append(TokenWithSynonym(token=word, synonym=None))

        return result
