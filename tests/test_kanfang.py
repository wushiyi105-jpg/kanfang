"""离线路径单测:只测本地规则,不发起任何网络请求。"""

from __future__ import annotations

import os
import tempfile
import unittest

from kanfang import agent, mock, rules
from kanfang.cli import read_text_file

TEXT = ("望京 精装全配 一室一厅 45平 4500元/月 押一付三 中介费半个月 "
        "服务费300/月 水电燃气自理 随时看房 仅此一套 房东直租")


class TestExtract(unittest.TestCase):
    def test_rent_with_context(self):
        rent, raw = rules.extract_rent("月租:5200 元 押一付一")
        self.assertEqual(rent, 5200)
        self.assertIn("5200", raw)

    def test_rent_ignores_service_fee(self):
        rent, _ = rules.extract_rent("服务费 300 元/月,租金 4500 元/月")
        self.assertEqual(rent, 4500)

    def test_deposit_and_pay_combo(self):
        dep, pay, raw = rules.extract_deposit_pay("押二付一,随时入住")
        self.assertEqual((dep, pay), (2, 1))
        self.assertIn("押二付一", raw)

    def test_deposit_amount_in_cash(self):
        months, amount, _ = rules.extract_deposit("押金 9000 元")
        self.assertIsNone(months)
        self.assertEqual(amount, 9000)

    def test_area_and_layout(self):
        area, _ = rules.extract_area("建筑面积 62.5 平方米")
        self.assertEqual(area, 62.5)
        rooms, halls, _ = rules.extract_layout("两室一厅")
        self.assertEqual((rooms, halls), (2, 1))

    def test_agency_half_month(self):
        fee, raw = rules.extract_agency("中介费半个月", 4000)
        self.assertEqual(fee, 2000)
        self.assertIn("半个月", raw)

    def test_agency_percent(self):
        fee, _ = rules.extract_agency("中介费 50%", 3000)
        self.assertEqual(fee, 1500)


class TestCost(unittest.TestCase):
    def test_monthly_and_first_payment(self):
        item = rules.parse_listing(TEXT, city="北京", budget=4000)
        # 首年 = 4500*12 + 2250 中介 + 300*12 服务 = 59850,÷12 = 4987.5
        self.assertEqual(item["monthly_cost"], 4988)
        # 首期 = 4500*3 + 4500 押金 + 2250 中介
        self.assertEqual(item["first_payment"], 20250)
        self.assertEqual(item["price_per_sqm"], 100.0)

    def test_utilities_included_not_double_counted(self):
        item = rules.parse_listing("租金 3000 元/月 包水电 物业费 200 元/月 包物业")
        self.assertEqual(item["utility_status"], "包含")
        self.assertEqual(item["utility_fixed"], 0)
        self.assertEqual(item["property_fee"], 0)

    def test_over_budget_flag(self):
        item = rules.parse_listing("租金 5000 元/月", budget=4500)
        self.assertTrue(item["over_budget"])


class TestRules(unittest.TestCase):
    def test_talk_hits_sorted_by_risk(self):
        hits = rules.hit_talks(TEXT)
        phrases = [h["phrase"] for h in hits]
        self.assertIn("随时看房", phrases)
        self.assertIn("房东直租", phrases)
        self.assertEqual(hits[0]["risk"], "高")

    def test_contradiction_small_area_many_rooms(self):
        item = rules.parse_listing("三室一厅 30平 租金 3000 元/月")
        kinds = [c["kind"] for c in rules.find_contradictions(item)]
        self.assertIn("面积与户型", kinds)

    def test_contradiction_direct_landlord_with_agency_fee(self):
        item = rules.parse_listing(TEXT)
        kinds = [c["kind"] for c in rules.find_contradictions(item)]
        self.assertIn("身份与收费", kinds)

    def test_contradiction_price_far_below_city(self):
        item = rules.parse_listing("租金 800 元/月 60平 一室一厅", city="北京")
        details = " ".join(c["detail"] for c in rules.find_contradictions(item))
        self.assertIn("低得反常", details)

    def test_checklist_has_three_groups(self):
        item = rules.parse_listing(TEXT, commute="望京SOHO")
        talks = rules.hit_talks(TEXT)
        checklist = rules.build_checklist(item, talks, rules.find_contradictions(item))
        self.assertEqual({"房间", "费用", "合同"}, {c["group"] for c in checklist})
        self.assertTrue(any("望京SOHO" in c["q"] for c in checklist))

    def test_contract_clauses_count(self):
        self.assertEqual(len(rules.contract_clauses()), 15)


