import os
import json
import time
import requests
import feedparser
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import google.generativeai as genai
from bs4 import BeautifulSoup
import re

# ─── 설정 ────────────────────────────────────────────────────────────────────
GOOGLE_API_KEY = os.environ.get("GEMINI_API_KEY") or ""
NEWSAPI_KEY         = os.environ.get("NEWSAPI_KEY") or ""
SLACK_WEBHOOK_URL   = os.environ.get("SLACK_WEBHOOK_VIETNAM") or ""

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

HISTORY_FILE = "docs/vietnam_sent_history.json"
MAX_HISTORY  = 500

# ─── 중복 방지 ────────────────────────────────────────────────────────────────
def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(history):
    os.makedirs("docs", exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-MAX_HISTORY:], f, ensure_ascii=False, indent=2)

def is_duplicate(title, history, threshold=0.8):
    for h in history:
        ratio = SequenceMatcher(None, title.lower(), h.lower()).ratio()
        if ratio >= threshold:
            return True
    return False

# ─── 뉴스 수집 ────────────────────────────────────────────────────────────────

def fetch_google_news_rss(query, lang="vi", country="VN", max_items=15):
    """Google News RSS로 뉴스 수집"""
    encoded_query = requests.utils.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl={lang}&gl={country}&ceid={country}:{lang}"
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            link  = entry.get("link", "").strip()
            pub   = entry.get("published", "")
            if title and link:
                articles.append({"title": title, "url": link, "published": pub, "source": "Google News"})
        print(f"  [Google RSS] '{query}': {len(articles)}건")
        return articles
    except Exception as e:
        print(f"  [Google RSS] '{query}' 오류: {e}")
        return []

def fetch_newsapi(query, max_items=15):
    """NewsAPI.org로 영문 뉴스 수집"""
    if not NEWSAPI_KEY:
        return []
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": max_items,
        "apiKey": NEWSAPI_KEY,
        "from": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        articles = []
        for a in data.get("articles", []):
            title = a.get("title", "").strip()
            link  = a.get("url", "").strip()
            if title and link and "[Removed]" not in title:
                articles.append({"title": title, "url": link, "published": a.get("publishedAt", ""), "source": "NewsAPI"})
        print(f"  [NewsAPI] '{query}': {len(articles)}건")
        return articles
    except Exception as e:
        print(f"  [NewsAPI] '{query}' 오류: {e}")
        return []

def fetch_iav_vn(max_items=20):
    """베트남 보험협회(iav.vn) 뉴스 크롤링"""
    url = "https://www.iav.vn/tin-tuc"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        articles = []

        # 일반적인 뉴스 목록 패턴 탐색
        for tag in ["h2", "h3", "h4"]:
            for heading in soup.find_all(tag):
                a_tag = heading.find("a") or heading.find_parent("a")
                if not a_tag:
                    a_tag = heading.find_next_sibling("a")
                if a_tag and a_tag.get("href"):
                    title = heading.get_text(strip=True)
                    href  = a_tag["href"]
                    if not href.startswith("http"):
                        href = "https://www.iav.vn" + href
                    if title and len(title) > 10:
                        articles.append({"title": title, "url": href, "published": "", "source": "iav.vn"})

        # 링크 기반 탐색 (fallback)
        if len(articles) < 5:
            for a_tag in soup.find_all("a", href=True):
                title = a_tag.get_text(strip=True)
                href  = a_tag["href"]
                if len(title) > 15 and ("/tin-tuc/" in href or "/news/" in href):
                    if not href.startswith("http"):
                        href = "https://www.iav.vn" + href
                    articles.append({"title": title, "url": href, "published": "", "source": "iav.vn"})

        seen = set()
        unique = []
        for a in articles:
            if a["title"] not in seen:
                seen.add(a["title"])
                unique.append(a)

        print(f"  [iav.vn] {len(unique[:max_items])}건")
        return unique[:max_items]
    except Exception as e:
        print(f"  [iav.vn] 크롤링 오류: {e}")
        return []

def fetch_thoibaotaichinh(max_items=10):
    """시장 재정신문 보험 RSS 수집"""
    url = "https://thoibaotaichinhvietnam.vn/rss/bao-hiem.rss"
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            link  = entry.get("link", "").strip()
            if title and link:
                articles.append({"title": title, "url": link, "published": entry.get("published", ""), "source": "thoibaotaichinhvietnam.vn"})
        print(f"  [thoibaotaichinhvietnam] {len(articles)}건")
        return articles
    except Exception as e:
        print(f"  [thoibaotaichinhvietnam] 오류: {e}")
        return []

# ─── 전체 수집 ────────────────────────────────────────────────────────────────

