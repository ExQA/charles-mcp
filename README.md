# Charles MCP Server

[![Версія](https://img.shields.io/badge/%D0%B2%D0%B5%D1%80%D1%81%D1%96%D1%8F-3.1.0rc2-0e7c76.svg)](CHANGELOG.md)
[![Ліцензія](https://img.shields.io/badge/%D0%BB%D1%96%D1%86%D0%B5%D0%BD%D0%B7%D1%96%D1%8F-MIT-0e7c76.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-0e7c76.svg)](pyproject.toml)

[English README](README.en.md) | [Документація](docs/README.uk.md) | [Посібник для агентів](docs/agent-guide.uk.md) | [Мапінг і перезапуск Charles](docs/charles-mapping.uk.md) | [Контракт інструментів](docs/contracts/tools.uk.md) | [AGENTS](AGENTS.uk.md) | [Сценарії для агента](docs/agent-workflows.uk.md) | [Встановлення для команди](docs/team-installation.uk.md)

**Дайте ШІ-агенту доступ до Charles Proxy.** Агент сам знаходить потрібні запити мобільного застосунку, пояснює, що пішло не так, і підміняє відповіді для тестування — без копіювання трафіку в чат і без ручних правил у Charles.

![Charles MCP: застосунок, Charles, агент і диспетчер моків](docs/images/charles-mcp-overview.png)

## Навіщо

Коли тестуєш мобільний клієнт через Charles, більшість часу йде на рутину:

- знайти серед сотень записів один запит і скопіювати його в чат з агентом;
- підмінити одне поле у відповіді, а Map Local вміє лише «один файл на весь URL»: не розрізняє POST-запити з різним `action` у тілі й завжди віддає 200;
- перевірити екран помилки, порожній стан чи повільний ендпоінт — і не забути потім усе вимкнути.

`charles-mcp` — MCP-сервер, через який агент (Claude Code, Cursor, Codex, Kiro та інші MCP-клієнти) робить це сам: читає сесію Charles, знаходить запит, будує мок зі справжньої відповіді, змінюючи лише названі поля, і перевіряє, що застосунок отримав підмінену відповідь.

## Що вміє

- **Читати трафік стисло.** Агент спершу бачить групи й короткі зведення, а не сирий дамп, і відкриває один запит лише тоді, коли треба. Працює і з живою сесією, і зі збереженими `.chlsj`.
- **Підміняти відповіді за тілом запиту.** Локальний диспетчер за одним правилом Map Remote розрізняє запити за методом, шляхом, query і полями тіла (`{"action": "get_balance"}`). Режими: правка полів справжньої відповіді або фікстура з будь-яким статусом (500, 401) і затримкою.
- **Моки — це дані, а не код.** Правила й фікстури лежать у `~/charles-mocks`, поза репозиторіями; у Charles налаштовується одне правило на домен.
- **Реверс-аналіз.** Імпорт сесій, декодування тіл (зокрема protobuf), повтор запиту зі змінами, пошук полів-підписів.
- **Безпечні значення за замовчуванням.** Не очищає сесію Charles, не пише його конфіг, поки Charles працює, і вимикає Map Remote, який увімкнув сам.

## Як це працює

![Як працює підміна: Map Remote, диспетчер, правила](docs/images/charles-mcp-mocking.png)

Застосунок ходить у мережу через Charles, як завжди. Сервер читає сесію Charles через його Web Interface. Для моків Charles одним правилом Map Remote відправляє запити домену в локальний диспетчер: той відповідає за правилом (фікстура або правка справжньої відповіді), а все без правила пропускає на справжній сервер без змін. Кожна відповідь несе заголовок `X-Charles-MCP-Rule`, тож агент перевіряє результат по свіжому трафіку.

## Приклад

![Три стани екрана гаманця: справжня відповідь, баланс 0, помилка 500](docs/images/charles-mcp-testing.png)

> **Ви:** На екрані гаманця баланс 10.50. Зроби, щоб `get_balance` повертав 0, а `init` — помилку 500.

Агент:

1. `start_live_capture` і `mock_discover_variants` — бачить `POST /api/v1/wallet` з двома варіантами `action`: `init` і `get_balance`.
2. `mock_rule_create_from_entry` двічі: для `get_balance` — правка `/data/balance` у справжній відповіді, для `init` — знята відповідь зі статусом 500.
3. `mock_dispatcher(action="start")`; ви оновлюєте екран.
4. `query_live_capture_entries(response_header_name="X-Charles-MCP-Rule")` — відповіді прийшли від обох правил, решта запитів пішла на сервер без змін.

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

Ще два налаштування Charles, обидва робляться один раз:

- **Виключіть власні запити сервера із запису.** Відкрийте `Proxy -> Recording Settings -> Exclude`, натисніть `Add` і вкажіть Host `control.charles`, решту полів залиште порожніми. Діє одразу, без перезапуску. Кожен виклик інструмента вивантажує сесію через проксі Charles; без цього виключення Charles записує кожне вивантаження з усією сесією як тілом, тож сесія зростає з кожним викликом (зі 100 КБ до 2 ГБ за один тестовий прогін), а інструменти сповільнюються до десятків секунд. Сервер попереджає про це через `charles_records_own_exports`.
- **Увімкніть SSL Proxying для хостів, які хочете читати.** Відкрийте `Proxy -> SSL Proxying Settings`, додайте хост API і переконайтеся, що пристрій довіряє кореневому сертифікату Charles. Без цього Charles записує HTTPS-з'єднання як один непрозорий тунель, і інструментам нема чого показати — ні шляхів, ні тіл; вони повідомляють про це попередженням `ssl_proxying_off`.

### 2. Встановіть і налаштуйте MCP-клієнт

Потрібні Python 3.10+ і [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
git clone https://github.com/ExQA/charles-mcp.git
cd charles-mcp && uv sync --locked
uv run charles-mcp --selftest
```

Далі `<PATH>` — абсолютний шлях до цієї папки. `--selftest` друкує версію і кількість інструментів і закінчується `OK`.

> Ставте саме з цього репозиторію. Пакет `charles-mcp` у PyPI — інший, старіший проєкт (upstream 3.0.3) без інструментів моків, тож `uvx charles-mcp` і `pip install charles-mcp` дадуть не той сервер. Для машин без доступу до мережі є офлайн-архів із залежностями: [docs/team-installation.uk.md](docs/team-installation.uk.md).

У `CHARLES_USER` / `CHARLES_PASS` нижче підставте свої дані від Charles Web Interface; наведені значення — заглушки.

#### Claude Code CLI

```bash
claude mcp add-json charles '{
  "type": "stdio",
  "command": "uv",
  "args": ["run", "--project", "<PATH>", "charles-mcp"],
  "env": {
    "CHARLES_USER": "ваш-логін",
    "CHARLES_PASS": "ваш-пароль",
    "CHARLES_MANAGE_LIFECYCLE": "false"
  }
}'
```

#### Claude Desktop / Cursor / Kiro / загальний JSON-конфіг

```json
{
  "mcpServers": {
    "charles": {
      "command": "uv",
      "args": ["run", "--project", "<PATH>", "charles-mcp"],
      "env": {
        "CHARLES_USER": "ваш-логін",
        "CHARLES_PASS": "ваш-пароль",
        "CHARLES_MANAGE_LIFECYCLE": "false"
      }
    }
  }
}
```

Якщо ставили з офлайн-архіву (`./install.sh`), замініть ці два рядки на програму з його віртуального оточення:

```json
"command": "<PATH>/.venv/bin/charles-mcp",
"args": []
```

Kiro читає `.kiro/settings/mcp.json` у проєкті (він виграє) або `~/.kiro/settings/mcp.json` і додатково приймає `"disabled": false` та `"autoApprove": [...]`.

#### Codex CLI

```toml
[mcp_servers.charles]
command = "uv"
args = ["run", "--project", "<PATH>", "charles-mcp"]

[mcp_servers.charles.env]
CHARLES_USER = "ваш-логін"
CHARLES_PASS = "ваш-пароль"
CHARLES_MANAGE_LIFECYCLE = "false"
```

### Автовстановлення через AI-агента

Дайте агенту (Claude Code, Cursor Agent, Kiro, Gemini CLI, ChatGPT з інструментами) промпт нижче, замінивши `<PATH>` на абсолютний шлях до чекауту чи розпакованого архіву. Промпт лишено англійською: агенти виконують його однаково будь-якою мовою спілкування.

<details>
<summary><strong>Натисніть, щоб розкрити промпт автовстановлення</strong></summary>

```text
Install the charles-mcp MCP server from the local directory <PATH> and configure
my MCP client to use it. Follow these steps exactly.

Install only from that directory: the same name on PyPI is a different, older
package, so a registry install looks healthy but has no mocking tools. Step 3
checks for them, so do not skip it.

Step 1 - Check the directory:
  Confirm <PATH> exists and contains pyproject.toml, charles_mcp/ and
  charles-mcp-server.py. If it does not, stop and tell me.
  Note whether it also contains wheels/ and requirements-lock.txt.

Step 2 - Build the environment, preferring the offline path:
  a) If wheels/ and requirements-lock.txt exist, use them (no network):
       cd <PATH>
       python3 -m venv .venv
       .venv/bin/pip install --no-index --find-links wheels -r requirements-lock.txt
     On Windows the interpreter is .venv\Scripts\python.exe.
     If pip reports no matching distribution, the wheels were built for other
     Python versions - report the Python version you used and stop.
  b) Otherwise, if `uv --version` works:
       cd <PATH> && uv sync --locked
  c) Otherwise tell me which of the two is missing instead of improvising.

Step 3 - Verify the build, not just that something starts:
    <PATH>/.venv/bin/python <PATH>/charles-mcp-server.py --selftest
  (or `uv run --project <PATH> charles-mcp --selftest` if you took path 2b).
  It prints the version and the tool counts and ends with OK. Stop and tell me
  if it prints FAIL, reports 0 mocking tools, or rejects --selftest as an
  unknown option: that build is not this fork, so the configuration would be
  pointless. A line about default credentials is expected and fine.

Step 4 - Detect my MCP client, first match wins:
  a) Kiro - .kiro/settings/mcp.json in the current project, else ~/.kiro/settings/mcp.json
  b) Claude Code - `claude --version` succeeds
  c) Cursor - ~/.cursor/mcp.json or .cursor/mcp.json
  d) Claude Desktop - ~/Library/Application Support/Claude/claude_desktop_config.json
     (Windows: %APPDATA%\Claude\claude_desktop_config.json,
      Linux: ~/.config/Claude/claude_desktop_config.json)
  e) Windsurf - ~/.codeium/windsurf/mcp_config.json
  If none is found, ask me.

