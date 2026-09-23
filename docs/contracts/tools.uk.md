# Контракт інструментів

Цей документ визначає **канонічний публічний набір інструментів** (canonical public surface) Charles MCP і контракт кожного інструмента.
`create_server()` публікує лише канонічні інструменти, перелічені тут.

> Переклад для людей. Оригінал — [tools.md](tools.md); за розбіжності вірний оригінал. Машинозчитуваний блок, за яким контрактні тести звіряють набір інструментів, є лише в оригіналі.

Встановлення, змінні оточення та приклади налаштування Claude CLI / Codex CLI / Antigravity:

- [README.md](../../README.md)
- [README.en.md](../../README.en.md)
- [docs/README.uk.md](../README.uk.md)
- [AGENTS.uk.md](../../AGENTS.uk.md)
- [agent-workflows.uk.md](../agent-workflows.uk.md)

Припущення за замовчуванням збігаються з README:

- `CHARLES_USER=admin`
- `CHARLES_PASS=123456`
- `CHARLES_MANAGE_LIFECYCLE=false`

## Загальні правила

1. live і history — два незалежні ланцюжки, не змішуйте ідентифікатори джерел.
2. Спочатку групування або зведення, потім деталі.
3. Вважайте зведення основним джерелом даних; не запитуйте повне тіло від початку.
4. Тільки `stop_live_capture.status="stopped"` означає справді завершене закриття.
5. Вивід полегшений під час серіалізації: `header_map`, `parsed_json`, `parsed_form`, `lower_name` до нього не потрапляють; значення `null` прибираються автоматично.

## Канонічний набір

Канонічні публічні інструменти:

```json
{
  "canonical_public_tool_names": [
    "start_live_capture",
    "read_live_capture",
    "peek_live_capture",
    "stop_live_capture",
    "query_live_capture_entries",
    "list_recordings",
    "get_recording_snapshot",
    "query_recorded_traffic",
    "analyze_recorded_traffic",
    "group_capture_analysis",
    "get_capture_analysis_stats",
    "get_traffic_entry_detail",
    "charles_status",
    "throttling",
    "reset_environment",
    "reverse_import_session",
    "reverse_list_captures",
    "reverse_query_entries",
    "reverse_get_entry_detail",
    "reverse_decode_entry_body",
    "reverse_replay_entry",
    "reverse_discover_signature_candidates",
    "reverse_list_findings",
    "reverse_start_live_analysis",
    "reverse_peek_live_entries",
    "reverse_read_live_entries",
    "reverse_stop_live_analysis",
    "reverse_charles_recording_status",
    "reverse_analyze_live_login_flow",
    "reverse_analyze_live_api_flow",
    "reverse_analyze_live_signature_flow",
    "mock_setup_host",
    "mock_create_from_entry",
    "mock_write",
    "mock_list",
    "mock_get",
    "mock_remove",
    "mock_set_enabled",
    "mock_route_setup",
    "mock_discover_variants",
    "mock_rule_create_from_entry",
    "mock_rule_write",
    "mock_rule_list",
    "mock_rule_get",
    "mock_rule_set_enabled",
    "mock_rule_remove",
    "mock_dispatcher",
    "purge_stored_data"
  ]
}
```

## Рекомендований набір інструментів

Документ описує лише канонічні публічні інструменти.

### Інструменти live-захоплення

| Інструмент | Основний контракт |
| --- | --- |
| `start_live_capture` | Повертає новий або підхоплений `capture_id`; від нього залежать усі подальші live-інструменти; захоплення завжди включає трафік, записаний до виклику |
| `read_live_capture` | Інкрементально читає за `capture_id + cursor` і зсуває курсор |
| `peek_live_capture` | Показує нові записи за `capture_id + cursor`, не зсуваючи курсор |
| `stop_live_capture` | Завершує захоплення, за потреби зберігає його; повертає `status`, `recoverable`, `active_capture_preserved` |
| `query_live_capture_entries` | Видає структуроване зведення по live-захопленню і повертає `next_cursor` |

### Інструменти історії

| Інструмент | Основний контракт |
| --- | --- |
| `list_recordings` | Повертає список доступних записів |
| `get_recording_snapshot` | Повертає сирий вміст знімка одного запису |
| `query_recorded_traffic` | Легка фільтрація останнього збереженого запису |
| `analyze_recorded_traffic` | Структуроване зведення за вказаним або останнім записом |

