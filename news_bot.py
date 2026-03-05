# -*- coding: utf-8 -*-
# ============================================================
# HabitFactory 일본 보험뉴스 자동 발송 스크립트
# 매일 아침 8시(KST) 자동 실행
# ============================================================
# pip install anthropic requests beautifulsoup4
# ============================================================

import os, json, re, requests
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from urllib.parse import quote
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
import anthropic

# ── 환경변수 ─────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
NEWSAPI_KEY       = os.environ.get("NEWSAPI_KEY", "")
SENT_HISTORY_FILE = "docs/sent_news_history.json"
GITHUB_PAGES_URL  = os.environ.get("GITHUB_PAGES_URL", "")
JST = timezone(timedelta(hours=9))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
# ─────────────────────────────────────────────────────────────

# ── 검색어 ───────────────────────────────────────────────────
AGENCY_QUERIES = [
    "保険代理店 M&A OR 買収 OR 統合 OR 事業承継",
    "乗合代理店 OR 保険ショップ OR 来店型保険ショップ",
    "保険代理店 手数料 OR 経営改善 OR コンプライアンス OR 行政処分",
]
INSURTECH_QUERIES = [
    "インシュアテック",
    "保険 AI活用 OR データ活用",
    "保険 スタートアップ OR 資金調達",
    "保険 デジタル募集 OR オンライン保険 OR 保険アプリ",
]
INSURER_QUERIES = [
    "生命保険 OR 損害保険 決算 OR 新商品 OR 提携",
    "東京海上 OR 損保ジャパン OR 三井住友海上",
    "日本生命 OR 第一生命 OR 大手生保",
]
REGULATION_QUERIES = [
    "金融庁 保険 OR 代理店 監督 OR 行政処分 OR 検査",
    "保険業法 改正 OR 顧客本位 OR 手数料開示",
]
RSS_QUERIES = AGENCY_QUERIES + INSURTECH_QUERIES + INSURER_QUERIES + REGULATION_QUERIES

NEWSAPI_QUERIES = [
    "保険代理店",
    "インシュアテック OR 保険 AI",
    "生命保険 OR 損害保険",
    "金融庁 保険",
    "保険 DX OR デジタル",
]

# ── 카테고리 설정 ────────────────────────────────────────────
CATS = [
    ("agency",     "🏢 보험대리점 관련", "#2E86AB"),
    ("insurtech",  "💡 InsureTech 관련", "#8B5CF6"),
    ("insurer",    "🏦 보험사 관련",     "#059669"),
    ("regulation", "⚖️ 규제 관련",       "#DC2626"),
]
CAT_SLACK_LABELS = {
    "agency":     "🏢 보험대리점",
    "insurtech":  "💡 InsureTech",
    "insurer":    "🏦 보험사",
    "regulation": "⚖️ 규제",
}
BANK_EXCLUDE_KEYWORDS = [
    "銀行", "バンク", "bank", "信用金庫", "信金", "銀行窓販", "窓口販売",
]
KOREAN_MEDIA_EXCLUDE = [
    "조선일보", "중앙일보", "동아일보", "한국경제", "매일경제", "연합뉴스",
    "한겨레", "경향신문", "서울신문", "아시아경제", "뉴시스", "뉴스1",
    "Chosun", "JoongAng", "Dong-A", "Hankyoreh", "Yonhap",
    "Korea Herald", "Korea Times", "KBS", "MBC", "SBS",
    "코리아", "korea", "Korean", "한국", "聯合ニュース",
    "朝鮮日報", "中央日報", "東亜日報", "韓国経済",
    ".kr/", "chosun.com", "joongang.co", "donga.com",
    "hankyung.com", "mk.co.kr", "yna.co.kr", "hani.co.kr",
]


# ═══════════════════════════════════════════════════════════
# 유사도 체크 (방법 A)
# ═══════════════════════════════════════════════════════════
def is_similar_title(t1: str, t2: str, threshold=0.8) -> bool:
    return SequenceMatcher(None, t1, t2).ratio() >= threshold

def is_duplicate(article_title: str, sent_titles: list[str], threshold=0.8) -> bool:
    for sent in sent_titles:
        if is_similar_title(article_title, sent, threshold):
            return True
    return False