Step 5 - Ask me for my Charles Web Interface login and password before writing
  them anywhere. Do not invent values and do not reuse the examples from the
  documentation.

Step 6 - Write the config entry, using ABSOLUTE paths:
    "charles": {
      "command": "<PATH>/.venv/bin/python",
      "args": ["<PATH>/charles-mcp-server.py"],
      "env": {
        "CHARLES_USER": "<the login I gave you>",
        "CHARLES_PASS": "<the password I gave you>",
        "CHARLES_MANAGE_LIFECYCLE": "false"
      }
    }
  If you used uv (path 2b) instead:
      "command": "uv",
      "args": ["run", "--project", "<PATH>", "charles-mcp"]

  Read any existing config file first, parse the JSON, add "charles" inside
  "mcpServers" (create "mcpServers" if absent) and write it back. Do not drop or
  overwrite other servers. Kiro also accepts "disabled": false and "autoApprove";
  if you set autoApprove, list only read-only tools: charles_status,
  start_live_capture, query_live_capture_entries, group_capture_analysis. Never
  auto-approve reset_environment, mock_setup_host, mock_route_setup or
  reverse_replay_entry - they close Charles, write its config, or send real
  requests to real servers.

Step 7 - Report which client you configured and which config file you edited,
  and tell me to restart that client. For Kiro, saving mcp.json reconnects the
  server - it should appear in the MCP panel. Remind me that Charles must be
  running with its Web Interface enabled (Proxy -> Web Interface Settings).
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
| `CHARLES_RETENTION_DAYS` | `0` | Скільки днів зберігати знімки, дані reverse-аналізу і бекапи конфігу; старші видаляються під час старту сервера. `0` вимикає |
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
   - Сегменти шляху, що залежать від платформи чи версії застосунку, замінюйте на `*` через `match_path`, наприклад `/api/*/wallet`.
   - За замовчуванням правило діє на всіх хостах маршруту (`host_scope="any"`); `host_scope="exact"` або `host="stage.example.com"` звужують його, і правило для конкретного хоста перемагає загальне.
   - Повторне прохання для того самого варіанта доповнює його правило (`merge=true`); правка того самого поля замінює попередню.
