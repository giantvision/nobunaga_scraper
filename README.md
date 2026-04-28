# 《信长之野望 真战》攻略Wiki抓取工具

从 `https://game8.jp/nobunaga-shinsen` 抓取结构化数据，并将
Markdown + JSON 快照写入 `output/` 目录。

---

## 1. 项目结构 (Layout)

```
nobunaga_scraper/
├── scraper.py # 主抓取程序（主页 / 武将 / 战法 / 任意文章）
├── scrape_guides.py # 批量获取57篇实用文章
├── requirements.txt
├── README.md # 本文件
├── cache/ # 基于HTTP响应SHA1密钥的缓存 (.html)
└── output/
    ├── 00_index.md # 网站导航总览
    ├── characters.md / characters.json # 武将列表（含详细提示框）
    ├── tactics.md / tactics.json # 战法列表（按S/A/B等级分类）
    
├── article_<slug>_<id>.md # 通过 CLI 单独获取的文章
    └── guides/
        ├── 00_guides_index.md # 攻略实用文章目录
        └── NN_<title>_<id>.md # 各篇文章 (01〜57)
```

---

## 2. 可获取的信息 (What it captures)

| 输出 | 源页面 | 内容 |
|---|---|---|
| `output/00_index.md` | wiki 首页 | 各 h2 章节的链接列表 (8 个章节) |
| `output/characters.md` `.json` | `/737773` 武将列表 | 共 111 名武将：稀有度・成本・势力・家门・性别・肖像URL，通过**弹出提示框**可查看固有战法 / 传授战法 / 评定众技能的完整数据 |
| `output/tactics.md` `.json` | `/746982` 战法列表 | 共 260 种战法：S/A/B 等级・适性兵种・对象类别・发动概率・战法详情・出处武将 |
| `output/article_*_<id>.md` | 任意文章 | 使用 `html2text` 将正文转换为 Markdown |
| `output/guides/*.md` | 实用攻略 57篇 | 涵盖前期/培养/战斗/设施/小游戏攻略等 |

---

## 3. 安装与使用方法

```bash
pip install -r requirements.txt

# 全部包含 (主页索引 / 武将 / 战法 / 预设的长文文章 6篇)
python3 scraper.py

# 按章节
python3 scraper.py --only index
python3 scraper.py --only chars
python3 scraper.py --only tactics
python3 scraper.py --only articles

# 逐篇将任意文章转换为 Markdown
python3 scraper.py --article 752952 --article 745568

# 清除缓存并重新抓取
python3 scraper.py --force
```

批量获取57篇实用文章：

```bash
python3 scrape_guides.py # 跳过已存在的文件
python3 scrape_guides.py --refresh # 重新生成 Markdown 文件，保留 HTTP 缓存
python3 scrape_guides.py --force # 清除 HTTP 缓存并重新获取所有数据
python3 scrape_guides.py --limit 5 # 仅获取前 5 条用于功能验证
```

> HTTP 响应保存在 `cache/<sha1>.html` 中。原始抓取间隔为 `REQUEST_DELAY=1.0s`。

---

## 4. 实现逻辑 (How the scrapers work)

### 4.1 通用基础层 (`scraper.py`)

| 函数 | 作用 |
|---|---|
| `fetch(url, force=False)` | 通过 sha1 缓存进行 HTTP GET。延迟 1 秒以保持礼貌模式。 |
| `soup_of(url)` | `fetch` → `BeautifulSoup(..., “lxml”)` 的封装函数。 |
| `write_output(name, content)` | 将内容以 UTF-8 格式写入 `output/<name>.md`。 |
| `article_to_markdown(article_id)` | 从文章主体（`div.archive-style-wrapper`）中去除冗余元素，并通过 `html2text` 转换为 Markdown 格式。 |

### 4.2 主页索引 (`scrape_index`)

- 依次遍历 `div.archive-style-wrapper` 下的 `<h2>`。
- 在每个 h2 标签之后到下一个 h2 标签之间包含的 `<a href>` 中，仅输出指向站内 (`/nobunaga-shinsen/`) 的链接，格式为 `- [text](url)` 格式输出。
- 去除连续空行中的重复项并进行格式调整。

