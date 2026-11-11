"""公開ページの「銘柄を調べる」用のデータを作る（毎日の取引終了後に GitHub が実行）

ネット公開版はその場で計算できないので、あらかじめ計算しておく：
  ・TOPIX500（大型〜中型の約500社）＋ 監視中の銘柄 ＋ 気になる銘柄.json に入っている銘柄
  ・それ以外の銘柄は、パソコンの画面（画面を開く.bat）ならその場で調べられる
使い方: py build_lookup.py            … 全部作り直す
        py build_lookup.py 7203 6758  … その銘柄を「気になる銘柄」に追加して作る
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kabu import data, lookup, news

ROOT = Path(__file__).parent
OUT = ROOT / "docs" / "data" / "lookup"
FAV = ROOT / "気になる銘柄.json"


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, default=float, separators=(",", ":")), encoding="utf-8")


def main(add=()):
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    lst = lookup.master(ROOT / "結果" / "銘柄一覧.json")
    known = {x["code"]: x for x in lst}
    fav = json.loads(FAV.read_text(encoding="utf-8")) if FAV.exists() else []
    for c in add:
        c = c.strip().upper()
        if re.fullmatch(r"[0-9][0-9A-Z]{3}", c) and c in known and c not in fav:
            fav.append(c)
    FAV.write_text(json.dumps(fav, ensure_ascii=False, indent=1), encoding="utf-8")

    names = dict(cfg["universe"])
    if add:   # 追加だけのときは、その銘柄だけ作る
        codes = [c.strip().upper() for c in add if c.strip().upper() in known]
    else:
        big = [x["code"] for x in lst if x["size"] in ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")]
        codes = list(dict.fromkeys(list(names) + fav + big))
    print(f"{len(codes)}銘柄を計算します…")
    prices = data.load_prices(codes + [data.INDEX], period="1y")
    ix = prices.pop(data.INDEX)
    regime = bool(ix["Close"].iloc[-1] > ix["Close"].rolling(75).mean().iloc[-1])
    fund = data.load_fundamentals(codes, ROOT / "結果" / "企業データ.json")
    st_path = ROOT / "仮想売買" / "state.json"
    held = json.loads(st_path.read_text(encoding="utf-8"))["engine"]["positions"] if st_path.exists() else {}

    # ニュースは件数が多いと時間がかかるので、監視中・気になる銘柄だけ
    news_for = set(names) | set(fav)

    def one(code):
        d = prices.get(code)
        if d is None or len(d) < 30:
            return None
        meta = {**known.get(code, {}), **({"name": names[code]} if code in names else {})}
        o = lookup.analyze(code, data.add_indicators(d, cfg["breakout_days"]), fund.get(code), cfg, regime,
                           meta, held=code in held)
        if code in news_for:
            o["news"] = [{"title": n["title"], "link": n["link"], "time": n["time"], "tone": news.tone(n["title"])}
                         for n in news.headlines_full(f"{o['name']} 株", days=7, limit=8)]
        dump(OUT / f"{code}.json", o)
        return code

    with ThreadPoolExecutor(6) as ex:
        done = [c for c in ex.map(one, codes) if c]
    # 検索用の一覧（全上場銘柄。データがあるものに印）
    have = {p.stem for p in OUT.glob("*.json") if p.stem != "index"}
    dump(OUT / "index.json", {"list": [[x["code"], x["name"], x["sector"], 1 if x["code"] in have else 0] for x in lst],
                              "fav": fav})
    print(f"完成: {len(done)}銘柄（調べられる銘柄の合計 {len(have)}）")


if __name__ == "__main__":
    main(sys.argv[1:])
