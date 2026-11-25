"""看房 · 本地规则层:费用解析、成本计算、话术命中、矛盾检测、清单生成。

这里的每一件事都能用文字加数字复核:
  - 抽出来的每一项费用都带原文片段(evidence);
  - 首月支出与真实月成本由固定公式算,不经过模型;
  - 话术命中是子串匹配,矛盾检测是区间与词面判断。
模型只负责语义层面的补充(前后矛盾、问题清单、砍价话术)。
"""

from __future__ import annotations

import json
import os
import re

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6}


def load_seed(name: str) -> list[dict]:
    path = os.path.join(DATA_DIR, name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _find(pattern: str, text: str) -> re.Match | None:
    return re.search(pattern, text)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def extract_rent(text: str) -> tuple[float | None, str]:
    """月租。优先带「租金/房租/月租」上下文的写法,再退回「xxxx 元/月」。"""
    m = _find(r"(?:月租|租金|房租|租价)\s*[:：]?\s*(\d{3,7})\s*(?:元|块)?", text)
    if m:
        return _num(m.group(1)), m.group(0)
    for m in re.finditer(r"(\d{3,7})\s*(?:元|块)?\s*(?:/\s*月|每月|一个月)", text):
        before = text[max(0, m.start() - 6):m.start()]
        if re.search(r"(服务|物业|水|电|燃气|网|中介|管理|保洁|取暖|押)", before):
            continue
        return _num(m.group(1)), m.group(0)
    return None, ""


def extract_area(text: str) -> tuple[float | None, str]:
    m = _find(r"(\d+(?:\.\d+)?)\s*(?:平方米|平米|平方|平|㎡|m²)", text)
    if m:
        return _num(m.group(1)), m.group(0)
    return None, ""


def extract_layout(text: str) -> tuple[int | None, int | None, str]:
    m = _find(r"([一二两三四五六1-6])\s*室\s*([一二两三四五六1-6])\s*厅", text)
    if m:
        return CN_NUM[m.group(1)], CN_NUM[m.group(2)], m.group(0)
    m = _find(r"([一二两三四五六1-6])\s*居(?:室)?", text)
    if m:
        return CN_NUM[m.group(1)], None, m.group(0)
    return None, None, ""


def extract_deposit_pay(text: str) -> tuple[int | None, int | None, str]:
    """一次吃掉「押一付三」「押二付一」这类合写。"""
    m = _find(r"押\s*([一二两三四1-4])\s*(?:个)?(?:月)?\s*付\s*([一二两三四1-4])\s*(?:个)?月?", text)
    if m:
        return CN_NUM[m.group(1)], CN_NUM[m.group(2)], m.group(0)
    return None, None, ""


def extract_deposit(text: str) -> tuple[int | None, float | None, str]:
    """返回 (押几个月, 押金金额, 原文)。"""
    m = _find(r"押\s*([一二两三四1-4])\s*(?:个)?月", text)
    if m:
        return CN_NUM[m.group(1)], None, m.group(0)
    m = _find(r"押金\s*[:：]?\s*(\d{3,6})\s*(?:元|块)", text)
    if m:
        return None, _num(m.group(1)), m.group(0)
    return None, None, ""


def extract_pay(text: str) -> tuple[int | None, str]:
    """首期付几个月。"""
    m = _find(r"付\s*([一二两三四1-4])\s*(?:个)?月", text)
    if m:
        return CN_NUM[m.group(1)], m.group(0)
    if "年付" in text:
        return 12, "年付"
    if "半年付" in text:
        return 6, "半年付"
    if "季付" in text:
        return 3, "季付"
    if "月付" in text:
        return 1, "月付"
    return None, ""


def extract_agency(text: str, rent: float | None) -> tuple[float, str]:
    """中介费(一次性),按「半个月/一个月/百分比/定额」四种写法归一。"""
    m = _find(r"中介费\s*[:：]?\s*(半个月|一个月|\d+(?:\.\d+)?\s*%|\d{3,6}\s*元)", text)
    if not m:
        return 0.0, ""
    token, raw = m.group(1), m.group(0)
    if "半个月" in token and rent:
        return rent * 0.5, raw
    if "一个月" in token and rent:
        return rent, raw
    percent = re.search(r"(\d+(?:\.\d+)?)\s*%", token)
    if percent and rent:
        return rent * _num(percent.group(1)) / 100, raw
    amount = re.search(r"(\d{3,6})", token)
    if amount:
        return _num(amount.group(1)), raw
    return 0.0, raw


def extract_monthly_fee(text: str, label: str) -> tuple[float, str]:
    m = _find(rf"{label}\s*(?:费)?\s*[:：]?\s*(\d{{2,5}})\s*(?:元|块)?\s*(?:/\s*月|每月)?", text)
    if m:
        return _num(m.group(1)), m.group(0)
    return 0.0, ""


def utilities_status(text: str) -> tuple[str, str]:
    """返回 (自理|包含|固定额|未知, 原文)。"""
    if re.search(r"包(水电|水电燃气|物业)|水电.{0,4}含在租金", text):
        m = _find(r"包(?:水电|水电燃气|物业)[^,。;；]*", text)
        return "包含", m.group(0) if m else ""
    if re.search(r"水电.{0,6}自理|水电燃气.{0,4}自理|水电另算", text):
        m = _find(r"水电[^,。;；]*自理|水电另算", text)
        return "自理", m.group(0) if m else ""
    fixed, raw = extract_monthly_fee(text, "水电")
    if fixed:
        return "固定额", raw
    return "未知", ""


def parse_listing(text: str, city: str | None = None, budget: float | None = None,
                  commute: str | None = None) -> dict:
    text = (text or "").replace("\r", " ").replace("\n", " ")
    rent, rent_raw = extract_rent(text)
    area, area_raw = extract_area(text)
    rooms, halls, layout_raw = extract_layout(text)
    deposit_months, deposit_amount, deposit_raw = extract_deposit(text)
    pay_months, pay_raw = extract_pay(text)
    combo_dep, combo_pay, combo_raw = extract_deposit_pay(text)
    if combo_dep is not None:
        deposit_months, pay_months = combo_dep, combo_pay
        deposit_raw, pay_raw = combo_raw, combo_raw
    agency, agency_raw = extract_agency(text, rent)
    service, service_raw = extract_monthly_fee(text, "服务费")
    prop, prop_raw = extract_monthly_fee(text, "物业费")
    network, network_raw = extract_monthly_fee(text, "网费")
    util_status, util_raw = utilities_status(text)
    util_fixed, _util_raw_fixed = extract_monthly_fee(text, "水电")

    if "包物业" in text:
        prop, prop_raw = 0.0, "包物业费(已含在租金)"
    if util_status == "包含":
        util_fixed = 0.0

    deposit_cash = deposit_amount if deposit_amount is not None else (
        rent * deposit_months if rent and deposit_months else 0.0
    )

    monthly_cost = 0.0
    if rent:
        annual = rent * 12 + agency
        annual += (service + prop + network + util_fixed) * 12
        monthly_cost = round(annual / 12)

    first_payment = 0.0
    if rent:
        first_payment = rent * (pay_months or 1) + deposit_cash + agency

    over_budget = bool(budget and monthly_cost and monthly_cost > budget)
    ppm = round(rent / area, 1) if rent and area else None

    return {
        "text": text,
        "city": city or "",
        "budget": budget,
        "commute": commute or "",
        "rent": rent,
        "rent_raw": rent_raw,
        "area": area,
        "area_raw": area_raw,
        "rooms": rooms,
        "halls": halls,
        "layout_raw": layout_raw,
        "deposit_months": deposit_months,
        "deposit_amount": deposit_cash or None,
        "deposit_raw": deposit_raw,
        "pay_months": pay_months,
        "pay_raw": pay_raw,
        "agency_fee": round(agency),
        "agency_raw": agency_raw,
        "service_fee": round(service),
        "service_raw": service_raw,
        "property_fee": round(prop),
        "property_raw": prop_raw,
        "network_fee": round(network),
        "network_raw": network_raw,
        "utility_status": util_status,
        "utility_raw": util_raw,
        "utility_fixed": round(util_fixed),
        "monthly_cost": monthly_cost,
        "first_payment": round(first_payment),
        "price_per_sqm": ppm,
        "over_budget": over_budget,
    }


# ---------------------------------------------------------------------------
# 话术命中
# ---------------------------------------------------------------------------
def hit_talks(text: str, talks: list[dict] | None = None) -> list[dict]:
    talks = talks if talks is not None else load_seed("talks.json")
    hits = [t for t in talks if t["phrase"] in text]
    order = {"高": 0, "中": 1, "低": 2}
    return sorted(hits, key=lambda t: order.get(t["risk"], 9))


# ---------------------------------------------------------------------------
# 矛盾检测(本地硬规则)
# ---------------------------------------------------------------------------
def city_tier(city: str, tiers: list[dict] | None = None) -> dict | None:
    tiers = tiers if tiers is not None else load_seed("rent_tiers.json")
    for t in tiers:
        if t["city"] and city and t["city"] in city:
            return t
    return None


def find_contradictions(item: dict, tiers: list[dict] | None = None) -> list[dict]:
    out: list[dict] = []
    text = item["text"]
    rent, area = item.get("rent"), item.get("area")
    rooms = item.get("rooms")

    if rent and area:
        tier = city_tier(item.get("city", ""), tiers)
        if tier:
            ppm = rent / area
            if ppm < tier["low"] * 0.55:
                out.append({
                    "kind": "价格偏离", "level": "高",
                    "detail": f"{item['city']} 参考 {tier['low']}–{tier['high']} 元/㎡·月,"
                              f"这套折算 {ppm:.1f} 元/㎡·月,低得反常",
                    "evidence": f"{item['rent_raw']} / {item['area_raw']}",
                })
            elif ppm > tier["high"] * 1.6:
                out.append({
                    "kind": "价格偏离", "level": "中",
                    "detail": f"折算 {ppm:.1f} 元/㎡·月,高于 {item['city']} 常见上限 "
                              f"{tier['high']} 的 1.6 倍,先确认是否含服务费",
                    "evidence": f"{item['rent_raw']} / {item['area_raw']}",
                })

    if rooms and area:
        if rooms >= 3 and area < 45:
            out.append({"kind": "面积与户型", "level": "高",
                        "detail": f"{rooms} 室却只有 {area:g} ㎡,单间面积不足 15 ㎡,可能是隔断房",
                        "evidence": f"{item['layout_raw']} / {item['area_raw']}"})
        elif rooms >= 4 and area < 60:
            out.append({"kind": "面积与户型", "level": "中",
                        "detail": f"{rooms} 室 {area:g} ㎡,注意是否为群租或隔断",
                        "evidence": f"{item['layout_raw']} / {item['area_raw']}"})
        elif rooms == 1 and area > 90:
            out.append({"kind": "面积与户型", "level": "低",
                        "detail": f"一室 {area:g} ㎡ 偏大,确认是否把整租写成单间",
                        "evidence": f"{item['layout_raw']} / {item['area_raw']}"})

    if "房东直租" in text and item.get("agency_fee"):
        out.append({"kind": "身份与收费", "level": "高",
                    "detail": "写着「房东直租」却收中介费,要么是中介冒充,要么费用换名",
                    "evidence": item["agency_raw"] or "中介费"})

    if "免中介费" in text and (item.get("service_fee") or item.get("agency_fee")):
        out.append({"kind": "费用换名", "level": "高",
                    "detail": "「免中介费」同时出现服务费/中介费,实际成本没少",
                    "evidence": "、".join(x for x in [item.get("service_raw"), item.get("agency_raw")] if x)})

    if "整租" in text and "合租" in text:
        out.append({"kind": "描述冲突", "level": "中",
                    "detail": "同一段里同时出现整租与合租,居住人数与费用分摊都要问清",
                    "evidence": "整租 / 合租"})

    if item.get("utility_status") == "未知" and rent:
        out.append({"kind": "费用缺项", "level": "中",
                    "detail": "没写水电燃气由谁承担,这部分最容易在退租时补差",
                    "evidence": "无水电话术"})

    if item.get("over_budget"):
        out.append({"kind": "预算", "level": "低",
                    "detail": f"真实月成本 {item['monthly_cost']} 元,超出预算 {item['budget']:g} 元",
                    "evidence": f"月成本 {item['monthly_cost']}"})
    return out


# ---------------------------------------------------------------------------
# 看房清单
# ---------------------------------------------------------------------------
def build_checklist(item: dict, talks: list[dict], contradictions: list[dict]) -> list[dict]:
    """按房间 / 费用 / 合同三组给现场要问的问题,每条挂一个风险点。"""
    rooms, fees, contract = [], [], []

    rooms += [
        {"q": "采光和通风:白天去看,两间房是否真对流", "risk": "「南北通透」常是话术"},
        {"q": "墙面、地板、门窗有无裂缝或渗水痕迹", "risk": "老房渗水修不好,退租还要算你的"},
        {"q": "水压与热水:开热水要等多久,两个龙头同时开会不会掉压", "risk": "住进去才发现洗澡要等十分钟"},
        {"q": "卫生间返味与排水:冲水、开排风扇各试一次", "risk": "管道问题房东通常不修"},
        {"q": "隔音:走廊、隔壁、楼上各听一分钟", "risk": "隔断房与临街房的噪音无法后期解决"},
    ]
    fees += [
        {"q": "月租之外还有哪些按月收的费用?逐项报金额并写进合同", "risk": "服务费/管理费常口头免、合同照收"},
        {"q": "水电燃气是民用价还是商用价?起始读数拍照留档", "risk": "商用价可能贵一倍"},
        {"q": "押金退还要几天、扣款只限哪些情形", "risk": "「随时退」没有时限就是拖"},
        {"q": "是否走租金贷/分期平台?付款账户名是谁", "risk": "分期逾期会影响征信"},
    ]
    contract += [
        {"q": "产权证、出租人身份证、委托书能否当场核对", "risk": "二房东跑路,押金难追"},
        {"q": "合同里写不写家具家电清单与退租验收标准", "risk": "没有清单就有「折损费」"},
        {"q": "提前退租的违约金怎么算、能否转租抵扣", "risk": "工作变动时损失一大笔"},
        {"q": "续租涨幅与调价周期是否写死", "risk": "第二年一口价,搬家成本更高"},
        {"q": "所有口头承诺能否作为附件一并签字", "risk": "口头承诺不进合同等于没有"},
    ]

    for t in talks:
        if t["risk"] == "高":
            fees.append({"q": f"「{t['phrase']}」具体指什么?让对方说明并写进合同", "risk": t["meaning"]})

    for c in contradictions:
        if c["kind"] == "面积与户型":
            rooms.append({"q": "现场量面积并核对户型图,确认不是隔断", "risk": c["detail"]})
        elif c["kind"] in ("身份与收费", "费用换名"):
            contract.append({"q": "让出租人出示产权与委托材料,费用逐项写进合同", "risk": c["detail"]})

    if item.get("commute"):
        rooms.insert(0, {"q": f"实测到「{item['commute']}」的通勤:早高峰走一遍", "risk": "宣传时间是到站台,不是到门口"})

    return (
        [{"group": "房间", **x} for x in rooms]
        + [{"group": "费用", **x} for x in fees]
        + [{"group": "合同", **x} for x in contract]
    )


def contract_clauses(traps: list[dict] | None = None) -> list[dict]:
    return traps if traps is not None else load_seed("traps.json")


# ---------------------------------------------------------------------------
# 砍价话术(离线模板;有模型时由 agent 重写)
# ---------------------------------------------------------------------------
def local_negotiation(item: dict, contradictions: list[dict]) -> list[str]:
    rent = item.get("rent")
    if not rent:
        return ["先把月租、押付、费用问清楚,再谈价格。"]
    target = int(round(rent * 0.9 / 50) * 50)
    room = item.get("area")
    lines = []
    basis = "、".join(c["kind"] for c in contradictions[:2]) or "同小区成交价"
    intro = f"房子我看了,{('%.0f 平' % room) if room else '面积'}这个户型整体可以,"
    lines.append(f"{intro}但{basis}这块我还得再核一下。周边同类我看到的成交在 {target} 上下,"
                 f"你这边 {int(rent)} 能不能往这个数靠?能定的话我这周就签。")
    if item.get("service_fee") or item.get("agency_fee"):
        lines.append(f"另外服务费/中介费这块能不能免掉或者折进租金?我算了一下,"
                     f"加上这些真实月成本已经到 {item['monthly_cost']} 了,超出我的预算。")
    lines.append("押金退还的时限和扣款条件我想写进合同,这个不影响价格,只是把话说清楚;"
                 "这两条没问题我们今天就能定。")
    return lines


def local_summary(item: dict, contradictions: list[dict]) -> str:
    if not item.get("rent"):
        return "这段文案里没找到月租,先让对方把租金、押付方式、费用逐项写清楚。"
    parts = [f"月租 {int(item['rent'])} 元"]
    if item.get("area"):
        parts.append(f"{item['area']:g} ㎡")
    parts.append(f"真实月成本约 {item['monthly_cost']} 元")
    parts.append(f"首次支付约 {item['first_payment']} 元")
    head = "、".join(parts) + "。"
    if contradictions:
        head += f"本地规则发现 {len(contradictions)} 处需要核对的点,先看「" + contradictions[0]["kind"] + "」。"
    return head