def collect_all_news():
    print("\n[뉴스 수집 시작]")
    all_articles = []

    # 1. 베트남 보험협회 (최우선 소스)
    all_articles += fetch_iav_vn(max_items=20)

    # 2. Google News RSS - 베트남어 키워드
    vn_queries = [
        ("bảo hiểm Việt Nam", "vi", "VN"),
        ("bảo hiểm nhân thọ phi nhân thọ", "vi", "VN"),
        ("đại lý bảo hiểm bancassurance", "vi", "VN"),
        ("insurtech bảo hiểm công nghệ số", "vi", "VN"),
        ("quy định bảo hiểm Bộ Tài Chính", "vi", "VN"),
    ]
    for q, lang, country in vn_queries:
        all_articles += fetch_google_news_rss(q, lang=lang, country=country, max_items=10)
        time.sleep(1)

    # 3. Google News RSS - 영어 키워드
    en_queries = [
        ("Vietnam insurance market", "en", "VN"),
        ("Vietnam insurtech fintech insurance", "en", "US"),
        ("Vietnam bancassurance regulation", "en", "US"),
    ]
    for q, lang, country in en_queries:
        all_articles += fetch_google_news_rss(q, lang=lang, country=country, max_items=8)
        time.sleep(1)

    # 4. NewsAPI
    all_articles += fetch_newsapi("Vietnam insurance", max_items=15)
    all_articles += fetch_newsapi("Vietnam insurtech bancassurance", max_items=10)

    # 5. 재정신문 보험 섹션
    all_articles += fetch_thoibaotaichinh(max_items=10)

    # 중복 URL 제거
    seen_urls = set()
    unique = []
    for a in all_articles:
        url = a["url"]
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(a)

    print(f"\n[수집 완료] 총 {len(unique)}건 (URL 중복 제거 후)")
    return unique

# ─── AI 분석 (Gemini) ─────────────────────────────────────────────────────────

def select_and_translate_news(articles, history):
    """Gemini로 10대 뉴스 선정 + 한국어 번역"""

    history_titles = "\n".join(f"- {t}" for t in history[-100:]) if history else "없음"

    articles_text = ""
    for i, a in enumerate(articles):
        articles_text += f"{i+1}. [{a['source']}] {a['title']}\n   URL: {a['url']}\n"

    prompt = f"""당신은 베트남 보험 업계 전문 애널리스트입니다.
해빗팩토리는 시그널파이낸셜랩(보험대리점)을 자회사로 두고 있으며, 베트남 보험대리점 인수 및 AI/디지털 역량 활용을 통한 해외 진출을 추진하고 있습니다.

아래 뉴스 목록에서 오늘의 베트남 보험 업계 10대 뉴스를 선정해 주세요.

## 선정 기준
- 보험사 및 보험대리점에 영향을 미칠 수 있는 뉴스 우선
- 베트남 보험 시장 전문 매체 (iav.vn, thoibaotaichinhvietnam.vn 등) 뉴스를 우선 반영
- 이미 보낸 뉴스(하단 참조)와 유사한 내용은 제외

## 카테고리 구성 (반드시 준수)
1. 🔥 오늘의 TOP 뉴스 (1개): 그날 가장 중요한 뉴스 1개
2. 🏢 보험대리점 (3개): 대리점 규제, bancassurance, 판매채널, GA 관련
3. 💡 인슈어테크 (3개): 디지털 보험, AI 활용, 핀테크 연계, 앱/플랫폼
4. 🏦 보험사 (3개): 보험사 실적, M&A, 신상품, 경영 동향

## 이미 보낸 뉴스 제목 (중복 제외):
{history_titles}

## 후보 뉴스 목록:
{articles_text}

## 출력 형식 (JSON만 출력, 다른 텍스트 없음):
{{
  "top": {{
    "number": 후보번호,
    "title_ko": "한국어 번역 제목",
    "summary_ko": "2-3문장 한국어 요약",
    "url": "URL"
  }},
  "agency": [
    {{"number": 후보번호, "title_ko": "한국어 번역 제목", "summary_ko": "2-3문장 한국어 요약", "url": "URL"}},
    {{"number": 후보번호, "title_ko": "한국어 번역 제목", "summary_ko": "2-3문장 한국어 요약", "url": "URL"}},
    {{"number": 후보번호, "title_ko": "한국어 번역 제목", "summary_ko": "2-3문장 한국어 요약", "url": "URL"}}
  ],
  "insurtech": [
    {{"number": 후보번호, "title_ko": "한국어 번역 제목", "summary_ko": "2-3문장 한국어 요약", "url": "URL"}},
    {{"number": 후보번호, "title_ko": "한국어 번역 제목", "summary_ko": "2-3문장 한국어 요약", "url": "URL"}},
    {{"number": 후보번호, "title_ko": "한국어 번역 제목", "summary_ko": "2-3문장 한국어 요약", "url": "URL"}}
  ],
  "insurer": [
    {{"number": 후보번호, "title_ko": "한국어 번역 제목", "summary_ko": "2-3문장 한국어 요약", "url": "URL"}},
    {{"number": 후보번호, "title_ko": "한국어 번역 제목", "summary_ko": "2-3문장 한국어 요약", "url": "URL"}},
    {{"number": 후보번호, "title_ko": "한국어 번역 제목", "summary_ko": "2-3문장 한국어 요약", "url": "URL"}}
  ]
}}"""

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
        # JSON 블록 추출
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"^```\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        return result
    except Exception as e:
        print(f"[Gemini 오류] {e}")
        return None

