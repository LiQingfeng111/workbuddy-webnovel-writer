# -*- coding: utf-8 -*-
"""Unit tests for scripts/novel.py (standard library only).

Run from the repository root:
    python -m unittest discover -s tests -v
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("novel", REPO / "scripts" / "novel.py")
novel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(novel)


class NovelCLITestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "book"

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, command, *extra):
        """Call the CLI in-process and return (exit_code, captured_stdout)."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = novel.main([command, "--root", str(self.root), *extra])
        return code, buf.getvalue()

    def init_book(self, **kwargs):
        opts = ["--title", "测试书", "--min", "1", "--target", "2", "--max", "100"]
        for key, value in kwargs.items():
            opts += ["--" + key.replace("_", "-"), str(value)]
        code, out = self.run_cli("init", *opts)
        self.assertEqual(code, 0, out)
        return out

    # ------------------------------------------------------------------
    def test_text_stats_excludes_frontmatter_and_comments(self):
        text = "---\n标题: 甲\n---\n\n# 第一章\n\n你好<!-- 注释里的字 -->世界\n"
        stats = novel.text_stats(text)
        self.assertEqual(stats["han"], 7)  # 第一章 + 你好 + 世界
        self.assertNotIn("注释", novel.clean_body(text))

    def test_frontmatter_roundtrip(self):
        meta = {"标题": "开端", "章号": "1", "状态": "待审"}
        parsed, body = novel.parse_frontmatter(novel.dump_frontmatter(meta) + "正文\n")
        self.assertEqual(parsed, meta)
        self.assertEqual(body.strip(), "正文")

    def test_chapter_helpers(self):
        self.assertEqual(novel.chapter_no(7), "007")
        self.assertTrue(novel.CHAPTER_FILE_RE.match("第012章_第十分钟.md"))
        self.assertIsNone(novel.CHAPTER_FILE_RE.match("草稿.md"))
        self.assertEqual(novel.safe_name("第/一:章?"), "第一章")

    def test_safe_rel_rejects_traversal(self):
        with self.assertRaises(ValueError):
            novel.safe_rel(self.root, "../outside.md")
        with self.assertRaises(ValueError):
            novel.safe_rel(self.root, Path("03-正文") / ".." / ".." / "x.md")

    def test_init_creates_book_and_state(self):
        self.init_book()
        self.assertTrue((self.root / ".novel" / "config.json").is_file())
        self.assertTrue((self.root / "00-契约.md").is_file())
        self.assertTrue((self.root / "05-账本" / "伏笔台账.md").is_file())
        cfg = novel.load_config(self.root)
        self.assertEqual(cfg["title"], "测试书")
        state = novel.derive_state(self.root)
        self.assertEqual(state["last_finalized_chapter"], 0)
        self.assertEqual(state["current_chapter"], 1)
        self.assertEqual(state["total_han"], 0)

    def test_init_does_not_overwrite(self):
        self.init_book()
        marker = self.root / "00-契约.md"
        before = novel.read_text(marker)
        code, out = self.run_cli("init", "--title", "另一本", "--min", "1", "--target", "2", "--max", "100", "--force")
        self.assertEqual(code, 0, out)
        self.assertEqual(novel.read_text(marker), before, "init 不应覆盖已有作品文件")

    def test_full_chapter_loop(self):
        self.init_book()
        code, out = self.run_cli("draft", "--chapter", "1", "--title", "开端")
        self.assertEqual(code, 0, out)
        draft = self.root / "04-草稿区" / "第001章" / "稿1.md"
        self.assertTrue(draft.is_file())
        novel.write_text(
            draft,
            "---\n标题: 开端\n章号: 1\n稿次: 1\n状态: 待审\n细纲版本: v1\n汉字数: 0\n创建时间: 2026-11-11\n---\n\n"
            "# 第001章 开端\n\n雨下了一夜，他推开门，灯还亮着。\n",
        )

        code, out = self.run_cli("count", "--file", str(draft), "--json")
        self.assertEqual(code, 0)
        stats = json.loads(out)
        self.assertGreater(stats["han"], 0)
        self.assertEqual(stats["han"], novel.text_stats(novel.read_text(draft))["han"])

        code, out = self.run_cli("check", "--chapter", "1")
        self.assertEqual(code, 0, out)
        self.assertIn("确定性检查", out)

        code, out = self.run_cli("settle", "--chapter", "1", "--title", "开端", "--confirm", "测试确认")
        self.assertEqual(code, 0, out)
        final = self.root / "03-正文" / "第001章_开端.md"
        self.assertTrue(final.is_file())
        meta, body = novel.parse_frontmatter(novel.read_text(final))
        self.assertEqual(meta["状态"], "已定稿")
        self.assertNotIn("<!--", body)
        self.assertEqual(novel.derive_state(self.root)["last_finalized_chapter"], 1)

        code, out = self.run_cli("export", "--format", "txt")
        self.assertEqual(code, 0, out)
        exports = list((self.root / "06-导出").glob("*_全本.txt"))
        self.assertEqual(len(exports), 1)
        self.assertIn("第001章", novel.read_text(exports[0]))

    def test_check_blocks_placeholder(self):
        self.init_book()
        self.run_cli("draft", "--chapter", "1", "--title", "开端")
        draft = self.root / "04-草稿区" / "第001章" / "稿1.md"
        novel.write_text(
            draft,
            "---\n标题: 开端\n章号: 1\n稿次: 1\n状态: 待审\n细纲版本: v1\n汉字数: 0\n创建时间: 2026-11-11\n---\n\n"
            "# 第001章 开端\n\nTODO：这里还要补一段。\n",
        )
        code, out = self.run_cli("check", "--chapter", "1")
        self.assertEqual(code, 2)
        self.assertIn("占位符", out)

    def test_retcon_numbering(self):
        self.init_book()
        self.run_cli("retcon", "--note", "第一次修订")
        self.run_cli("retcon", "--note", "第二次修订")
        text = novel.read_text(self.root / ".novel" / "变更记录.md")
        self.assertIn("[RC-001]", text)
        self.assertIn("[RC-002]", text)

    def test_status_json_is_machine_readable(self):
        self.init_book()
        code, out = self.run_cli("status", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("last_finalized_chapter", data)
        self.assertIn("config", data)


if __name__ == "__main__":
    unittest.main()
