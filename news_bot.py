# -*- coding: utf-8 -*-
# ============================================================
# HabitFactory 일본 보험뉴스 자동 발송 스크립트
# 매일 아침 8시(KST) 자동 실행
# ============================================================
# pip install anthropic requests beautifulsoup4
# ============================================================

import os, json, re, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
import anthropic

# ── 환경변수 ─────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
SENT_HISTORY_FILE = "sent_news_history.json"
GITHUB_PAGES_URL  = os.environ.get("GITHUB_PAGES_URL", "")
JST = timezone(timedelta(hours=9))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
# ─────────────────────────────────────────────────────────────

# ── RSS 검색어 (정밀 설계) ───────────────────────────────────
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


# ═══════════════════════════════════════════════════════════
# 소스 1: Google News RSS
# ═══════════════════════════════════════════════════════════
def resolve_url(url: str) -> str:
    try:
        res = requests.get(url, allow_redirects=True, timeout=6, headers=HEADERS, stream=True)
        return res.url
    except Exception:
        return url


def fetch_rss(query: str, max_items=8, days=7) -> list[dict]:
    encoded = quote(query, safe="")
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"    ⚠️ RSS 실패 ({query[:20]}): {e}")
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
# 소스 2: 保険毎日新聞 (homai.co.jp) — 오늘의 종이면 크롤링
# ═══════════════════════════════════════════════════════════
def fetch_homai() -> list[dict]:
    """保険毎日新聞 메인 페이지에서 '오늘의 종이면' 헤드라인 추출"""
    url = "https://homai.co.jp/"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        res.encoding = res.apparent_encoding
    except Exception as e:
        print(f"    ⚠️ homai.co.jp 접속 실패: {e}")
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    items = []
    today = datetime.now(JST).strftime("%Y/%m/%d")

    # 오늘의 종이면 영역에서 텍스트 추출
    # 페이지 전체에서 면(面) 정보가 포함된 텍스트를 찾음
    for el in soup.find_all(["p", "div", "li", "span", "a"]):
        text = el.get_text(strip=True)
        # "N면" 패턴이 있는 헤드라인을 추출 (1面, 2面, 3面...)
        if re.match(r"^[0-9０-９]{1,2}面", text) and len(text) > 5:
            # 면 번호 제거하고 제목만 추출
            title = re.sub(r"^[0-9０-９]{1,2}面\s*", "", text).strip()
            if title and len(title) > 3:
                link = el.get("href") or el.find_parent("a")
                href = ""
                if link:
                    href = link if isinstance(link, str) else (link.get("href") or "")
                    if href and not href.startswith("http"):
                        href = "https://homai.co.jp" + href

                items.append({
                    "title": title,
                    "url": href or "https://homai.co.jp/",
                    "pub": today,
                    "source": "保険毎日新聞",
                })

    # 면 패턴으로 못 찾으면, 일반 기사 링크에서 추출
    if not items:
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            # 보험 관련 키워드가 포함된 링크
            if any(kw in text for kw in ["保険", "損保", "生保", "代理店", "金融庁"]) and len(text) > 8:
                href = a["href"]
                if not href.startswith("http"):
                    href = "https://homai.co.jp" + href
                items.append({
                    "title": text,
                    "url": href,
                    "pub": today,
                    "source": "保険毎日新聞",
                })

    print(f"    保険毎日新聞: {len(items)}건 수집")
    return items


# ═══════════════════════════════════════════════════════════
# 소스 3: inswatch (inswatch.co.jp) — 최신호 목차 크롤링
# ═══════════════════════════════════════════════════════════
def fetch_inswatch() -> list[dict]:
    """inswatch 메인 페이지에서 최신호 목차 추출"""
    url = "https://www.inswatch.co.jp/"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        res.encoding = res.apparent_encoding
    except Exception as e:
        print(f"    ⚠️ inswatch.co.jp 접속 실패: {e}")
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    items = []
    today = datetime.now(JST).strftime("%Y/%m/%d")

    # 최신호 목차에서 【N】패턴의 기사 제목 추출
    page_text = soup.get_text()
    # 【0】～【9】패턴으로 기사 분리
    pattern = r"【[０-９0-9]+】(.+?)(?=【[０-９0-9]+】|執筆者|$)"
    matches = re.findall(pattern, page_text, re.DOTALL)

    for match in matches:
        # 제목과 부제 추출 (＝...＝ 패턴)
        title_match = re.search(r"＝(.+?)＝", match)
        if title_match:
            title = title_match.group(1).strip()
        else:
            title = match.strip()[:100]

        # 줄바꿈/공백 정리
        title = re.sub(r"\s+", " ", title).strip()

        if title and len(title) > 3:
            items.append({
                "title": title,
                "url": "https://www.inswatch.co.jp/",
                "pub": today,
                "source": "inswatch",
            })

    print(f"    inswatch: {len(items)}건 수집")
    return items


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


