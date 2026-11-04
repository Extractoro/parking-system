# C09 «Паркінги» — спільний проєкт Web + DPC

Курси «Обмін даними у Web-застосунках» (Web) і «Розподілені та паралельні обчислення» (DPC).
Команда: Вадим, Дарина. Плани етапів — `docs/plans/README.md` (строго по одному, по черзі).

## Частини й стан

- **Web** (`web/`): FastAPI з каркаса, nginx `:8080` → app-a/app-b; gateway, publisher, worker, router;
  клієнт React + TS + Vite у `web/client/` поза Compose. Контракт — `web/openapi.yaml`.
  Володіє: каталог `garage`, `level`, `level_hour`, `booking`, `users`, `operations`, `outbox`, `notifications`.
- **DPC** (`dpc/`): FastAPI api `:8000`, Ray, Spark (report + stream), etcd + coordinator-a/b.
  Контракт — `dpc/openapi.yaml`. Володіє: зайнятість `garage` (spots, occupied), `entry`,
  `tariff`, покоління/історія координатора, таблиці panel/report.
- Спільні лише PostgreSQL (одна база) і Kafka (сервіс `broker`, псевдонім `kafka`). Один `compose.yaml` у корені.

## Правила

- Жодна частина не пише в таблиці іншої; читати зафіксований результат іншої частини можна.
  Статус бронювання `entered` Web обчислює з таблиці въїздів DPC (у БД бронювання лишається `confirmed`).
- Деньги — цілі копійки (`bigint`); час — `timestamptz`, ім'я зони — `garage.tz`; ID у JSON — рядки.
- Керовані відмови — лише через `fault("<name>")` каркаса і `FAULTS=on` (`web/docs/FAULTS.md`, `dpc/docs/FAULTS.md`).
- Міграції — лише новими файлами `migrations/NNN_name.sql`, нумерація спільна для Web і DPC;
  застосовані файли не редагувати.
- Рішення, що не переглядаються без запису, — `docs/decisions.md`.
- Файли-межі: `compose.yaml`, `migrations/` — Вадим; `web/client/`, `dpc/spark/jobs/` — Дарина.

## Git

- **Ніколи не додавати `Co-Authored-By: Claude` чи інші рядки атрибуції AI** у повідомлення комітів і описи PR.
- Не пушити без окремої вказівки.

## Після кожної задачі

1. Тести відповідної частини; `docker compose config --quiet`.
2. Оновити розділ ЛР у `README.md`, `openapi.yaml` відповідної частини, `docs/project-status.md`,
   статус у `docs/requirements-web.md` / `docs/requirements-dpc.md`.
3. Не заявляти «працює», якщо перевірено лише збирання. Указувати, що саме запущено й що отримано.

## Після кожної ЛР

Створити `docs/reports/<web|dpc>-lrN.md` (з нього збирається docx-звіт, `docs/reports/report-spec.md`):
- виконані пункти й де реалізовано;
- команди дослідів, параметри, фактичний вивід (скорочений);
- шляхи до знімків екрана в `docs/reports/img/`;
- файли й рядки 3–6 ключових фрагментів коду;
- хеш коміту.
Лише фактичні результати.

## Корисне

- Git Bash на Windows переписує шляхи в `docker compose exec ... /opt/...`: використовуйте `MSYS_NO_PATHCONV=1`.
- Збирання: `docker compose build app-a api coordinator-a spark-master external-sim` (по одному сервісу
  на образ; паралельне `build` усіх сервісів падає з `image ... already exists`).
- Пам'ять: повний набір ≈3.9 ГіБ без навантаження; для задачі однієї частини піднімати лише її
  сервіси (`docker compose up -d nginx` — Web, `docker compose up -d api` — DPC; таблиця в README, розділ 5).
- Довідка каркасів: `web/KIT-README.md`, `dpc/KIT-README.md`, `*/docs/ENVIRONMENT.md`.
