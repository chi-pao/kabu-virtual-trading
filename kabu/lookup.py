"""気になる銘柄を調べる：短期・中期・長期の点数と、今の数字をまとめる。

・銘柄の一覧（コード・日本語の社名・業種）は日本取引所(JPX)の公式一覧を月1回取ってくる
・点数は数字から機械的に出した参考値で、値上がりを約束するものではない
"""
import json
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from . import data, horizon
from .views import evaluate

JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"


def _norm(s):
    """全角→半角・ひらがな→カタカナ・小文字にそろえて、検索しやすくする"""
    s = unicodedata.normalize("NFKC", str(s)).lower().replace(" ", "")
    return "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in s)


_KANA = {}
for _cons, _row in [("", "アイウエオァィゥェォヲーッャュョヮ"), ("k", "カキクケコヵヶ"), ("g", "ガギグゲゴ"), ("s", "サシスセソザズゼゾ"),
                    ("j", "ジ"), ("t", "タテトツ"), ("c", "チ"), ("d", "ダヂヅデド"), ("n", "ナニヌネノン"), ("h", "ハヒヘホ"),
                    ("f", "フ"), ("b", "バビブベボヴ"), ("p", "パピプペポ"), ("m", "マミムメモ"), ("y", "ヤユヨ"),
                    ("r", "ラリルレロ"), ("w", "ワ")]:
    for _ch in _row:
        _KANA[_ch] = _cons


def skeleton(s):
    """カタカナと英語の社名を、子音だけの並びにそろえる（例：サンサン と Sansan → snsn）"""
    s = _norm(s)
    if re.search(r"[ァ-ヶー]", s):
        out = "".join(_KANA.get(ch, "") for ch in s if "ァ" <= ch <= "ヶ" or ch == "ー")
    else:
        s = re.sub(r"[^a-z]", "", s)
        for a, b in [("ph", "f"), ("th", "s"), ("sh", "s"), ("ch", "c"), ("ck", "k"), ("q", "k"), ("x", "ks"),
                     ("l", "r"), ("v", "b"), ("z", "s")]:
            s = s.replace(a, b)
        s = re.sub(r"c(?=[eiy])", "s", s).replace("c", "k")
        s = re.sub(r"m(?=[bp])", "n", s)
        s = re.sub(r"(?<=[^aeiou])h", "", s)
        s = re.sub(r"[wy](?![aeiou])", "", s)
        out = re.sub(r"[aeiouy]", "", s)
    return re.sub(r"(.)\1+", r"\1", out)


def master(cache_path, max_age_days=30):
    """[{code, name, market, sector, size}]（国内の株だけ）。取れないときは前回の一覧を使う"""
    p = Path(cache_path)
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    if old and old.get("_at", "") > (datetime.now() - timedelta(days=max_age_days)).isoformat():
        return [x for x in old["list"] if len(x["code"]) == 4]
    try:
        df = pd.read_excel(JPX_URL, dtype=str)
        df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
        lst = [{"code": r["コード"].strip(), "name": unicodedata.normalize("NFKC", r["銘柄名"]).strip(),
                "market": r["市場・商品区分"].replace("（内国株式）", ""), "sector": r["33業種区分"],
                "size": r["規模区分"]} for _, r in df.iterrows()]
        p.write_text(json.dumps({"_at": datetime.now().isoformat(), "list": lst}, ensure_ascii=False), encoding="utf-8")
        return [x for x in lst if len(x["code"]) == 4]   # 5けたは優先株などの特殊な株なので除く
    except Exception as e:
        print(f"  銘柄一覧を取得できませんでした ({e})")
        return old["list"] if old else []


def search(q, lst, limit=20):
    """コード（4けた）か社名の一部で探す"""
    qn = _norm(q)
    if not qn:
        return []
    code = qn.upper()
    if re.fullmatch(r"[0-9][0-9A-Z]{3}", code):
        hit = [x for x in lst if x["code"] == code]
        if hit:
            return hit
    hits = [x for x in lst if qn in _norm(x["name"])]
    hits.sort(key=lambda x: (not _norm(x["name"]).startswith(qn), len(x["name"])))
    if len(hits) < limit and re.fullmatch(r"[ァ-ヶー]+", qn):   # カタカナで英語名の会社も探す
        sk = skeleton(qn)
        if len(sk) >= 2:
            seen = {x["code"] for x in hits}
            more = [x for x in lst if x["code"] not in seen and not re.search(r"[ァ-ヶ]", x["name"])
                    and skeleton(x["name"]).startswith(sk)]
            hits += sorted(more, key=lambda x: len(x["name"]))
    return hits[:limit]