def select_and_translate(articles: list[dict], sent_keys: list[str]) -> dict:
    exclude_block = ""
    if sent_keys:
        exclude_block = "Exclude these already-sent URLs:\n" + "\n".join(sent_keys[-30:])

    slim = [
        {"i": i, "t": a["title"], "u": a["url"], "s": a["source"], "p": a["pub"]}
        for i, a in enumerate(articles[:50])  # 전문매체 추가로 50건까지 확장
    ]

    prompt = f"""You are a Japanese insurance news analyst.
From the articles below, select the most important articles and categorize them.

Categories (select EXACTLY up to 3 articles per category, total max 12 articles):
- agency: 보험대리점 관련 (agency M&A, management, sales channels) — EXCLUDE bank/banking articles (銀行, バンク, 信用金庫, 銀行窓販, 窓口販売)
- insurtech: InsureTech 관련 (AI, digital, startups, tech innovation in INSURANCE industry only)
- insurer: 보험사 관련 (insurance company management, products, financials)
- regulation: 규제 관련 (FSA rules, legal changes, compliance, government policy)

Selection priority criteria (higher = more important):
★★★ HIGHEST PRIORITY:
  - Regulatory changes by FSA (金融庁) that affect insurance sales or agency operations
  - Large-scale M&A or consolidation among insurance companies or agencies
  - Industry-wide statistics or reports showing major market shifts

★★ HIGH PRIORITY:
  - InsureTech products/services adopted by insurers or agencies (MUST be insurance-specific)
  - AI or data-driven solutions in insurance underwriting, claims, or distribution
  - Major funding rounds by InsureTech startups
  - Partnerships between InsureTechs and major insurers

★ MEDIUM PRIORITY:
  - Financial results of major insurers with notable changes
  - New distribution channel strategies
  - Customer behavior shifts affecting insurance demand

AVOID:
  - General IT/tech news NOT specific to insurance industry
  - Minor local events or single-branch news
  - Pure PR without business substance

IMPORTANT: Articles from 保険毎日新聞 and inswatch are from specialized insurance media — prioritize these as they are highly relevant.

For each selected article, also find the best alternative free source:
- alt_url: URL of a free alternative article on the same topic
- If no good alternative exists, set alt_url to same as url
- alt_source: name of the alternative source

Rules:
- Each category MUST have at least 1 article, MAX 3 articles
- For agency category: EXCLUDE any articles about banks (銀行, 信用金庫, 窓口販売)
- Keep original URL exactly as given
- title_ko: Korean translation of title
- summary_ko: one short Korean sentence (NO pipe character "|")
{exclude_block}

Articles:
{json.dumps(slim, ensure_ascii=False)}

Output pipe-separated lines only (no header, no blank lines, no markdown):
CATEGORY|RANK|TITLE_JA|TITLE_KO|SUMMARY_KO|SOURCE|URL|PUBLISHED|ALT_URL|ALT_SOURCE

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

        url     = parts[6].strip()
        alt_url = parts[8].strip() if len(parts) > 8 and parts[8].strip() else url
        alt_src = parts[9].strip() if len(parts) > 9 and parts[9].strip() else ""

        news_list.append({
            "category":   normalize_category(parts[0]),
            "rank":       int(parts[1].strip()) if parts[1].strip().isdigit() else len(news_list) + 1,
            "title_ja":   parts[2].strip(),
            "title_ko":   parts[3].strip(),
            "summary_ko": parts[4].strip(),
            "source":     parts[5].strip(),
            "url":        url,
            "published":  parts[7].strip() if len(parts) > 7 else today,
            "alt_url":    alt_url if alt_url != url else "",
            "alt_source": alt_src,
        })

    if not news_list:
        raise ValueError(f"파싱 실패:\n{raw}")

    # 후처리: agency에서 은행 관련 제외
    news_list = [
        n for n in news_list
        if not (n["category"] == "agency" and any(
            kw in (n["title_ja"] + n["title_ko"] + n["summary_ko"])
            for kw in BANK_EXCLUDE_KEYWORDS
        ))
    ]

    # 후처리: 카테고리별 최대 3건 제한
    filtered = []
    cat_count = {}
    for n in news_list:
        cat = n["category"]
        cat_count[cat] = cat_count.get(cat, 0) + 1
        if cat_count[cat] <= 3:
            filtered.append(n)
    news_list = filtered

    return {
        "fetch_date": datetime.now(JST).strftime("%Y年%m月%d日"),
        "news": news_list,
    }


# ── 이력 관리 ────────────────────────────────────────────────
def load_sent_history() -> list[str]:
    if os.path.exists(SENT_HISTORY_FILE):
        with open(SENT_HISTORY_FILE) as f:
            return json.load(f)
    return []


def save_sent_history(history: list[str]):
    with open(SENT_HISTORY_FILE, "w") as f:
        json.dump(history[-200:], f, ensure_ascii=False, indent=2)


# ── HTML 생성 ────────────────────────────────────────────────
def build_html(data: dict, for_web=False) -> str:
    rows = ""
    for key, label, color in CATS:
        items = [n for n in data["news"] if n["category"] == key]
        if not items:
            continue
        rows += f'<tr><td colspan="2" style="background:{color};color:white;padding:10px 16px;font-weight:bold;font-size:15px;">{label}</td></tr>'
        for item in items:
            alt_link = ""
            if item.get("alt_url"):
                alt_src = item.get("alt_source") or "무료 기사"
                alt_link = f'<br><a href="{item["alt_url"]}" style="color:#059669;font-size:12px;text-decoration:none;">🔓 유사 무료 기사 보기 ({alt_src}) →</a>'
            rows += f"""<tr style="border-bottom:1px solid #eee;">
  <td style="padding:14px 16px;vertical-align:top;">
    <a href="{item['url']}" style="color:#1D4ED8;font-weight:bold;font-size:15px;text-decoration:none;line-height:1.5;">{item['title_ko']}</a><br>
    <span style="color:#6B7280;font-size:13px;">🇯🇵 {item['title_ja']}</span><br>
    <span style="color:#9CA3AF;font-size:12px;">📅 {item['published']} · 📰 {item['source']}</span>
    {alt_link}<br>
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
    print("  ✅ docs/index.html 저장 완료")


