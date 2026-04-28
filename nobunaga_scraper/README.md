# 信長の野望 真戦 攻略Wiki Scraper

Scrapes structured data from `https://game8.jp/nobunaga-shinsen` and writes
markdown + JSON snapshots to `output/`.

---

## 1. プロジェクト構成 (Layout)

```
nobunaga_scraper/
├── scraper.py            # メインスクレイパー (homepage / 武将 / 戦法 / 任意記事)
├── scrape_guides.py      # お役立ち記事 57本のバッチ取得
├── requirements.txt
├── README.md             # 本ファイル
├── cache/                # HTTPレスポンス sha1-keyed キャッシュ (.html)
└── output/
    ├── 00_index.md                       # ホームページのナビ全体マップ
    ├── characters.md / characters.json   # 武将一覧 (詳細 tooltip 含む)
    ├── tactics.md / tactics.json         # 戦法一覧 (S/A/B ランク別)
    ├── article_<slug>_<id>.md            # CLI から個別取得した記事
    └── guides/
        ├── 00_guides_index.md            # 攻略お役立ち記事 TOC
        └── NN_<title>_<id>.md            # 各記事 (01〜57)
```

---

## 2. 取得できる情報 (What it captures)

| Output | 元ページ | 内容 |
|---|---|---|
| `output/00_index.md` | wiki homepage | h2 セクションごとのリンク一覧 (8 セクション) |
| `output/characters.md` `.json` | `/737773` 武将一覧 | 全 111 武将: レア度・コスト・勢力・家門・性別・ポートレートURL、**popup tooltip** から 固有戦法 / 伝授戦法 / 評定衆技能 の完全データ |
| `output/tactics.md` `.json` | `/746982` 戦法一覧 | 全 260 戦法: S/A/B ランク・適性兵種・対象種別・発動確率・戦法詳細・出典武将 |
| `output/article_*_<id>.md` | 任意の記事 | `html2text` で本文を Markdown 化 |
| `output/guides/*.md` | お役立ち記事 57本 | 序盤/育成/戦闘/施設/ミニゲーム攻略など全網羅 |

---

## 3. インストールと使い方

```bash
pip install -r requirements.txt

# 全部入り (homepage index / 武将 / 戦法 / 既定の長文記事 6本)
python3 scraper.py

# セクション単位
python3 scraper.py --only index
python3 scraper.py --only chars
python3 scraper.py --only tactics
python3 scraper.py --only articles

# 任意の記事を追加で 1本ずつ Markdown 化
python3 scraper.py --article 752952 --article 745568

# キャッシュを破棄して再取得
python3 scraper.py --force
```

お役立ち記事 57本のバッチ取得:

```bash
python3 scrape_guides.py            # 既存ファイルはスキップ
python3 scrape_guides.py --refresh  # md は再生成、HTTP キャッシュは保持
python3 scrape_guides.py --force    # HTTP キャッシュも破棄して全件再取得
python3 scrape_guides.py --limit 5  # 動作確認用に先頭5件のみ
```

> HTTP レスポンスは `cache/<sha1>.html` に保存。生フェッチ間は `REQUEST_DELAY=1.0s`。

---

## 4. 実装ロジック (How the scrapers work)

### 4.1 共通基盤 (`scraper.py`)

| 関数 | 役割 |
|---|---|
| `fetch(url, force=False)` | sha1 キャッシュ越しの HTTP GET。1秒スリープでマナーモード。 |
| `soup_of(url)` | `fetch` → `BeautifulSoup(..., "lxml")` のラッパー。 |
| `write_output(name, content)` | `output/<name>.md` への UTF-8 書き出し。 |
| `article_to_markdown(article_id)` | 記事本体 (`div.archive-style-wrapper`) からノイズ要素を除去し `html2text` で Markdown 化。 |

### 4.2 ホームページ index (`scrape_index`)

- `div.archive-style-wrapper` 配下の `<h2>` を順に走査。
- 各 h2 の次の h2 までに含まれる `<a href>` のうち、ホスト内 (`/nobunaga-shinsen/`) のリンクだけを `- [text](url)` として吐く。
- 連続する空行は重複排除して整形。

