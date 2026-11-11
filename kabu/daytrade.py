"""デイトレード（その日のうちに必ず売り切る）の売買ルール

無料の株価は約20分遅れなので、「遅れたデータで今まさに売買する」と不公平な結果になる。
そこで、取引時間が終わったあとにその日の5分足を、朝から1本ずつ順番に再生する。
・判断に使うのは「その時点までに出ていた足」だけ（未来の値段は見ない）
・注文は次の足の始値で約定。損切り・利確は足の高値・安値で判定
・同じ足で損切りと利確の両方に届いた場合は、損切りが先に起きたことにする（厳しめ）
"""
import math

import numpy as np
import pandas as pd


def prep_day(df):
    """1日分の5分足に、VWAP・寄り付き30分の高値/安値などを足す（その時点までの値だけ使う）"""
    d = df.copy()
    tp = (d["High"] + d["Low"] + d["Close"]) / 3
    v = d["Volume"].fillna(0)
    cv = v.cumsum()
    d["vwap"] = np.where(cv > 0, (tp * v).cumsum() / cv.replace(0, np.nan), d["Close"].expanding().mean())
    d["avgvol"] = v.expanding().mean().shift(1)
    return d


class DayTrader:
    def __init__(self, cfg, state=None):
        self.cfg = cfg
        self.s = state or {"cash": float(cfg["capital"]), "trades": [], "days": [], "log": [], "fees": 0.0}

    def _cost(self, value):
        return value * self.cfg["fee_rate"]

    def run_day(self, day, bars, index_bars, names, prev_close):
        """day=日付, bars={コード: その日の5分足}, index_bars=日経平均の5分足"""
        cfg = self.cfg
        start_eq = self.s["cash"]
        or_end = pd.Timestamp(f"{day} {cfg['or_end']}").tz_localize("Asia/Tokyo")
        last_entry = pd.Timestamp(f"{day} {cfg['last_entry']}").tz_localize("Asia/Tokyo")
        flat_at = pd.Timestamp(f"{day} {cfg['flat_at']}").tz_localize("Asia/Tokyo")
        ix_open = float(index_bars["Open"].iloc[0]) if index_bars is not None and len(index_bars) else None

        data, info = {}, {}
        for code, df in bars.items():
            if len(df) < 20:
                continue
            d = prep_day(df)
            orb = d[d.index < or_end]
            if len(orb) < 3:
                continue
            hi, lo = float(orb["High"].max()), float(orb["Low"].min())
            op = float(d["Open"].iloc[0])
            rng = (hi - lo) / op
            gap = op / prev_close[code] - 1 if prev_close.get(code) else 0
            data[code] = d
            info[code] = {"hi": hi, "lo": lo, "rng": rng, "gap": gap, "done": False}

        times = sorted(set().union(*[d.index for d in data.values()])) if data else []
        pos, pending, day_trades = {}, [], []
        stop_new = False
        realized = 0.0

        def close_pos(code, price, t, why):
            nonlocal realized
            p = pos.pop(code)
            side = p["side"]
            px = price * (1 - cfg["slippage"]) if side == "買い" else price * (1 + cfg["slippage"])
            fee = self._cost(px * p["shares"])
            gross = (px - p["entry"]) * p["shares"] * (1 if side == "買い" else -1)
            pnl = gross - fee - p["fee_in"]
            self.s["cash"] += pnl
            self.s["fees"] += fee
            realized += pnl
            tr = {"date": str(day), "code": code, "name": names.get(code, code), "side": side, "shares": p["shares"],
                  "in_time": p["time"], "entry": round(p["entry"], 1), "out_time": t.strftime("%H:%M"),
                  "exit": round(px, 1), "pnl": round(pnl), "reason": why, "setup": p["setup"]}
            self.s["trades"].append(tr)
            day_trades.append(tr)
            self.s["log"].append(f"{day} {t.strftime('%H:%M')} 決済 {tr['name']} {side}→{why} {pnl:+,.0f}円")

        for t in times:
            # 1) 前の足で決めた注文をこの足の始値で約定
            for o in pending:
                d = data[o["code"]]
                if t not in d.index or o["code"] in pos:
                    continue
                op = float(d.loc[t, "Open"])
                side = o["side"]
                risk_ps = o["risk_ps"]
                entry = op * (1 + cfg["slippage"]) if side == "買い" else op * (1 - cfg["slippage"])
                stop = entry - risk_ps if side == "買い" else entry + risk_ps
                target = entry + cfg["target_r"] * risk_ps if side == "買い" else entry - cfg["target_r"] * risk_ps
                fee_in = self._cost(entry * o["shares"])
                self.s["fees"] += fee_in
                pos[o["code"]] = {"side": side, "shares": o["shares"], "entry": entry, "stop": stop, "target": target,
                                  "risk_ps": risk_ps, "be": False, "time": t.strftime("%H:%M"), "fee_in": fee_in, "setup": o["setup"]}
                self.s["log"].append(f"{day} {t.strftime('%H:%M')} {side} {names.get(o['code'])} {o['shares']}株 @{entry:,.1f}  損切{stop:,.1f} 利確{target:,.1f} [{o['setup']}]")
            pending = []

            # 2) 持ち株：損切り・利確・時間切れ
            for code in list(pos):
                d = data[code]
                if t not in d.index:
                    continue
                r = d.loc[t]
                p = pos[code]
                if t >= flat_at:
                    close_pos(code, float(r["Open"]), t, "大引け前に全部手じまい")
                    continue
                long = p["side"] == "買い"
                hit_stop = r["Low"] <= p["stop"] if long else r["High"] >= p["stop"]
                hit_tgt = r["High"] >= p["target"] if long else r["Low"] <= p["target"]
                if hit_stop:
                    px = min(float(r["Open"]), p["stop"]) if long else max(float(r["Open"]), p["stop"])
                    close_pos(code, px, t, "トントンで撤退" if p["be"] else "損切り")
                    continue
                if hit_tgt:
                    px = max(float(r["Open"]), p["target"]) if long else min(float(r["Open"]), p["target"])
                    close_pos(code, px, t, "利益確定")
                    continue
                # 1R進んだら損切りを買値に（負けない形にする）
                moved = (r["Close"] - p["entry"]) if long else (p["entry"] - r["Close"])
                if not p["be"] and moved >= p["risk_ps"] * cfg["breakeven_r"]:
                    p["stop"] = p["entry"] * (1 + 2 * cfg["slippage"]) if long else p["entry"] * (1 - 2 * cfg["slippage"])
                    p["be"] = True

            # 3) 1日の損失上限
            if not stop_new and realized < -cfg["daily_loss_limit"] * start_eq:
                stop_new = True
                self.s["log"].append(f"{day} {t.strftime('%H:%M')} 1日の損失上限に達したので、今日はもう新しく買わない")

            # 4) 新しい注文を探す（この足が確定した時点の情報だけで判断）
            if stop_new or t < or_end or t >= last_entry or len(pos) + len(pending) >= cfg["max_positions"]:
                continue
            ix_up = ix_dn = True
            if cfg["use_index"] and index_bars is not None and ix_open:
                ib = index_bars[index_bars.index <= t]
                if len(ib):
                    ic = float(ib["Close"].iloc[-1])
                    ix_up, ix_dn = ic > ix_open, ic < ix_open
            eq = self.s["cash"]
            cands = []
            for code, d in data.items():
                inf = info[code]
                if inf["done"] or code in pos or t not in d.index:
                    continue
                if not (cfg["min_range"] <= inf["rng"] <= cfg["max_range"]) or abs(inf["gap"]) > cfg["max_gap"]:
                    continue
                r = d.loc[t]
                c, vw = float(r["Close"]), float(r["vwap"])
                volok = pd.notna(r["avgvol"]) and r["Volume"] > cfg["vol_mult"] * r["avgvol"]
                rngp = inf["hi"] - inf["lo"]
                setup = None
                if cfg["strategy"] == "orb":
                    if c > inf["hi"] and c > vw and volok and ix_up:
                        setup = ("買い", "寄り付き30分の高値を上抜け")
                    elif cfg["allow_short"] and c < inf["lo"] and c < vw and volok and ix_dn:
                        setup = ("売り", "寄り付き30分の安値を下抜け")
                elif cfg["strategy"] == "vwap":
                    prev = d[d.index < t]
                    if len(prev) < 6:
                        continue
                    vw_up = vw > float(prev["vwap"].iloc[-6])
                    if vw_up and float(r["Low"]) <= vw * 1.001 and c > vw and c > float(r["Open"]) and ix_up:
                        setup = ("買い", "上昇中にVWAPまで押して反発")
                    elif cfg["allow_short"] and not vw_up and float(r["High"]) >= vw * 0.999 and c < vw and c < float(r["Open"]) and ix_dn:
                        setup = ("売り", "下落中にVWAPまで戻して反落")
                if not setup:
                    continue
                risk_ps = max(cfg["stop_or_mult"] * rngp, c * cfg["min_stop_pct"])
                shares = math.floor(eq * cfg["risk_per_trade"] / risk_ps / 100) * 100
                shares = min(shares, math.floor(eq * cfg["max_position_pct"] / c / 100) * 100)
                if shares < 100:
                    continue
                # 手数料・すべりに対して、利確幅が十分あるか
                cost = 2 * c * (cfg["slippage"] + cfg["fee_rate"])
                if cfg["target_r"] * risk_ps < cfg["min_reward_cost"] * cost:
                    continue
                cands.append((inf["rng"], code, setup, risk_ps, shares))
            cands.sort(key=lambda x: -x[0])
            for _, code, (side, why), risk_ps, shares in cands:
                if len(pos) + len(pending) >= cfg["max_positions"]:
                    break
                pending.append({"code": code, "side": side, "risk_ps": risk_ps, "shares": shares, "setup": why})
                info[code]["done"] = True  # 1銘柄1日1回まで

        # 念のため、最後まで残った持ち株は最後の足の終値で手じまい
        for code in list(pos):
            d = data[code]
            close_pos(code, float(d["Close"].iloc[-1]), d.index[-1], "大引け前に全部手じまい")

        wins = sum(1 for x in day_trades if x["pnl"] > 0)
        self.s["days"].append({"date": str(day), "pnl": round(realized), "trades": len(day_trades), "wins": wins,
                               "equity": round(self.s["cash"])})
        return realized
