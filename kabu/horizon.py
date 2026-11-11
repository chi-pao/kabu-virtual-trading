"""中期（数週間〜数か月）・長期（1年以上）のおすすめ度。100点満点と、その根拠を返す。

数字だけで機械的に採点した参考値で、売買の推奨ではない。
来期の予想利益（アナリスト予想）とアナリストの目標株価も使う（四季報の業績予想の代わり）。
"""
from .data import _ok_pe, analyst_upside, net_cash_ratio, next_year_change, per_of

FINANCIALS = {"8306", "8316", "8411", "8766", "8725"}  # 銀行・保険（預金などがあり、現金−借金で比べられない）


def _lin(x, lo, hi, pts):
    """x が lo のとき0点、hi のとき満点（間は比例）"""
    if x is None:
        return 0.0
    if hi > lo:
        return max(0.0, min(1.0, (x - lo) / (hi - lo))) * pts
    return max(0.0, min(1.0, (lo - x) / (lo - hi))) * pts


def _p(x):
    return "-" if x is None else f"{x * 100:+.0f}%"


def _common(f, price):
    nx = next_year_change(f)
    up = analyst_upside(f, price)
    n = f.get("numberOfAnalystOpinions") or 0
    t, fw = f.get("trailingPE"), f.get("forwardPE")
    per_txt = f"PER 今期{t:.1f}倍" if t and t > 0 else "PER 今期 赤字"
    if fw:
        per_txt += f"・来期予想{fw:.1f}倍" if _ok_pe(fw) else "・来期予想はデータ不正確のため不使用"
    nx_item = (_lin(nx, -0.3, 0.2, 15) if nx is not None else 7, 15,
               f"来期の利益見込み：{_p(nx)}（アナリスト予想）" if nx is not None else "来期の利益見込み：予想なし（中間点）")
    up_item = (_lin(up, -0.1, 0.2, 5) if up is not None else 2, 5,
               f"アナリストの目標株価：平均{f.get('targetMeanPrice'):,.0f}円（今より{_p(up)}・{n}人）" if up is not None
               else "アナリストの目標株価：予想が少ない、またはデータ不正確のため不使用")
    return per_txt, nx_item, up_item


def mid_term(f, d):
    """中期：業績が伸びていて、来期も減らず、株価も上向き、割高すぎない"""
    f = f or {}
    r = d.iloc[-1]
    c = float(r["Close"])
    eg, rg, roe = f.get("earningsGrowth"), f.get("revenueGrowth"), f.get("returnOnEquity")
    per = per_of(f)
    per_txt, nx_item, up_item = _common(f, c)
    m120 = c / float(d["Close"].iloc[-121]) - 1 if len(d) > 121 else None
    ext = c / float(r["sma25"]) - 1 if r["sma25"] == r["sma25"] else 0
    items = [
        (_lin(eg, 0, 0.3, 12) + _lin(rg, 0, 0.15, 8), 20, f"今期の業績の伸び：利益{_p(eg)}・売上{_p(rg)}"),
        nx_item,
        ((10 if c > r["sma75"] else 0) + (6 if r["sma25"] > r["sma75"] else 0) + _lin(m120, 0, 0.3, 4), 20,
         f"株価の流れ：{'75日平均より上' if c > r['sma75'] else '75日平均より下'}・半年で{_p(m120)}"),
        (_lin(roe, 0.05, 0.2, 10), 10, f"稼ぐ力：ROE {_p(roe).lstrip('+')}"),
        (_lin(per, 30, 10, 15), 15, f"割安さ：{per_txt}（高い方で判定）"),
        (10 if ext < 0.1 else 5 if ext < 0.2 else 0, 10, f"上がりすぎていない：25日平均から{_p(ext)}"),
        up_item,
        (_lin(f.get("dividendYield"), 0, 4, 5), 5, f"配当：利回り{f.get('dividendYield') or 0:.1f}%"),
    ]
    return _pack(items)


def long_term(f, d, code=""):
    """長期：稼ぐ力が高く、財務が強く、割安で、来期も伸び、配当がある（清原・井村流を数字に）"""
    f = f or {}
    fin = code in FINANCIALS
    c = float(d["Close"].iloc[-1])
    roe, rg = f.get("returnOnEquity"), f.get("revenueGrowth")
    per, pbr = per_of(f), f.get("priceToBook")
    per_txt, nx_item, up_item = _common(f, c)
    nc = net_cash_ratio(f)
    dy = f.get("dividendYield")
    y1 = c / float(d["Close"].iloc[0]) - 1
    items = [
        (_lin(roe, 0.05, 0.2, 20), 20, f"稼ぐ力：ROE {_p(roe).lstrip('+')}（高いほど効率よく稼ぐ）"),
        (7.5, 15, "財務の強さ：銀行・保険は預金などがあり比べられないため中間点") if fin else
        (_lin(nc, -0.3, 0.3, 15), 15, f"財務の強さ：ネットキャッシュ比率 {nc * 100:+.0f}%（現金−借金÷時価総額）"),
        (_lin(per, 25, 8, 10) + _lin(pbr, 4, 0.8, 5), 15, f"割安さ：{per_txt}・PBR {pbr:.1f}倍" if pbr else f"割安さ：{per_txt}"),
        (_lin(rg, 0, 0.15, 15), 15, f"成長：売上{_p(rg)}"),
        nx_item,
        (_lin(dy, 0, 4, 10), 10, f"配当：利回り{dy or 0:.1f}%"),
        (_lin(y1, -0.2, 0.3, 5), 5, f"1年の株価：{_p(y1)}"),
        up_item,
    ]
    return _pack(items)


def _pack(items):
    score = round(sum(min(p, mx) for p, mx, _ in items))
    reasons = [{"ok": p >= mx * 0.6, "mid": mx * 0.3 <= p < mx * 0.6, "text": t, "pts": round(min(p, mx)), "max": mx}
               for p, mx, t in items]
    return score, reasons
