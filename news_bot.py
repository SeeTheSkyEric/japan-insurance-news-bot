# -*- coding: utf-8 -*-
# ============================================================
# HabitFactory 일본 보험뉴스 자동 발송 스크립트
# 매일 아침 8시(KST) 자동 실행
# ============================================================
# pip install anthropic requests
# ============================================================

import os, json, re, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
import anthropic

# ── 환경변수 ─────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
SENT_HISTORY_FILE = "sent_news_history.json"
GITHUB_PAGES_URL  = os.environ.get("GITHUB_PAGES_URL", "")    # 예: https://seetheskyeric.github.io/japan-insurance-news-bot
# ─────────────────────────────────────────────────────────────

# 일반 RSS 검색어 - 중요 키워드 강화
RSS_QUERIES = [
    "保険代理店 M&A OR 規制 OR 金融庁 OR 経営効率",
    "インシュアテック 資金調達 OR 新サービス OR AI OR 大量導入",
    "生命保険 損害保険 決算 OR 新商品 OR 規制 OR DX",
    "保険業界 DX OR デジタル OR 改革 OR 自動化",
    "金融庁 保険 規制 OR 監督 OR 改正 OR ガイドライン",
    "保険 AI OR データ活用 OR 業務改善 OR コスト削減",
]

# 보험 전문 언론 헤드라인 검색
SPECIALTY_MEDIA_QUERIES = [
    "保険毎日新聞",
    "インシュアランス 保険",
    "日本保険新聞",
    "ニッキン 保険",
]


def resolve_url(url: str) -> str:
    """Google News 리다이렉트 URL → 실제 기사 URL로 변환"""
    try:
        res = requests.get(
            url, allow_redirects=True, timeout=6,
            headers={"User-Agent": "Mozilla/5.0"},
            stream=True  # 본문 다운로드 없이 헤더만
        )
        return res.url
    except:
        return url


def fetch_rss(query: str, max_items=6, days=15) -> list[dict]:
    encoded = quote(query, safe="")
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"RSS 수집 실패 ({query}): {e}")
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
        except:
            pub = pub_str[:10]

        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None else "Google News"

        # 실제 URL로 변환
        real_url = resolve_url(link) if link else ""

        items.append({
            "title": title, "url": real_url,
            "pub": pub, "source": source,
            "hint": query,
        })
    return items


def select_and_translate(articles: list[dict], sent_keys: list[str]) -> dict:
    exclude_block = ""
    if sent_keys:
        exclude_block = "Exclude these already-sent URLs:\n" + "\n".join(sent_keys[-30:])

    slim = [
        {"i": i, "t": a["title"], "u": a["url"], "s": a["source"], "p": a["pub"], "free": a.get("free", True)}
        for i, a in enumerate(articles[:30])
    ]

    prompt = f"""You are a Japanese insurance news analyst.
From the articles below, select the most important articles and categorize them.

Categories:
- agency: 보험대리점 관련 (agency M&A, management, sales channels)
- insurtech: InsureTech 관련 (AI, digital, startups, tech innovation)
- insurer: 보험사 관련 (insurance company management, products, financials)
- regulation: 규제 관련 (FSA rules, legal changes, compliance, government policy)

Selection priority criteria (higher = more important):
★★★ HIGHEST PRIORITY:
  - Regulatory changes by FSA (金融庁) that affect insurance sales or agency operations
  - Large-scale M&A or consolidation among insurance companies or agencies
  - Industry-wide statistics or reports showing major market shifts
  - New laws or compliance requirements with broad industry impact

★★ HIGH PRIORITY:
  - InsureTech products/services newly adopted at scale by insurers or agencies
  - Expected or confirmed mass adoption of digital tools improving operational efficiency
  - AI or data-driven solutions that significantly reduce costs or improve underwriting
  - New insurance products targeting emerging risks (cyber, climate, health tech)
  - Major funding rounds or IPOs by InsureTech startups with insurance industry relevance
  - Management efficiency improvements (cost reduction, automation, workflow reform)
  - Partnerships between InsureTechs and major insurers or agency networks

★ MEDIUM PRIORITY:
  - Financial results (earnings) of major insurers with notable YoY changes
  - New distribution channel strategies or agency network restructuring
  - Customer behavior shifts affecting insurance demand
  - Regional or niche market developments with growth potential

AVOID:
  - Minor local events or single-branch news
  - Pure PR or press releases without business substance
  - Articles without concrete numbers, deals, or policy implications
  - Repetitive coverage of already-known events

IMPORTANT - For each selected article, also find the best alternative free source:
- Search for a similar/related free article covering the same topic
- alt_url: URL of a free alternative article on the same topic (use Google News, NHK, Nikkei free section, Reuters Japan, etc.)
- If no good alternative exists, set alt_url to same as url
- alt_source: name of the alternative source

Rules:
- Each category MUST have at least 1 article
- Include MORE articles per category if they are highly important (max 4 per category)
- Keep original URL exactly as given
- title_ko: Korean translation
- summary_ko: one short Korean sentence (no pipe character)
{exclude_block}

Articles:
{json.dumps(slim, ensure_ascii=False)}

Output pipe-separated lines only (no header, no blank lines):
CATEGORY|RANK|TITLE_JA|TITLE_KO|SUMMARY_KO|SOURCE|URL|PUBLISHED|ALT_URL|ALT_SOURCE

Output only the selected lines, nothing else."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    print(f"API 응답:\n{raw[:600]}")

    news_list = []
    JST = timezone(timedelta(hours=9))
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
        # alt_url이 원본과 같으면 None으로 처리
        news_list.append({
            "category":   parts[0].strip().lower(),
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

    JST = timezone(timedelta(hours=9))
    return {
        "fetch_date": datetime.now(JST).strftime("%Y年%m月%d日"),
        "news": news_list,
    }


def load_sent_history() -> list[str]:
    if os.path.exists(SENT_HISTORY_FILE):
        with open(SENT_HISTORY_FILE) as f:
            return json.load(f)
    return []


def save_sent_history(history: list[str]):
    with open(SENT_HISTORY_FILE, "w") as f:
        json.dump(history[-200:], f, ensure_ascii=False, indent=2)


# ── HTML 생성 (이메일 + 웹페이지 공용) ───────────────────────
CATS = [
    ("agency",    "🏢 보험대리점 관련", "#2E86AB"),
    ("insurtech", "💡 InsureTech 관련", "#8B5CF6"),
    ("insurer",   "🏦 보험사 관련",     "#059669"),
    ("regulation","⚖️ 규제 관련",       "#DC2626"),
]

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
    refresh = '<meta http-equiv="refresh" content="3600">' if for_web else ""  # 웹페이지는 1시간마다 자동 새로고침

    return f"""<html><head>{meta}{refresh}</head>