def plan(d, cfg):
    """このツールのルールで買うなら：買う目安（20日間の高値）・損切り・利確の予定"""
    r = d.iloc[-1]
    trend = "上昇" if r["Close"] > r["sma25"] > r["sma75"] else "下降" if r["Close"] < r["sma25"] < r["sma75"] else "もみあい"
    trigger = float(max(r["hh"], r["Close"])) if pd.notna(r["hh"]) else None
    sd = None
    if trigger and pd.notna(r["atr"]):
        sd = min(max(cfg["stop_atr"] * float(r["atr"]), cfg["min_stop_pct"] * trigger), cfg["max_stop_pct"] * trigger)
    return {"trend": trend, "trigger": trigger,
            "plan_stop": trigger - sd if sd else None,
            "plan_target": trigger + cfg["target_r"] * sd if sd else None}


def short_term(f, d, cfg, regime, price=None):
    """短期（数日〜数週間）：このツールの売買ルールにどれだけ当てはまるか。100点満点"""
    r = d.iloc[-1]
    price = price or float(r["Close"])
    pl = plan(d, cfg)
    ok, why = data.fundamental_ok(f, cfg["fundamental"])
    _, size = evaluate(f, r, cfg, regime) if pd.notna(r["sma25"]) else ({}, 1)
    q = data.quality(f, cfg.get("quality_parts", {"roe": 1, "growth": 1, "netcash": 0.5}))
    tt = pl["trigger"] / price - 1 if pl["trigger"] else None
    mom = float(r["mom60"]) if pd.notna(r["mom60"]) else 0.0
    rsi = float(r["rsi"]) if pd.notna(r["rsi"]) else None
    tr = pl["trend"]
    items = [
        ("会社選び（PER・ROE・売上の伸び）", 25 if ok else 0, 25, why if ok else f"✕ {why}"),
        ("上昇トレンド（株価＞25日平均＞75日平均）", 25 if tr == "上昇" else 10 if tr == "もみあい" else 0, 25, tr),
        ("買う目安（20日間の高値）に近い",
         20 if tt is not None and tt <= 0 else 15 if tt is not None and tt <= 0.02 else 8 if tt is not None and tt <= 0.05 else 0, 20,
         "到達" if tt is not None and tt <= 0 else f"あと{tt * 100:.1f}%" if tt is not None else "-"),
        ("会社の良さ（ROE・成長・現金）", max(0, min(15, q * 60)), 15, f"点数 {q:.2f}"),
        ("勢い（60日の上昇率）", max(0, min(10, mom * 50)), 10, f"{mom * 100:+.0f}%"),
        ("割高・上がりすぎではない", 5 if size >= 1 else 0, 5, "通常の量で買える" if size >= 1 else "買うなら半分の量"),
    ]
    score = round(sum(p for _, p, _, _ in items))
    reasons = [{"ok": p >= mx * 0.6, "mid": mx * 0.3 <= p < mx * 0.6, "label": lab, "note": note,
                "text": f"{lab}：{note}", "pts": round(p), "max": mx} for lab, p, mx, note in items]
    nxt = ("買い注文を出す候補" if score >= 80 and regime else "地合いが良くなれば候補" if score >= 80
           else "条件がそろうのを待つ")
    extra = []
    if rsi is not None:
        extra.append(f"RSI {rsi:.0f}（70以上は買われすぎ・30以下は売られすぎの目安）")
    if not regime:
        extra.append("いまは日経平均が75日平均より下なので、このツールは新しく買うのをお休み中")
    return score, reasons, {**pl, "to_trigger": tt, "next": nxt, "notes": extra, "fund_ok": ok, "fund_note": why}


