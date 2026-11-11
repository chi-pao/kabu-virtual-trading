"""ニュースの見出しから「良い・悪い」を点数にする（無料：Googleニュースの見出しを読むだけ）"""
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser

GOOD = ["上方修正", "増益", "最高益", "過去最高", "自社株買い", "増配", "好調", "急伸", "大幅高", "続伸",
        "受注", "提携", "承認", "黒字転換", "上振れ", "買収で合意", "格上げ"]
BAD = ["下方修正", "減益", "赤字", "減配", "無配", "不祥事", "不正", "リコール", "訴訟", "急落", "大幅安",
       "続落", "業務停止", "行政処分", "粉飾", "延期", "格下げ", "下振れ", "撤退", "特損", "特別損失"]
VERY_BAD = ["粉飾", "業務停止", "上場廃止", "債務超過", "不正会計", "破綻"]

MACRO_QUERIES = ["日経平均", "日銀 金利", "米国株 市場", "為替 円相場", "関税 貿易", "地政学 リスク"]
MACRO_BAD = ["暴落", "急落", "ショック", "危機", "戦争", "侵攻", "景気後退", "リセッション", "金融不安", "大幅安", "パニック"]
MACRO_GOOD = ["最高値", "急反発", "大幅高", "緩和", "回復", "停戦", "合意", "上昇"]


def headlines(query, days=3):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "ja", "gl": "JP", "ceid": "JP:ja"})
    try:
        feed = feedparser.parse(url)
    except Exception:
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for e in feed.entries:
        t = e.get("published_parsed")
        if t and datetime(*t[:6], tzinfo=timezone.utc) < since:
            continue
        out.append(e.get("title", ""))
    time.sleep(0.5)
    return out


def headlines_full(query, days=2, limit=15):
    """画面表示用：見出し・リンク・時刻・発信元つき"""
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "ja", "gl": "JP", "ceid": "JP:ja"})
    try:
        feed = feedparser.parse(url)
    except Exception:
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for e in feed.entries:
        t = e.get("published_parsed")
        when = datetime(*t[:6], tzinfo=timezone.utc) if t else None
        if when and when < since:
            continue
        title = e.get("title", "")
        src = (e.get("source") or {}).get("title", "")
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        out.append({"title": title, "link": e.get("link", ""), "source": src,
                    "time": when.isoformat() if when else "", "q": query})
    return out[:limit]


def tone(title, good=GOOD + MACRO_GOOD, bad=BAD + MACRO_BAD):
    if any(w in title for w in VERY_BAD):
        return -2
    g = sum(w in title for w in good)
    b = sum(w in title for w in bad)
    return (g > b) - (b > g)


def _score(titles, good, bad, very_bad=()):
    s, hits = 0, []
    for t in titles:
        for w in very_bad:
            if w in t:
                s -= 5
                hits.append(f"[重大]{t}")
                break
        else:
            g = sum(w in t for w in good)
            b = sum(w in t for w in bad)
            if g or b:
                s += g - b
                hits.append(("[+]" if g > b else "[-]" if b > g else "[±]") + t)
    return s, hits


def stock_news(name):
    """銘柄のニュース点数。-3以下なら買わない"""
    titles = headlines(f"{name} 株")
    s, hits = _score(titles, GOOD, BAD, VERY_BAD)
    return s, hits[:5], len(titles)


def macro_news():
    """世界情勢・相場全体のニュース点数。大きくマイナスなら新しく買うのを止める"""
    total, hits = 0, []
    for q in MACRO_QUERIES:
        s, h = _score(headlines(q, days=2), MACRO_GOOD, MACRO_BAD)
        total += s
        hits += h[:3]
    return total, hits
