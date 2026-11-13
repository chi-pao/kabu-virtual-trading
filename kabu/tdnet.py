"""東証の適時開示（TDnet 閲覧サービス）から、その日の決算と業績予想の修正を読む。

個人で使う範囲で、1日1回だけ、少しずつ間をあけて読む。書類そのものは保存・公開せず、
営業利益などの数字だけを取り出して使う。
"""
import io
import re
import time
import urllib.request
import zipfile

BASE = "https://www.release.tdnet.info/inbs/"
UA = {"User-Agent": "Mozilla/5.0 (personal research; kabu-virtual-trading)"}


def _get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(5)
        except Exception:
            time.sleep(5)
    return None


def day_list(day):
    """その日に出た開示の一覧 [{time, code, name, title, xbrl}]（day は 'YYYYMMDD'）"""
    out = []
    for page in range(1, 60):
        b = _get(f"{BASE}I_list_{page:03d}_{day}.html")
        if not b:
            break
        h = b.decode("utf-8", "ignore")
        rows = re.findall(r'kjTime"[^>]*>([^<]*)</td>.*?kjCode"[^>]*>([^<]*)</td>.*?kjName"[^>]*>(.*?)</td>'
                          r'.*?kjTitle"[^>]*>(.*?)</td>.*?kjXbrl"[^>]*>(.*?)</td>', h, re.S)
        if not rows:
            break
        for tm, code, name, title, xb in rows:
            z = re.search(r'href="([^"]+\.zip)"', xb)
            out.append({"time": tm.strip(), "code": code.strip()[:4], "name": re.sub(r"<[^>]+>|\s", "", name),
                        "title": re.sub(r"<[^>]+>", "", title).strip(), "xbrl": BASE + z.group(1) if z else None})
        time.sleep(0.5)
    return out


def _num(txt, attrs):
    t = re.sub(r"<[^>]+>", "", txt).replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    v = float(t)
    sc = re.search(r'scale="(-?\d+)"', attrs)
    if sc:
        v *= 10 ** int(sc.group(1))
    if re.search(r'sign="-"', attrs):
        v = -v
    return v


def facts(zip_url):
    """XBRL の「サマリー」から {(項目名, 文脈): 数字} と書類名を取り出す"""
    b = _get(zip_url)
    if not b:
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
    except zipfile.BadZipFile:
        return None
    out, doc = {}, ""
    for n in z.namelist():
        if "Attachment" in n or not n.endswith("ixbrl.htm"):   # サマリー（と予想修正の本体）だけ読む
            continue
        s = z.read(n).decode("utf-8", "ignore")
        s = re.sub(r"<ix:(nonFraction|nonNumeric)[^>]*/>", "", s)   # 空欄（xsi:nil）の項目は除く
        for m in re.finditer(r"<ix:(nonFraction|nonNumeric)([^>]*)>(.*?)</ix:\1>", s, re.S):
            a = m.group(2)
            nm = re.search(r'name="([^"]+)"', a)
            cx = re.search(r'contextRef="([^"]+)"', a)
            if not nm or not cx:
                continue
            nm = nm.group(1).split(":")[-1]
            if nm == "DocumentName":
                doc = re.sub(r"<[^>]+>", "", m.group(3)).strip()
            elif m.group(1) == "nonFraction":
                v = _num(m.group(3), a)
                if v is not None:
                    out[(nm, cx.group(1))] = v
    return {"doc": doc, "facts": out}


def _pick(f, ctx_pred):
    """営業利益（無ければ経常利益）を、文脈の条件に合うものから取る。幅のある予想は真ん中"""
    for item in ("OperatingIncome", "OperatingIncomeIFRS", "OperatingIncomeUS", "OrdinaryIncome"):
        vals = {c: v for (n, c), v in f.items() if n == item and ctx_pred(c)}
        if not vals:
            continue
        cons = {c: v for c, v in vals.items() if "NonConsolidated" not in c} or vals
        plain = [v for c, v in cons.items() if "Lower" not in c and "Upper" not in c]
        if plain:
            return plain[0]
        both = [v for c, v in cons.items() if "Lower" in c][:1] + [v for c, v in cons.items() if "Upper" in c][:1]
        return sum(both) / len(both)
    return None


def summarize(x):
    """決算短信・業績予想の修正から、作戦に使う数字を作る
    kind: 'q'（四半期）, 'fy'（本決算）, 'rev'（予想の修正）"""
    f, doc = x["facts"], x["doc"]
    ctxs = {c for _, c in f}
    if any("_PreviousMember" in c for c in ctxs):   # 業績予想の修正（修正前と修正後が並んでいる）
        year = lambda c: c.startswith("CurrentYear") and "Quarter" not in c   # 通期の予想（中間期の予想は除く）
        prev = _pick(f, lambda c: year(c) and "_PreviousMember" in c)
        new = _pick(f, lambda c: year(c) and "_CurrentMember" in c and "Result" not in c)
        return {"kind": "rev", "prev": prev, "new": new}
    for q in (1, 2, 3):
        if any(c.startswith(f"CurrentAccumulatedQ{q}Duration") for c in ctxs):
            act = _pick(f, lambda c: c.startswith(f"CurrentAccumulatedQ{q}Duration") and "Result" in c)
            fc = _pick(f, lambda c: c.startswith("CurrentYearDuration") and "Forecast" in c)
            return {"kind": "q", "q": q, "act": act, "fc": fc}
    act = _pick(f, lambda c: c.startswith("CurrentYearDuration") and "Result" in c)
    nxt = _pick(f, lambda c: c.startswith("NextYearDuration") and "Forecast" in c)
    if act is not None or nxt is not None:
        return {"kind": "fy", "act": act, "next_fc": nxt}
    return None
