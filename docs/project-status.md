# Стан проєкту C09 «Паркінги»

## Етап R — збирач звітів .docx: **виконано** (2026-11-04)

| Перевірка | Результат |
|---|---|
| `uv run scripts/build_report.py docs/reports/web-lr1.md --pdf` | `docs/reports/out/Звіт_Web_ЛР1.docx`, 13 сторінок PDF (через Word) |
| Перегляд сторінок | титулка (опечатку «робіти» виправлено), розділи 1.1–1.3 і «Висновки» на місцях, таблиці, рисунки 1–4, лістинги 1–5 з git |
| `pytest scripts/tests` | 9 passed (розділи, рисунок, лістинг, помилки джерела) |

Відхилення від плану етапу R: знімки клієнта зроблено вбудованим браузером, а не скриптом Playwright
(`scripts/screenshots/` не створено); PDF — через Word (LibreOffice не встановлено), сторінки PNG — через PyMuPDF.

## Етап 1 — Web ЛР1: **виконано** (2026-11-04, відповідальна Дарина)

| Перевірка | Результат |
|---|---|
| Міграції з нуля (порожній том pgdata) | 001, 002, 003 (notx), 900 — applied; 901 (англійські назви) — applied пізніше |
| `npx @redocly/cli lint web/openapi.yaml` | valid, 0 помилок, 16 попереджень (службові маршрути й компоненти каркаса) |
| `pytest web/server/tests` (стенд `docker compose up -d nginx`) | 17 passed (після PC-1 і регресу) |
| Гонка: 20 одночасних запитів на 1 місце через app-a і app-b | 1×201, 19×409, `free = 0` |
| Клієнт у браузері (Vite :5173 → nginx :8080) | 409 як Problem Details, 201, список 7 бронювань за 2 сторінки; знімки `docs/reports/img/web-lr1-*.jpg` |
| Журнал app-a/app-b | JSON з `time`, `instance`, `route`, `status`, `duration_ms`; `X-User-Id` — 0 входжень |
| `npm run build`, `eslint` клієнта | без помилок |

Регрес-перевірка: 3 відповіді 500 і 3 вади клієнта виправлено (звіт `docs/reports/web-lr1.md`).

Не зроблено в етапі 1: механізм ідемпотентності (ЛР2; зараз без `Idempotency-Key` — 201, план ЛР2: 400), вхід (ЛР3).

## Етап 0 — репозиторій і спільний Compose: **виконано** (2026-11-04)

Середовище: Windows 11, Docker Desktop 28.4.0 (WSL2), Compose v2.39.4; для WSL виділено 12 ГБ
(`.wslconfig` `memory=12GB`; з 4 ГБ Docker Desktop зависав — див. нижче), 4 CPU.

### Фактично виконані перевірки

| Перевірка | Результат |
|---|---|
| `docker compose config --quiet` | CONFIG_OK |
| `docker compose config --services` | 26 сервісів у типовому наборі (35 з `observability` + `extras`) |
| Збирання образів | `project-web`, `project-dpc-api`, `project-dpc-coordinator`, `project-dpc-spark`, `external-sim` — зібрано (по одному сервісу на образ) |
| `docker compose up -d` | усі сервіси `Up`; `migrate`, `broker-init` — `Exited (0)`; рестартів і OOM немає |
| `/health` :8080, :8001, :8002, :8000 | 200 на всіх чотирьох |
| Теми Kafka | `barrier-events` (1 партиція), `domain-events`, `domain-events.dlq`, `notifications` |
| Ім'я `kafka` з контейнера `api` | розв'язується в IP брокера |
| `docker compose run --rm migrate` | `no migrations in /migrations`, exit 0 |
| `docker compose --profile observability up -d` | Grafana: дашборди «Web project» (`web-project`) і «DPC project» (`dpc-project`); Prometheus: 7 цілей `up` (app-a, app-b, app-canary, gateway-a, gateway-b, api, ray-head); kafka-ui 200, jaeger 200 |
| `docker stats --no-stream` без навантаження | типовий набір ≈3.9 ГіБ; з `observability` ≈4.8 ГіБ. Найбільші: ray-head 739 МіБ, stream 549, grafana 480, spark-worker ×2 ≈455, kafka-ui 381, broker 335, ray-worker ×2 ≈297 |

### Інцидент із пам'яттю

З лімітом WSL 4 ГБ (3.8 ГіБ для Docker) типовий набір займав ≈2.7 ГіБ, але через кілька хвилин
Docker Desktop зависав (500 на всі запити до API), а після перезапуску не стартував через залишені
сокети в `%LOCALAPPDATA%\Docker\run\`. Виправлено: зупинка Docker Desktop, `wsl --shutdown`,
`memory=12GB` у `.wslconfig` (копія — `.wslconfig.bak`), каталог `run` перейменовано на
`run.stale-*`, запуск Docker Desktop. Перезавантаження комп'ютера не знадобилось.
