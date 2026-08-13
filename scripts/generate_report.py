#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日医学科研精读日报 - 云端自动生成脚本
数据源:PubMed E-utilities(公开免费)、arXiv API(公开免费)
流程:抓取最新论文 -> 生成 Markdown -> (可选)转 PDF
在 GitHub Actions 上每天定时执行;本地也可以直接运行调试。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 配置区
# ---------------------------------------------------------------------------
# PubMed 检索方向(每个方向取最新前 3 篇,合并去重后取前 N 篇)
PUBMED_QUERIES = [
    "cancer AND (radiotherapy OR immunotherapy)",
    "metabolic dysfunction-associated steatotic liver disease",
    "heart failure AND imaging",
    "aging AND autophagy",
    "meningioma AND deep learning",
]
PUBMED_PER_QUERY = 3   # 每个方向取几篇
PUBMED_MAX = 5          # 医学板块最多几篇

# arXiv 分类(每个分类取最新前 2 篇)
ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CV"]
ARXIV_PER_CATEGORY = 2  # 每个分类取几篇
ARXIV_MAX = 3           # AI 板块最多几篇

# 经典精读配置(每天按日期轮换,可自行增删条目)
CLASSICS_FILE = os.path.join(os.path.dirname(__file__), "classics.json")

# 摘要显示长度
ABSTRACT_LIMIT = 300

# 输出目录(默认:项目根目录的 reports/)
DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports"
)

# ---------------------------------------------------------------------------
# PubMed 抓取
# ---------------------------------------------------------------------------
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "daily-med-report/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError:
        # 本地调试环境可能缺少系统 CA 证书:仅在此类环境下降级重试一次。
        # 云端(GitHub Actions)证书完整,永远走上面安全的默认路径。
        import ssl
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")


def pubmed_esearch(query: str, retmax: int) -> list:
    """esearch 返回最新 PMID 列表"""
    params = urllib.parse.urlencode({
        "db": "pubmed", "term": query, "retmax": retmax,
        "sort": "date", "retmode": "json",
    })
    data = json.loads(http_get(f"{EUTILS_BASE}/esearch.fcgi?{params}"))
    return data.get("esearchresult", {}).get("idlist", [])


def pubmed_efetch(pmids: list) -> list:
    """efetch 批量获取完整元数据(XML)"""
    if not pmids:
        return []
    params = urllib.parse.urlencode({
        "db": "pubmed", "id": ",".join(pmids), "retmode": "xml",
    })
    xml_text = http_get(f"{EUTILS_BASE}/efetch.fcgi?{params}")
    root = ET.fromstring(xml_text)
    papers = []
    for art in root.iter("PubmedArticle"):
        medline = art.find("MedlineCitation")
        article = medline.find("Article") if medline is not None else None
        if article is None:
            continue

        title = "".join(article.find("ArticleTitle").itertext()) \
            if article.find("ArticleTitle") is not None else "(无标题)"

        abstract = ""
        abstract_el = article.find("Abstract")
        if abstract_el is not None:
            parts = []
            for t in abstract_el.iter("AbstractText"):
                parts.append("".join(t.itertext()))
            abstract = " ".join(parts).strip()

        authors = []
        author_list = article.find("AuthorList")
        if author_list is not None:
            for au in list(author_list)[:6]:
                last = au.findtext("LastName") or ""
                fore = au.findtext("ForeName") or ""
                if last:
                    authors.append(f"{fore} {last}".strip())
        if not authors:
            authors = ["(作者信息缺失)"]

        journal = article.findtext("Journal/Title") or "(期刊未知)"

        pubdate = ""
        pd = article.find("Journal/JournalIssue/PubDate")
        if pd is not None:
            year = pd.findtext("Year") or pd.findtext("MedlineDate") or ""
            month = pd.findtext("Month") or ""
            day = pd.findtext("Day") or ""
            pubdate = f"{year}-{month}-{day}" if day else (year or "(日期未知)")

        doi = ""
        # 优先从 PubmedData/ArticleIdList 取 DOI(最权威)
        for art_id in art.iter("ArticleId"):
            if art_id.get("IdType") == "doi":
                doi = art_id.text or ""
                break
        if not doi:
            id_list_el = article.find("ELocationID")
            if id_list_el is not None and id_list_el.get("EIdType") == "doi":
                doi = id_list_el.text or ""

        pmid = medline.findtext("PMID") if medline is not None else ""
        papers.append({
            "title": title, "abstract": abstract, "authors": authors,
            "journal": journal, "pubdate": pubdate, "doi": doi,
            "pmid": pmid, "source_type": "pubmed",
        })
    return papers


