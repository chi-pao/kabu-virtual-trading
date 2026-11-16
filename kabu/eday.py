"""決算デイトレ：良い決算（今期の利益予想を+10%以上引き上げ／1年の利益が会社予想を+10%以上上回った）の翌朝、
上げ幅が0〜5%で始まったら寄り付きで買い、大引けで必ず売る。1回30万円まで・1日3社まで・特に売買が活発な株だけ。
（過去2年の確認：+44%、前半+18%・後半+22%、いちばん減ったとき-10%。ただし将来も同じとは限らない）
候補は earnings.py が適時開示から作る（決算作戦/state.json の day_pending）。
"""
import json
from pathlib import Path

import pandas as pd

from . import data

ROOT = Path(__file__).resolve().parent.parent
RULE = {"gap_min": 0.0, "gap_max": 0.05, "per_trade": 300000, "max_trades": 3, "min_turnover": 1e8, "slippage": 0.001}


def cands():
    p = ROOT / "決算作戦" / "state.json"
    return json.loads(p.read_text(encoding="utf-8")).get("day_pending", []) if p.exists() else []


def entry_day(days, disc, tm):
    t0 = pd.Timestamp(disc)
    ok = days[days >= t0] if tm < "09:00" else days[days > t0]
    return ok[0] if len(ok) else None


def plan(day, cs, px, days):
    """その日の朝に買う銘柄を決める（朝の値段まででわかること）。[(候補, 始値, 前日終値, 上げ幅, 見送り理由)]"""
    out = []
    for c in sorted([c for c in cs if entry_day(days, c["disc"], c["time"]) == day], key=lambda c: -c["val"]):
        d0 = px.get(c["code"])
        if d0 is None or day not in d0.index:
            out.append((c, None, None, None, "株価が取れない")); continue
        i = d0.index.get_loc(day)
        if i < 1:
            out.append((c, None, None, None, "前日の株価がない")); continue
        op, pc = float(d0["Open"].iloc[i]), float(d0["Close"].iloc[i - 1])
        gap = op / pc - 1
        turn = float((d0["Close"] * d0["Volume"]).iloc[max(0, i - 20):i].mean())
        why = None
        if turn < RULE["min_turnover"]:
            why = f"売買が少ない（1日{turn / 1e8:.1f}億円）"
        elif not (RULE["gap_min"] < gap <= RULE["gap_max"]):
            why = f"朝の上げ幅が{gap * 100:+.1f}%（0〜5%の時だけ買う）"
        out.append((c, op, pc, gap, why))
    ok = [x for x in out if not x[4]][:RULE["max_trades"]]
    return ok, [x for x in out if x[4] or x not in ok]


def run_days(eng, todo):
    """終わった日を順番に処理（記録の形はこれまでのデイトレと同じ）"""
    cs = cands()
    codes = sorted({c["code"] for c in cs})
    px = data.load_prices_fresh(codes, period="3mo") if codes else {}
    days = data.load_prices_fresh([data.INDEX], period="3mo")[data.INDEX].index
    for day in todo:
        d = pd.Timestamp(day)
        ok, skip = plan(d, cs, px, days)
        for c, *_r, why in skip:
            if why:
                eng["log"].append(f"{day} 見送り {c['name']}: {why}")
        pnl_day, wins, n = 0.0, 0, 0
        for c, op, pc, gap, _ in ok:
            buy = op * (1 + RULE["slippage"])
            sh = int(min(RULE["per_trade"], eng["cash"]) / buy // 100 * 100)
            if sh < 100:
                eng["log"].append(f"{day} 見送り {c['name']}: 100株を買うお金が足りない"); continue
            close = float(px[c["code"]].loc[d, "Close"])
            sell = close * (1 - RULE["slippage"])
            pnl = (sell - buy) * sh
            pnl_day += pnl; wins += pnl > 0; n += 1
            eng["trades"].append({"date": str(day), "code": c["code"], "name": c["name"], "side": "買い",
                                  "in_time": "09:00", "out_time": "15:30", "shares": sh, "entry": round(buy, 1),
                                  "exit": round(sell, 1), "pnl": round(pnl),
                                  "reason": f"{c['why']}・朝{gap * 100:+.1f}%で始まる → 大引けで売り"})
            eng["log"].append(f"{day} 決算デイトレ {c['name']} {sh}株 {buy:,.0f}→{sell:,.0f}円 損益{pnl:+,.0f}円")
        eng["cash"] += pnl_day
        eng["days"].append({"date": str(day), "pnl": round(pnl_day), "trades": n, "wins": wins, "equity": round(eng["cash"])})


def live(eng, capital):
    """今日の途中経過（取引時間中に画面で見る用。株価は約20分遅れ）"""
    cs = cands()
    codes = sorted({c["code"] for c in cs})
    ixd = data.load_prices_fresh([data.INDEX], period="1mo")[data.INDEX]
    days = ixd.index
    if not len(days):
        return None
    day = days[-1]
    px = data.load_prices_fresh(codes, period="1mo") if codes else {}
    ok, skip = plan(day, cs, px, days)
    opens, unreal = [], 0.0
    for c, op, pc, gap, _ in ok:
        buy = op * (1 + RULE["slippage"])
        sh = int(min(RULE["per_trade"], eng["cash"]) / buy // 100 * 100)
        if sh < 100:
            continue
        now = float(px[c["code"]].loc[day, "Close"])
        u = (now - buy) * sh
        unreal += u
        opens.append({"code": c["code"], "name": c["name"], "side": "買い", "shares": sh, "time": "09:00",
                      "entry": round(buy, 1), "stop": None, "target": None, "price": round(now, 1), "pnl": round(u),
                      "be": False, "setup": f"{c['why']}・朝{gap * 100:+.1f}%"})
    upcoming = [c for c in cs if (e := entry_day(days, c["disc"], c["time"])) is None or e > day]
    return {"date": str(day.date()), "open": opens, "unrealized": round(unreal),
            "skipped": [{"name": c["name"], "why": w} for c, *_r, w in skip if w],
            "next": [{"name": c["name"], "code": c["code"], "why": c["why"]} for c in sorted(upcoming, key=lambda c: -c["val"])[:8]]}
