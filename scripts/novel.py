#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy Webnovel Writer - deterministic book-repository tools.

Only the Python standard library is used. All commands operate on plain UTF-8
Markdown/JSON files inside a single book root. The script never judges prose
quality and never fabricates review results: it only counts, checks, moves and
reports. Semantic work (drafting, reviewing, settling facts) belongs to the
model and the author, as described in SKILL.md.

Usage:
    python -X utf8 novel.py <command> --root <book-root> [options]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

FORMAT_VERSION = 1

HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
FM_RE = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
CHAPTER_FILE_RE = re.compile(r"^第(\d{3,})章[_\s]*(.*)\.md$")
DRAFT_FILE_RE = re.compile(r"^稿(\d+)\.md$")
OUTLINE_HEAD_RE = re.compile(r"^###\s*第\s*0*(\d+)\s*章")
FORESHADOW_RE = re.compile(r"F\d{3,}")
PLACEHOLDERS = ("TODO", "TBD", "待补", "XXX", "【待", "（此处补充）", "（待确认）")
ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|]')

TRUE_TEXTS = ("真源", "source")
FINAL_TEXTS = ("正文", "chapters")
LEDGER_TEXTS = ("账本", "ledger")


# --------------------------------------------------------------------------
# basic helpers
# --------------------------------------------------------------------------

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def write_text(path: Path, text: str, atomic: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if atomic:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    else:
        path.write_text(text, encoding="utf-8", newline="\n")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse the tiny flat YAML frontmatter used by this format (no PyYAML)."""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, text[m.end():]


def dump_frontmatter(meta: dict[str, str]) -> str:
    lines = ["---"]
    lines.extend(f"{k}: {v}" for k, v in meta.items())
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n"


def clean_body(text: str) -> str:
    """Body used for counting: no frontmatter, no HTML comments."""
    _, body = parse_frontmatter(text)
    return COMMENT_RE.sub("", body)


def text_stats(text: str) -> dict[str, int]:
    body = clean_body(text)
    return {
        "han": len(HAN_RE.findall(body)),
        "chars": len(body),
        "nonspace": len(re.sub(r"\s", "", body)),
    }


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def chapter_no(n: int) -> str:
    return f"{n:03d}"


def safe_name(title: str) -> str:
    name = ILLEGAL_FILENAME.sub("", title).strip().strip(".")
    return name or "未命名"


def safe_rel(root: Path, rel: str | Path) -> Path:
    """Reject absolute paths, drive letters and traversal outside the root."""
    p = Path(rel)
    if p.is_absolute() or p.drive or ".." in p.parts:
        raise ValueError(f"不安全的路径（必须是书仓内相对路径）: {rel}")
    resolved = (root / p).resolve()
    if root.resolve() not in resolved.parents and resolved != root.resolve():
        raise ValueError(f"路径越出书仓: {rel}")
    return root / p


def require_root(root: Path) -> None:
    if not (root / ".novel" / "config.json").is_file():
        raise SystemExit(f"错误：{root} 不是有效书仓（缺少 .novel/config.json），请先运行 init。")


def load_config(root: Path) -> dict:
    return json.loads(read_text(root / ".novel" / "config.json"))


def print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------
# repository scanning (the only source of truth is the files on disk)
# --------------------------------------------------------------------------

def finalized_chapters(root: Path) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    d = root / "03-正文"
    if not d.is_dir():
        return out
    for p in d.glob("*.md"):
        m = CHAPTER_FILE_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def continuous_last(chapters: list[tuple[int, Path]]) -> int:
    nums = {n for n, _ in chapters}
    last = 0
    while last + 1 in nums:
        last += 1
    return last


def draft_chapter_dirs(root: Path) -> dict[int, Path]:
    out: dict[int, Path] = {}
    d = root / "04-草稿区"
    if not d.is_dir():
        return out
    for p in d.iterdir():
        if p.is_dir():
            m = re.match(r"^第(\d{3,})章$", p.name)
            if m:
                out[int(m.group(1))] = p
    return out


def draft_files(chdir: Path) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    if not chdir.is_dir():
        return out
    for p in chdir.glob("稿*.md"):
        m = DRAFT_FILE_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def outline_volumes(root: Path) -> list[Path]:
    d = root / "02-大纲"
    return sorted(d.glob("卷*.md")) if d.is_dir() else []


def outline_section(root: Path, n: int) -> str:
    """Return the outline section for chapter n, or an empty string."""
    for v in outline_volumes(root):
        lines = read_text(v).splitlines()
        start = None
        for i, line in enumerate(lines):
            m = OUTLINE_HEAD_RE.match(line)
            if m and start is None and int(m.group(1)) == n:
                start = i
            elif m and start is not None:
                return "\n".join(lines[start:i])
        if start is not None:
            return "\n".join(lines[start:])
    return ""


def unresolved_foreshadows(root: Path) -> list[str]:
    p = root / "05-账本" / "伏笔台账.md"
    if not p.is_file():
        return []
    text = read_text(p)
    m = re.search(r"^##\s*未回收\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return []
    ids = []
    for line in m.group(1).splitlines():
        cell = line.strip()
        if not cell.startswith("|"):
            continue
        cells = [c.strip() for c in cell.strip("|").split("|")]
        if cells and re.fullmatch(r"F\d{3,}", cells[0]):
            ids.append(cells[0])
    return ids


def total_han(root: Path) -> int:
    return sum(text_stats(read_text(p))["han"] for _, p in finalized_chapters(root))


def derive_state(root: Path) -> dict:
    chapters = finalized_chapters(root)
    last = continuous_last(chapters)
    drafts = draft_chapter_dirs(root)
    pending = sorted(n for n in drafts if n > last and draft_files(drafts[n]))
    current = pending[0] if pending else last + 1
    stage = "未开始"
    report = ""
    if current in drafts:
        files = draft_files(drafts[current])
        report = drafts[current] / "审读报告.md"
        if report.is_file():
            stage = "待裁决" if "处置: 待定" in read_text(report) or "待定" in read_text(report) else "审读完成"
        elif files:
            stage = "起草中"
    elif last:
        stage = "上一章已定稿"
    return {
        "format_version": FORMAT_VERSION,
        "last_finalized_chapter": last,
        "current_chapter": current,
        "stage": stage,
        "finalized_files": len(chapters),
        "total_han": total_han(root),
        "pending_draft_chapters": pending,
        "unresolved_foreshadows": len(unresolved_foreshadows(root)),
        "next_outline_ready": bool(outline_section(root, last + 1)),
        "updated": now(),
        "note": "派生缓存，冲突时以文件为准，可用 rebuild 重建",
    }


def save_state(root: Path, extra: dict | None = None) -> dict:
    state = derive_state(root)
    if extra:
        state.update(extra)
    write_text(root / ".novel" / "state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n", atomic=True)
    return state


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def make_finding(idx: int, dimension: str, severity: str, evidence: str, problem: str, advice: str) -> dict:
    return {
        "编号": f"C-{idx:02d}",
        "维度": dimension,
        "严重度": severity,
        "证据": evidence,
        "问题": problem,
        "建议": advice,
    }


def run_checks(root: Path, chapter: int, draft: Path | None) -> list[dict]:
    findings: list[dict] = []
    idx = 1

    def add(dim, sev, ev, problem, advice):
        nonlocal idx
        findings.append(make_finding(idx, dim, sev, ev, problem, advice))
        idx += 1

    cfg = load_config(root)
    lo, target, hi = cfg.get("min_words"), cfg.get("target_words"), cfg.get("max_words")
    if not all(isinstance(v, int) and v > 0 for v in (lo, target, hi)) or not (lo <= target <= hi):
        add("文本规范", "阻断", ".novel/config.json 篇幅字段", "篇幅约定缺失或非法", "在作品契约与 config.json 中写入合法的下限/目标/上限")
        lo = target = hi = None

    finalized = dict(finalized_chapters(root))
    if chapter in finalized:
        add("章号", "阻断", finalized[chapter].name, "该章已定稿，禁止重复入档", "确认章号，或走 retcon 流程修订")

    if draft is None or not draft.is_file():
        add("稿件", "阻断", f"04-草稿区/第{chapter_no(chapter)}章/", "未找到待审稿", "先运行 draft 创建草稿")
        return findings

    text = read_text(draft)
    meta, body = parse_frontmatter(text)
    for key in ("标题", "章号", "稿次", "状态"):
        if key not in meta:
            add("文本规范", "重要", f"{draft.name} frontmatter", f"缺少必填字段「{key}」", "补齐 frontmatter")
    if meta.get("章号") and meta["章号"].lstrip("0") != str(chapter):
        add("章号", "阻断", f"frontmatter 章号: {meta['章号']}", "frontmatter 章号与命令参数不一致", "统一章号")

    plain = COMMENT_RE.sub("", body)
    stats = text_stats(text)
    if lo is not None:
        if not (lo <= stats["han"] <= hi):
            add("文本规范", "重要", f"汉字数 {stats['han']}，区间 {lo}–{hi}", "正文篇幅越界", f"按契约篇幅调整至目标 {target} 汉字左右，另存新稿")
    else:
        add("文本规范", "阻断", "未取得篇幅约定", "篇幅未核验", "先确认作品契约的章节篇幅")

    for ph in PLACEHOLDERS:
        if ph in plain:
            add("文本规范", "阻断", f"正文含「{ph}」", "占位符残留", "补写或删除占位内容")

    chdir = draft.parent
    pending_states = []
    for _, p in draft_files(chdir):
        m, _ = parse_frontmatter(read_text(p))
        if m.get("状态") in ("待审", "待裁决"):
            pending_states.append(p.name)
    if len(pending_states) > 1:
        add("稿件", "重要", "、".join(pending_states), "存在多个待审稿", "指定唯一待审稿，其余标记作废")

    section = outline_section(root, chapter)
    if not section:
        add("细纲", "重要", "02-大纲/卷*.md", "未找到本章细纲", "补细纲并经作者确认")
    else:
        ids: set[str] = set()
        for line in section.splitlines():
            if "伏笔" in line:
                ids.update(FORESHADOW_RE.findall(line))
        if ids:
            ledger = root / "05-账本" / "伏笔台账.md"
            ledger_text = read_text(ledger) if ledger.is_file() else ""
            missing = sorted(i for i in ids if i not in ledger_text)
            if missing:
                add("线索与伏笔", "重要", "细纲声明 " + "、".join(missing), "细纲中的伏笔未在台账登记", "在伏笔台账中补登记，或修正细纲")

    return findings


def pick_draft(root: Path, chapter: int) -> Path | None:
    d = root / "04-草稿区" / f"第{chapter_no(chapter)}章"
    files = draft_files(d)
    return files[-1][1] if files else None


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    root = Path(args.root).resolve()
    template = Path(__file__).resolve().parent.parent / "assets" / "book-template"
    if not template.is_dir():
        raise SystemExit(f"错误：找不到书仓模板 {template}")
    root.mkdir(parents=True, exist_ok=True)
    cfg_path = root / ".novel" / "config.json"
    if cfg_path.is_file() and not args.force:
        raise SystemExit(f"错误：{root} 已是书仓。确需重建配置请加 --force（不会删除已有文件）。")

    lo, target, hi = args.min, args.target, args.max
    if not (0 < lo <= target <= hi):
        raise SystemExit("错误：篇幅需满足 0 < min <= target <= max。")

    repl = {
        "__TITLE__": args.title,
        "__GENRE__": args.genre,
        "__PLATFORM__": args.platform,
        "__DATE__": today(),
        "__MIN__": str(lo),
        "__TARGET__": str(target),
        "__MAX__": str(hi),
    }
    copied, skipped = [], []
    for src in sorted(template.rglob("*")):
        rel = src.relative_to(template)
        dst = root / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        if dst.exists():
            skipped.append(str(rel))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in (".md", ".txt", ".json"):
            text = read_text(src)
            for k, v in repl.items():
                text = text.replace(k, v)
            write_text(dst, text)
        else:
            shutil.copy2(src, dst)
        copied.append(str(rel))

    config = {
        "title": args.title,
        "genre": args.genre,
        "platform": args.platform,
        "pov": args.pov,
        "min_words": lo,
        "target_words": target,
        "max_words": hi,
        "created": today(),
        "format_version": FORMAT_VERSION,
    }
    write_text(cfg_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    save_state(root)

    print(f"已创建书仓：{root}")
    print(f"书名：{args.title}｜题材：{args.genre}｜平台：{args.platform}")
    print(f"篇幅：{lo} / {target} / {hi} 汉字（下限/目标/上限）")
    print(f"新增文件 {len(copied)} 个，跳过已有文件 {len(skipped)} 个。")
    print("下一步：与作者确认 00-契约.md，再按 references/01、02 建世界书与大纲。")
    return 0


def cmd_status(args) -> int:
    root = Path(args.root).resolve()
    require_root(root)
    state = derive_state(root)
    cfg = load_config(root)
    foreshadows = unresolved_foreshadows(root)
    if args.json:
        state["config"] = {k: cfg.get(k) for k in ("title", "min_words", "target_words", "max_words")}
        state["unresolved_foreshadow_ids"] = foreshadows
        print_json(state)
        return 0

    print(f"《{cfg.get('title', '未命名')}》状态（从文件推导）")
    print(f"- 最后定稿章：第 {chapter_no(state['last_finalized_chapter'])} 章" if state["last_finalized_chapter"] else "- 尚无定稿章节")
    print(f"- 当前章：第 {chapter_no(state['current_chapter'])} 章｜环节：{state['stage']}")
    print(f"- 定稿文件：{state['finalized_files']} 个｜累计汉字数：{state['total_han']}")
    print(f"- 未回收伏笔：{len(foreshadows)} 条" + ("（" + "、".join(foreshadows[:8]) + "）" if foreshadows else ""))
    print(f"- 下一章细纲：{'已备' if state['next_outline_ready'] else '未找到'}")
    if state["pending_draft_chapters"]:
        print(f"- 存在未完成草稿的章：{', '.join(chapter_no(n) for n in state['pending_draft_chapters'])}")
    if state["stage"] == "起草中":
        print("- 建议：完成起草后运行 count 与 check，再做八维审读。")
    elif state["stage"] in ("待裁决", "审读完成"):
        print("- 建议：向作者完整呈报并等待裁决，勿自行 settle。")
    elif not state["next_outline_ready"]:
        print("- 建议：先补下一章细纲并经作者确认。")
    else:
        print("- 建议：可开始下一章的写作备料。")
    print("（state.json 仅为派生缓存；与文件冲突时以文件为准。）")
    return 0


def cmd_draft(args) -> int:
    root = Path(args.root).resolve()
    require_root(root)
    n = args.chapter
    if n < 1:
        raise SystemExit("错误：章号必须为正整数。")
    if n in dict(finalized_chapters(root)):
        raise SystemExit(f"错误：第 {chapter_no(n)} 章已定稿，不能创建草稿。")
    d = root / "04-草稿区" / f"第{chapter_no(n)}章"
    d.mkdir(parents=True, exist_ok=True)
    files = draft_files(d)
    version = files[-1][0] + 1 if files else 1
    target = d / f"稿{version}.md"
    if target.exists():
        raise SystemExit(f"错误：{target} 已存在。")
    title = args.title or load_config(root).get("title", "未命名章节")
    meta = {
        "标题": title,
        "章号": str(n),
        "稿次": str(version),
        "状态": "起草中",
        "细纲版本": args.outline_version,
        "汉字数": "0",
        "创建时间": today(),
    }
    body = (
        dump_frontmatter(meta)
        + f"# 第{chapter_no(n)}章 {title}\n\n"
        + "<!-- 写作提示：按 02-大纲/卷NN.md 的本章细纲起草；正文写在下方。"
        + "本注释不计入汉字数，定稿时会被移除。 -->\n\n"
    )
    write_text(target, body)
    print(f"已创建草稿：{target}")
    print("提醒：按细纲写完正文后，依次运行 count → check → 八维审读 → 作者裁决 → settle。")
    return 0


def cmd_count(args) -> int:
    p = Path(args.file).resolve()
    if not p.is_file():
        raise SystemExit(f"错误：文件不存在 {p}")
    stats = text_stats(read_text(p))
    result = {"文件": str(p), **stats, "口径": "汉字数含章标题，排除 frontmatter 与 HTML 注释"}
    if args.json:
        print_json(result)
    else:
        print(f"文件：{p.name}")
        print(f"汉字数：{stats['han']}｜字符数：{stats['chars']}｜非空白字符数：{stats['nonspace']}")
        print(f"口径：{result['口径']}")
    return 0


def cmd_check(args) -> int:
    root = Path(args.root).resolve()
    require_root(root)
    draft = Path(args.file).resolve() if args.file else pick_draft(root, args.chapter)
    findings = run_checks(root, args.chapter, draft)
    blocking = [f for f in findings if f["严重度"] == "阻断"]
    result = {
        "章号": args.chapter,
        "稿件": str(draft) if draft and draft.is_file() else None,
        "汉字数": text_stats(read_text(draft))["han"] if draft and draft.is_file() else None,
        "发现项": findings,
        "阻断项": len(blocking),
        "确定性检查通过": not blocking,
    }
    if args.json:
        print_json(result)
    else:
        print(f"第 {chapter_no(args.chapter)} 章 · 确定性检查")
        print(f"稿件：{result['稿件'] or '未找到'}｜汉字数：{result['汉字数'] if result['汉字数'] is not None else '未核验'}")
        if not findings:
            print("无发现项。")
        for f in findings:
            print(f"[{f['编号']}] {f['严重度']}｜{f['维度']}｜{f['问题']}")
            print(f"      证据：{f['证据']}")
            print(f"      建议：{f['建议']}")
        soft = len(findings) - len(blocking)
        verdict = "无发现项" if not findings else (f"通过（无阻断项，仍有 {soft} 项非阻断发现）" if not blocking else "未通过（存在阻断项）")
        print(f"阻断项 {len(blocking)} 个；确定性检查{verdict}。")
        print("注意：脚本只做确定性核对，文字质量须另做八维语义审读。")
    return 2 if blocking else 0


def cmd_query(args) -> int:
    root = Path(args.root).resolve()
    require_root(root)
    scope = args.scope
    files: list[Path] = []
    if scope in ("真源", "全部"):
        files.append(root / "00-契约.md")
        for d in ("01-世界书", "02-大纲", "05-账本"):
            files.extend(sorted((root / d).glob("*.md")))
    if scope in ("正文", "全部"):
        files.extend(sorted((root / "03-正文").glob("*.md")))
    if scope in ("草稿", "全部"):
        files.extend(sorted((root / "04-草稿区").rglob("*.md")))
    hits, total = [], 0
    for p in files:
        if not p.is_file():
            continue
        for i, line in enumerate(read_text(p).splitlines(), 1):
            if args.term in line:
                total += 1
                if len(hits) < args.limit:
                    hits.append(f"{p.relative_to(root)}:{i}: {line.strip()[:200]}")
    for h in hits:
        print(h)
    print(f"共 {total} 处命中" + (f"，显示前 {len(hits)} 处" if total > len(hits) else "") + f"｜范围：{scope}")
    return 0


def cmd_settle(args) -> int:
    root = Path(args.root).resolve()
    require_root(root)
    n = args.chapter
    if n in dict(finalized_chapters(root)):
        raise SystemExit(f"错误：第 {chapter_no(n)} 章已定稿。修订请走 retcon。")
    draft = pick_draft(root, n)
    findings = run_checks(root, n, draft)
    blocking = [f for f in findings if f["严重度"] == "阻断"]
    if blocking and not args.force:
        print("确定性检查存在阻断项，拒绝入档：")
        for f in blocking:
            print(f"  [{f['编号']}] {f['问题']}")
        print("修复后重跑 check；确需带病入档须由作者明确批准并加 --force。")
        return 2
    if draft is None or not draft.is_file():
        raise SystemExit("错误：没有可入档的草稿。")

    text = read_text(draft)
    meta, body = parse_frontmatter(text)
    body = COMMENT_RE.sub("", body).strip() + "\n"
    stats = text_stats(text)
    title = args.title or meta.get("标题") or "未命名"
    meta.update({"状态": "已定稿", "汉字数": str(stats["han"]), "定稿时间": today()})
    final_text = dump_frontmatter(meta) + body
    dst = safe_rel(root, Path("03-正文") / f"第{chapter_no(n)}章_{safe_name(title)}.md")
    if dst.exists():
        raise SystemExit(f"错误：目标已存在 {dst}，拒绝覆盖。")
    write_text(dst, final_text, atomic=True)

    log = root / ".novel" / "变更记录.md"
    entry = f"- [定稿] 第{chapter_no(n)}章《{title}》｜汉字数 {stats['han']}｜作者确认：{args.confirm}｜{now()}\n"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    save_state(root, {"last_settle": {"chapter": n, "confirm": args.confirm, "at": now()}})

    print(f"已定稿入档：{dst}")
    print(f"正文汉字数：{stats['han']}")
    print("待完成（语义沉淀，脚本不代做）：")
    print("  1) 05-账本/事实账本.md  2) 伏笔台账.md  3) 人物状态.md  4) 本书记忆.md")
    print("  5) 01-世界书/时间线.md 与人物卡当前状态  6) 运行 status 核对")
    if blocking:
        print(f"警告：带 {len(blocking)} 个阻断项入档（--force），已在呈报中记录。")
    print("已停在定稿边界，不自动开下一章。")
    return 0


def cmd_retcon(args) -> int:
    root = Path(args.root).resolve()
    require_root(root)
    log = root / ".novel" / "变更记录.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    existing = read_text(log) if log.is_file() else ""
    seq = len(re.findall(r"^-\s*\[RC-\d{3,}\]", existing, re.M)) + 1
    rid = f"RC-{seq:03d}"
    entry = f"- [{rid}] {args.note}｜{now()}\n"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    print(f"已记录设定修订：{rid}")
    print("提醒：留痕不等于已修改。先做影响分析，作者批准后再改文件，并把旧事实标记「已被该编号修订」。")
    return 0


def cmd_export(args) -> int:
    root = Path(args.root).resolve()
    require_root(root)
    cfg = load_config(root)
    chapters = finalized_chapters(root)
    if not chapters:
        raise SystemExit("错误：没有可导出的定稿章节（导出不含草稿）。")
    if args.volume:
        vol = root / "02-大纲" / f"卷{args.volume:02d}.md"
        if not vol.is_file():
            raise SystemExit(f"错误：找不到卷纲 {vol}")
        wanted = {int(m.group(1)) for m in (OUTLINE_HEAD_RE.match(l) for l in read_text(vol).splitlines()) if m}
        chapters = [(n, p) for n, p in chapters if n in wanted]
        if not chapters:
            raise SystemExit("错误：该卷纲中没有已定稿章节。")
        suffix = f"卷{args.volume:02d}"
    else:
        suffix = "全本"
    ext = args.format
    outdir = root / "06-导出"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"{safe_name(cfg.get('title', '未命名'))}_{suffix}.{ext}"
    manifest = outdir / f"{safe_name(cfg.get('title', '未命名'))}_{suffix}_来源清单.md"
    if out.exists() and not args.force:
        raise SystemExit(f"错误：{out} 已存在。确认覆盖请加 --force（旧文件会备份为 .bak）。")
    if out.exists():
        shutil.copy2(out, out.with_suffix(out.suffix + ".bak"))

    parts, rows = [], []
    for n, p in chapters:
        text = read_text(p)
        meta, body = parse_frontmatter(text)
        body = COMMENT_RE.sub("", body).strip()
        head = meta.get("标题", p.stem)
        if ext == "md":
            parts.append(f"# 第{chapter_no(n)}章 {head}\n\n{body}\n")
        else:
            parts.append(f"第{chapter_no(n)}章 {head}\n\n{body}\n")
        rows.append(f"| {chapter_no(n)} | {p.name} | {text_stats(text)['han']} | {meta.get('定稿时间', '—')} |")
    write_text(out, "\n\n".join(parts))
    write_text(
        manifest,
        f"# {suffix} 导出来源清单\n\n- 生成时间：{now()}\n- 仅包含 03-正文/ 中已定稿章节，不含草稿与候选事实。\n\n"
        "| 章号 | 文件 | 汉字数 | 定稿时间 |\n| --- | --- | --- | --- |\n" + "\n".join(rows) + "\n",
    )
    print(f"已导出：{out}")
    print(f"来源清单：{manifest}")
    print(f"共 {len(chapters)} 章，{sum(text_stats(read_text(p))['han'] for _, p in chapters)} 汉字。")
    print("平台排版与投稿请自行核对。")
    return 0


def cmd_rebuild(args) -> int:
    root = Path(args.root).resolve()
    require_root(root)
    state = save_state(root)
    print(f"已从文件重建 {root / '.novel' / 'state.json'}")
    print_json(state) if args.json else print(f"最后定稿章：{state['last_finalized_chapter']}｜累计汉字数：{state['total_han']}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", required=True, help="书仓根目录")
    common.add_argument("--json", action="store_true", help="输出机器可读 JSON（部分命令支持）")

    p = argparse.ArgumentParser(description="WorkBuddy 长篇网文书仓确定性工具（仅标准库）")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("init", parents=[common], help="从模板生成书仓")
    q.add_argument("--title", required=True, help="书名")
    q.add_argument("--genre", default="待定", help="题材")
    q.add_argument("--platform", default="待定", help="目标平台")
    q.add_argument("--pov", default="第三人称有限视角", help="叙事人称")
    q.add_argument("--min", type=int, default=1600, help="下限汉字数")
    q.add_argument("--target", type=int, default=2000, help="目标汉字数")
    q.add_argument("--max", type=int, default=2400, help="上限汉字数")
    q.add_argument("--force", action="store_true", help="允许覆盖 config.json（不删除已有文件）")
    q.set_defaults(func=cmd_init)

    q = sub.add_parser("status", parents=[common], help="从文件推导进度")
    q.set_defaults(func=cmd_status)

    q = sub.add_parser("draft", parents=[common], help="创建章节草稿骨架")
    q.add_argument("--chapter", type=int, required=True)
    q.add_argument("--title", default="")
    q.add_argument("--outline-version", default="v1")
    q.set_defaults(func=cmd_draft)

    q = sub.add_parser("count", parents=[common], help="统计汉字数")
    q.add_argument("--file", required=True)
    q.set_defaults(func=cmd_count)

    q = sub.add_parser("check", parents=[common], help="确定性检查")
    q.add_argument("--chapter", type=int, required=True)
    q.add_argument("--file", default="", help="指定待审稿，默认取最新稿")
    q.set_defaults(func=cmd_check)

    q = sub.add_parser("query", parents=[common], help="全文检索")
    q.add_argument("--term", required=True)
    q.add_argument("--scope", choices=["真源", "正文", "草稿", "全部"], default="全部")
    q.add_argument("--limit", type=int, default=60)
    q.set_defaults(func=cmd_query)

    q = sub.add_parser("settle", parents=[common], help="作者确认后定稿入档")
    q.add_argument("--chapter", type=int, required=True)
    q.add_argument("--title", default="")
    q.add_argument("--confirm", required=True, help="作者确认的原话或摘要，写入留痕")
    q.add_argument("--force", action="store_true", help="作者明确批准后允许带阻断项入档")
    q.set_defaults(func=cmd_settle)

    q = sub.add_parser("retcon", parents=[common], help="设定修订留痕")
    q.add_argument("--note", required=True)
    q.set_defaults(func=cmd_retcon)

    q = sub.add_parser("export", parents=[common], help="导出定稿合集")
    q.add_argument("--volume", type=int, default=0)
    q.add_argument("--format", choices=["txt", "md"], default="txt")
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_export)

    q = sub.add_parser("rebuild", parents=[common], help="从文件重建 state.json")
    q.set_defaults(func=cmd_rebuild)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 3
    except (OSError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
