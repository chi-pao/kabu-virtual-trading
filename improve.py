"""いくつかの作戦を過去データで比べて、どれが良いかと、改善のヒントを出す

使い方:  py improve.py
結果は 結果\改善レポート.html に出ます。設定(config.json)は自動では変えません。
"""
import copy
import html

import pandas as pd

from backtest import OUT, load_cfg, load_market, simulate
from kabu import report
from kabu.analyze import analyze, tables_html

# 試す作戦の一覧（「今の作戦」からの変更点だけを書く）
VARIANTS = [
    ("今の作戦（高値更新を買う）", "上昇トレンド中に20日高値を更新した株を買う", {}),
    ("①割安・高ROE・増収の会社だけ", "PER15倍以下・ROE10%以上・売上の伸び10%以上",
     {"fundamental": {"roe_min": 0.10, "per_max": 15, "revenue_growth_min": 0.10}}),
    ("③RSI＋MACDで反発を買う", "RSI30以下のあとMACDがゴールデンクロスで買い、RSI70以上で売り",
     {"entry": "rsi_macd", "use_regime": False}),
    ("④1回2%リスク・利確3倍", "1回の損失上限を資金の2%、利確を損切り幅の3倍に",
     {"risk_per_trade": 0.02, "target_r": 3.0}),
    ("③＋④", "RSI＋MACDの買い方に、④のリスク管理を組み合わせ",
     {"entry": "rsi_macd", "use_regime": False, "risk_per_trade": 0.02, "target_r": 3.0}),
    ("持ち株を増やす", "同時に持つ数を6→8、1銘柄の上限を20%→25%に",
     {"max_positions": 8, "max_position_pct": 0.25}),
    ("利益を長く伸ばす", "利益を守る売りのラインを広げる（ATR3倍→4倍）",
     {"trail_atr": 4.0}),
    ("持ち株を増やす＋利益を長く伸ばす", "上の2つを組み合わせ",
     {"max_positions": 8, "max_position_pct": 0.25, "trail_atr": 4.0}),
    ("井村・五月流：成長と収益力を優先", "候補が複数あるとき、利益・売上の伸びとROEが高い会社を優先",
     {"quality_weight": 1.0, "quality_parts": {"roe": 1.0, "growth": 1.0}}),
    ("清原流：お金持ちで割安な会社を優先", "候補が複数あるとき、ネットキャッシュ比率が高い会社を優先",
     {"quality_weight": 1.0, "quality_parts": {"netcash": 1.0}}),
    ("4人の視点で買う量を調整", "割高（PER30倍超・PBR5倍超）や上がりすぎ（25日平均+15%超）は半分だけ買う",
     {"use_views": True}),
    ("複合：全員の考え＋持ち株増＋利益を伸ばす", "成長・収益力・ネットキャッシュで優先順位、割高は半分、持ち株8・ATR4倍",
     {"quality_weight": 1.0, "quality_parts": {"roe": 1.0, "growth": 1.0, "netcash": 0.5}, "use_views": True,
      "max_positions": 8, "max_position_pct": 0.25, "trail_atr": 4.0}),
    ("複合＋①＋④", "上の複合に、①の会社選び（PER15倍以下・ROE10%以上・増収10%以上）と④のリスク管理（2%・利確3倍）",
     {"quality_weight": 1.0, "quality_parts": {"roe": 1.0, "growth": 1.0, "netcash": 0.5}, "use_views": True,
      "max_positions": 8, "max_position_pct": 0.25, "trail_atr": 4.0,
      "fundamental": {"roe_min": 0.10, "per_max": 15, "revenue_growth_min": 0.10},
      "risk_per_trade": 0.02, "target_r": 3.0}),
    ("①＋④", "①の会社選びと④のリスク管理だけ",
     {"fundamental": {"roe_min": 0.10, "per_max": 15, "revenue_growth_min": 0.10},
      "risk_per_trade": 0.02, "target_r": 3.0}),
]


def merge(base, diff):
    cfg = copy.deepcopy(base)
    for k, v in diff.items():
        if isinstance(v, dict):
            cfg.setdefault(k, {}).update(v)
        else:
            cfg[k] = v
    return cfg


