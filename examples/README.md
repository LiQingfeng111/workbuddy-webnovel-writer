# 示例书仓

`demo-book/` 是一个**真实跑通过**的最小书仓，用来展示格式与命令效果，不是模板本身（模板在 `assets/book-template/`）。

- 书名：《长夜将明·示例》
- 篇幅约定：200 / 300 / 400 汉字（下限 / 目标 / 上限），刻意设小以便用短章演示
- 已定稿：第 001 章《楼道灯灭了》，316 汉字
- 未回收伏笔：F001
- 第 002 章细纲已备，尚未起草

## 复现这个书仓

在仓库根目录执行：

```bash
R=./examples/demo-book
P=scripts/novel.py

python -X utf8 $P init   --root $R --title "长夜将明·示例" --genre 都市异能 --platform 示例 --min 200 --target 300 --max 400
# 手工填写 02-大纲/卷01.md 与 05-账本/伏笔台账.md
python -X utf8 $P draft  --root $R --chapter 1 --title "楼道灯灭了"
# 在 04-草稿区/第001章/稿1.md 写正文
python -X utf8 $P count  --root $R --file "$R/04-草稿区/第001章/稿1.md"
python -X utf8 $P check  --root $R --chapter 1
python -X utf8 $P settle --root $R --chapter 1 --title "楼道灯灭了" --confirm "作者确认通过"
python -X utf8 $P export --root $R --volume 1 --format txt
python -X utf8 $P status --root $R --json
```

`check` 在本示例上的输出应为「无发现项」，因为细纲声明的 F001 已在伏笔台账登记、正文篇幅在区间内、无占位符残留。

## 值得看的文件

| 文件 | 看什么 |
| --- | --- |
| `00-契约.md` | 作品契约与章节篇幅小节 |
| `02-大纲/卷01.md` | `### 第 NNN 章` 细纲格式，脚本靠它定位 |
| `04-草稿区/第001章/稿1.md` | 草稿 frontmatter 与写作提示注释 |
| `03-正文/第001章_楼道灯灭了.md` | 定稿后的 frontmatter（`状态: 已定稿`、`汉字数`） |
| `05-账本/伏笔台账.md` | 未回收表的行格式 |
| `.novel/变更记录.md` | 定稿与 retcon 留痕 |
| `06-导出/` | 导出合集与来源清单（派生，可删除重建） |

## 注意

示例书仓里的 `state.json` 与 `06-导出/` 都是派生产物；`status` 会直接从 `03-正文/`、`04-草稿区/`、账本文件重新推导进度，删掉它们也不影响创作数据。
