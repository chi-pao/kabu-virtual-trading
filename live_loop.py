"""取引時間中、約10分ごとに公開ページを更新し続ける（GitHub が朝に1回起動する）

GitHub の予約実行は30分おきなどにすると遅れたり飛んだりしやすいので、
1回起動したら取引終了（15:35）まで自分でくり返す方式にしている。
GitHub の1回の実行は最長6時間なので、5時間40分で一度終わり、次の起動に引き継ぐ。
"""
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_site
import dashboard as dash

JST = timezone(timedelta(hours=9))
EVERY = 600            # 10分ごと
MAX_RUN = 5 * 3600 + 40 * 60


def hm(t):
    return t.hour * 60 + t.minute


def push():
    subprocess.run(["git", "add", "docs"], check=False)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-q", "-m", f"取引時間中の更新 {datetime.now(JST):%m/%d %H:%M}"], check=False)
    for _ in range(3):
        if subprocess.run(["git", "pull", "-q", "--rebase", "-X", "ours"]).returncode == 0 and \
                subprocess.run(["git", "push", "-q"]).returncode == 0:
            return
        time.sleep(5)


def main():
    start = time.time()
    while True:
        now = datetime.now(JST)
        if now.weekday() >= 5 or hm(now) >= 15 * 60 + 35 or time.time() - start > MAX_RUN:
            print("終了", now.strftime("%H:%M"))
            return
        if hm(now) < 9 * 60 + 5:            # 9時すぎまで待つ
            time.sleep(60)
            continue
        if 11 * 60 + 50 <= hm(now) < 12 * 60 + 40:   # 昼休み（20分遅れのデータなので少しずらす）
            time.sleep(120)
            continue
        t0 = time.time()
        try:
            dash._cache.clear()
            build_site.main()
            push()
            print("更新", now.strftime("%H:%M"), f"{time.time() - t0:.0f}秒")
        except Exception as e:
            print("更新に失敗", now.strftime("%H:%M"), e)
        time.sleep(max(60, EVERY - (time.time() - t0)))


if __name__ == "__main__":
    main()
