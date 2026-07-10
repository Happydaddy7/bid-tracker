#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
세인바이오 사료원료 입찰공고 자동 수집기
=========================================
게시판을 보유한 발주처(농협사료·안양축협·도드람·TS사료)를 직접 스크래핑하고,
추가로 검색 기반 자동 탐색으로 등록되지 않은 신규 회사까지 찾아
data/bids.json 에 누적 저장한다.

- 실행 주기: GitHub Actions cron (매일 1회, .github/workflows/scrape-bids.yml)
- 출력: data/bids.json (index.html "입찰공고" 탭이 이 파일을 fetch해서 렌더링)
- 중복 방지: (source, id) 조합을 key로 기존 데이터와 merge
- 각 소스는 독립된 함수로 분리되어 있어 한 곳이 깨져도 나머지는 계속 수집됨

⚠️ 검증 상태
  - nonghyup_feed (농협사료 본사)  : 실제 페이지 구조 확인 완료
  - nonghyup_local (지역축협 공통) : 안양축협 기준 확인, 다른 지역조합은 board_id만
    다를 뿐 동일 플랫폼(nonghyup.com 지역조합 CMS)이라 SOURCES 리스트에 추가만 하면 됨
  - dodram (도드람양돈농협 모바일 게시판) : URL 패턴 기반 best-effort, 최초 실행 후
    구조 확인 필요할 수 있음
  - ts_feed (TS사료) : 사이트가 SPA(JS 렌더링) 형태라 requests만으로는 목록을
    가져오지 못할 가능성이 높음 → 최초 실행 결과를 보고 Selenium/Playwright 전환 여부 판단
"""

import json
import os
import re
import sys
import time
import hashlib
import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "bids.json"

# 관심 품목 키워드 (제목에 하나라도 매칭되면 "관심" 표시, 매칭 안 돼도 목록엔 포함)
KEYWORDS = [
    "비타민", "아미노산", "라이신", "메치오닌", "트레오닌", "트립토판", "발린",
    "MDCP", "TCP", "DCP", "인산칼슘", "보호지방", "팔미트산", "메가팻",
    "나노비타", "황산라이신", "콜린", "미네랄", "사료원료", "배합사료",
    "구매입찰", "견적", "입찰",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def is_relevant(title: str) -> bool:
    return any(kw in title for kw in KEYWORDS)


def make_id(source: str, raw_id: str) -> str:
    return hashlib.md5(f"{source}:{raw_id}".encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 1) 농협사료 본사 - 구매입찰 게시판 (navi=4-3-1, bdid=N2, tbid=NHFBOARD)
# ---------------------------------------------------------------------------
def scrape_nonghyup_feed(pages: int = 2):
    base = "https://www.nonghyupsaryo.co.kr/noti/listT1.asp"
    results = []
    for page in range(1, pages + 1):
        params = {
            "page": page, "navi": "4-3-1", "tbid": "NHFBOARD", "bdid": "N2", "ifid": "",
            "s1": "", "s2": "", "s3": "", "s4": "", "s5": "",
            "s6": "", "s7": "", "s8": "", "s9": "", "s10": "",
        }
        try:
            resp = requests.get(base, params=params, headers=HEADERS, timeout=15)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"[nonghyup_feed] page {page} 요청 실패: {e}", file=sys.stderr)
            continue

        rows = soup.select("table tr")
        for row in rows:
            link = row.find("a", href=re.compile(r"view\.asp\?seq="))
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link.get("href", "")
            m = re.search(r"seq=(\d+)", href)
            seq = m.group(1) if m else href
            tds = row.find_all("td")
            date_text = ""
            for td in tds:
                t = td.get_text(strip=True)
                if re.match(r"\d{4}[.\-]\d{2}[.\-]\d{2}", t):
                    date_text = t.replace(".", "-")
                    break
            if not title:
                continue
            detail_url = "https://www.nonghyupsaryo.co.kr/noti/" + href.lstrip("./")
            results.append({
                "id": make_id("nonghyup_feed", seq),
                "source": "농협사료(본사)",
                "title": title,
                "date": date_text,
                "url": detail_url,
                "relevant": is_relevant(title),
                "raw_text": _fetch_detail_text(detail_url),
            })
        time.sleep(0.5)
    return results


def _fetch_detail_text(url: str) -> str:
    """상세페이지 본문 텍스트를 가져온다 (추출 단계 입력용). 실패해도 조용히 빈 문자열 반환."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = resp.apparent_encoding or "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        return text[:4000]  # 추출 프롬프트 토큰 절약을 위해 앞부분만
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 2) 농협 지역조합 공통 CMS (indexSub.do 플랫폼)
#    안양축협을 시작으로, 같은 플랫폼을 쓰는 다른 지역조합은
#    SOURCES 리스트에 site_id/board_id만 추가하면 됨.
# ---------------------------------------------------------------------------
NONGHYUP_LOCAL_SOURCES = [
    {"name": "안양축협", "site_id": "aylc", "board_id": "116179"},
    # 예시로 추가하려면 아래처럼 한 줄만 추가:
    # {"name": "대구축협", "site_id": "dgcattle", "board_id": "XXXXXX"},
]


