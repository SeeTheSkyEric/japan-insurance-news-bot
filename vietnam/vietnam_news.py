import os
import json
import time
import requests
import feedparser
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from google import genai
from bs4 import BeautifulSoup
import re

# ─── 설정 ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY") or ""
NEWSAPI_KEY         = os.environ.get("NEWSAPI_KEY") or ""
SLACK_WEBHOOK_URL   = os.environ.get("SLACK_WEBHOOK_VIETNAM") or ""

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

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
    url = "https://www.iav.vn/tin-tuc"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        articles = []
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

def collect_all_news():
    print("\n[뉴스 수집 시작]")
    all_articles = []
    all_articles += fetch_iav_vn(max_items=20)
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
    en_queries = [
        ("Vietnam insurance market", "en", "VN"),
        ("Vietnam insurtech fintech insurance", "en", "US"),
        ("Vietnam bancassurance regulation", "en", "US"),
    ]
    for q, lang, country in en_queries:
        all_articles += fetch_google_news_rss(q, lang=lang, country=country, max_items=8)
        time.sleep(1)
    all_articles += fetch_newsapi("Vietnam insurance", max_items=15)
    all_articles += fetch_newsapi("Vietnam insurtech bancassurance", max_items=10)
    all_articles += fetch_thoibaotaichinh(max_items=10)
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
    history_titles = "\n".join(f"- {t}" for t in history[-100:]) if history else "없음"
    articles_text = ""
    for i, a in enumerate(articles):
        articles_text += f"{i+1}. [{a['source']}] {a['title']}\n   URL: {a['url']}\n"

    prompt = f"""당신은 베트남 보험 업계 전문 애널리스트입니다.
해빗팩토리는 시그널파이낸셜랩(보험대리점)을 자회사로 두고 있으며, 베트남 보험대리점 인수 및 AI/디지털 역량 활용을 통한 해외 진출을 추진하고 있습니다.

아래 뉴스 목록에서 오늘의 베트남 보험 업계 10대 뉴스를 선정해 주세요.

카테고리 구성 (반드시 준수):
1. top: 그날 가장 중요한 뉴스 1개
2. agency: 보험대리점 관련 뉴스 3개 (대리점 규제, bancassurance, 판매채널, GA)
3. insurtech: 인슈어테크 관련 뉴스 3개 (디지털 보험, AI, 핀테크, 앱/플랫폼)
4. insurer: 보험사 관련 뉴스 3개 (실적, M&A, 신상품, 경영 동향)

이미 보낸 뉴스 제목 (중복 제외):
{history_titles}

후보 뉴스 목록:
{articles_text}

반드시 아래 JSON 형식으로만 응답하세요. JSON 외 다른 텍스트는 절대 포함하지 마세요:
{{"top":{{"number":1,"title_ko":"한국어 번역 제목","summary_ko":"2-3문장 한국어 요약","url":"URL"}},"agency":[{{"number":2,"title_ko":"제목","summary_ko":"요약","url":"URL"}},{{"number":3,"title_ko":"제목","summary_ko":"요약","url":"URL"}},{{"number":4,"title_ko":"제목","summary_ko":"요약","url":"URL"}}],"insurtech":[{{"number":5,"title_ko":"제목","summary_ko":"요약","url":"URL"}},{{"number":6,"title_ko":"제목","summary_ko":"요약","url":"URL"}},{{"number":7,"title_ko":"제목","summary_ko":"요약","url":"URL"}}],"insurer":[{{"number":8,"title_ko":"제목","summary_ko":"요약","url":"URL"}},{{"number":9,"title_ko":"제목","summary_ko":"요약","url":"URL"}},{{"number":10,"title_ko":"제목","summary_ko":"요약","url":"URL"}}]}}"""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw = response.text.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"^```\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        print("[Gemini] 10대 뉴스 선정 완료")
        return result
    except Exception as e:
        print(f"[Gemini 오류] {e}")
        return None

# ─── 슬랙 전송 ────────────────────────────────────────────────────────────────

def build_slack_message(news_data, today_str):
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🇻🇳 베트남 보험 10대 뉴스 | {today_str}"}},
        {"type": "divider"},
    ]
    top = news_data.get("top", {})
    if top:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*🔥 오늘의 TOP 뉴스*\n\n*<{top['url']}|{top['title_ko']}>*\n{top.get('summary_ko', '')}"}})
        blocks.append({"type": "divider"})
    for cat, emoji, label in [("agency", "🏢", "보험대리점"), ("insurtech", "💡", "인슈어테크"), ("insurer", "🏦", "보험사")]:
        items = news_data.get(cat, [])
        if items:
            text = f"*{emoji} {label}*\n\n"
            for i, item in enumerate(items, 1):
                text += f"*{i}. <{item['url']}|{item['title_ko']}>*\n{item.get('summary_ko', '')}\n\n"
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text.strip()}})
            blocks.append({"type": "divider"})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "📌 _해빗팩토리 베트남 보험 뉴스봇 | 매 영업일 오전 8시 (KST)_"}]})
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
    history = load_history()
    print(f"[히스토리] {len(history)}건 로드")
    articles = collect_all_news()
    if not articles:
        print("[오류] 수집된 뉴스가 없습니다.")
        return
    filtered = [a for a in articles if not is_duplicate(a["title"], history)]
    print(f"[필터링] 히스토리 중복 제거 후 {len(filtered)}건 남음")
    if len(filtered) < 10:
        print("[경고] 후보 뉴스 10건 미만. 전체 사용.")
        filtered = articles
    print("\n[Gemini] 10대 뉴스 선정 및 번역 중...")
    news_data = select_and_translate_news(filtered[:80], history)
    if not news_data:
        print("[오류] Gemini 응답 실패")
        return
    message = build_slack_message(news_data, today_str)
    success = send_to_slack(message)
    if success:
        new_titles = []
        for section in ["top", "agency", "insurtech", "insurer"]:
            item = news_data.get(section)
            if isinstance(item, dict):
                new_titles.append(item.get("title_ko", ""))
            elif isinstance(item, list):
                for i in item:
                    new_titles.append(i.get("title_ko", ""))
        history.extend([t for t in new_titles if t])
        save_history(history)
        print(f"[히스토리] {len(new_titles)}건 추가 저장 완료")
    print("=== 완료 ===")

if __name__ == "__main__":
    main()
