#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
세인바이오 사료원료 입찰공고 자동 수집기
=========================================
게시판을 보유한 발주처(농협사료·안양축협·도드람·TS사료)를 직접 스크래핑하고,
추가로 검색 기반 자동 탐색으로 등록되지 않은 신규 회사까지 찾아
data/bids.json 에 누적 저장한다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【거래처 전수조사 결과 (2026-07)】 — 재조사 방지용 기록

■ 수집 가능 (코드로 구현됨)
  · 농협사료(본사)   : 구매입찰 게시판 운영. ※부산바이오는 농협사료 소속
                      지사이므로 이 소스에 포함됨(별도 회사 아님)
  · 도드람 그룹      : dodram.nonghyup.com 통합 입찰게시판.
                      디에스피드 포함 계열사 11곳 공고가 한 곳에 모임
  · TS사료           : 입찰 게시판 운영 (tsfeed.co.kr)
  · 한국사료협회     : 업계 공통 '인사채용/입찰공고' 게시판
  · 인천축협/공덕농협: 공지사항 게시판에 입찰공고 게재

■ 게시판 없음 → 자동 수집 불가 (이메일/팩스로만 발송)
  · 팜스코, CJ피드앤케어, 카길애그리퓨리나(퓨리나), 이지바이오
  · 안양축협 : 전체 메뉴 확인 결과 입찰 게시판 자체가 없음
  · 우성사료 : '공고게시판'이 있으나 결산공고(대차대조표)만 게재. 입찰 없음
  · 대한제당(TS그룹 모회사) : 채용공고만 운영

■ 확인 실패 / 접근 차단
  · home.dodram.com  : robots.txt 차단 (단, dodram.nonghyup.com은 사용 가능)
  · 농협경제지주(nhabgroup) : robots.txt 차단
  · 미래부연합사료   : 홈페이지를 찾지 못함. 주소를 알면 추가 가능

→ 게시판 없는 회사들은 Slack #0-06입찰공고 채널 연동으로 커버하는 것이
  유일한 방법. (직원들이 이메일/팩스 수신분을 채널에 올리고 있음)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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

# ---------------------------------------------------------------------------
# 관련성 판단 규칙
# ---------------------------------------------------------------------------
# [핵심] 실제 품목/사료 관련 키워드. 이 중 하나라도 걸려야 "우리와 관련 있는 공고"로 본다.
#   ⚠️ "입찰", "견적", "구매입찰" 같은 문서 형식 단어는 여기 넣으면 안 된다.
#      (그러면 사무용품·공사·차량 입찰까지 전부 걸려버림)
PRODUCT_KEYWORDS = [
    # 아미노산
    "아미노산", "라이신", "황산라이신", "메치오닌", "메티오닌", "트레오닌",
    "트립토판", "발린", "이소류신", "알지닌",
    # 비타민 / 첨가제
    "비타민", "비타민E", "비타민C", "비타민A", "비타민D", "콜린", "염화콜린",
    "나노비타", "프리믹스", "첨가제", "유기산", "효소제", "생균제", "항산화제",
    # 기능성 첨가제 (도드람·농협사료 공고에서 실제 확인된 품목명)
    "바인더", "톡신", "곰팡이독소", "흡착제", "클레이", "제올라이트",
    "감미료", "향미제", "완충제", "유화제", "프로바이오틱", "효모",
    # 미네랄 / 인산염
    "미네랄", "인산칼슘", "MDCP", "TCP", "DCP", "MCP", "석회석", "탄산칼슘",
    "산화아연", "황산동", "미량광물질",
    # 유지 / 보호지방
    "보호지방", "팔미트산", "메가팻", "하이팻", "지방산", "유지", "우지", "대두유",
    # 단백/일반 원료
    "대두박", "채종박", "옥수수", "소맥", "밀기울", "어분", "탈지분유", "유청",
    "글루텐", "주정박", "코코넛", "코코시스",
    # 사료 일반
    "사료", "사료원료", "배합사료", "단미사료", "보조사료", "TMR", "조사료",
]

# [제외] 이 단어가 제목에 있으면 사료 관련 키워드가 걸렸더라도 목록에서 뺀다.
#   (예: "사료창고 신축공사" → "사료"가 걸리지만 우리 입찰이 아님)
EXCLUDE_KEYWORDS = [
    "공사", "신축", "증축", "개축", "철거", "포장공사", "전기공사", "설비공사",
    "청소", "경비", "용역", "위탁운영", "임대", "매각", "폐기물", "차량", "지게차",
    "채용", "모집", "인턴", "교육", "연수", "행사", "홍보물", "인쇄", "사무용품",
    "소프트웨어", "전산", "시스템 구축", "유지보수", "보험", "급식", "식당",
]

