"""株価・企業の数字（PER/ROEなど）を無料で取ってくる"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

INDEX = "^N225"


def _ticker(code):
    return code if code.startswith("^") else f"{code}.T"


def load_prices(codes, period="6y"):
    """{コード: 日足(Open/High/Low/Close/Volume)} を返す。分割・配当は調整済み"""
    tickers = [_ticker(c) for c in codes]
    df = yf.download(tickers, period=period, auto_adjust=True, progress=False,
                     group_by="ticker", threads=True)
    out = {}
    for c, t in zip(codes, tickers):
        try:
            d = df[t][["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "High", "Low", "Close"])
        except KeyError:
            continue
        d = d[(d["Close"] > 0) & (d["Open"] > 0)]
        if len(d):
            d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
            out[c] = d
    return out


def add_indicators(d, breakout_days=20):
    """移動平均・ATR(値動きの大きさ)・RSI・高値更新などを計算（その日までのデータだけを使う）"""
    d = d.copy()
    c = d["Close"]
    d["sma25"] = c.rolling(25).mean()
    d["sma75"] = c.rolling(75).mean()
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - c.shift()).abs(), (d["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    d["hh"] = c.shift(1).rolling(breakout_days).max()
    d["vol20"] = d["Volume"].shift(1).rolling(20).mean()
    delta = c.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    d["mom60"] = c / c.shift(60) - 1
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    d["macd_gc"] = (macd > sig) & (macd.shift(1) <= sig.shift(1))  # MACDのゴールデンクロス
    d["rsi_min3"] = d["rsi"].rolling(3).min()                       # 直近3日のRSIの最低
    return d


FIELDS = ["shortName", "trailingPE", "forwardPE", "priceToBook", "returnOnEquity",
          "earningsGrowth", "revenueGrowth", "dividendYield", "totalCash", "totalDebt", "marketCap"]


def net_cash_ratio(f):
    """清原達郎さんのネットキャッシュ比率の簡易版：(現金 − 借金) ÷ 時価総額。銀行・保険は意味がないので0扱い"""
    if not f or not f.get("marketCap") or f.get("totalCash") is None:
        return 0.0
    return ((f.get("totalCash") or 0) - (f.get("totalDebt") or 0)) / f["marketCap"]


def quality(f, w):
    """会社の良さの点数（高いほど良い）。w = {"roe":…, "growth":…, "netcash":…}"""
    if not f:
        return 0.0
    roe = min(max(f.get("returnOnEquity") or 0, -0.2), 0.4)
    growth = min(max(((f.get("revenueGrowth") or 0) + (f.get("earningsGrowth") or 0)) / 2, -0.5), 0.5)
    nc = min(max(net_cash_ratio(f), -0.5), 0.5)
    return w.get("roe", 0) * roe + w.get("growth", 0) * growth + w.get("netcash", 0) * nc


def load_fundamentals(codes, cache_path, max_age_days=7):
    """PER・PBR・ROEなど。1週間に1回だけ取り直す"""
    cache_path = Path(cache_path)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    limit = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    changed = False
    for c in codes:
        if c in cache and cache[c].get("_at", "") > limit and "marketCap" in cache[c]:
            continue
        try:
            info = yf.Ticker(_ticker(c)).info
            cache[c] = {k: info.get(k) for k in FIELDS}
            cache[c]["_at"] = datetime.now().isoformat()
            changed = True
            time.sleep(0.3)
        except Exception as e:  # 取れなかった銘柄は前回の値を使う
            print(f"  {c}: 企業データを取得できませんでした ({e})")
    if changed:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


def fundamental_ok(f, rule):
    """良い会社かどうか（ROEが高い・割高すぎない・利益が大きく減っていない）"""
    if not f:
        return False, "データなし"
    roe, per, pbr, eg = f.get("returnOnEquity"), f.get("trailingPE") or f.get("forwardPE"), f.get("priceToBook"), f.get("earningsGrowth")
    if roe is None or roe < rule["roe_min"]:
        return False, f"ROEが低い({_pct(roe)})"
    if per is None or not (rule["per_min"] < per <= rule["per_max"]):
        return False, f"PERが範囲外({_num(per)})"
    if pbr is not None and pbr > rule["pbr_max"]:
        return False, f"PBRが高すぎ({_num(pbr)})"
    if eg is not None and eg < rule["earnings_growth_min"]:
        return False, f"利益が大きく減少({_pct(eg)})"
    rg = f.get("revenueGrowth")
    if rule.get("revenue_growth_min") is not None and (rg is None or rg < rule["revenue_growth_min"]):
        return False, f"売上の伸びが足りない({_pct(rg)})"
    return True, f"ROE {_pct(roe)} / PER {_num(per)} / PBR {_num(pbr)}"


def _pct(x):
    return "-" if x is None else f"{x * 100:.1f}%"


def _num(x):
    return "-" if x is None else f"{x:.1f}"
