import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/"app"))
from core_runtime import ModelCallError
import json
import ast
from datetime import datetime
from pathlib import Path
import tempfile
import subprocess
import time
import unittest
from unittest.mock import patch, Mock

import web_search as ws
import conversation_engine as ce

NOW = datetime(2026, 10, 26, 18, tzinfo=ws.BEIJING)


class RoutingTests(unittest.TestCase):
    def plan(self, text, history=None):
        return ws.plan_search(text, history, now=NOW)

    def test_worlds_without_latest(self):
        plan = self.plan("英雄联盟全球总决赛")
        self.assertTrue(plan.current)
        self.assertIn("2026", plan.query)

    def test_historical_years_and_seasons(self):
        for text, year in [("2014年英雄联盟全球总决赛谁夺冠", 2014),
                           ("14年世界赛冠军", 2014),
                           ("英雄联盟S14总决赛", 2024),
                           ("去年全球总决赛冠军是谁", 2025)]:
            with self.subTest(text=text):
                plan = self.plan(text)
                self.assertFalse(plan.current)
                self.assertEqual(plan.target_year, year)
                self.assertIn(str(year), plan.query)
                self.assertNotIn("2026", plan.query)

    def test_history_question_does_not_default_to_current_year(self):
        plan = self.plan("英雄联盟历届世界赛冠军")
        self.assertFalse(plan.current)
        self.assertNotIn("2026", plan.query)

    def test_followup_uses_topic_not_old_assistant_answer(self):
        history = [{"role": "user", "content": "英雄联盟全球总决赛"},
                   {"role": "assistant", "content": "2014年的比赛已经结束"}]
        plan = self.plan("冠军是谁？", history)
        self.assertTrue(plan.current)
        self.assertIn("2026", plan.query)
        self.assertNotIn("2014", plan.query)

    def test_followup_can_change_year_in_both_directions(self):
        for previous, followup, target, unwanted in [
            ("2014年英雄联盟全球总决赛", "那今年呢", "2026", "2014"),
            ("2026年英雄联盟全球总决赛", "那2014年呢", "2014", "2026"),
            ("2026年英雄联盟全球总决赛", "那去年呢", "2025", "2026"),
        ]:
            plan = self.plan(followup, [{"role": "user", "content": previous}])
            self.assertIn(target, plan.query)
            self.assertNotIn(unwanted, plan.query)

    def test_chitchat_and_stable_knowledge_skip_search(self):
        for text in ["你好", "我最近很难受", "我喜欢看世界赛", "你今天开心吗", "为什么天空是蓝色的", "不要联网，讲讲世界赛"]:
            with self.subTest(text=text):
                self.assertIsNone(self.plan(text))

    def test_other_current_subjects(self):
        for text in ["这周NBA赛程", "现在上映的电影有哪些", "最新版本更新公告", "这手机多少钱"]:
            with self.subTest(text=text):
                self.assertTrue(self.plan(text).current)

    def test_recent_events_use_month_or_day_not_just_year(self):
        self.assertIn("2026年10月", self.plan("最近上映的电影有哪些").query)
        self.assertIn("2026-10-25", self.plan("昨天世界赛谁赢了").query)
        self.assertNotIn("2026年10月", self.plan("2014年上映的电影有哪些").query)


