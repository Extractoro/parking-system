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
- Відповідальні за ЛР (`docs/plans/00-initial-analysis.md`, §14): **Дарина** — Web ЛР1, ЛР3, ЛР6; DPC ЛР1, ЛР2, ЛР4
  + екрани клієнта в усіх ЛР; **Вадим** — Web ЛР2, ЛР4, ЛР5; DPC ЛР3, ЛР5, ЛР6 + інфраструктура
  (`compose.yaml`, Kafka, `migrations/`), зв'язок Web↔DPC, спільний README.
- Автор коміту — відповідальний за ЛР, до якої належить зміна; зміни різних власників — окремими комітами.

## Git

- **Ніколи не додавати `Co-Authored-By: Claude` чи інші рядки атрибуції AI** у повідомлення комітів і описи PR.
- Не пушити без окремої вказівки.

## Після кожної задачі

1. Тести відповідної частини; `docker compose config --quiet`.
2. Оновити розділ ЛР у `README.md`, `openapi.yaml` відповідної частини, `docs/project-status.md`,
   статус у `docs/requirements-web.md` / `docs/requirements-dpc.md`.
3. Не заявляти «працює», якщо перевірено лише збирання. Указувати, що саме запущено й що отримано.

## Після кожної ЛР

Один звіт на команду на кожну ЛР (титулка з обома). Структура джерела —
`docs/plans/stage-R-report-pipeline.md` §2, **форма тексту — строго `docs/reports/STYLE.md`**
(нумерація «1.2.N», без кодів пунктів і жирних міток, без шляхів до файлів, без тире «—»/«–»),
зразок — `docs/reports/web-lr1.md`. Перевірка: `uv run scripts/build_report.py <джерело> --lint-only`.
1. Зберегти виводи перевірок у `results/<web|dpc>-lrN/` (`| tee`); знімки клієнта — у `docs/reports/img/`.
2. Написати `docs/reports/<web|dpc>-lrN.md`: front matter (`course`, `lab`, `title`, повний `commit`
   перевірок, `date`), `## Мета роботи`, `## Хід роботи` (по `###` на кожен виконаний пункт: що зроблено,
   де, як перевірено, очікуваний і фактичний результат), `## Код програми` (3–6 маркерів
   `<!-- code: path#La-Lb -->`, ≤ 60 рядків кожен), `## Висновки` (абзац із числами). Лише фактичні результати.
3. `uv run scripts/build_report.py docs/reports/<course>-lrN.md --pdf` → `docs/reports/out/Звіт_<Курс>_ЛРN.docx`;
   переглянути 2–3 сторінки PNG (титулка, таблиця, рисунок, лістинг).
4. `docs/plans/` і `docs/reports/` (плани, джерела звітів, знімки, `.docx`) — лише локально, у `.gitignore`;
   комітити `results/` і зміни коду — автор відповідальний за ЛР.

## Корисне

- Git Bash на Windows переписує шляхи в `docker compose exec ... /opt/...`: використовуйте `MSYS_NO_PATHCONV=1`.
- Збирання: `docker compose build app-a api coordinator-a spark-master external-sim` (по одному сервісу
  на образ; паралельне `build` усіх сервісів падає з `image ... already exists`).
- Пам'ять: повний набір ≈3.9 ГіБ без навантаження; для задачі однієї частини піднімати лише її
  сервіси (`docker compose up -d nginx` — Web, `docker compose up -d api` — DPC; таблиця в README, розділ 5).
- Довідка каркасів: `web/KIT-README.md`, `dpc/KIT-README.md`, `*/docs/ENVIRONMENT.md`.