4. `mock_dispatcher(action="start")`, потім повторіть дії в застосунку.
5. Перевірте через `query_live_capture_entries(response_header_name="X-Charles-MCP-Rule")`: у заголовку id правила або `passthrough`.

Зберігання в `<CHARLES_MOCK_DIR>/_rules/`: `routes.json` (маршрути), `_any/` (правила для будь-якого хоста або шаблону хоста), `<host>/` (правила для одного хоста), фікстури поруч із правилами як `<rule_id>.body`. Диспетчер перечитує їх на кожен запит, тому правки діють одразу. Він пересилає лише запити, покриті маршрутом, слухає `127.0.0.1` і має працювати, доки в Charles увімкнене правило Map Remote; для постійної роботи є команда `charles-mcp-dispatcher`. Маршрут на весь хост (`/*`) пропускає через диспетчер і статику, і WebSocket: завантаження буферизуються, а WebSocket не підтримується, тому краще вказувати префікс API.

> ⚠️ Фікстури — це зняті справжні відповіді, у них можуть бути персональні та платіжні дані. Тримайте `CHARLES_MOCK_DIR` поза репозиторіями і нікому не пересилайте; діліться описами правил із синтетичними даними.

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
| `get_recording_snapshot` | Показує один збережений запис короткими зведеннями, посторінково, без тіл | Коли треба побачити, що лежить у збереженому знімку |
| `query_recorded_traffic` | Легка фільтрація останнього збереженого запису | Для швидкого пошуку за host, методом або регулярним виразом |