# ═══════════════════════════════════════════════════════════
# 소스 A: Google News RSS 검색
# ═══════════════════════════════════════════════════════════
def resolve_url(url: str) -> str:
    try:
        res = requests.get(url, allow_redirects=True, timeout=6, headers=HEADERS, stream=True)
        return res.url
    except Exception:
        return url

def is_url_alive(url: str) -> bool:
    if not url:
        return False
    try:
        res = requests.head(url, headers=HEADERS, timeout=6, allow_redirects=True)
        return res.status_code < 400
    except Exception:
        try:
            res = requests.get(url, headers=HEADERS, timeout=6, allow_redirects=True, stream=True)
            return res.status_code < 400
        except Exception:
            return False

def fetch_google_rss(query: str, max_items=8, days=7) -> list[dict]:
    encoded = quote(query, safe="")
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"    ⚠️ Google RSS 실패 ({query[:30]}): {e}")
        return []

    root = ET.fromstring(res.content)
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for item in root.findall(".//item")[:max_items]:
        title   = item.findtext("title") or ""
        title   = re.sub(r" - [^-]+$", "", title).strip()
        link    = item.findtext("link") or ""
        pub_str = item.findtext("pubDate") or ""
        try:
            pub_dt = parsedate_to_datetime(pub_str)
            if pub_dt < cutoff:
                continue
            pub = pub_dt.strftime("%Y/%m/%d")
        except Exception:
            pub = pub_str[:10]
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None else "Google News"
        real_url = resolve_url(link) if link else ""
        items.append({"title": title, "url": real_url, "pub": pub, "source": source})
    return items


# ═══════════════════════════════════════════════════════════
# 소스 B: NewsAPI.org
# ═══════════════════════════════════════════════════════════
def fetch_newsapi(query: str, max_items=10, days=7) -> list[dict]:
    if not NEWSAPI_KEY:
        return []
    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query, "language": "ja", "from": from_date,
        "sortBy": "relevancy", "pageSize": max_items, "apiKey": NEWSAPI_KEY,
    }
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        print(f"    ⚠️ NewsAPI 실패 ({query[:30]}): {e}")
        return []

    items = []
    for art in data.get("articles", []):
        title = (art.get("title") or "").strip()
        if not title or title == "[Removed]":
            continue
        source = art.get("source", {}).get("name", "NewsAPI")
        pub_str = art.get("publishedAt", "")[:10].replace("-", "/")
        items.append({"title": title, "url": art.get("url", ""), "pub": pub_str, "source": source})
    return items


# ═══════════════════════════════════════════════════════════
# 소스 C: 전문매체 헤드라인 크롤링
# ═══════════════════════════════════════════════════════════
def crawl_homai_headlines() -> list[str]:
    url = "https://homai.co.jp/"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        res.encoding = res.apparent_encoding
    except Exception as e:
        print(f"    ⚠️ homai.co.jp 실패: {e}")
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    headlines = []
    for el in soup.find_all(["p", "div", "li", "span", "a"]):
        text = el.get_text(strip=True)
        if re.match(r"^[0-9０-９]{1,2}面", text) and len(text) > 5:
            title = re.sub(r"^[0-9０-９]{1,2}面\s*", "", text).strip()
            if title and len(title) > 5:
                headlines.append(title)
    if not headlines:
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            if any(kw in text for kw in ["保険", "損保", "生保", "代理店", "金融庁"]) and len(text) > 8:
                headlines.append(text)
    print(f"    保険毎日新聞: {len(headlines)}건 헤드라인")
    return headlines

def crawl_inswatch_headlines() -> list[str]:
    url = "https://www.inswatch.co.jp/"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        res.encoding = res.apparent_encoding
    except Exception as e:
        print(f"    ⚠️ inswatch.co.jp 실패: {e}")
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    headlines = []
    page_text = soup.get_text()
    pattern = r"【[０-９0-9]+】(.+?)(?=【[０-９0-9]+】|執筆者|$)"
    matches = re.findall(pattern, page_text, re.DOTALL)
    for match in matches:
        title_match = re.search(r"＝(.+?)＝", match)
        title = title_match.group(1).strip() if title_match else match.strip()[:100]
        title = re.sub(r"\s+", " ", title).strip()
        if title and len(title) > 5:
            headlines.append(title)
    print(f"    inswatch: {len(headlines)}건 헤드라인")
    return headlines

