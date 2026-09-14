# Charles MCP Server

[![PyPI version](https://img.shields.io/pypi/v/charles-mcp.svg)](https://pypi.org/project/charles-mcp/)
[![License](https://img.shields.io/pypi/l/charles-mcp.svg)](LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/charles-mcp.svg)](https://pypi.org/project/charles-mcp/)

[Документація](docs/README.uk.md) | [Встановлення для команди](docs/team-installation.uk.md) | [Посібник для агентів](docs/agent-guide.uk.md) | [Мапінг і перезапуск Charles](docs/charles-mapping.uk.md) | [ADR-0001](docs/adr/0001-data-driven-body-aware-mocks.uk.md) | [Контракт інструментів](docs/contracts/tools.uk.md) | [AGENTS](AGENTS.uk.md) | [Сценарії для агента](docs/agent-workflows.uk.md) | [English README](README.en.md)

Charles MCP Server підключає Charles Proxy до MCP-клієнтів. Агент може дивитися живий трафік, аналізувати збережені записи і розкривати окремі запити лише тоді, коли це справді потрібно.

Сервер розв'язує три задачі:

- читати новий трафік поточної сесії Charles, доки запис ще триває;
- вести live- і history-аналіз через структуровані дані, а не віддавати агенту сирі дампи;
- спершу видавати зведення (summary-first), щоб агент знайшов гарячі точки до того, як запитувати деталі.

## Напрям релізу (v3.0)

З `v3.0` `charles-mcp` розвивається від простого перегляду трафіку до сценаріїв реверс-інжинірингу.

- Поверх наявного live/history-аналізу додано набір reverse-інструментів: імпорт, запити, декодування, повтор (replay), пошук кандидатів у підписи і live-сесії реверс-аналізу.
- Мета — дати агенту не лише переглядати трафік, а й вибудовувати повний цикл реверс-аналізу навколо авторизації, підписів, мутації параметрів і відтворюваності запитів.

## Швидкий старт

### 1. Увімкніть Charles Web Interface

У Charles відкрийте: `Proxy -> Web Interface Settings`

Перевірте, що:

- позначено `Enable web interface`;
- ім'я користувача `admin`;
- пароль `123456`.

> Значення `admin` / `123456` використовуються за замовчуванням в усіх прикладах. Для справжньої роботи задайте свій пароль і передайте його через `CHARLES_USER` / `CHARLES_PASS`.

Де розташований пункт меню:

![Charles Web Interface Menu](docs/images/charles-web-interface-menu.png)

Вікно налаштувань:

![Charles Web Interface Settings](docs/images/charles-web-interface-settings.png)

### 2. Встановіть і налаштуйте MCP-клієнт

> **Цього форку немає в PyPI.** Пакет `charles-mcp` у PyPI — це upstream 3.0.3: у ньому немає інструментів моків, а без обмеження `mcp<2` він падає на старті з `ModuleNotFoundError: mcp.server.fastmcp`. Ставте форк із репозиторію за [docs/team-installation.uk.md](docs/team-installation.uk.md); приклади з `uvx` нижче стосуються релізів upstream.

Клонувати репозиторій і створювати virtualenv вручну не потрібно. Потрібен [uv](https://docs.astral.sh/uv/getting-started/installation/).

#### Claude Code CLI

```bash
claude mcp add-json charles '{
  "type": "stdio",
  "command": "uvx",
  "args": ["charles-mcp"],
  "env": {
    "CHARLES_USER": "admin",
    "CHARLES_PASS": "123456",
    "CHARLES_MANAGE_LIFECYCLE": "false"
  }
}'
```

#### Claude Desktop / Cursor / загальний JSON-конфіг

```json
{
  "mcpServers": {
    "charles": {
      "command": "uvx",
      "args": ["charles-mcp"],
      "env": {
        "CHARLES_USER": "admin",
        "CHARLES_PASS": "123456",
        "CHARLES_MANAGE_LIFECYCLE": "false"
      }
    }
  }
}
```

#### Codex CLI

```toml
[mcp_servers.charles]
command = "uvx"
args = ["charles-mcp"]

[mcp_servers.charles.env]
CHARLES_USER = "admin"
CHARLES_PASS = "123456"
CHARLES_MANAGE_LIFECYCLE = "false"
```

### Автовстановлення через AI-агента

Скопіюйте промпт нижче в будь-якого AI-агента (Claude Code, ChatGPT, Gemini CLI, Cursor Agent тощо), і він сам встановить і налаштує charles-mcp. Промпт лишено англійською: агенти виконують його однаково будь-якою мовою спілкування.

[![Автовстановлення](https://img.shields.io/badge/%D0%90%D0%B2%D1%82%D0%BE%D0%B2%D1%81%D1%82%D0%B0%D0%BD%D0%BE%D0%B2%D0%BB%D0%B5%D0%BD%D0%BD%D1%8F-%D1%80%D0%B5%D0%BA%D0%BE%D0%BC%D0%B5%D0%BD%D0%B4%D0%BE%D0%B2%D0%B0%D0%BD%D0%BE-e53935?style=for-the-badge)](#автовстановлення-через-ai-агента)

<details>
<summary><strong>🔴 Натисніть, щоб розкрити промпт автовстановлення (рекомендовано)</strong></summary>

```text
Install the "charles-mcp" MCP server and configure it for my MCP client. Follow these steps exactly:

Step 1 — Detect OS:
  Determine if this machine runs Windows, macOS, or Linux.

Step 2 — Ensure uv is installed:
  Run: uv --version
  If the command fails (uv not found):
    - macOS/Linux: run: curl -LsSf https://astral.sh/uv/install.sh | sh
    - Windows: run: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  After installing, verify uv works: uv --version

Step 3 — Detect which MCP client is installed:
  Check in this order and use the FIRST match:

  a) Claude Code — run: claude --version
     If it succeeds, run this command and skip to Step 5:
       claude mcp add-json charles '{"type":"stdio","command":"uvx","args":["charles-mcp"],"env":{"CHARLES_USER":"admin","CHARLES_PASS":"123456","CHARLES_MANAGE_LIFECYCLE":"false"}}'

  b) Claude Desktop — check if config file exists:
     - macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json
     - Windows: %APPDATA%\Claude\claude_desktop_config.json
     - Linux:   ~/.config/Claude/claude_desktop_config.json

  c) Cursor — check if any of these exist:
     - ~/.cursor/mcp.json
     - .cursor/mcp.json (in current project)

  d) Windsurf — check if exists:
     - ~/.codeium/windsurf/mcp_config.json

  e) Kiro — check if either of these exists:
     - .kiro/settings/mcp.json (in current project, wins over the user one)
     - ~/.kiro/settings/mcp.json

  If none detected, ask the user which client they use.

Step 4 — Write config (for clients b/c/d/e):
  The config entry to add is:
    "charles": {
      "command": "uvx",
      "args": ["charles-mcp"],
      "env": {
        "CHARLES_USER": "admin",
        "CHARLES_PASS": "123456",
        "CHARLES_MANAGE_LIFECYCLE": "false"
      }
    }

  IMPORTANT: If the config file already exists, read it first, parse the JSON,
  add the "charles" key inside "mcpServers" (create "mcpServers" if absent),
  and write back. Do NOT overwrite other existing MCP server entries.
  If the file does not exist, create it with:
    { "mcpServers": { "charles": { ... } } }

  Kiro takes the same entry and additionally accepts "disabled": false and
  "autoApprove": [...]. If you set autoApprove, list only read-only tools such as
  charles_status, start_live_capture, query_live_capture_entries and
  group_capture_analysis. Never auto-approve reset_environment, mock_setup_host or
  mock_route_setup: they close Charles or write its config.

Step 5 — Verify:
  Run: uvx charles-mcp
  Wait 3 seconds then terminate the process.
  If it starts without import errors, the installation is successful.

Step 6 — Report:
  Print: "charles-mcp installed successfully. Please restart your MCP client to load the new server."
  For Kiro, saving mcp.json reconnects the server; check it in the MCP panel.
  Also mention that Charles Proxy must be running with Web Interface enabled (Proxy → Web Interface Settings, username: admin, password: 123456).
```

</details>

## Вимоги

- Python 3.10+
- запущений локально Charles Proxy
- увімкнений Charles Web Interface
- проксі Charles слухає `127.0.0.1:8888`

Рекомендоване значення за замовчуванням — `CHARLES_MANAGE_LIFECYCLE=false`. Не дозволяйте MCP-серверу закривати ваш Charles, якщо ви явно не хочете, щоб він керував запуском і зупинкою Charles.

## Змінні оточення

| Змінна | За замовчуванням | Призначення |
| --- | --- | --- |
| `CHARLES_USER` | `admin` | Ім'я користувача Charles Web Interface |
| `CHARLES_PASS` | `123456` | Пароль Charles Web Interface |
| `CHARLES_PROXY_HOST` | `127.0.0.1` | Хост проксі Charles |
| `CHARLES_PROXY_PORT` | `8888` | Порт проксі Charles |
| `CHARLES_CONFIG_PATH` | автовизначення | Шлях до файлу конфігурації Charles |
| `CHARLES_REQUEST_TIMEOUT` | `10` | Тайм-аут HTTP-запитів до Charles у секундах |
| `CHARLES_MANAGE_LIFECYCLE` | `false` | Чи має MCP-сервер запускати і зупиняти Charles |
| `CHARLES_REVERSE_STATE_DIR` | `${CHARLES_STATE_DIR}/reverse` | Каталог стану reverse-аналізу: артефакти і база SQLite |
| `CHARLES_MOCK_DIR` | `~/charles-mocks` | Корінь сховища моків Map Local; Charles мапить `https://<host>/*` у `<CHARLES_MOCK_DIR>/<host>/`. Правила диспетчера лежать у `<CHARLES_MOCK_DIR>/_rules/` |
| `CHARLES_DISPATCHER_PORT` | `18080` | Порт локального диспетчера, на який указують правила Map Remote |
| `CHARLES_DISPATCHER_TIMEOUT` | `20` | Тайм-аут у секундах для запитів, які диспетчер пересилає на сервер |

## Рекомендовані сценарії

### Live-аналіз

1. `start_live_capture`
2. `group_capture_analysis`
3. `query_live_capture_entries`
4. `get_traffic_entry_detail`
5. `stop_live_capture`

Цей шлях дозволяє спершу знайти гарячі точки з мінімальною витратою токенів, а потім розкрити один підтверджений запит.

### Аналіз історії

1. `list_recordings`
2. `analyze_recorded_traffic`
3. `group_capture_analysis(source="history")`
4. `get_traffic_entry_detail`

Цей шлях підходить, щоб переглянути збережені записи і потім заглибитися в обрані запити.

### Підміна відповідей через Map Local

> Як влаштовані правила в Charles, коли і як Charles перезапускається, відкат і діагностика — у [docs/charles-mapping.uk.md](docs/charles-mapping.uk.md).

1. `mock_setup_host` — один раз на хост: створює `<CHARLES_MOCK_DIR>/<host>/` і правила Charles. З `apply=true` (Charles має бути закритий) записує їх у конфіг Charles, інакше повертає інструкцію для ручного налаштування.
2. `start_live_capture` + `query_live_capture_entries` — знайти справжню відповідь.
3. `mock_create_from_entry` з правками `patches` у форматі JSON Pointer (або `mock_write`, щоб написати відповідь з нуля).
4. Застосунок повторює запит; перевірте результат через `query_live_capture_entries(response_header_name="X-Charles-Map-Local")`.
5. `mock_remove` — повернутися до справжнього сервера (файл іде в архів, а не видаляється); `mock_set_enabled(false)` — призупинити всі моки.

Мок — це просто файл за шляхом `<CHARLES_MOCK_DIR>/<host>/<шлях запиту>`. Доки файл існує, Charles віддає його і перечитує на кожен запит; якщо файлу немає, запит іде на справжній сервер.

Обмеження Map Local:

- статус відповіді завжди 200;
- query string і HTTP-метод ігноруються, тому всі варіанти одного шляху отримують той самий файл;
- `/users` і `/users/42` не можна замокати одночасно: `users` не може бути водночас файлом і каталогом.

Налаштування також додає правило Rewrite, яке віддає моки як `application/json`: Charles позначає файли без розширення як `text/plain`.

### Багато шляхів і action: правила диспетчера

Використовуйте для справжнього API: багато шляхів, POST-запити, де операцію обирає поле `action`, і те саме API на кількох хостах (dev, stage тощо). Map Local такі запити не розрізняє, диспетчер розрізняє. Обґрунтування — в [ADR-0001](docs/adr/0001-data-driven-body-aware-mocks.uk.md).

> Правило в Charles ставиться один раз на домен і потребує перезапуску Charles (або ручного введення в UI без перезапуску). Інструменти самі Charles не закривають і не запускають: порядок дій, що втрачається і як відкотити — у [docs/charles-mapping.uk.md](docs/charles-mapping.uk.md).

**Один раз на домен.** `mock_route_setup(host="*.example.com", path="/api/*")` заводить маршрут і повертає одне правило Map Remote для Charles: `https://*.example.com/api/*` → локальний диспетчер із порожнім шляхом призначення (Charles зберігає вихідний шлях) і ввімкненим «Preserve host header». `apply=true` записує його, доки Charles закритий. Після цього всі шляхи, action і хости під маршрутом — лише дані, а запити без правила йдуть на справжній сервер без змін.

**На кожну сесію.**

1. `start_live_capture`, потім пройдіть сценарій у застосунку.
2. `mock_discover_variants(source="live", capture_id)` показує всю сесію: метод × хост × шлях × значення `action` (`null` для GET) з кількістю, прикладами `entry_id` і статусами. Фільтри — `host_contains` / `path_contains`.
3. На кожну правку, яку просить користувач: `mock_rule_create_from_entry(entry_id, match_body_fields=["/action"], response_patches=[...], request_patches=[...])`.
   - `mode="patch"` (за замовчуванням): відповідає справжній сервер, змінюються лише вказані поля запиту й відповіді.
   - `mode="fixture"`: знята відповідь із правками і будь-яким `status`, наприклад 500, без звернення до сервера.
   - `status` підміняє код відповіді, `delay_ms` затримує її (0–60000 мс) — для перевірки лоадерів і тайм-аутів, `request_headers` задає або видаляє заголовки запиту перед відправкою.
   - Сегменти шляху, що залежать від платформи чи версії застосунку, замінюйте на `*` через `match_path`, наприклад `/api/*/payoneer`.
   - За замовчуванням правило діє на всіх хостах маршруту (`host_scope="any"`); `host_scope="exact"` або `host="stage.example.com"` звужують його, і правило для конкретного хоста перемагає загальне.
   - Повторне прохання для того самого варіанта доповнює його правило (`merge=true`); правка того самого поля замінює попередню.
4. `mock_dispatcher(action="start")`, потім повторіть дії в застосунку.
5. Перевірте через `query_live_capture_entries(response_header_name="X-Charles-MCP-Rule")`: у заголовку id правила або `passthrough`.

Зберігання в `<CHARLES_MOCK_DIR>/_rules/`: `routes.json` (маршрути), `_any/` (правила для будь-якого хоста або шаблону хоста), `<host>/` (правила для одного хоста), фікстури поруч із правилами як `<rule_id>.body`. Диспетчер перечитує їх на кожен запит, тому правки діють одразу. Він пересилає лише запити, покриті маршрутом, слухає `127.0.0.1` і має працювати, доки в Charles увімкнене правило Map Remote; для постійної роботи є команда `charles-mcp-dispatcher`. Маршрут на весь хост (`/*`) пропускає через диспетчер і статику, і WebSocket: завантаження буферизуються, а WebSocket не підтримується, тому краще вказувати префікс API.

> ⚠️ Фікстури — це зняті справжні відповіді, у них можуть бути персональні та платіжні дані. Тримайте `CHARLES_MOCK_DIR` поза репозиторіями і нікому не пересилайте; діліться описами правил із синтетичними даними.

## Основні зміни версії (v3.0.3)

- Точки входу в документацію зведені до `docs/README.md`.
- Додані документи для агентів: `AGENTS.md` у корені та `docs/agent-workflows.md` зі сценаріями за задачами.
- У README і `docs/contracts/tools.md` додані посилання на документи для агентів зі шляхами відносно репозиторію.
- В описи часто вживаних інструментів додані мінімально потрібні підказки (збереження ідентичності джерела, summary-first, різниця між peek і read), а контрактні тести не дають їм розходитися з документацією.
- Напрям продукту явно включає реверс-інжиніринг: доступні reverse-інструменти для імпорту, декодування, повтору запитів, пошуку кандидатів у підписи і live-сценаріїв реверс-аналізу.
- `read_live_capture` і `peek_live_capture` тепер повертають лише короткі поля маршруту (`host`, `method`, `path`, `status`) замість сирих записів Charles. Так частий опит не переповнює контекстне вікно.
- `query_live_capture_entries` став інструментом лише для читання і не зсуває live-курсор. Той самий `capture_id` можна перевикористовувати з різними фільтрами, не «з'їдаючи» накопичений приріст.
- Зведення `analyze_recorded_traffic` і `query_live_capture_entries` повертають `matched_fields` і `match_reasons`, щоб агент міг пояснити, чому обрано запит.
- У `get_traffic_entry_detail` за замовчуванням `include_full_body=false` і `max_body_chars=2048`. Якщо оцінний розмір відповіді перевищує приблизно 12 000 символів, інструмент додає попередження з порадою звузити запит.
- Зведення і деталі автоматично прибирають значення `null` і приховують внутрішні поля `header_map`, `parsed_json`, `parsed_form`, `lower_name`. Заголовки беріть зі списку `headers`.

## Каталог інструментів

Цей README описує весь публічний набір інструментів.

### Інструменти live-захоплення

| Інструмент | Що робить | Коли використовувати |
| --- | --- | --- |
| `start_live_capture` | Запускає або підхоплює поточне live-захоплення і повертає `capture_id`; за замовчуванням `adopt_existing=true`, `reset_session=false`, **не очищає** вже записаний трафік Charles і **завжди включає його** в захоплення | Перед початком спостереження в реальному часі |
| `read_live_capture` | Читає нові записи за курсором і повертає лише короткі зведення маршрутів | Для безперервного читання нового трафіку, коли спершу потрібні лише host/path/status |
| `peek_live_capture` | Показує нові записи без зсуву курсора, лише короткі зведення маршрутів | Щоб зазирнути в новий трафік, не змінюючи позицію читання |
| `stop_live_capture` | Зупиняє захоплення і за потреби зберігає знімок | Під час завершення або експорту live-сесії |
| `query_live_capture_entries` | Видає структуроване зведення по live-захопленню без зсуву курсора; `since_seconds=N` обмежує трафік останніми N секундами | Для багаторазової фільтрації важливих запитів із поточного трафіку |

### Інструменти аналізу

| Інструмент | Що робить | Коли використовувати |
| --- | --- | --- |
| `group_capture_analysis` | Групує live- або history-трафік за ключем | Коли потрібен найощадливіший за токенами огляд гарячих точок |
| `get_capture_analysis_stats` | Повертає грубу статистику за класами трафіку | Щоб швидко побачити розподіл: API, статика, помилки |
| `get_traffic_entry_detail` | Завантажує деталі одного запису і попереджає про завелику відповідь | Коли `entry_id` цілі вже відомий |
| `analyze_recorded_traffic` | Видає структуроване зведення за збереженим записом із причинами збігу | Для аналізу знімка `.chlsj` |

### Інструменти історії

| Інструмент | Що робить | Коли використовувати |
| --- | --- | --- |
| `list_recordings` | Показує збережені файли записів | Перед вибором історичного знімка |
| `get_recording_snapshot` | Завантажує сирий вміст одного збереженого запису | Коли потрібен сам збережений знімок |
| `query_recorded_traffic` | Легка фільтрація останнього збереженого запису | Для швидкого пошуку за host, методом або регулярним виразом |

### Інструменти стану і керування

| Інструмент | Що робить | Коли використовувати |
| --- | --- | --- |
| `charles_status` | Показує зв'язок з Charles і стан активного захоплення; поле `recommended_next_action` підказує наступний крок | Щоб перевірити, чи доступний Charles і чи активне захоплення |
| `throttling` | Вмикає пресет сповільнення мережі в Charles | Для емуляції 3G, 4G, 5G або вимкнення сповільнення |
| `reset_environment` | Відновлює конфігурацію Charles і очищає поточне оточення | Коли треба повернутися до чистого стану |

> ⚠️ `reset_environment` закриває Charles, перезаписує його конфіг із бекапа і видаляє каталог збережених записів. Викликайте його лише свідомо.

### Інструменти реверс-аналізу

| Інструмент | Що робить | Коли використовувати |
| --- | --- | --- |
| `reverse_import_session` | Імпортує офіційну XML- або нативну сесію Charles у канонічне reverse-сховище | Щоб почати replay, декодування або аналіз підписів зі збережених експортів |
| `reverse_list_captures` | Показує імпортовані reverse-захоплення | Щоб обрати захоплення, яке вже лежить у reverse-SQLite |
| `reverse_query_entries` | Фільтрує імпортовані reverse-записи за полями маршруту | Щоб звузити набір кандидатів перед детальним переглядом або replay |
| `reverse_get_entry_detail` | Повертає канонічну деталізацію одного імпортованого запису | Для глибокого розбору одного базового запиту |
| `reverse_decode_entry_body` | Декодує збережене тіло запиту або відповіді, включно з protobuf за дескриптором | Коли треба зрозуміти структуру payload |
| `reverse_replay_entry` | Повторює один імпортований запит з необов'язковими мутаціями | Щоб перевірити, чи відтворюється запит і як він реагує на зміни |
| `reverse_discover_signature_candidates` | Порівнює кілька імпортованих записів і ранжує поля, схожі на підпис | Для пошуку динамічних параметрів авторизації або підпису |
| `reverse_list_findings` | Показує збережені результати replay і аналізу підписів | Щоб переглянути вже зібрані докази |
| `reverse_charles_recording_status` | Показує стан запису Charles і reverse live-сесії | Щоб перевірити готовність до live-реверс-аналізу |
| `reverse_start_live_analysis` | Запускає reverse live-сесію і знімає сесію Charles через офіційні сторінки експорту | Коли реверс-аналіз має стежити за свіжим трафіком |
| `reverse_peek_live_entries` | Читає нові reverse live-записи без зсуву курсора | Щоб подивитися новий трафік до його обробки |
| `reverse_read_live_entries` | Читає нові reverse live-записи і зсуває курсор | Щоб просунути reverse live-аналіз |
| `reverse_stop_live_analysis` | Зупиняє reverse live-сесію і за потреби відновлює запис | Для акуратного завершення reverse live-сесії |
| `reverse_analyze_live_login_flow` | Оцінює новий трафік на зв'язок із логіном і авторизацією та пропонує наступні кроки | Для розбору логіна, отримання токена, встановлення сесії |
| `reverse_analyze_live_api_flow` | Оцінює новий трафік як API-ланцюжок і пропонує наступні кроки | Для розбору ланцюжків бізнес-API |
| `reverse_analyze_live_signature_flow` | Фокусується на запитах, чутливих до підпису, і планує експерименти з мутаціями | Для розбору захистів на sign, nonce, timestamp |

### Інструменти моків Map Local

| Інструмент | Що робить | Коли використовувати |
| --- | --- | --- |
| `mock_setup_host` | Створює каталог моків для хоста і правила Map Local + Rewrite (інструкція для ручного налаштування або запис у конфіг з `apply=true`, доки Charles закритий) | Один раз на кожен API-хост |
| `mock_create_from_entry` | Перетворює захоплену відповідь на мок, застосовуючи правки JSON Pointer | Коли користувач хоче змінити дані, які отримав застосунок |
| `mock_write` | Пише мок із JSON-значення або сирого тексту | Коли справжньої відповіді ще немає |
| `mock_list` / `mock_get` | Список активних моків / вміст одного | Щоб подивитися, що зараз підміняється |
| `mock_remove` | Відправляє мок в архів, шлях знову йде на справжній сервер | Коли сценарій завершено |
| `mock_set_enabled` | Вмикає або вимикає весь Map Local через Web Interface | Щоб призупинити або відновити всі моки |

### Інструменти правил диспетчера

| Інструмент | Що робить | Коли використовувати |
| --- | --- | --- |
| `mock_route_setup` | Спрямовує хост або шаблон домену (за замовчуванням усі шляхи) через диспетчер і повертає одне правило Map Remote (з `apply=true` при закритому Charles записує його в конфіг) | Один раз на домен |
| `mock_discover_variants` | Групує всю сесію за методом, хостом, шляхом і полем тіла, наприклад `/action` | Одразу після читання сесії |
| `mock_rule_create_from_entry` | Створює або доповнює правило одного варіанта (метод + шлях + значення тіла/query): режим patch або fixture, будь-який статус, затримка, правка заголовків запиту, за замовчуванням на будь-якому хості | На кожну правку, яку назвав користувач |
| `mock_rule_write` | Пише документ правила напряму, за бажанням із фікстурою | Коли знятого запиту немає |
| `mock_rule_list` / `mock_rule_get` | Список маршрутів і правил / одне правило з фікстурою | Щоб подивитися, що замокано |
| `mock_rule_set_enabled` / `mock_rule_remove` | Призупинити правило / відправити в архів | Коли сценарій завершено |
| `mock_dispatcher` | Запускає, зупиняє або перевіряє локальний диспетчер; start/stop заразом вмикають і вимикають Map Remote у Charles, щоб трафік не впирався в зупинений диспетчер | Перед тестом і після |

## Ключова поведінка

### 1. За замовчуванням повертаються сирі дані

Ця версія більше не маскує вміст запитів і відповідей:

- зведення, деталі, live і history повертають сирі значення;
- якщо потрібне маскування, його має робити MCP-клієнт або агент.

> ⚠️ Токени, cookie і персональні дані з трафіку потрапляють у контекст моделі як є. Використовуйте тестові акаунти і стенди.

### 2. Спочатку зведення, потім деталі

Спершу викликайте `group_capture_analysis`, `query_live_capture_entries` або `analyze_recorded_traffic`, і лише для підтвердженої цілі — `get_traffic_entry_detail`.

Не вмикайте `include_full_body=true` без явної причини.

### 3. Вивід оптимізований під бюджет токенів

Серіалізація всіх зведень і деталей полегшена:

- внутрішні поля `header_map`, `parsed_json`, `parsed_form`, `lower_name` не потрапляють у вивід інструментів;
- значення `null` автоматично прибираються під час серіалізації;
- якщо в детальному вигляді є `full_text`, надлишковий `preview_text` видаляється.

Значення за замовчуванням зменшені, щоб берегти контекстне вікно:

| Параметр | Старе значення | Нове значення |
| --- | --- | --- |
| `max_items` | 20 | 10 |
| `max_preview_chars` | 256 | 128 |
| `max_headers_per_side` | 8 | 6 |
| `max_body_chars` | 4096 | 2048 |

Якщо потрібен ширший огляд, більші значення так само можна передати явно.

### 4. Деталі з історії потребують стабільного ідентифікатора джерела

Зведення по історії повертають `recording_path`, зведення по live — `capture_id`.

Для `get_traffic_entry_detail`:

- в історії передавайте `recording_path`;
- у live передавайте `capture_id`.

### 5. Збій `stop_live_capture` відновлюваний

У `stop_live_capture` два стабільні кінцеві стани:

- `status="stopped"` — захоплення справді закрите;
- `status="stop_failed"` — короткий повтор теж не вдався, але захоплення збережене.

Якщо результат такий:

```json
{
  "status": "stop_failed",
  "recoverable": true,
  "active_capture_preserved": true
}
```

означає, що захоплення так само можна читати, діагностувати і згодом зупинити повторно.

## Розробка

CI пропускає зміни лише після успішного проходження ruff, mypy і pytest. Локально запускайте те саме:

```bash
python -m ruff check charles_mcp tests
python -m mypy charles_mcp
python -m pytest -q
```

Корисні команди для локального запуску:

```bash
python charles-mcp-server.py
python -c "from charles_mcp.main import main; main()"
```

## Див. також

- [Документація](docs/README.uk.md)
- [Як це працює: схеми та налаштування Kiro](docs/how-it-works.uk.html)
- [English README](README.en.md)
- [Контракт інструментів](docs/contracts/tools.uk.md)