def halves(eq):
    """前半と後半に分けた利回り（どちらでも良いなら、たまたまではない可能性が高い）"""
    mid = eq.index[0] + (eq.index[-1] - eq.index[0]) / 2
    a, b = eq[eq.index <= mid], eq[eq.index >= mid]
    return a.iloc[-1] / a.iloc[0] - 1, b.iloc[-1] / b.iloc[0] - 1


def main():
    base = load_cfg()
    mk = load_market(base)
    rows, base_result = [], None
    for name, desc, diff in VARIANTS:
        cfg = merge(base, diff)
        eng, s, eq = simulate(cfg, mk)
        h1, h2 = halves(eq)
        score = (s["年あたり利回り"] or 0) + s["最大の落ち込み"] * 0.5  # 落ち込みが大きいほど減点
        rows.append({"name": name, "desc": desc, "s": s, "h1": h1, "h2": h2, "score": score})
        if not diff:
            base_result = (eng, s, cfg)
        print(f"  {name}: 利回り{s['利回り（税引前）']:+.1%} / 最大落ち込み{s['最大の落ち込み']:.1%} / {s['取引回数']}回")

    best = max(rows, key=lambda r: r["score"])
    eng, s, cfg = base_result
    tables, tips = analyze(eng.s["trades"], cfg, s)
    idx = s.get("比較：日経平均を買って持っていた場合")

    esc = html.escape
    tr = "".join(
        f"<tr{' style=background:#eaf7ea' if r is best else ''}><td><b>{esc(r['name'])}</b><br><small>{esc(r['desc'])}</small></td>"
        f"<td>{r['s']['利回り（税引前）']:+.1%}</td><td>{r['s']['損益（税引後の目安）']:+,.0f}円</td>"
        f"<td>{r['s']['最大の落ち込み']:.1%}</td><td>{r['s']['取引回数']}回</td>"
        f"<td>{(r['s']['勝率'] or 0):.0%}</td><td>{r['h1']:+.0%} / {r['h2']:+.0%}</td></tr>"
        for r in sorted(rows, key=lambda r: -r["score"]))
    same = "今の作戦のままがいちばん良い結果でした。" if best["name"] == VARIANTS[0][0] else \
        f"いちばんバランスが良かったのは「{esc(best['name'])}」です。前半・後半の両方でプラスなら、採用を検討する価値があります。"
    page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>改善レポート</title><style>
body{{font-family:'Yu Gothic','Meiryo',sans-serif;max-width:1000px;margin:20px auto;padding:0 16px;color:#222;background:#fff}}
table{{border-collapse:collapse;width:100%;margin:8px 0 20px}}th,td{{border:1px solid #ddd;padding:6px 8px;font-size:14px;text-align:left}}
th{{background:#f4f4f4}}.p{{color:#1a7f37;font-weight:bold}}.m{{color:#c00;font-weight:bold}}
.note{{background:#fff8e1;border:1px solid #f0d58c;padding:10px;border-radius:6px;font-size:14px}}li{{margin:6px 0}}</style></head><body>
<h1>改善レポート</h1>
<div class="note">過去{base['backtest_years']}年のデータで、作戦を入れ替えて比べました（手数料・約定のずれ込み）。
同じ期間に日経平均を持っていただけなら <b>{idx:+.0%}</b> でした。<br>
「点数」は、年あたりの利回りから、資産の最大の落ち込みの半分を引いたもの（大きく減る作戦は減点）。過去にうまくいった作戦が、この先もうまくいくとは限りません。</div>
<h2>作戦の比べっこ（上ほど良い）</h2>
<table><tr><th>作戦</th><th>利回り</th><th>税引後の損益</th><th>最大の落ち込み</th><th>取引回数</th><th>勝率</th><th>前半 / 後半</th></tr>{tr}</table>
<p><b>{same}</b> 設定は自動では変えていません。</p>
<h2>今の作戦の、売買記録からの気づき・改善案</h2><ul>{''.join(f'<li>{esc(t)}</li>' for t in tips)}</ul>
{tables_html(tables)}
</body></html>"""
    (OUT / "改善レポート.html").write_text(page, encoding="utf-8")
    print(f"\n結果: {OUT / '改善レポート.html'}")


if __name__ == "__main__":
    main()