def fetch_pubmed() -> list:
    """抓取 PubMed 最新论文并去重"""
    seen, papers = set(), []
    for q in PUBMED_QUERIES:
        try:
            pmids = pubmed_esearch(q, PUBMED_PER_QUERY)
            time.sleep(0.4)  # NCBI 限速:无 key 时 <= 3 req/s
            for p in pubmed_efetch(pmids):
                key = p["title"].lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    papers.append(p)
            time.sleep(0.4)
        except Exception as exc:
            print(f"[warn] PubMed 检索失败({q}): {exc}", file=sys.stderr)
    return papers[:PUBMED_MAX]


# ---------------------------------------------------------------------------
# arXiv 抓取
# ---------------------------------------------------------------------------
ARXIV_API = "http://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def fetch_arxiv() -> list:
    papers = []
    for cat in ARXIV_CATEGORIES:
        try:
            query = urllib.parse.urlencode({
                "search_query": f"cat:{cat}",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": ARXIV_PER_CATEGORY,
            })
            xml_text = http_get(f"{ARXIV_API}?{query}", timeout=60)
            root = ET.fromstring(xml_text)
            for entry in root.findall("atom:entry", ATOM_NS):
                title = " ".join((entry.findtext("atom:title", "", ATOM_NS) or "").split())
                summary = " ".join((entry.findtext("atom:summary", "", ATOM_NS) or "").split())
                authors = [au.findtext("atom:name", "", ATOM_NS)
                           for au in entry.findall("atom:author", ATOM_NS)]
                published = entry.findtext("atom:published", "", ATOM_NS) or ""
                link = entry.findtext("atom:id", "", ATOM_NS) or ""
                link = link.replace("http://", "https://").replace("abs/", "abs/")
                papers.append({
                    "title": title, "abstract": summary,
                    "authors": authors[:6] or ["(作者信息缺失)"],
                    "journal": "arXiv Preprint", "pubdate": published[:10],
                    "doi": "", "pmid": "", "link": link,
                    "source_type": "arxiv",
                })
        except Exception as exc:
            print(f"[warn] arXiv 抓取失败({cat}): {exc}", file=sys.stderr)
    return papers[:ARXIV_MAX]


