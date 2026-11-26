"""命令行入口:python -m kanfang <命令> [选项]。"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, agent, llm, rules
from . import db as dbmod
from .llm import LLMError

MOCK_BANNER = (
    "⚠️  当前为离线模式(未配置 LLM_API_KEY):费用、话术、矛盾、清单由本地规则给出。\n"
    "   在 .env 填入 LLM_API_KEY 后,语义矛盾与砍价话术由模型补充。\n"
)


def _money(v) -> str:
    if v is None:
        return "—"
    return f"{int(round(v))} 元" if float(v).is_integer() else f"{v} 元"


def render_report(r: dict) -> str:
    item = r["item"]
    head = []
    if item.get("city"):
        head.append(item["city"])
    if item.get("area"):
        head.append(f"{item['area']:g} ㎡")
    if item.get("rooms"):
        head.append(f"{item['rooms']}室{item.get('halls') or 0}厅")
    mode = "模型" if r["mode"] == "model" else "离线模式"
    lines = [f"🏠 看房报告  [{' · '.join(head) or '未识别户型'}]  [{mode}]", ""]
    lines += ["📌 " + r["summary"], ""]

    lines += ["💰 费用表(本地复算)"]
    rows = [
        ("月租", _money(item.get("rent")), item.get("rent_raw", "")),
        ("押金", _money(item.get("deposit_amount")),
         f"押 {item['deposit_months']} 个月" if item.get("deposit_months") else item.get("deposit_raw", "")),
        ("首期租金", _money((item.get("rent") or 0) * (item.get("pay_months") or 1)),
         item.get("pay_raw", "")),
        ("中介费", _money(item.get("agency_fee")), item.get("agency_raw", "")),
        ("服务费", _money(item.get("service_fee")) + "/月", item.get("service_raw", "")),
        ("物业费", _money(item.get("property_fee")) + "/月", item.get("property_raw", "")),
        ("网费", _money(item.get("network_fee")) + "/月", item.get("network_raw", "")),
        ("水电", item.get("utility_status", "未知"),
         item.get("utility_raw", "")),
    ]
    for name, val, ev in rows:
        if val in ("—", "0 元", "0 元/月") and not ev:
            continue
        line = f"  · {name}:{val}"
        if ev:
            line += f"    ← {ev}"
        lines.append(line)
    lines.append(f"  · 真实月成本:{_money(item.get('monthly_cost'))}  (首年总额 ÷ 12,含一次性中介费)")
    lines.append(f"  · 首次支付:{_money(item.get('first_payment'))}  (首期租金 + 押金 + 中介费)")
    if item.get("price_per_sqm"):
        lines.append(f"  · 折算单价:{item['price_per_sqm']:g} 元/㎡·月")
    if item.get("budget"):
        flag = "⚠️ 超出预算" if item.get("over_budget") else "✅ 在预算内"
        lines.append(f"  · 预算 {_money(item['budget'])} → {flag}")
    lines.append("")

    if r["contradictions"]:
        lines.append(f"⚠️  疑点({len(r['contradictions'])})")
        for i, c in enumerate(r["contradictions"], 1):
            lines.append(f"  {i}. [{c.get('level','中')}] {c.get('kind','')}:{c.get('detail','')}")
            if c.get("evidence"):
                lines.append(f"     原文:{c['evidence']}")
        lines.append("")

    if r["talks"]:
        lines.append(f"🧾 话术命中({len(r['talks'])})")
        for t in r["talks"]:
            lines.append(f"  · [{t['risk']}] {t['phrase']} —— {t['meaning']}")
        lines.append("")

    lines.append(f"❓ 看房清单({len(r['checklist'])} 条,现场逐条确认)")
    for group in ("房间", "费用", "合同"):
        items = [c for c in r["checklist"] if c["group"] == group]
        if not items:
            continue
        lines.append(f"  【{group}】")
        for c in items:
            lines.append(f"  □ {c['q']}")
            lines.append(f"      风险:{c['risk']}")
    lines.append("")

    if r["negotiation"]:
        lines.append("✍  砍价话术(可直接改一改发出去)")
        for i, msg in enumerate(r["negotiation"], 1):
            lines.append(f"  {i}. {msg}")
        lines.append("")

    lines.append("📄 签约前必须写进合同的条款")
    for i, c in enumerate(r["clauses"], 1):
        lines.append(f"  {i}. {c['clause']} —— {c['why']}")
    if r.get("note"):
        lines.append("")
        lines.append(f"注:{r['note']}")
    if r.get("saved_id"):
        lines.append(f"已存入库(#{r['saved_id']}),`python -m kanfang history` 可对比。")
    return "\n".join(lines)


def render_history(rows: list[dict]) -> str:
    if not rows:
        return "还没有记录。`python -m kanfang scan --text \"...\" --save` 存一条。"
    lines = ["🏠 看过的房源(最近在前)", f"  {'ID':<4}{'城市':<6}{'户型':<8}{'月租':<8}{'真实月成本':<10}{'时间'}"]
    for r in rows:
        lines.append(
            f"  {r['id']:<4}{r.get('city') or '—':<6}{r.get('layout') or '—':<8}"
            f"{_money(r.get('rent')):<8}{_money(r.get('monthly_cost')):<10}{r.get('created_at','')}"
        )
    return "\n".join(lines)


def read_text_file(path: str) -> str:
    """读文案文件:先按 utf-8-sig,失败回退 gb18030(兼容记事本 ANSI 另存)。"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except OSError as e:
            print(f"❌ 读不到文件 {path}:{e.strerror}", file=sys.stderr)
            sys.exit(2)
    print(f"❌ 文件不是 UTF-8/GBK 编码,请另存为 UTF-8 后重试:{path}", file=sys.stderr)
    sys.exit(2)