def scrape_nonghyup_local(pages: int = 1):
    results = []
    for src in NONGHYUP_LOCAL_SOURCES:
        base = f"https://{src['site_id']}.nonghyup.com/user/boardList.do"
        for page in range(1, pages + 1):
            params = {
                "siteId": src["site_id"], "boardId": src["board_id"],
                "command": "list", "page": page,
            }
            try:
                resp = requests.get(base, params=params, headers=HEADERS, timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")
            except Exception as e:
                print(f"[nonghyup_local:{src['name']}] 요청 실패: {e}", file=sys.stderr)
                continue

            links = soup.find_all("a", href=re.compile(r"boardSeq="))
            for link in links:
                title = link.get_text(strip=True)
                if not title or "입찰" not in title and "견적" not in title and not is_relevant(title):
                    # 지역조합 게시판은 입찰 외 공지도 섞여 있으므로 1차 필터링
                    if "입찰" not in title and "견적" not in title:
                        continue
                href = link.get("href", "")
                m = re.search(r"boardSeq=(\d+)", href)
                board_seq = m.group(1) if m else href
                full_url = href if href.startswith("http") else (
                    f"https://{src['site_id']}.nonghyup.com{href}"
                )
                results.append({
                    "id": make_id(f"nonghyup_local_{src['site_id']}", board_seq),
                    "source": src["name"],
                    "title": title,
                    "date": "",
                    "url": full_url,
                    "relevant": is_relevant(title),
                    "raw_text": title,
                })
            time.sleep(0.5)
    return results


# ---------------------------------------------------------------------------
# 3) 도드람양돈농협 (pkpork.co.kr 모바일 게시판, tender 게시판)
# ---------------------------------------------------------------------------
def scrape_dodram(pages: int = 1):
    base = "https://m.pkpork.co.kr/board/Board.do"
    results = []
    for page in range(1, pages + 1):
        params = {
            "action": "list", "page": page, "board_id": "9170",
            "mgr_id": "tender", "group_id": "1",
        }
        try:
            resp = requests.get(base, params=params, headers=HEADERS, timeout=15)
            resp.encoding = resp.apparent_encoding
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"[dodram] page {page} 요청 실패: {e}", file=sys.stderr)
            continue

        links = soup.find_all("a", href=re.compile(r"rwnum="))
        for link in links:
            title = link.get_text(strip=True)
            if not title:
                continue
            href = link.get("href", "")
            m = re.search(r"rwnum=(\d+)", href)
            rwnum = m.group(1) if m else href
            full_url = href if href.startswith("http") else "https://m.pkpork.co.kr" + href
            results.append({
                "id": make_id("dodram", rwnum),
                "source": "도드람양돈농협",
                "title": title,
                "date": "",
                "url": full_url,
                "relevant": is_relevant(title),
                "raw_text": title,
            })
        time.sleep(0.5)
    return results


