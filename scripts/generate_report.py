#!/usr/bin/env python3
# copies out/<latest YYYY-MM-DD>/** → archive/<same>/** and updates index.html
import os, shutil, re
from pathlib import Path
import datetime as dt

ROOT  = Path(__file__).resolve().parents[1]
OUT   = ROOT / "out"
ARCH  = ROOT / "archive"
INDEX = ROOT / "index.html"

def is_ymd(name: str) -> bool:
    try:
        dt.datetime.strptime(name, "%Y-%m-%d")
        return True
    except Exception:
        return False

def find_latest_out_dir() -> Path | None:
    if not OUT.exists():
        return None
    dated = [p for p in OUT.iterdir() if p.is_dir() and is_ymd(p.name)]
    if not dated:
        return None
    return sorted(dated, key=lambda p: p.name)[-1]  # 가장 최신 폴더

def copy_dir(src: Path) -> Path:
    dst = ARCH / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print("[copy]", src, "→", dst)
    return dst

def update_index(latest_dir: Path):
    if not INDEX.exists():
        print("[warn] index.html not found; skip update")
        return

    ymd = latest_dir.name
    files = {p.name: p for p in latest_dir.iterdir() if p.is_file()}

    links = []
    if f"bm20_daily_{ymd}.html" in files:
        links.append(f'<a href="archive/{ymd}/bm20_daily_{ymd}.html">HTML</a>')
    if f"bm20_daily_{ymd}.pdf" in files:
        links.append(f'<a href="archive/{ymd}/bm20_daily_{ymd}.pdf">PDF</a>')

    img_tag = ""
    if f"bm20_bar_{ymd}.png" in files:
        img_tag = (
          f'<img src="archive/{ymd}/bm20_bar_{ymd}.png" alt="performance" '
          f'style="max-width:100%;border:1px solid #eee;border-radius:8px;margin-top:8px;" />'
        )

    block = f"""
<div>
  <strong>Latest: {ymd}</strong> — {' | '.join(links) if links else 'no files'}
  {img_tag}
</div>
""".strip()

    html = INDEX.read_text(encoding="utf-8")
    new_html = re.sub(
        r"(<!--LATEST_START-->)(.*?)(<!--LATEST_END-->)",
        lambda m: f"{m.group(1)}\n{block}\n{m.group(3)}",
        html, flags=re.S
    )
    INDEX.write_text(new_html, encoding="utf-8")
    print("[update] index.html latest block updated")

def main():
    latest = find_latest_out_dir()
    if latest is None:
        raise SystemExit(f"[generate_report] no dated folder under {OUT} (e.g. out/2025-08-12)")
    dst = copy_dir(latest)
    update_index(dst)
    # GitHub Pages(Jekyll) 무시 파일
    (ROOT / ".nojekyll").write_text("", encoding="utf-8")
    print("[done] site updated")

if __name__ == "__main__":
    main()
