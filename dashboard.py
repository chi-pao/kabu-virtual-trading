"""仮想売買を一目で見る画面（このパソコンの中だけで動く）

使い方:  py dashboard.py   → ブラウザで http://127.0.0.1:8765 が開きます
  ・株価は約20分遅れ（無料データのため）。1分ごとに自動で更新
  ・ニュースは10分ごとに更新
  ・売買そのものは paper.py（毎日の仮想売買）が行い、この画面は「見るだけ」
"""
import json
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from kabu import data, news
from kabu.views import evaluate

ROOT = Path(__file__).parent
PORT = 8765
JST = timezone(timedelta(hours=9))
NEWS_QUERIES = ["日経平均", "日銀 金利", "米国株 市場", "為替 円相場", "関税 貿易", "地政学 リスク", "決算 上方修正"]

_cache = {}
_lock = threading.Lock()


def cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def state():
    p = ROOT / "仮想売買" / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def daily():
    def load():
        c = cfg()
        codes = [x for x, _ in c["universe"]]
        prices = data.load_prices(codes + [data.INDEX], period="1y")
        ix = prices.pop(data.INDEX)
        ind = {k: data.add_indicators(v, c["breakout_days"]) for k, v in prices.items()}
        return ind, ix
    return cached("daily", 1800, load)


