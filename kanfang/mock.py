"""离线模式:没有 key 或显式 --mock 时的确定性结果。

规则层已经把费用、话术命中、矛盾点、检查清单算完,这里只补三段文字:
摘要、少量补充问题、砍价话术。全部由模板拼出,同一段房源文本每次结果一致。
"""

from __future__ import annotations

from . import rules


def offline(item: dict, contradictions: list[dict]) -> dict:
    return {
        "summary": rules.local_summary(item, contradictions),
        "extra_contradictions": [],
        "extra_checklist": [],
        "negotiation": rules.local_negotiation(item, contradictions),
        "note": "离线模式:摘要、清单与话术由本地规则生成,未经过模型。",
    }
