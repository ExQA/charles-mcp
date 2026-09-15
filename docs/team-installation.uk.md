# Встановлення та поширення в команді

Як передати цей форк `charles-mcp` команді та поставити його на інший комп'ютер.

> English version: [team-installation.md](team-installation.md)

## Архітектура та зони відповідальності

`charles-mcp` — локальний MCP-сервер, що працює через stdio. Кожен розробник запускає його на тому самому комп'ютері, де працює його Charles Proxy.

- Charles, його ліцензія, облікові дані Web Interface, порт проксі, захоплення, моки, бекапи і стан — свої на кожному комп'ютері.
- MCP-клієнт сам запускає `charles-mcp` по stdio, коли він потрібен.
- Диспетчер моків за тілом запиту (див. [ADR-0001](adr/0001-data-driven-body-aware-mocks.uk.md)) теж працює локально на `127.0.0.1`.
- Спільного Charles немає, облікові дані нікуди не передаються.

Діліться репозиторієм і зафіксованим тегом. **Ніколи не передавайте** конфіги Charles, захоплення (`*.chlsj`), cookie, токени, файли `.env` і каталог моків (`~/charles-mocks`): фікстури моків — це зняті справжні відповіді.

## Чому не PyPI

Пакет `charles-mcp` у PyPI належить автору upstream, це версія 3.0.3. У ньому немає інструментів моків, а оскільки він не обмежує `mcp<2`, під час встановлення підтягується `mcp` 2.x, і сервер падає на старті з `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. Цей форк ставиться з командного репозиторію. Не публікуйте його в публічний PyPI.

## Що потрібно

1. Charles Proxy з ліцензією за правилами команди.
2. [`uv`](https://docs.astral.sh/uv/getting-started/installation/); за потреби він сам поставить Python 3.10+.
3. git.
4. MCP-клієнт з підтримкою stdio: Claude Code, Claude Desktop, Cursor, Codex CLI, Kiro тощо.

## Встановлення з репозиторію

```bash
git clone <TEAM_REPOSITORY_URL> charles-mcp
cd charles-mcp
git checkout <TAG_OR_BRANCH>
uv sync --locked --extra dev
uv run python -m pytest -q
```

Якщо доступу до репозиторію немає, мейнтейнер може передати git bundle (`git bundle create charles-mcp.bundle --all`), тоді перша команда буде `git clone -b <branch> charles-mcp.bundle charles-mcp`.

На macOS не кладіть проєкт у теки із синхронізацією iCloud, наприклад `~/Documents`: синхронізація заважає віртуальному оточенню.

### Налаштування MCP-клієнта

Вкажіть клієнту абсолютний шлях до репозиторію:

```json
{
  "mcpServers": {
    "charles": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/to/charles-mcp", "charles-mcp"],
      "env": {
        "CHARLES_USER": "<local-charles-user>",
        "CHARLES_PASS": "<local-charles-password>",
        "CHARLES_MANAGE_LIFECYCLE": "false"
      }
    }
  }
}
```

У Claude Code те саме без правки JSON:

```bash
claude mcp add-json charles '{"type":"stdio","command":"uv","args":["run","--project","/absolute/path/to/charles-mcp","charles-mcp"],"env":{"CHARLES_USER":"<local-charles-user>","CHARLES_PASS":"<local-charles-password>","CHARLES_MANAGE_LIFECYCLE":"false"}}'
```

| Клієнт | Де зазвичай лежить конфіг |
| --- | --- |
| Claude Desktop | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`; Windows `%APPDATA%\Claude\claude_desktop_config.json`; Linux `~/.config/Claude/claude_desktop_config.json` |
| Cursor | проєкт `.cursor/mcp.json`, користувач `~/.cursor/mcp.json` |
| Kiro | проєкт `.kiro/settings/mcp.json`, користувач `~/.kiro/settings/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| Codex CLI | аналогічний запис у TOML |

Додавайте запис `charles` до наявного файлу, не затираючи інші сервери. Перезапустіть клієнт і попросіть його викликати `charles_status`.

Можна й через звичайний virtualenv: `python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"`, а командою в конфігу вказати `/absolute/path/to/charles-mcp/.venv/bin/charles-mcp` (на Windows `.venv\Scripts\charles-mcp.exe`).