def intraday():
    """今日の5分足（約20分遅れ）"""
    def load():
        c = cfg()
        tickers = [f"{x}.T" for x, _ in c["universe"]] + [data.INDEX]
        try:
            df = yf.download(tickers, period="1d", interval="5m", auto_adjust=True, progress=False,
                             group_by="ticker", threads=True)
        except Exception:
            return {}
        out = {}
        for t in tickers:
            try:
                s = df[t][["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(s):
                s.index = s.index.tz_convert(JST) if s.index.tz is not None else s.index
                out[t.replace(".T", "")] = s
        return out
    return cached("intraday", 60, load)


def news_feed():
    def load():
        c = cfg()
        st = state()
        held = [p["name"] for p in (st["engine"]["positions"].values() if st else [])]
        picked = (_cache.get("pick_names") or (0, []))[1]
        queries = NEWS_QUERIES + [f"{n} 株" for n in held + [p for p in picked if p not in held]]
        with ThreadPoolExecutor(6) as ex:
            lists = list(ex.map(news.headlines_full, queries))
        seen, items = set(), []
        for lst in lists:
            for it in lst:
                if it["title"] in seen:
                    continue
                seen.add(it["title"])
                it["tone"] = news.tone(it["title"])
                it["held"] = any(n in it["title"] for n in held)
                it["pick"] = next((n for n in picked if n in it["title"]), None)
                items.append(it)
        items.sort(key=lambda x: x["time"], reverse=True)
        return items[:60]
    return cached("news", 600, load)


def now_price(code, ind, intra):
    s = intra.get(code)
    if s is not None and len(s):
        return float(s["Close"].iloc[-1]), s.index[-1].strftime("%H:%M")
    d = ind.get(code)
    return (float(d["Close"].iloc[-1]), d.index[-1].strftime("%m/%d")) if d is not None else (None, "")


def prev_close(close, s):
    """前日の終値（5分足があればその日の前日、なければ日足の1つ前）"""
    if s is not None and len(s):
        day = pd.Timestamp(s.index[-1].date())
        before = close[close.index < day]
        return float(before.iloc[-1]) if len(before) else None
    return float(close.iloc[-2]) if len(close) > 1 else None


def market_status():
    n = datetime.now(JST)
    if n.weekday() >= 5:
        return "休場（土日）"
    hm = n.hour * 60 + n.minute
    if 9 * 60 <= hm < 11 * 60 + 30 or 12 * 60 + 30 <= hm < 15 * 60 + 30:
        return "取引時間中"
    if 11 * 60 + 30 <= hm < 12 * 60 + 30:
        return "昼休み"
    return "取引時間外"


def api_summary():
    c = cfg()
    st = state()
    ind, ix = daily()
    intra = intraday()
    fund = data.load_fundamentals([x for x, _ in c["universe"]], ROOT / "結果" / "企業データ.json")
    names = dict(c["universe"])
    eng = st["engine"] if st else {"cash": c["capital"], "positions": {}, "orders": [], "trades": [], "equity": [], "log": []}
    sma75 = ix["Close"].rolling(75).mean()
    ix_now = intra.get(data.INDEX)
    ix_price = float(ix_now["Close"].iloc[-1]) if ix_now is not None and len(ix_now) else float(ix["Close"].iloc[-1])
    ix_prev = prev_close(ix["Close"], ix_now) or ix_price
    regime = bool(ix_price > sma75.iloc[-1])

    positions, mv = [], 0.0
    for code, p in eng["positions"].items():
        price, at = now_price(code, ind, intra)
        price = price or p["last"]
        mv += price * p["shares"]
        d = ind.get(code)
        v, _ = evaluate(fund.get(code), d.iloc[-1], c, regime) if d is not None else ({}, 1)
        positions.append({
            "code": code, "name": p["name"], "shares": p["shares"], "entry_date": p["entry_date"],
            "entry": p["entry"], "cost_ps": p["cost_ps"], "price": price, "at": at,
            "pnl": (price - p["cost_ps"]) * p["shares"], "pnl_pct": price / p["cost_ps"] - 1,
            "stop": p["stop"], "target": None if p["partial"] else p["target"], "partial": p["partial"],
            "trail_note": "利益確定済み。残りは株価が上がると損切りラインも一緒に上がる" if p["partial"]
            else f"{p['target']:,.0f}円で半分を利益確定 → 残りは上昇に合わせて損切りラインを引き上げ",
            "days": p["days"], "views": v})

    orders = []
    for o in eng["orders"]:
        price, _ = now_price(o["code"], ind, intra)
        item = {"side": o["side"], "code": o["code"], "name": names.get(o["code"], o["code"]), "shares": o["shares"],
                "reason": o["reason"], "price": price}
        if o["side"] == "buy":
            item.update({"ref": o["ref"], "stop": o["ref"] - o["stopdist"], "target": o["ref"] + c["target_r"] * o["stopdist"],
                         "max_price": o["ref"] * (1 + c["max_gap_up"]), "amount": o["shares"] * o["ref"]})
        orders.append(item)

    fund_ok = {k: data.fundamental_ok(fund.get(k), c["fundamental"]) for k in names}
    watch = []
    for code, name in names.items():
        d = ind.get(code)
        if d is None or len(d) < 80:
            continue
        r = d.iloc[-1]
        price, at = now_price(code, ind, intra)
        prev = prev_close(d["Close"], intra.get(code))
        trend = "上昇" if r["Close"] > r["sma25"] > r["sma75"] else "下降" if r["Close"] < r["sma25"] < r["sma75"] else "もみあい"
        trigger = float(max(r["hh"], r["Close"])) if pd.notna(r["hh"]) else None
        stopdist = min(max(c["stop_atr"] * float(r["atr"]), c["min_stop_pct"] * trigger), c["max_stop_pct"] * trigger) if trigger else None
        ok, why = fund_ok[code]
        v, size = evaluate(fund.get(code), r, c, regime)
        f = fund.get(code) or {}
        watch.append({
            "code": code, "name": name, "price": price, "at": at, "chg": (price / prev - 1) if price and prev else 0,
            "trend": trend, "fund_ok": ok, "fund_note": why, "held": code in eng["positions"],
            "trigger": trigger, "to_trigger": (trigger / price - 1) if trigger and price else None,
            "plan_stop": trigger - stopdist if trigger else None,
            "plan_target": trigger + c["target_r"] * stopdist if trigger else None,
            "per": f.get("trailingPE") or f.get("forwardPE"), "pbr": f.get("priceToBook"), "roe": f.get("returnOnEquity"),
            "size": size, "views": v})
    watch.sort(key=lambda w: (not w["held"], not w["fund_ok"], w["trend"] != "上昇", w["to_trigger"] if w["to_trigger"] is not None else 9))

    # 「いま一番良いと思う銘柄」：このツールの売買ルールにどれだけ近いかを100点満点で採点
    picks = []
    for w in watch:
        if w["held"]:
            continue
        d = ind[w["code"]]
        r = d.iloc[-1]
        q = data.quality(fund.get(w["code"]), c.get("quality_parts", {"roe": 1, "growth": 1, "netcash": 0.5}))
        tt = w["to_trigger"]
        checks = [
            ("会社選び（PER・ROE・売上の伸び）", w["fund_ok"], 25 if w["fund_ok"] else 0, w["fund_note"]),
            ("上昇トレンド（株価＞25日平均＞75日平均）", w["trend"] == "上昇", 25 if w["trend"] == "上昇" else 10 if w["trend"] == "もみあい" else 0, w["trend"]),
            ("買う目安（20日間の高値）に近い", tt is not None and tt <= 0.02,
             20 if tt is not None and tt <= 0 else 15 if tt is not None and tt <= 0.02 else 8 if tt is not None and tt <= 0.05 else 0,
             "到達" if tt is not None and tt <= 0 else f"あと{tt * 100:.1f}%" if tt is not None else "-"),
            ("会社の良さ（ROE・成長・現金）", q > 0.1, max(0, min(15, q * 60)), f"点数 {q:.2f}"),
            ("勢い（60日の上昇率）", (r["mom60"] or 0) > 0, max(0, min(10, float(r["mom60"] or 0) * 50)), f"{float(r['mom60'] or 0) * 100:+.0f}%"),
            ("割高・上がりすぎではない", w["size"] >= 1, 5 if w["size"] >= 1 else 0, w["views"].get("結論", "")),
        ]
        score = sum(x[2] for x in checks)
        picks.append({"code": w["code"], "name": w["name"], "price": w["price"], "chg": w["chg"], "score": round(score),
                      "checks": [{"label": a, "ok": bool(b), "note": n} for a, b, _, n in checks],
                      "trigger": w["trigger"], "plan_stop": w["plan_stop"], "plan_target": w["plan_target"],
                      "next": "買い注文を出す候補" if score >= 80 and regime else
                              "地合いが良くなれば候補" if score >= 80 else "条件がそろうのを待つ"})
    picks.sort(key=lambda p: -p["score"])
    picks = picks[:5]
    with _lock:
        _cache["pick_names"] = (time.time(), [p["name"] for p in picks[:3]])

    equity = eng["cash"] + mv
    ixs = ix["Close"]
    eq = eng["equity"]
    bench = []
    if eq:
        base = ixs.asof(pd.Timestamp(eq[0][0]))
        bench = [[d, round(c["capital"] * float(ixs.asof(pd.Timestamp(d))) / float(base))] for d, _ in eq]
    return {
        "updated": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"), "market": market_status(),
        "capital": c["capital"], "equity": equity, "cash": eng["cash"], "invested": mv,
        "pnl": equity - c["capital"], "fees": eng.get("fees", 0),
        "index": {"price": ix_price, "chg": ix_price / ix_prev - 1, "sma75": float(sma75.iloc[-1]), "regime": regime},
        "macro_score": (st or {}).get("news", {}).get("macro_score"), "last_run": (st or {}).get("last_date"),
        "positions": positions, "orders": orders, "watch": watch, "picks": picks, "trades": eng["trades"][-30:][::-1],
        "equity_curve": eq, "bench": bench, "log": eng["log"][-20:][::-1],
        "rules": {"risk": c["risk_per_trade"], "target_r": c["target_r"], "max_pos": c["max_positions"]},
    }


def api_chart(code):
    c = cfg()
    ind, ix = daily()
    intra = intraday()
    d = ind.get(code) if code != "N225" else data.add_indicators(ix)
    if d is None:
        return {}
    d = d.tail(250)
    st = state()
    p = (st or {}).get("engine", {}).get("positions", {}).get(code)
    s = intra.get(code if code != "N225" else data.INDEX)
    r2 = lambda x: None if pd.isna(x) else round(float(x), 1)
    vol = lambda v: 0.0 if pd.isna(v) else float(v)
    candles = [{"time": t.strftime("%Y-%m-%d"), "open": r2(o), "high": r2(h), "low": r2(l), "close": r2(cl), "vol": vol(v)}
               for t, o, h, l, cl, v in zip(d.index, d["Open"], d["High"], d["Low"], d["Close"], d["Volume"])
               if not pd.isna(o) and not pd.isna(cl)]
    # 5分足：グラフ部品は世界標準時で表示するので、日本時間に見えるよう9時間ずらした数字を渡す
    intra_c = []
    if s is not None:
        for t, row in s.iterrows():
            if row[["Open", "High", "Low", "Close"]].isna().any():
                continue
            ts = int(t.timestamp()) + 9 * 3600
            intra_c.append({"time": ts, "open": r2(row["Open"]), "high": r2(row["High"]), "low": r2(row["Low"]),
                            "close": r2(row["Close"]), "vol": vol(row["Volume"])})
    return {
        "code": code, "name": dict(c["universe"]).get(code, "日経平均"),
        "candles": candles,
        "sma25": [{"time": t.strftime("%Y-%m-%d"), "value": r2(v)} for t, v in d["sma25"].items() if pd.notna(v)],
        "sma75": [{"time": t.strftime("%Y-%m-%d"), "value": r2(v)} for t, v in d["sma75"].items() if pd.notna(v)],
        "intraday": intra_c,
        "position": p,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == "/":
                self._send((ROOT / "kabu" / "dashboard.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
            elif u.path == "/api/summary":
                self._send(json.dumps(api_summary(), ensure_ascii=False, default=float), "application/json")
            elif u.path == "/api/news":
                self._send(json.dumps(news_feed(), ensure_ascii=False), "application/json")
            elif u.path == "/api/chart":
                code = parse_qs(u.query).get("code", ["N225"])[0]
                self._send(json.dumps(api_chart(code), ensure_ascii=False, default=float), "application/json")
            else:
                self.send_error(404)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"error: {e}".encode("utf-8"))


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"画面を開いています: {url}  （この黒い画面を閉じると止まります）")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
