"""核心编排:本地硬算打底,模型补语义层。

流程(每一层都可单独关闭):
  1. rules.parse_listing —— 抽费用项、面积、押付,算首月支出与真实月成本;
  2. rules.hit_talks / find_contradictions —— 话术命中与数字矛盾(本地);
  3. rules.build_checklist —— 房间/费用/合同三组清单(本地);
  4. 模型:找规则漏掉的语义矛盾、补现场问题、写砍价话术;
  5. 数字守卫:话术里出现的每个数字都必须在费用表里找得到,否则整句退回本地模板。
"""

from __future__ import annotations

import re

from . import llm, mock, rules

JSON_ONLY = "只输出 JSON,不要输出解释或多余文字。"


def _use_model(config: dict | None, force_mock: bool) -> bool:
    if force_mock:
        return False
    cfg = {**llm.default_config(), **(config or {})}
    return bool(cfg.get("api_key"))


def _cfg(config: dict | None) -> dict:
    return {**llm.default_config(), **(config or {})}


def _allowed_numbers(item: dict) -> set[str]:
    """可信数字集合:费用表里出现过的数与押付/月数。"""
    nums: set[str] = set()
    for key in ("rent", "area", "monthly_cost", "first_payment", "agency_fee",
                "service_fee", "property_fee", "network_fee", "utility_fixed",
                "deposit_amount", "price_per_sqm"):
        val = item.get(key)
        if val:
            nums.add(str(int(val)) if float(val).is_integer() else str(val))
    for key in ("deposit_months", "pay_months", "rooms", "halls"):
        if item.get(key):
            nums.add(str(item[key]))
    for n in range(1, 13):
        nums.add(str(n))
    return nums


def _guard_messages(messages: list[str], item: dict) -> tuple[list[str], bool]:
    """剔掉引用了不可核实数字的句子;全被剔光就退回本地模板。"""
    allowed = _allowed_numbers(item)
    kept = []
    for msg in messages:
        ok = True
        for token in re.findall(r"\d+(?:\.\d+)?", msg):
            base = token.split(".")[0]
            if token not in allowed and base not in allowed:
                ok = False
                break
        if ok:
            kept.append(msg)
    if not kept:
        return rules.local_negotiation(item, []), True
    return kept, False


def _model_layers(item: dict, contradictions: list[dict], config: dict | None) -> dict:
    cfg = _cfg(config)
    facts = {
        "月租": item.get("rent"), "面积": item.get("area"),
        "户型": f"{item.get('rooms') or '?'}室{item.get('halls') or '?'}厅",
        "押付": f"押{item.get('deposit_months') or '?'}付{item.get('pay_months') or '?'}",
        "中介费": item.get("agency_fee"), "服务费": item.get("service_fee"),
        "物业费": item.get("property_fee"), "网费": item.get("network_fee"),
        "水电": item.get("utility_status"), "真实月成本": item.get("monthly_cost"),
        "首次支付": item.get("first_payment"), "城市": item.get("city"),
    }
    hits = [t["phrase"] for t in rules.hit_talks(item["text"])]

    raw = llm.chat([{
        "role": "system",
        "content": ("你是租房核查助手。只依据用户给的房源文案与费用表说话,"
                    "不补充文案里没有的价格、面积、承诺;引用文案时逐字照抄。"),
    }, {
        "role": "user",
        "content": (
            f"房源文案:\n{item['text']}\n\n本地抽取的费用表:{facts}\n"
            f"已命中的话术:{hits}\n本地已发现的矛盾:{[c['detail'] for c in contradictions]}\n"
            f"{JSON_ONLY}\n输出格式:"
            '{"summary":"三句话内的判断,数字只能来自费用表",'
            '"contradictions":[{"detail":"前后矛盾或可疑之处","quote":"文案原文片段",'
            '"level":"高/中/低"}],'
            '"checklist":{"rooms":["现场要确认的问题"],"fees":["费用要问清的问题"],'
            '"contract":["签约要写进合同的问题"]}}'
        ),
    }], config=cfg, temperature=0.2, max_tokens=2600)
    data = llm.extract_json(raw)

    text = item["text"]
    kept = []
    for c in data.get("contradictions", []):
        quote = (c.get("quote") or "").strip()
        if quote and quote not in text:
            continue  # 引用必须能在原文里找到,找不到就是编的
        kept.append({"kind": "语义矛盾", "level": c.get("level") or "中",
                     "detail": c.get("detail", ""), "evidence": quote or "文案上下文"})

    groups = []
    for key, label in (("rooms", "房间"), ("fees", "费用"), ("contract", "合同")):
        for q in (data.get("checklist") or {}).get(key, []):
            q = str(q).strip()
            if 6 <= len(q) <= 100:
                groups.append({"group": label, "q": q, "risk": "模型补充,现场核实"})

    raw2 = llm.chat([{
        "role": "system",
        "content": ("你是租房谈判助手。帮租客写发给中介或房东的消息:只讲事实与诉求,"
                    "不吵架,不编造市场价;价格只能引用给定的数字。"),
    }, {
        "role": "user",
        "content": (
            f"房源情况:{facts}\n已发现问题:{[c['detail'] for c in contradictions]}\n"
            f"请写 2~3 段可直接发送的消息,总字数 200 字以内。{JSON_ONLY}\n"
            '输出格式:{"messages":["消息1","消息2"]}'
        ),
    }], config=cfg, temperature=0.4, max_tokens=1200)
    raw_msgs = llm.extract_json(raw2).get("messages", [])
    messages, fell_back = _guard_messages([str(m) for m in raw_msgs], item)

    return {
        "summary": str(data.get("summary") or rules.local_summary(item, contradictions)),
        "extra_contradictions": kept,
        "extra_checklist": groups,
        "negotiation": messages,
        "guard_fallback": fell_back,
        "note": "离线模板兜底:话术里有无法核实的数字" if fell_back else "",
    }


def analyze(text: str, city: str | None = None, budget: float | None = None,
            commute: str | None = None, config: dict | None = None, db=None,
            force_mock: bool = False, save: bool = False) -> dict:
    item = rules.parse_listing(text, city=city, budget=budget, commute=commute)
    talks = rules.hit_talks(item["text"])
    contradictions = rules.find_contradictions(item)
    checklist = rules.build_checklist(item, talks, contradictions)

    if _use_model(config, force_mock):
        layers = _model_layers(item, contradictions, config)
        contradictions = contradictions + layers["extra_contradictions"]
        checklist = checklist + layers["extra_checklist"]
        mode = "model"
    else:
        layers = mock.offline(item, contradictions)
        mode = "offline"

    level_order = {"高": 0, "中": 1, "低": 2}
    contradictions = sorted(contradictions, key=lambda c: level_order.get(c.get("level"), 3))

    clauses = rules.contract_clauses()
    report = {
        "item": item,
        "summary": layers["summary"],
        "talks": talks,
        "contradictions": contradictions,
        "checklist": checklist,
        "negotiation": layers["negotiation"],
        "clauses": clauses,
        "mode": mode,
        "note": layers.get("note", ""),
        "saved_id": None,
    }
    if db is not None and save:
        report["saved_id"] = db.save(item, report)
    return report