class ResultsTests(unittest.TestCase):
    def test_search_protocol_accepts_any_topic_and_rejects_bad_types(self):
        request = ws.parse_search_request('<web_search>{"query":"索尼相机供应情况","time_sensitive":true}</web_search>')
        self.assertEqual(request["query"], "索尼相机供应情况")
        self.assertTrue(request["time_sensitive"])
        self.assertIsNone(ws.parse_search_request('<web_search>{"query":"x","time_sensitive":"yes"}</web_search>'))
        self.assertIsNone(ws.parse_search_request("普通聊天回复"))

    def test_worker_has_hard_timeout_and_no_shell(self):
        with patch.object(ws.subprocess, "run", return_value=Mock(returncode=0, stdout='{"results":[]}')) as run:
            ws._run_lookup(ws.SearchPlan("公开问题", True, 2026), 4)
            self.assertEqual(run.call_args.kwargs["timeout"], ws.SEARCH_DEADLINE)
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertNotIn("agy", " ".join(run.call_args.args[0]))

    def test_article_dates_do_not_confuse_match_time_with_publication(self):
        parser = ws._ArticleParser()
        parser.feed('<main><time datetime="2026-11-14">决赛时间</time><script>ignore me</script><article>赛程正文</article></main>')
        self.assertEqual(parser.published, "")
        self.assertIn("赛程正文", parser.parts)
        self.assertNotIn("ignore me", parser.parts)
        parser.feed('<meta property="article:published_time" content="2026-10-01">')
        self.assertEqual(parser.published, "2026-10-01")

    def test_old_and_non_web_results_are_not_current_evidence(self):
        plan = ws.SearchPlan("2026 英雄联盟全球总决赛", True, 2026)
        raw = [
            {"title": "2014年世界赛冠军", "href": "https://example.org/2014", "body": "2014年的结果"},
            {"title": "bad", "href": "javascript:alert(1)"},
            {"title": "2026 Worlds", "href": "https://lolesports.com/news/worlds", "body": "2026赛程"},
            {"title": "duplicate", "href": "https://lolesports.com/news/worlds"},
        ]
        rows = ws._normalize(raw, plan, 4)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "lolesports.com")
        context = ws._format(plan, rows, NOW.isoformat())
        self.assertIn("发布日期：未提供", context)
        self.assertIn("https://lolesports.com/news/worlds", context)
        self.assertIn("不是完整正文", context)

    def test_dated_news_and_historical_results_keep_dates(self):
        plan = ws.SearchPlan("2014 全球总决赛", False, 2014)
        rows = ws._normalize([{"title": "2014冠军", "url": "https://lolesports.com/news/2014", "date": "2014-10-20", "body": "历史报道"}], plan, 4)
        self.assertEqual(rows[0]["published"], "2014-10-20")

    def test_old_dates_in_reference_text_do_not_discard_current_software(self):
        plan = ws.SearchPlan("2026 Python latest stable version", True, 2026)
        rows = ws._normalize([{"title": "Latest Python release", "href": "https://www.python.org/downloads/", "body": "Also links to historical Python releases from 2014."}], plan, 4)
        self.assertEqual(len(rows), 1)

    def test_failure_is_explicit(self):
        context = ws._format(ws.SearchPlan("2026 世界赛", True, 2026), [], NOW.isoformat())
        self.assertIn("未取得可核实的资料", context)
        self.assertIn("不要编造", context)

    def test_cache_avoids_duplicate_network_queries(self):
        rows = [{"title": "2026赛程", "url": "https://example.org/a", "source": "example.org", "published": "2026-10-26", "snippet": "赛事报道"}]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch.object(ws, "CACHE_PATH", Path(directory) / "cache.json"), patch.object(ws, "_run_lookup", return_value=rows) as lookup:
            first = ws.search_web_context("英雄联盟全球总决赛")
            second = ws.search_web_context("英雄联盟全球总决赛")
            self.assertIn("https://example.org/a", first)
            self.assertIn("短期缓存", second)
            self.assertEqual(lookup.call_count, 1)

    def test_lookup_failure_cannot_silently_drop_context(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch.object(ws, "CACHE_PATH", Path(directory) / "cache.json"), patch.object(ws, "_run_lookup", side_effect=OSError("offline")):
            context = ws.search_web_context("英雄联盟全球总决赛")
            self.assertIn("未取得可核实的资料", context)


class ChatIntegrationTests(unittest.TestCase):
    def chat(self, replies, plugin_context=""):
        source = Path(__file__).parent/'app'/'clawbot_service.py'
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "ask_ai_chat")
        namespace = {"ModelCallError": ModelCallError, "re": __import__("re"), "json": json, "os": __import__("os"), "time": time, "subprocess": subprocess,
                     "FUND_KEYWORDS": (), "SEARCH_INSTRUCTIONS": ws.SEARCH_INSTRUCTIONS,
                     "parse_search_request": ws.parse_search_request, "search_allowed": ws.search_allowed, "filter_source_links": ws.filter_source_links}
        for name in ("CONVERSATION_INSTRUCTIONS", "format_history", "memory_instruction",
                     "normalize_reply", "parse_response", "select_memories"):
            namespace[name] = getattr(ce, name)
        namespace["add_user_memory"] = Mock()
        for name, result in {"check_and_update_persona": "", "load_history": [], "build_fund_report": "",
                             "build_plugin_context": plugin_context, "send_typing": None,
                             "load_persona": "测试角色", "load_user_memory": [],
                             "get_beijing_time_str": "2026-10-26", "save_history": None,
                             "extract_new_memory_in_background": None, "log": None}.items():
            namespace[name] = Mock(return_value=result)
        namespace["invoke_agy_llm"] = Mock(side_effect=replies)
        namespace["search_web_context"] = Mock(return_value="【联网核实资料】检索成功，来源 https://example.org/current")
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        return namespace

    def test_unknown_topic_can_request_lookup_without_a_keyword(self):
        ns = self.chat(['<web_search>{"query":"索尼相机供应情况","time_sensitive":true}</web_search>', "查到一条新信息 https://example.org/current"])
        reply = ns["ask_ai_chat"]("url", "token", "user", "ctx", "索尼这款相机已经能买到了吗")
        self.assertIn("https://example.org/current", reply)
        self.assertEqual(ns["search_web_context"].call_count, 1)
        self.assertEqual(ns["invoke_agy_llm"].call_count, 2)
        saved = ns["save_history"].call_args.args[0]
        self.assertNotIn("<web_search>", saved[-1]["content"])
        self.assertIn("联网核实资料", ns["invoke_agy_llm"].call_args.args[0])

    def test_chitchat_needs_one_model_call_and_no_lookup(self):
        ns = self.chat(["我在呢"])
        self.assertEqual(ns["ask_ai_chat"]("url", "token", "user", "ctx", "在吗"), "我在呢")
        ns["search_web_context"].assert_not_called()
        self.assertEqual(ns["invoke_agy_llm"].call_count, 1)

    def test_no_unbounded_search_or_exposed_protocol(self):
        request = '<web_search>{"query":"另一件公开的事","time_sensitive":true}</web_search>'
        ns = self.chat([request, request])
        reply = ns["ask_ai_chat"]("url", "token", "user", "ctx", "了解这家公司")
        self.assertNotIn("<web_search>", reply)
        self.assertEqual(ns["search_web_context"].call_count, 1)

    def test_explicit_offline_request_is_enforced(self):
        request = '<web_search>{"query":"人物近况","time_sensitive":true}</web_search>'
        ns = self.chat([request, "我没有联网核实"])
        ns["ask_ai_chat"]("url", "token", "user", "ctx", "不要联网，聊聊这个人")
        ns["search_web_context"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
