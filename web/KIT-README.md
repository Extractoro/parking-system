# Обмін даними у Web-застосунках – каркас проєкту

Каркас проєкту курсу «Обмін даними у Web-застосунках». Приклади контрактів і даних описують міську бібліотеку: читач бронює книгу, сервер обробляє операцію, а клієнт отримує її стан і сповіщення.

HTTP-запити проходять через nginx до `app-a` або `app-b`. Стан зберігається в PostgreSQL.
Kafka передає події фоновим процесам і шлюзам WebSocket. `publisher` – публікатор подій,
`worker` – фоновий обробник, `router` – маршрутизатор сповіщень. Їхня предметна логіка
позначена `TODO`. Наявність процесу не означає реалізації відповідного механізму.

## Склад

| Каталог | Що там |
|---|---|
| `server/` | каркас: API, шлюз, `publisher`, `worker`, `router` (заглушки з `TODO`) – [README](server/README.md) |
| `client/` | оболонка вебклієнта |
| `migrations/` | міграції проєкту; `examples/` – приклад схеми й тестових даних |
| `configs/` | nginx, Kafka, RabbitMQ, Keycloak, OpenTelemetry, Prometheus, Grafana, прапорці функцій (feature flags), Swarm |
| `tools/` | симулятор, генератор, навантаження, події відсутньої частини DPC, події з файлу в Kafka, `ws-fleet`, оновлення, повільний клієнт, сторонній сайт |
| `docs/` | середовище Compose, налаштування перевірки, керовані відмови, таблиця сумісності |
| `joint/` | спільний проєкт із DPC |
| `*.example.yaml` | заповнені приклади `openapi`, `asyncapi`, `checks` для області «Міська бібліотека» |

## Запуск

Потрібні Docker із Compose v2 (пам'ять для Docker – від 6 ГБ), Python 3.14 і
[uv](https://docs.astral.sh/uv/) для інструментів, Node.js 22 для `ws-fleet`. Команди
виконуються з кореня каталогу курсу.

```sh
cp .env.example .env
docker compose up -d --build
docker compose ps
curl -i http://localhost:8080/health
python -m http.server 8081 --bind 127.0.0.1 --directory client   # клієнт – в окремому терміналі
```

Запуск надає інфраструктуру й службові маршрути каркаса, але не готову предметну систему.
Щоб створити таблиці й тестові дані прикладу, скопіюйте `migrations/examples/*.sql`
у `migrations/` і виконайте `docker compose run --rm migrate`.

| Що | Адреса |
|---|---|
| API через nginx | http://localhost:8080 |
| `app-a`, `app-b`, `gateway-a`, `gateway-b` напряму | :8001, :8002, :8091, :8092 |
| вебклієнт; сторонній сайт (О3.3) | http://localhost:8081; http://localhost:8082 |
| симулятор, прапорці функцій (протокол OFREP – OpenFeature Remote Evaluation Protocol) | :8090, :8099 |
| PostgreSQL (`app` / `app`, база `app`) – для pgAdmin чи іншого клієнта | localhost:5432 |
| Kafka з хоста, Kafka UI | localhost:9094, http://localhost:8085 |
| Redis, Keycloak (`admin` / `admin`) | localhost:6379, http://localhost:8180 |
| Jaeger, Prometheus, Grafana | :16686, :9090, :3000 |

Порти можна змінити в `.env`. Тоді виправте адреси й у `checks.yaml`.

## Документація

- [Шаблон README реалізованого проєкту](README-template.md) – опис системи й результатів для викладача.
- [Середовище Docker Compose](docs/ENVIRONMENT.md) – Kafka, попередня версія, інша мова, типові проблеми, довідник компонентів.
- [Налаштування перевірки](docs/CHECKS.md) – як заповнити `checks.yaml` за [прикладом](checks.example.yaml): пункти, ролі операцій, приклади тіл і ключі.
- [Міграції](migrations/README.md) – версії схеми й тестові дані.
- [HTTP-навантаження](tools/workload/README.md) – сценарії, параметри й результати вимірювань.
- [Керовані відмови](docs/FAULTS.md) – механізм одноразової аварії процесу.
- [Docker Swarm](configs/swarm/README.md) – конфігурація для кількох вузлів.
- [Інтеграція Web і DPC](joint/README.md) – межа компонентів спільного проєкту.