# ---------------------------------------------------------------------------
# 4) TS사료 - 게시판이 <table> 안에 목록이 서버렌더링되어 있고, 상세보기는
#    자바스크립트로 열리는 방식 (앵커 태그에 href가 없음). 그래서 목록은
#    표를 직접 파싱해서 가져오고, 원문 링크는 게시판 목록 페이지로 건다.
# ---------------------------------------------------------------------------
def scrape_ts_feed():
    url = "https://www.tsfeed.co.kr/kor/sub05/menu_04.html"
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            print("[ts_feed] 표를 찾지 못함 - 사이트 구조가 바뀌었을 수 있음", file=sys.stderr)
            return results

        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue  # 헤더 행 등 skip
            no = cells[0].get_text(strip=True)
            title = cells[1].get_text(strip=True).replace("공지", "", 1).strip()
            date_text = cells[2].get_text(strip=True)
            if not title or not no.isdigit():
                continue
            results.append({
                "id": make_id("ts_feed", no),
                "source": "TS사료",
                "title": title,
                "date": "20" + date_text.replace(".", "-") if len(date_text) == 8 else date_text,
                "url": url,  # 상세링크가 JS라 목록 페이지로 연결 (게시판 글번호: no)
                "relevant": is_relevant(title),
                "raw_text": f"{title} (게시글 번호 {no})",
            })
    except Exception as e:
        print(f"[ts_feed] 요청 실패: {e}", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# 5) 검색 기반 자동 탐색 - 미리 등록해두지 않은 "어떤 회사든" 새로 발견하기 위한 소스.
#    - Google Custom Search JSON API로 "사료원료 구매입찰" 류 키워드를 검색해서
#      최근 7일 내 게시된 페이지를 찾고, 그 페이지 본문을 읽어서 관련 있으면 수집한다.
#    - 회사별 스크래퍼처럼 100% 정확하진 않지만, 검색엔진에 노출되는 페이지라면
#      사전에 등록하지 않은 회사도 잡아낼 수 있다.
#    - 로그인/비공개 게시판, 검색엔진에 안 잡히는 페이지는 여전히 커버 불가 (구조적 한계).
#    - 필요: Google Cloud Console에서 Custom Search JSON API 활성화 후 API 키 발급 +
#      programmablesearchengine.google.com 에서 검색엔진(cx) 생성.
#      GitHub Secrets에 GOOGLE_CSE_API_KEY, GOOGLE_CSE_CX 로 등록.
# ---------------------------------------------------------------------------
SEARCH_QUERIES = [
    "사료원료 구매입찰 공고",
    "배합사료 원료 입찰공고",
    "아미노산 구매입찰 공고",
    "비타민 사료 구매입찰",
    "보호지방 구매입찰 공고",
    "인산칼슘 구매입찰",
]

# 이미 전용 스크래퍼로 커버 중인 도메인은 검색 결과에서 걸려도 중복 수집하지 않도록 제외
SEARCH_EXCLUDE_DOMAINS = ["nonghyupsaryo.co.kr", "nonghyup.com", "pkpork.co.kr", "tsfeed.co.kr"]


def scrape_search_discovery(max_results_per_query: int = 10):
    api_key = os.environ.get("GOOGLE_CSE_API_KEY")
    cx = os.environ.get("GOOGLE_CSE_CX")
    if not api_key or not cx:
        print("[search] GOOGLE_CSE_API_KEY/GOOGLE_CSE_CX 미설정 - 검색 탐색 건너뜀", file=sys.stderr)
        return []

    base = "https://www.googleapis.com/customsearch/v1"
    results = []
    seen_urls = set()

    for q in SEARCH_QUERIES:
        params = {
            "key": api_key, "cx": cx, "q": q,
            "num": max_results_per_query,
            "dateRestrict": "d7",  # 최근 7일 이내 게시된 페이지만
            "gl": "kr", "hl": "ko",
        }
        try:
            resp = requests.get(base, params=params, timeout=15)
            data = resp.json()
        except Exception as e:
            print(f"[search] '{q}' 요청 실패: {e}", file=sys.stderr)
            continue

        for item in data.get("items", []):
            link = item.get("link", "")
            if not link or link in seen_urls:
                continue
            domain = urlparse(link).netloc
            if any(ex in domain for ex in SEARCH_EXCLUDE_DOMAINS):
                continue  # 전용 스크래퍼가 이미 커버하는 곳은 스킵
            seen_urls.add(link)

            title = item.get("title", "")
            snippet = item.get("snippet", "")
            if not (is_relevant(title) or is_relevant(snippet)):
                continue

            raw_text = _fetch_detail_text(link) or snippet
            results.append({
                "id": make_id("search", link),
                "source": domain,
                "title": title,
                "date": "",
                "url": link,
                "relevant": True,
                "raw_text": (raw_text or snippet)[:4000],
            })
        time.sleep(0.3)
    return results


# ---------------------------------------------------------------------------
# 병합 & 저장
# ---------------------------------------------------------------------------
def load_existing():
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save(all_items):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "items": all_items,
    }
    DATA_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    existing = load_existing()
    existing_items = existing.get("items", existing) if isinstance(existing, dict) else existing
    existing_by_id = {item["id"]: item for item in existing_items}

    collected = []
    for fn in (scrape_nonghyup_feed, scrape_nonghyup_local, scrape_dodram, scrape_ts_feed, scrape_search_discovery):
        try:
            items = fn()
            print(f"{fn.__name__}: {len(items)}건 수집")
            collected.extend(items)
        except Exception as e:
            print(f"{fn.__name__} 실패: {e}", file=sys.stderr)

    now = datetime.datetime.now().isoformat(timespec="seconds")
    for item in collected:
        if item["id"] not in existing_by_id:
            item["first_seen"] = now
        else:
            item["first_seen"] = existing_by_id[item["id"]].get("first_seen", now)
        existing_by_id[item["id"]] = item

    merged = sorted(
        existing_by_id.values(), key=lambda x: x.get("first_seen", ""), reverse=True
    )
    # 오래된 항목 무한정 누적 방지 (최근 300건만 유지)
    merged = merged[:300]

    save(merged)
    print(f"총 {len(merged)}건 저장 완료 -> {DATA_PATH}")


if __name__ == "__main__":
    main()
