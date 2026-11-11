"""有名投資家4人の「公開されている考え方」を、無料で取れる数字に置き換えて評価する。

※ 本人たちの売買を再現するものではなく、インタビュー・著書で語られている考え方を参考にした簡易版。
  テスタ   … 損切りは機械的に・負けを前提にトータルで勝つ・地合い（相場全体）を重視
  井村俊哉 … オニール流の成長株：利益と売上の伸び・高い利益率（ROE）・新高値
  片山晃(五月) … 小型成長株の初動に集中。高すぎる株価（期待の織り込み済み）は追わない
  清原達郎 … 割安株：ネットキャッシュ比率（現金−借金 ÷ 時価総額）・低PER
最後に「どれくらいの量を買うか」を決める：
  期待が織り込まれた割高株や、すでに大きく上がった株は、フルではなく小さめ（半分）にする。
"""
from .data import net_cash_ratio


def evaluate(f, r, cfg, regime_ok):
    """f=企業データ, r=その日の株価・指標。 (視点ごとのコメント, 買う量の倍率) を返す"""
    f = f or {}
    per = f.get("trailingPE") or f.get("forwardPE")
    pbr = f.get("priceToBook")
    roe = f.get("returnOnEquity")
    rg, eg = f.get("revenueGrowth"), f.get("earningsGrowth")
    nc = net_cash_ratio(f)
    close, sma25 = float(r["Close"]), float(r["sma25"])
    ext = close / sma25 - 1
    v = {}

    # テスタ視点：地合い・勢い・損切りの置きやすさ
    if not regime_ok:
        v["テスタ"] = "✕ 地合いが悪い（日経平均が下向き）。新規は見送り"
    elif ext > cfg.get("extended_pct", 0.15):
        v["テスタ"] = f"△ 勢いはあるが、25日平均より{ext:.0%}上で上がりすぎ。損切りを決めて小さめに"
    else:
        v["テスタ"] = "○ 地合い良好・上昇トレンド。損切りラインを決めて機械的に"

    # 井村視点：成長と収益力
    if (eg or 0) >= 0.15 and (rg or 0) >= 0.08 and (roe or 0) >= 0.12:
        v["井村"] = f"○ 利益{_p(eg)}・売上{_p(rg)}成長、ROE{_p(roe)}。成長株として保有を検討できる"
    elif (eg or 0) < 0 or (rg or 0) < 0:
        v["井村"] = f"△ 利益{_p(eg)}・売上{_p(rg)}。成長が止まっていないか、決算資料で確認したい"
    else:
        v["井村"] = f"− 成長は普通（利益{_p(eg)}・売上{_p(rg)}）"

    # 五月（片山）視点：期待の織り込み度合い
    expensive = (per is not None and per > cfg.get("per_expensive", 30)) or (pbr is not None and pbr > cfg.get("pbr_expensive", 5))
    if expensive:
        v["五月"] = f"△ PER{_n(per)}倍・PBR{_n(pbr)}倍で安くない。期待は織り込み済み。下げた場面を待つのが合理的"
    elif (eg or 0) >= 0.15 and ext <= 0.10:
        v["五月"] = "○ 成長しているのに、まだ上がりきっていない。初動の可能性"
    else:
        v["五月"] = f"− 割高ではない（PER{_n(per)}倍・PBR{_n(pbr)}倍）"

    # 清原視点：割安さ
    if nc >= 0.3 and per is not None and per <= 12:
        v["清原"] = f"○ ネットキャッシュ比率{nc:.0%}・PER{_n(per)}倍。かなり割安"
    elif per is not None and per <= 12:
        v["清原"] = f"○ PER{_n(per)}倍で割安寄り（ネットキャッシュ比率{nc:.0%}）"
    elif expensive:
        v["清原"] = "✕ 指標面で割安性に欠ける。割安度が改善する場面を待つ"
    else:
        v["清原"] = f"− 割安とまでは言えない（PER{_n(per)}倍・ネットキャッシュ比率{nc:.0%}）"

    # 買う量の結論
    size = 1.0
    reasons = []
    if expensive:
        size *= cfg.get("expensive_size", 0.5)
        reasons.append("割高")
    if ext > cfg.get("extended_pct", 0.15):
        size *= cfg.get("extended_size", 0.5)
        reasons.append("上がりすぎ")
    v["結論"] = "フルで買ってよい" if size >= 1 else f"買うなら小さめ（通常の{size:.0%}・理由：{'・'.join(reasons)}）"
    return v, size


def _p(x):
    return "-" if x is None else f"{x * 100:+.0f}%"


def _n(x):
    return "-" if x is None else f"{x:.1f}"
