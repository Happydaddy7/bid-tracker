# 세인바이오 입찰공고 트래커

사료 원료 구매입찰 공고를 매일 자동으로 수집해서, Claude가 품목·마감일·수량을 정리해주는 대시보드입니다.

배포 주소(GitHub Pages 켠 뒤): `https://<사용자명>.github.io/<저장소명>/`

## 수집 소스
- 농협사료(본사) 구매입찰 게시판
- 안양축협 (다른 지역조합도 코드 한 줄 추가로 확장 가능)
- 도드람양돈농협
- TS사료
- 검색 기반 자동 탐색 (Google Custom Search, 신규 회사 발견용 — 선택)

팜스코·CJ피드앤케어·카길·이지바이오처럼 홈페이지에 입찰 게시판 자체가 없는 회사는
애초에 인터넷에 공개된 정보가 없어 수집 대상이 아닙니다.

## 최초 설정 (딱 한 번만)

### 1. 이 파일들을 새 GitHub 저장소에 올리기
`index.html`, `scripts/`, `.github/workflows/`, `data/bids.json` 구조 그대로 올려주세요.

### 2. GitHub Pages 켜기
저장소 **Settings → Pages** → Source를 "Deploy from a branch" → `main` / `(root)` 선택 → Save.
몇 분 후 위 배포 주소로 접속하면 대시보드가 보입니다.

### 3. 키 등록
저장소 **Settings → Secrets and variables → Actions → New repository secret** 에서 등록:

| 이름 | 용도 | 필수 여부 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 품목/마감일/수량 자동 추출 (Claude API) | **필수** |
| `GOOGLE_CSE_API_KEY` | 검색 기반 신규 회사 자동 탐색 | 선택 |
| `GOOGLE_CSE_CX` | 검색 기반 신규 회사 자동 탐색 | 선택 |

`ANTHROPIC_API_KEY`는 [console.anthropic.com](https://console.anthropic.com)에서 발급합니다.
검색 탐색 키가 없으면 그 기능만 조용히 건너뛰고 나머지 4개 소스는 정상 동작합니다.

### 4. 첫 실행
저장소 **Actions** 탭 → "입찰공고 자동 수집" 워크플로 선택 → **Run workflow** 버튼으로 수동 실행.
초록 체크가 뜨면 성공, 실패하면 로그를 확인하세요.

## 이후
매일 한국시간 오전 9시에 자동으로 돌아갑니다. 별도로 손 댈 일 없습니다.

## 회사 추가하고 싶을 때
`scripts/scrape_bids.py` 안의 `NONGHYUP_LOCAL_SOURCES` 리스트에 지역조합을 한 줄 추가하면
바로 확장됩니다. 그 외 사이트는 구조가 달라 전용 스크래퍼를 새로 작성해야 합니다.
