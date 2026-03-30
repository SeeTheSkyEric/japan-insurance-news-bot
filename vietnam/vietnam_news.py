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
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY") or ""
NEWSAPI_KEY       = os.environ.get("NEWSAPI_KEY") or ""
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_VIETNAM") or ""
GITHUB_PAGES_URL  = os.environ.get("GITHUB_PAGES_URL") or "https://seetheskyeric.github.io/japan-insurance-news-bot/vietnam.html"

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

HISTORY_FILE = "docs/vietnam_sent_history.json"
MAX_HISTORY  = 500

# ─── 카테고리 설정 ────────────────────────────────────────────────────────────
CATS = [
    ("top",       "🔥 오늘의 TOP 뉴스",       "#F59E0B"),
    ("bvl",       "⭐ BVL 관련 뉴스",          "#DC2626"),
    ("agency",    "🏢 보험대리점 관련",         "#2E86AB"),
    ("insurtech", "💡 인슈어테크 관련",         "#8B5CF6"),
    ("insurer",   "🏦 보험사 관련",             "#059669"),
]
CAT_SLACK_LABELS = {
    "top":       "🔥 TOP 뉴스",
    "bvl":       "⭐ BVL",
    "agency":    "🏢 보험대리점",
    "insurtech": "💡 인슈어테크",
    "insurer":   "🏦 보험사",
}

# BVL 관련 검색 키워드
BVL_QUERIES_VI = [
    "Bảo Việt Life",
    "Bảo Việt nhân thọ",
    "Tập đoàn Bảo Việt",
    "BVL bảo hiểm",
    "Bảo Việt Holdings",
]
BVL_QUERIES_EN = [
    "Bao Viet Life insurance",
    "Bao Viet Holdings Vietnam",
    "BVL Vietnam insurance",
]

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

def fetch_bvl_news():
    """바오비엣라이프(BVL) 전용 뉴스 수집"""
    print("\n  [BVL 전용 수집 시작]")
    bvl_articles = []
    seen_urls = set()

    for q in BVL_QUERIES_VI:
        for a in fetch_google_news_rss(q, lang="vi", country="VN", max_items=8):
            if a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                bvl_articles.append(a)
        time.sleep(0.5)

    for q in BVL_QUERIES_EN:
        for a in fetch_google_news_rss(q, lang="en", country="US", max_items=5):
            if a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                bvl_articles.append(a)
        if NEWSAPI_KEY:
            for a in fetch_newsapi(q, max_items=5):
                if a["url"] not in seen_urls:
                    seen_urls.add(a["url"])
                    bvl_articles.append(a)
        time.sleep(0.5)

    print(f"  [BVL 전용] 총 {len(bvl_articles)}건 수집")
    return bvl_articles

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

def select_bvl_news(bvl_articles, history, max_items=3):
    """BVL 관련 뉴스 번역 및 선정 (최대 3건, 없으면 0건)"""
    if not bvl_articles:
        return []

    history_titles = "\n".join(f"- {t}" for t in history[-100:]) if history else "없음"
    articles_text = ""
    for i, a in enumerate(bvl_articles):
        articles_text += f"{i+1}. [{a['source']}] {a['title']}\n   URL: {a['url']}\n"

    prompt = f"""당신은 베트남 보험 업계 전문 애널리스트입니다.

아래는 베트남 최대 국영 생명보험사 바오비엣라이프(Bảo Việt Life, BVL) 및 바오비엣 그룹 관련 뉴스 후보입니다.
실제로 BVL 또는 바오비엣 그룹과 직접 관련된 뉴스만 최대 {max_items}건 선정해 주세요.
관련 없는 뉴스는 선정하지 마세요. 뉴스가 없으면 빈 배열을 반환하세요.

이미 보낸 뉴스 (중복 제외):
{history_titles}

후보 뉴스:
{articles_text}

반드시 아래 JSON 배열 형식으로만 응답하세요:
[{{"number":1,"title_ko":"한국어 번역 제목","summary_ko":"2-3문장 한국어 요약","url":"URL","source":"출처","published":""}}]

관련 뉴스가 없으면: []"""

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
        print(f"[Gemini] BVL 뉴스 {len(result)}건 선정")
        return result if isinstance(result, list) else []
    except Exception as e:
        print(f"[Gemini BVL 오류] {e}")
        return []

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
{{"top":{{"number":1,"title_ko":"한국어 번역 제목","summary_ko":"2-3문장 한국어 요약","url":"URL","source":"출처","published":""}},"agency":[{{"number":2,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":3,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":4,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}}],"insurtech":[{{"number":5,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":6,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":7,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}}],"insurer":[{{"number":8,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":9,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":10,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}}]}}"""

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

# ─── HTML 생성 ────────────────────────────────────────────────────────────────

def build_html(news_data, bvl_news, fetch_date, for_web=False):
    rows = ""
    for key, label, color in CATS:
        if key == "bvl":
            items = bvl_news
        elif key == "top":
            items = [news_data.get("top")] if news_data.get("top") else []
        else:
            items = news_data.get(key, [])

        if not items:
            continue

        rows += f'<tr><td style="background:{color};color:white;padding:10px 16px;font-weight:bold;font-size:15px;">{label}</td></tr>'
        for item in items:
            title_style = (
                "color:#D97706;font-weight:bold;font-size:17px;text-decoration:none;line-height:1.5;"
                if key == "top" else
                "color:#DC2626;font-weight:bold;font-size:15px;text-decoration:none;line-height:1.5;"
                if key == "bvl" else
                "color:#1D4ED8;font-weight:bold;font-size:15px;text-decoration:none;line-height:1.5;"
            )
            pub = item.get("published", "")
            source = item.get("source", "")
            rows += f"""<tr style="border-bottom:1px solid #eee;">
  <td style="padding:14px 16px;vertical-align:top;">
    <a href="{item['url']}" style="{title_style}">{item['title_ko']}</a><br>
    <span style="color:#9CA3AF;font-size:12px;">{'📅 ' + pub + ' · ' if pub else ''}📰 {source}</span>
    <div style="background:#F9FAFB;padding:10px 12px;border-radius:6px;margin-top:8px;font-size:13px;color:#374151;line-height:1.8;">{item['summary_ko']}</div>
  </td>
