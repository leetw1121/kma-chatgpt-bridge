# 독립 예약 실행 복구: Cloudflare Worker Cron → GitHub workflow_dispatch

## 왜 이 코드가 필요한가

2026-11-14 11:50/12:10/12:20 KST, GitHub는 `schedule` 실행 기록을 아예 생성하지 않았습니다.
반면 `push` 트리거로 실행된 똑같은 `update_kma.yml` 코드는 174개 구·시 대표 격자를 수집하고 PDF를 생성했습니다.

따라서 기상청 수집·PDF 코드 오류가 아닌 **GitHub 내부 예약 트리거 미발행/지연 문제**가 이번 확인된 직접적 장애입니다. GitHub의 서버 내부 구체적인 결함은 확인되지 않았습니다.
GitHub 예약 시도 횟수만 늘리면 같은 서비스의 공통 장애를 피하지 못합니다. 완화책으로 **다른 회사 Cloudflare의 Cron**이 PDF 신선도를 확인하고, 실제 갱신이 필요할 때만 GitHub의 수동 실행 API(`workflow_dispatch`)를 호출합니다.

Cloudflare는 **PDF를 직접 만들지 않으며**, 기존 기상청 수집 프로그램과 PDF 생성 프로그램은 GitHub Actions에서 그대로 실행합니다.
GitHub Actions 실행 서비스 전체가 중단되면 이 방식도 동작하지 않습니다. Cloudflare를 통한 트리거는 GitHub의 *cron 트리거* 장애에 대한 독립 예비 경로입니다.

## 준비물

1. 본인 Cloudflare 계정(무료 Workers 기능 포함 여부를 직접 확인).
2. GitHub **fine-grained personal access token(PAT)** 하나. 저장소는 반드시 `leetw1121/kma-chatgpt-bridge` **하나만 선택**, Repository permissions에서 **Actions: Read and write**. Contents 등 필요 없는 권한은 부여하지 않습니다. 만료 시 재발급해야 합니다.
3. **API 키/토큰을 이 저장소·ChatGPT·스크린샷에 붙여넣지 않습니다.** Cloudflare Worker secret `GITHUB_ACTIONS_TOKEN`에만 넣습니다.

이 외부 백업 스케줄러가 GitHub Actions를 호출할 때 기상청 API 인증키 `KMA_SERVICE_KEY` 자체는 필요하지 않습니다. 기상청 키는 기존 GitHub Actions Secrets에 그대로 유지됩니다.

## Cloudflare 대시보드에서 설정 (PC의 PowerShell 불필요)

1. [Cloudflare dashboard](https://dash.cloudflare.com/) → **Workers & Pages** → **Create Worker**에서 Worker 생성.
2. Worker의 코드 편집기에서 이 폴더의 [worker.mjs](worker.mjs) 내용을 넣어 저장·배포.
3. Worker **Settings → Variables and Secrets**(메뉴 명칭은 UI 버전에 따라 다를 수 있음) → Secret `GITHUB_ACTIONS_TOKEN`을 추가하고 GitHub에서 발급한 본인 PAT를 값으로 저장. 환경 변수로 평문 공개하지 말 것.
4. Worker의 **Triggers / Cron Triggers**에서 UTC 크론 식 `12 3,9,15,21 * * *` 을 등록.
   - 한국시간 00:12 / 06:12 / 12:12 / 18:12 (각 ChatGPT 30분 알림 전에 실행)
5. Worker의 Cron Events / Logs에서 최신 실행 결과를 확인.
   - `No dispatch; PDF already fresh` : 최신 PDF가 있어서 GitHub 수집 불필요.
   - `Backup GitHub workflow dispatch sent` : PDF가 없거나 구버전이라 GitHub 작업을 강제 시작.
   - `Existing KMA job is active` : GitHub에서 이미 신규 작업 중이라 중복 시작 방지.
   - HTTP 401/403: 토큰 오류·만료·Actions 권한 부족. Cloudflare secret 확인.
6. GitHub [Actions 실행 기록](https://github.com/leetw1121/kma-chatgpt-bridge/actions/workflows/update_kma.yml)에서 Event가 `workflow_dispatch`인 실행이 생성됐는지 확인. 이후 [PDF 메타데이터](../../data/pdf_manifest.json)의 `source_issue_at_kst`, `source_generated_at_kst`가 실제 최신 기상청 데이터와 일치하는지도 확인.

참고: Worker 코드에는 토큰이 없습니다. Secret 등록을 마쳐야 백업 실행이 *실제로 활성화*됩니다. 단순히 이 폴더를 GitHub에 저장한 것으로는 외부 스케줄러가 작동하지 않습니다.

## 직접 코드로 배포 (선택)

Cloudflare Wrangler를 사용하는 경우 이 폴더에서 `wrangler deploy`, `wrangler secret put GITHUB_ACTIONS_TOKEN` 등으로 설정할 수도 있습니다. `wrangler.toml`에 실제 토큰을 넣지 마세요.

## 신선도 검증·호출 범위

- KST 시각과 기상청 단기예보 발표 기준시각(02/05/08/11/14/17/20/23시)을 비교하며, 발표 후 최소 18분 여유를 둡니다.
- `data/latest.json`과 `data/pdf_manifest.json`의 기상청 발표시각, 데이터 수집시각, PDF 생성 범위와 수집 성공률을 비교.
- 이미 해당 발표본 PDF가 완성돼 있으면 API 호출을 하지 않습니다.
- 이전 수집본을 최신으로 표기하거나, 미제공 구별 강수량·적설량을 생성하지 않습니다.
- GitHub workflow가 최근 이미 실행 중이면 중복 디스패치를 피합니다.
- 외부 HTTP 요청 실패는 Worker 로그에 남깁니다. 예보를 조회했다고 거짓 보고하지 않습니다.

## 추후 개선 (외부 백업 정상 운영 확인 후)

현재 GitHub 자체 `schedule`은 더 자주 실행하도록 설정되어 있습니다. **Cloudflare 백업 운영이 실제 성공한 것을 확인한 뒤** GitHub 예약을 각 알림 전 1~2회 정도로 줄이고 독립 예약 중복을 방지할 수 있습니다.

기존 ChatGPT 알림 예약(00:30,06:30,12:30,18:30 KST)은 변경하지 않습니다.

공식 문서:
- [GitHub schedule 지연·누락](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)
- [GitHub workflow_dispatch REST API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
- [Cloudflare Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
