import os
import json
import time
import requests
import feedparser
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from google import genai
import re

# ─── 설정 ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY") or ""
NEWSAPI_KEY       = os.environ.get("NEWSAPI_KEY") or ""
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_JP_HOUSING") or ""
GITHUB_PAGES_URL  = os.environ.get("GITHUB_PAGES_URL") or "https://seetheskyeric.github.io/japan-insurance-news-bot/japan-housing.html"

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

HISTORY_FILE = "docs/housing_sent_history.json"
MAX_HISTORY  = 500

# ─── 뉴스 최신성(recency) 설정 ────────────────────────────────────────────────
# 직전 N일 이내 기사만 선정 대상. 주택/대출 뉴스는 일 발생량이 많지 않아
# 후보 확보를 위해 베트남 봇(14일)과 동일하게 넉넉히 잡는다. (조정 가능)
RECENCY_DAYS = 14

# ─── 카테고리 설정 ────────────────────────────────────────────────────────────
# top      : 그날 가장 중요한 주택/대출 뉴스 1개
# iida     : 이이다 그룹 전용 섹션 (BVL 자리 대체, 관련 뉴스 있을 때만 노출)
# housing  : 주택시장 동향 3개
# mortgage : 주택대출·금리 3개 (일본은행 금융정책·국채금리 등 거시·정책 흡수)
# proptech : 프롭테크·모기지테크 3개
CATS = [
    ("top",      "🔥 오늘의 TOP 뉴스",       "#F59E0B"),
    ("iida",     "🏠 이이다 그룹 관련",       "#DC2626"),
    ("housing",  "🏘️ 주택시장 동향",         "#2E86AB"),
    ("mortgage", "🏦 주택대출·금리",          "#059669"),
    ("proptech", "💡 프롭테크·모기지테크",     "#8B5CF6"),
]
CAT_SLACK_LABELS = {
    "top":      "🔥 TOP 뉴스",
    "iida":     "🏠 이이다 그룹",
    "housing":  "🏘️ 주택시장",
    "mortgage": "🏦 주택대출·금리",
    "proptech": "💡 프롭테크·모기지테크",
}

# ─── 이이다 그룹 전용 검색 키워드 ──────────────────────────────────────────────
# 홀딩스 + 분양주택 브랜드 7사 + FLS(모기지뱅크) + IGIS(보험서비스).
# 후보는 넓게 모으고, 실제 이이다 관련 여부는 Gemini 선정 단계에서 필터링한다.
IIDA_QUERIES_JP = [
    "飯田グループホールディングス",   # Iida Group Holdings (3291)
    "飯田グループ 住宅",              # 그룹 일반
    "アーネストワン",                 # Arnest One
    "一建設",                        # Hajime Kensetsu
    "飯田産業",                       # Iida Sangyo
    "東栄住宅",                       # Tohei Jutaku
    "タクトホーム",                   # Tact Home
    "アイディホーム",                 # Aidee Home
    "ファミリーライフサービス",        # FLS = Family Life Service (모기지뱅크, フラット35)
    "飯田保険サービス",               # IGIS = Iida Insurance Service (구 FLI, 보험대리점)
]
IIDA_QUERIES_EN = [
    "Iida Group Holdings",
]

# ─── 발행일 최신성 판별 ────────────────────────────────────────────────────────
def is_recent(published_parsed, days=RECENCY_DAYS):
    """
    feedparser의 published_parsed(struct_time)를 기준으로 최근 N일 이내인지 판단.
    - 날짜 정보가 없으면 True(보수적 통과). Google News는 when: 필터가 1차로
      걸러주므로, 날짜 없는 소수 항목까지 버리면 정상 최신 기사를 놓칠 수 있다.
    """
    if not published_parsed:
        return True
    try:
        pub_dt = datetime.fromtimestamp(time.mktime(published_parsed))
        return pub_dt >= (datetime.now() - timedelta(days=days))
    except Exception:
        return True

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

# ─── Gemini 호출 (503 UNAVAILABLE 대비 지수 백오프 재시도) ────────────────────
def gemini_generate(prompt, max_attempts=5):
    """
    Gemini 호출을 재시도로 감싼다. 8시 KST 전후 글로벌 배치 트래픽 때문에
    503 UNAVAILABLE이 자주 발생하므로, 30~120초 간격으로 최대 5회 시도한다.
    """
    delays = [30, 45, 68, 101, 120]  # 초 단위, 지수적으로 증가(최대 120초)
    last_err = None
    for attempt in range(max_attempts):
        try:
            resp = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            return resp.text.strip()
        except Exception as e:
            last_err = e
            wait = delays[min(attempt, len(delays) - 1)]
            print(f"  [Gemini 재시도] {attempt+1}/{max_attempts} 실패: {e}")
            if attempt < max_attempts - 1:
                print(f"    → {wait}초 후 재시도")
                time.sleep(wait)
    print(f"  [Gemini 최종 실패] {last_err}")
    return None

