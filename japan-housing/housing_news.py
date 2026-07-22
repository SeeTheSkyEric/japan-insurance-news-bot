import os
import json
import time
import requests
import feedparser
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from google import genai
import re
from googlenewsdecoder import gnewsdecoder

# ─── 설정 ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY") or ""
NEWSAPI_KEY       = os.environ.get("NEWSAPI_KEY") or ""
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_JP_HOUSING") or ""
GITHUB_PAGES_URL  = os.environ.get("GITHUB_PAGES_URL") or "https://seetheskyeric.github.io/japan-insurance-news-bot/japan-housing.html"

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# 이이다 그룹 CI (base64 임베드, 흰 배경 합성·폭 300px 최적화)
IIDA_LOGO_DATA_URI = "data:image/png;base64,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"

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
    ("iida",     "🏠 이이다 그룹 관련",       "#52525B"),
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
    - 발행일 정보가 없거나 파싱에 실패하면 False(제외). 발행일이 확인되지 않는
      기사(오래된 PR 기사 등)가 새어 들어오는 것을 막는다.
    """
    if not published_parsed:
        return False
    try:
        pub_dt = datetime.fromtimestamp(time.mktime(published_parsed))
        return pub_dt >= (datetime.now() - timedelta(days=days))
    except Exception:
        return False

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

# ─── URL 변환 (Google News 링크 → 실제 원문 URL) ──────────────────────────────
def resolve_google_news_url(url, max_attempts=3):
    """
    Google News 링크(news.google.com/.../articles/CBMi...)에는 원문 URL이 직접
    들어있지 않아, 브라우저에서 열면 '리디렉션 알림 / 잘못된 웹 주소' 오류가 난다.
    googlenewsdecoder로 실제 원문 URL을 조회해 반환한다. 일시적 실패(레이트리밋 등)에
    대비해 최대 max_attempts회 재시도하고, 그래도 실패하면 원본을 그대로 둔다.
    (후보 전체가 아니라 '최종 선정 기사'에만 적용해 호출 수를 줄인다)
    """
    if "news.google.com" not in url:
        return url
    last_msg = "알 수 없음"
    for attempt in range(max_attempts):
        try:
            res = gnewsdecoder(url, interval=1)
            if res.get("status") and res.get("decoded_url"):
                return res["decoded_url"]
            last_msg = res.get("message", "알 수 없음")
        except Exception as e:
            last_msg = str(e)
        if attempt < max_attempts - 1:
            time.sleep(2)
    print(f"  [URL 디코드 최종 실패] {last_msg}")
    return url

def resolve_selected_urls(news_data, iida_news):
    """최종 선정된 기사들의 Google News 링크만 원문 URL로 변환한다."""
    print("\n[URL 변환] 선정 기사 링크를 원문으로 변환 중...")
    top = news_data.get("top")
    if isinstance(top, dict) and top.get("url"):
        top["url"] = resolve_google_news_url(top["url"])
    for key in ["housing", "mortgage", "proptech"]:
        for item in (news_data.get(key) or []):
            if item.get("url"):
                item["url"] = resolve_google_news_url(item["url"])
    for item in (iida_news or []):
        if item.get("url"):
            item["url"] = resolve_google_news_url(item["url"])
    print("  ✅ URL 변환 완료")

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
        if key == "iida":
            rows += f'<tr><td style="background:white;padding:16px 16px 8px;"><img src="{IIDA_LOGO_DATA_URI}" alt="Iida Group Holdings" style="height:30px;display:block;"></td></tr>'
        for item in items:
            title_style = (
                "color:#D97706;font-weight:bold;font-size:17px;text-decoration:none;line-height:1.5;"
                if key == "top" else
                "color:#3F3F46;font-weight:bold;font-size:15px;text-decoration:none;line-height:1.5;"
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

    # 최종 선정 기사의 Google News 링크를 실제 원문 URL로 변환
    resolve_selected_urls(news_data, iida_news)

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
