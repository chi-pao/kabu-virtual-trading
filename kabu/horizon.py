"""中期（数週間〜数か月）・長期（1年以上）のおすすめ度。100点満点と、その根拠を返す。

数字だけで機械的に採点した参考値で、売買の推奨ではない。
"""
from .data import net_cash_ratio


def _lin(x, lo, hi, pts):
    """x が lo のとき0点、hi のとき満点（間は比例）"""
    if x is None:
        return 0.0
    if hi > lo:
        return max(0.0, min(1.0, (x - lo) / (hi - lo))) * pts
    return max(0.0, min(1.0, (lo - x) / (lo - hi))) * pts


def _p(x):
    return "-" if x is None else f"{x * 100:+.0f}%"


def mid_term(f, d):
    """中期：業績が伸びていて、株価も上向き、割高すぎない"""
    f = f or {}
    r = d.iloc[-1]
    eg, rg, roe = f.get("earningsGrowth"), f.get("revenueGrowth"), f.get("returnOnEquity")
    per = f.get("trailingPE") or f.get("forwardPE")
    c = float(r["Close"])
    m120 = c / float(d["Close"].iloc[-121]) - 1 if len(d) > 121 else None
    ext = c / float(r["sma25"]) - 1 if r["sma25"] == r["sma25"] else 0
    items = [
        (_lin(eg, 0, 0.3, 15) + _lin(rg, 0, 0.15, 10), 25, f"業績の伸び：利益{_p(eg)}・売上{_p(rg)}"),
        ((12 if c > r["sma75"] else 0) + (8 if r["sma25"] > r["sma75"] else 0) + _lin(m120, 0, 0.3, 5), 25,
         f"株価の流れ：{'75日平均より上' if c > r['sma75'] else '75日平均より下'}・半年で{_p(m120)}"),
        (_lin(roe, 0.05, 0.2, 15), 15, f"稼ぐ力：ROE {_p(roe).lstrip('+')}"),
        (_lin(per, 30, 10, 15), 15, f"割安さ：PER {per:.1f}倍" if per else "割安さ：PER 不明"),
        (10 if ext < 0.1 else 5 if ext < 0.2 else 0, 10, f"上がりすぎていない：25日平均から{_p(ext)}"),
        (_lin(f.get("dividendYield"), 0, 4, 10), 10, f"配当：利回り{f.get('dividendYield') or 0:.1f}%"),
    ]
    return _pack(items)


FINANCIALS = {"8306", "8316", "8411", "8766", "8725"}  # 銀行・保険（預金などがあり、現金−借金で比べられない）


def long_term(f, d, code=""):
    """長期：稼ぐ力が高く、財務が強く、割安で、配当があり、伸びている（清原・井村流を数字に）"""
    f = f or {}
    fin = code in FINANCIALS
    roe, rg = f.get("returnOnEquity"), f.get("revenueGrowth")
    per = f.get("trailingPE") or f.get("forwardPE")
    pbr = f.get("priceToBook")
    nc = net_cash_ratio(f)
    dy = f.get("dividendYield")
    c = float(d["Close"].iloc[-1])
    y1 = c / float(d["Close"].iloc[0]) - 1
    items = [
        (_lin(roe, 0.05, 0.2, 25), 25, f"稼ぐ力：ROE {_p(roe).lstrip('+')}（高いほど効率よく稼ぐ）"),
        (10, 20, "財務の強さ：銀行・保険は預金などがあり比べられないため中間点") if fin else
        (_lin(nc, -0.3, 0.3, 20), 20, f"財務の強さ：ネットキャッシュ比率 {nc * 100:+.0f}%（現金−借金÷時価総額）"),
        (_lin(per, 25, 8, 12) + _lin(pbr, 4, 0.8, 8), 20, f"割安さ：PER {per:.1f}倍・PBR {pbr:.1f}倍" if per and pbr else "割安さ：一部不明"),
        (_lin(rg, 0, 0.15, 15), 15, f"成長：売上{_p(rg)}"),
        (_lin(dy, 0, 4, 10), 10, f"配当：利回り{dy or 0:.1f}%"),
        (_lin(y1, -0.2, 0.3, 10), 10, f"1年の株価：{_p(y1)}"),
    ]
    return _pack(items)


def _pack(items):
    score = round(sum(min(p, mx) for p, mx, _ in items))
    reasons = [{"ok": p >= mx * 0.6, "mid": mx * 0.3 <= p < mx * 0.6, "text": t, "pts": round(min(p, mx)), "max": mx}
               for p, mx, t in items]
    return score, reasons