## Налаштування Charles на кожному комп'ютері

1. Запустіть Charles і ввімкніть `Proxy -> Web Interface Settings` зі своїми логіном і паролем, не типовими; «Allow anonymous access» не вмикайте.
2. Перевірте порт проксі, зазвичай `8888`.
3. Увімкніть SSL Proxying для хостів API і поставте кореневий сертифікат Charles на тестовий пристрій.
4. Спрямуйте пристрій на LAN-адресу комп'ютера і порт проксі, дозвольте його в Access Control Charles.
5. Лишіть `CHARLES_MANAGE_LIFECYCLE=false`.
6. Якщо конфіг не знаходиться автоматично, задайте `CHARLES_CONFIG_PATH`. На macOS з Charles 5 це `~/Library/Preferences/com.xk72.charles.config`.

Інструменти, які пишуть конфіг Charles (`mock_setup_host`, `mock_route_setup` з `apply=true`), працюють лише при закритому Charles: під час виходу він перезаписує конфіг з пам'яті, і правки, зроблені на льоту, втрачаються. Самі інструменти Charles не закривають і не запускають; порядок перезапуску, бекапи, відкат і діагностика — у [charles-mapping.uk.md](charles-mapping.uk.md).

## Моки в команді

- Моки Map Local: файли `<CHARLES_MOCK_DIR>/<host>/<path>`, одне правило Charles на хост (`mock_setup_host`).
- Правила диспетчера: `<CHARLES_MOCK_DIR>/_rules/` (`routes.json`, `_any/` для правил на будь-який хост, `<host>/` для одного хоста), їх віддає диспетчер (`mock_route_setup` один раз на домен, `mock_discover_variants`, `mock_rule_create_from_entry`, `mock_dispatcher`).
- Доки в Charles увімкнене правило Map Remote на диспетчер, диспетчер має працювати. Запустіть його з агента через `mock_dispatcher(action="start")` (зупиниться разом з MCP-сервером) або окремо:

  ```bash
  uv run --project /absolute/path/to/charles-mcp charles-mcp-dispatcher --port 18080
  ```

- Щоб поділитися сценарієм, передавайте JSON правила із синтетичними значеннями у фікстурі, а не каталог моків.

### Зберігайте оригінальний контракт відповіді

Деякі мобільні клієнти або адаптери відповідей працюють крихкіше, ніж стандартний JSON-парсер. Хоча порядок ключів JSON семантично не має значення, фікстуру потрібно зберігати якомога ближче до захопленої відповіді:

1. Захопіть справжній запит і відповідь, потім розкрийте один підтверджений entry через `get_traffic_entry_detail` з повними заголовками і body запиту/відповіді.
2. Переважно використовуйте `mock_rule_create_from_entry` у режимі `mode="fixture"` або `mock_create_from_entry` і змінюйте лише потрібні поля через JSON Pointer. Не відновлюйте всю відповідь вручну з пам'яті.
3. Якщо використовуєте `mock_rule_write(fixture_json=...)`, зберігайте порядок ключів верхнього рівня і вкладених об'єктів, порядок масивів, назви полів, наявність `null` і скалярні типи. Для точного raw body записуйте сирий JSON через `fixture_text` або редагуйте створений файл `.body`; не перетворюйте відповідь у pretty-printed JSON, якщо вона має збігатися із захопленим контрактом byte-to-byte.
4. Зберігайте важливі для застосунку заголовки відповіді, особливо `Content-Type` разом із `charset=utf-8`. Диспетчер сам керує транспортними заголовками `Content-Length`, `Server` і `X-Charles-MCP-Rule`; вони не є частиною JSON-контракту застосунку.
5. Після повторення запиту застосунком запитайте новий live entry і порівняйте оригінальний та mock entry через `get_traffic_entry_detail`. Перевірте заголовок правила, HTTP status, content type, форму верхнього рівня відповіді, порядок масивів і скалярні типи (`0.0` проти `0`, рядок проти числа). Не покладайтеся на обрізане preview або лише на HTTP 200.

