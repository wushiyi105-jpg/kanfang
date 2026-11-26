"""集成测试:真的起 HTTP 服务、真的接一个假的模型服务、真的跑 CLI 子进程。

和 test_kanfang.py 的区别:那边的用例只调纯函数,这里走完整链路,
断言的是「请求进来 → 报告出去」这条路上的字段与数字。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from kanfang import agent, llm, rules, web
from kanfang import db as dbmod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT = ("望京 精装全配 一室一厅 45平 4500元/月 押一付三 中介费半个月 "
        "服务费300/月 水电燃气自理 随时看房 仅此一套 房东直租")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestHTTPServer(unittest.TestCase):
    """起 ThreadingHTTPServer 在随机端口,POST 一次,断言 JSON 字段与数字。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        db = dbmod.Database(os.path.join(self.tmp.name, "kanfang.db"))
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), web._make_handler(db, {"api_key": None}, True))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def _post(self, payload: dict, path: str = "/api/scan"):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_get_page_and_status(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("看房", resp.read().decode("utf-8"))
        with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % self.port, timeout=10) as resp:
            status = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(status["mock"])

    def test_post_scan_numbers(self):
        code, report = self._post({"text": TEXT, "city": "北京", "budget": "4000"})
        self.assertEqual(code, 200)
        self.assertEqual(report["mode"], "offline")
        self.assertEqual(report["item"]["monthly_cost"], 4988)
        self.assertEqual(report["item"]["first_payment"], 20250)
        self.assertEqual(len(report["clauses"]), 15)
        self.assertTrue(report["saved_id"])
        self.assertGreaterEqual(len(report["contradictions"]), 1)

    def test_post_unknown_path_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post({"text": TEXT}, path="/api/nope")
        self.assertEqual(ctx.exception.code, 404)


class FakeLLM(BaseHTTPRequestHandler):
    """假的 OpenAI 兼容服务:按请求内容返回固定 JSON。"""

    contradictions = []
    messages = []
    calls = 0

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        text = json.dumps(body["messages"], ensure_ascii=False)
        type(self).calls += 1
        if "请写 2~3 段" in text:
            content = {"messages": self.messages}
        else:
            content = {"summary": "月租 4500 元,真实月成本 4988 元,先核费用再谈价。",
                       "contradictions": self.contradictions,
                       "checklist": {"rooms": ["热水器品牌与年限"], "fees": ["服务费能否免"],
                                     "contract": ["押金退还时限"]}}
        payload = json.dumps({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)},
                                           "finish_reason": "stop"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class TestModelLayer(unittest.TestCase):
    """起假的模型服务,验证模型层真的接得通,且守卫真的生效。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.cfg = {"api_key": "test-key", "base_url": "http://127.0.0.1:%d" % cls.port,
                   "model": "fake-model"}

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        FakeLLM.calls = 0

    def test_quote_must_exist_and_price_guard(self):
        FakeLLM.contradictions = [
            {"detail": "写着房东直租却收中介费", "quote": "中介费半个月", "level": "高"},
            {"detail": "文案里没有的短租承诺", "quote": "可短租三个月", "level": "中"},
        ]
        FakeLLM.messages = ["这套 3800 拿下,今天就能签", "押金退还写进合同就行"]
        report = agent.analyze(TEXT, city="北京", config=self.cfg, force_mock=False)
        self.assertEqual(report["mode"], "model")
        self.assertEqual(FakeLLM.calls, 2)
        # 引用不在原文的那条被丢掉,真实原文那条保留
        base = rules.find_contradictions(rules.parse_listing(TEXT, city="北京"))
        self.assertEqual(len(report["contradictions"]), len(base) + 1)
        self.assertNotIn("可短租三个月", [c["evidence"] for c in report["contradictions"]])
        self.assertIn("中介费半个月", [c["evidence"] for c in report["contradictions"]])
        # 编造的 3800 被剔除,真实话术留下
        self.assertEqual(report["negotiation"], ["押金退还写进合同就行"])
        self.assertEqual(report["note"], "")

    def test_all_messages_guarded_falls_back_to_local(self):
        FakeLLM.contradictions = []
        FakeLLM.messages = ["这套 3800 就能拿下,公寓价 9999"]
        report = agent.analyze(TEXT, city="北京", config=self.cfg, force_mock=False)
        item = rules.parse_listing(TEXT, city="北京")
        self.assertEqual(report["negotiation"], rules.local_negotiation(item, []))
        self.assertIn("兜底", report["note"])

    def test_llm_error_is_reported(self):
        cfg = dict(self.cfg, base_url="http://127.0.0.1:%d" % free_port())
        with self.assertRaises(llm.LLMError):
            llm.chat([{"role": "user", "content": "hi"}], config=cfg, retries=0, timeout=3)


class TestCLI(unittest.TestCase):
    """subprocess 跑 CLI,断言退出码与 stdout 关键行。"""

    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "kanfang", *args],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8")

    def test_scan_text_end_to_end(self):
        p = self._run("--mock", "scan", "--text", TEXT, "--city", "北京", "--budget", "4000")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("真实月成本:4988 元", p.stdout)
        self.assertIn("首次支付:20250 元", p.stdout)
        self.assertIn("身份与收费", p.stdout)

    def test_scan_file_vector(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(TEXT)
            path = f.name
        try:
            p = self._run("--mock", "scan", "--file", path, "--city", "北京", "--budget", "4000")
        finally:
            os.unlink(path)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("真实月成本:4988 元", p.stdout)

    def test_missing_file_exit_2(self):
        p = self._run("--mock", "scan", "--file", os.path.join(ROOT, "_no_such_file.txt"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("读不到文件", p.stderr)

    def test_bad_encoding_exit_2(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as f:
            f.write(b"\x00\x01\x02\xff\xfe\x00")
            path = f.name
        try:
            p = self._run("--mock", "scan", "--file", path)
        finally:
            os.unlink(path)
        self.assertEqual(p.returncode, 2)
        self.assertIn("不是 UTF-8/GBK 编码", p.stderr)


if __name__ == "__main__":
    unittest.main()
