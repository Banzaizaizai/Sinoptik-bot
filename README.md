# Weather bot — Дрогобич

Бот трижды в сутки берёт прогноз погоды с [sinoptik.ua/pohoda/drohobych](https://sinoptik.ua/pohoda/drohobych)
и публикует его в Telegram-канал: **08:00 и 13:00 — прогноз на сегодня**, **20:00 — на завтра**.
Полная спецификация — в [SPEC.md](SPEC.md).

Боевой хостинг — собственная Ubuntu-ВМ (`cron`), не GitHub Actions: sinoptik.ua блокирует
IP-диапазоны GitHub-раннеров (см. раздел «Хостинг» ниже).

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
scripts/register_task_scheduler.ps1   # регистрация запуска 3 раза в сутки
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
.\.venv\Scripts\python.exe run_bot.py --mode today      # прогноз на сегодня
.\.venv\Scripts\python.exe run_bot.py --mode tomorrow    # прогноз на завтра (по умолчанию)
```

Логи пишутся в `logs/weather_bot.log` и в stdout.

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Тесты парсера используют реальные сохранённые HTML-страницы (`tests/fixtures/`) —
сеть не требуется. Если сайт поменяет вёрстку, `parser.py` начнёт кидать `ParseError`
с понятным сообщением — тогда фикстуры нужно обновить и подправить парсер.

## Хостинг: боевой запуск на Ubuntu-ВМ (cron)

GitHub Actions для этого не подходит: sinoptik.ua блокирует IP-диапазоны GitHub-раннеров
(запросы падают по `ConnectTimeout`, проверено). Поэтому бот крутится на отдельной ВМ.

Настройка на ВМ (Ubuntu):

```bash
sudo apt-get install -y python3-venv python3-pip git
git clone https://github.com/Banzaizaizai/Sinoptik-bot.git ~/weather-bot
cd ~/weather-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env   # заполнить токен и chat_id
```

Крон (`crontab -e`), 08:00 и 13:00 -> сегодня, 20:00 -> завтра, время — Europe/Kyiv:

```
CRON_TZ=Europe/Kyiv
0 8 * * * cd /home/joshua/weather-bot && /home/joshua/weather-bot/.venv/bin/python run_bot.py --mode today >> /home/joshua/weather-bot/logs/cron.log 2>&1
0 13 * * * cd /home/joshua/weather-bot && /home/joshua/weather-bot/.venv/bin/python run_bot.py --mode today >> /home/joshua/weather-bot/logs/cron.log 2>&1
0 20 * * * cd /home/joshua/weather-bot && /home/joshua/weather-bot/.venv/bin/python run_bot.py --mode tomorrow >> /home/joshua/weather-bot/logs/cron.log 2>&1
```

После обновления кода на ВМ: `git pull && .venv/bin/pip install -r requirements.txt`.

### Альтернатива: Windows Task Scheduler

Если бот когда-нибудь снова понадобится на локальной Windows-машине:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_task_scheduler.ps1
```

Создаёт три задания: `WeatherBotDrohobych-Morning` (`--mode today`, 08:00),
`WeatherBotDrohobych-Midday` (`--mode today`, 13:00) и `WeatherBotDrohobych-Evening`
(`--mode tomorrow`, 20:00). Проверить/запустить вручную/удалить — см. вывод скрипта
или `taskschd.msc`.

### GitHub Actions (сейчас не используется как планировщик)

`.github/workflows/weather-bot.yml` рабочий, но `schedule`-триггеры фактически не
срабатывают из-за блокировки IP — оставлен на случай самостоятельного runner'а или
смены источника данных. Ручной запуск (`workflow_dispatch`) с выбором режима работает,
но так же упрётся в блокировку сети при обращении к sinoptik.ua.

## Если сломался парсинг

`main.py` не публикует в канал, если `parser.py` не смог разобрать страницу — вместо
этого пишет в лог и (если задан `ADMIN_CHAT_ID`) шлёт алерт в личку. Типичная причина —
sinoptik.ua изменил вёрстку; чинить нужно `src/weather_bot/parser.py` (и обновить
фикстуры в `tests/fixtures/`, скачав свежий HTML).
