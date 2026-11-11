"""売買の記録（トレード日誌）を分析して、「何を足せば良くなるか」を考える"""
from collections import defaultdict

import pandas as pd

WEEK = "月火水木金土日"


def analyze(trades, cfg, stats=None):
    """(分析の表たち, 気づき・改善案のリスト) を返す"""
    if not trades:
        return {}, ["まだ売買の記録がありません。記録がたまると分析できます。"]
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["hold"] = (df["exit_date"] - df["entry_date"]).dt.days
    df["曜日"] = df["entry_date"].dt.weekday.map(lambda i: WEEK[i])

    def summary(g):
        return pd.Series({"回数": len(g), "勝率": (g["pnl"] > 0).mean(), "合計損益": g["pnl"].sum(), "平均損益": g["pnl"].mean()})

    tables = {
        "売った理由ごと": df.groupby("reason").apply(summary, include_groups=False).sort_values("合計損益"),
        "銘柄ごと": df.groupby("name").apply(summary, include_groups=False).sort_values("合計損益"),
        "買った曜日ごと": df.groupby("曜日").apply(summary, include_groups=False).reindex([w for w in WEEK[:5] if w in set(df["曜日"])]),
    }

    tips = []
    n = len(df)
    wins, losses = df[df.pnl > 0], df[df.pnl <= 0]
    avg_w = wins.pnl.mean() if len(wins) else 0
    avg_l = -losses.pnl.mean() if len(losses) else 0
    small = n < 30
    if small:
        tips.append(f"⚠ まだ取引が{n}回しかないので、ここから言えることは「傾向」程度です（30回以上たまると信頼度が上がります）。")

    # ④ リスク管理のチェック
    cap = cfg["capital"]
    big = df[df.pnl < -cap * 0.02]
    if len(big):
        tips.append(f"【リスク管理】1回で資金の2%を超えて負けた取引が{len(big)}回あります（例：{big.iloc[0]['name']} {big.iloc[0]['pnl']:+,}円）。"
                    "寄り付きで大きく下に窓を開けた可能性があります。決算発表の前は買わない、などのルール追加を検討。")
    else:
        tips.append("【リスク管理】1回の負けは、すべて資金の2%以内に収まっています。")
    if avg_l > 0:
        rr = avg_w / avg_l
        if rr < 1.5:
            tips.append(f"【損小利大】平均の勝ち÷平均の負けが{rr:.2f}倍しかありません。利確ラインを遠くする（リスクリワード1:3など）か、"
                        "負けをもっと早く切ることを検討。")
        else:
            tips.append(f"【損小利大】平均の勝ちは平均の負けの{rr:.2f}倍。損小利大になっています。")

    # 手数料
    gross = df.pnl.sum()
    fees = stats.get("払った手数料等") if stats else None
    if fees and gross > 0 and fees > gross * 0.3:
        tips.append(f"【手数料】手数料等（{fees:,.0f}円）が利益の3割を超えています。売買の回数を減らす"
                    "（買う条件を厳しくする・手数料負けの基準を上げる）か、手数料の安い証券会社を検討。")

    # 売った理由
    by_r = tables["売った理由ごと"]
    sl = by_r.loc["損切り", "回数"] if "損切り" in by_r.index else 0
    if sl / n > 0.5:
        tips.append(f"【買うタイミング】取引の{sl / n:.0%}が損切りで終わっています。買う条件をもう少し厳しくする"
                    "（相場全体が上向きのときだけ・出来高の条件を上げる）と、無駄な負けが減る可能性があります。")
    for r, row in by_r.iterrows():
        if "上がらない" in r and row["合計損益"] < 0:
            tips.append("【保有期間】「長く持っても上がらない」で売った取引がマイナスです。持つ期間の上限を短くすると改善するかもしれません。")
        if "トレンド" in r and row["合計損益"] < 0 and row["回数"] >= 5:
            tips.append("【売るタイミング】「上昇トレンドが崩れた」での売りが合計マイナスです。75日線より早い合図（25日線割れ）で売る案も試す価値あり。")

    # 銘柄
    bad = tables["銘柄ごと"]
    bad = bad[(bad["回数"] >= 3) & (bad["合計損益"] < 0) & (bad["勝率"] < 0.4)]
    if len(bad):
        tips.append("【銘柄】何度やっても負けやすい銘柄：" + "、".join(f"{k}（{int(v['回数'])}回・{v['合計損益']:+,.0f}円）" for k, v in bad.head(3).iterrows())
                    + "。この作戦と相性が悪い可能性があるので、対象から外すことを検討。")

    # 曜日
    wk = tables["買った曜日ごと"]
    if not small and len(wk) >= 3:
        worst = wk["平均損益"].idxmin()
        best = wk["平均損益"].idxmax()
        if wk.loc[worst, "平均損益"] < 0 < wk.loc[best, "平均損益"] and wk.loc[worst, "回数"] >= 10:
            tips.append(f"【曜日】{worst}曜日に買った取引の成績が悪く（平均{wk.loc[worst, '平均損益']:+,.0f}円）、{best}曜日が良い（平均{wk.loc[best, '平均損益']:+,.0f}円）。"
                        "偶然の可能性も高いので、もっと記録がたまってから判断。")

    # 市場との比較
    if stats and stats.get("比較：日経平均を買って持っていた場合") is not None:
        mine, idx = stats["利回り（税引前）"], stats["比較：日経平均を買って持っていた場合"]
        if mine < idx:
            tips.append(f"【市場との比較】同じ期間、日経平均を持っているだけの方が良い成績でした（この作戦{mine:+.0%} / 日経平均{idx:+.0%}）。"
                        "上昇相場では、売買しすぎ・持ち株が少なすぎると市場に負けます。資金の使い方（同時に持つ数・1銘柄の上限）を見直す余地あり。")
        else:
            tips.append(f"【市場との比較】日経平均を持っているだけ（{idx:+.0%}）より良い成績です（この作戦{mine:+.0%}）。")
    return tables, tips


def tables_html(tables):
    out = []
    for title, t in tables.items():
        if t is None or t.empty:
            continue
        rows = "".join(
            f"<tr><td>{k}</td><td>{int(v['回数'])}回</td><td>{v['勝率'] * 100:.0f}%</td>"
            f"<td class='{'p' if v['合計損益'] > 0 else 'm'}'>{v['合計損益']:+,.0f}円</td><td>{v['平均損益']:+,.0f}円</td></tr>"
            for k, v in t.iterrows())
        out.append(f"<h3>{title}</h3><table><tr><th></th><th>回数</th><th>勝率</th><th>合計損益</th><th>平均損益</th></tr>{rows}</table>")
    return "".join(out)