### Інструменти стану і керування

| Інструмент | Що робить | Коли використовувати |
| --- | --- | --- |
| `charles_status` | Показує зв'язок з Charles і стан активного захоплення; поле `recommended_next_action` підказує наступний крок | Щоб перевірити, чи доступний Charles і чи активне захоплення |
| `throttling` | Вмикає пресет сповільнення мережі в Charles | Для емуляції 3G, 4G, 5G або вимкнення сповільнення |
| `reset_environment` | Відновлює конфігурацію Charles і очищає поточне оточення | Коли треба повернутися до чистого стану |
| `charles_recording_exclude` | Додає хост (за замовчуванням `control.charles`) у Proxy → Recording Settings → Exclude: повертає кроки для UI або з `apply=true` записує конфіг, доки Charles закритий | Один раз під час налаштування, щоб Charles не записував власні вивантаження сервера |
| `purge_stored_data` | Видаляє збережені знімки, дані реверс-аналізу і бекапи конфігу, старші за поріг; за замовчуванням `dry_run=true` | Щоб прибрати збережений трафік |

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
| `mock_scenario_save` / `mock_scenario_apply` | Називає набір правил (цілий сценарій QA, наприклад «оплата падає після логіну») і вмикає його одним викликом; `exclusive` вимикає всі інші правила | Щоб перемикатися між сценаріями без залишків |
| `mock_scenario_list` / `mock_scenario_remove` | Показує сценарії і чи активні вони повністю / видаляє сценарій, не чіпаючи його правил | Щоб переглянути чи прибрати збережені сценарії |

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

Значення за замовчуванням невеликі, щоб берегти контекстне вікно:

| Параметр | За замовчуванням |
| --- | --- |
| `max_items` | 10 |
| `max_preview_chars` | 128 |
| `max_headers_per_side` | 6 |
| `max_body_chars` | 2048 |

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

## Походження

Це форк [heizaheiza/Charles-mcp](https://github.com/heizaheiza/Charles-mcp) (MIT). Форк додав підміну відповідей (Map Local і диспетчер правил), роботу з довгими сесіями та виправлення, описані в [CHANGELOG.md](CHANGELOG.md). Подробиці — у [PROVENANCE.md](PROVENANCE.md).

## Див. також

- [Документація](docs/README.uk.md)
- [Як це працює: схеми та налаштування Kiro](docs/how-it-works.uk.html)
- [English README](README.en.md)
- [Контракт інструментів](docs/contracts/tools.uk.md)