### 4.3 武将一覧 (`parse_character_page`)

`https://game8.jp/nobunaga-shinsen/737773` の構造観察結果:

- 1ページ内に `<table>` が約 393個ある (各セルがネストテーブルを多用)。
- 武将データは **ヘッダ行が「キャラ / 説明 / レア度 / コスト / 勢力 / 家門 / 性別」を持つ最初のテーブル** にある。`_select_character_table` がヘッダのテキストで判定。
- 各武将は `tr` 1本 (cells 17個) として現れ、レイアウト用の colspan/rowspan で間に空 tr が挟まる。`len(cells) >= 17` の行だけが本物。
- セル位置: `[0]=name+image`, `[1]=説明 (tooltip 含む)`, `[12]=レア度`, `[13]=コスト`, `[14]=勢力`, `[15]=家門`, `[16]=性別`。

#### Tooltip popup の取り出し方

ページ上で 固有戦法 / 伝授戦法 / 評定衆技能 のリンクをホバーすると詳細 popup が表示される。
HTML 内には `<span class="js-detail-tooltip">` の中に
`<template class="js-tooltip-content">` として **静的に埋め込まれている**ため、追加の HTTP リクエストは不要。

```text
<span class="js-detail-tooltip"> 武田之赤備
  <template class="js-tooltip-content">
    <table>
      <tr><th>S戦法</th><th>武田之赤備</th></tr>
      <tr><td>【適性兵種】[img alts] 【対象種別】敵軍単体 【発動確率】100%
              【戦法詳細】... 【戦法種類】固有戦法： 山県昌景</td></tr>
    </table>
  </template>
</span>
```

抽出フロー:

1. `_extract_character_details(desc_cell)` がセル内を **線形に** 走査し、直近に出現した `【固有戦法】` / `【伝授戦法】` / `【評定衆技能】` ラベルを記憶。
2. 次に来る `js-detail-tooltip` span をそのラベルにひも付け。
3. ラベルが「戦法」系なら `_parse_tactic_tooltip` が
   - ヘッダ行から `S戦法` → `rank` (`S`/`A`/`B`)
   - 本体行のテキストに対し `【対象種別】 / 【発動確率】 / 【戦法詳細】 / 【戦法種類】` の正規表現を適用
   - `<img alt>` から 兵器/足軽/鉄砲/弓兵/騎兵 を `troop_types` に集約
4. ラベルが「評定衆」なら `_parse_council_tooltip` が `利点` / `欠点` 行を取り出して `CouncilSkillDetail` に。

その他の追加抽出:

| フィールド | 取得元 |
|---|---|
| `image_url` | キャラセル内 `<img data-src>` (lazyload を考慮) |
| `intro` | 本文先頭の `<p class="a-paragraph">` |
| `filters` | `div.a-controllableForm` の文字列を `★` / 数字 / `男・女` / それ以外 で 4バケットに振り分け |

JSON スキーマ:

```jsonc
{
  "source": "https://game8.jp/nobunaga-shinsen/737773",
  "intro": "信長の野望 真戦における、全武将を一覧で…",
  "filters": { "rarity": [...], "cost": [...], "clan": [...], "gender": [...] },
  "characters": [
    {
      "name": "山県昌景",
      "detail_url": "...",
      "image_url": "https://img.game8.jp/.../...webp/original",
      "rarity": "★★★★★", "cost": "7", "faction": "武田", "clan": "武田", "gender": "男",
      "unique_tactic": "武田之赤備",
      "transferable_tactic": "縦横馳突",
      "council_skill": "別働奇襲",
      "description": "【コスト】7 …",
      "unique_tactic_detail": {
        "name": "武田之赤備", "rank": "S",
        "troop_types": "兵器/足軽/鉄砲/弓兵/騎兵",
        "target": "敵軍単体", "activation": "100%",
        "detail": "受動 戦闘中、…", "source": "固有戦法： 山県昌景"
      },
      "transferable_tactic_detail": { ... },
      "council_skill_detail": { "name": "...", "benefit": "...", "drawback": "..." }
    }
  ]
}
```

