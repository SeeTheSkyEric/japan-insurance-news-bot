# -*- coding: utf-8 -*-
# GitHub Pages push 완료 후 슬랙 발송
import os, json, requests
from datetime import datetime, timezone, timedelta

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
GITHUB_PAGES_URL  = os.environ.get("GITHUB_PAGES_URL", "")
NEWS_CACHE_FILE   = "news_cache.json"  # news_bot.py가 저장한 결과

def main():
    if not SLACK_WEBHOOK_URL:
        print("⏭ SLACK_WEBHOOK_URL 미설정 — 건너뜀")
        return

    JST = timezone(timedelta(hours=9))
    today = datetime.now(JST).strftime("%Y年%m月%d日")

    # news_bot.py가 저장한 결과 파일 읽기
    if not os.path.exists(NEWS_CACHE_FILE):
        print("⚠️ news_cache.json 없음")
        return

    with open(NEWS_CACHE_FILE, encoding="utf-8") as f:
        data = json.load(f)

    news_list = data.get("news", [])
    fetch_date = data.get("fetch_date", today)

    if not news_list:
        requests.post(SLACK_WEBHOOK_URL, json={
            "blocks": [{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🇯🇵 *오늘의 일본 보험 뉴스* — {fetch_date}\n\n⚠️ 오늘은 선별된 보험 뉴스가 없습니다."
                }
            }]
        }, timeout=10)
        return

    # 카테고리별 건수 요약
    cat_labels = {
        "agency":     "🏢 보험대리점",
        "insurtech":  "💡 InsureTech",
        "insurer":    "🏦 보험사",
        "regulation": "⚖️ 규제",
    }
    summary = ""
    for key, label in cat_labels.items():
        items = [n for n in news_list if n["category"] == key]
        if items:
            summary += f"\n{label} ({len(items)}건)"

    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🇯🇵 *오늘의 일본 보험 뉴스* — {fetch_date}{summary}\n\n<{GITHUB_PAGES_URL}|📰 전체 기사 헤드라인 보기>"
                }
            }
        ]
    }
    res = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    if res.status_code == 200:
        print("✅ 슬랙 발송 완료")
    else:
        print(f"⚠️ 슬랙 발송 실패: {res.status_code} {res.text}")

if __name__ == "__main__":
    main()