class TestOfflinePipeline(unittest.TestCase):
    def test_analyze_offline_shape(self):
        report = agent.analyze(TEXT, city="北京", budget=4000, force_mock=True)
        self.assertEqual(report["mode"], "offline")
        self.assertIsNone(report["saved_id"])
        self.assertTrue(report["summary"])
        self.assertTrue(report["negotiation"])
        self.assertEqual(len(report["clauses"]), 15)
        self.assertEqual(report["item"]["pay_months"], 3)

    def test_offline_is_deterministic(self):
        a = agent.analyze(TEXT, city="北京", force_mock=True)
        b = agent.analyze(TEXT, city="北京", force_mock=True)
        self.assertEqual(a["summary"], b["summary"])
        self.assertEqual(a["negotiation"], b["negotiation"])

    def test_number_guard_drops_fabricated_price(self):
        item = rules.parse_listing(TEXT)
        kept, fell_back = agent._guard_messages(
            ["这套 4500 可以谈到 3800,再送一个月", "押金退还写进合同就行"], item)
        self.assertFalse(fell_back)
        self.assertEqual(kept, ["押金退还写进合同就行"])

    def test_number_guard_all_dropped_falls_back(self):
        item = rules.parse_listing(TEXT)
        kept, fell_back = agent._guard_messages(["这套 3800 就能拿下,公寓价 9999"], item)
        self.assertTrue(fell_back)
        self.assertEqual(kept, rules.local_negotiation(item, []))

    def test_number_guard_keeps_real_numbers(self):
        item = rules.parse_listing(TEXT)
        kept, fell_back = agent._guard_messages(
            ["房子 45 平,真实月成本 4988,能不能按 4500 谈"], item)
        self.assertFalse(fell_back)
        self.assertEqual(len(kept), 1)

    def test_mock_matches_rules(self):
        item = rules.parse_listing(TEXT)
        contradiction = rules.find_contradictions(item)
        out = mock.offline(item, contradiction)
        self.assertEqual(out["summary"], rules.local_summary(item, contradiction))
        self.assertEqual(out["extra_contradictions"], [])


class TestFileEncoding(unittest.TestCase):
    """issue #3:记事本另存的 GBK 文案要能读,带 BOM 的 UTF-8 要干净,坏文件要说人话。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data: bytes) -> str:
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_gbk_file_is_readable(self):
        p = self._write("gbk.txt", TEXT.encode("gbk"))
        self.assertEqual(read_text_file(p), TEXT)

    def test_utf8_bom_is_stripped(self):
        p = self._write("bom.txt", TEXT.encode("utf-8-sig"))
        self.assertEqual(read_text_file(p), TEXT)
        self.assertFalse(read_text_file(p).startswith("\ufeff"))

    def test_broken_file_exits_2(self):
        p = self._write("bin.dat", b"\x00\x81\xfe\xff\x00garbage")
        with self.assertExit(2):
            read_text_file(p)

    def test_missing_file_exits_2(self):
        with self.assertExit(2):
            read_text_file(os.path.join(self.tmp.name, "nope.txt"))

    def assertExit(self, code):
        test = self

        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                test.assertEqual(exc_type, SystemExit)
                test.assertEqual(exc.code, code)
                return True

        return _Ctx()


if __name__ == "__main__":
    unittest.main()