### 4.3 武将列表 (`parse_character_page`)

对 `https://game8.jp/nobunaga-shinsen/737773` 结构的观察结果：

- 单页内约有 393 个 `<table>`（各单元格大量使用嵌套表格）。
- 武将数据位于 **标题行包含“角色 / 说明 / 稀有度 / 成本 / 势力 / 家门 / 性别”的首个表格** 中。通过 `_select_character_table` 根据标题文本进行判定。
- 每位武将以 1 行 `tr`（17 个单元格）的形式呈现，布局用的 colspan/rowspan 会在中间插入空行。仅 `len(cells) >= 17` 的行才是有效数据。
- 单元格位置：`[0]=名称+图片`，`[1]=说明（含工具提示）`，`[12]=稀有度`，`[13]=成本`，`[14]=势力`，`[15]=家门`，`[16]=性别`。

#### 如何提取工具提示弹窗

在页面上将鼠标悬停于 固有战法 / 传授战法 / 评定众技能 的链接上时，会显示详细信息弹窗。
由于 HTML 中已将 `<span class="js-detail-tooltip">` 内的
`<template class="js-tooltip-content">` **静态嵌入**，因此无需额外的 HTTP 请求。

```text
<span class="js-detail-tooltip"> 武田之赤备
  <template class="js-tooltip-content">
    <table>
      <tr><th>S战法</th><th>武田之赤备</th></tr>
      
<tr><td>【适性兵种】[img alts] 【目标类型】敌军单体 【发动概率】100%
              【战法详情】... 【战法种类】固有战法： 山县昌景</td></tr>
    </table>
  </template>
</span>
```

提取流程：

1. `_extract_character_details(desc_cell)` **线性**扫描单元格内容，并记住最近出现的 `【固有战法】` / `【传授战法】` / `【评定众技能】` 标签。
2. 将随后出现的 `js-detail-tooltip` span 与该标签关联。
3. 若标签属于“战法”类，则由 `_parse_tactic_tooltip` 负责：
   - 从标题行提取 `S战法` → `rank` (`S`/`A`/`B`)
   - 对正文行文本应用 `【对象类别】 / 【发动概率】 / 【战法详情】 / 【战法种类】` 的正则表达式
   
- 从 `<img alt>` 中提取兵器/足轻/火枪/弓兵/骑兵，并汇总到 `troop_types`
4. 若标签为“评定众”，则由 `_parse_council_tooltip` 提取 `利点` / `缺点` 行并写入 `CouncilSkillDetail`。

其他附加提取项：

| 字段 | 来源 |
|---|---|
| `image_url` | 角色单元格内的 `<img data-src>`（考虑延迟加载） |
| `intro` | 正文开头的 `<p class="a-paragraph">` |
| `filters` | 将 `div.a-controllableForm` 中的字符串按 `★` / 数字 / `男・女` / 其他 分为 4 个类别 |

JSON 模式：

```jsonc
{
  “source”: “https://game8.jp/nobunaga-shinsen/737773”,
  
“intro”: “《信长之野望 真战》中，所有武将的列表…”,
  “filters”: { “rarity”: [...], “cost”: [...], ‘clan’: [...], “gender”: [...] },
  
“characters”: [
    {
      “name”: “山县昌景”,
      “detail_url”: “...”,
      ‘image_url’: "https://img.game8.jp/. ../...webp/original“,
      ”稀有度“: ”★★★★★“, ”成本“: ”7“, ”阵营“: ”武田“, ”氏族“: ”武田“, ”性别“: ”男“,
      ‘专属战术’: ”武田之赤备",
      
“transferable_tactic”: “纵横驰突”,
      “council_skill”: “别动奇袭”,
      “description”: “【消耗】7 …”,
      “unique_tactic_detail”: {
        “name”: “武田之赤备”, “rank”: ‘S’,
        “troop_types”: “兵器/足轻/铁炮/弓兵/骑兵”,
        “target”: “敌军单体”, “activation”: “100%”,
        “detail”: “被动 战斗中，…”, ‘source’: “固有战法： 山县昌景”
      
},
      “transferable_tactic_detail”: { ... },
      “council_skill_detail”: { “name”: “...”, “benefit”: “...”, ‘drawback’: “...” }
    }
  ]
}
```