def search_by_headline(headline: str) -> list[dict]:
    q = headline[:40]
    results = fetch_google_rss(q, max_items=3, days=14)
    if NEWSAPI_KEY:
        results += fetch_newsapi(q, max_items=3, days=14)
    return results


# ═══════════════════════════════════════════════════════════
# 이력 저장/로드 — URL + 제목 분리 관리
# ═══════════════════════════════════════════════════════════
def load_sent_history() -> dict:
    if os.path.exists(SENT_HISTORY_FILE):
        with open(SENT_HISTORY_FILE) as f:
            data = json.load(f)
            if isinstance(data, list):  # 기존 포맷 호환
                return {"urls": data, "titles": []}
            return data
    return {"urls": [], "titles": []}

def save_sent_history(history: dict):
    history["urls"]   = history["urls"][-200:]
    history["titles"] = history["titles"][-200:]
    os.makedirs("docs", exist_ok=True)
    with open(SENT_HISTORY_FILE, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════
# AI 선별/번역
# ═══════════════════════════════════════════════════════════
def normalize_category(cat: str) -> str:
    cat = cat.lower().strip()
    if any(x in cat for x in ["agency", "代理店", "대리점"]):
        return "agency"
    if any(x in cat for x in ["insur", "tech", "디지털", "digital"]):
        return "insurtech"
    if any(x in cat for x in ["insurer", "company", "보험사", "生保", "損保"]):
        return "insurer"
    if any(x in cat for x in ["regul", "規制", "규제", "fsa", "법", "금융"]):
        return "regulation"
    return cat

def select_and_translate(articles: list[dict], sent_history: dict) -> dict:
    sent_urls   = sent_history.get("urls", [])
    sent_titles = sent_history.get("titles", [])

    # 방법 B: AI 프롬프트에 이미 발송된 URL + 제목 모두 전달
    exclude_block = ""
    if sent_urls:
        exclude_block += "Exclude these already-sent URLs:\n" + "\n".join(sent_urls[-60:]) + "\n"
    if sent_titles:
        exclude_block += "Exclude articles with titles similar to these already-sent titles:\n" + "\n".join(sent_titles[-60:])

    slim = [
        {"i": i, "t": a["title"], "u": a["url"], "s": a["source"], "p": a["pub"]}
        for i, a in enumerate(articles[:50])
    ]

    prompt = f"""You are a Japanese insurance news analyst.
From the articles below, select the most important articles and categorize them.

Categories (select EXACTLY up to 3 articles per category, total max 12):
- agency: 보험대리점 관련 — EXCLUDE bank articles (銀行, バンク, 信用金庫, 窓口販売)
- insurtech: InsureTech 관련 (MUST be insurance-specific, not general IT)
- insurer: 보험사 관련
- regulation: 규제 관련

Selection priority:
★★★ Regulatory changes by FSA, large M&A, industry-wide shifts
★★  InsureTech adoption, AI/data in insurance, major funding
★    Insurer financials, distribution strategies

AVOID: General IT news, minor local events, pure PR

Rules:
- Each category: 1-3 articles
- agency: EXCLUDE bank articles
- title_ko: Korean translation
- summary_ko: one short Korean sentence (NO pipe "|")
- Keep original URL exactly
{exclude_block}

Articles:
{json.dumps(slim, ensure_ascii=False)}

Output pipe-separated lines only (no header, no blank lines, no markdown):
CATEGORY|RANK|TITLE_JA|TITLE_KO|SUMMARY_KO|SOURCE|URL|PUBLISHED

Output only the selected lines, nothing else."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    for attempt in range(2):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            print(f"  API 응답 (attempt {attempt+1}):\n  {raw[:400]}...")
            break
        except Exception as e:
            print(f"  ⚠️ API 실패 (attempt {attempt+1}): {e}")
            if attempt == 1:
                raise

    news_list = []
    today = datetime.now(JST).strftime("%Y/%m/%d")
    for line in raw.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        news_list.append({
            "category":   normalize_category(parts[0]),
            "rank":       int(parts[1].strip()) if parts[1].strip().isdigit() else len(news_list) + 1,
            "title_ja":   parts[2].strip(),
            "title_ko":   parts[3].strip(),
            "summary_ko": parts[4].strip(),
            "source":     parts[5].strip(),
            "url":        parts[6].strip(),
            "published":  parts[7].strip() if len(parts) > 7 else today,
        })

    if not news_list:
        raise ValueError(f"파싱 실패:\n{raw}")

    # 후처리: agency 은행 제외
    news_list = [
        n for n in news_list
        if not (n["category"] == "agency" and any(
            kw in (n["title_ja"] + n["title_ko"] + n["summary_ko"])
            for kw in BANK_EXCLUDE_KEYWORDS
        ))
    ]

    # 방법 A: URL 완전일치 OR 제목 유사도 80% 이상이면 중복 제외
    sent_url_set = set(sent_urls)
    news_list = [
        n for n in news_list
        if n.get("url") not in sent_url_set
        and not is_duplicate(n.get("title_ja", ""), sent_titles, threshold=0.8)
    ]

    # 카테고리별 최대 3건
    filtered, cat_count = [], {}
    for n in news_list:
        c = n["category"]
        cat_count[c] = cat_count.get(c, 0) + 1
        if cat_count[c] <= 3:
            filtered.append(n)

    return {"fetch_date": datetime.now(JST).strftime("%Y年%m月%d日"), "news": filtered}


# ── HTML/Slack ───────────────────────────────────────────────
def build_html(data: dict, for_web=False) -> str:
    rows = ""
    for key, label, color in CATS:
        items = [n for n in data["news"] if n["category"] == key]
        if not items:
            continue
        rows += f'<tr><td colspan="2" style="background:{color};color:white;padding:10px 16px;font-weight:bold;font-size:15px;">{label}</td></tr>'
        for item in items:
            rows += f"""<tr style="border-bottom:1px solid #eee;">
  <td style="padding:14px 16px;vertical-align:top;">
    <a href="{item['url']}" style="color:#1D4ED8;font-weight:bold;font-size:15px;text-decoration:none;line-height:1.5;">{item['title_ko']}</a><br>
    <span style="color:#6B7280;font-size:13px;">🇯🇵 {item['title_ja']}</span><br>
    <span style="color:#9CA3AF;font-size:12px;">📅 {item['published']} · 📰 {item['source']}</span><br>
    <div style="background:#F9FAFB;padding:8px 10px;border-radius:6px;margin-top:6px;font-size:13px;color:#374151;">{item['summary_ko']}</div>
  </td>
</tr>"""
    meta = '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">' if for_web else ""
    refresh = '<meta http-equiv="refresh" content="3600">' if for_web else ""
    return f"""<html><head>{meta}{refresh}</head>
<body style="font-family:sans-serif;background:#F0F2F5;padding:20px;margin:0;">
  <div style="max-width:700px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">
    <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:24px 28px;color:white;">
      <h1 style="margin:0;font-size:20px;">🇯🇵 일본 보험뉴스</h1>
      <p style="margin:6px 0 0;opacity:.7;font-size:13px;">HabitFactory Global Team · {data['fetch_date']}</p>
    </div>
    <table style="width:100%;border-collapse:collapse;">{rows}</table>
    <div style="padding:16px;text-align:center;color:#9CA3AF;font-size:12px;">© HabitFactory Global Team</div>
  </div>
</body></html>"""

def save_web_page(data: dict):
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(build_html(data, for_web=True))
    print("  ✅ docs/index.html 저장")

def send_slack(data: dict, page_url: str):
    if not SLACK_WEBHOOK_URL:
        print("  ⏭ SLACK_WEBHOOK_URL 미설정")
        return
    summary_parts = []
    for key, label in CAT_SLACK_LABELS.items():
        cnt = len([n for n in data["news"] if n["category"] == key])
        if cnt:
            summary_parts.append(f"{label} {cnt}건")
    summary = " · ".join(summary_parts)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"🇯🇵 *일본 보험뉴스* — {data['fetch_date']}\n{summary}\n\n<{page_url}|📰 기사 헤드라인 보기 →>"}},
    ]
    try:
        res = requests.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=10)
        print(f"  ✅ 슬랙 {'완료' if res.status_code == 200 else '실패: ' + str(res.status_code)}")
    except Exception as e:
        print(f"  ⚠️ 슬랙 에러: {e}")

def send_slack_no_news():
    if not SLACK_WEBHOOK_URL:
        return
    today = datetime.now(JST).strftime("%Y年%m月%d日")
    requests.post(SLACK_WEBHOOK_URL, json={
        "blocks": [{"type": "section", "text": {"type": "mrkdwn",
            "text": f"🇯🇵 *일본 보험뉴스* — {today}\n\n⚠️ 오늘은 선별된 보험 뉴스가 없습니다."}}]
    }, timeout=10)


# ═══════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════
def main():
    print("=" * 50)
    print(f"🇯🇵 일본 보험뉴스 봇 — {datetime.now(JST).strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    all_articles, seen_urls, seen_titles = [], set(), set()

    def is_korean_media(a):
        src = a.get("source", "") + " " + a.get("url", "")
        return any(kw in src for kw in KOREAN_MEDIA_EXCLUDE)

    def add(a):
        if a["url"] and a["url"] not in seen_urls and a["title"] not in seen_titles:
            if is_korean_media(a):
                return
            all_articles.append(a)
            seen_urls.add(a["url"])
            seen_titles.add(a["title"])

    # STEP 1: 전문매체 헤드라인 크롤링
    print("\n📰 STEP 1: 전문매체 헤드라인 크롤링...")
    headlines = crawl_homai_headlines() + crawl_inswatch_headlines()
    if headlines:
        print(f"\n🔍 STEP 1b: 헤드라인 → Google+NewsAPI 검색 ({len(headlines)}건)...")
        for hl in headlines:
            for a in search_by_headline(hl):
                add(a)
        print(f"    전문매체 기반: {len(all_articles)}건 확보")

    # STEP 2: Google News RSS 키워드 검색
    print(f"\n📡 STEP 2: Google News 키워드 검색...")
    for q in RSS_QUERIES:
        for a in fetch_google_rss(q, max_items=8, days=7):
            add(a)
    print(f"    Google News 후: {len(all_articles)}건")

    # STEP 3: NewsAPI 키워드 검색
    if NEWSAPI_KEY:
        print(f"\n📡 STEP 3: NewsAPI 키워드 검색...")
        for q in NEWSAPI_QUERIES:
            for a in fetch_newsapi(q, max_items=10, days=7):
                add(a)
        print(f"    NewsAPI 후: {len(all_articles)}건")
    else:
        print("\n  ⏭ NEWSAPI_KEY 미설정 — NewsAPI 건너뜀")

    print(f"\n  📊 총 {len(all_articles)}개 기사 수집")
    if not all_articles:
        print("  ⚠️ 수집된 기사 없음")
        send_slack_no_news()
        return

    # STEP 4: AI 선별/번역 (방법 A+B 중복 제거 적용)
    sent_history = load_sent_history()
    print(f"\n🤖 STEP 4: AI 선별/번역... (이력 URL {len(sent_history['urls'])}건, 제목 {len(sent_history['titles'])}건)")
    data = select_and_translate(all_articles, sent_history)

    if not data["news"]:
        print("  ⚠️ 뉴스 없음")
        send_slack_no_news()
        return

    print(f"\n📋 선별 결과:")
    for key, label in CAT_SLACK_LABELS.items():
        cnt = len([n for n in data["news"] if n["category"] == key])
        print(f"  {label}: {cnt}건")
    print(f"  합계: {len(data['news'])}건")

    # URL 유효성 검증
    print(f"\n🔗 URL 검증 중...")
    valid_news = []
    for n in data["news"]:
        if is_url_alive(n["url"]):
            valid_news.append(n)
        else:
            print(f"    ❌ 제외 (404): {n['title_ja'][:40]}")
    data["news"] = valid_news
    print(f"  검증 후: {len(data['news'])}건")

    # STEP 5: 저장 및 발송
    with open("news_cache.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("\n  ✅ news_cache.json 저장")
    save_web_page(data)

    print("\n📤 STEP 5: Slack 발송...")
    send_slack(data, GITHUB_PAGES_URL)

    # 이력 업데이트: URL + 제목 분리 저장
    new_urls   = [n["url"]      for n in data["news"] if n.get("url")]
    new_titles = [n["title_ja"] for n in data["news"] if n.get("title_ja")]
    save_sent_history({
        "urls":   sent_history["urls"]   + new_urls,
        "titles": sent_history["titles"] + new_titles,
    })
    print(f"  📝 이력 저장 (URL {len(sent_history['urls']+new_urls)}건, 제목 {len(sent_history['titles']+new_titles)}건)")
    print("\n✅ 완료!")


if __name__ == "__main__":
    main()
