"""いろいろなやり方を、それぞれ仮想の100万円で同時に試して、どれが一番もうかるかを比べる（試す期間用）。
どれも「8社に分けて持ち、毎月はじめに選び直す」。相場の向きでは止めない。1社が買値から15%下がったら次の朝に売る。
売買はすべて「終わった日の数字で決めて、次の営業日の寄り付きで行う」（先の値段は使わない）。
状態は 実験/state.json。クラウドで毎日（大引けのあと）動く。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from kabu import data

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "実験"
CAPITAL = 1_000_000
N = 8                 # 何社に分けて持つか
KEEP = 2              # 上位（N×KEEP）位以内に残っていれば、入れ替えずに持ち続ける
STOP = 0.15           # 買値から15%下がったら売る
SLIP = 0.001          # 売り買いのたびに0.1%不利な値段になる想定

STRATS = {
    "value": {"name": "割安株（PER＋PBRが低い）", "evidence": "過去2年：上位1割が平均より月+1.1%（8社では1年目+1.4%/月・2年目±0）"},
    "per": {"name": "割安株（PERが低い）", "evidence": "過去2年：上位1割が平均より月+0.5%"},
    "div": {"name": "高配当（配当利回りが高い）", "evidence": "まだ確かめていない（個人に人気のやり方として比べる）"},
    "vdc": {"name": "割安＋配当＋現金が多い", "evidence": "過去2年：上位1割が平均より月+0.9%（8社では平均並み）"},
    "mom": {"name": "株価の勢い（半年の上昇率が高い）", "evidence": "過去2年：1年目は大きく勝ち・2年目は負け（ぶれが大きい）"},
}


def rk(s):
    return s.rank(pct=True)


def ranking(kind, fund, uni, px_hist=None):
    """選ぶ順番（よい順の [コード, …]）。会社の数字は 結果/企業データ.json（毎日少しずつ取り直している）"""
    rows = []
    for code in uni:
        f = fund.get(code) or {}
        price = f.get("currentPrice")
        if not price or price < 100 or price * 100 > CAPITAL / N * 1.25:   # 100株が1社分の予算に入る株だけ（100円未満は除く）
            continue
        per, pbr = data.per_of(f), f.get("priceToBook")
        rows.append({"code": code, "per": per if per and 4 <= per <= 100 else np.nan,
                     "pbr": pbr if pbr and 0.1 <= pbr <= 20 else np.nan,
                     "dy": f.get("dividendYield") or 0.0, "nc": data.net_cash_ratio(f)})
    df = pd.DataFrame(rows).set_index("code")
    if kind == "value":
        s = rk(1 / df.per) + rk(1 / df.pbr)
    elif kind == "per":
        s = rk(1 / df.per)
    elif kind == "div":
        s = df.dy.where((df.per <= 25) & (df.dy <= 8))     # 利益が出ていない会社・異常な利回りは除く
    elif kind == "vdc":
        s = rk(1 / df.per) + rk(1 / df.pbr) + rk(df.dy) + rk(df.nc)
    elif kind == "mom":
        if not px_hist:
            return []
        m = {c: float(d0["Close"].iloc[-1] / d0["Close"].iloc[-121] - 1) for c, d0 in px_hist.items()
             if c in df.index and len(d0) > 121}
        s = pd.Series(m)
    return list(s.dropna().sort_values(ascending=False).index)


def new_wallet(kind):
    return {"kind": kind, "cash": float(CAPITAL), "positions": {}, "trades": [], "equity": [], "orders": [],
            "rebalanced": "", "log": []}


def load_state():
    p = OUT / "state.json"
    st = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"start": None, "last": None, "wallets": {}}
    for k in STRATS:
        st["wallets"].setdefault(k, new_wallet(k))
    return st


def step_day(w, day, px, names):
    """1日分：朝に注文を出し（売り→買い）、大引けで損切りの判定と資産の記録"""
    ds = str(day.date())
    sells = [o for o in w["orders"] if o["side"] == "sell"]
    buys = [o for o in w["orders"] if o["side"] == "buy"]
    w["orders"] = []
    for o in sells:
        p = w["positions"].get(o["code"])
        d0 = px.get(o["code"])
        if not p or d0 is None or day not in d0.index:
            if p:
                w["orders"].append(o)          # 値段が取れない日は次の日にもう一度
            continue
        sell = float(d0.loc[day, "Open"]) * (1 - SLIP)
        pnl = (sell - p["buy"]) * p["shares"]
        w["cash"] += sell * p["shares"]
        w["trades"].append({"date": ds, "code": o["code"], "name": p["name"], "shares": p["shares"], "buy": round(p["buy"], 1),
                            "sell": round(sell, 1), "pnl": round(pnl), "days": p.get("days", 0), "why": o["why"]})
        w["log"].append(f"{ds} 売り {p['name']} {p['shares']}株 {sell:,.0f}円 損益{pnl:+,.0f}円（{o['why']}）")
        del w["positions"][o["code"]]
    if buys:
        eq = w["cash"] + sum(p["last"] * p["shares"] for p in w["positions"].values())
        slot = eq / N
        need = N - len(w["positions"])
        for o in buys:
            if need <= 0:
                break
            code = o["code"]
            d0 = px.get(code)
            if code in w["positions"] or d0 is None or day not in d0.index:
                continue
            buy = float(d0.loc[day, "Open"]) * (1 + SLIP)
            # 100株単位なので、1社分の予算にいちばん近い株数にする（お金が余りすぎないように。手元のお金は超えない）
            sh = min(int(round(slot / buy / 100)) * 100, int(w["cash"] / buy // 100 * 100))
            if sh < 100:
                continue
            w["cash"] -= buy * sh
            w["positions"][code] = {"name": names.get(code, code), "shares": sh, "buy": buy, "date": ds, "last": buy, "days": 0}
            w["log"].append(f"{ds} 買い {names.get(code, code)} {sh}株 {buy:,.0f}円（{o['why']}）")
            need -= 1
    for code, p in w["positions"].items():
        d0 = px.get(code)
        if d0 is not None and day in d0.index:
            p["last"] = float(d0.loc[day, "Close"])
            p["days"] = p.get("days", 0) + 1
        if p["last"] <= p["buy"] * (1 - STOP) and not any(o["code"] == code for o in w["orders"]):
            w["orders"].append({"side": "sell", "code": code, "why": f"買値から{STOP:.0%}下がった（損切り）"})
    w["equity"].append([ds, round(w["cash"] + sum(p["last"] * p["shares"] for p in w["positions"].values()))])


def plan_rebalance(w, order, ds):
    """毎月はじめ：上位から N 社を持つように、売りと買いの注文を作る（翌朝に行う）"""
    keep_zone = set(order[:N * KEEP])
    for code in w["positions"]:
        if code not in keep_zone and not any(o["code"] == code for o in w["orders"]):
            w["orders"].append({"side": "sell", "code": code, "why": "毎月の見直しで順位が下がった"})
    stay = [c for c in w["positions"] if c in keep_zone]
    cands = [c for c in order if c not in w["positions"]][:N * 3]   # 買えない株があったときの予備も入れておく
    for i, c in enumerate(cands):
        w["orders"].append({"side": "buy", "code": c, "why": f"毎月の見直し（{order.index(c) + 1}位）"})
    w["rebalanced"] = ds[:7]
    w["log"].append(f"{ds} 見直し：{len(stay)}社はそのまま、{N - len(stay)}社を入れ替え予定")


def main():
    OUT.mkdir(exist_ok=True)
    st = load_state()
    rk_path = ROOT / "結果" / "全銘柄ランキング.json"
    uni = dict(json.loads(rk_path.read_text(encoding="utf-8"))["tradable"])
    fund = json.loads((ROOT / "結果" / "企業データ.json").read_text(encoding="utf-8"))
    days = data.load_prices_fresh([data.INDEX], period="1mo")[data.INDEX].index
    last = st["last"]
    todo = [d for d in days if last is None or str(d.date()) > last]
    if not st["start"]:
        todo = []                      # 初回は選ぶだけ（翌営業日の朝から売買）
        st["start"] = str(days[-1].date())
    held = {c for w in st["wallets"].values() for c in list(w["positions"]) + [o["code"] for o in w["orders"]]}
    px = data.load_prices_fresh(sorted(held), period="1mo") if held and todo else {}
    for d in todo:
        for w in st["wallets"].values():
            step_day(w, d, px, uni)
    if len(days):
        st["last"] = str(days[-1].date())
    # 月が変わったら（初回も）選び直す。勢いの計算だけは、全部の株の半年分の値段が必要
    ds = st["last"]
    due = [k for k, w in st["wallets"].items() if w["rebalanced"] != ds[:7] or not w["positions"] and not w["orders"]]
    hist = None
    if "mom" in due:
        cands = [c for c in uni if ((fund.get(c) or {}).get("currentPrice") or 1e9) * 100 <= CAPITAL / N * 1.25]
        hist = data.load_prices_chunked(sorted(cands), period="7mo")
    for k in due:
        order = ranking(k, fund, uni, hist)
        if order:
            w = st["wallets"][k]
            w["orders"] = [o for o in w["orders"] if o["side"] == "sell"]
            plan_rebalance(w, order, ds)
    for w in st["wallets"].values():
        w["log"] = w["log"][-200:]
    st["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    (OUT / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, w in st["wallets"].items():
        eq = w["equity"][-1][1] if w["equity"] else CAPITAL
        print(f"{STRATS[k]['name']}: 資産 {eq:,.0f}円 持ち株{len(w['positions'])}社 注文{len(w['orders'])}件")


if __name__ == "__main__":
    sys.exit(main())
