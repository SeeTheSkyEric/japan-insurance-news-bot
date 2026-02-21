# -*- coding: utf-8 -*-
# ============================================================
# HabitFactory 일본 보험뉴스 자동 발송 스크립트
# 매일 아침 8시(KST) 자동 실행 → hantaek.hong@habitfactory.co
# ============================================================
# 필요 패키지: pip install anthropic feedparser
# ============================================================

import os, json, re, smtplib, requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from urllib.parse import quote
from xml.etree import ElementTree as ET
import anthropic

# ── 환경변수로 관리 (GitHub Secrets에 등록) ──────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GMAIL_USER        = os.environ["GMAIL_USER"]        # 발신 Gmail 주소
GMAIL_APP_PW      = os.environ["GMAIL_APP_PW"]      # Gmail 앱 비밀번호
RECV_EMAIL        = "hantaek.hong@habitfactory.co"
SENT_HISTORY_FILE = "sent_news_history.json"        # 중복 방지용 이력 파일
# ─────────────────────────────────────────────────────────────

RSS_QUERIES = {
    "top":       "保険 日本 最新",
    "agency":    "保険代理店",
    "insurtech": "インシュアテック 保険 AI デジタル",
    "insurer":   "生命保険 損害保険",
}

def fetch_rss(query: str, max_items=8) -> list[dict]:
    encoded_query = quote(query, safe="")
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers, timeout=10)
    res.raise_for_status()

    root = ET.fromstring(res.content)
    ns = {"media": "http://search.yahoo.com/mrss/"}
    items = []
    for item in root.findall(".//item")[:max_items]:
        title = item.findtext("title") or ""
        title = re.sub(r" - [^-]+$", "", title)
        link  = item.findtext("link") or ""
        pub   = (item.findtext("pubDate") or "")[:16]
        source_el = item.find("source")
        source = source_el.text if source_el is not None else "Google News"
        items.append({"title": title, "url": link, "pub": pub, "source": source, "hint": query})
    return items

def load_sent_history() -> list[str]:
    if os.path.exists(SENT_HISTORY_FILE):
        with open(SENT_HISTORY_FILE) as f:
            return json.load(f)
    return []

def save_sent_history(history: list[str]):
    with open(SENT_HISTORY_FILE, "w") as f:
        json.dump(history[-200:], f, ensure_ascii=False, indent=2)

def select_and_translate(articles: list[dict], sent_keys: list[str]) -> dict:
    exclude_block = ""
    if sent_keys:
        exclude_block = f"\n이미 발송된 항목 (제외 필수):\n" + "\n".join(sent_keys[-60:])

    articles_json = json.dumps(articles, ensure_ascii=False)
    prompt = f"""You are a Japanese insurance industry news analyst.
Select and categorize exactly 10 articles from the input:
- top: 1 (most impactful)
- agency: 3 (保険代理店 M&A, regulation, management)
- insurtech: 3 (AI, digital, InsurTech startups)
- insurer: 3 (生保・損保 management, products, regulation)
{exclude_block}

For each: keep original URL, translate title to Korean, write 2~3 sentence Korean summary.

Input articles:
{articles_json}

Output ONLY valid JSON (no markdown):
{{"fetch_date":"...","news":[{{"category":"top","rank":1,"title_ja":"...","title_ko":"...","summary_ko":"...","source":"...","url":"...","published":"..."}},...(10 items total)]}}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text
    cb = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if cb:
        return json.loads(cb.group(1))
    s, e = raw.find("{"), raw.rfind("}")
    return json.loads(raw[s:e+1])

def build_html_email(data: dict) -> str:
    cats = [
        ("top",       "🏆 TOP 뉴스",       "#FF6B35"),
        ("agency",    "🏢 보험대리점 관련", "#2E86AB"),
        ("insurtech", "💡 인슈어테크 관련", "#8B5CF6"),
        ("insurer",   "🏦 보험사 관련",     "#059669"),
    ]
    rows = ""
    for key, label, color in cats:
        items = [n for n in data["news"] if n["category"] == key]
        if not items: continue
        rows += f'<tr><td colspan="2" style="background:{color};color:white;padding:10px 16px;font-weight:bold;">{label}</td></tr>'
        for item in items:
            rows += f"""
            <tr style="border-bottom:1px solid #eee;">
              <td style="padding:14px 16px;width:28px;text-align:center;font-weight:bold;color:{color};">{item['rank']}</td>
              <td style="padding:14px 16px;">
                <a href="{item['url']}" style="color:#1D4ED8;font-weight:bold;font-size:15px;text-decoration:none;">{item['title_ko']}</a><br>
                <span style="color:#6B7280;font-size:13px;">🇯🇵 {item['title_ja']}</span><br>
                <span style="color:#9CA3AF;font-size:12px;">📅 {item['published']} · 📰 {item['source']}</span><br>
                <div style="background:#F9FAFB;padding:8px 10px;border-radius:6px;margin-top:6px;font-size:13px;color:#374151;">{item['summary_ko']}</div>
              </td>
            </tr>"""
    return f"""
    <html><body style="font-family:sans-serif;background:#F0F2F5;padding:20px;">
      <div style="max-width:680px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
        <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:24px;color:white;">
          <h1 style="margin:0;font-size:20px;">🇯🇵 일본 보험뉴스 10선</h1>
          <p style="margin:6px 0 0;opacity:0.7;font-size:13px;">HabitFactory Global Team · {data['fetch_date']}</p>
        </div>
        <table style="width:100%;border-collapse:collapse;">{rows}</table>
        <div style="padding:16px;text-align:center;color:#9CA3AF;font-size:12px;">
          © HabitFactory Global Team · AI-powered Japan Insurance News Bot
        </div>
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

def main():
    print("📡 RSS 수집 중...")
    all_articles, seen_urls = [], set()
    for articles in [fetch_rss(q) for q in RSS_QUERIES.values()]:
        for a in articles:
            if a["url"] not in seen_urls:
                all_articles.append(a); seen_urls.add(a["url"])

    sent_keys = load_sent_history()

    print("🤖 AI 번역/분류 중...")
    data = select_and_translate(all_articles[:32], sent_keys)

    new_keys = [n.get("url") or n.get("title_ja", "") for n in data["news"]]
    data["news"] = [n for n in data["news"] if (n.get("url") or n.get("title_ja")) not in set(sent_keys)]

    if not data["news"]:
        print("⚠️ 모든 뉴스가 이미 발송됐습니다.")
        return

    today = datetime.now().strftime("%Y年%m月%d日")
    subject = f"[HabitFactory] 일본 보험뉴스 10선 — {today}"

    print("📧 이메일 발송 중...")
    send_email(subject, build_html_email(data))
    save_sent_history(sent_keys + new_keys)
    print(f"📝 이력 저장 완료 ({len(sent_keys + new_keys)}건)")

if __name__ == "__main__":
    main()
