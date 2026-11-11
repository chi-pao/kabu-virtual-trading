"""公開ページの「銘柄を調べる」用のデータを作る（毎日の取引終了後に GitHub が実行）

ネット公開版はその場で計算できないので、上場している全銘柄（約3,700社）をあらかじめ計算しておく。
データは量が多いので、ふだんの保存場所（main）ではなく、毎日上書きする別の場所
（lookup-data という枝）に置く。こうすると、保存の履歴がふくらまない。

使い方: py build_lookup.py            … 全銘柄を作り直して公開
        py build_lookup.py --local    … 作るだけ（公開しない。_lookup フォルダに出る）
"""
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kabu import data, lookup, news

ROOT = Path(__file__).parent
OUT = ROOT / "_lookup"
BRANCH = "lookup-data"
FETCH_PER_DAY = int(os.environ.get("FETCH_PER_DAY", "900"))   # 企業データを1日に取り直す数（1週間で全銘柄が一回り）


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, default=float, separators=(",", ":")), encoding="utf-8")


if not shutil.which("git") and Path(r"C:\Program Files\Git\cmd\git.exe").exists():   # このパソコンで動かすとき
    os.environ["PATH"] += os.pathsep + r"C:\Program Files\Git\cmd"


def git(*args, cwd=OUT, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check)


def remote_url():
    tok, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if tok and repo:
        return f"https://x-access-token:{tok}@github.com/{repo}.git"
    return subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def publish():
    """_lookup フォルダの中身を、履歴なし（1回分だけ）で lookup-data に上書きする"""
    shutil.rmtree(OUT / ".git", ignore_errors=True)
    git("init", "-q")
    git("checkout", "-q", "-b", BRANCH)
    git("config", "user.name", "kabu-bot")
    git("config", "user.email", "kabu-bot@users.noreply.github.com")
    git("add", "-A")
    git("commit", "-q", "-m", f"銘柄データ {time.strftime('%Y-%m-%d %H:%M')}")
    git("push", "-q", "-f", remote_url(), f"{BRANCH}:{BRANCH}")
    shutil.rmtree(OUT / ".git", ignore_errors=True)


def load_prices_chunked(codes, size=400):
    out = {}
    for i in range(0, len(codes), size):
        part = codes[i:i + size]
        for _ in range(3):
            try:
                out.update(data.load_prices(part, period="1y"))
                break
            except Exception as e:
                print("  株価の取得をやり直します:", e)
                time.sleep(10)
        print(f"  株価 {min(i + size, len(codes))}/{len(codes)}")
    return out


def already_today():
    """今日の取引終了後（15:30以降）に作ったデータがもう公開されていれば True（予備の実行で二度作らない）"""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return False
    try:
        import urllib.request
        with urllib.request.urlopen(f"https://raw.githubusercontent.com/{repo}/{BRANCH}/index.json", timeout=30) as r:
            up = json.loads(r.read().decode("utf-8")).get("updated", "")
        return up >= time.strftime("%Y-%m-%d") + " 15:30"
    except Exception:
        return False


