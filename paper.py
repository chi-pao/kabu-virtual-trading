"""毎日の仮想売買（本物のお金は使わない）

使い方:  py paper.py   … 平日の15:30以降（株価が確定したあと）に1日1回
  ・前回から今日までの株価で、注文の約定・損切り・利確を処理
  ・ニュースと世界情勢をチェックして、明日の注文を決める
  ・結果を 仮想売買\成績.html にまとめ、売買記録から改善案も出す
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from kabu import data, news, report
from kabu.analyze import analyze, tables_html
from kabu.engine import Engine
from kabu.views import evaluate

ROOT = Path(__file__).parent
DIR = ROOT / "仮想売買"
STATE = DIR / "state.json"
MACRO_STOP = -6   # 世界情勢ニュースの点数がこれ以下なら、新しく買わない
STOCK_STOP = -3   # 銘柄ニュースの点数がこれ以下なら、その株は買わない


def main():
    DIR.mkdir(exist_ok=True)
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    names = dict(cfg["universe"])
    codes = list(names)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None

    print("株価データを取得中…")
    prices = data.load_prices(codes + [data.INDEX], period="1y")
    ix = prices.pop(data.INDEX)
    ind = {c: data.add_indicators(d, cfg["breakout_days"]) for c, d in prices.items()}
    ix_sma = ix["Close"].rolling(75).mean()
    dates = sorted(set().union(*[d.index for d in prices.values()]))
    latest = dates[-1]

    print("企業の数字を確認中…")
    fund = data.load_fundamentals(codes, ROOT / "結果" / "企業データ.json")
    fund_ok = {c: data.fundamental_ok(fund.get(c), cfg["fundamental"])[0] for c in codes}
    qual = {c: data.quality(fund.get(c), cfg.get("quality_parts", {})) for c in codes}

    eng = Engine(cfg, state["engine"] if state else None)
    last = pd.Timestamp(state["last_date"]) if state else None
    todo = [d for d in dates if last is None or d > last]
    if last is None:
        todo = [latest]
        eng._log(latest.strftime("%Y-%m-%d"), f"仮想売買スタート（資金 {cfg['capital']:,}円）")
    if not todo:
        print(f"新しい株価はまだありません（最後に処理した日: {last.date()}）。")
        write_page(eng, cfg, ix, state.get("news", {}), views_html(eng, cfg, ind, fund, fund_ok, names, ix, ix_sma))
        return

    print("世界情勢のニュースを確認中…")
    m_score, m_hits = news.macro_news()
    news_info = {"date": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"), "macro_score": m_score, "macro": m_hits, "stocks": {}}

    def news_check(code):
        s, hits, n = news.stock_news(names[code])
        news_info["stocks"][names[code]] = {"score": s, "hits": hits}
        if s <= STOCK_STOP:
            return False, f"悪いニュースが多い（点数{s:+d}）: " + " / ".join(hits[:2])
        return True, f"ニュース点数{s:+d}（{n}件確認）"

    for d in todo:
        is_latest = d == latest
        ix_close = ix["Close"].asof(d)
        sma = ix_sma.asof(d)
        regime = bool(pd.notna(sma) and ix_close > sma)
        allow = is_latest and m_score > MACRO_STOP
        if is_latest and not regime:
            eng._log(d.strftime("%Y-%m-%d"), "日経平均が75日平均より下 → 新しく買うのはお休み")
        if is_latest and m_score <= MACRO_STOP:
            eng._log(d.strftime("%Y-%m-%d"), f"世界情勢ニュースが悪い（点数{m_score:+d}）→ 新しく買うのはお休み")
        eng.step(d, ind, names, fund_ok, regime, allow_entries=allow, news_check=news_check if allow else None, quality=qual, fund=fund)

    state = {"engine": eng.s, "last_date": latest.strftime("%Y-%m-%d"), "news": news_info}
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.DataFrame(eng.s["trades"]).to_csv(DIR / "売買記録.csv", index=False, encoding="utf-8-sig")
    write_page(eng, cfg, ix, news_info, views_html(eng, cfg, ind, fund, fund_ok, names, ix, ix_sma))
    print(f"\n処理した日: {todo[0].date()} 〜 {latest.date()}")
    print(f"現在の資産: {eng.equity():,.0f}円（スタート {cfg['capital']:,}円）")
    for line in eng.s["log"][-8:]:
        print("  " + line)


def views_html(eng, cfg, ind, fund, fund_ok, names, ix, ix_sma):
    """持ち株と、会社選びの条件を満たす株を、4人の投資家の視点で評価した表"""
    regime = bool(ix["Close"].iloc[-1] > ix_sma.iloc[-1])
    codes = list(eng.s["positions"]) + [c for c in names if fund_ok.get(c) and c not in eng.s["positions"]]
    rows = []
    for c in codes[:15]:
        d = ind.get(c)
        if d is None or d[["sma25"]].iloc[-1].isna().any():
            continue
        v, _ = evaluate(fund.get(c), d.iloc[-1], cfg, regime)
        tag = "（保有中）" if c in eng.s["positions"] else ""
        rows.append(f"<tr><td><b>{report.html.escape(names[c])}</b>{tag}</td>" +
                    "".join(f"<td>{report.html.escape(v[k])}</td>" for k in ["テスタ", "井村", "五月", "清原", "結論"]) + "</tr>")
    if not rows:
        return "<h2>4人の投資家の視点</h2><p>今は、会社選びの条件（PER15倍以下・ROE10%以上・売上の伸び10%以上）を満たす株がありません。</p>"
    return ("<h2>4人の投資家の視点（持ち株と、会社選びの条件を満たす株）</h2>"
            "<p><small>公開インタビュー・著書の考え方を数字に置き換えた簡易版です。本人の判断ではありません。</small></p>"
            "<table><tr><th>銘柄</th><th>テスタ（地合い・勢い）</th><th>井村（成長）</th><th>五月（織り込み度）</th>"
            f"<th>清原（割安さ）</th><th>買う量</th></tr>{''.join(rows)}</table>")


def write_page(eng, cfg, ix, news_info, views=""):
    s, eq = report.stats(eng.s, cfg, ix["Close"])
    if len(eq) > 1:
        report.chart(eq, cfg, ix["Close"], DIR / "成績.png", "仮想売買の資産の動き")
    tables, tips = analyze(eng.s["trades"], cfg, s)
    mh = "".join(f"<li>{report.html.escape(h)}</li>" for h in news_info.get("macro", [])[:10]) or "<li>目立つニュースなし</li>"
    note = (f"<b>本物のお金は使っていません（仮想売買）。</b> 最終更新 {news_info.get('date', '-')}<br>"
            f"世界情勢ニュースの点数: <b>{news_info.get('macro_score', 0):+d}</b>（{MACRO_STOP}以下なら新しく買うのを止めます）"
            f"<details><summary>気になったニュース見出し</summary><ul>{mh}</ul></details>")
    page = report.page("仮想売買の成績", note, s, "成績.png", eng.s["trades"], eng.s["positions"], eng.s["orders"], eng.s["log"])
    extra = (views + "<h2>売買記録からの気づき・改善案</h2><ul>" + "".join(f"<li>{report.html.escape(t)}</li>" for t in tips)
             + "</ul>" + tables_html(tables))
    (DIR / "成績.html").write_text(page.replace("</body>", extra + "</body>"), encoding="utf-8")


if __name__ == "__main__":
    main()
