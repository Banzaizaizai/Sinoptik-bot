# Weather bot — Дрогобич

Бот дважды в сутки берёт прогноз погоды на завтра с [sinoptik.ua/pohoda/drohobych](https://sinoptik.ua/pohoda/drohobych)
и публикует его в Telegram-канал. Полная спецификация — в [SPEC.md](SPEC.md).

> ⚠️ Данные на sinoptik.ua даются с шагом **3 часа** (00:00, 03:00, ... 21:00), не почасово.
> Это ограничение источника, не бота — см. SPEC.md, раздел 2.

## Структура проекта

```
src/weather_bot/
  config.py            # чтение .env
  models.py             # ForecastDay, HourSlot
  fetcher.py             # HTTP-запрос + определение URL "на завтра"
  parser.py               # HTML -> ForecastDay
  formatter.py             # ForecastDay -> текст сообщения
  telegram_sender.py        # отправка через Telegram Bot API
  main.py                     # оркестрация, логирование, обработка ошибок
run_bot.py                     # точка входа (используется Task Scheduler'ом)
tests/                          # юнит-тесты на реальных HTML-фикстурах
scripts/register_task_scheduler.ps1   # регистрация запуска 2 раза в сутки
```

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

(`requirements-dev.txt` включает `requirements.txt` + `pytest`; для прод-окружения
без тестов достаточно `pip install -r requirements.txt`.)

## Настройка

1. Создайте Telegram-бота через [@BotFather](https://t.me/BotFather) → команда `/newbot` → сохраните токен.
2. Добавьте бота в ваш канал **администратором** с правом публикации сообщений.
3. Узнайте `chat_id` канала: перешлите любое сообщение из канала боту [@userinfobot](https://t.me/userinfobot),
   либо (для публичных каналов) используйте `@username` канала напрямую.
4. Скопируйте `.env.example` в `.env` и заполните:

```powershell
Copy-Item .env.example .env
notepad .env
```

```
TELEGRAM_BOT_TOKEN=123456:AA...
TELEGRAM_CHANNEL_ID=@my_weather_channel   # или -100xxxxxxxxxx
ADMIN_CHAT_ID=                            # опционально — личный chat_id для алертов об ошибках
CITY_SLUG=drohobych
CITY_NAME=Дрогобич
TIMEZONE=Europe/Kyiv
```

## Запуск вручную

```powershell
.\.venv\Scripts\python.exe run_bot.py
```

Логи пишутся в `logs/weather_bot.log` и в stdout.

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Тесты парсера используют реальные сохранённые HTML-страницы (`tests/fixtures/`) —
сеть не требуется. Если сайт поменяет вёрстку, `parser.py` начнёт кидать `ParseError`
с понятным сообщением — тогда фикстуры нужно обновить и подправить парсер.

## Запуск по расписанию (2 раза в сутки, 08:00 и 20:00 Europe/Kyiv)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_task_scheduler.ps1
```

Создаёт два задания в Windows Task Scheduler: `WeatherBotDrohobych-Morning` и
`WeatherBotDrohobych-Evening`. Проверить/запустить вручную/удалить — см. вывод скрипта
или `taskschd.msc`.

## Если сломался парсинг

`main.py` не публикует в канал, если `parser.py` не смог разобрать страницу — вместо
этого пишет в лог и (если задан `ADMIN_CHAT_ID`) шлёт алерт в личку. Типичная причина —
sinoptik.ua изменил вёрстку; чинить нужно `src/weather_bot/parser.py` (и обновить
фикстуры в `tests/fixtures/`, скачав свежий HTML).
