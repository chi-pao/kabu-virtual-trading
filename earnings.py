"""決算作戦（仮想のお金100万円・別の財布）

過去2年（J-Quantsのデータ）で、相場全体の動きに頼らず平均より上がりやすかった2つの合図で売買する。
  ① 会社が今期の利益予想を20%以上引き上げた → 翌朝、前日より高く始まったら買い、5日後の引けで売る
  ② 四半期の時点で、今期予想に対する利益の進み具合が、普通（1Q=25%・2Q=50%・3Q=75%）より20ポイント以上多い
     → 翌朝、前日より高く始まったら買い、20日後の引けで売る
発表は東証の適時開示（TDnet）から毎日読む。1社に使うのは資産の25%まで、同時に4社まで。

使い方: py -3.14 earnings.py        … 前回の続きから今日までを処理（取りこぼした日もまとめて処理する）
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from kabu import data, tdnet

ROOT = Path(__file__).parent
DIR = ROOT / "決算作戦"
ST = DIR / "state.json"
JST = timezone(timedelta(hours=9))
CFG = {"capital": 1000000, "slots": 4, "per_pos": 0.25, "slippage": 0.002, "lot": 100,
       "rev_min": 0.20, "rev_hold": 5, "prog_min": 0.20, "prog_hold": 20, "start": "2026-11-13"}


def load():
    if ST.exists():
        return json.loads(ST.read_text(encoding="utf-8"))
    return {"cfg": CFG, "cash": float(CFG["capital"]), "positions": {}, "pending": [], "trades": [],
            "equity": [], "log": [], "seen": {}, "last_day": None}


def save(s):
    DIR.mkdir(exist_ok=True)
    s["seen"] = {d: v for d, v in s["seen"].items() if d >= (datetime.now(JST) - timedelta(days=10)).strftime("%Y%m%d")}
    ST.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def log(s, day, msg):
    s["log"].append(f"{day} {msg}")
    print(day, msg)


def tradable():
    p = ROOT / "結果" / "全銘柄ランキング.json"
    return dict(json.loads(p.read_text(encoding="utf-8"))["tradable"]) if p.exists() else {}


def scan(s, uni):
    """まだ読んでいない開示を読み、合図が出た会社を「買う候補」に入れる（直近3日分は毎回見直す）"""
    c = s["cfg"]
    today = datetime.now(JST).date()
    start = max(datetime.strptime(c["start"], "%Y-%m-%d").date(), today - timedelta(days=3))
    if s["last_day"]:
        start = min(start, datetime.strptime(s["last_day"], "%Y-%m-%d").date() + timedelta(days=1))
        start = max(start, today - timedelta(days=9))
    d = start
    while d <= today:
        key = d.strftime("%Y%m%d")
        seen = set(s["seen"].get(key, []))
        rows = tdnet.day_list(key)
        n_new = 0
        for r in rows:
            t = r["title"]
            if not r["xbrl"] or r["xbrl"] in seen or r["code"] not in uni or "訂正" in t:
                continue
            if not ("決算短信" in t or ("業績予想" in t and "修正" in t) or ("予想" in t and "差異" in t)):
                continue
            seen.add(r["xbrl"]); n_new += 1
            x = tdnet.facts(r["xbrl"])
            time.sleep(0.3)
            sm = x and tdnet.summarize(x)
            if not sm:
                continue
            cand = None
            if sm["kind"] == "rev" and sm["prev"] and sm["prev"] > 0 and sm["new"] is not None:
                ch = sm["new"] / sm["prev"] - 1
                if ch >= c["rev_min"]:
                    cand = {"kind": "上方修正", "val": ch, "hold": c["rev_hold"],
                            "why": f"今期の利益予想を{ch * 100:+.0f}%引き上げ（{sm['prev'] / 1e8:,.1f}億→{sm['new'] / 1e8:,.1f}億円）"}
            elif sm["kind"] == "q" and sm["fc"] and sm["fc"] > 0 and sm["act"] is not None:
                ahead = sm["act"] / sm["fc"] - sm["q"] / 4
                if ahead >= c["prog_min"]:
                    cand = {"kind": "進み具合", "val": ahead, "hold": c["prog_hold"],
                            "why": f"第{sm['q']}四半期で今期予想の{sm['act'] / sm['fc'] * 100:.0f}%を達成（普通は{sm['q'] * 25}%）"}
            if cand and not any(p["code"] == r["code"] and p["kind"] == cand["kind"] for p in s["pending"]):
                cand.update({"code": r["code"], "name": uni.get(r["code"], r["name"]),
                             "disc": d.strftime("%Y-%m-%d"), "time": r["time"] or "15:00"})
                s["pending"].append(cand)
                log(s, cand["disc"], f"合図 {cand['name']}({r['code']}) {cand['why']} → 翌朝、上げて始まれば買う")
        s["seen"][key] = sorted(seen)
        print(f"{key}: 開示{len(rows)}件、新しく読んだ決算・修正 {n_new}件")
        d += timedelta(days=1)


def entry_day(days, disc, tm):
    """発表を知ってから買える最初の取引日（朝9時より前の発表ならその日、それ以外は翌取引日）"""
    t0 = pd.Timestamp(disc)
    ok = days[days >= t0] if tm < "09:00" else days[days > t0]
    return ok[0] if len(ok) else None


def replay(s):
    c = s["cfg"]
    codes = sorted({p["code"] for p in s["pending"]} | set(s["positions"]))
    px = data.load_prices(codes, period="3mo") if codes else {}
    ix = data.load_prices([data.INDEX], period="3mo")[data.INDEX]
    days = ix.index
    last = pd.Timestamp(s["last_day"]) if s["last_day"] else pd.Timestamp(c["start"]) - pd.Timedelta(days=1)
    now = datetime.now(JST)
    cutoff = pd.Timestamp(now.date()) if now.hour * 60 + now.minute >= 15 * 60 + 40 else pd.Timestamp(now.date()) - pd.Timedelta(days=1)
    for d in days[(days > last) & (days <= cutoff)]:   # 株価が確定した日だけ
        ds = d.strftime("%Y-%m-%d")
        # 寄り付き：今日が買う日の候補（前日より高く始まったものだけ。合図の強い順）
        today = [p for p in s["pending"] if entry_day(days, p["disc"], p["time"]) == d]
        stale = [p for p in s["pending"] if (e := entry_day(days, p["disc"], p["time"])) is not None and e < d]
        s["pending"] = [p for p in s["pending"] if p not in today and p not in stale]
        eq = s["cash"] + sum(p["shares"] * p["last"] for p in s["positions"].values())
        for p in sorted(today, key=lambda p: -p["val"]):
            d0 = px.get(p["code"])
            if d0 is None or d not in d0.index:
                log(s, ds, f"見送り {p['name']}: 株価が取れない"); continue
            i = d0.index.get_loc(d)
            op, prev = float(d0["Open"].iloc[i]), float(d0["Close"].iloc[i - 1]) if i else None
            if not prev or op <= prev:
                log(s, ds, f"見送り {p['name']}: 朝、上げて始まらなかった（{op:,.0f}円 / 前日{prev or 0:,.0f}円）"); continue
            if p["code"] in s["positions"] or len(s["positions"]) >= c["slots"]:
                log(s, ds, f"見送り {p['name']}: 枠がいっぱい"); continue
            buy = op * (1 + c["slippage"])
            sh = int(min(eq * c["per_pos"], s["cash"]) / buy // c["lot"] * c["lot"])
            if sh < c["lot"]:
                log(s, ds, f"見送り {p['name']}: 100株を買うお金が足りない（{op * 100:,.0f}円）"); continue
            s["cash"] -= sh * buy
            s["positions"][p["code"]] = {"name": p["name"], "shares": sh, "buy": buy, "entry_date": ds, "days": 0,
                                         "hold": p["hold"], "kind": p["kind"], "why": p["why"], "last": op}
            log(s, ds, f"買い {p['name']}({p['code']}) {sh}株 @{buy:,.0f}円  {p['hold']}日後に売る [{p['why']}]")
        # 引け：持つ日数が来たものを売る
        for code in list(s["positions"]):
            h = s["positions"][code]
            d0 = px.get(code)
            if d0 is None or d not in d0.index:
                continue
            h["last"] = float(d0.loc[d, "Close"]); h["days"] += 1
            if h["days"] >= h["hold"]:
                sell = h["last"] * (1 - c["slippage"])
                pnl = (sell - h["buy"]) * h["shares"]
                s["cash"] += sell * h["shares"]
                s["trades"].append({"code": code, "name": h["name"], "kind": h["kind"], "entry_date": h["entry_date"],
                                    "exit_date": ds, "shares": h["shares"], "entry": round(h["buy"], 1), "exit": round(sell, 1),
                                    "pnl": round(pnl), "ret": round(sell / h["buy"] - 1, 4), "why": h["why"]})
                log(s, ds, f"売り {h['name']}({code}) {h['shares']}株 @{sell:,.0f}円  損益 {pnl:+,.0f}円 [{h['days']}日持った]")
                del s["positions"][code]
        eqv = s["cash"] + sum(p["shares"] * p["last"] for p in s["positions"].values())
        s["equity"].append([ds, round(eqv)])
        s["last_day"] = ds


def main():
    s = load()
    uni = tradable()
    scan(s, uni)
    replay(s)   # 15:40より前は今日の株価が確定していないので、昨日までを処理
    save(s)
    eqv = s["equity"][-1][1] if s["equity"] else s["cash"]
    print(f"決算作戦: 資産 {eqv:,.0f}円（損益 {eqv - s['cfg']['capital']:+,.0f}円）、持ち株 {len(s['positions'])}、候補 {len(s['pending'])}")


if __name__ == "__main__":
    main()
