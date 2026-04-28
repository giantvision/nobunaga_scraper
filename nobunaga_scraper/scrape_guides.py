"""Batch-scrape the 攻略お役立ち記事 (guide articles) listed in output/00_index.md.

Reads the お役立ち section, normalises titles into safe filenames, fetches each
article via scraper.article_to_markdown (HTTP-cached), and writes:

  output/guides/<NN>_<slug>_<id>.md   one file per article
  output/guides/00_guides_index.md    table of contents with links + char count

Run:
    python3 scrape_guides.py            # incremental (skip files already written)
    python3 scrape_guides.py --force    # bust the HTTP cache + rewrite all files
    python3 scrape_guides.py --refresh  # rewrite md files but keep the HTML cache
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from scraper import (
    BASE,
    CACHE_DIR,
    OUT_DIR,
    article_to_markdown,
)

INDEX_FILE = OUT_DIR / "00_index.md"
GUIDES_DIR = OUT_DIR / "guides"
SECTION_HEADER = "## 信長の野望 真戦の攻略お役立ち記事"

LINK_RE = re.compile(r"^- \[([^\]]+)\]\((https?://[^\)]+)\)\s*$")
ID_RE = re.compile(r"/(\d+)(?:[/?#]|$)")
SLUG_BAD = re.compile(r"[^\w぀-ヿ㐀-鿿\-]+")


def parse_guide_links(index_path: Path) -> list[tuple[str, str, str]]:
    """Return [(title, url, article_id)] for every link in the お役立ち section."""
    if not index_path.exists():
        raise SystemExit(f"index file not found: {index_path}. Run scraper.py --only index first.")
    text = index_path.read_text(encoding="utf-8").splitlines()
    in_section = False
    out: list[tuple[str, str, str]] = []
    for line in text:
        if line.startswith("## "):
            in_section = line.strip() == SECTION_HEADER
            continue
        if not in_section:
            continue
        m = LINK_RE.match(line.strip())
        if not m:
            continue
        title, url = m.group(1), m.group(2)
        idm = ID_RE.search(url)
        if not idm:
            continue
        out.append((title, url, idm.group(1)))
    return out


def slugify(title: str) -> str:
    s = SLUG_BAD.sub("_", title).strip("_")
    return s[:60] or "guide"


def write_index(entries: list[dict]) -> Path:
    GUIDES_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 攻略お役立ち記事 一覧",
        "",
        f"Source section: {BASE}",
        f"Total: {len(entries)} 記事",
        "",
        "| # | タイトル | 記事ID | 文字数 | ローカル | 元記事 |",
        "|---|---|---|---|---|---|",
    ]
    for e in entries:
        lines.append(
            f"| {e['n']:02d} | {e['title']} | {e['id']} | {e['size']:,} | "
            f"[{e['filename']}]({e['filename']}) | [game8]({e['url']}) |"
        )
    path = GUIDES_DIR / "00_guides_index.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true",
                   help="Drop HTTP cache then refetch + rewrite everything.")
    p.add_argument("--refresh", action="store_true",
                   help="Rewrite md files even if present; keep HTTP cache.")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after N articles (debug).")
    args = p.parse_args(argv)

    if args.force and CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.html"):
            f.unlink()

    items = parse_guide_links(INDEX_FILE)
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("[warn] no guide links found — is the index up to date?", file=sys.stderr)
        return 1

    GUIDES_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    skipped = written = failed = 0

    for n, (title, url, aid) in enumerate(items, 1):
        slug = slugify(title)
        filename = f"{n:02d}_{slug}_{aid}.md"
        out_path = GUIDES_DIR / filename
        if out_path.exists() and not (args.force or args.refresh):
            md = out_path.read_text(encoding="utf-8")
            entries.append({
                "n": n, "title": title, "id": aid, "url": url,
                "filename": filename, "size": len(md),
            })
            skipped += 1
            print(f"[skip ] {filename} (already exists)", file=sys.stderr)
            continue
        try:
            md = article_to_markdown(aid)
        except Exception as exc:  # network / parse error
            failed += 1
            print(f"[fail ] {url}: {exc}", file=sys.stderr)
            continue
        out_path.write_text(md, encoding="utf-8")
        written += 1
        entries.append({
            "n": n, "title": title, "id": aid, "url": url,
            "filename": filename, "size": len(md),
        })
        print(f"[write] {filename} ({len(md):,} chars)", file=sys.stderr)

    idx_path = write_index(entries)
    print(
        f"\nDone. wrote={written} skipped={skipped} failed={failed} "
        f"total_listed={len(items)}\nIndex: {idx_path}",
        file=sys.stderr,
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