<body style="font-family:sans-serif;background:#F0F2F5;padding:20px;margin:0;">
  <div style="max-width:700px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">
    <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:24px 28px;color:white;">
      <h1 style="margin:0;font-size:20px;">🇯🇵 일본 보험뉴스 10선</h1>
      <p style="margin:6px 0 0;opacity:.7;font-size:13px;">HabitFactory Global Team · {data['fetch_date']}</p>
    </div>
    <table style="width:100%;border-collapse:collapse;">{rows}</table>
    <div style="padding:16px;text-align:center;color:#9CA3AF;font-size:12px;">© HabitFactory Global Team</div>
  </div>
</body></html>"""


def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = RECV_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, RECV_EMAIL, msg.as_string())
    print(f"✅ 이메일 발송 완료 → {RECV_EMAIL}")


def save_web_page(data: dict):
    """GitHub Pages용 HTML 파일 저장"""
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(build_html(data, for_web=True))
    print("✅ docs/index.html 저장 완료")


def send_slack(fetch_date: str, page_url: str, news_list: list):
    if not SLACK_WEBHOOK_URL:
        print("⏭ SLACK_WEBHOOK_URL 미설정 — 슬랙 발송 건너뜀")
        return

    # 카테고리별 요약
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
                    "text": f"🇯🇵 *오늘의 일본 보험 뉴스* — {fetch_date}{summary}\n\n<{page_url}|📰 전체 기사 헤드라인 보기>"
                }
            }
        ]
    }
    res = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    if res.status_code == 200:
        print("✅ 슬랙 발송 완료")
    else:
        print(f"⚠️ 슬랙 발송 실패: {res.status_code} {res.text}")


def main():
    print("📡 RSS 수집 중...")
    all_articles, seen_urls = [], set()

    # 일반 RSS
    for q in RSS_QUERIES:
        for a in fetch_rss(q, max_items=6, days=15):
            if a["url"] not in seen_urls:
                all_articles.append(a); seen_urls.add(a["url"])

    # 보험 전문 언론 RSS
    print("📰 보험 전문 언론 검색 중...")
    for q in SPECIALTY_MEDIA_QUERIES:
        for a in fetch_rss(q, max_items=4, days=15):
            if a["url"] not in seen_urls:
                all_articles.append(a); seen_urls.add(a["url"])

    print(f"총 {len(all_articles)}개 기사 수집")

    sent_keys = load_sent_history()

    print("🤖 AI 번역/분류 중...")
    data = select_and_translate(all_articles, [])  # 테스트 중: 중복 제외 비활성화

    # 테스트 중: 중복 제거 비활성화
    # new_keys  = [n.get("url") or n.get("title_ja", "") for n in data["news"]]
    # data["news"] = [n for n in data["news"] if (n.get("url") or n.get("title_ja")) not in set(sent_keys)]

    new_keys = [n.get("url") or n.get("title_ja", "") for n in data["news"]]

    if not data["news"]:
        print("⚠️ 뉴스가 없습니다.")
        if SLACK_WEBHOOK_URL:
            requests.post(SLACK_WEBHOOK_URL, json={
                "blocks": [{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"🇯🇵 *오늘의 일본 보험 뉴스* — {today}\n\n⚠️ 오늘은 선별된 보험 뉴스가 없습니다."
                    }
                }]
            }, timeout=10)
        return

    today   = datetime.now().strftime("%Y年%m月%d日")
    subject = f"[HabitFactory] 일본 보험뉴스 10선 — {today}"

    # 이메일 발송 제거됨

    # 결과를 캐시 파일로 저장 (slack_notify.py가 읽어서 발송)
    with open("news_cache.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("✅ news_cache.json 저장 완료")

    # GitHub Pages HTML 저장
    save_web_page(data)

    save_sent_history(sent_keys + new_keys)
    print(f"📝 이력 저장 완료 ({len(sent_keys + new_keys)}건)")


if __name__ == "__main__":
    main()
