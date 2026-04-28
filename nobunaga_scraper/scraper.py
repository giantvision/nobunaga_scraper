"""game8.jp 信長の野望 真戦 攻略Wiki scraper.

Outputs structured markdown for:
  - Site index (homepage navigation + section links)
  - Character (武将) list
  - Tactic (戦法) list
  - Arbitrary article pages (converted to markdown)

Run:
    python3 scraper.py            # scrape default targets
    python3 scraper.py --only chars
    python3 scraper.py --article 737789  # scrape any article id

HTML pages are cached under cache/ so re-runs are cheap and polite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
import html2text

BASE = "https://game8.jp/nobunaga-shinsen"
ROOT = Path(__file__).parent
CACHE_DIR = ROOT / "cache"
OUT_DIR = ROOT / "output"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.7",
}
REQUEST_DELAY = 1.0  # seconds between live HTTP fetches


def fetch(url: str, force: bool = False) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    cache_file = CACHE_DIR / f"{key}.html"
    if cache_file.exists() and not force:
        return cache_file.read_text(encoding="utf-8")
    print(f"[fetch] {url}", file=sys.stderr)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    cache_file.write_text(resp.text, encoding="utf-8")
    time.sleep(REQUEST_DELAY)
    return resp.text


def soup_of(url: str) -> BeautifulSoup:
    return BeautifulSoup(fetch(url), "lxml")


# ---------------------------------------------------------------------------
# Homepage / index
# ---------------------------------------------------------------------------

def scrape_index() -> str:
    soup = soup_of(BASE)
    title = soup.title.string.strip() if soup.title else "信長の野望 真戦 Wiki"
    main = soup.select_one("div.archive-style-wrapper") or soup
    lines: list[str] = [f"# {title}", "", f"Source: {BASE}", ""]
    for h2 in main.find_all("h2"):
        section = h2.get_text(strip=True)
        lines.append(f"## {section}")
        lines.append("")
        # Collect links until next h2
        for sib in h2.find_all_next():
            if sib.name == "h2":
                break
            if sib.name == "a" and sib.get("href"):
                href = urljoin(BASE + "/", sib["href"])
                if "/nobunaga-shinsen/" not in href:
                    continue
                text = sib.get_text(" ", strip=True)
                if not text or len(text) > 80:
                    continue
                lines.append(f"- [{text}]({href})")
        lines.append("")
    # De-dupe consecutive identical lines
    out: list[str] = []
    for ln in lines:
        if out and out[-1] == ln == "":
            continue
        out.append(ln)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Character list
# ---------------------------------------------------------------------------

@dataclass
class TacticDetail:
    """Popup-extracted tactic detail (固有戦法 / 伝授戦法)."""
    name: str
    rank: str            # S / A / B (parsed from "S戦法" header)
    troop_types: str     # 兵器/足軽/鉄砲/弓兵/騎兵 — image alts
    target: str          # 対象種別
    activation: str      # 発動確率
    detail: str          # 戦法詳細 body
    source: str          # 戦法種類: 固有戦法/伝授戦法 + source character(s)


@dataclass
class CouncilSkillDetail:
    """Popup-extracted 評定衆技能 detail (利点/欠点)."""
    name: str
    benefit: str  # 利点
    drawback: str  # 欠点


@dataclass
class Character:
    name: str
    detail_url: str | None
    image_url: str | None
    rarity: str
    cost: str
    faction: str
    clan: str
    gender: str
    unique_tactic: str
    transferable_tactic: str
    council_skill: str
    description: str
    unique_tactic_detail: TacticDetail | None = None
    transferable_tactic_detail: TacticDetail | None = None
    council_skill_detail: CouncilSkillDetail | None = None


@dataclass
class CharacterListPage:
    intro: str
    filters: dict[str, list[str]]
    characters: list[Character]


_FIELD_PATTERNS = {
    "cost":       re.compile(r"【コスト】\s*([^\s【]+)"),
    "gender":     re.compile(r"【性別】\s*([^\s【]+)"),
    "faction":    re.compile(r"【勢力】\s*([^\s【]+)"),
    "clan":       re.compile(r"【家門】\s*([^\s【]+)"),
    "unique":     re.compile(r"【固有戦法】\s*([^\s【]+)"),
    "transfer":   re.compile(r"【伝授戦法】\s*([^\s【]+)"),
    "council":    re.compile(r"【評定衆技能】\s*([^\s【]+)"),
}

_TACTIC_DETAIL_FIELDS = {
    "target":     re.compile(r"【対象種別】\s*([^\n【]*)"),
    "rate":       re.compile(r"【発動確率】\s*([^\n【]*)"),
    "detail":     re.compile(r"【戦法詳細】\s*(.+?)(?=【戦法種類】|$)", re.S),
    "kind_type":  re.compile(r"【戦法種類】\s*(.+)$", re.S),
}


def _parse_tooltip(span: Tag) -> tuple[str, Tag] | None:
    """Return (label, parsed-tooltip-tree) for a js-detail-tooltip span, or None."""
    label = span.get_text(" ", strip=True)
    tpl = span.find("template", class_="js-tooltip-content")
    if not tpl:
        return None
    inner = BeautifulSoup(tpl.decode_contents(), "lxml")
    return label, inner


def _troop_alts(node: Tag) -> str:
    """Extract 兵種 names from <img alt> in the tooltip."""
    alts: list[str] = []
    for img in node.find_all("img"):
        alt = (img.get("alt") or "").strip()
        # filter the marker images (★, separators, faction badges)
        if alt and alt in {"兵器", "足軽", "鉄砲", "弓兵", "騎兵"}:
            alts.append(alt)
    return "/".join(alts)


def _parse_tactic_tooltip(label: str, inner: Tag) -> TacticDetail | None:
    rows = inner.find_all("tr")
    if len(rows) < 2:
        return None
    header_cells = rows[0].find_all(["th", "td"])
    if len(header_cells) < 2:
        return None
    rank_text = header_cells[0].get_text(" ", strip=True)  # "S戦法"
    m = re.match(r"([SAB])戦法", rank_text)
    rank = m.group(1) if m else ""
    name = header_cells[1].get_text(" ", strip=True) or label
    body_cell = rows[1].find(["th", "td"])
    if not body_cell:
        return None
    body_text = body_cell.get_text("\n", strip=True)
    troop = _troop_alts(body_cell)
    def _grab(key: str) -> str:
        m = _TACTIC_DETAIL_FIELDS[key].search(body_text)
        return re.sub(r"\s+", " ", m.group(1).strip()) if m else ""
    return TacticDetail(
        name=name,
        rank=rank,
        troop_types=troop,
        target=_grab("target"),
        activation=_grab("rate"),
        detail=_grab("detail"),
        source=_grab("kind_type"),
    )


def _parse_council_tooltip(label: str, inner: Tag) -> CouncilSkillDetail | None:
    benefit = drawback = ""
    for row in inner.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        head = cells[0].get_text(" ", strip=True)
        body = cells[1].get_text(" ", strip=True)
        if "利点" in head:
            benefit = body
        elif "欠点" in head:
            drawback = body
    if not benefit and not drawback:
        return None
    return CouncilSkillDetail(name=label, benefit=benefit, drawback=drawback)


def _extract_character_details(desc_cell: Tag) -> tuple[
    TacticDetail | None, TacticDetail | None, CouncilSkillDetail | None
]:
    """Walk tooltip spans in the description cell and label them by adjacent 【...】 marker."""
    unique = transfer = None
    council = None
    spans = desc_cell.find_all("span", class_="js-detail-tooltip")
    # Build a map from span to the nearest preceding 【...】 label text within the cell.
    cell_text_pieces: list[tuple[str, Tag | None]] = []
    for el in desc_cell.descendants:
        if isinstance(el, Tag) and el.name == "span" and "js-detail-tooltip" in (el.get("class") or []):
            cell_text_pieces.append(("__SPAN__", el))
        elif getattr(el, "string", None):
            cell_text_pieces.append((str(el.string), None))

    last_label = ""
    for text, span in cell_text_pieces:
        if span is None:
            if "【固有戦法】" in text:
                last_label = "unique"
            elif "【伝授戦法】" in text:
                last_label = "transfer"
            elif "【評定衆技能】" in text:
                last_label = "council"
            continue
        parsed = _parse_tooltip(span)
        if not parsed:
            continue
        label_text, inner = parsed
        if last_label == "council":
            council = _parse_council_tooltip(label_text, inner)
        else:
            tactic = _parse_tactic_tooltip(label_text, inner)
            if tactic is None:
                continue
            if last_label == "unique" or (last_label == "" and unique is None):
                unique = tactic
            elif last_label == "transfer" or transfer is None:
                transfer = tactic
    return unique, transfer, council


def _pick(text: str, key: str) -> str:
    m = _FIELD_PATTERNS[key].search(text)
    return m.group(1).strip() if m else ""


def _select_character_table(soup: BeautifulSoup) -> Tag | None:
    for t in soup.find_all("table"):
        first = t.find("tr")
        if not first:
            continue
        head = " ".join(c.get_text(strip=True) for c in first.find_all(["th", "td"]))
        if "キャラ" in head and "レア度" in head and "勢力" in head:
            return t
    return None


def _extract_filters(soup: BeautifulSoup) -> dict[str, list[str]]:
    """Pull the filter form options into labelled buckets."""
    form = soup.select_one("div.a-controllableForm")
    if not form:
        return {}
    raw = [t.strip() for t in form.stripped_strings if t.strip() and t.strip() != "検索条件をリセット"]
    buckets: dict[str, list[str]] = {"rarity": [], "cost": [], "clan": [], "gender": []}
    for tok in raw:
        if "★" in tok:
            buckets["rarity"].append(tok)
        elif tok in {"男", "女"}:
            buckets["gender"].append(tok)
        elif tok.isdigit():
            buckets["cost"].append(tok)
        else:
            buckets["clan"].append(tok)
    return {k: v for k, v in buckets.items() if v}


def _extract_intro(soup: BeautifulSoup) -> str:
    main = soup.select_one("div.archive-style-wrapper") or soup
    for p in main.find_all("p", class_="a-paragraph"):
        txt = p.get_text(" ", strip=True)
        if txt:
            return txt
    return ""


def parse_character_page() -> CharacterListPage:
    soup = soup_of(f"{BASE}/737773")
    table = _select_character_table(soup)
    if table is None:
        raise RuntimeError("character table not found")

    intro = _extract_intro(soup)
    filters = _extract_filters(soup)

    chars: list[Character] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 17:
            continue  # filler rows
        name = cells[0].get_text(" ", strip=True)
        if not name or name == "キャラ":
            continue
        link = cells[0].find("a")
        url = urljoin(BASE + "/", link["href"]) if link and link.get("href") else None
        img = cells[0].find("img")
        image_url = (img.get("data-src") or img.get("src")) if img else None
        if image_url and image_url.startswith("data:"):
            image_url = img.get("data-src")
        desc = cells[1].get_text(" ", strip=True)
        rarity = cells[12].get_text(" ", strip=True)
        cost = cells[13].get_text(" ", strip=True)
        faction = cells[14].get_text(" ", strip=True)
        clan = cells[15].get_text(" ", strip=True)
        gender = cells[16].get_text(" ", strip=True)
        unique_d, transfer_d, council_d = _extract_character_details(cells[1])
        chars.append(Character(
            name=name,
            detail_url=url,
            image_url=image_url,
            rarity=rarity,
            cost=cost or _pick(desc, "cost"),
            faction=faction or _pick(desc, "faction"),
            clan=clan or _pick(desc, "clan"),
            gender=gender or _pick(desc, "gender"),
            unique_tactic=(unique_d.name if unique_d else _pick(desc, "unique")),
            transferable_tactic=(transfer_d.name if transfer_d else _pick(desc, "transfer")),
            council_skill=(council_d.name if council_d else _pick(desc, "council")),
            description=desc,
            unique_tactic_detail=unique_d,
            transferable_tactic_detail=transfer_d,
            council_skill_detail=council_d,
        ))
    return CharacterListPage(intro=intro, filters=filters, characters=chars)


def parse_characters() -> list[Character]:  # back-compat shim
    return parse_character_page().characters


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def characters_to_markdown(
    chars: list[Character],
    intro: str = "",
    filters: dict[str, list[str]] | None = None,
) -> str:
    chars_sorted = sorted(chars, key=lambda x: (-len(x.rarity), x.faction, x.name))
    lines = [
        "# 武将一覧 (Character List)",
        "",
        f"Source: {BASE}/737773",
        f"Total: {len(chars_sorted)} 武将",
        "",
    ]
    if intro:
        lines += ["> " + intro, ""]
    if filters:
        lines += ["## 絞り込み条件 (filter enums)", ""]
        labels = {
            "rarity": "レア度", "cost": "コスト", "clan": "家門/勢力", "gender": "性別",
        }
        for key in ("rarity", "cost", "clan", "gender"):
            vals = filters.get(key)
            if vals:
                lines.append(f"- **{labels[key]}** ({len(vals)}): {' / '.join(vals)}")
        lines.append("")
    lines += [
        "## 概要表",
        "",
        "| 武将 | レア度 | コスト | 勢力 | 家門 | 性別 | 固有戦法 | 伝授戦法 | 評定衆技能 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in chars_sorted:
        link = f"[{c.name}]({c.detail_url})" if c.detail_url else c.name
        lines.append(
            "| {name} | {r} | {co} | {f} | {cl} | {g} | {ut} | {tt} | {cs} |".format(
                name=link, r=c.rarity or "-", co=c.cost or "-",
                f=c.faction or "-", cl=c.clan or "-", g=c.gender or "-",
                ut=c.unique_tactic or "-", tt=c.transferable_tactic or "-",
                cs=c.council_skill or "-",
            )
        )

    lines.append("")
    lines.append("## 武将詳細 (戦法 / 評定衆技能)")
    lines.append("")
    for c in chars_sorted:
        title = f"### {c.name} ({c.rarity}, {c.faction}/{c.clan}, コスト{c.cost}, {c.gender})"
        if c.detail_url:
            title += f" — [詳細ページ]({c.detail_url})"
        lines.append(title)
        lines.append("")
        if c.image_url:
            lines.append(f"![{c.name}]({c.image_url})")
            lines.append("")

        def _render_tactic(label: str, t: TacticDetail | None, fallback: str) -> None:
            if t is None:
                lines.append(f"- **{label}:** {fallback or '-'}")
                return
            lines.append(f"- **{label}: {t.name}** ({t.rank}戦法)")
            if t.troop_types:
                lines.append(f"  - 適性兵種: {t.troop_types}")
            if t.target:
                lines.append(f"  - 対象種別: {t.target}")
            if t.activation:
                lines.append(f"  - 発動確率: {t.activation}")
            if t.detail:
                lines.append(f"  - 戦法詳細: {t.detail}")
            if t.source:
                lines.append(f"  - 戦法種類: {t.source}")

        _render_tactic("固有戦法", c.unique_tactic_detail, c.unique_tactic)
        _render_tactic("伝授戦法", c.transferable_tactic_detail, c.transferable_tactic)

        if c.council_skill_detail:
            cs = c.council_skill_detail
            lines.append(f"- **評定衆技能: {cs.name}**")
            if cs.benefit:
                lines.append(f"  - 利点: {cs.benefit}")
            if cs.drawback:
                lines.append(f"  - 欠点: {cs.drawback}")
        else:
            lines.append(f"- **評定衆技能:** {c.council_skill or '-'}")
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Tactic list
# ---------------------------------------------------------------------------

@dataclass
class Tactic:
    name: str
    rank: str  # S / A / B
    detail_url: str | None
    target: str
    activation: str
    troop_types: str
    description: str
    source: str
    category: str  # 固有/伝授/事件...
    kind: str      # 能動/突撃/...


_TACTIC_FIELDS = {
    "troop":      re.compile(r"【適性兵種】\s*([^\n【]*)"),
    "target":     re.compile(r"【対象種別】\s*([^\n【]*)"),
    "rate":       re.compile(r"【発動確率】\s*([^\n【]*)"),
    "detail":     re.compile(r"【戦法詳細】\s*(.+?)\s*【戦法種類】", re.S),
    "kind_type":  re.compile(r"【戦法種類】\s*(.+)$", re.S),
}


def _select_tactic_table(soup: BeautifulSoup) -> Tag | None:
    for t in soup.find_all("table"):
        first = t.find("tr")
        if not first:
            continue
        head = " ".join(c.get_text(strip=True) for c in first.find_all(["th", "td"]))
        if "戦法名" in head and "効果" in head:
            return t
    return None


def parse_tactics() -> list[Tactic]:
    soup = soup_of(f"{BASE}/746982")
    table = _select_tactic_table(soup)
    if table is None:
        raise RuntimeError("tactic table not found")

    tactics: list[Tactic] = []
    rows = table.find_all("tr")
    for row in rows[1:]:  # skip header
        cells = row.find_all(["th", "td"])
        if len(cells) < 4:
            continue
        name_cell = cells[0]
        full_name = name_cell.get_text(" ", strip=True)
        if not full_name:
            continue
        # Split "攻其不備 （S戦法）" → name + rank
        m = re.match(r"^(.*?)\s*[（(]\s*([SAB])戦法\s*[）)]\s*$", full_name)
        name, rank = (m.group(1), m.group(2)) if m else (full_name, "")
        link = name_cell.find("a")
        url = urljoin(BASE + "/", link["href"]) if link and link.get("href") else None

        effect = cells[1].get_text("\n", strip=True)
        category = cells[2].get_text(" ", strip=True)
        kind = cells[3].get_text(" ", strip=True)

        troop = (_TACTIC_FIELDS["troop"].search(effect) or [None, ""])
        target = (_TACTIC_FIELDS["target"].search(effect) or [None, ""])
        rate = (_TACTIC_FIELDS["rate"].search(effect) or [None, ""])
        detail = (_TACTIC_FIELDS["detail"].search(effect) or [None, ""])
        kind_type = (_TACTIC_FIELDS["kind_type"].search(effect) or [None, ""])

        tactics.append(Tactic(
            name=name,
            rank=rank,
            detail_url=url,
            target=target[1].strip() if target else "",
            activation=rate[1].strip() if rate else "",
            troop_types=troop[1].strip() if troop else "",
            description=re.sub(r"\s+", " ", detail[1].strip()) if detail else "",
            source=re.sub(r"\s+", " ", kind_type[1].strip()) if kind_type else "",
            category=category,
            kind=kind,
        ))
    return tactics


def tactics_to_markdown(tactics: list[Tactic]) -> str:
    rank_order = {"S": 0, "A": 1, "B": 2, "": 3}
    tactics = sorted(tactics, key=lambda t: (rank_order.get(t.rank, 9), t.name))

    lines = [
        "# 戦法一覧 (Tactic List)",
        "",
        f"Source: {BASE}/746982",
        f"Total: {len(tactics)} 戦法",
        "",
    ]
    by_rank: dict[str, list[Tactic]] = {}
    for t in tactics:
        by_rank.setdefault(t.rank or "未分類", []).append(t)

    for rank in ["S", "A", "B", "未分類"]:
        bucket = by_rank.get(rank, [])
        if not bucket:
            continue
        lines.append(f"## {rank}戦法 ({len(bucket)})")
        lines.append("")
        lines.append("| 戦法名 | 種別 | 発動 | 対象 | 適性兵種 | 効果 | 出典 |")
        lines.append("|---|---|---|---|---|---|---|")
        for t in bucket:
            name = f"[{t.name}]({t.detail_url})" if t.detail_url else t.name
            desc = t.description.replace("|", "\\|")
            src = t.source.replace("|", "\\|")
            lines.append(
                f"| {name} | {t.category}/{t.kind} | {t.activation or '-'} | "
                f"{t.target or '-'} | {t.troop_types or '-'} | {desc or '-'} | {src or '-'} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Arbitrary article → markdown
# ---------------------------------------------------------------------------

def article_to_markdown(article_id: str | int) -> str:
    url = f"{BASE}/{article_id}"
    soup = soup_of(url)
    title = soup.title.string.strip() if soup.title else url
    body = soup.select_one("div.archive-style-wrapper") or soup.body
    # Strip noisy widgets before conversion
    for sel in ["script", "style", "iframe", ".ad", ".g8-cmt", "#comment"]:
        for el in body.select(sel):
            el.decompose()
    h = html2text.HTML2Text()
    h.ignore_images = True
    h.body_width = 0
    md = h.handle(str(body))
    md = re.sub(r"\n{3,}", "\n\n", md)
    return f"# {title}\n\nSource: {url}\n\n{md}"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

DEFAULT_ARTICLES = {
    "ranking_strongest": 737771,
    "formations": 746980,
    "tactic_ranking": 761125,
    "season2_start": 763127,
    "early_game": 737789,
    "craftsman_skills": 748721,
}


def write_output(name: str, content: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    print(f"[write] {path} ({len(content):,} chars)", file=sys.stderr)
    return path


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--only", choices=["index", "chars", "tactics", "articles"], default=None)
    p.add_argument("--article", action="append", default=[],
                   help="Extra article id to scrape (can repeat)")
    p.add_argument("--no-articles", action="store_true",
                   help="Skip the default article batch")
    p.add_argument("--force", action="store_true", help="Bypass HTTP cache")
    args = p.parse_args(list(argv) if argv is not None else None)

    if args.force and CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.html"):
            f.unlink()

    targets = {args.only} if args.only else {"index", "chars", "tactics", "articles"}

    if "index" in targets:
        write_output("00_index", scrape_index())

    if "chars" in targets:
        page = parse_character_page()
        write_output(
            "characters",
            characters_to_markdown(page.characters, intro=page.intro, filters=page.filters),
        )
        (OUT_DIR / "characters.json").write_text(
            json.dumps(
                {
                    "source": f"{BASE}/737773",
                    "intro": page.intro,
                    "filters": page.filters,
                    "characters": [asdict(c) for c in page.characters],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    if "tactics" in targets:
        tactics = parse_tactics()
        write_output("tactics", tactics_to_markdown(tactics))
        (OUT_DIR / "tactics.json").write_text(
            json.dumps([asdict(t) for t in tactics], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if "articles" in targets and not args.no_articles:
        for slug, aid in DEFAULT_ARTICLES.items():
            write_output(f"article_{slug}_{aid}", article_to_markdown(aid))

    for aid in args.article:
        write_output(f"article_{aid}", article_to_markdown(aid))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