### 4.4 戦法一覧 (`parse_tactics`)

- `_select_tactic_table` は「戦法名 / 効果 / 固有/伝授 / 種類」を持つテーブルを採用。
- 各行 4セル: `[0]=戦法名(SAB)`, `[1]=効果ブロブ`, `[2]=固有/伝授/事件`, `[3]=能動/受動/指揮/兵種`。
- `効果` セルから `_TACTIC_FIELDS` の正規表現で
  `troop / target / rate / detail / kind_type` を分離。
- Markdown 出力は S → A → B → 未分類の順に表として書き出し。

### 4.5 任意記事 → Markdown (`article_to_markdown`)

1. `div.archive-style-wrapper` を本文ルートとし、
2. `<script> <style> <iframe> .ad .g8-cmt #comment` を除去、
3. `html2text.HTML2Text(body_width=0, ignore_images=True)` で Markdown 化、
4. 連続改行 (`\n{3,}`) を `\n\n` に正規化。

### 4.6 お役立ち記事バッチ (`scrape_guides.py`)

`output/00_index.md` の `## 信長の野望 真戦の攻略お役立ち記事` セクションを**信頼できる入力**として使う。

ロジック:

1. `parse_guide_links()` が index ファイルを行単位で読み、`SECTION_HEADER` から次の `## ` までの間にある `- [title](url)` を抽出。
2. 各 URL から `ID_RE = /(\d+)/` で記事 ID を切り出し。
3. `slugify(title)` がファイル名に使えない記号を `_` に置換 (日本語の漢字・かな・カナは保持) して 60文字に切り詰め。
4. `output/guides/NN_<slug>_<id>.md` を順番に書き出し (`article_to_markdown` を再利用)。
5. 既にファイルがある場合は `--force` / `--refresh` でない限りスキップ。
6. 全件処理後、文字数つきの TOC `output/guides/00_guides_index.md` を生成。

エラー処理: 個別記事のフェッチが失敗してもループは続行し、`failed` カウンタに加算 (終了コード 2)。

---

## 5. 実行結果サマリ (Reproduced output)

| 項目 | 値 |
|---|---|
| 武将 | 111 (★★★★★ 56 / ★★★★ 38 / ★★★ 17) |
| 武将 tooltip 完全抽出 | 111/111 (固有・伝授・評定衆すべて) |
| 戦法 | 260 (S 145 / A / B / 未分類) |
| お役立ち記事 | 57/57 (failed=0) |
| クラン enum | 26 (うち 24 がロスター内に存在、斎藤・今川は filter のみ) |

---

## 6. 拡張のしかた (Adding new sections)

別ページを Markdown 化したいときは、ほぼ全て `scraper.py` 内の以下のヘルパで完結する。

- 表ベースのページ → `_select_*_table` のように **ヘッダのテキストで** 目的テーブルを掴むのが安全 (DOM 順は変動する)。
- popup を含むページ → `js-detail-tooltip` + `js-tooltip-content` の組み合わせを `_parse_tooltip` が共通ハンドル。
- 純粋な記事 → `article_to_markdown(article_id)` を呼ぶだけ。

新しいバッチカテゴリ (例: 武将記事 57本一括) を増やす場合は `scrape_guides.py` をテンプレートに、`SECTION_HEADER` を変えれば同じ流れで動く。

---

## 7. 注意事項 (Caveats)

- game8 は時々マークアップを変える。パーサが 0件を返したら `cache/` の HTML を `BeautifulSoup` で開き直してセレクタを更新。
- `REQUEST_DELAY` は ≥ 1秒を維持し、なるべくキャッシュを使う。
- ポートレート画像の「新」「S2」バッジは画像にベイクされており HTML 上に独立要素がないため、HTML パースだけでは判別不可 (OCR が必要)。
- 出力は日本語 (UTF-8)。下流で扱う際はエンコーディングに注意。