</tr>"""

    meta    = '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">' if for_web else ""
    refresh = '<meta http-equiv="refresh" content="3600">' if for_web else ""
    return f"""<html><head>{meta}{refresh}</head>
<body style="font-family:sans-serif;background:#F0F2F5;padding:20px;margin:0;">
<div style="max-width:700px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">
  <div style="background:linear-gradient(135deg,#c8102e,#ff6b35);padding:24px 28px;color:white;">
    <h1 style="margin:0;font-size:20px;">🇻🇳 베트남 보험뉴스 TOP 10</h1>
    <p style="margin:6px 0 0;opacity:.7;font-size:13px;">HabitFactory Global Team · {fetch_date}</p>
  </div>
  <table style="width:100%;border-collapse:collapse;">{rows}</table>
  <div style="padding:16px;text-align:center;color:#9CA3AF;font-size:12px;">© HabitFactory Global Team</div>
</div>
</body></html>"""

def save_web_page(news_data, bvl_news, fetch_date):
    os.makedirs("docs", exist_ok=True)
    with open("docs/vietnam.html", "w", encoding="utf-8") as f:
        f.write(build_html(news_data, bvl_news, fetch_date, for_web=True))
    print("  ✅ docs/vietnam.html 저장")

# ─── 슬랙 전송 ────────────────────────────────────────────────────────────────

def send_to_slack(news_data, bvl_news, fetch_date, page_url):
    if not SLACK_WEBHOOK_URL:
        print("[슬랙] SLACK_WEBHOOK_VIETNAM 환경변수가 없습니다.")
        return False

    # TOP 뉴스
    top = news_data.get("top", {})
    top_line = ""
    if top:
        top_line = f"\n\n🔥 *오늘의 TOP 뉴스*\n<{top['url']}|{top['title_ko']}>\n_{top.get('summary_ko', '')}_"

    # BVL 뉴스
    bvl_line = ""
    if bvl_news:
        bvl_line = "\n\n⭐ *BVL 관련 뉴스*"
        for item in bvl_news:
            bvl_line += f"\n• <{item['url']}|{item['title_ko']}>"

    # 카테고리별 건수
    summary_parts = []
    for key, label in CAT_SLACK_LABELS.items():
        if key in ("top", "bvl"):
            continue
        items = news_data.get(key, [])
        if items:
            summary_parts.append(f"{label} {len(items)}건")
    summary = " · ".join(summary_parts)

    text = (
        f"🇻🇳 *베트남 보험뉴스 TOP 10* — {fetch_date}"
        f"{top_line}"
        f"{bvl_line}"
        f"\n\n{summary}"
        f"\n\n<{page_url}|📰 전체 기사 보기 →>"
    )

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=15)
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
    fetch_date = datetime.now().strftime("%Y年%m月%d日")
    today_str  = datetime.now().strftime("%Y년 %m월 %d일")
    print(f"=== 베트남 보험 뉴스봇 시작: {today_str} ===")

    history = load_history()
    print(f"[히스토리] {len(history)}건 로드")

    # 일반 뉴스 수집
    articles = collect_all_news()
    if not articles:
        print("[오류] 수집된 뉴스가 없습니다.")
        return

    filtered = [a for a in articles if not is_duplicate(a["title"], history)]
    print(f"[필터링] 히스토리 중복 제거 후 {len(filtered)}건 남음")
    if len(filtered) < 10:
        print("[경고] 후보 뉴스 10건 미만. 전체 사용.")
        filtered = articles

    # BVL 전용 뉴스 수집
    bvl_raw = fetch_bvl_news()

    # Gemini: 일반 10대 뉴스 선정
    print("\n[Gemini] 10대 뉴스 선정 및 번역 중...")
    news_data = select_and_translate_news(filtered[:80], history)
    if not news_data:
        print("[오류] Gemini 응답 실패")
        return

    # Gemini: BVL 뉴스 선정 (별도 호출)
    print("\n[Gemini] BVL 뉴스 선정 중...")
    bvl_news = select_bvl_news(bvl_raw, history, max_items=3)

    # GitHub Pages HTML 저장
    save_web_page(news_data, bvl_news, fetch_date)

    # 슬랙 전송
    success = send_to_slack(news_data, bvl_news, fetch_date, GITHUB_PAGES_URL)

    if success:
        new_titles = []
        for section in ["top", "agency", "insurtech", "insurer"]:
            item = news_data.get(section)
            if isinstance(item, dict):
                new_titles.append(item.get("title_ko", ""))
            elif isinstance(item, list):
                for i in item:
                    new_titles.append(i.get("title_ko", ""))
        for item in bvl_news:
            new_titles.append(item.get("title_ko", ""))
        history.extend([t for t in new_titles if t])
        save_history(history)
        print(f"[히스토리] {len(new_titles)}건 추가 저장 완료")

    print("=== 완료 ===")

if __name__ == "__main__":
    main()