def analyze(code, d, f, cfg, regime, meta=None, price=None, at="", prev=None, held=False):
    """1銘柄の、今の数字と短期・中期・長期の点数をまとめる"""
    meta = meta or {}
    f = f or {}
    r = d.iloc[-1]
    price = price or float(r["Close"])
    prev = prev or (float(d["Close"].iloc[-2]) if len(d) > 1 else price)
    y = d.tail(250)
    hi, lo = float(y["High"].max()), float(y["Low"].min())
    v20 = r["vol20"]
    out = {
        "code": code, "name": meta.get("name") or f.get("shortName") or code,
        "market": meta.get("market", ""), "sector": meta.get("sector", ""), "size_class": meta.get("size", ""),
        "price": price, "at": at or d.index[-1].strftime("%m/%d"), "chg": price / prev - 1 if prev else 0, "held": held,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "facts": _facts(f, price, hi, lo, r, v20),
    }
    if len(d) < 80:
        out["note"] = "上場して間もないなどで株価データが少ないため、点数は出せません"
        return out
    s, rs, pl = short_term(f, d, cfg, regime, price)
    out["short"] = {"score": s, "reasons": rs, **pl}
    if len(d) >= 130:
        out["mid"] = dict(zip(("score", "reasons"), horizon.mid_term(f, d)))
        out["long"] = dict(zip(("score", "reasons"), horizon.long_term(f, d, code)))
    out["views"], _ = evaluate(f, r, cfg, regime)
    out["verdict"] = _verdict(out)
    tail = d.tail(130)
    rnd = lambda s: [None if pd.isna(v) else round(float(v), 1) for v in s]
    out["series"] = {"dates": [t.strftime("%m/%d") for t in tail.index], "close": rnd(tail["Close"]),
                     "sma25": rnd(tail["sma25"]), "sma75": rnd(tail["sma75"])}
    return out


def _facts(f, price, hi, lo, r, v20):
    p = lambda x: None if x is None else round(x * 100, 1)
    nx, up = data.next_year_change(f), data.analyst_upside(f, price)
    tp = f.get("targetMeanPrice")
    return [
        ["PER（今期）", f"{f['trailingPE']:.1f}倍" if f.get("trailingPE") else "赤字またはデータなし"],
        ["PER（来期予想）", f"{f['forwardPE']:.1f}倍" if data._ok_pe(f.get("forwardPE")) else "データなし・不正確"],
        ["PBR", f"{f['priceToBook']:.2f}倍" if f.get("priceToBook") else "-"],
        ["ROE（稼ぐ力）", f"{p(f['returnOnEquity'])}%" if f.get("returnOnEquity") is not None else "-"],
        ["配当利回り", f"{f.get('dividendYield') or 0:.2f}%"],
        ["売上の伸び（前年比）", f"{p(f['revenueGrowth']):+}%" if f.get("revenueGrowth") is not None else "-"],
        ["利益の伸び（前年比）", f"{p(f['earningsGrowth']):+}%" if f.get("earningsGrowth") is not None else "-"],
        ["来期の利益見込み", f"{p(nx):+}%" if nx is not None else "予想なし・不正確"],
        ["アナリストの目標株価", f"{tp:,.0f}円（今より{p(up):+}%・{f.get('numberOfAnalystOpinions')}人）" if up is not None else "予想が少ない・不正確"],
        ["時価総額", f"{f['marketCap'] / 1e8:,.0f}億円" if f.get("marketCap") else "-"],
        ["ネットキャッシュ比率", f"{data.net_cash_ratio(f) * 100:+.0f}%（現金−借金÷時価総額）" if f.get("marketCap") else "-"],
        ["1年の高値・安値", f"{hi:,.0f}円 ／ {lo:,.0f}円（高値から{(price / hi - 1) * 100:+.0f}%）"],
        ["25日平均からの離れ", f"{(price / r['sma25'] - 1) * 100:+.1f}%" if pd.notna(r["sma25"]) else "-"],
        ["出来高（今日÷20日平均）", f"{r['Volume'] / v20:.1f}倍" if pd.notna(v20) and v20 else "-"],
    ]


def _verdict(o):
    """3つの点数をまとめた、ひとこと"""
    lab = lambda s: "高い" if s >= 70 else "まずまず" if s >= 55 else "低め"
    parts = [f"短期{o['short']['score']}点（{lab(o['short']['score'])}）"]
    if "mid" in o:
        parts += [f"中期{o['mid']['score']}点（{lab(o['mid']['score'])}）", f"長期{o['long']['score']}点（{lab(o['long']['score'])}）"]
    return "・".join(parts)