### Спільні інструменти аналізу

| Інструмент | Основний контракт |
| --- | --- |
| `group_capture_analysis` | Групує за `host`, `path`, `status` та іншими вимірами; підходить, щоб спершу побачити гарячі точки |
| `get_capture_analysis_stats` | Повертає лічильники за класами і загальні підсумки |
| `get_traffic_entry_detail` | Читає деталі лише одного запису `entry_id`; не для масового вивантаження деталей |

### Інструменти стану і керування

| Інструмент | Основний контракт |
| --- | --- |
| `charles_status` | Повертає зв'язок з Charles і стан активного захоплення |
| `throttling` | Вмикає пресет сповільнення мережі в Charles |
| `reset_environment` | Відновлює конфігурацію Charles і очищає робоче оточення |

### Інструменти реверс-аналізу

| Інструмент | Основний контракт |
| --- | --- |
| `reverse_import_session` | Імпортує офіційну XML- або нативну сесію Charles і повертає `capture_id` для подальших reverse-запитів і replay |
| `reverse_list_captures` | Повертає список наборів даних, імпортованих у reverse-SQLite |
| `reverse_query_entries` | Фільтрує імпортовані reverse-записи лише за полями маршруту, не розкриваючи деталі |
| `reverse_get_entry_detail` | Читає канонічну деталізацію одного reverse-запису: request / response / body blob / декодовані артефакти |
| `reverse_decode_entry_body` | Структурно декодує тіло запиту або відповіді одного reverse-запису |
| `reverse_replay_entry` | Повторює один reverse-запис, за потреби зберігає experiment / run / finding |
| `reverse_discover_signature_candidates` | Порівнює поля кількох reverse-записів і ранжує параметри, схожі на підпис |
| `reverse_list_findings` | Повертає знахідки за replay і підписами |
| `reverse_start_live_analysis` | Запускає reverse live-сесію; подальші reverse live-інструменти залежать від `live_session_id` |
| `reverse_peek_live_entries` | Читає новий трафік reverse live-сесії, не зсуваючи курсор |
| `reverse_read_live_entries` | Читає новий трафік reverse live-сесії і зсуває курсор |
| `reverse_stop_live_analysis` | Зупиняє reverse live-сесію; за параметром вирішує, чи відновлювати стан запису |
| `reverse_charles_recording_status` | Одночасно повертає стан запису Charles і reverse live-сесії |
| `reverse_analyze_live_login_flow` | Виконує на reverse live-трафіку сценарій, спрямований на логін / авторизацію |
| `reverse_analyze_live_api_flow` | Виконує на reverse live-трафіку сценарій, спрямований на API |
| `reverse_analyze_live_signature_flow` | Виконує на reverse live-трафіку сценарій, спрямований на підписи / динамічні параметри |

### Інструменти моків Map Local

| Інструмент | Контракт |
| --- | --- |
| `mock_setup_host` | Створює `<CHARLES_MOCK_DIR>/<host>/`; `apply=false` повертає кроки ручного налаштування Map Local + Rewrite, `apply=true` записує обидва правила в конфіг Charles, попередньо робить його бекап, відмовляється працювати при запущеному Charles і не створює дублікатів для одного хоста |
| `mock_create_from_entry` | Читає один запис (`capture_id` для live, `recording_path` для history), застосовує правки `patches` у форматі JSON Pointer (`set` / `remove`) і пише мок для host і path цього запису; `warnings` попереджають про метод, статус і query, які Map Local ігнорує |
| `mock_write` | Пише рівно одне з: `body` (JSON-значення) або `body_text` |
| `mock_list` | Показує активні моки; архівні версії не включаються |
| `mock_get` | Повертає вміст одного мока, обрізаний до `max_chars` |
| `mock_remove` | Переносить мок у `_archive/`; нічого не видаляє |
| `mock_set_enabled` | Викликає `/tools/map-local/enable` або `/disable`; файли моків лишаються на диску |

Попередній мок за тим самим шляхом архівується перед перезаписом. Відповіді, віддані з мока, містять заголовок відповіді `X-Charles-Map-Local`.

### Інструменти правил диспетчера

