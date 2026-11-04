# Середовище Docker Compose

Конфігурація – [compose.yaml](../compose.yaml). Запуск стенда й адреси сервісів описано в
[README](../README.md#запуск). Усі команди виконуються з кореня каталогу курсу. Приклади
команд наведено для Bash.

## Kafka

Тема (topic) Kafka об’єднує повідомлення одного потоку. Команди нижче стосуються навчальних даних. Видалення теми стирає її повідомлення.

```sh
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:9092 \
  --topic gate-events --from-beginning --property print.key=true
# тема заново (дослід з нуля або інша кількість партицій)
docker compose stop stream
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --delete --topic gate-events
docker compose run --rm kafka-init
docker compose run --rm stream rm -rf /data/checkpoints/stream && docker compose up -d stream
```

## Інша мова

Замініть `api/Dockerfile` (або `coordinator/Dockerfile`) і рядки `command` у `compose.yaml`.
Процес слухає порт `8000`, читає налаштування зі змінних середовища, має `/health` і `/metrics`,
пише журнал JSON у stdout і реалізує керовані відмови за [FAULTS.md](FAULTS.md).
Для вузлів Ray потрібен образ із Python і Ray тієї самої версії, що в клієнті.
Для профілювання підходять async-profiler (JVM), `--inspect` або clinic (Node.js), pprof (Go).
Параметр `cap_add: SYS_PTRACE` уже задано.

## Типові проблеми

| Симптом | Що зробити |
|---|---|
| `port is already allocated` | змінити порт у `.env` |
| `\r: not found` у скрипті | кінці рядків CRLF. Виконати `git config core.autocrlf input` і клонувати заново |
| контейнери зупиняються (`exit 137`) | бракує пам'яті. Збільшити пам'ять Docker або зупинити непотрібні сервіси |
| `api` не підключається до Ray | версії Python і Ray в `api` і на вузлах мають збігатися (один образ). Переглянути `docker compose logs ray-head` |
| `stream` перезапускається | Kafka ще не готова або в `stream.py` помилка. Переглянути `docker compose logs stream` |

## Довідник компонентів

| Компонент або можливість | Налаштування чи команда |
|---|---|
| каркас `api`, режим `RESOURCE_MODE` | `api/app/main.py` |
| навантаження | `python tools/load.py http://localhost:8000/v1/returns tools/body.example.json --requests 400 --concurrency 60` |
| `/metrics`, Prometheus, Grafana «DPC project» | `metrics.gauge("queue_length", ...)`, `in_flight`, `rejected` |
| `stop_grace_period: 120s` | `docker compose stop api` |
| `py-spy`, таблиця прискорення | `docker compose exec api py-spy record -o /data/profile.svg --pid 1 --duration 20`; `nproc`; `python tools/bench_table.py runs.csv` |
| Ray з образу `api`, `EXECUTION=ray`, `IN_FLIGHT_LIMIT`, `RECEIVER_DELAY_MS` | панель Ray :8265 |
| керована відмова | [FAULTS.md](FAULTS.md) |
| `ray-worker-extra` (ресурс `extra`) | `@ray.remote(resources={"extra": 1})` |
| генератор історії | `uv run tools/generator/generate_history.py tools/generator/spec.example.json --out data --extend tools/generator/columns.example.py`, потім `docker compose cp ./data/. api:/data/` |
| запускач і заготовка програми формування звіту | [spark/jobs/README.md](../spark/jobs/README.md) |
| `SPARK_AQE`, `BROADCAST_THRESHOLD`, сервер історії Spark, метрики стадій | `python tools/spark_stages.py http://localhost:18080` |
| втрата виконавця Spark (executor) | `docker compose kill spark-worker-1` під час звіту |
| генератор подій (сценарії `normal`, `late`, `duplicates`, `boundary`; службова подія `tick` фіналізує останнє вікно) | `python tools/generator/generate_events.py tools/generator/events.example.json --scenario late --out late.jsonl` |
| Kafka, тема (topic) `EVENTS_TOPIC`, заготовка `stream.py` | `uv run tools/jsonl_to_kafka.py late.jsonl gate-events --key gate_id` |
| одна партиція | `EVENTS_PARTITIONS=1` у `.env`, повторне створення теми описане в розділі «Kafka» |
| контрольна точка (checkpoint) у томі `data` | `docker compose kill stream && docker compose up -d stream` |
| etcd, координатори, спостереження за роллю | `python tools/etcd_watch.py /roles/assigner`; `docker compose pause coordinator-a` |
| режим без перевірки покоління | `FENCING=off` у `.env` |
| стек Swarm | [configs/swarm/README.md](../configs/swarm/README.md) |
