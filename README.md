# 대한민국 전국 비·눈 정밀예보 — KMA → ChatGPT

기상청 단기예보 `getVilageFcst`를 GitHub Actions가 자동 수집하며, **인증키가 없는 공개 예보 결과만** 저장합니다. ChatGPT 기존 예약은 이 저장소의 최신 결과를 확인하고 기상청 단기/중기/특보를 보완합니다.

## 자동 실행 / 원본 결과

- 수집: 00:12 / 06:12 / 12:12 / 18:12 **KST** (GitHub 예약 실행은 지연/누락 가능)
- 기존 ChatGPT 알림: 00:30 / 06:30 / 12:30 / 18:30 KST
- 정량 단기예보: [data/latest.json](data/latest.json) · [data/summary.md](data/summary.md)
- GitHub Actions: [KMA forecast feed](.github/workflows/update_kma.yml)
- 공식 API: https://www.data.go.kr/data/15084084/openapi.do
- 인증키는 GitHub Secret `KMA_SERVICE_KEY`에 **한 번만** 등록합니다. 채팅이나 공개 파일에 넣지 않습니다.

## 실제 적용된 데이터 품질 보호

- 발표시각·수집 시작/완료시각을 구분하고 최근 수집을 검증합니다.
- 구 대표 격자는 동일 좌표가 겹치면 **한 번만 API 호출**합니다.
- API 요청은 최대 4개 고유 격자씩 병렬 조회합니다.
- 1시간 강수량(PCP)은 **비 + 눈을 포함한 총강수량**이며 신적설(SNO, cm)과 구분합니다.
- 범주형 강수량(예: `1mm 미만`)은 정확한 단일 수치가 아닌 **가능 범위**로 보존합니다.
- 조회된 모든 시각이 하루 00~23시인지 기록합니다. 일부 시각 자료를 24시간 누적으로 표시하지 않습니다.
- 우열을 확정할 수 없는 강수량 구간은 엄밀한 1위/2위로 주장하지 않습니다.
- KMA 단기예보로 **7일 전체의 구별 정량 강수량을 제공한다고 주장하지 않습니다.** 나머지 날짜는 공식 중기예보로 보완합니다.

## 전국 구별 격자 수집 확대 (최초 1회)

현재 `locations.csv`는 API 검증을 위해 9개 대표 위치만 포함합니다. **전국 구별로 확대하려면 기상청의 공식 최신 격자 위치표가 필요합니다.** 임의의 좌표를 만들어 전수 순위라고 주장하지 않습니다.

공식 기준자료: [기상자료개방포털 자료실](https://data.kma.go.kr/community/board/selectBoardList.do?bbrdTypeNo=4) 의 **[기상예보] 동네예보 지점 좌표 정보** 중 최신 게시물(2026-11-12 기준 2026년 3분기, 게시일 10월 19일).

1. 공식 자료실에서 최신 격자 위경도 CSV 또는 XLSX를 다운로드합니다.
2. 이 저장소 `assets/`에 다음 이름으로 업로드합니다: **`kma_grid_mapping.xlsx`** 또는 **`kma_grid_mapping.csv`** (원본 엑셀/CSV가 아니면 안 됩니다).
3. 업로드 후 GitHub Actions가 해당 파일의 `1단계`/`2단계`/`격자 X`/`격자 Y`를 읽고 `locations.csv`를 자동 생성합니다.
4. 변경된 `locations.csv`가 저장소에 기록되며 전국 구·시 대표격자 예보를 자동 조회합니다. 중복된 격자는 한 번만 요청합니다.
5. XLSX 파일은 자동화 실행 시에만 `openpyxl`을 설치하여 읽습니다. **Windows PowerShell과 매번 인증키 입력은 필요 없습니다.**

참고: 2026년 실제 행정구역 명칭을 보장하려면 반드시 최신 기상청 파일을 사용하세요. 새로 발표된 지도는 같은 파일명으로 교체 업로드할 수 있습니다. 자동 수집은 여전히 예보의 **구 전체 평균/최대**가 아닌 **구 대표 5km 격자**를 의미합니다.

## GitHub 자동화 실행이 지연되거나 실패하는 경우

GitHub는 예약 실행을 지연 또는 일부 생략할 수 있다고 안내합니다: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule . 따라서 ChatGPT는 공개 데이터의 **실제 완료시각·발표시각·조회 성공 지점수**를 확인하고 오래된 데이터는 최신 예보로 쓰지 않습니다.

자동화 성공 여부는 [Actions](../../actions/workflows/update_kma.yml) 탭에서 확인합니다. 인증키나 API 요청 URL은 공개하지 마세요.