# ─── 슬랙 전송 ────────────────────────────────────────────────────────────────

def build_slack_message(news_data, today_str):
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🇻🇳 베트남 보험 10대 뉴스 | {today_str}"}
        },
        {"type": "divider"},
    ]

    # TOP 뉴스
    top = news_data.get("top", {})
    if top:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🔥 오늘의 TOP 뉴스*\n\n*<{top['url']}|{top['title_ko']}>*\n{top.get('summary_ko', '')}"
            }
        })
        blocks.append({"type": "divider"})

    # 보험대리점
    agency_items = news_data.get("agency", [])
    if agency_items:
        text = "*🏢 보험대리점*\n\n"
        for i, item in enumerate(agency_items, 1):
            text += f"*{i}. <{item['url']}|{item['title_ko']}>*\n{item.get('summary_ko', '')}\n\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text.strip()}})
        blocks.append({"type": "divider"})

    # 인슈어테크
    insurtech_items = news_data.get("insurtech", [])
    if insurtech_items:
        text = "*💡 인슈어테크*\n\n"
        for i, item in enumerate(insurtech_items, 1):
            text += f"*{i}. <{item['url']}|{item['title_ko']}>*\n{item.get('summary_ko', '')}\n\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text.strip()}})
        blocks.append({"type": "divider"})

    # 보험사
    insurer_items = news_data.get("insurer", [])
    if insurer_items:
        text = "*🏦 보험사*\n\n"
        for i, item in enumerate(insurer_items, 1):
            text += f"*{i}. <{item['url']}|{item['title_ko']}>*\n{item.get('summary_ko', '')}\n\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text.strip()}})

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "📌 _해빗팩토리 베트남 보험 뉴스봇 | 매 영업일 오전 8시 (KST)_"}]
    })

    return {"blocks": blocks}

def send_to_slack(message):
    if not SLACK_WEBHOOK_URL:
        print("[슬랙] SLACK_WEBHOOK_VIETNAM 환경변수가 없습니다.")
        return False
    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json=message, timeout=15)
        if resp.status_code == 200:
            print("[슬랙] 전송 성공!")
            return True
        else:
            print(f"[슬랙] 전송 실패: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"[슬랙] 전송 오류: {e}")
        return False

# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    today_str = datetime.now().strftime("%Y년 %m월 %d일")
    print(f"=== 베트남 보험 뉴스봇 시작: {today_str} ===")

    # 1. 히스토리 로드
    history = load_history()
    print(f"[히스토리] {len(history)}건 로드")

    # 2. 뉴스 수집
    articles = collect_all_news()
    if not articles:
        print("[오류] 수집된 뉴스가 없습니다.")
        return

    # 3. 히스토리 기반 1차 필터링
    filtered = [a for a in articles if not is_duplicate(a["title"], history)]
    print(f"[필터링] 히스토리 중복 제거 후 {len(filtered)}건 남음")

    if len(filtered) < 10:
        print("[경고] 후보 뉴스가 10건 미만입니다. 히스토리 필터 완화하여 재시도.")
        filtered = articles  # 필터 없이 전체 사용

    # 4. Gemini로 10대 뉴스 선정 + 번역
    print("\n[Gemini] 10대 뉴스 선정 및 번역 중...")
    news_data = select_and_translate_news(filtered[:80], history)

    if not news_data:
        print("[오류] Gemini 응답 실패")
        return

    # 5. 슬랙 전송
    message = build_slack_message(news_data, today_str)
    success = send_to_slack(message)

    # 6. 히스토리 업데이트 (전송 성공 시)
    if success:
        new_titles = []
        for section in ["top", "agency", "insurtech", "insurer"]:
            item = news_data.get(section)
            if isinstance(item, dict):
                new_titles.append(item.get("title_ko", ""))
            elif isinstance(item, list):
                for i in item:
                    new_titles.append(i.get("title_ko", ""))

        # 원문 제목도 추가
        used_numbers = set()
        for section in ["top", "agency", "insurtech", "insurer"]:
            item = news_data.get(section)
            if isinstance(item, dict):
                used_numbers.add(item.get("number", -1))
            elif isinstance(item, list):
                for i in item:
                    used_numbers.add(i.get("number", -1))

        for num in used_numbers:
            if 1 <= num <= len(filtered):
                new_titles.append(filtered[num - 1]["title"])

        history.extend([t for t in new_titles if t])
        save_history(history)
        print(f"[히스토리] {len(new_titles)}건 추가 저장 완료")

    print("=== 완료 ===")

if __name__ == "__main__":
    main()
