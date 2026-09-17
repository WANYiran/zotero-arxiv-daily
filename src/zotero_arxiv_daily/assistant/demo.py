"""Offline demonstration, using clearly fictional paper fixtures."""

import argparse
from datetime import datetime
import tempfile
from zoneinfo import ZoneInfo

from .core import Assistant, render_digest
from .store import Store


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="paper-assistant-demo-") as directory:
        store = Store(directory + "/demo.sqlite3")
        papers = [{"title": f"[模拟论文 {i}] Human-AI interaction study",
                   "abstract": "DEMO ONLY. Fictional data to test paper selection; not a real research finding.",
                   "url": f"https://example.org/demo-paper-{i}"} for i in range(1, 6)]
        digest = store.put_digest(str(datetime.now(ZoneInfo("Asia/Shanghai")).date()), papers)
        print("演示数据：以下论文是虚构测试资料，不是实际推荐。\n" + render_digest(digest))
        assistant = Assistant(store)
        questions = iter(["第3篇", "它的链接", "关注 AI Agent", "偏好", "第99篇"])
        turn = 0
        while True:
            if args.interactive:
                text = input("\n你（输入 exit 结束）> ")
                if text == "exit":
                    break
            else:
                text = next(questions, None)
                if text is None:
                    break
                print("\n你 > " + text)
            turn += 1
            print("助手 > " + assistant.chat(str(turn), text))


if __name__ == "__main__":
    run()
