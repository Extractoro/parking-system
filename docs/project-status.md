# Стан проєкту C09 «Паркінги»

## Етап 0 — репозиторій і спільний Compose: **виконано частково** (2026-11-04)

### Фактично виконані перевірки

| Перевірка | Результат |
|---|---|
| `docker compose config --quiet` | CONFIG_OK |
| `docker compose config --services` | 26 сервісів у типовому наборі (35 з `observability` + `extras`) |
| Збирання образів | `project-web`, `project-dpc-api`, `project-dpc-coordinator`, `project-dpc-spark`, `external-sim` — зібрано (по одному сервісу на образ) |
| `docker compose up -d` (перший запуск) | усі сервіси `running`; `migrate`, `broker-init` — `Exited (0)`; без рестартів і OOM |
| `/health` :8080, :8001, :8002, :8000 | 200 на всіх чотирьох (повторено після пересоздання стека) |
| Теми Kafka | `barrier-events` (1 партиція), `domain-events`, `domain-events.dlq`, `notifications` |
| Ім'я `kafka` з контейнера `api` | розв'язується в IP брокера (той самий, що й `broker`) |
| `docker compose run --rm migrate` | `no migrations in /migrations`, exit 0 |
| `docker stats --no-stream` (типовий набір, без навантаження) | ≈2.7 ГіБ сумарно; найбільші: stream 518 МіБ, ray-head 458 МіБ, broker 348 МіБ, spark-worker ×2 ≈300 МіБ |

### Не виконано

| Перевірка | Причина |
|---|---|
| `--profile observability`: Grafana з двома дашбордами | Docker Desktop (WSL2, `.wslconfig` `memory=4GB`) зависав: спершу при додаванні профілю `observability` до типового набору, потім і на одному типовому наборі через кілька хвилин після старту. Після перезапуску Docker Desktop не стартує через старі сокети в `AppData\Local\Docker\run\` |
| Стабільність типового набору | на 4 ГБ для WSL не стабільний; потрібне збільшення пам'яті WSL (див. звіт етапу 0) |
