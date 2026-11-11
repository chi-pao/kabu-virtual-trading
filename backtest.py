"""過去のデータで、この作戦を試す（お金はかからない）

使い方:  py backtest.py
結果は 結果\過去の検証.html に出ます。
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from kabu import data, report
from kabu.engine import Engine

ROOT = Path(__file__).parent
OUT = ROOT / "結果"


def load_cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def load_market(cfg):
    """株価・指標・企業データをまとめて用意（何度も試すときは1回だけ呼ぶ）"""
    OUT.mkdir(exist_ok=True)
    codes = [c for c, _ in cfg["universe"]]
    print("株価データを取得中…")
    prices = data.load_prices(codes + [data.INDEX], period=f"{cfg['backtest_years'] + 1}y")
    ix = prices.pop(data.INDEX)
    print("企業の数字（ROE・PER・PBR・売上の伸び）を取得中…")
    fund = data.load_fundamentals(codes, OUT / "企業データ.json")
    return {"prices": prices, "ix": ix, "fund": fund, "ind": {}}


def simulate(cfg, mk):
    names = dict(cfg["universe"])
    key = cfg["breakout_days"]
    if key not in mk["ind"]:
        mk["ind"][key] = {c: data.add_indicators(d, key) for c, d in mk["prices"].items()}
    ind = mk["ind"][key]
    ix = mk["ix"]
    ix_sma = ix["Close"].rolling(75).mean()
    fund_ok = {c: data.fundamental_ok(mk["fund"].get(c), cfg["fundamental"])[0] for c in names}
    qual = {c: data.quality(mk["fund"].get(c), cfg.get("quality_parts", {})) for c in names}
    start = ix.index[-1] - pd.DateOffset(years=cfg["backtest_years"])
    eng = Engine(cfg)
    for d in ix.index[ix.index >= start]:
        if cfg.get("use_regime", True):
            regime = bool(ix.loc[d, "Close"] > ix_sma.loc[d]) if pd.notna(ix_sma.loc[d]) else False
        else:
            regime = True
        eng.step(d, ind, names, fund_ok, regime, quality=qual, fund=mk["fund"])
    s, eq = report.stats(eng.s, cfg, ix["Close"])
    s["対象になった会社数"] = sum(fund_ok.values())
    return eng, s, eq


NOTE = ("<b>過去のデータで試した結果です。将来も同じになるとは限りません。</b><br>"
        "手数料（売買ごとに0.1%）と、注文が少し不利な値段で約定する分（0.1%）を差し引いています。<br>"
        "注意：企業の数字（ROE・PERなど）は「今の値」で会社を選んでいるため、過去の検証は実際より少し良く出る可能性があります。"
        "ニュースの判定は過去にさかのぼれないため、この検証には入っていません（毎日の仮想売買では使います）。")


def main():
    cfg = load_cfg()
    mk = load_market(cfg)
    eng, s, eq = simulate(cfg, mk)
    report.chart(eq, cfg, mk["ix"]["Close"], OUT / "過去の検証.png",
                 f"過去{cfg['backtest_years']}年の検証（仮想資金 {cfg['capital'] / 10000:.0f}万円）")
    (OUT / "過去の検証.html").write_text(
        report.page("過去のデータでの検証", NOTE, s, "過去の検証.png", eng.s["trades"]), encoding="utf-8")
    pd.DataFrame(eng.s["trades"]).to_csv(OUT / "過去の検証_売買記録.csv", index=False, encoding="utf-8-sig")
    for k, v in s.items():
        print(f"  {k}: {report.fmt(k, v)}")
    print(f"\n結果: {OUT / '過去の検証.html'}")


if __name__ == "__main__":
    main()