def parse_gemini_json(raw):
    """Gemini 응답에서 코드펜스를 제거하고 JSON 파싱."""
    if not raw:
        return None
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)

# ─── 뉴스 수집 ────────────────────────────────────────────────────────────────
def fetch_google_news_rss(query, lang="ja", country="JP", max_items=15, recency_days=RECENCY_DAYS):
    # 1차 방어: when:Nd 연산자로 Google News 검색 자체를 최근 N일로 제한
    full_query    = f"{query} when:{recency_days}d"
    encoded_query = requests.utils.quote(full_query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl={lang}&gl={country}&ceid={country}:{lang}"
    try:
        feed = feedparser.parse(url)
        articles = []
        dropped  = 0
        for entry in feed.entries[:max_items * 2]:
            title = entry.get("title", "").strip()
            link  = entry.get("link", "").strip()
            pub   = entry.get("published", "")
            pub_parsed = entry.get("published_parsed")
            if not (title and link):
                continue
            # 2차 방어: 발행일이 최근 N일 이내인지 재검증
            if not is_recent(pub_parsed, recency_days):
                dropped += 1
                continue
            articles.append({"title": title, "url": link, "published": pub, "source": "Google News"})
            if len(articles) >= max_items:
                break
        msg = f"  [Google RSS] '{query}': {len(articles)}건"
        if dropped:
            msg += f" (기간 초과 {dropped}건 제외)"
        print(msg)
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
        "from": (datetime.now() - timedelta(days=RECENCY_DAYS)).strftime("%Y-%m-%d"),
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

def fetch_iida_news():
    """이이다 그룹 전용 뉴스 수집 (Google RSS의 when: 필터가 이미 적용됨)"""
    print("\n  [이이다 그룹 전용 수집 시작]")
    iida_articles = []
    seen_urls = set()

    for q in IIDA_QUERIES_JP:
        for a in fetch_google_news_rss(q, lang="ja", country="JP", max_items=6):
            if a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                iida_articles.append(a)
        time.sleep(0.5)

    for q in IIDA_QUERIES_EN:
        for a in fetch_google_news_rss(q, lang="en", country="US", max_items=5):
            if a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                iida_articles.append(a)
        time.sleep(0.5)

    print(f"  [이이다 전용] 총 {len(iida_articles)}건 수집")
    return iida_articles

def collect_all_news():
    print("\n[뉴스 수집 시작]")
    all_articles = []

    # 일본어 쿼리 (주력) — 전문매체(다이아몬드부동산·리크루트·주택신보 등)는
    # Google News RSS가 이미 색인하므로 좋은 키워드로 함께 잡힌다.
    jp_queries = [
        "住宅ローン 金利",
        "フラット35 金利",
        "住宅市場 マンション価格",
        "地価 不動産価格 動向",
        "住宅着工 統計",
        "日銀 金利 住宅ローン",
        "住宅ローン減税 政策",
        "不動産テック プロップテック",
        "住宅ローン フィンテック オンライン",
    ]
    for q in jp_queries:
        all_articles += fetch_google_news_rss(q, lang="ja", country="JP", max_items=10)
        time.sleep(1)

    # 한국어 쿼리 (국내 매체의 일본 시장 보도)
    kr_queries = [
        "일본 집값 부동산",
        "일본 주택담보대출 금리",
    ]
    for q in kr_queries:
        all_articles += fetch_google_news_rss(q, lang="ko", country="KR", max_items=8)
        time.sleep(1)

    # 영어 쿼리
    en_queries = [
        "Japan housing market",
        "Japan mortgage rates BOJ",
        "Japan proptech real estate technology",
    ]
    for q in en_queries:
        all_articles += fetch_google_news_rss(q, lang="en", country="US", max_items=8)
        time.sleep(1)

    # NewsAPI 보강
    all_articles += fetch_newsapi("Japan housing market real estate", max_items=15)
    all_articles += fetch_newsapi("Japan mortgage housing loan interest rate", max_items=10)

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
def select_iida_news(iida_articles, history, max_items=3):
    """이이다 그룹 관련 뉴스 번역·선정 (최대 3건, 없으면 0건)"""
    if not iida_articles:
        return []

    history_titles = "\n".join(f"- {t}" for t in history[-100:]) if history else "없음"
    articles_text = ""
    for i, a in enumerate(iida_articles):
        articles_text += f"{i+1}. [{a['source']}] {a['title']}\n   URL: {a['url']}\n"

    prompt = f"""당신은 일본 주택·부동산 시장 전문 애널리스트입니다.

아래는 일본 최대 분양주택 그룹인 '이이다 그룹 홀딩스(飯田グループホールディングス)' 및
그 계열사 관련 뉴스 후보입니다. 대상 계열사에는 다음이 포함됩니다:
- 분양주택: 一建設, アーネストワン, 飯田産業, 東栄住宅, タクトホーム, アイディホーム
- FLS = ファミリーライフサービス (그룹 주택대출/フラット35 담당 모기지뱅크)
- IGIS = 飯田保険サービス (그룹 보험대리점, 구 株式会社FLI)

실제로 이이다 그룹 또는 위 계열사와 직접 관련된 뉴스만 최대 {max_items}건 선정하세요.
'ファミリーライフサービス' 등 일반적인 이름 때문에 딸려온 무관한 회사 뉴스는 제외하세요.
관련 뉴스가 없으면 빈 배열을 반환하세요.

[중요] 최근 {RECENCY_DAYS}일 이내의 최신 뉴스만 대상입니다. 제목·내용상 명백히 오래된
기사(수 개월/수 년 전 사건)로 판단되면 절대 선정하지 마세요.

이미 보낸 뉴스 (중복 제외):
{history_titles}

후보 뉴스:
{articles_text}

반드시 아래 JSON 배열 형식으로만 응답하세요. JSON 외 텍스트는 절대 포함하지 마세요:
[{{"number":1,"title_ko":"한국어 번역 제목","summary_ko":"3-4문장 한국어 요약","url":"URL","source":"출처","published":""}}]

관련 뉴스가 없으면: []"""

    try:
        result = parse_gemini_json(gemini_generate(prompt))
        n = len(result) if isinstance(result, list) else 0
        print(f"[Gemini] 이이다 그룹 뉴스 {n}건 선정")
        return result if isinstance(result, list) else []
    except Exception as e:
        print(f"[Gemini 이이다 오류] {e}")
        return []

def select_and_translate_news(articles, history):
    history_titles = "\n".join(f"- {t}" for t in history[-100:]) if history else "없음"
    articles_text = ""
    for i, a in enumerate(articles):
        articles_text += f"{i+1}. [{a['source']}] {a['title']}\n   URL: {a['url']}\n"

    prompt = f"""당신은 일본 주택·주택대출 시장 전문 애널리스트입니다.

해빗팩토리는 보험대리점(시그널파이낸셜랩)을 자회사로 두고 있으며, AI/Data/Digital 역량과
보험대리점 사업을 바탕으로 일본 등 해외 진출을 추진하고 있습니다. 일본의 '주택 판매 →
주택대출 → 보험'으로 이어지는 가치사슬(이이다 그룹이 대표적)을 모니터링하는 것이 목적입니다.

아래 뉴스 목록에서 오늘의 일본 주택·대출 시장 10대 뉴스를 선정해 주세요.
주택시장과 주택대출 시장에 실질적 영향을 주는 뉴스를 중요도 순으로 고르세요.

[중요] 최근 {RECENCY_DAYS}일 이내의 최신 뉴스만 선정 대상입니다. 제목·내용상 명백히
오래된 기사(수 개월/수 년 전 사건)로 보이면 절대 선정하지 마세요.

카테고리 구성 (반드시 준수):
1. top: 그날 가장 중요한 뉴스 1개 (주택/대출 통틀어 가장 임팩트 큰 것)
2. housing: 주택시장 동향 3개 (집값·거래량·공급/착공·부동산 정책·규제)
3. mortgage: 주택대출·금리 3개 (변동/고정 금리, フラット35, 일본은행 금융정책·국채금리, 대출 상품·규제)
4. proptech: 프롭테크·모기지테크 3개 (부동산 테크, 모기지 온라인/디지털화, AI·핀테크, 플랫폼)

요약(summary_ko)은 3~4문장으로: (1)무슨 일이 있었는지 (2)핵심 수치/금액 (3)배경 맥락
(4)주택·대출 업계에 미치는 영향 순으로 작성하세요.

이미 보낸 뉴스 제목 (중복 제외):
{history_titles}

후보 뉴스 목록:
{articles_text}

반드시 아래 JSON 형식으로만 응답하세요. JSON 외 다른 텍스트는 절대 포함하지 마세요:
{{"top":{{"number":1,"title_ko":"한국어 번역 제목","summary_ko":"3-4문장 한국어 요약","url":"URL","source":"출처","published":""}},"housing":[{{"number":2,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":3,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":4,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}}],"mortgage":[{{"number":5,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":6,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":7,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}}],"proptech":[{{"number":8,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":9,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}},{{"number":10,"title_ko":"제목","summary_ko":"요약","url":"URL","source":"출처","published":""}}]}}"""

    try:
        result = parse_gemini_json(gemini_generate(prompt))
        if result:
            print("[Gemini] 10대 뉴스 선정 완료")
        return result
    except Exception as e:
        print(f"[Gemini 오류] {e}")
        return None

# ─── HTML 생성 ────────────────────────────────────────────────────────────────
def build_html(news_data, iida_news, fetch_date, for_web=False):
    rows = ""
    for key, label, color in CATS:
        if key == "iida":
            items = iida_news
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
                if key == "iida" else
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
  <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:24px 28px;color:white;">
    <h1 style="margin:0;font-size:20px;">🏠 일본 주택·대출 뉴스 TOP 10</h1>
    <p style="margin:6px 0 0;opacity:.7;font-size:13px;">HabitFactory Global Team · {fetch_date}</p>
  </div>
  <table style="width:100%;border-collapse:collapse;">{rows}</table>
  <div style="padding:16px;text-align:center;color:#9CA3AF;font-size:12px;">© HabitFactory Global Team</div>
</div>
</body></html>"""

def save_web_page(news_data, iida_news, fetch_date):
    os.makedirs("docs", exist_ok=True)
    with open("docs/japan-housing.html", "w", encoding="utf-8") as f:
        f.write(build_html(news_data, iida_news, fetch_date, for_web=True))
    print("  ✅ docs/japan-housing.html 저장")

# ─── 슬랙 전송 ────────────────────────────────────────────────────────────────
def send_to_slack(news_data, iida_news, fetch_date, page_url):
    if not SLACK_WEBHOOK_URL:
        print("[슬랙] SLACK_WEBHOOK_JP_HOUSING 환경변수가 없습니다.")
        return False

    # TOP 뉴스
    top = news_data.get("top", {})
    top_line = ""
    if top:
        top_line = f"\n\n🔥 *오늘의 TOP 뉴스*\n<{top['url']}|{top['title_ko']}>\n_{top.get('summary_ko', '')}_"

    # 이이다 그룹 뉴스
    iida_line = ""
    if iida_news:
        iida_line = "\n\n🏠 *이이다 그룹 관련 뉴스*"
        for item in iida_news:
            iida_line += f"\n• <{item['url']}|{item['title_ko']}>"

    # 카테고리별 건수
    summary_parts = []
    for key, label in CAT_SLACK_LABELS.items():
        if key in ("top", "iida"):
            continue
        items = news_data.get(key, [])
        if items:
            summary_parts.append(f"{label} {len(items)}건")
    summary = " · ".join(summary_parts)

    text = (
        f"🏠 *일본 주택·대출 뉴스 TOP 10* — {fetch_date}"
        f"{top_line}"
        f"{iida_line}"
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
    print(f"=== 일본 주택·대출 뉴스봇 시작: {today_str} (최근 {RECENCY_DAYS}일 기사 대상) ===")

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

    # 이이다 그룹 전용 뉴스 수집
    iida_raw = fetch_iida_news()

    # Gemini: 일반 10대 뉴스 선정
    print("\n[Gemini] 10대 뉴스 선정 및 번역 중...")
    news_data = select_and_translate_news(filtered[:80], history)
    if not news_data:
        print("[오류] Gemini 응답 실패")
        return

    # Gemini: 이이다 그룹 뉴스 선정 (별도 호출)
    print("\n[Gemini] 이이다 그룹 뉴스 선정 중...")
    iida_news = select_iida_news(iida_raw, history, max_items=3)

    # GitHub Pages HTML 저장
    save_web_page(news_data, iida_news, fetch_date)

    # 슬랙 전송
    success = send_to_slack(news_data, iida_news, fetch_date, GITHUB_PAGES_URL)

    if success:
        new_titles = []
        for section in ["top", "housing", "mortgage", "proptech"]:
            item = news_data.get(section)
            if isinstance(item, dict):
                new_titles.append(item.get("title_ko", ""))
            elif isinstance(item, list):
                for i in item:
                    new_titles.append(i.get("title_ko", ""))
        for item in iida_news:
            new_titles.append(item.get("title_ko", ""))
        history.extend([t for t in new_titles if t])
        save_history(history)
        print(f"[히스토리] {len(new_titles)}건 추가 저장 완료")

    print("=== 완료 ===")

if __name__ == "__main__":
    main()