# ── Slack 발송 ───────────────────────────────────────────────
def send_slack(data: dict, page_url: str):
    if not SLACK_WEBHOOK_URL:
        print("  ⏭ SLACK_WEBHOOK_URL 미설정 — 건너뜀")
        return

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🇯🇵 일본 보험뉴스 — {data['fetch_date']}"}},
    ]
    for key, label in CAT_SLACK_LABELS.items():
        items = [n for n in data["news"] if n["category"] == key]
        if not items:
            continue
        lines = [f"*{label}* ({len(items)}건)"]
        for i, item in enumerate(items, 1):
            lines.append(f"  {i}. <{item['url']}|{item['title_ko']}>")
            lines.append(f"      _{item['summary_ko']}_")
        lines.append("")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})

    if page_url:
        blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"📰 <{page_url}|전체 기사 헤드라인 보기 →>"}})

    try:
        res = requests.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=10)
        print(f"  ✅ 슬랙 발송 {'완료' if res.status_code == 200 else '실패: ' + str(res.status_code)}")
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


# ── 메인 ─────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print(f"🇯🇵 일본 보험뉴스 봇 — {datetime.now(JST).strftime('%Y-%m-%d %H:%M KST')}")
    print("=" * 50)

    all_articles, seen_urls = [], set()

    # ① 전문매체 크롤링 (우선순위 높음)
    print("\n📰 전문매체 크롤링...")
    for a in fetch_homai():
        if a["url"] not in seen_urls:
            all_articles.append(a); seen_urls.add(a["url"])

    for a in fetch_inswatch():
        if a["title"] not in {x["title"] for x in all_articles}:  # 제목 기준 중복 제거
            all_articles.append(a)

    # ② Google News RSS
    print("\n📡 Google News RSS 수집...")
    for q in RSS_QUERIES:
        for a in fetch_rss(q, max_items=8, days=7):
            if a["url"] not in seen_urls:
                all_articles.append(a); seen_urls.add(a["url"])

    print(f"\n  📊 총 {len(all_articles)}개 기사 수집")
    if not all_articles:
        print("  ⚠️ 수집된 기사 없음")
        send_slack_no_news()
        return

    # ③ AI 선별/번역
    # sent_keys = load_sent_history()  # TODO: 테스트 완료 후 활성화
    sent_keys = []
    print("\n🤖 AI 선별/번역 중...")
    data = select_and_translate(all_articles, sent_keys)

    new_keys = [n.get("url") or n.get("title_ja", "") for n in data["news"]]
    # data["news"] = [n for n in data["news"]
    #                 if (n.get("url") or n.get("title_ja")) not in set(sent_keys)]

    if not data["news"]:
        print("  ⚠️ 새로운 뉴스 없음")
        send_slack_no_news()
        return

    # ④ 결과 요약
    print(f"\n📋 선별 결과:")
    for key, label in CAT_SLACK_LABELS.items():
        cnt = len([n for n in data["news"] if n["category"] == key])
        print(f"  {label}: {cnt}건")
    print(f"  합계: {len(data['news'])}건")

    # ⑤ 저장 및 발송
    with open("news_cache.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("\n  ✅ news_cache.json 저장")

    save_web_page(data)

    print("\n📤 Slack 발송...")
    send_slack(data, GITHUB_PAGES_URL)

    save_sent_history(sent_keys + new_keys)
    print(f"  📝 이력 저장 ({len(sent_keys + new_keys)}건)")
    print("\n✅ 완료!")


if __name__ == "__main__":
    main()
