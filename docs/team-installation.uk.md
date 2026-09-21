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

## Встановлення з офлайн-архіву

Для машин, яким взагалі не можна ходити в PyPI, — або для людей без `uv` —
мейнтейнер збирає архів, що несе залежності в собі:

```bash
uv run python scripts/build_offline_archive.py
```

Скрипт вивантажує з `uv.lock` закріплений набір рантайм-залежностей у
`requirements-lock.txt`, качає їх як колеса в `wheels/` для кожної версії Python
і платформи зі свого списку і пакує це разом із відстежуваним деревом `HEAD`.
За замовчуванням — Python 3.12-3.14 для Apple Silicon та Intel macOS; для іншого
передайте `--python-version` / `--platform` (обидва можна повторювати), а шлях
архіву — через `--out`.

Для встановлення потрібен лише Python — ні `uv`, ні мережі:

```bash
unzip charles-mcp-<дата>.zip -d ~/Projects
cd ~/Projects/charles-mcp
python3 -m venv .venv
.venv/bin/pip install --no-index --find-links wheels -r requirements-lock.txt
```

Офлайн це робить саме `--no-index`: pip не звертається до жодного реєстру і
ставить рівно ті закріплені версії з хешами, на яких форк перевірявся. У
вітрині лежить кілька версій інтерпретатора одразу, pip сам бере сумісний файл;
якщо він каже, що відповідного дистрибутива немає, — цю версію Python у цей
архів не збирали.

MCP-клієнт тоді запускає інтерпретатор оточення і обгортку з кореня
репозиторію, а не `uv`:

```json
"command": "/Users/<ви>/Projects/charles-mcp/.venv/bin/python",
"args": ["/Users/<ви>/Projects/charles-mcp/charles-mcp-server.py"]
```

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

Деякі застосунки розбирають відповідь суворіше за будь-яку JSON-бібліотеку, тому фікстуру варто тримати близькою до знятої: будуйте її зі справжнього запису, а не з пам'яті, міняйте лише названі поля, зберігайте `Content-Type` і звіряйте mock-запис із вихідним, перш ніж вірити відповіді 200. Покрокова інструкція для агента, включно з тим, яке форматування інструменти зберігають, а яке переписують, — у [agent-guide.uk.md](agent-guide.uk.md), розділ 6.5.

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
4. Для команд, що працюють з архівом, зберіть із цього тега офлайн-архів (`uv run python scripts/build_offline_archive.py`) і передайте його; колеса беруться з того самого локу, тому обидва шляхи встановлення дають однакові версії.
5. Повідомте тег команді; кожен виконує `git fetch && git checkout <tag> && uv sync --locked --extra dev` і перезапускає MCP-клієнт.

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
