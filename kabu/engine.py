"""売買のルール本体。過去データでの検証と、毎日の仮想売買の両方で同じルールを使う。

1日の流れ（その日の株価が出たあとに1回動く）
  1. 前の日に決めた注文を、その日の寄り付き（始値）で約定させる
  2. 持ち株を点検：損切りライン・利益確定ラインに触れたら、その値段で売る（逆指値・指値を置いている想定）
  3. 引け後：損切りラインを引き上げる（トレーリング）、トレンドが崩れた株は翌日売る注文を出す
  4. 条件を満たした株があれば、翌日買う注文を出す
"""
import math

from .views import evaluate


class Engine:
    def __init__(self, cfg, state=None):
        self.cfg = cfg
        self.s = state or {"cash": float(cfg["capital"]), "positions": {}, "orders": [],
                           "trades": [], "equity": [], "fees": 0.0, "log": []}

    # ---------- お金の計算（手数料・スリッページ込み） ----------
    def _fee(self, value):
        return max(value * self.cfg["fee_rate"], self.cfg["fee_min"])

    def _buy(self, date, code, name, shares, price, stopdist, reason):
        px = price * (1 + self.cfg["slippage"])
        fee = self._fee(px * shares)
        cost = px * shares + fee
        if cost > self.s["cash"]:
            shares = int(self.s["cash"] / (px * (1 + self.cfg["fee_rate"])) // 100 * 100)
            if shares < 100:
                return
            fee = self._fee(px * shares)
            cost = px * shares + fee
        self.s["cash"] -= cost
        self.s["fees"] += fee
        stop = price - stopdist
        self.s["positions"][code] = {
            "name": name, "shares": shares, "entry": px, "cost_ps": cost / shares,
            "stop": stop, "stopdist": stopdist, "target": price + self.cfg["target_r"] * stopdist,
            "partial": False, "highest": price, "days": 0, "entry_date": date, "last": price}
        self._log(date, f"買い {name}({code}) {shares}株 @{px:,.0f}円  損切り{stop:,.0f} / 利確{price + self.cfg['target_r'] * stopdist:,.0f}  [{reason}]")

    def _sell(self, date, code, shares, price, reason):
        p = self.s["positions"][code]
        shares = min(shares, p["shares"])
        px = price * (1 - self.cfg["slippage"])
        fee = self._fee(px * shares)
        proceeds = px * shares - fee
        pnl = proceeds - p["cost_ps"] * shares
        self.s["cash"] += proceeds
        self.s["fees"] += fee
        self.s["trades"].append({"code": code, "name": p["name"], "entry_date": p["entry_date"], "exit_date": date,
                                 "shares": shares, "entry": round(p["entry"], 1), "exit": round(px, 1),
                                 "pnl": round(pnl), "ret": round(pnl / (p["cost_ps"] * shares), 4), "reason": reason})
        self._log(date, f"売り {p['name']}({code}) {shares}株 @{px:,.0f}円  損益 {pnl:+,.0f}円  [{reason}]")
        p["shares"] -= shares
        if p["shares"] <= 0:
            del self.s["positions"][code]

    def _log(self, date, msg):
        self.s["log"].append(f"{date} {msg}")

    def equity(self):
        return self.s["cash"] + sum(p["shares"] * p["last"] for p in self.s["positions"].values())

    # ---------- 1日分の処理 ----------
    def step(self, date, ind, names, fund_ok, regime_ok, allow_entries=True, news_check=None, quality=None, fund=None):
        quality = quality or {}
        fund = fund or {}
        qw = self.cfg.get("quality_weight", 0)
        cfg = self.cfg
        ds = date.strftime("%Y-%m-%d")

        # 1) 前日に出した注文を寄り付きで約定
        orders, self.s["orders"] = self.s["orders"], []
        for o in orders:
            d = ind.get(o["code"])
            if d is None or date not in d.index:
                if o.get("age", 0) < 3:
                    o["age"] = o.get("age", 0) + 1
                    self.s["orders"].append(o)
                continue
            r = d.loc[date]
            if o["side"] == "sell" and o["code"] in self.s["positions"]:
                self._sell(ds, o["code"], o["shares"], r["Open"], o["reason"])
            elif o["side"] == "buy" and o["code"] not in self.s["positions"]:
                if r["Open"] > o["ref"] * (1 + cfg["max_gap_up"]):
                    self._log(ds, f"見送り {names[o['code']]}: 寄り付きで{cfg['max_gap_up']*100:.0f}%以上高く始まった（高値づかみ防止）")
                    continue
                if r["Open"] <= o["ref"] - o["stopdist"]:
                    continue
                self._buy(ds, o["code"], names[o["code"]], o["shares"], r["Open"], o["stopdist"], o["reason"])

        # 2) 持ち株の点検（その日の高値・安値で、損切り・利確に触れたか）
        for code in list(self.s["positions"]):
            p = self.s["positions"][code]
            d = ind.get(code)
            if d is None or date not in d.index:
                continue
            r = d.loc[date]
            if r["Low"] <= p["stop"]:
                why = "損切り" if p["stop"] < p["entry"] else "利益を守る売り(トレーリング)"
                self._sell(ds, code, p["shares"], min(r["Open"], p["stop"]), why)
                continue
            if not p["partial"] and r["High"] >= p["target"]:
                half = p["shares"] // 200 * 100 or p["shares"]
                self._sell(ds, code, half, max(r["Open"], p["target"]), "利益確定(半分)")
                if code not in self.s["positions"]:
                    continue
                p["partial"] = True
                cost_rate = 2 * (cfg["fee_rate"] + cfg["slippage"])
                p["stop"] = max(p["stop"], p["cost_ps"] * (1 + cost_rate))  # 残りは最悪でもトントン
            # 3) 引け後の処理
            p["last"] = float(r["Close"])
            p["days"] += 1
            p["highest"] = max(p["highest"], float(r["Close"]))
            if p["partial"] or r["Close"] > p["entry"] + p["stopdist"]:
                p["stop"] = max(p["stop"], p["highest"] - cfg["trail_atr"] * float(r["atr"]))
            mode = cfg.get("entry", "breakout")
            if mode == "rsi_macd" and r["rsi"] >= cfg.get("rsi_exit", 70):
                self.s["orders"].append({"side": "sell", "code": code, "shares": p["shares"], "reason": "RSIが70以上（買われすぎ）"})
            elif mode == "breakout" and r["Close"] < r["sma75"]:
                self.s["orders"].append({"side": "sell", "code": code, "shares": p["shares"], "reason": "上昇トレンドが崩れた"})
            elif p["days"] >= cfg["max_hold_days"] and r["Close"] < p["cost_ps"]:
                self.s["orders"].append({"side": "sell", "code": code, "shares": p["shares"], "reason": "長く持っても上がらない"})

        # 4) 新しく買う株を探す
        eq = self.equity()
        if allow_entries and regime_ok:
            slots = cfg["max_positions"] - len(self.s["positions"])
            cands = []
            for code, d in ind.items():
                if code in self.s["positions"] or not fund_ok.get(code) or date not in d.index:
                    continue
                r = d.loc[date]
                if r[["sma25", "sma75", "atr", "hh", "vol20", "rsi", "mom60"]].isna().any():
                    continue
                if cfg.get("entry", "breakout") == "rsi_macd":
                    # 売られすぎ(RSI30以下)のあと、MACDがゴールデンクロス → 反発の始まりを買う
                    if r["rsi_min3"] <= cfg.get("rsi_entry", 30) and r["macd_gc"]:
                        cands.append((-r["rsi_min3"], code, r))
                elif (r["Close"] > r["sma25"] > r["sma75"] and r["Close"] >= r["hh"]
                        and r["Volume"] > cfg["volume_ratio"] * r["vol20"] and r["rsi"] < cfg["rsi_max"]):
                    # 勢い(60日の上昇率) ＋ 会社の良さ(ROE・成長・ネットキャッシュ) で順位をつける
                    cands.append((r["mom60"] + qw * quality.get(code, 0), code, r))
            cands.sort(key=lambda x: -x[0])
            cash_left = self.s["cash"]
            for _, code, r in cands:
                if slots <= 0:
                    break
                close = float(r["Close"])
                stopdist = min(max(cfg["stop_atr"] * float(r["atr"]), cfg["min_stop_pct"] * close), cfg["max_stop_pct"] * close)
                size = 1.0
                view = None
                if cfg.get("use_views", False):
                    view, size = evaluate(fund.get(code), r, cfg, regime_ok)
                shares = math.floor(eq * cfg["risk_per_trade"] * size / stopdist / 100) * 100
                shares = min(shares, math.floor(eq * cfg["max_position_pct"] / close / 100) * 100,
                             math.floor(cash_left / (close * 1.01) / 100) * 100)
                if shares < 100:
                    continue
                roundtrip = 2 * shares * close * (cfg["fee_rate"] + cfg["slippage"])
                if cfg["target_r"] * stopdist * shares < cfg["min_reward_cost_ratio"] * roundtrip:
                    continue  # 手数料負けしやすい取引はしない
                if cfg.get("entry", "breakout") == "rsi_macd":
                    reason = f"売られすぎから反発（RSI最低{r['rsi_min3']:.0f}・MACDゴールデンクロス）"
                else:
                    reason = f"高値更新・上昇トレンド・出来高増 (60日で{r['mom60'] * 100:+.0f}%)"
                if view and size < 1:
                    reason += f" / {view['結論']}"
                if news_check:
                    ok, note = news_check(code)
                    if not ok:
                        self._log(ds, f"見送り {names[code]}: {note}")
                        continue
                    reason += f" / {note}"
                self.s["orders"].append({"side": "buy", "code": code, "shares": shares, "stopdist": stopdist,
                                         "ref": close, "reason": reason})
                cash_left -= shares * close * 1.01
                slots -= 1

        self.s["equity"].append([ds, round(self.equity())])
