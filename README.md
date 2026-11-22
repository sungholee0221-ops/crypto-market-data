# crypto-market-data

Binance 현물 USDT 페어 상위 100종(스테이블·레버리지 토큰 제외)의 캔들 데이터. GitHub Actions가 매일 00:17 UTC에 갱신하고 `data` 브랜치에 게시한다.

| 경로 (`data` 브랜치) | 내용 |
|---|---|
| `universe.csv` | 종목, 기초 자산, 선정 시 24시간 거래대금 순위, 편입일. 한 번 편입된 종목은 빠지지 않음 |
| `1d/<SYMBOL>.parquet`, `4h/<SYMBOL>.parquet` | 2017-08 이후 전체 |
| `15m/<SYMBOL>.parquet` | 최근 730일 |
| `meta.json`, `probe.json` | 마지막 일봉, 사용 엔드포인트, 접속 확인 결과 |

열: `open_time`(UTC), `open`, `high`, `low`, `close`, `volume`, `quote_volume`, `trades`. 마감된 캔들만.

Binance 공식 엔드포인트(`data-api.binance.vision`, `api.binance.com`)만 사용한다. 접속이 안 되면 새 데이터 없이 이전 데이터를 유지한다.

이 저장소는 시장 데이터만 담는다. 개인 관심 종목·판정 기록은 두지 않는다.
