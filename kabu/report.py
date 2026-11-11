"""成績を計算して、見やすいページ(HTML)にする"""
import html
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams["font.family"] = ["Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP", "sans-serif"]


def stats(state, cfg, index_close=None):
    eq = pd.Series({pd.Timestamp(d): v for d, v in state["equity"]}).sort_index()
    if eq.empty:
        return {}, eq
    start = float(cfg["capital"])
    trades = state["trades"]
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365)
    dd = (eq / eq.cummax() - 1).min()
    # 税金：年ごとの確定利益（プラスの年だけ）に約20%
    by_year = defaultdict(float)
    for t in trades:
        by_year[t["exit_date"][:4]] += t["pnl"]
    tax = sum(max(v, 0) for v in by_year.values()) * cfg["tax_rate"]
    s = {
        "開始資金": start,
        "現在の資産": float(eq.iloc[-1]),
        "損益（税引前）": float(eq.iloc[-1]) - start,
        "損益（税引後の目安）": float(eq.iloc[-1]) - start - tax,
        "利回り（税引前）": float(eq.iloc[-1]) / start - 1,
        "年あたり利回り": (float(eq.iloc[-1]) / start) ** (1 / years) - 1 if years >= 0.5 else None,
        "最大の落ち込み": float(dd),
        "取引回数": len(trades),
        "勝率": len(wins) / len(trades) if trades else None,
        "平均の勝ち": sum(wins) / len(wins) if wins else 0,
        "平均の負け": sum(losses) / len(losses) if losses else 0,
        "プロフィットファクター": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "払った手数料等": state["fees"],
        "税金の目安": tax,
    }
    if index_close is not None:
        ix = index_close[(index_close.index >= eq.index[0]) & (index_close.index <= eq.index[-1])]
        if len(ix) > 1:
            s["比較：日経平均を買って持っていた場合"] = float(ix.iloc[-1] / ix.iloc[0] - 1)
    return s, eq


def fmt(k, v):
    if v is None:
        return "-"
    if any(w in k for w in ["利回り", "落ち込み", "勝率", "比較"]):
        return f"{v * 100:+.1f}%" if "勝率" not in k else f"{v * 100:.0f}%"
    if k in ("取引回数",):
        return f"{v}回"
    if k.endswith("会社数"):
        return f"{v}社"
    if k == "プロフィットファクター":
        return f"{v:.2f}"
    return f"{v:,.0f}円"


def chart(eq, cfg, index_close, path, title):
    fig, ax = plt.subplots(figsize=(9, 4), dpi=110)
    ax.plot(eq.index, eq.values / 10000, label="この作戦", color="#1a7f37", lw=2)
    if index_close is not None:
        ix = index_close[(index_close.index >= eq.index[0]) & (index_close.index <= eq.index[-1])]
        if len(ix) > 1:
            ax.plot(ix.index, cfg["capital"] * ix / ix.iloc[0] / 10000, label="日経平均を持っていた場合", color="#888", lw=1.2)
    ax.axhline(cfg["capital"] / 10000, color="#c00", lw=0.8, ls="--")
    ax.set_ylabel("資産（万円）")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def page(title, note, s, img_name, trades, positions=None, orders=None, log=None):
    esc = html.escape
    rows = "".join(f"<tr><th>{esc(k)}</th><td>{fmt(k, v)}</td></tr>" for k, v in s.items())
    tr = "".join(
        f"<tr><td>{t['exit_date']}</td><td>{esc(t['name'])}</td><td>{t['shares']}</td><td>{t['entry']:,.0f}</td>"
        f"<td>{t['exit']:,.0f}</td><td class='{'p' if t['pnl'] > 0 else 'm'}'>{t['pnl']:+,}</td><td>{esc(t['reason'])}</td></tr>"
        for t in reversed(trades[-200:]))
    pos = ""
    if positions is not None:
        pr = "".join(
            f"<tr><td>{esc(p['name'])}</td><td>{p['shares']}</td><td>{p['entry']:,.0f}</td><td>{p['last']:,.0f}</td>"
            f"<td class='{'p' if p['last'] >= p['cost_ps'] else 'm'}'>{(p['last'] - p['cost_ps']) * p['shares']:+,.0f}</td>"
            f"<td>{p['stop']:,.0f}</td><td>{'済' if p['partial'] else p['target']:{'' if p['partial'] else ',.0f'}}</td></tr>"
            for p in positions.values()) or "<tr><td colspan=7>今は株を持っていません</td></tr>"
        pos = ("<h2>今持っている株</h2><table><tr><th>銘柄</th><th>株数</th><th>買値</th><th>今の値段</th><th>含み損益</th>"
               f"<th>損切りライン</th><th>利確ライン</th></tr>{pr}</table>")
    od = ""
    if orders is not None:
        orr = "".join(f"<li>{'買い' if o['side'] == 'buy' else '売り'} {o['code']} {o['shares']}株 — {esc(o['reason'])}</li>"
                      for o in orders) or "<li>なし</li>"
        od = f"<h2>次の営業日の寄り付きで出す注文</h2><ul>{orr}</ul>"
    lg = ""
    if log:
        lg = "<h2>最近の動き</h2><pre>" + esc("\n".join(log[-60:][::-1])) + "</pre>"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>
body{{font-family:'Yu Gothic','Meiryo',sans-serif;max-width:980px;margin:20px auto;padding:0 16px;color:#222;background:#fff}}
table{{border-collapse:collapse;margin:8px 0 20px;width:100%}}th,td{{border:1px solid #ddd;padding:5px 8px;text-align:left;font-size:14px}}
th{{background:#f4f4f4}}.p{{color:#1a7f37;font-weight:bold}}.m{{color:#c00;font-weight:bold}}
.note{{background:#fff8e1;border:1px solid #f0d58c;padding:10px;border-radius:6px;font-size:14px}}pre{{background:#f7f7f7;padding:10px;font-size:13px;white-space:pre-wrap}}
img{{max-width:100%}}</style></head><body><h1>{esc(title)}</h1><div class="note">{note}</div>
<h2>成績</h2><table>{rows}</table><img src="{img_name}">{pos}{od}
<h2>売買の記録（新しい順）</h2><table><tr><th>売った日</th><th>銘柄</th><th>株数</th><th>買値</th><th>売値</th><th>損益(円)</th><th>理由</th></tr>{tr}</table>
{lg}</body></html>"""
