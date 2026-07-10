#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
입찰공고 구조화 추출기
======================
scrape_bids.py 가 모아온 data/bids.json 의 각 항목(raw_text)을 Claude API로 읽어서
품목 / 마감일 / 수량 을 구조화된 필드로 뽑아낸다.

- 이미 추출된 항목(item['structured'] 존재)은 재호출하지 않는다 (비용 절감).
- 한 번 실행에 최대 MAX_CALLS 건만 처리 (GitHub Actions 매일 실행이므로
  누적 신규 건이 많지 않으면 자연히 다 처리됨. 너무 많으면 다음 실행에 이어서 처리).
- 필요 환경변수: ANTHROPIC_API_KEY (GitHub 저장소 Secrets에 등록)
"""

import json
import os
import re
import sys
import time
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "bids.json"
MAX_CALLS = int(os.environ.get("BID_EXTRACT_MAX_CALLS", "40"))
MODEL = os.environ.get("BID_EXTRACT_MODEL", "claude-haiku-4-5-20251001")  # 비용 우선, 품질 필요시 claude-sonnet-5로 교체

SYSTEM_PROMPT = """너는 사료 원료(아미노산/비타민/보호지방/인산칼슘 등) 구매입찰 공고문에서
핵심 정보를 뽑아내는 도구다. 입력된 텍스트(공고 제목 + 본문 일부)를 읽고 아래 JSON 스키마로만 답하라.
설명, 인사말, 마크다운 코드블록 없이 순수 JSON 객체 하나만 출력한다.

스키마:
{
  "items": ["품목명1", "품목명2"],       // 입찰/견적 대상 품목. 모르면 빈 배열
  "quantity": "예: 월 25~27톤",          // 수량/물량 정보를 원문 표현 그대로. 모르면 ""
  "deadline_raw": "예: 2026.07.15(수) 10:00", // 마감일시 원문 표현. 모르면 ""
  "deadline_date": "YYYY-MM-DD 또는 null", // 마감일을 ISO 날짜로 변환. 알 수 없으면 null
  "buyer_hint": "본문에서 파악되는 발주처명 (이미 알고 있으면 비워도 됨)",
  "confidence": "high | medium | low"    // 추출 확신도
}

규칙:
- 텍스트가 입찰/견적 공고가 아니라 채용, 잡담 등 무관한 내용이면 items를 빈 배열로, confidence를 "low"로 둔다.
- 날짜에 연도가 없으면 문맥상 가장 최근 미래 연도로 추정한다.
- 절대 JSON 외의 텍스트를 출력하지 않는다.
"""


def call_claude(api_key: str, title: str, raw_text: str) -> dict:
    import urllib.request

    body = {
        "model": MODEL,
        "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": f"제목: {title}\n\n본문:\n{raw_text[:3000]}"}
        ],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    raw = "\n".join(text_blocks).strip()
    # 코드블록으로 감싸져 오는 경우 대비
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"items": [], "quantity": "", "deadline_raw": "", "deadline_date": None,
                "buyer_hint": "", "confidence": "low"}


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY 미설정 - 구조화 추출 건너뜀", file=sys.stderr)
        return

    if not DATA_PATH.exists():
        print("bids.json이 없습니다. 먼저 scrape_bids.py를 실행하세요.", file=sys.stderr)
        return

    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    items = payload.get("items", [])

    todo = [i for i in items if not i.get("structured") and i.get("raw_text")]
    todo = todo[:MAX_CALLS]
    print(f"구조화 추출 대상 {len(todo)}건 (전체 미처리 중 최대 {MAX_CALLS}건)")

    done, failed = 0, 0
    for item in todo:
        try:
            structured = call_claude(api_key, item.get("title", ""), item.get("raw_text", ""))
            item["structured"] = structured
            done += 1
        except Exception as e:
            print(f"추출 실패 ({item.get('id')}): {e}", file=sys.stderr)
            failed += 1
        time.sleep(0.3)  # 레이트리밋 여유

    payload["items"] = items
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료: 성공 {done}건 / 실패 {failed}건 -> {DATA_PATH}")


if __name__ == "__main__":
    main()
