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

**2026-09-30 좌표표 적용 완료:** 사용자가 제공한 기상청 공식 원본 `동네예보지점좌표(위경도)_260930.xlsx`에서 기상청이 명시한 *2단계 행정단위의 대표 격자 행*만 추출했습니다. 현재 `locations.csv`는 **174개 행정지역(구 109개·시 65개), 서로 다른 격자 144개**를 포함합니다. 군은 사용자가 요청한 출력 범위에서 제외합니다. 구 전체의 평균/최고 강수량이 아니라 구의 대표 격자 예보임을 유의하세요.

원본 Excel SHA-256: `76c1bdae7b5299c81e0582b42d1507775183603072f141ce8d96e6054851b391`. 공식 2단계 좌표만 추출한 재현용 자료는 `assets/kma_grid_mapping.csv`에 보관했습니다.

공식 기준자료: [기상자료개방포털 자료실](https://data.kma.go.kr/community/board/selectBoardList.do?bbrdTypeNo=4) 의 **[기상예보] 동네예보 지점 좌표 정보** 중 최신 게시물(2026-11-12 기준 2026년 3분기, 게시일 10월 19일).

1. 공식 자료실에서 최신 격자 위경도 CSV 또는 XLSX를 다운로드합니다.
2. 이 저장소 `assets/`에 다음 이름으로 업로드합니다: **`kma_grid_mapping.xlsx`** 또는 **`kma_grid_mapping.csv`** (원본 엑셀/CSV가 아니면 안 됩니다).
3. 업로드 후 GitHub Actions가 **3단계(동·읍·면)가 비어 있는 공식 행**의 `1단계`/`2단계`/`격자 X`/`격자 Y`를 읽고 `locations.csv`를 자동 생성합니다. 동 단위 좌표를 투표처럼 다수결하여 구의 대표 좌표로 바꾸지 않습니다.
4. 변경된 `locations.csv`가 저장소에 기록되며 전국 구·시 대표격자 예보를 자동 조회합니다. 중복된 격자는 한 번만 요청합니다.
5. XLSX 파일은 자동화 실행 시에만 `openpyxl`을 설치하여 읽습니다. **Windows PowerShell과 매번 인증키 입력은 필요 없습니다.**

참고: 2026년 실제 행정구역 명칭을 보장하려면 반드시 최신 기상청 파일을 사용하세요. 새로 발표된 지도는 같은 파일명으로 교체 업로드할 수 있습니다. 자동 수집은 여전히 예보의 **구 전체 평균/최대**가 아닌 **구 대표 5km 격자**를 의미합니다.

## 예약 실행 신뢰성 / 독립 예비 스케줄러

GitHub 예약 실행이 아예 생성되지 않는 사례가 2026-11-14에 확인됐습니다. 같은 GitHub cron의 반복 횟수를 늘리는 것만으로는 독립적인 복구가 되지 않습니다.

- **완료:** Cloudflare Workers Cron 예비 트리거 코드와 최신 기상청 발표본/PDF 검증 코드 등록. Node 테스트는 별도 GitHub Actions에서 성공.
- **미완료:** Cloudflare 계정에 Worker 실제 배포 및 저장소 한정 Actions:write 인증 Secret 등록. **아직 독립 예비 스케줄러는 가동되지 않습니다.**
- 설정 안내: [Cloudflare 독립 백업 스케줄러 설치 방법](backup-scheduler/cloudflare-worker/README.md).
- 적용 후 Cloudflare Cron이 최신 PDF를 먼저 검사하고, 누락됐을 때만 GitHub `workflow_dispatch`로 수집을 시작합니다. 기존 GitHub 예약과 ChatGPT 30분 알림은 유지합니다.

## GitHub 자동화 실행이 지연되거나 실패하는 경우

GitHub는 예약 실행을 지연 또는 일부 생략할 수 있다고 안내합니다: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule . 따라서 ChatGPT는 공개 데이터의 **실제 완료시각·발표시각·조회 성공 지점수**를 확인하고 오래된 데이터는 최신 예보로 쓰지 않습니다.

자동화 성공 여부는 [Actions](../../actions/workflows/update_kma.yml) 탭에서 확인합니다. 인증키나 API 요청 URL은 공개하지 마세요.
