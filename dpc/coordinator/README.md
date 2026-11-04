# Координатор (ЛР6)

Сервіси `coordinator-a` і `coordinator-b` збираються з цього каталогу. `main.py` – заглушка
(стартує, чекає на etcd і пише в журнал `todo`). `etcd.py` – тонкий клієнт etcd з операціями: оренда, продовження,
транзакція, читання, спостереження.
Захоплення ролі, покоління й перевірку покоління на ресурсі пише команда (О6.1–О6.3).

Команди виконуються з кореня проєкту.

```sh
python tools/etcd_watch.py /roles/assigner
docker compose pause coordinator-a && sleep 9 && docker compose unpause coordinator-a
```