### 4.4 战法一览 (`parse_tactics`)

- `_select_tactic_table` 采用包含“战法名称 / 效果 / 固有/传授 / 种类”的表格。
- 每行 4 个单元格： `[0]=战法名称(SAB)`, `[1]=效果块`, `[2]=固有/传授/事件`, `[3]=主动/被动/指挥/兵种`。
- 从 `效果` 单元格中，使用 `_TACTIC_FIELDS` 的正则表达式
  分离出 `troop / target / rate / detail / kind_type`。
- Markdown 输出按 S → A → B → 未分类的顺序以表格形式导出。

### 4.5 任意文章 → Markdown (`article_to_markdown`)

1. 以 `div.archive-style-wrapper` 为正文根节点，
2. 移除 `<script> <style> <iframe> .ad .g8-cmt #comment`，
3. 使用 `html2text.HTML2Text(body_width=0, ignore_images=True)` 转换为 Markdown，
4. 将连续换行（`\n{3,}`）规范化为 `\n\n`。

### 4.6 实用文章批处理（`scrape_guides.py`）

将 `output/00_index.md` 中的 `## 信长之野望 真战攻略实用文章` 部分作为**可信输入**使用。

逻辑：

1. `parse_guide_links()` 按行读取索引文件，从 `SECTION_HEADER` 到下一个 `## ` 之间提取 `- [title](url)`。
2. 从每个 URL 中使用 `ID_RE = /(\d+)/` 截取文章 ID。
3. 使用 `slugify(title)` 将文件名中不可用的符号替换为 `_`（保留日语汉字、假名及片假名），并截短至 60 个字符。
4. 按顺序输出 `output/guides/NN_<slug>_<id>.md`（复用 `article_to_markdown`）。
5. 若文件已存在，除非使用 `--force` / `--refresh` 选项，否则跳过。
6. 处理完毕后，生成带字数标记的目录文件 `output/guides/00_guides_index.md`。

错误处理：即使单个文章的获取失败，循环仍继续执行，并将失败次数计入 `failed` 计数器（退出代码 2）。

---

## 5. 执行结果摘要 (Reproduced output)

| 项目 | 值 |
|---|---|
| 武将 | 111 (★★★★★ 56 / ★★★★ 38 / ★★★ 17) |
| 武将工具提示完全提取 | 111/111（包含固有、传授、评定众） |
| 战法 | 260（S 145 / A / B / 未分类） |
| 实用文章 | 57/57（failed=0） |
| 军团枚举 | 26 (其中 24 个存在于名册中，斋藤·今川仅限筛选) |

---

## 6. 扩展方法 (添加新章节)

若想将其他页面转换为 Markdown 格式，几乎完全可以通过 `scraper.py` 中的以下辅助函数实现。

- 基于表格的页面 → 建议通过 **表头文本** 来定位目标表格（如 `_select_*_table`），这样更稳妥（因为 DOM 顺序可能变动）。
- 包含弹出窗口的页面 → `js-detail-tooltip` 与 `js-tooltip-content` 的组合由 `_parse_tooltip` 统一处理。
- 纯文章 → 只需调用 `article_to_markdown(article_id)`。

若需增加新的批量分类（例如：57篇武将文章批量处理），可将 `scrape_guides.py` 作为模板，只需修改 `SECTION_HEADER` 即可按相同流程运行。

---

## 7. 注意事项 (Caveats)

- game8 有时会更改标记结构。若解析器返回 0 条结果，请使用 `BeautifulSoup` 重新解析 `cache/` 目录下的 HTML 文件并更新选择器。
- 请将 `REQUEST_DELAY` 保持在 ≥ 1 秒，并尽可能使用缓存。
- 头像图片上的“新”和“S2”徽章已嵌入图片中，HTML 上没有独立元素，因此仅靠解析 HTML 无法识别（需要 OCR）。
- 输出为日语（UTF-8）。在后续处理时请注意编码问题。