Якщо застосунок показує загальний екран «сервер недоступний», хоча mock повертає HTTP 200, спочатку порівняйте фактичні body і headers. Поширені причини: змінений порядок відповіді, відсутня обгортка на кшталт `data`, інший порядок масиву, перейменоване поле, змінений скалярний тип або відсутній UTF-8 content type — а не обов'язково помилка маршрутизації.

## Змінні оточення

| Змінна | Призначення |
| --- | --- |
| `CHARLES_USER` / `CHARLES_PASS` | Локальні облікові дані Charles Web Interface |
| `CHARLES_PROXY_HOST` / `CHARLES_PROXY_PORT` | Адреса проксі Charles, зазвичай `127.0.0.1` і `8888` |
| `CHARLES_CONFIG_PATH` | Явний шлях до конфіга Charles |
| `CHARLES_MANAGE_LIFECYCLE` | Лишіть `false`, щоб сервер ніколи не закривав Charles користувача |
| `CHARLES_STATE_DIR` / `CHARLES_REVERSE_STATE_DIR` | Стан користувача для захоплень і reverse-аналізу |
| `CHARLES_MOCK_DIR` | Каталог моків користувача, за замовчуванням `~/charles-mocks` |
| `CHARLES_DISPATCHER_PORT` / `CHARLES_DISPATCHER_TIMEOUT` | Порт диспетчера (за замовчуванням `18080`) і тайм-аут запитів до сервера в секундах (за замовчуванням `20`) |
| `CHARLES_LOG_DIR` | Каталог логу MCP-сервера |

Для локальних значень скопіюйте `.env.example` у `.env`; `.env` виключений з git.

## Випуск версії для команди

1. Підніміть `[project].version` у `pyproject.toml`.
2. Запустіть `uv run ruff check charles_mcp tests`, `uv run mypy charles_mcp` і `uv run pytest -q`.
3. Поставте тег на коміт і відправте його в командний репозиторій.
4. Повідомте тег команді; кожен виконує `git fetch && git checkout <tag> && uv sync --locked --extra dev` і перезапускає MCP-клієнт.

## Правила безпеки

- Ніколи не комітьте облікові дані, cookie, заголовки авторизації, токени, `.env`, конфіги Charles, захоплення `*.chlsj` і фікстури моків.
- Бекапи конфіга Charles перед `apply=true` зберігаються в `<CHARLES_STATE_DIR>/charles-config-backups/` (поза репозиторієм): у них ліцензійний ключ Charles і облікові дані Web Interface.
- Ніколи не кладіть зняті дані у вихідний код і тести. Тести використовують синтетичні дані на `example.com` або `localhost`.
- Дані трафіку чутливі: MCP-сервер передає сирі значення запитів і відповідей MCP-клієнту та його моделі.
- У кожного розробника свій пароль Web Interface.
- До баг-репорту додавайте версію, ОС, MCP-клієнт, версію Charles, очищений лог і вивід `charles_status`, а не сирі захоплення.

## Діагностика

**`ModuleNotFoundError: mcp.server.fastmcp`.** Пакет поставлено з PyPI або без lock-файлу. Ставте з репозиторію через `uv sync --locked`.

**Клієнт не може запустити сервер.** Використовуйте абсолютний шлях у `--project` і перезапустіть клієнт після правки конфіга.

**`charles_status` пише, що Charles недоступний.** Перевірте, що Charles запущений, Web Interface увімкнений, облікові дані збігаються, адреса і порт проксі правильні.

**Запити на замаплений URL падають.** Диспетчер не запущений: викличте `mock_dispatcher(action="status")`, запустіть його або вимкніть правило Map Remote.

**Диспетчер відповідає 421.** Для хоста не заведений маршрут; виконайте для нього `mock_route_setup`.

**Live-інструменти гальмують або закінчується місце на диску.** Кожен live-запит вивантажує всю сесію Charles через Web Interface; довга сесія легко виростає до гігабайтів (спостерігали 8 ГБ). Очищайте сесію перед сценарієм (у Charles: Proxy → Clear Session або `start_live_capture(reset_session=true)` за згодою користувача) і перезапускайте Charles між довгими прогонами.

**На іншому комп'ютері немає моків.** Так і має бути: моки і стан у кожного комп'ютера свої. Створіть їх там заново.
