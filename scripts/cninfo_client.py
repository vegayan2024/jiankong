# -*- coding: utf-8 -*-
"""
巨潮资讯网 (CNINFO) 官方信披权威客户端
======================================
作为上市公司定期报告（年报/半年报/季报）、招股说明书与官方披露公告的【第一法定信任源】。

特性：
  1. 自动旁路本地代理（NO_PROXY），直连国内信披服务器，防止403拦截；
  2. 自动维护 Session Cookie（JSESSIONID, SF_cookie_4）；
  3. 沪/深/北交易所智能路由（避免无效跨市场查询触发限流）；
  4. 支持 PDF 原文秒级解析（基于 PyMuPDF/fitz），定位业务、技术、行业与竞争格局；
  5. 招股书时序价值评价矩阵（T1: <=3年, T2: 3~8年, T3: >8年）。
"""

import os
import re
import sys
import time
import json
import random
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit
import requests

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# 控制台编码
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

logger = logging.getLogger("CNINFOClient")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DOWNLOAD_BASE = "https://static.cninfo.com.cn/"
NOTICE_URL = "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice"
TOP_SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
EARLIEST_DATE = "2001-01-01"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


class CNINFOClient:
    """巨潮资讯法定信披数据客户端"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path(__file__).resolve().parent.parent / "scratch" / "cninfo_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.trust_env = False  # 禁用全局代理，直连国内信披
        self._init_session()
        self._org_cache: Dict[str, Tuple[str, str, str]] = {}

    def _init_session(self):
        """初始化会话并获取有效 Cookie"""
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": NOTICE_URL,
            "X-Requested-With": "XMLHttpRequest",
        }
        self.session.headers.update(headers)
        try:
            resp = self.session.get(NOTICE_URL, timeout=10)
            if resp.status_code == 200:
                logger.debug(f"[CNINFO] 会话初始化成功，已获取有效 Cookie: {list(self.session.cookies.keys())}")
        except Exception as e:
            logger.warning(f"[CNINFO] 初始化访问 notice 异常: {e}")

    def resolve_org_info(self, stock_code: str) -> Optional[Tuple[str, str, str]]:
        """
        获取股票代码、orgId 和中文简称
        返回: (code, orgId, secName)
        """
        code_clean = re.sub(r"\D", "", str(stock_code))
        if code_clean in self._org_cache:
            return self._org_cache[code_clean]

        try:
            resp = self.session.post(
                TOP_SEARCH_URL,
                data={"keyWord": code_clean, "maxNum": 10},
                timeout=10,
            )
            if resp.status_code == 200:
                hits = resp.json()
                if isinstance(hits, list) and hits:
                    for it in hits:
                        if str(it.get("code")) == code_clean and it.get("orgId"):
                            res = (str(it.get("code")), str(it.get("orgId")), str(it.get("zwjc", "")))
                            self._org_cache[code_clean] = res
                            return res
                    # 若无精确匹配取第一条
                    first = hits[0]
                    if first.get("orgId"):
                        res = (str(first.get("code")), str(first.get("orgId")), str(first.get("zwjc", "")))
                        self._org_cache[code_clean] = res
                        return res
        except Exception as e:
            logger.warning(f"[CNINFO] 解析代码 {code_clean} orgId 失败: {e}")
        return None

    def _determine_exchange(self, code: str) -> Tuple[str, str, str]:
        """根据股票代码前缀进行交易所智能路由"""
        code = str(code).strip()
        if code.startswith(("60", "68")):
            return "sse", "sh", "沪市"
        elif code.startswith(("00", "30")):
            return "szse", "sz", "深市"
        elif code.startswith(("4", "8", "92")):
            return "bj", "bj", "北交所"
        return "sse", "sh", "沪市"

    def query_prospectus(self, stock_code: str) -> List[Dict[str, Any]]:
        """
        检索指定股票代码的招股说明书（优先选择正文，排除摘要、意向书更正等）
        """
        code_clean = re.sub(r"\D", "", str(stock_code))
        column, plate, label = self._determine_exchange(code_clean)
        
        # 获取 orgId
        org_info = self.resolve_org_info(code_clean)
        stock_val = f"{org_info[0]},{org_info[1]}" if org_info else ""

        today = datetime.today().strftime("%Y-%m-%d")
        query_data = {
            "pageNum": 1,
            "pageSize": 30,
            "tabName": "fulltext",
            "column": column,
            "stock": stock_val,
            "searchkey": "招股",
            "secid": "",
            "plate": plate,
            "category": "",
            "trade": "",
            "seDate": f"{EARLIEST_DATE}~{today}",
        }

        try:
            resp = self.session.post(QUERY_URL, data=query_data, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                announcements = data.get("announcements", []) or []
                
                # 过滤出真正的招股说明书/招股意向书正文
                valid_reports = []
                for item in announcements:
                    title = item.get("announcementTitle", "")
                    # 剔除摘要、更正、问询回复、核查意见等
                    if any(bad in title for bad in ["摘要", "更正", "核查", "问询", "补充说明", "确认意见", "回复"]):
                        continue
                    if "招股说明书" in title or "招股意向书" in title or "招股书" in title:
                        valid_reports.append(item)
                
                # 优先保留“招股说明书”，无“招股说明书”则用“招股意向书”
                full_prospectus = [r for r in valid_reports if "招股说明书" in r.get("announcementTitle", "")]
                return full_prospectus if full_prospectus else valid_reports
        except Exception as e:
            logger.warning(f"[CNINFO] 标的 {stock_code} 招股书检索异常: {e}")
        return []

    def query_annual_reports(self, stock_code: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        检索指定股票的年报正文（作为第一法定信任源）
        """
        code_clean = re.sub(r"\D", "", str(stock_code))
        column, plate, label = self._determine_exchange(code_clean)
        org_info = self.resolve_org_info(code_clean)
        stock_val = f"{org_info[0]},{org_info[1]}" if org_info else ""

        today = datetime.today().strftime("%Y-%m-%d")
        query_data = {
            "pageNum": 1,
            "pageSize": 30,
            "tabName": "fulltext",
            "column": column,
            "stock": stock_val,
            "searchkey": code_clean if not stock_val else "",
            "secid": "",
            "plate": plate,
            "category": "category_ndbg_szsh",
            "trade": "",
            "seDate": f"{EARLIEST_DATE}~{today}",
        }

        try:
            resp = self.session.post(QUERY_URL, data=query_data, timeout=15)
            if resp.status_code == 200:
                announcements = resp.json().get("announcements", []) or []
                filtered = []
                for item in announcements:
                    title = item.get("announcementTitle", "")
                    if any(bad in title for bad in ["摘要", "已取消", "更正", "提示"]):
                        continue
                    if "年年度报告" in title or "年度报告" in title:
                        if year is not None:
                            if str(year) not in title:
                                continue
                        filtered.append(item)
                return filtered
        except Exception as e:
            logger.warning(f"[CNINFO] 标的 {stock_code} 年报检索异常: {e}")
        return []

    def download_pdf(self, adjunct_url: str, save_path: Path) -> bool:
        """从 static.cninfo.com.cn 安全下载 PDF 原文"""
        if not adjunct_url:
            return False
        if save_path.exists() and save_path.stat().st_size > 10000:
            return True

        full_url = urljoin(DOWNLOAD_BASE, adjunct_url)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = save_path.with_suffix(".tmp")

        for attempt in range(3):
            try:
                resp = self.session.get(full_url, timeout=30, stream=True)
                if resp.status_code == 200:
                    with open(temp_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            f.write(chunk)
                    if temp_path.stat().st_size > 5000:
                        temp_path.replace(save_path)
                        return True
            except Exception as e:
                logger.debug(f"[CNINFO] 下载重试 ({attempt+1}/3): {e}")
                time.sleep(1 + attempt)
        if temp_path.exists():
            temp_path.unlink()
        return False

    def evaluate_time_proximity(self, ipo_date_str: str) -> Dict[str, Any]:
        """
        时序价值评价模型
        T1: <=3年 (极高现实参考价值)
        T2: 3~8年 (高结构价值，量化需校准)
        T3: >8年 (基因溯源与历史锚点)
        """
        current_year = 2026
        ipo_year = current_year
        if ipo_date_str:
            try:
                match = re.search(r"(\d{4})", str(ipo_date_str))
                if match:
                    ipo_year = int(match.group(1))
            except Exception:
                pass

        delta_t = current_year - ipo_year

        if delta_t <= 3:
            return {
                "tier": "Tier 1 (极高现实价值)",
                "rating": "high",
                "deltaYears": delta_t,
                "ipoYear": str(ipo_year),
                "summary": f"上市时间较近（{ipo_year}年，距今{delta_t}年），招股书中的行业数据、产业链产能与竞争格局同当前周期高度契合，具备极高即期研判参考价值。",
                "usageGuidance": "直接作为行业格局与竞争壁垒的核心分析依据，重点提取主要客户、募投产能与行业排名。",
            }
        elif delta_t <= 8:
            return {
                "tier": "Tier 2 (结构有效/演进校准)",
                "rating": "medium",
                "deltaYears": delta_t,
                "ipoYear": str(ipo_year),
                "summary": f"上市时间处于中期（{ipo_year}年，距今{delta_t}年），产业链上下游生态、商业模式、成本结构与牌照壁垒稳固，但市场供需规模指标已随产业周期演化。",
                "usageGuidance": "作为【上市基线口径】，结合近三年年报管理层讨论（MD&A）做时序演进对照，验证公司竞争优势是否持续强化。",
            }
        else:
            return {
                "tier": "Tier 3 (基因溯源与历史基线)",
                "rating": "historical",
                "deltaYears": delta_t,
                "ipoYear": str(ipo_year),
                "summary": f"早期/成熟期上市（{ipo_year}年，距今{delta_t}年），招股书供需量化数据已发生周期性漂移，但具备不可替代的【企业基因溯源】价值，揭示初始特许牌照、资源禀赋与原始壁垒成型史。",
                "usageGuidance": "作为公司商业模式基石与历史底蕴锚点，当前市场份额与行业数据以最新年报及行业实际数据为准。",
            }

    def parse_prospectus_pdf(self, pdf_path: Path) -> Dict[str, Any]:
        """
        使用 PyMuPDF 高速解析招股说明书中的“业务与技术/行业概况/竞争格局”核心章节
        """
        if not fitz or not pdf_path.exists():
            return {}

        result = {
            "industryBaseline": "",
            "supplyChain": {"upstream": [], "downstream": []},
            "competitiveMoat": "",
            "keyExcerpts": [],
        }

        try:
            doc = fitz.open(str(pdf_path))
            toc = doc.get_toc()  # 目录大纲
            
            target_pages = []
            # 1. 优先通过目录检索“业务与技术”、“行业概况”、“行业发展”
            for item in toc:
                lvl, title, page = item[0], item[1].strip(), item[2]
                if any(k in title for k in ["业务与技术", "行业基本情况", "行业概况", "竞争格局", "发行人主要业务"]):
                    target_pages.append((title, page))

            extracted_texts = []
            if target_pages:
                # 截取对应大纲章节内容（前 10-15 页精读）
                start_page = max(0, target_pages[0][1] - 1)
                end_page = min(len(doc), start_page + 15)
                for pno in range(start_page, end_page):
                    text = doc[pno].get_text()
                    if len(text.strip()) > 100:
                        extracted_texts.append(text)
            else:
                # 兜底：快速抽样扫描前 80 页包含“行业基本情况”或“竞争优势”的页面
                scan_limit = min(80, len(doc))
                for pno in range(scan_limit):
                    page_text = doc[pno].get_text()
                    if "行业竞争" in page_text or "主要产品" in page_text or "主要竞争优势" in page_text:
                        extracted_texts.append(page_text)
                        if len(extracted_texts) >= 6:
                            break

            doc.close()

            full_text = "\n".join(extracted_texts)
            if not full_text:
                return result

            # 结构化提炼核心洞见
            # 1) 行业定位/主营业务
            m_intro = re.search(r"(公司主要从事.*?[。；])|(公司是一家.*?[。；])|(主营业务为.*?[。；])", full_text)
            if m_intro:
                result["industryBaseline"] = m_intro.group(0).strip()

            # 2) 竞争优势/护城河
            moat_paragraphs = []
            for line in full_text.splitlines():
                line_s = line.strip()
                if any(w in line_s for w in ["竞争优势", "核心竞争力", "技术优势", "规模优势", "客户资源优势", "牌照优势"]):
                    if 15 < len(line_s) < 150:
                        moat_paragraphs.append(line_s)
            if moat_paragraphs:
                result["competitiveMoat"] = "；".join(moat_paragraphs[:3])

            # 3) 精选摘要
            summary_sentences = [
                s.strip() for s in re.split(r"[。\n]", full_text) 
                if 20 < len(s.strip()) < 120 and any(kw in s for kw in ["市场份额", "行业地位", "龙头", "产业链", "上下游", "技术壁垒"])
            ]
            result["keyExcerpts"] = summary_sentences[:5]

        except Exception as e:
            logger.warning(f"[CNINFO] 解析 PDF {pdf_path.name} 异常: {e}")

        return result


# 全局客户端实例
cninfo_client = CNINFOClient()

if __name__ == "__main__":
    print("=== 巨潮资讯权威信披客户端测试 ===")
    test_code = "603977"
    org = cninfo_client.resolve_org_info(test_code)
    print(f"[{test_code}] 标的信息: {org}")
    prospectuses = cninfo_client.query_prospectus(test_code)
    print(f"[{test_code}] 招股说明书列表: {len(prospectuses)} 篇")
    for p in prospectuses:
        print(f"  - {p.get('announcementTitle')} ({p.get('announcementTime')}) URL: {p.get('adjunctUrl')}")