| Інструмент | Контракт |
| --- | --- |
| `mock_route_setup` | Нормалізує `host` (ім'я хоста або шаблон виду `*.example.com`, але не `*`) і `path` (`/*` за замовчуванням, `/prefix/*` або один шлях) і оновлює маршрут у `<CHARLES_MOCK_DIR>/_rules/routes.json`; `apply=false` повертає кроки налаштування Map Remote, `apply=true` записує `https://<host>:<port><path>` → `http://127.0.0.1:<CHARLES_DISPATCHER_PORT>` (для шаблону шляху без шляху призначення, тому Charles зберігає вихідний шлях) з `preserveHostHeader=true`, попередньо робить бекап конфіга, відмовляється працювати при запущеному Charles і є ідемпотентним (відсутній `<enabled>` за замовчуванням вважається true); `verify_tls=false` вимикає перевірку сертифіката для цього маршруту і додає попередження |
| `mock_discover_variants` | Проходить усе захоплення вікнами по 200 записів (до `limit`) з пресетом `api_focus` і необов'язковими `host_contains` / `path_contains` / `methods`; групує за методом, хостом, шляхом і значенням у `body_field` (JSON або form; `null`, якщо поля немає або в методу немає тіла); для кожної групи повертає `value_json`, `count`, до 3 `entry_ids` і статуси, плюс `total_groups` |
| `mock_rule_create_from_entry` | Зіставляє метод і шлях запису (або `match_path` — шаблон, де `*` замінює один сегмент чи його частину; він має покривати шлях запису) і значення в `match_body_fields` / `match_query_fields`; хост правила — `*` (`host_scope="any"`), хост запису (`"exact"`) або `host`; `mode="patch"` зберігає `response_patches` / `request_patches` / `request_headers` (значення задає заголовок, `null` видаляє; `Host` і `Content-Length` відхиляються), `mode="fixture"` зберігає зняте тіло без змін як `<rule_id>.body` і застосовує `response_patches` під час віддачі; `status` підміняє код відповіді, `delay_ms` затримує її на вказані мілісекунди; за `merge=true` наявне правило того самого варіанта і режиму зберігає свої правки, а нова правка того самого вказівника замінює попередню; правки пробно застосовуються до знятого запиту й відповіді та відхиляються, якщо не підходять |
| `mock_rule_write` | Перевіряє повний документ правила; правилам fixture потрібен `fixture_json` або `fixture_text`, якщо фікстури ще немає |
| `mock_rule_list` | Повертає маршрути (з позначкою `verify_tls=false`, якщо перевірку вимкнено), зведення правил (`body_match_json` зберігає значення null/false) і `errors` для файлів правил, які диспетчер ігнорує |
| `mock_rule_get` | Повертає `rule_json` і для правил fixture — фікстуру, обрізану до `max_chars` |
| `mock_rule_set_enabled` | Міняє прапорець `enabled` правила без архівування |
| `mock_rule_remove` | Переносить правило і фікстуру в `_archive/_rules/<host>/<timestamp>/`; нічого не видаляє |
| `mock_dispatcher` | `start` запускає диспетчер на `127.0.0.1` усередині MCP-сервера (зупиняється разом з ним) і за `toggle_map_remote=true` (за замовчуванням) вмикає Map Remote у Charles, щойно на порту хтось відповідає; `stop` зупиняє цей екземпляр і вимикає Map Remote; `status` повідомляє, чи відповідає щось на порту; `verify` стукає в диспетчер напряму, а потім через проксі Charles — по одному URL на кожен налаштований маршрут, і для кожного каже, чи направив його Charles у диспетчер. Правила, фікстури і збережені мапінги лишаються |
| | Charles завантажує мапінги Map Remote лише під час запуску: мапінг, записаний `mock_route_setup(apply=true)`, не діє, доки користувач не запустить Charles. `verify` доводить це, не чіпаючи застосунок |

Поведінка диспетчера: запити, не покриті маршрутом з `routes.json` (спочатку точний хост, потім найвужчий шаблон, потім шаблон шляху), отримують 421; кандидати — правила самого хоста плюс правила з `_any/`, чий шаблон хоста підходить; перемагає ввімкнене відповідне правило з більшим `priority`, потім точний хост важливіший за шаблон, потім точний шлях важливіший за шаблон шляху, потім більше умов, потім менший `id`; запити без правила пересилаються без змін; вихідні запити ніколи не йдуть через системний проксі; кожна відповідь містить `X-Charles-MCP-Rule: <id правила | passthrough | error>`, а помилки правок повідомляються в `X-Charles-MCP-Warning` і не роняють запит. Тіла запитів і відповідей не пишуться в лог.

## Рекомендований порядок викликів

### Live

1. `start_live_capture`
2. `group_capture_analysis`
3. `query_live_capture_entries`
4. `get_traffic_entry_detail`
5. `stop_live_capture`

Чому так:

- `group_capture_analysis` найощадливіший за токенами — зручно спершу знайти гарячі точки;
- `query_live_capture_entries` повертає структуроване зведення — зручно для безперервної фільтрації;
- `get_traffic_entry_detail` використовується лише після підтвердження цілі.

### History

1. `list_recordings`
2. `analyze_recorded_traffic`
3. `group_capture_analysis(source="history")`
4. `get_traffic_entry_detail`

## Домовленості summary-first

### `query_live_capture_entries`

Звертайте увагу на поля:

- `items`
- `matched_count`
- `filtered_out_count`
- `filtered_out_by_class`
- `next_cursor`
- `warnings`

### `analyze_recorded_traffic`

Звертайте увагу на поля:

- `items`
- `matched_count`
- `filtered_out_count`
- `filtered_out_by_class`
- `warnings`

### `group_capture_analysis`

Підтримувані поширені групування:

- `host`
- `path`
- `response_status`
- `resource_class`
- `method`
- `host_path`
- `host_status`

Звертайте увагу на поля:

- `groups`
- `matched_count`
- `filtered_out_count`
- `filtered_out_by_class`
- `warnings`

## Контракт видимості даних

Поточна реалізація за замовчуванням повертає сирий вміст:

- summary / detail / live / history більше не маскуються;
- якщо верхньому рівню потрібне маскування, його має робити MCP-клієнт або агент.

## Контракт деталей

### `get_traffic_entry_detail`

Правила:

1. спочатку визначте `entry_id` через зведення або групування;
2. у history використовуйте `recording_path`;
3. у live використовуйте `capture_id`;
4. без явної потреби не вмикайте `include_full_body=true`.

Параметри за замовчуванням (оптимізовані під бюджет токенів):

| Параметр | За замовчуванням | Опис |
| --- | --- | --- |
| `include_full_body` | `false` | Чи включати повне тіло |
| `max_body_chars` | `2048` | Максимум символів у full_text |

Правила серіалізації виводу:

- `header_map` у вивід не потрапляє (використовується лише для внутрішнього зіставлення); заголовки беріть зі списку `headers`;
- `parsed_json` і `parsed_form` у вивід не потрапляють (їх покривають `full_text` або `preview_text`);
- якщо є `full_text`, надлишковий `preview_text` видаляється автоматично;
- усі поля зі значенням `null` автоматично прибираються;
- якщо вивід перевищує 12 000 символів, у `warnings` з'являється порада звузити запит.

Правила прив'язки деталей історії:

- зведення по history повертає `recording_path`;
- зведення по live повертає `capture_id`;
- якщо в деталей history немає ідентифікатора джерела, має повертатися помилка;
- мовчазного відкату до останнього запису більше немає.

## Контракт `stop_live_capture`

### Успішний стан

```json
{
  "status": "stopped",
  "recoverable": false,
  "active_capture_preserved": false
}
```

Значення:

- зупинка пройшла успішно;
- активне захоплення закрите.

### Відновлюваний стан збою

```json
{
  "status": "stop_failed",
  "recoverable": true,
  "active_capture_preserved": true
}
```

Значення:

- після одного короткого повтору зупинка все одно не вдалася;
- захоплення збережене;
- можна продовжувати `read_live_capture`;
- можна знову викликати `stop_live_capture`.

За `stop_failed` агент має:

1. зберегти `capture_id`;
2. не вважати захоплення закритим;
3. прочитати `error` і `warnings`;
4. за потреби викликати `charles_status`;
5. якщо треба довести закриття до кінця — знову викликати `stop_live_capture`.

Пов'язані попередження:

- `stop_recording_retry_succeeded`
- `stop_recording_failed_after_retry`