# ---------------------------------------------------------------------------
# 经典精读
# ---------------------------------------------------------------------------
def load_classics() -> list:
    try:
        with open(CLASSICS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def pick_classic(classics: list, date_obj: datetime) -> dict:
    if not classics:
        return None
    day_index = date_obj.toordinal()
    return classics[day_index % len(classics)]


# ---------------------------------------------------------------------------
# Markdown 生成
# ---------------------------------------------------------------------------
def truncate(text: str, limit: int = ABSTRACT_LIMIT) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def fmt_authors(authors: list) -> str:
    return ", ".join(authors[:5])


def build_markdown(med_papers, arxiv_papers, classic, date_obj) -> str:
    today = date_obj.strftime("%Y年%m月%d日")
    lines = [f"# 每日医学科研精读日报 | {today}", ""]

    # 一、今日前沿论文精选
    lines.append("## 一、今日前沿论文精选")
    lines.append("")
    idx = 1
    for group in (med_papers, arxiv_papers):
        for p in group:
            lines.append(f"### {idx}. {p['title']}")
            lines.append("")
            lines.append(f"- 发表期刊 / 影响因子:{p['journal']}")
            lines.append(f"- 发表时间:{p['pubdate'] or '未知'}")
            lines.append(f"- 第一/通讯作者:{fmt_authors(p['authors'])}")
            lines.append(f"- 核心结论:{truncate(p['abstract']) or '参见原文摘要'}")
            lines.append(f"- 原文DOI / 链接:{p['doi'] or p.get('link', '') or '参见原文'}")
            lines.append("")
            idx += 1
    if idx == 1:
        lines.append("*今日暂无新论文,请直接查看 PubMed / arXiv 原文。*")
        lines.append("")

    # 二、科研公众号干货
    lines.append("## 二、科研公众号干货")
    lines.append("")
    lines.append("*说明:微信公众号文章检索需要微信搜一搜等公开渠道的即时访问能力,当前自动检索模块正在优化中,建议通过以下方式自行获取:*")
    lines.append("- 关注「肿瘤放疗」、「生信技能树」、「医学僧的科研日记」等公众号")
    lines.append("- 使用微信搜一搜搜索关键词:肿瘤放疗 生信分析")
    lines.append("- 本板块将在后续版本中接入 WeChat 公开内容 API")
    lines.append("")

    # 三、经典精读
    lines.append("## 三、每日高分经典精读")
    lines.append("")
    if classic:
        lines.append(f"### {classic.get('title', '')}")
        lines.append("")
        lines.append(f"- 发表期刊 / 影响因子 / 总引用量:{classic.get('journal', '')}")
        lines.append(f"- 研究背景与里程碑意义:{classic.get('background', '')}")
        lines.append(f"- 核心实验设计与方法:{classic.get('methods', '')}")
        lines.append(f"- 关键结论与行业影响:{classic.get('conclusion', '')}")
        lines.append(f"- 创新点:{classic.get('innovation', '')}")
        lines.append(f"- 原文DOI / 链接:{classic.get('link', '')}")
        lines.append("")
    else:
        lines.append("*暂无经典文献配置,请在 scripts/classics.json 中补充。*")
        lines.append("")

    # 四、当日总结
    total = len(med_papers) + len(arxiv_papers)
    lines.append("## 四、当日总结")
    lines.append("")
    focus = med_papers[0]["title"] if med_papers else "无"
    lines.append(f"今日共精选{total}篇最新研究"
                 + (f"+1篇经典精读。" if classic else "。")
                 + f"关注方向:{focus}")
    lines.append("")
    lines.append("---")
    lines.append(f"*日报自动生成于 {date_obj.strftime('%Y年%m月%d日')} | "
                 f"数据来源:PubMed、arXiv | 下一期:明日7:00*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PDF 转换(pandoc + wkhtmltopdf,工具缺失时跳过)
# ---------------------------------------------------------------------------
def md_to_pdf(md_path: str, pdf_path: str) -> bool:
    if not (shutil.which("pandoc") and shutil.which("wkhtmltopdf")):
        print("[info] 本机缺少 pandoc/wkhtmltopdf,跳过 PDF 生成(云端已安装)")
        return False
    html_path = pdf_path.replace(".pdf", ".html")
    css_path = os.path.join(os.path.dirname(__file__), "report.css")
    subprocess.run(
        ["pandoc", md_path, "-f", "markdown", "-t", "html", "--standalone",
         f"--css={css_path}", "-o", html_path], check=True)
    subprocess.run(
        ["wkhtmltopdf", "--encoding", "utf-8", "--enable-local-file-access",
         html_path, pdf_path], check=True)
    os.remove(html_path)
    return True


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="生成每日医学科研精读日报")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD(默认今天)")
    args = parser.parse_args()

    date_obj = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    os.makedirs(args.output_dir, exist_ok=True)

    print(">> 抓取 PubMed 最新论文 ...")
    med = fetch_pubmed()
    print(f"   获取 {len(med)} 篇医学论文")
    print(">> 抓取 arXiv 最新论文 ...")
    arxiv = fetch_arxiv()
    print(f"   获取 {len(arxiv)} 篇 AI 论文")
    classic = pick_classic(load_classics(), date_obj)

    md = build_markdown(med, arxiv, classic, date_obj)
    stamp = date_obj.strftime("%Y%m%d")
    md_path = os.path.join(args.output_dir, f"{stamp}_医学科研精读日报.md")
    pdf_path = os.path.join(args.output_dir, f"{stamp}_医学科研精读日报.pdf")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[ok] Markdown 已生成:{md_path}")

    md_to_pdf(md_path, pdf_path)  # 转 PDF(依赖 pandoc+wkhtmltopdf)
    if os.path.exists(pdf_path):
        print(f"[ok] PDF 已生成:{pdf_path}")

    print(">> 完成")


if __name__ == "__main__":
    main()