def ranking(results, cfg, held):
    """全銘柄の中から、売買が活発な銘柄だけで「おすすめ」を作り、自動売買の対象リストも作る。
    売買代金が少ない銘柄は、思った値段で売り買いできないことが多いので除く"""
    # 1回に買う金額が、その株の1日の売買代金の1%以下になる株だけ（自分の注文で値段を動かさない）
    per_buy = cfg["capital"] * cfg["max_position_pct"]
    mt = max(cfg.get("min_turnover_floor", 1e7), per_buy * cfg.get("liquidity_mult", 100))
    lot = cfg.get("lot", 100)
    for o in results.values():
        o["min_buy"] = o["price"] * lot
    ok = [o for o in results.values() if o.get("turnover", 0) >= mt and "short" in o]
    top = lambda key, n=12: sorted([o for o in ok if key in o], key=lambda o: -o[key]["score"])[:n]
    base = lambda o: {"code": o["code"], "name": o["name"], "price": o["price"], "chg": o["chg"], "held": o["code"] in held,
                      "min_buy": o["min_buy"], "affordable": o["min_buy"] <= per_buy}
    # 「今すぐ」は実際に買える株だけ（最低購入金額が1銘柄の上限以下）
    now = [{**base(o), "score": o["short"]["score"],
            "checks": [{"label": r["label"], "ok": r["ok"], "note": r["note"]} for r in o["short"]["reasons"]],
            "trigger": o["short"]["trigger"], "plan_stop": o["short"]["plan_stop"],
            "plan_target": o["short"]["plan_target"], "next": o["short"]["next"]}
           for o in top("short", 60) if o["code"] not in held and o["min_buy"] <= per_buy][:12]
    out = {"updated": time.strftime("%Y-%m-%d %H:%M"), "min_turnover": mt, "count": len(ok),
           "per_buy": per_buy, "lot": lot,
           "tradable": sorted([o["code"], o["name"]] for o in ok),
           "now": now,
           "mid": [{**base(o), "score": o["mid"]["score"], "reasons": o["mid"]["reasons"]} for o in top("mid")],
           "long": [{**base(o), "score": o["long"]["score"], "reasons": o["long"]["reasons"]} for o in top("long")]}
    (ROOT / "結果" / "全銘柄ランキング.json").write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print(f"売買の対象: {len(ok)}社（1日の売買代金 {mt / 1e4:,.0f}万円以上・1回の購入 最大{per_buy / 1e4:,.0f}万円）")


def main(local=False):
    if not local and "--force" not in sys.argv and already_today():
        print("今日の分はもう作ってあるので、とばします")
        return
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    for p in OUT.glob("*.json"):
        p.unlink()
    lst = lookup.master(ROOT / "結果" / "銘柄一覧.json")
    known = {x["code"]: x for x in lst}
    names = dict(cfg["universe"])
    codes = list(dict.fromkeys(list(names) + [x["code"] for x in lst]))
    print(f"{len(codes)}銘柄を計算します…")
    prices = load_prices_chunked(codes)
    ix = data.load_prices([data.INDEX], period="1y")[data.INDEX]
    regime = bool(ix["Close"].iloc[-1] > ix["Close"].rolling(75).mean().iloc[-1])
    fund = data.load_fundamentals(codes, ROOT / "結果" / "企業データ.json", max_fetch=FETCH_PER_DAY)
    st_path = ROOT / "仮想売買" / "state.json"
    held = json.loads(st_path.read_text(encoding="utf-8"))["engine"]["positions"] if st_path.exists() else {}

    def one(code):
        d = prices.get(code)
        if d is None or len(d) < 30:
            return None
        meta = {**known.get(code, {}), **({"name": names[code]} if code in names else {})}
        try:
            o = lookup.analyze(code, data.add_indicators(d, cfg["breakout_days"]), fund.get(code), cfg, regime,
                               meta, held=code in held)
        except Exception as e:
            print(f"  {code}: 計算できませんでした ({e})")
            return None
        if not fund.get(code):
            o["note"] = "PER・ROEなどの企業データはまだ取得待ちです（数日のうちに入ります）。今は株価の動きだけで採点しています"
        if code in names:   # ニュースは時間がかかるので、監視中の銘柄だけ（他はリンクで探す）
            o["news"] = [{"title": n["title"], "link": n["link"], "time": n["time"], "tone": news.tone(n["title"])}
                         for n in news.headlines_full(f"{o['name']} 株", days=7, limit=8)]
        dump(OUT / f"{code}.json", o)
        return code, o

    with ThreadPoolExecutor(8) as ex:
        results = dict(r for r in ex.map(one, codes) if r)
    done = set(results)
    ranking(results, cfg, held)
    dump(OUT / "index.json", {"updated": time.strftime("%Y-%m-%d %H:%M"),
                              "list": [[x["code"], x["name"], x["sector"], 1 if x["code"] in done else 0] for x in lst]})
    print(f"完成: {len(done)}銘柄 / 一覧 {len(lst)}銘柄")
    if not local:
        publish()
        print("公開しました（lookup-data）")


if __name__ == "__main__":
    main(local="--local" in sys.argv)
