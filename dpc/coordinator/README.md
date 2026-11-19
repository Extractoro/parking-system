# Координатор (ЛР6): менеджер динамічного тарифу

Сервіси `coordinator-a` і `coordinator-b` збираються з цього каталогу (лише стандартна бібліотека Python).
`etcd.py` - тонкий клієнт etcd v3 (оренда, продовження, транзакція, читання, спостереження від ревізії),
`main.py` - координатор.

- Роль - ключ `/roles/tariff-manager`, значення - ім'я координатора. Оренда `LEASE_TTL_S=5`, продовження кожні
  `RENEW_EVERY_S=1.5` с окремим потоком; захоплення - одна транзакція `IF create_revision == 0 THEN PUT … lease`.
- Покоління = `create_revision` ключа. Власник реєструє його на ресурсі для кожного паркінгу
  (`POST /v1/tariff/generations`) і надсилає з кожним записом (`PUT /v1/tariff/{garage_id}`); перевіряє ресурс.
- Кожні `TARIFF_EVERY_S=3` с: тариф = `base · (1 + occupied/spots)` з `GET /v1/garage/{id}`, до 100 коп
  (`ROUNDING`: A - вгору, B - вниз).
- Втрата оренди (TTL 0 або немає продовження весь TTL) чи 409 від ресурсу - `role.lost`, запис припинено,
  очікування видалення ключа. SIGTERM відкликає оренду (`role.released`).
- Журнал JSON: `role.acquired`, `generation.registered`, `tariff.prepared`, `tariff.write` (`accepted`, `reason`),
  `role.lost`, `role.waiting`.

Команди виконуються з кореня проєкту.

```sh
python dpc/tools/etcd_watch.py /roles/tariff-manager
docker compose logs -f coordinator-a coordinator-b
docker compose pause coordinator-a && sleep 9 && docker compose unpause coordinator-a
uv run experiments/dpc_lr6.py transition|pause|unfenced|restore
```
