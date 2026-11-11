"""デイトレードの検証と、1週間の仮想デイトレ

使い方:
  py daytrade.py test   … 過去約60日の5分足で、作戦を比べる（手数料ありなし両方）
  py daytrade.py run    … 仮想デイトレを進める（取引時間が終わった日を、朝から順番に再生）
結果は デイトレ\成績.html に出ます。
"""
import copy
import html
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from kabu.daytrade import DayTrader

ROOT = Path(__file__).parent
DIR = ROOT / "デイトレ"
JST = timezone(timedelta(hours=9))

BASE = {
    "capital": 3000000,
    "strategy": "orb",          # orb=寄り付き30分の高値/安値ブレイク, vwap=VWAP押し目・戻り
    "allow_short": True,        # 空売り（実際には信用取引の口座が必要）
    "use_index": True,          # 日経平均が始値より上なら買いだけ、下なら売りだけ
    "or_end": "09:30", "last_entry": "14:00", "flat_at": "15:15",
    "risk_per_trade": 0.005,    # 1回の損失は資金の0.5%まで
    "max_position_pct": 0.3, "max_positions": 3,
    "daily_loss_limit": 0.015,  # 1日に資金の1.5%負けたら、その日は終わり
    "target_r": 2.0, "breakeven_r": 1.0,
    "stop_or_mult": 0.5, "min_stop_pct": 0.003,
    "min_range": 0.004, "max_range": 0.03, "max_gap": 0.03, "vol_mult": 1.5,
    "slippage": 0.0005,         # 売買のたびに0.05%不利な値段で約定する想定
    "fee_rate": 0.0,            # デイトレ手数料0円のプランを想定（下の比べっこで0.1%も試す）
    "min_reward_cost": 4,
}


def universe():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    return dict(cfg["universe"])