def _get_text(args) -> str:
    if args.file:
        return read_text_file(args.file)
    if args.text:
        return args.text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def cmd_scan(args):
    text = _get_text(args).strip()
    if not text:
        print("❌ 没有房源文本:用 --text \"...\" 或 --file 房源.txt,也可以管道输入。", file=sys.stderr)
        sys.exit(2)
    db = dbmod.Database(args.db) if args.db else dbmod.Database()
    cfg = llm.config_from_args(args)
    r = agent.analyze(text, city=args.city, budget=args.budget, commute=args.commute,
                      config=cfg, db=db, force_mock=args.mock, save=args.save)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(render_report(r))


def cmd_clauses(args):
    clauses = rules.contract_clauses()
    if args.json:
        print(json.dumps(clauses, ensure_ascii=False, indent=2))
        return
    print("📄 签约前写进合同的 15 条(按组看)")
    for group in ("钱", "费用", "居住", "权利", "退租", "身份", "流程"):
        items = [c for c in clauses if c["group"] == group]
        if not items:
            continue
        print(f"\n【{group}】")
        for c in items:
            print(f"  · {c['clause']} —— {c['why']}")


def cmd_talks(args):
    talks = rules.load_seed("talks.json")
    if args.grep:
        talks = [t for t in talks if args.grep in t["phrase"] or args.grep in t["meaning"]]
    if args.json:
        print(json.dumps(talks, ensure_ascii=False, indent=2))
        return
    print(f"🧾 中介话术库({len(talks)} 条)")
    for t in talks:
        print(f"  [{t['risk']}] {t['phrase']} —— {t['meaning']}")


def cmd_history(args):
    db = dbmod.Database(args.db) if args.db else dbmod.Database()
    rows = db.latest(args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(render_history(rows))


def cmd_web(args):
    from . import web
    web.serve(host=args.host, port=args.port, db_path=args.db, force_mock=args.mock)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kanfang",
        description="看房 · 租房助手:拆费用、识话术、算真实月成本、给看房清单与砍价话术。",
    )
    p.add_argument("--version", action="version", version=f"kanfang {__version__}")
    p.add_argument("--mock", action="store_true", help="强制离线模式(不调用模型)")
    p.add_argument("--json", action="store_true", help="输出原始 JSON")
    p.add_argument("--model", help="覆盖 LLM_MODEL")
    p.add_argument("--base-url", help="覆盖 LLM_BASE_URL")
    p.add_argument("--api-key", help="覆盖 LLM_API_KEY")
    p.add_argument("--db", help="SQLite 路径")

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("scan", help="分析一段房源文案")
    sp.add_argument("--text", help="房源文案")
    sp.add_argument("--file", help="从文件读文案")
    sp.add_argument("--city", help="城市,用于租金区间对照,如:成都")
    sp.add_argument("--budget", type=float, help="月预算上限")
    sp.add_argument("--commute", help="通勤目的地,如:天府三街")
    sp.add_argument("--save", action="store_true", help="存入库,便于横向对比")
    sp.set_defaults(func=cmd_scan)

    cp = sub.add_parser("clauses", help="列出签约前必须写进合同的条款")
    cp.set_defaults(func=cmd_clauses)

    tp = sub.add_parser("talks", help="查看中介话术库")
    tp.add_argument("--grep", help="按关键词过滤")
    tp.set_defaults(func=cmd_talks)

    hp = sub.add_parser("history", help="查看已存的房源记录")
    hp.add_argument("--limit", type=int, default=10)
    hp.set_defaults(func=cmd_history)

    wp = sub.add_parser("web", help="启动 Web 界面")
    wp.add_argument("--host", default="127.0.0.1")
    wp.add_argument("--port", type=int, default=8000)
    wp.add_argument("--mock", action="store_true", default=argparse.SUPPRESS,
                    help="以离线模式启动(不调用模型)")
    wp.set_defaults(func=cmd_web)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.mock and args.command == "scan":
        if not llm.config_from_args(args).get("api_key"):
            print(MOCK_BANNER, file=sys.stderr)
    try:
        args.func(args)
    except LLMError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()