# 하위호환용 별칭 (기존 코드에서 KEYWORDS를 참조하는 부분이 있을 수 있음)
KEYWORDS = PRODUCT_KEYWORDS

# [사료업계 발주처명] Slack 공고는 "○○축협 입찰공고입니다"처럼 회사명만 쓰고
#   품목은 첨부파일에 있는 경우가 많다. 따라서 알려진 사료업계 발주처명이
#   제목에 있으면 품목 키워드가 없어도 관련 공고로 인정한다.
FEED_BUYER_KEYWORDS = [
    "농협사료", "부산바이오", "군산바이오", "안양축협", "안양연합", "인천축협",
    "수원축협", "대구축협", "서울축협", "양주축협", "홍천축협", "도드람",
    "디에스피드", "TS사료", "티에스사료", "대한제당", "체리부로", "우성사료",
    "다원케미칼", "한국썸벳", "De Heus", "데헤우스", "미래부", "부경양돈",
    "팜스코", "카길", "퓨리나", "CJ피드", "이지바이오", "고려산업", "대한사료",
    "동원팜스", "선진", "하림", "한국사료협회",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def is_relevant(text: str) -> bool:
    """사료 원료 관련 공고인지 판단 (엄격 모드).

    잡다한 글이 섞여 들어오는 소스(검색 탐색, Slack, 일반 공지 게시판)에 사용.
    - 품목 키워드가 하나도 없으면 무관 (False)
    - 품목 키워드가 있어도 제외 키워드에 걸리면 무관 (False)
      예) "사료공장 전기공사 입찰" → '사료'는 있지만 '공사'가 있어 제외
    """
    if not text:
        return False
    if any(ex in text for ex in EXCLUDE_KEYWORDS):
        return False
    if any(kw in text for kw in PRODUCT_KEYWORDS):
        return True
    # 품목명이 없어도, 알려진 사료업계 발주처 + 입찰/견적 표현이면 관련 공고로 본다.
    # (예: "안양축협 입찰공고입니다" — 품목은 첨부파일에 있음)
    if any(b in text for b in FEED_BUYER_KEYWORDS):
        if any(w in text for w in ("입찰", "견적", "공고", "구매")):
            return True
    return False


def is_relevant_on_bid_board(text: str) -> bool:
    """입찰 전용 게시판에서 온 글인지 판단 (완화 모드).

    농협사료 '구매입찰' 게시판, TS사료 '입찰' 게시판처럼 게시판 자체가
    이미 구매입찰 전용인 곳은, 거기 올라온 글이면 기본적으로 우리 관심사다.
    제목에 품목명이 안 적힌 경우가 많으므로(예: "구매입찰 공고(2026-08)")
    품목 키워드를 요구하지 않고, 명백히 무관한 것만 제외한다.
    """
    if not text:
        return False
    return not any(ex in text for ex in EXCLUDE_KEYWORDS)


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
            detail_text = _fetch_detail_text(detail_url)
            # ⚠️ 관련성 판단은 '제목'으로만 한다.
            #    상세페이지 본문 전체를 넣으면 사이트 공통 메뉴/푸터에 들어있는
            #    "채용", "공사", "용역" 같은 단어까지 제외 키워드에 걸려
            #    정상 입찰공고가 전부 탈락해버린다.
            results.append({
                "id": make_id("nonghyup_feed", seq),
                "source": "농협사료(본사)",
                "title": title,
                "date": date_text,
                "url": detail_url,
                "relevant": is_relevant_on_bid_board(title),
                "bid_board": True,   # 입찰 전용 게시판 → 완화 기준 적용
                "raw_text": detail_text or title,
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
# 2) 농협 지역조합 (nonghyup.com 지역조합 CMS)
#
# ⚠️ 현재 등록된 조합 없음 (2026-07 확인)
#    안양축협(aylc.nonghyup.com) 전체 메뉴를 확인한 결과, 입찰공고 게시판이
#    존재하지 않았다. (메뉴 구성: 농협소개/상호금융/지도사업/사료사업/
#    유통사업/고객지원 — 고객지원 하위도 공지사항·축산소식·농업뉴스·
#    직원전용·고객의소리뿐)
#    → 안양축협 입찰은 홈페이지가 아닌 이메일/팩스로만 오는 것으로 보인다.
#
#    다른 지역조합을 추가하려면, 먼저 해당 조합 홈페이지에 실제로
#    '입찰공고' 게시판이 있는지 눈으로 확인한 뒤 아래 형식으로 등록할 것.
#    게시판 URL은 다음 형태다 (순천농협 사례로 확인):
#      https://{site_id}.nonghyup.com/user/boardList.do?siteId={site_id}&boardId={board_id}
#    board_id는 게시판을 열었을 때 주소창에서 확인할 수 있다.
# ---------------------------------------------------------------------------
# 전국 축협/농협은 모두 nonghyup.com 공통 플랫폼을 쓰고, 게시판 주소는 아래 형태다:
#   https://{site_id}.nonghyup.com/user/boardList.do?siteId={site_id}&boardId={board_id}
#
# 【추가하는 방법】
#   1) 해당 조합 홈페이지 접속 (예: 인천축협 → icch.nonghyup.com)
#   2) 공지사항/입찰공고 게시판을 연다
#   3) 주소창에서 siteId=OOO, boardId=NNNNN 두 값을 복사
#   4) 아래 리스트에 한 줄 추가
#
# 확인된 site_id 예시: aylc(안양축협), icch(인천축협), lico(수원축협),
#   nhkd(공덕농협), dodram(도드람) — 단, 조합마다 입찰 게시판 유무가 다르다.
#   ※ 축협 공지사항 게시판에는 공사·용역 공고가 많으므로 엄격 필터가 적용된다.
NONGHYUP_LOCAL_SOURCES = [
    # 인천축협 공지사항 (입찰공고가 이 게시판에 올라옴 - 2026.07 확인)
    {"name": "인천축협", "site_id": "icch", "board_id": "67597"},
    # 공덕농협 공지사항
    {"name": "공덕농협", "site_id": "nhkd", "board_id": "125449"},
    # 필요 시 아래 형식으로 계속 추가:
    # {"name": "○○축협", "site_id": "xxxx", "board_id": "123456"},
]


def scrape_nonghyup_local(pages: int = 1):
    results = []
    for src in NONGHYUP_LOCAL_SOURCES:
        base = f"https://{src['site_id']}.nonghyup.com/user/boardList.do"
        for page in range(1, pages + 1):
            params = {
                "siteId": src["site_id"], "boardId": src["board_id"], "page": page,
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
                if not title:
                    continue
                # 지역조합 게시판은 입찰 외 공지(인사, 행사 등)도 섞여 있으므로
                # 품목 키워드 기준으로 걸러낸다 (main()에서 최종 필터링도 한 번 더 수행)
                if not is_relevant(title):
                    continue
                href = link.get("href", "")
                m = re.search(r"boardSeq=(\d+)", href)
                board_seq = m.group(1) if m else href
                host = f"https://{src['site_id']}.nonghyup.com"
                if href.startswith("http"):
                    full_url = href
                elif href.startswith("/"):
                    full_url = host + href
                else:
                    full_url = f"{host}/user/" + href.lstrip("./")
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
# 3) 도드람양돈농협 — 자동 수집 제외 (2026-07 확인)
#
# ⚠️ 도드람은 입찰공고 게시판을 운영하고 있으나(home.dodram.com),
#    해당 사이트가 robots.txt로 자동 접근을 명시적으로 차단하고 있다.
#    GitHub Actions에서 계속 발생하던 'RemoteDisconnected' 오류도
#    네트워크 문제가 아니라 이 차단 때문이었다.
#    사이트 정책을 존중해 자동 수집 대상에서 제외한다.
#    → 도드람 공고는 담당자가 직접 확인하거나, 이메일 수신분을 활용할 것.
#      게시판 주소: http://home.dodram.com/?page_id=820
# ---------------------------------------------------------------------------
DODRAM_BOARD_ID = "5358073"

# 도드람 그룹 게시판에는 계열사 11곳 공고가 섞여 올라오므로 제목에서 발주처를 구분한다.
DODRAM_COMPANIES = [
    "디에스피드", "도드람푸드시스템", "도드람푸드", "푸르샨식품", "도드람엘피씨",
    "부광산업", "도드람양돈서비스", "도드람에프씨", "대명오앤씨", "도드람김제에프엠씨",
]


def guess_dodram_company(title: str) -> str:
    for c in DODRAM_COMPANIES:
        if c in title:
            return f"도드람-{c}"
    return "도드람양돈농협"


def scrape_dodram(pages: int = 2):
    base = "https://dodram.nonghyup.com/user/boardList.do"
    results = []
    for page in range(1, pages + 1):
        params = {"boardId": DODRAM_BOARD_ID, "siteId": "dodram", "page": page}
        try:
            resp = requests.get(base, params=params, headers=HEADERS, timeout=20)
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"[dodram] page {page} 요청 실패: {e}", file=sys.stderr)
            continue

        # 상세보기 링크 형태: ...boardList.do?command=view&...&boardSeq=NNNNNNN
        links = soup.find_all("a", href=re.compile(r"boardSeq=\d+"))
        for link in links:
            title = link.get_text(strip=True)
            m = re.search(r"boardSeq=(\d+)", link.get("href", ""))
            if not title or not m:
                continue
            board_seq = m.group(1)
            href = link.get("href", "")
            full_url = href if href.startswith("http") else (
                "https://dodram.nonghyup.com/user/" + href.lstrip("./")
            )
            date_text = ""
            row = link.find_parent("tr")
            if row:
                dm = re.search(r"(20\d{2}-\d{2}-\d{2})", row.get_text(" ", strip=True))
                if dm:
                    date_text = dm.group(1)

            results.append({
                "id": make_id("dodram", board_seq),
                "source": guess_dodram_company(title),
                "title": title,
                "date": date_text,
                "url": full_url,
                # 건축·설비공사 공고도 섞여 있으므로 엄격 기준(품목 키워드 필요) 적용
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
                "relevant": is_relevant_on_bid_board(title),
                "bid_board": True,   # 입찰 전용 게시판 → 완화 기준 적용
                "raw_text": f"{title} (게시글 번호 {no})",
            })
    except Exception as e:
        print(f"[ts_feed] 요청 실패: {e}", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# 4-b) 한국사료협회 (kofeed.org) '인사채용/입찰공고' 게시판
#      개별 회사가 아니라 업계 전체 창구라, 회원사들의 공고가 올라온다.
#      ⚠️ 이 사이트는 목록 제목을 자바스크립트로 채우는 구조라 정적 파싱이
#         실패할 수 있다. 실패 시 조용히 0건 반환하고 로그만 남긴다.
#         (첫 실행 로그를 보고 필요하면 방식 변경)
# ---------------------------------------------------------------------------
def scrape_kofeed():
    url = "http://www.kofeed.org/bbs/selectBoardList.do"
    params = {"bbsId": "BBSMSTR_000000000101", "menuNo": "5050000"}
    results = []
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[kofeed] 요청 실패: {e}", file=sys.stderr)
        return results

    links = soup.find_all("a", href=re.compile(r"nttId=\d+|selectBoardArticle"))
    for link in links:
        title = link.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        href = link.get("href", "")
        m = re.search(r"nttId=(\d+)", href)
        ntt_id = m.group(1) if m else href[:40]
        full_url = href if href.startswith("http") else (
            "http://www.kofeed.org" + (href if href.startswith("/") else "/bbs/" + href)
        )
        results.append({
            "id": make_id("kofeed", ntt_id),
            "source": "한국사료협회",
            "title": title,
            "date": "",
            "url": full_url,
            "relevant": is_relevant(title),
            "raw_text": title,
        })

    if not results:
        print("[kofeed] 목록을 파싱하지 못함(JS 렌더링 가능성) - 게시판 직접 확인 필요",
              file=sys.stderr)
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
# 6) Slack #0-06입찰공고 채널 - 직원들이 이메일/팩스로 받은 공고까지 다 올리는
#    채널이라, 게시판이 없는 회사(팜스코, CJ피드앤케어, 카길 등)까지 사실상
#    커버할 수 있는 유일한 방법. 웹 스크래핑 4곳 + 검색탐색과 같은 표에 합쳐진다.
#    - 필요: Slack Bot Token (scopes: channels:history, channels:read) 발급 후
#      해당 봇을 #0-06입찰공고 채널에 초대.
#      GitHub Secrets에 SLACK_BOT_TOKEN 으로 등록.
# ---------------------------------------------------------------------------
SLACK_CHANNEL_ID = os.environ.get("SLACK_BID_CHANNEL_ID", "C08UA9R0VBQ")  # #0-06입찰공고

# 메시지 본문에서 발주처명을 추정하기 위한 키워드. 새 회사가 자주 등장하면
# 이 리스트에 이름만 한 줄 추가하면 다음부터 정확히 분류된다.
COMPANY_KEYWORDS = [
    "농협사료", "안양축협", "대구축협", "서울축협", "양주축협", "인천축협",
    "부산바이오", "군산바이오", "도드람", "디에스피드", "팜스코",
    "TS사료", "TS대한제당", "대한제당", "다원케미칼", "체리부로", "우성사료",
    "한국썸벳", "썸벳", "De Heus", "데헤우스", "부경양돈", "미래부연합", "미래부",
    "동원팜스", "동원팀스", "대한제분", "CJ FEED", "CJ피드앤케어", "이지바이오",
    "이지홀딩스", "안양연합", "고려산업", "대한사료", "세인비에스", "나람",
]


def guess_company(text: str) -> str:
    for kw in COMPANY_KEYWORDS:
        if kw in text:
            return kw
    return "기타(확인 필요)"


def scrape_slack_channel(limit: int = 100):
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("[slack] SLACK_BOT_TOKEN 미설정 - Slack 채널 수집 건너뜀", file=sys.stderr)
        return []

    url = "https://slack.com/api/conversations.history"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"channel": SLACK_CHANNEL_ID, "limit": limit}

    results = []
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        data = resp.json()
    except Exception as e:
        print(f"[slack] 요청 실패: {e}", file=sys.stderr)
        return []

    if not data.get("ok"):
        print(f"[slack] API 오류: {data.get('error')}", file=sys.stderr)
        return []

    for msg in data.get("messages", []):
        text = (msg.get("text") or "").strip()
        ts = msg.get("ts", "")
        files = msg.get("files", [])
        title_line = text.split("\n")[0][:120] if text else (
            files[0].get("name", "첨부파일 공고") if files else "제목 없음"
        )
        if not title_line:
            continue
        permalink = f"https://sein-biobs.slack.com/archives/{SLACK_CHANNEL_ID}/p{ts.replace('.', '')}"
        date_str = ""
        try:
            date_str = datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
        except Exception:
            pass
        results.append({
            "id": make_id("slack", ts),
            "source": guess_company(text),
            "title": title_line,
            "date": date_str,
            "url": permalink,
            "relevant": is_relevant(text) or is_relevant(title_line),
            "origin": "slack",   # 대시보드에서 웹 스크래핑분과 구분 표시하기 위한 태그
            "raw_text": text[:4000],
        })
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
    for fn in (scrape_nonghyup_feed, scrape_nonghyup_local, scrape_dodram,
               scrape_ts_feed, scrape_kofeed, scrape_search_discovery,
               scrape_slack_channel):
        try:
            items = fn()
            print(f"{fn.__name__}: {len(items)}건 수집")
            collected.extend(items)
        except Exception as e:
            print(f"{fn.__name__} 실패: {e}", file=sys.stderr)

    # 사료와 무관한 공고는 아예 목록에서 제외한다.
    before = len(collected)
    dropped = [i for i in collected if not i.get("relevant")]
    collected = [i for i in collected if i.get("relevant")]
    print(f"관련성 필터: {before}건 중 {len(collected)}건 유지 ({len(dropped)}건 제외)")
    # 무엇이 왜 빠졌는지 로그로 남긴다 (필터가 과한지 확인용)
    for d in dropped[:15]:
        print(f"  [제외] {d.get('source', '')} | {d.get('title', '')[:60]}")
    if len(dropped) > 15:
        print(f"  ... 외 {len(dropped) - 15}건")

    # 필터 규칙이 바뀌었을 수 있으므로, 기존에 저장돼 있던 항목도 현재 기준으로 재평가한다.
    # 단, 입찰 전용 게시판(bid_board=True)에서 온 항목은 완화 기준을 적용한다.
    # ⚠️ 판단은 '제목'으로만 한다. raw_text(본문 전체)를 넣으면 사이트 공통
    #    메뉴/푸터의 "채용", "공사" 같은 단어에 걸려 정상 공고가 탈락한다.
    purged = 0
    for key in list(existing_by_id.keys()):
        item = existing_by_id[key]
        title = item.get("title", "")
        check = is_relevant_on_bid_board if item.get("bid_board") else is_relevant
        if not check(title):
            del existing_by_id[key]
            purged += 1
    if purged:
        print(f"기존 저장분 중 무관 항목 {purged}건 정리")

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
