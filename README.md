# 전국 비·눈 정밀예보 — KMA → ChatGPT 연동

기상청 공공데이터포털 단기예보를 GitHub Actions가 자동 수집해 **공개 예보 결과만** `data/latest.json` 및 `data/summary.md`에 기록합니다.

- 기상청 API 인증키는 **GitHub Actions Secret `KMA_SERVICE_KEY`** 에만 등록합니다.
- 자동 실행: 매일 한국시간 00:12 / 06:12 / 12:12 / 18:12 (GitHub 실행 지연 가능)
- 기존 ChatGPT 알림: 한국시간 00:30 / 06:30 / 12:30 / 18:30
- 처음에는 연결 확인을 위한 일부 대표 격자만 조회하며, **전국 모든 구의 전수 순위가 아닙니다.**
- 하루 시간별 강수량(PCP), 신적설(SNO)이 범주로 제공되면 범위를 유지하며 정확한 단일 수치처럼 다루지 않습니다.
- 이 작업이 사용하는 기상청 단기예보는 7일 전체 정량자료를 제공하지 않습니다. 나머지는 기상청 중기예보로 별도 보완합니다.

## 최초 1회 준비
1. GitHub 저장소 **Settings → Secrets and variables → Actions → New repository secret** 열기
2. 이름: `KMA_SERVICE_KEY`, 값: 공공데이터포털 **일반 인증키(Decoding)** 입력
3. 저장소 **Actions → KMA forecast feed → Run workflow** 수동 첫 실행
4. 성공 시 생성되는 [summary.md](data/summary.md)의 수집시각·지역수를 확인

인증키는 채팅, 코드, 공개 저장소 또는 스크린샷에 올리지 마세요.
