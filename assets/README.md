# 기상청 공식 전국 격자표를 이 폴더에 넣으세요.

최신 공식 파일 출처: https://data.kma.go.kr/community/board/selectBoardList.do?bbrdTypeNo=4

이 폴더에 직접 **kma_grid_mapping.xlsx** 또는 **kma_grid_mapping.csv** 이름으로 업로드하면 GitHub Actions가 전국 구별 대표 격자 위치를 자동 추출합니다.

**주의**: CSV/XLSX는 최신 기상청 공식 지도에 기재된 '1단계', '2단계', '격자 X', '격자 Y' 열이 필요합니다.
GitHub Secret에 등록한 인증키를 업로드하거나 이 폴더의 파일에 절대 넣지 마세요.
