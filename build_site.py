"""ネット公開用のページ（docs フォルダ）を作る。GitHub が自動で実行する。

docs/index.html        … 仮想売買ボード（スマホでも見られる）
docs/data/*.json       … ボードが読むデータ（株価・ニュース・持ち株）
docs/reports/*.html    … くわしい成績・レポート
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dashboard as dash

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, default=float), encoding="utf-8")


def main():
    (DOCS / "data").mkdir(parents=True, exist_ok=True)
    (DOCS / "reports").mkdir(exist_ok=True)
    page = (ROOT / "kabu" / "dashboard.html").read_text(encoding="utf-8").replace("const STATIC = false;", "const STATIC = true;")
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    print("持ち株・候補をまとめています…")
    dump(DOCS / "data" / "summary.json", dash.api_summary())
    print("今日のデイトレの途中経過…")
    try:
        dump(DOCS / "data" / "live.json", dash.api_live())
    except Exception as e:
        print("  途中経過を作れませんでした:", e)
    print("ニュースを集めています…")
    dump(DOCS / "data" / "news.json", dash.news_feed())
    print("チャートのデータを作っています…")
    cfg = dash.cfg()
    for code in ["N225"] + [c for c, _ in cfg["universe"]] + dash.extra_codes():
        dump(DOCS / "data" / f"chart_{code}.json", dash.api_chart(code))

    # くわしいレポート（あるものだけ）
    copies = [
        (ROOT / "仮想売買" / "成績.html", "swing.html"), (ROOT / "仮想売買" / "成績.png", "成績.png"),
        (ROOT / "デイトレ" / "成績.html", "daytrade.html"),
        (ROOT / "結果" / "改善レポート.html", "improve.html"),
        (ROOT / "結果" / "過去の検証.html", "backtest.html"), (ROOT / "結果" / "過去の検証.png", "過去の検証.png"),
    ]
    for src, name in copies:
        if src.exists():
            shutil.copy(src, DOCS / "reports" / name)
    print("完成:", DOCS / "index.html")


if __name__ == "__main__":
    main()