def load(period):
    names = universe()
    tickers = [f"{c}.T" for c in names] + ["^N225"]
    df = yf.download(tickers, period=period, interval="5m", auto_adjust=False, progress=False, group_by="ticker", threads=True)
    daily = yf.download([f"{c}.T" for c in names], period="6mo", interval="1d", auto_adjust=False, progress=False, group_by="ticker")
    bars, prevc = {}, {}
    for c in names:
        try:
            d = df[f"{c}.T"][["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "Close"])
        except KeyError:
            continue
        d.index = d.index.tz_convert("Asia/Tokyo")
        bars[c] = d
        try:
            dc = daily[f"{c}.T"]["Close"].dropna()
            dc.index = pd.to_datetime(dc.index).tz_localize(None).normalize()
            prevc[c] = dc
        except KeyError:
            pass
    ix = df["^N225"][["Open", "High", "Low", "Close"]].dropna()
    ix.index = ix.index.tz_convert("Asia/Tokyo")
    return names, bars, ix, prevc


def days_of(bars):
    return sorted({t.date() for d in bars.values() for t in d.index})


def run_days(tr, days, names, bars, ix, prevc):
    for day in days:
        db = {c: d[d.index.date == day] for c, d in bars.items()}
        db = {c: d for c, d in db.items() if len(d)}
        ib = ix[ix.index.date == day]
        pc = {}
        for c, s in prevc.items():
            before = s[s.index < pd.Timestamp(day)]
            if len(before):
                pc[c] = float(before.iloc[-1])
        tr.run_day(day, db, ib, names, pc)


def summary(s, cap):
    t = s["trades"]
    days = s["days"]
    wins = [x["pnl"] for x in t if x["pnl"] > 0]
    loss = [x["pnl"] for x in t if x["pnl"] <= 0]
    eq = pd.Series([d["equity"] for d in days]) if days else pd.Series([cap])
    dd = float((eq / eq.cummax().clip(lower=cap) - 1).min()) if len(eq) else 0
    return {
        "日数": len(days), "損益": s["cash"] - cap, "利回り": s["cash"] / cap - 1,
        "取引回数": len(t), "勝率": len(wins) / len(t) if t else None,
        "平均の勝ち": sum(wins) / len(wins) if wins else 0, "平均の負け": sum(loss) / len(loss) if loss else 0,
        "プラスの日": sum(1 for d in days if d["pnl"] > 0), "マイナスの日": sum(1 for d in days if d["pnl"] < 0),
        "1日平均": (s["cash"] - cap) / len(days) if days else 0, "最大の落ち込み": dd, "手数料": s["fees"],
    }


VARIANTS = [
    ("寄り付き30分ブレイク（買い・売り両方）", {}),
    ("寄り付き30分ブレイク（買いだけ）", {"allow_short": False}),
    ("VWAP押し目・戻り（買い・売り両方）", {"strategy": "vwap"}),
    ("寄り付き30分ブレイク・日経平均の向きを見ない", {"use_index": False}),
    ("寄り付き30分ブレイク・利確を3倍に", {"target_r": 3.0}),
    ("【参考】手数料が売買ごとに0.1%かかる場合", {"fee_rate": 0.001}),
    ("【参考】約定のずれが0.1%の場合", {"slippage": 0.001}),
]


def test():
    DIR.mkdir(exist_ok=True)
    print("過去約60日の5分足を取得中…")
    names, bars, ix, prevc = load("60d")
    days = days_of(bars)
    print(f"  {days[0]} 〜 {days[-1]}（{len(days)}日）")
    rows = []
    for title, diff in VARIANTS:
        cfg = {**copy.deepcopy(BASE), **diff}
        tr = DayTrader(cfg)
        run_days(tr, days, names, bars, ix, prevc)
        s = summary(tr.s, cfg["capital"])
        # 1週間（5営業日）ごとの成績
        w = [sum(d["pnl"] for d in tr.s["days"][i:i + 5]) for i in range(0, len(tr.s["days"]), 5)]
        s["プラスの週"] = f"{sum(1 for x in w if x > 0)}/{len(w)}"
        s["1週間の平均"] = sum(w) / len(w) if w else 0
        rows.append((title, s))
        print(f"  {title}: {s['損益']:+,.0f}円 / 取引{s['取引回数']}回 / 勝率{(s['勝率'] or 0):.0%} / 1週間平均{s['1週間の平均']:+,.0f}円 / プラスの週{s['プラスの週']}")
    esc = html.escape
    tb = "".join(f"<tr><td>{esc(t)}</td><td class='{'p' if s['損益'] > 0 else 'm'}'>{s['損益']:+,.0f}円</td><td>{s['1週間の平均']:+,.0f}円</td>"
                 f"<td>{s['プラスの週']}</td><td>{s['プラスの日']}/{s['日数']}</td><td>{s['取引回数']}回</td><td>{(s['勝率'] or 0):.0%}</td>"
                 f"<td>{s['平均の勝ち']:+,.0f} / {s['平均の負け']:+,.0f}</td><td>{s['最大の落ち込み']:.1%}</td></tr>" for t, s in rows)
    (DIR / "作戦の比べっこ.html").write_text(f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>デイトレ作戦の比べっこ</title>
<style>body{{font-family:'Yu Gothic','Meiryo',sans-serif;max-width:1100px;margin:20px auto;padding:0 16px}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ddd;padding:6px 8px;font-size:14px}}th{{background:#f4f4f4}}.p{{color:#1a7f37;font-weight:bold}}.m{{color:#c00;font-weight:bold}}
.note{{background:#fff8e1;border:1px solid #f0d58c;padding:10px;border-radius:6px}}</style></head><body>
<h1>デイトレ作戦の比べっこ（過去{len(days)}日・仮想資金{BASE['capital'] // 10000}万円）</h1>
<div class="note">5分足を朝から1本ずつ再生して、その時点までの情報だけで判断。約定のずれ（0.05%）を差し引き。<br>
過去にうまくいった作戦が、この先もうまくいくとは限りません。60日はデイトレの検証としては短い期間です。</div>
<table><tr><th>作戦</th><th>60日の損益</th><th>1週間の平均</th><th>プラスの週</th><th>プラスの日</th><th>取引回数</th><th>勝率</th><th>平均の勝ち/負け</th><th>最大の落ち込み</th></tr>{tb}</table>
</body></html>""", encoding="utf-8")
    print(f"\n結果: {DIR / '作戦の比べっこ.html'}")


def run():
    """仮想デイトレを進める。取引が終わった日（15:30以降の今日、またはそれより前）だけ処理する"""
    DIR.mkdir(exist_ok=True)
    sp = DIR / "state.json"
    st = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else None
    cfg = {**BASE, **(st["cfg"] if st else json.loads((DIR / "使う作戦.json").read_text(encoding="utf-8")))}
    names, bars, ix, prevc = load("5d")
    now = datetime.now(JST)
    done = [d for d in days_of(bars) if d < now.date() or (d == now.date() and now.hour * 60 + now.minute >= 15 * 60 + 40)]
    start = pd.Timestamp(st["start"] if st else cfg.get("start", str(now.date()))).date()
    last = pd.Timestamp(st["last"]).date() if st and st.get("last") else None
    todo = [d for d in done if d >= start and (last is None or d > last)]
    tr = DayTrader(cfg, st["engine"] if st else None)
    if todo:
        run_days(tr, todo, names, bars, ix, prevc)
    st = {"cfg": {k: cfg[k] for k in cfg if k in BASE or k == "start"}, "engine": tr.s, "start": str(start),
          "last": str(todo[-1]) if todo else (st or {}).get("last")}
    sp.write_text(json.dumps(st, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    s = summary(tr.s, cfg["capital"])
    esc = html.escape
    days_html = "".join(f"<tr><td>{d['date']}</td><td class='{'p' if d['pnl'] > 0 else 'm' if d['pnl'] < 0 else ''}'>{d['pnl']:+,}円</td>"
                        f"<td>{d['trades']}回</td><td>{d['wins']}勝</td><td>{d['equity']:,}円</td></tr>" for d in tr.s["days"])
    tr_html = "".join(f"<tr><td>{t['date']}</td><td>{esc(t['name'])}</td><td>{t['side']}</td><td>{t['in_time']}→{t['out_time']}</td>"
                      f"<td>{t['shares']}</td><td>{t['entry']:,}</td><td>{t['exit']:,}</td><td class='{'p' if t['pnl'] > 0 else 'm'}'>{t['pnl']:+,}</td>"
                      f"<td>{esc(t['reason'])}</td></tr>" for t in reversed(tr.s["trades"]))
    (DIR / "成績.html").write_text(f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>仮想デイトレの成績</title><style>body{{font-family:'Yu Gothic','Meiryo',sans-serif;max-width:1000px;margin:16px auto;padding:0 16px}}
table{{border-collapse:collapse;width:100%;margin-bottom:18px}}th,td{{border:1px solid #ddd;padding:5px 7px;font-size:13px}}th{{background:#f4f4f4}}
.p{{color:#1a7f37;font-weight:bold}}.m{{color:#c00;font-weight:bold}}.note{{background:#fff8e1;border:1px solid #f0d58c;padding:10px;border-radius:6px}}</style></head><body>
<h1>仮想デイトレの成績（{st['start']}〜）</h1>
<div class="note">本物のお金は使っていません。作戦：寄り付き60分の高値を上抜けたら買い、その日のうちに必ず売る。1回の損失は資金の0.5%まで、1日1.5%負けたら終了。<br>
取引時間が終わったあと、その日の5分足を朝から順に再生して売買しています（その時点までの値段だけで判断）。</div>
<h2>合計：<span class="{'p' if s['損益'] > 0 else 'm'}">{s['損益']:+,.0f}円</span>（{s['日数']}日・{s['取引回数']}回・勝率{(s['勝率'] or 0):.0%}）</h2>
<h2>日ごと</h2><table><tr><th>日付</th><th>損益</th><th>取引</th><th>勝ち</th><th>資産</th></tr>{days_html}</table>
<h2>売買の記録</h2><table><tr><th>日付</th><th>銘柄</th><th>売買</th><th>時間</th><th>株数</th><th>買値</th><th>売値</th><th>損益</th><th>理由</th></tr>{tr_html}</table>
</body></html>""", encoding="utf-8")
    print(f"処理した日: {', '.join(map(str, todo)) or 'なし（まだ取引時間が終わっていない日は処理しません）'}")
    print(f"ここまで{s['日数']}日: 損益 {s['損益']:+,.0f}円（取引{s['取引回数']}回）")


def live():
    """今日の仮想デイトレの途中経過（取引時間中に画面で見る用。記録はしない）。
    株価は約20分遅れなので「20分前までの値動きで、ルールどおりならこうなっている」という状況"""
    sp = DIR / "state.json"
    st = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else None
    cfg = {**BASE, **(st["cfg"] if st else json.loads((DIR / "使う作戦.json").read_text(encoding="utf-8")))}
    names, bars, ix, prevc = load("5d")
    days = days_of(bars)
    if not days:
        return {"ok": False, "msg": "株価データを取得できませんでした"}
    day = days[-1]
    now = datetime.now(JST)
    total_before = (st["engine"]["cash"] if st else cfg["capital"]) - cfg["capital"]
    base = {"ok": True, "date": str(day), "capital": cfg["capital"], "or_end": cfg["or_end"],
            "last_entry": cfg["last_entry"], "flat_at": cfg["flat_at"], "updated": now.strftime("%H:%M")}
    # すでに記録済みの日（取引終了後）は、記録をそのまま見せる
    if st and st.get("last") and pd.Timestamp(st["last"]).date() >= day:
        trades = [t for t in st["engine"]["trades"] if t["date"] == str(day)]
        realized = sum(t["pnl"] for t in trades)
        return {**base, "finished": True, "phase": "この日の取引は終了（記録済み）", "trades": trades[::-1], "open": [],
                "watch": [], "realized": realized, "unrealized": 0, "total": total_before,
                "as_of": cfg["flat_at"], "stop_new": False}
    db = {c: d[d.index.date == day] for c, d in bars.items()}
    db = {c: d for c, d in db.items() if len(d)}
    ib = ix[ix.index.date == day]
    pc = {}
    for c, s in prevc.items():
        before = s[s.index < pd.Timestamp(day)]
        if len(before):
            pc[c] = float(before.iloc[-1])
    tr = DayTrader(cfg, {"cash": float(st["engine"]["cash"]) if st else float(cfg["capital"]),
                         "trades": [], "days": [], "log": [], "fees": 0.0})
    tr.run_day(day, db, ib, names, pc, live=True)
    L = tr.live
    last = L["last_t"] or max((d.index[-1] for d in db.values()), default=None)
    as_of = last.strftime("%H:%M") if last is not None else "-"
    hm = last.hour * 60 + last.minute if last is not None else 0
    toi = lambda s: int(s[:2]) * 60 + int(s[3:])
    opens, unreal = [], 0.0
    for code, p in L["pos"].items():
        d = L["data"][code]
        px = float(d["Close"].iloc[-1])
        u = (px - p["entry"]) * p["shares"] * (1 if p["side"] == "買い" else -1)
        unreal += u
        opens.append({"code": code, "name": names.get(code, code), "side": p["side"], "shares": p["shares"],
                      "time": p["time"], "entry": round(p["entry"], 1), "stop": round(p["stop"], 1),
                      "target": round(p["target"], 1), "price": round(px, 1), "pnl": round(u), "be": p["be"],
                      "setup": p["setup"]})
    # まだ買っていない銘柄のうち、朝の高値まであと少しのもの
    watch = []
    if toi(cfg["or_end"]) <= hm < toi(cfg["last_entry"]) and not L["stop_new"]:
        for code, inf in L["info"].items():
            if inf["done"] or code in L["pos"]:
                continue
            px = float(L["data"][code]["Close"].iloc[-1])
            ok_rng = cfg["min_range"] <= inf["rng"] <= cfg["max_range"] and abs(inf["gap"]) <= cfg["max_gap"]
            if ok_rng and px < inf["hi"]:
                watch.append({"code": code, "name": names.get(code, code), "price": round(px, 1),
                              "line": round(inf["hi"], 1), "dist": inf["hi"] / px - 1})
        watch.sort(key=lambda w: w["dist"])
        watch = watch[:6]
    if last is None:
        phase = "まだ今日の株価が届いていません（9時すぎから。約20分遅れ）"
    elif hm < toi(cfg["or_end"]):
        phase = f"{cfg['or_end']}までは朝の値動きを観察中（この間の高値を上抜けたら買う）"
    elif L["stop_new"]:
        phase = "今日は損失上限に達したので、新しく買うのは終了"
    elif hm < toi(cfg["last_entry"]):
        phase = f"買うチャンスを探し中（{cfg['last_entry']}まで）"
    elif hm < toi(cfg["flat_at"]):
        phase = f"新しく買うのは終了。持ち株は{cfg['flat_at']}までに全部売る"
    else:
        phase = "今日の取引は終了（このあと記録されます）"
    return {**base, "finished": False, "phase": phase, "as_of": as_of, "trades": L["day_trades"][::-1], "open": opens,
            "pending": [{"name": names.get(o["code"]), "side": o["side"], "shares": o["shares"]} for o in L["pending"]],
            "watch": watch, "realized": round(L["realized"]), "unrealized": round(unreal),
            "total": total_before, "stop_new": L["stop_new"]}


if __name__ == "__main__":
    {"test": test, "run": run}.get(sys.argv[1] if len(sys.argv) > 1 else "run", run)()
