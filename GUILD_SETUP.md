# Схема гильдии TeamSpeak

Автоматическая разметка каналов и групп через ServerQuery-сидер [`scripts/seed_guild.py`](scripts/seed_guild.py).

## Роли (server groups)

| Группа | join power | Права |
|--------|------------|--------|
| **Гость** | 10 | Только канал `[Гостевая]`. Без move и без выдачи групп. Дефолт для новых клиентов. |
| **Рядовой** | 50 | Общие каналы (Лобби, Общий, Рейд, AFK). Без move и групп. |
| **Офицер** | 70 | Все каналы, включая Офицерскую. Выдача/снятие **Рядовой** и **Офицер**. Перетаскивание клиентов, kick из канала. |
| **Server Admin** | без изменений | Полный доступ. Офицер **не** может выдавать/снимать эту группу (`needed` add/remove = 100 > power офицера 75). |

## Дерево каналов

```text
[Гостевая]       needed_join=10   ← default channel
Лобби            needed_join=50
Общий            needed_join=50
Рейд / Ивенты    needed_join=50
AFK              needed_join=50
Офицерская       needed_join=70
```

## Подготовка

1. Скопируйте `.env.example` → `.env` (если ещё нет).
2. Запустите сервер:
   ```bash
   docker compose up -d
   ```
3. Возьмите пароль ServerQuery из логов первого старта:
   ```bash
   docker compose logs teamspeak
   ```
   Найдите строки вида `loginname=serveradmin` и `password=...`. Пропишите пароль в `.env`:
   ```env
   TS3_QUERY_PASSWORD=ваш_пароль
   ```
4. Privilege key (`token=...`) по-прежнему нужен, чтобы в клиенте стать Server Admin.

**Не публикуйте порт 10011 наружу.** В compose он слушает только `127.0.0.1`.

## Запуск сидера

```bash
docker compose up -d
docker compose --profile tools run --rm seeder
```

С хоста (если Python установлен и Query доступен на localhost):

```bash
# Windows PowerShell
$env:TS3_QUERY_PASSWORD="ваш_пароль"
python scripts/seed_guild.py
```

Повторный запуск безопасен: существующие группы/каналы не дублируются, пермы и needed join обновляются.

Просмотр команд без изменений:

```bash
python scripts/seed_guild.py --dry-run
```

Для «чистой» схемы с нуля удалите volume (`teamspeak-data`) и поднимите сервер заново — сидер не делает полный reset уже замусоренной БД.

## Если сидер пишет `timed out` на login

Query принял TCP и показал Welcome, но **не отвечает на команды** — почти всегда flood-limit (IP сидера нет в allowlist).

1. В `.env`: `TS3_QUERY_PASSWORD=` пароль из логов (`password= "..."`, не token).
2. На сервере пересоздайте TS (подтянется allowlist + сброс blacklist):
   ```bash
   docker compose up -d --force-recreate
   docker compose --profile tools run --rm seeder
   ```
3. В логе сидера должно быть `Using Query serveradmin@teamspeak:10011`. Если всё ещё `127.0.0.1` — на сервере старый compose без обновления.

## Чеклист проверки

Подключите три клиента (или смените группы одному):

1. **Гость** — заходит только в `[Гостевая]`; вход в Лобби/Общий отклоняется.
2. **Рядовой** — ходит по общим каналам; в **Офицерская** не пускает.
3. **Офицер** — заходит в офицерскую; может выдать/снять Рядовой и Офицер; может перетащить клиента между каналами; **не** может выдать/снять Server Admin.
