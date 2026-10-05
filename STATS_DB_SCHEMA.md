# Карта схемы `stats.db`

Источник исходного описания: продовая SQLite-база `/root/UDB_bot/stats.db` на VPS, схема прочитана 2026-06-06. Банковские таблицы и `sit_ledger` сверены с `bank_core.ensure_schema` и `db.initialize_db` 2026-10-02; это сверка с кодом, а не новый снимок продовой БД.

Назначение файла: компактное описание схемы для text2sql. Описания намеренно короткие, но смысловые.

## Общие правила

- `user_id` - Telegram ID пользователя.
- `chat_id` - Telegram ID чата; групповые чаты обычно имеют отрицательный `chat_id`.
- Даты в полях `date`, `date_taken`, `date_completed`, `catch_date`, `grow_date`, `subscription_till` обычно хранятся как текст `YYYY-MM-DD`.
- Время в поле `time` обычно `HH:MM`, в `scheduled_time` - `HH:MM`, в `sit_stats.time` - `HH:MM:SS`.
- Поля-флаги обычно `INTEGER`: `0` = нет/выключено, `1` = да/включено.
- Валюта бота называется "ситы"; баланс лежит в `users.sits`, полный аудит движений — в `sit_ledger`. `sit_stats` — старый журнал начислений, не полный источник банковских операций.
- Полный аудит пользовательских движений находится в `sit_ledger`; банковские суммы в таблицах `bank_*` хранятся целыми миллиситами, ставки — в базисных пунктах.
- Для имен пользователей в статистике обычно JOIN: `... JOIN users u ON u.user_id = t.user_id AND u.chat_id = t.chat_id`.
- Для обычной статистики активности используйте `daily_stats` за период или `total_stats` за всё время.
- Для текстов сообщений и реакций используйте `messages_reactions`; это самая большая таблица.

## Таблицы

### Справочник `ai_mechanics_sections` и `ai_mechanics_fts`

Повторяемая схема создаётся `mechanics.ensure_schema` при общей инициализации RAG.
- Служебные ai_mechanics_sections/ai_mechanics_fts недоступны пользовательскому SQL; справка проходит проверку актуальности в отдельном поиске, не через чтение индекса моделью.
`ai_mechanics_sections`: id TEXT PK (детерминированный UUID ревизии фрагмента);
document_id, section_id, title, text, hash, revision, sources_json, state, vector_json — TEXT;
current INTEGER NOT NULL DEFAULT 1 (актуальная версия). Остальные поля допускают NULL.
Состояния: prepared, embedded (временный вектор сохранён), active (Qdrant подтвердил запись).
Индекс idx_mechanics_current(current,state). Источники — JSON путей к публичному коду,
вектор — JSON 768 чисел, размерность и модель не смешиваются.
`ai_mechanics_fts` — FTS5 unicode61: id UNINDEXED, text; только актуальные разделы,
доступные лексическому поиску до завершения векторизации. Внешних ключей нет.
Удалённые версии имеют current=0 до подтверждённого удаления точек Qdrant.

Подготовка использует существующие ai_tasks.rag_state/rag_deadline_at и
ai_rag_queries: queued/preparing/done/timeout; UTC ISO-дедлайн пять секунд,
token/lease_until ограждают поздний результат. Триггер ai_mechanics_prepare
срабатывает при INSERT mechanics/text_to_sql/data_analysis_sql после готовности справки.
В payload_json.mechanics сохраняются query, fragments с ревизиями, error и duration_ms;
анализ использует снимок родительского SQL. ai_rag_state хранит mechanics_* состояние,
агрегаты, хеш манифеста и завершение первичного прохода.
ai_rag_usage.purpose дополнительно допускает mechanics_index и mechanics_probe; tokens — токены,
status reserved/done/error. Дневной бюджет общий с историей, сброс America/Los_Angeles.

### Банковские таблицы `bank_*`

Сит-банк существует отдельно для каждого `chat_id`. Все банковские запросы
фильтруются по чату; пользовательские — также по `user_id`.

Денежные поля `_milli` — `INTEGER` в тысячных долях сита: `135000 = 135 сит`.
Для отображения в SQL делить на `1000.0`, чтобы избежать целочисленного деления.
Ставки `_bp` — `INTEGER` в базисных пунктах: `100 bp = 1%`, `600 = 6%`.
Депозитные и кредитные ставки фиксируются в договоре и относятся к неделе;
налоговая ставка применяется к облагаемому начислению.
Даты — `YYYY-MM-DD`, время — ISO datetime; банковский планировщик работает
по `Asia/Yekaterinburg`, клиринг — в 23:00. Договор после 23:00 начинает
банковский срок со следующего дня.

Связи ниже логические: `bank_core.ensure_schema` не объявляет `FOREIGN KEY`
и `CHECK` для банковских таблиц. Значения статусов, диапазоны ставок, суммы,
сроки и запрет отрицательного капитала проверяет бизнес-логика.
Не складывать снимки остатков из журналов: суммируются дельты либо выбирается
последний снимок. Возврат тела вклада и выдача тела кредита не являются доходом.

### `bank_accounts`

Собственные средства и деньги банка чата. Ключ: `chat_id`.
Счёт создаётся лениво с однократной эмиссией 50 сит, отражённой в `bank_ledger`.
Тело вклада увеличивает ликвидность, но не капитал. Резервы, свободная
ликвидность, свободный капитал, Coverage, U и состояние банка вычисляются
в `bank_core.bank_metrics`, отдельных колонок для них здесь нет.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | PK; чат банка. |
| `liquidity_milli` | INTEGER | Обязательное поле; текущие реальные деньги банка. |
| `capital_milli` | INTEGER | Обязательное поле; собственный капитал банка. |
| `key_rate_bp` | INTEGER | Ключевая ставка, NOT NULL, DEFAULT `1000` (10%). |
| `tax_rate_bp` | INTEGER | Налог, NOT NULL, DEFAULT `500` (5%). |
| `last_key_rate_change_date` | TEXT | День последнего изменения КС, NULL до первого изменения. |
| `last_tax_rate_change_date` | TEXT | День последнего изменения налога, NULL до первого изменения. |
| `created_at` | TEXT | NOT NULL; время создания банка. |
| `created_date` | TEXT | NOT NULL; первый банковский день, начало догоняющего клиринга. |

### `bank_ministers`

Министр банка конкретного чата; отсутствие строки означает отсутствие министра.
Глобальные администраторы задаются в конфигурации, не в этой таблице.
Ключ: `chat_id`. Миграция идемпотентно назначает первоначального министра основного чата.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | PK; чат банка. |
| `user_id` | INTEGER | NOT NULL; Telegram ID министра. |
| `appointed_at` | TEXT | NOT NULL; время назначения. |

### `bank_credit_profiles`

Кредитный рейтинг и оставшийся фиксированный дефолтный долг пользователя в чате.
Ключ: `PRIMARY KEY (chat_id, user_id)`; связь с `users` по обоим полям.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | NOT NULL; чат профиля. |
| `user_id` | INTEGER | NOT NULL; пользователь. |
| `rating` | INTEGER | NOT NULL, DEFAULT `15`; рейтинг, в логике ограничен диапазоном 3–60. |
| `default_debt_milli` | INTEGER | NOT NULL, DEFAULT `0`; остаток долга после дефолта, уменьшается взысканиями с доходов. |
| `defaults_count` | INTEGER | NOT NULL, DEFAULT `0`; число состоявшихся дефолтов. |
| `updated_at` | TEXT | NOT NULL; время создания/изменения профиля. |

### `bank_daily_income`

Дневной чистый доход, учитываемый при расчёте кредитного лимита. Ключ:
`PRIMARY KEY (chat_id, user_id, income_date)`; все поля NOT NULL.
Сумма после налога и дефолтного взыскания, только для классифицированных
доходов. P2P, `/charity`, возврат тела вклада и выдача кредита исключены.
Текущий доход по вкладу учитывается. Среднее вычисляется по календарным дням
(включая нулевые), максимум за 90 дней, не ранее 2026-08-29 и первого сообщения
пользователя. Сами дни сообщений находятся в `daily_stats`.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | Чат дохода. |
| `user_id` | INTEGER | Получатель дохода. |
| `income_date` | TEXT | Банковская дата дохода. |
| `amount_milli` | INTEGER | Чистый учтённый доход за день, DEFAULT `0`. |

### `bank_deposits`

Договоры вкладов, включая завершённые. Ключ: `id` (AUTOINCREMENT).
Связь с `users` по `(chat_id, user_id)`.
Частичный уникальный индекс `idx_bank_deposit_one_active(chat_id, user_id)`
при `status='active'` разрешает только один активный вклад пользователя в чате.
Индекс `idx_bank_deposit_maturity(chat_id, status, maturity_date)` — для клиринга.
Все поля NOT NULL, кроме полей предложения/уведомления о продлении и `closed_at`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | PK; номер договора. |
| `chat_id` | INTEGER | Чат банка. |
| `user_id` | INTEGER | Вкладчик. |
| `principal_milli` | INTEGER | Первоначальное тело вклада. |
| `rate_bp` | INTEGER | Зафиксированная недельная ставка договора. |
| `term_weeks` | INTEGER | Срок: 1, 3 или 5 недель. |
| `opened_at` | TEXT | Время открытия. |
| `start_date` | TEXT | Первый день банковского срока. |
| `maturity_date` | TEXT | День окончания срока; расчёт на клиринге. |
| `maturity_milli` | INTEGER | Тело плюс полный договорный доход до налога/взыскания с дохода. |
| `capital_reserve_milli` | INTEGER | Резерв собственного капитала под обычный доход и максимум 30 дней кризисных процентов. |
| `auto_renew` | INTEGER | DEFAULT `0`; согласие на продление тела на тот же срок по новой ставке. |
| `renewal_offer_rate_bp` | INTEGER | NULL или ставка предложения автопродления. |
| `renewal_offer_date` | TEXT | NULL или день подготовки предложения. |
| `renewal_notified_at` | TEXT | NULL или время отметки обработанного уведомления; отметка ставится также при ошибке доставки. |
| `status` | TEXT | DEFAULT `active`; `active`, `closed_early`, `matured`, `renewed`. |
| `closed_at` | TEXT | NULL или время завершения договора. |

`matured` означает передачу обязательства в `bank_deposit_claims`, а не
гарантированную выплату. `renewed` — прежний договор завершён, для продлённого
тела создаётся новая строка; доход прежнего договора становится требованием.
Резерв активных вкладов суммируется только с `status='active'`.

### `bank_loans`

Кредитные договоры. Ключ: `id` (AUTOINCREMENT); связь с `users` по
`(chat_id, user_id)`. Частичный уникальный индекс
`idx_bank_loan_one_active(chat_id, user_id)` при `status='active'` запрещает
несколько активных кредитов в чате. Все поля NOT NULL, кроме `closed_at`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | PK; номер кредита. |
| `chat_id` | INTEGER | Чат банка. |
| `user_id` | INTEGER | Заёмщик. |
| `principal_milli` | INTEGER | Первоначально выданное тело кредита. |
| `remaining_principal_milli` | INTEGER | Непогашенное тело; используется для активного кредитного портфеля. |
| `rate_bp` | INTEGER | Зафиксированная недельная ставка с учётом рейтинга. |
| `term_weeks` | INTEGER | Срок: 1, 3 или 5 недель. |
| `total_milli` | INTEGER | Первоначальная полная договорная сумма тела и процентов. |
| `paid_milli` | INTEGER | DEFAULT `0`; сумма успешных выплат. |
| `issued_at` | TEXT | Время выдачи. |
| `start_date` | TEXT | Первый банковский день кредита. |
| `first_payment_date` | TEXT | День первого платежа после grace-периода. |
| `raw_limit_milli` | INTEGER | Снимок исходного расчётного лимита на момент выдачи. |
| `available_limit_milli` | INTEGER | Снимок доступного лимита с учётом ограничений банка на момент выдачи. |
| `rating_threshold_milli` | INTEGER | Снимок минимальной суммы кредита, позволяющей получить бонус рейтинга. |
| `had_overdue` | INTEGER | DEFAULT `0`; была ли хоть одна просрочка, даже если впоследствии погашена. |
| `status` | TEXT | DEFAULT `active`; `active`, `paid`, `paid_early`, `defaulted`. |
| `closed_at` | TEXT | NULL или время закрытия/дефолта. |

При дефолте текущий фиксированный долг следует брать из
`bank_credit_profiles.default_debt_milli`: `total_milli - paid_milli`
завершённого кредита не отражает последующие взыскания.

### `bank_loan_payments`

График ежедневных платежей и их состояние. Ключ: `id` (AUTOINCREMENT),
`UNIQUE (loan_id, installment_no)`. Связь: `loan_id -> bank_loans.id`.
Индекс `idx_bank_payments_due(status, due_date)`. Все поля NOT NULL, кроме `paid_at`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | PK; ID платежа. |
| `loan_id` | INTEGER | Кредитный договор. |
| `installment_no` | INTEGER | Порядковый номер платежа, начиная с 1. |
| `due_date` | TEXT | День планового списания на клиринге. |
| `amount_milli` | INTEGER | Полная сумма платежа. |
| `principal_milli` | INTEGER | Часть платежа в погашение тела. |
| `interest_milli` | INTEGER | Процентная часть платежа; доход банка. |
| `status` | TEXT | DEFAULT `scheduled`; `scheduled`, `overdue`, `paid`, `cancelled`, `defaulted`. |
| `paid_at` | TEXT | NULL или время успешного платежа. |

Просрочки считаются по `status='overdue'`, привязка к чату/пользователю —
через JOIN с `bank_loans`. `cancelled` — оставшиеся платежи при досрочном
погашении, `defaulted` — оставшиеся платежи кредита, перешедшего в дефолт.

### `bank_deposit_claims`

Неисполненные обязательства по завершённым/продлённым вкладам. Ключ: `id`
(AUTOINCREMENT). Связь: `deposit_id -> bank_deposits.id`, пользователь —
`(chat_id, user_id) -> users`. Индекс `idx_bank_claims_open(chat_id, status)`.
Все поля NOT NULL, кроме `last_capitalized_date`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | PK; ID требования. |
| `deposit_id` | INTEGER | Исходный договор вклада. |
| `chat_id` | INTEGER | Чат банка. |
| `user_id` | INTEGER | Вкладчик. |
| `principal_remaining_milli` | INTEGER | Остаток тела к возврату, освобождён от налога и взыскания. |
| `interest_remaining_milli` | INTEGER | Остаток дохода к выплате, включая кризисные проценты; облагается налогом и взысканием. |
| `rate_bp` | INTEGER | Недельная ставка для кризисной капитализации. |
| `capital_reserve_remaining_milli` | INTEGER | DEFAULT `0`; оставшийся резерв капитала под будущую кризисную капитализацию. |
| `crisis_interest_days` | INTEGER | DEFAULT `0`; число уже начисленных кризисных дней, не более 30 в логике. |
| `created_date` | TEXT | День появления требования. |
| `last_capitalized_date` | TEXT | NULL или последний обработанный день кризисных процентов. |
| `status` | TEXT | DEFAULT `open`; `open` — есть остаток, `paid` — требование полностью погашено. |

Остаток требования: `principal_remaining_milli + interest_remaining_milli`.
В статистику текущих обязательств включать только `status='open'`.
После 30 дней прекращается начисление дополнительных процентов, долг сохраняется.

### `bank_ledger`

Аудит всех движений ликвидности и капитала банка. Ключ: `id` (AUTOINCREMENT);
`idempotency_key` уникален, допускает NULL. Индекс
`idx_bank_ledger_chat_date(chat_id, operation_date)`.
Все поля NOT NULL, кроме `user_id`, `reference_type`, `reference_id`, `idempotency_key`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | PK; порядок банковских операций. |
| `created_at` | TEXT | Время события. |
| `operation_date` | TEXT | Банковский день события; при догоняющем клиринге может быть прошлым. |
| `chat_id` | INTEGER | Банк чата. |
| `user_id` | INTEGER | NULL или участник операции. |
| `event_code` | TEXT | Машинный код события: например `bank_genesis`, `income_split`, `deposit_open`, `credit_payment`, `deposit_claim_payment`. |
| `liquidity_delta_milli` | INTEGER | DEFAULT `0`; изменение ликвидности, со знаком. |
| `capital_delta_milli` | INTEGER | DEFAULT `0`; изменение капитала, со знаком. |
| `liquidity_after_milli` | INTEGER | Снимок ликвидности после операции. |
| `capital_after_milli` | INTEGER | Снимок капитала после операции. |
| `reference_type` | TEXT | NULL или тип ссылки: например `deposit`, `loan`, `claim`. |
| `reference_id` | INTEGER | NULL или ID связанного объекта указанного типа. |
| `idempotency_key` | TEXT | NULL или ключ защиты события от повторного учёта. |
| `metadata_json` | TEXT | DEFAULT `'{}'`; подробности события. |

Для `income_split` JSON содержит `action_code`, `gross_milli`, `player_milli`,
`tax_milli`, `garnishment_milli`. Ликвидность растёт на налог плюс взыскание,
капитал — только на налог. Для аналитики налогов/взысканий извлекать эти
поля через `json_extract`; не считать всю положительную дельту налогом.

### `bank_daily_runs`

Выполненные клиринги и их сохранённые отчёты. Ключ:
`PRIMARY KEY (chat_id, run_date)` предотвращает повторный клиринг за день.
Все поля NOT NULL.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | Чат банка. |
| `run_date` | TEXT | Обработанный банковский день. |
| `completed_at` | TEXT | Время, записанное расчётом; при догоняющем запуске передаётся 23:00 обрабатываемого дня. |
| `report_json` | TEXT | DEFAULT `'{}'`; результат клиринга и снимок метрик. |

JSON включает `chat_id`, `run_date`, `credit_payments_milli`, `new_overdue`,
`new_defaults`, `matured_deposits`, `renewed_deposits`, `claim_payments_milli`,
`crisis_interest_milli`, `tax_income_milli`, `credit_interest_income_milli`,
`deposit_interest_expense_milli` и `metrics`. В `metrics` хранятся вычисленные
остатки, резервы, портфель, Coverage, U и состояние; бесконечный Coverage
сериализуется как JSON `null`.

### `bank_rate_changes`

История управленческих изменений ставок. Ключ: `id` (AUTOINCREMENT).
Все поля NOT NULL; ограничение частоты изменений проверяет код по датам
в `bank_accounts`, а не уникальный индекс этой таблицы.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | PK; ID изменения. |
| `chat_id` | INTEGER | Чат банка. |
| `changed_at` | TEXT | Время изменения. |
| `changed_date` | TEXT | Банковский день изменения. |
| `user_id` | INTEGER | Администратор или министр, изменивший ставку. |
| `rate_kind` | TEXT | `key` — ключевая ставка, `tax` — налог. |
| `old_bp` | INTEGER | Предыдущее значение ставки. |
| `new_bp` | INTEGER | Новое значение ставки. |

### `bank_migrations`

Идемпотентные переносы банковских данных. Ключ: `migration_key`.

| Поле | Тип | Описание |
|---|---:|---|
| `migration_key` | TEXT | PK; уникальное имя выполненного переноса. |
| `applied_at` | TEXT | NOT NULL; время отметки выполнения. |

`credit-income-from-sit-ledger-v1` отмечает однократный перенос
классифицированного положительного дохода с 2026-08-29 из `sit_ledger`
в `bank_daily_income`; исторический доход по вкладу исключён из переноса.
Сама схема создаётся через `CREATE ... IF NOT EXISTS`; недостающие
`bank_deposits.renewal_notified_at` и
`bank_deposit_claims.capital_reserve_remaining_milli` добавляются после проверки
`PRAGMA table_info`. Эти структурные миграции не отмечаются отдельными строками
в `bank_migrations`.

### `sit_ledger`

Полный аудит изменения балансов пользователей через `db.apply_sit_change`.
Ключ: `id` (AUTOINCREMENT). Все поля NOT NULL. Индексы:
`idx_sit_ledger_user_chat_created(user_id, chat_id, created_at)`,
`idx_sit_ledger_chat_created(chat_id, created_at)`,
`idx_sit_ledger_action_created(action_code, created_at)`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | PK; ID движения. |
| `created_at` | TEXT | Время операции. |
| `date` | TEXT | День операции `YYYY-MM-DD`. |
| `time` | TEXT | Время операции `HH:MM:SS`. |
| `chat_id` | INTEGER | Чат баланса. |
| `user_id` | INTEGER | Пользователь. |
| `nick` | TEXT | DEFAULT `''`; снимок username. |
| `display_name` | TEXT | DEFAULT `''`; снимок отображаемого имени. |
| `amount` | REAL | Фактическая дельта баланса в ситах; положительное начисление уже после налога/взыскания. |
| `balance_before` | REAL | Баланс до операции в ситах. |
| `balance_after` | REAL | Баланс после операции в ситах. |
| `action_code` | TEXT | Машинный код источника/операции. |
| `action_ru` | TEXT | Человекочитаемая причина движения. |
| `metadata_json` | TEXT | DEFAULT `'{}'`; детали операции. |

При налоге или взыскании JSON содержит `bank_gross_milli`, `bank_tax_milli`,
`bank_garnishment_milli`, `bank_player_net_milli`. Если удержаний не было,
эти ключи могут отсутствовать. Не смешивать `amount` (ситы) с JSON-суммами
(миллиситы). Для аналитики заработка выбирать доходные `action_code`, а не
все положительные движения: выдача кредита, возврат вклада и переводы тоже
могут иметь положительную дельту.

### `tamagotchi_pets`

Веб-питомец игрока в конкретном чате. В v1 используется только веб-сценой: яйцо с 5 стадиями и вылупившийся верблюдик.

Ключ: `PRIMARY KEY (user_id, chat_id)`.

| Поле | Тип | Описание |
|---|---:|---|
| `user_id` | INTEGER | Telegram ID владельца питомца. |
| `chat_id` | INTEGER | Чат, к которому привязан питомец. |
| `state` | TEXT | Состояние питомца: `egg` или `rabbit`; `rabbit` - внутреннее legacy-значение для любого вылупившегося питомца. |
| `egg_stage` | INTEGER | Стадия яйца `1..5`; frontend может уменьшать на 1 после 10 секунд без клика, после вылупления остаётся `5`. |
| `level` | INTEGER | Уровень питомца, v1 хранится без геймплейной логики. |
| `experience` | INTEGER | Опыт питомца. |
| `ascension_level` | INTEGER | Уровень восхождения питомца. |
| `size` | REAL | Размер питомца. |
| `weight` | REAL | Вес питомца. |
| `mood` | INTEGER | Настроение питомца. |
| `hunger` | INTEGER | Голод питомца. |
| `hygiene` | INTEGER | Гигиена питомца. |
| `energy` | INTEGER | Энергия питомца. |
| `created_at` | TEXT | Дата-время создания записи. |
| `updated_at` | TEXT | Дата-время последнего изменения. |
| `hatched_at` | TEXT | Дата-время вылупления питомца, `NULL` пока питомец в яйце. |

### `ai_tasks`

Очередь AI-задач для локального worker. Для обычных статистических запросов пользователей обычно не использовать.

Ключ: `id`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | ID AI-задачи. |
| `task_type` | TEXT | Тип задачи: `response` для ответа в чат, `text_to_sql` для запросов к БД, `data_analysis_sql`/`data_analysis_response` для аналитики по SQL-контексту, `profile_update` для AI-профиля или `chat_summary` для саммари. |
| `status` | TEXT | Статус: `pending`, `processing`, `done`, `failed`. |
| `priority` | INTEGER | Приоритет выбора задачи; большее значение важнее. |
| `model` | TEXT | Модель, которую worker должен вызвать в Ollama. |
| `prompt` | TEXT | Полный prompt, который worker передает в LLM. |
| `payload_json` | TEXT | JSON с исходными параметрами задачи. |
| `result_text` | TEXT | Итоговый текст результата; для `response` и `data_analysis_response` хранится текст ответа, для `text_to_sql` и `data_analysis_sql` - SQL, для `profile_update` - JSON, для `chat_summary` - саммари. |
| `error_text` | TEXT | Последняя ошибка обработки задачи. |
| `chat_id` | INTEGER | Чат, из которого создана задача. |
| `user_id` | INTEGER | Пользователь, создавший задачу. |
| `request_message_id` | INTEGER | ID исходного сообщения Telegram с запросом; для фоновых задач может быть `0`. |
| `response_message_id` | INTEGER | ID сообщения Telegram с ответом бота; у фоновых profile-задач обычно `NULL`. |
| `attempt` | INTEGER | Номер попытки обработки, начиная с `0`. |
| `lease_until` | TEXT | Время, до которого задача закреплена за worker. |
| `created_at` | TEXT | Дата-время создания задачи. |
| `updated_at` | TEXT | Дата-время последнего обновления задачи. |
| `finished_at` | TEXT | Дата-время завершения задачи. |

### `ai_summary`

Короткие AI-саммари сообщений чата за период между успешными сжатиями. Таблица доступна для `/db`, но запросы должны фильтровать `chat_id`.

Ключ: `id`. Уникальность: `UNIQUE(chat_id, window_start, window_end)`. Связь: `task_id -> ai_tasks.id`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | ID записи саммари. |
| `chat_id` | INTEGER | Чат, для которого собрано саммари. |
| `task_id` | INTEGER | ID задачи `chat_summary` в `ai_tasks`. |
| `status` | TEXT | Статус обработки: `pending`, `done`, `failed`. |
| `summary_text` | TEXT | Короткое саммари переписки за окно, до 150 символов. |
| `message_count` | INTEGER | Количество сообщений, попавших в prompt после фильтра длины. |
| `window_start` | TEXT | Начало окна сообщений, обычно ISO datetime. |
| `window_end` | TEXT | Конец окна сообщений, обычно ISO datetime. |
| `model` | TEXT | LLM-модель, которая строила саммари. |
| `error_text` | TEXT | Последняя ошибка обработки, если задача упала или ушла на retry. |
| `created_at` | TEXT | Дата-время создания placeholder саммари. |
| `updated_at` | TEXT | Дата-время последнего обновления записи. |
| `finished_at` | TEXT | Дата-время успешного или неуспешного завершения обработки. |

### `users`

Пользователи в разрезе чатов. Базовая таблица для имен, баланса и профиля.

Ключ: `PRIMARY KEY (user_id, chat_id)`.

| Поле | Тип | Описание |
|---|---:|---|
| `user_id` | INTEGER | Telegram ID пользователя. |
| `chat_id` | INTEGER | Telegram ID чата, где известен пользователь. |
| `name` | TEXT | Отображаемое имя пользователя из Telegram. |
| `sits` | REAL | Текущий баланс сит пользователя в этом чате. |
| `punished` | INTEGER | Флаг наказания пользователя. |
| `sex` | TEXT | Пол пользователя: обычно `m`, `f` или `NULL`. |
| `nick` | TEXT | Telegram username, обычно с `@`; может быть пустым. |
| `is_all` | INTEGER | Служебный флаг участия/особого статуса пользователя. |
| `subscription_till` | TEXT | Дата окончания активной подписки `YYYY-MM-DD`; пустая строка, если подписки нет. |
| `cepen` | REAL | Длина цепня в сантиметрах; `0` означает отсутствие заражения. |
| `cepen_growth_date` | TEXT | Дата последней попытки роста или анабиоза `YYYY-MM-DD`. |
| `cepen_name` | TEXT | Пользовательское имя цепня; `NULL` или пустая строка означает отсутствие имени. |
| `cepen_profession` | TEXT | Ключ выбранной профессии/скина цепня; `NULL` означает, что бесплатный первый выбор ещё не сделан. |

### `cepen_event_checks`

Обработанные групповые события и дейлики для однократной проверки передачи цепня.

Ключ: `PRIMARY KEY (event_kind, event_id, chat_id)`.

| Поле | Тип | Описание |
|---|---:|---|
| `event_kind` | TEXT | `group` или `daily`. |
| `event_id` | TEXT | Идентификатор события. |
| `chat_id` | INTEGER | Чат события. |
| `checked_at` | TEXT | Время проверки заражения. |

### `cepen_daily_messages`

Архив прежних ежедневных реплик. Новые задания здесь не создаются; последняя `sent_at` используется для перехода на расписание `cepen_letters`. Время хранится в локальном времени сервера.

Ключ: `PRIMARY KEY (message_date, chat_id, user_id)`.

| Поле | Тип | Описание |
|---|---:|---|
| `message_date` | TEXT | Серверная дата задания `YYYY-MM-DD`. |
| `chat_id` | INTEGER | Чат хоста. |
| `user_id` | INTEGER | Пользователь с цепнем. |
| `scheduled_at` | TEXT | Случайное время отправки `YYYY-MM-DD HH:MM:SS`. |
| `phrase` | TEXT | Зафиксированный шаблон реплики с `{nickname}`. |
| `sent_at` | TEXT | Время успешной отправки или `NULL`. |

### `cepen_letters`

Повторяемая миграция в `cepen_messages.ensure_schema`, вызывается при инициализации БД. Одна текущая реплика на владельца в чате: `PRIMARY KEY(chat_id,user_id)`; оба INTEGER NOT NULL. Новые владельцы начинают отсчёт от запуска, существующие — от последней отправленной архивной реплики.

| Поле | Тип | Описание |
|---|---:|---|
| `due_at` | TEXT NOT NULL | ISO-время с часовым поясом Екатеринбурга, ближайшая попытка; у `sending` — начало следующего цикла. |
| `state` | TEXT NOT NULL DEFAULT 'pending' | `pending`: нужна генерация; `ready`: сохранён текст; `sending`: отправка началась, повтор запрещён до нового цикла. |
| `text`, `prompt` | TEXT | Зафиксированные ответ и контекст; NULL до генерации и после завершения цикла. |
| `gift` | REAL NOT NULL DEFAULT 0 | Подарок в ситах до банковских удержаний, 0 или 0,50–2,00. |
| `reward_applied` | INTEGER NOT NULL DEFAULT 0 | Однократное начисление через sit_ledger, атомарно с `sending`. |
| `last_sent_at` | TEXT | Последняя подтверждённая доставка, ISO с часовым поясом. |
| `error` | TEXT | Последняя ошибка/неопределённость доставки. |

Связь с users по chat_id/user_id; внешние ключи не объявлены. Источник настроения — cepen_scratches за местную дату. Подарок имеет action_code `cepen_letter_gift`; обычные налоги/взыскания применяются. При неопределённой доставке подарок сохраняется, старый текст не отправляется повторно, следующий цикл начинается через 68–74 часа с учётом окна тишины.

### `cepen_scratches`

Успешные чесания цепня другими участниками или самим владельцем. Одна строка одновременно служит основанием для награды владельцу и аудитом суточных лимитов. Совпадение owner_id и scratcher_id допустимо; общий предел пять от одного участника применяется и к владельцу.

Ключ: `PRIMARY KEY (callback_query_id)`; повторная доставка одного Telegram callback не создаёт вторую награду.

| Поле | Тип | Описание |
|---|---:|---|
| `callback_query_id` | TEXT | Уникальный ID нажатия Telegram. |
| `scratch_date` | TEXT | Серверная дата чесания `YYYY-MM-DD`. |
| `chat_id` | INTEGER | Чат цепня. |
| `owner_id` | INTEGER | Владелец цепня и получатель награды. |
| `scratcher_id` | INTEGER | Пользователь, который почесал цепня. |
| `reward` | REAL | Фактически начисленная владельцу сумма после налога и взыскания; исходная награда — `0.1` сита. |
| `created_at` | TEXT | Серверные дата и время успешного нажатия. |

### `daily_stats`

Дневная статистика активности пользователя в чате.

Ключи: `id` - технический PK, `UNIQUE(user_id, chat_id, date)`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | Технический автоинкрементный ID записи. |
| `user_id` | INTEGER | Пользователь, к которому относится дневная статистика. |
| `chat_id` | INTEGER | Чат, в котором набрана статистика. |
| `date` | TEXT | День статистики в формате `YYYY-MM-DD`. |
| `messages` | INTEGER | Количество обычных текстовых/медийных сообщений. |
| `words` | INTEGER | Количество слов в сообщениях. |
| `chars` | INTEGER | Количество символов в сообщениях. |
| `stickers` | INTEGER | Количество отправленных стикеров. |
| `coffee` | INTEGER | Счетчик выпитого за день кофе |
| `react_given` | INTEGER | Количество реакций, поставленных пользователем другим сообщениям. |
| `react_taken` | INTEGER | Количество реакций, полученных на сообщения пользователя. |
| `rounds` | INTEGER | Количество отправленных видеокружков. |
| `bites_given` | INTEGER | Количество укусов, совершенных пользователем. |
| `bites_received` | INTEGER | Количество укусов, полученных пользователем. |
| `profanity_count` | INTEGER | Количество найденных матерных слов в сообщениях. |

### `total_stats`

Накопительная статистика пользователя за всё время в чате.

Ключ: `PRIMARY KEY (user_id, chat_id)`.

| Поле | Тип | Описание |
|---|---:|---|
| `user_id` | INTEGER | Пользователь, к которому относится суммарная статистика. |
| `chat_id` | INTEGER | Чат, в котором набрана статистика. |
| `messages` | INTEGER | Всего обычных сообщений. |
| `words` | INTEGER | Всего слов. |
| `chars` | INTEGER | Всего символов. |
| `stickers` | INTEGER | Всего отправленных стикеров. |
| `coffee` | INTEGER | Всего выпитого кофе. |
| `react_given` | INTEGER | Всего реакций, поставленных пользователем. |
| `react_taken` | INTEGER | Всего реакций, полученных пользователем. |
| `rounds` | INTEGER | Всего видеокружков. |
| `bites_received` | INTEGER | Всего полученных укусов. |
| `bites_given` | INTEGER | Всего совершенных укусов. |
| `profanity_count` | INTEGER | Всего матерных слов. |

### `messages_reactions`

Журнал сообщений, по которым бот отслеживает текст и количество реакций.

Ключ: `PRIMARY KEY (chat_id, message_id)`.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | Чат, где было сообщение. |
| `message_id` | INTEGER | ID сообщения Telegram внутри чата. |
| `user_id` | INTEGER | Автор сообщения. |
| `message_text` | TEXT | Текст или подпись сообщения, сохраненные ботом. |
| `reactions_count` | INTEGER | Текущее суммарное количество реакций на сообщение. |
| `date` | TEXT | Дата-время сохранения сообщения, обычно ISO timestamp. |

Note: for Telegram media messages, `message_text` stores message text or caption.

Успешно доставленные ИИ-ответы типа response также сохраняются здесь под реальным Telegram user_id бота, без начисления игровой статистики. Роль assistant определяется связью ai_tasks.response_message_id + chat_id; команды и служебные уведомления не сохраняются этим путём. Новых колонок для роли не вводится.

### `web_chat_attachments`

Local web-chat attachments saved by the bot for protected rendering in the web UI.
Rows and local files are automatically cleaned after 7 days.

Key: `id`; unique message attachment slot: `(chat_id, message_id, attachment_index)`.

| Field | Type | Description |
|---|---:|---|
| `id` | INTEGER | Attachment ID used by `/api/chat/media/{id}`. |
| `chat_id` | INTEGER | Telegram chat ID. |
| `message_id` | INTEGER | Telegram message ID inside the chat. |
| `attachment_index` | INTEGER | Attachment order within the message; v1 uses `0`. |
| `media_type` | TEXT | Attachment type; v1 supports `photo`. |
| `telegram_file_id` | TEXT | Telegram file_id used for downloading. |
| `telegram_file_unique_id` | TEXT | Telegram stable file unique ID, when available. |
| `local_path` | TEXT | Local file path under `web_chat_media/`. |
| `mime_type` | TEXT | MIME type used when serving the file. |
| `width` | INTEGER | Image width from Telegram metadata. |
| `height` | INTEGER | Image height from Telegram metadata. |
| `file_size` | INTEGER | Telegram file size, when available. |
| `created_at` | TEXT | ISO timestamp when attachment metadata was saved. |

### `sticker_stats`

Подробная статистика по конкретным стикерам.

Ключ: `PRIMARY KEY (chat_id, file_id, date)`.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | Чат, где отправляли стикер. |
| `file_id` | TEXT | Telegram `file_id` конкретного стикера. |
| `set_name` | TEXT | Имя стикерпака Telegram. |
| `date` | TEXT | День отправки `YYYY-MM-DD`. |
| `count` | INTEGER | Сколько раз этот стикер отправили в этот день в этом чате. |

### `sit_stats`

Журнал начислений сит. В коде пишутся положительные начисления; расходы не всегда логируются здесь.

Ключ: `id`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | Технический ID записи. |
| `date` | TEXT | Дата операции `YYYY-MM-DD`. |
| `time` | TEXT | Время операции `HH:MM:SS`. |
| `chat_id` | INTEGER | Чат операции. |
| `user_id` | INTEGER | Пользователь, которому начислены ситы. |
| `name` | TEXT | Имя пользователя на момент операции. |
| `amount` | REAL | Размер начисления в ситах; может быть дробным. |

### `achievements`

Справочник ачивок.

Ключ: `key`.

| Поле | Тип | Описание |
|---|---:|---|
| `key` | TEXT | Уникальный код ачивки. |
| `name_m` | TEXT | Название ачивки для мужского пола. |
| `name_f` | TEXT | Название ачивки для женского пола. |

Известные ключи: `biter`, `bitten`, `dobroe_serdtse`, `dushnila`, `fluder`, `kolobok`, `likesobornik`, `lubimka`, `matershinnik`, `matsturbator`, `skromnyashka`, `sticker_bomber`, `tsarsky_like`.

### `user_achievements`

Факты выдачи ачивок пользователям.

Ключ: `id`. Связь: `achievement_key -> achievements.key`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | Технический автоинкрементный ID. |
| `user_id` | INTEGER | Пользователь, получивший ачивку. |
| `chat_id` | INTEGER | Чат, в котором выдали ачивку. |
| `achievement_key` | TEXT | Код выданной ачивки из `achievements.key`. |
| `date` | TEXT | Дата выдачи ачивки. |

### `quests_catalog`

Справочник квестов, из которого пользователю предлагаются ежедневные задания.

Ключ: `quest_id`.

| Поле | Тип | Описание |
|---|---:|---|
| `quest_id` | INTEGER | ID квеста. |
| `name` | TEXT | Короткое название квеста. |
| `description` | TEXT | Текстовое описание задания. |
| `type` | TEXT | Тип события, которое двигает прогресс. |
| `target` | INTEGER | Сколько событий нужно для выполнения. |
| `reward` | INTEGER | Награда в ситах за выполнение. |

Известные `type`: `coffee_fail`, `coffee_safe`, `group_part`, `group_win`, `likes_given`, `likes_received`, `messages_sent`, `round`, `stickers_sent`.

### `user_quests`

Выбранные пользователями ежедневные квесты и их прогресс.

Ключ: `PRIMARY KEY (user_id, chat_id, date_taken)`. Связь: `quest_id -> quests_catalog.quest_id`.

| Поле | Тип | Описание |
|---|---:|---|
| `user_id` | INTEGER | Пользователь, взявший квест. |
| `chat_id` | INTEGER | Чат, где взят квест. |
| `quest_id` | INTEGER | ID квеста из `quests_catalog`. |
| `date_taken` | TEXT | Дата взятия квеста `YYYY-MM-DD`. |
| `status` | TEXT | Статус: `active`, `completed` или `failed`. |
| `progress` | INTEGER | Текущий прогресс по квесту. |
| `date_completed` | TEXT | Дата выполнения квеста; `NULL`, если не выполнен. |

### `settings`

Настройки бота на уровне чата.

Ключ: `PRIMARY KEY (chat_id, name)`.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | Чат, к которому относится настройка. |
| `name` | TEXT | Код настройки. |
| `value` | INTEGER | Значение настройки: обычно флаг `0/1`, но для некоторых настроек может быть числом. |

Известные `name`: `daily_reminders`, `enable_geyser`, `enable_cepen`, `forbid_mujlo`, `group_masturbation`, `ai_response_chance_percent`. Отсутствие `enable_cepen` означает, что механика включена.

### `daily_events`

Запланированные "дейлики"/события чата.

Ключ: `id`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | ID дейлика. |
| `chat_id` | INTEGER | Чат, где создан дейлик. |
| `creator_user_id` | INTEGER | Пользователь-создатель дейлика. |
| `name` | TEXT | Название дейлика. |
| `description` | TEXT | Описание дейлика. |
| `date` | TEXT | Дата события `YYYY-MM-DD`. |
| `time` | TEXT | Время события `HH:MM`. |
| `cars` | TEXT | Нужны ли машины, чтобы добраться, обычно `да`/`нет`. |
| `link` | TEXT | Ссылка на событие/созвон/место. |
| `reminded` | INTEGER | Было ли отправлено напоминание за сутки. |
| `calendar_event_id` | TEXT | ID события в Google Calendar. |

### `daily_participants`

Участники дейликов.

Ключ: `id`. Связь: `daily_id -> daily_events.id`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | Технический ID участия. |
| `daily_id` | INTEGER | ID дейлика из `daily_events`. |
| `user_id` | INTEGER | Пользователь-участник. |
| `is_driver` | INTEGER | Флаг водителя среди участников. |

### `geyser_events`

Планировщик и состояние чатовых "гейзеров" с ситами.

Ключи: `id`, `UNIQUE(chat_id, date, scheduled_time)`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | ID запланированного гейзера. |
| `chat_id` | INTEGER | Чат, где должен появиться гейзер. |
| `date` | TEXT | Дата появления `YYYY-MM-DD`. |
| `scheduled_time` | TEXT | Запланированное время появления `HH:MM`. |
| `status` | TEXT | Статус: `pending`, `sent`, `caught`, `expired`. |
| `message_id` | INTEGER | ID сообщения бота с гейзером. |
| `caught_by` | INTEGER | `user_id` пользователя, поймавшего гейзер. |

Примечание: старый код упоминает поле `count`, но в текущей продовой схеме его нет.

### `web_geyser_daily_catches`

Дневные лимиты/счетчики ловли веб-гейзера пользователями.

Ключ: `PRIMARY KEY (user_id, chat_id, catch_date)`.

| Поле | Тип | Описание |
|---|---:|---|
| `user_id` | INTEGER | Пользователь, ловивший веб-гейзер. |
| `chat_id` | INTEGER | Чат, в котором учитывается ловля. |
| `catch_date` | TEXT | День учета `YYYY-MM-DD`. |
| `amount` | INTEGER | Количество веб-поимок за день. |
| `updated_at` | TEXT | Когда счетчик обновлялся последний раз. |

### `mujlo`

Состояние ночного ограничения "тише, мужло, пора спать" по пользователям.

Ключ: `PRIMARY KEY (chat_id, user_id)`.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | Чат, где действует состояние. |
| `user_id` | INTEGER | Пользователь. |
| `mujlo_freed` | INTEGER | Купил ли пользователь право говорить до сброса; `1` = освобожден. |

### `sosalsa_stats`

Парная статистика взаимодействий "сосаться"/"шпехаться".

Ключ: `PRIMARY KEY (chat_id, user_id1, user_id2)`. В паре ID отсортированы по возрастанию.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | Чат, где была пара. |
| `user_id1` | INTEGER | Первый пользователь пары, меньший ID. |
| `user_id2` | INTEGER | Второй пользователь пары, больший ID. |
| `sosalsa_count` | INTEGER | Количество взаимодействий типа "сосаться" у пары. |
| `shpehalsa_count` | INTEGER | Количество взаимодействий типа "шпехаться" у пары. |

### `body_parts`

Справочник частей тела для механики укусов.

Ключ: `id`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | ID части тела. |
| `name_nom` | TEXT | Название в именительном падеже: "что?". |
| `name_acc` | TEXT | Название в винительном падеже: "укусил за что?". |
| `name_gen` | TEXT | Название в родительном падеже: "лишился чего?". |

Текущие части: `Жопа`, `Нипель`, `Щека`, `Носик`, `Пятка`, `Мизинчик на левой ноге`, `Второй нипель`.

### `user_body_parts`

Состояние частей тела пользователя в механике укусов.

Ключи: `id`, `UNIQUE(user_id, chat_id, body_part_id)`. Связь: `body_part_id -> body_parts.id`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | Технический ID записи. |
| `user_id` | INTEGER | Пользователь. |
| `chat_id` | INTEGER | Чат. |
| `body_part_id` | INTEGER | Часть тела из `body_parts`. |
| `state` | INTEGER | Состояние части: `1` = на месте, `0` = откушено. |

### `dicks`

Игровая статистика длины в механике `/dick`.

Ключ: `PRIMARY KEY (user_id, chat_id)`.

| Поле | Тип | Описание |
|---|---:|---|
| `user_id` | INTEGER | Пользователь. |
| `chat_id` | INTEGER | Чат. |
| `length` | INTEGER | Текущая длина в сантиметрах/игровых единицах. |
| `grow_date` | TEXT | Дата последнего роста/изменения, чтобы ограничивать раз в день. |
| `buff` | TEXT | Активный бафф/модификатор. |
| `buff_exp` | TEXT | Срок действия баффа. |
| `top1_entrance_date` | TEXT | Дата, когда пользователь стал топ-1 по длине в чате. |

### `masturbate_log`

История групповой мини-игры мастурбации.

Ключ: `id`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | Технический ID записи. |
| `created_at` | TEXT | Дата-время игры. |
| `user_id` | INTEGER | Участник игры. |
| `chat_id` | INTEGER | Чат игры. |
| `is_winner` | INTEGER | Флаг победителя игры. |
| `reward_sits` | INTEGER | Сколько сит получил победитель; у остальных обычно `0`. |

### `idle_building_levels`

Справочник уровней idle-зданий веб-игры.

Ключ: `PRIMARY KEY (building_code, level)`.

| Поле | Тип | Описание |
|---|---:|---|
| `building_code` | TEXT | Код здания. |
| `building_name` | TEXT | Название здания. |
| `image_file` | TEXT | Файл изображения здания. |
| `level` | INTEGER | Уровень здания от 1 до 20. |
| `upgrade_cost_sits` | REAL | Цена покупки/апгрейда до этого уровня в ситах. |
| `income_microsits_per_hour` | INTEGER | Почасовой доход уровня в микроситах. |
| `order` | INTEGER | Порядок открытия/показа здания. |

Коды зданий: `sitopilka`, `kolodec_sita`, `sitoferma`, `masitskaya`, `sitvolny_zavod`.

### `idle_player_buildings`

Купленные idle-здания игроков и накопленный доход.

Ключи: `id`, `UNIQUE(user_id, chat_id, building_code)`. Связь: `(building_code, current_level) -> idle_building_levels(building_code, level)`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | Технический ID владения зданием. |
| `user_id` | INTEGER | Владелец здания. |
| `chat_id` | INTEGER | Чат, в котором куплено здание. |
| `building_code` | TEXT | Код здания из `idle_building_levels`. |
| `current_level` | INTEGER | Текущий уровень здания игрока. |
| `lifetime_earned_microsits` | INTEGER | Сколько микросит здание заработало за всё время. |
| `created_at` | TEXT | Когда запись владения создана. |
| `updated_at` | TEXT | Когда запись владения обновлялась. |

### `idle_hourly_income_ticks`

Служебная таблица учета обработанных часов idle-дохода.

Ключ: `hour_key`.

| Поле | Тип | Описание |
|---|---:|---|
| `hour_key` | TEXT | Ключ часа, за который уже начислен idle-доход. |
| `processed_at` | TEXT | Когда этот час был обработан. |

### `web_settings`

Пользовательские настройки веб-интерфейса в разрезе чатов.

Ключ: `PRIMARY KEY (user_id, chat_id)`.

| Поле | Тип | Описание |
|---|---:|---|
| `user_id` | INTEGER | Пользователь веб-интерфейса. |
| `chat_id` | INTEGER | Выбранный чат. |
| `hide_base` | INTEGER | Скрывать базу/idle-постройки от других игроков. |
| `reject_geyser_catch_by_guest` | INTEGER | Запретить гостям ловить гейзер у пользователя. |
| `updated_at` | TEXT | Когда настройки обновлялись. |
| `notify_group_masturbation` | INTEGER | Включены ли групповые уведомления по мини-игре мастурбации. |
| `notify_group_masturbation_sound` | INTEGER | Включен ли звук для уведомлений мини-игры мастурбации. |

### `web_auth_codes`

Одноразовые коды авторизации в веб-интерфейсе.

Ключ: `id`. Для статистических запросов обычно не нужна.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | Технический ID кода. |
| `user_id` | INTEGER | Пользователь, для которого выпущен код. |
| `code` | TEXT | 4-значный код авторизации. |
| `issued_bucket` | TEXT | Часовой бакет выпуска кода. |
| `attempt` | INTEGER | Номер попытки генерации кода внутри бакета. |
| `expires_at` | INTEGER | Unix timestamp истечения кода. |
| `created_at` | INTEGER | Unix timestamp создания кода. |
| `used_at` | INTEGER | Unix timestamp использования; `NULL`, если код активен/не использован. |

### `web_chat_titles`

Кеш названий чатов для веб-интерфейса.

Ключ: `chat_id`.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | Чат. |
| `title` | TEXT | Последнее известное название чата. |
| `updated_at` | INTEGER | Unix timestamp обновления названия. |

### `new_year_greetings`

Справочник новогодних поздравлений и подарков.

Ключ: `id`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | ID поздравления. |
| `text_m` | TEXT | Текст поздравления для мужского пола. |
| `text_f` | TEXT | Текст поздравления для женского пола. |
| `gift_name` | TEXT | Название подарка. |
| `gift_sits` | INTEGER | Сколько сит дает подарок; может быть отрицательным. |

### `new_year_runs`

Служебная таблица, чтобы не запускать новогоднюю рассылку повторно.

Ключ: `chat_id`.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | Чат, где рассылка уже выполнялась. |
| `executed_at` | TEXT | Дата-время выполнения рассылки. |

### `summary_publish_log`

Журнал публикации ежедневных саммари чатов.

Ключ: `PRIMARY KEY (chat_id, date_key)`.

| Поле | Тип | Описание |
|---|---:|---|
| `chat_id` | INTEGER | Чат, для которого опубликовано саммари. |
| `date_key` | TEXT | День/ключ саммари. |
| `published_at` | TEXT | Дата-время публикации. |
| `summary_file` | TEXT | Путь или имя файла с саммари. |
| `message_id` | INTEGER | ID сообщения с опубликованным саммари. |

### `sqlite_sequence`

Внутренняя служебная таблица SQLite для автоинкрементов. Для пользовательских text2sql-запросов обычно не использовать.

| Поле | Тип | Описание |
|---|---:|---|
| `name` | TEXT | Имя таблицы с автоинкрементным ключом. |
| `seq` | INTEGER | Последнее выданное значение автоинкремента. |

## Быстрый выбор таблицы под запрос

### Служебные очереди личных фотоисторий

В существующую `ai_workers` добавлено `task_types_json TEXT NOT NULL DEFAULT '[]'`: пустой список — обычный worker (без фотоисторий); `["photo_story","photo_story_merge"]` — выделенный внешний обработчик фото. Старые регистрации и heartbeat совместимы; миграция повторяема. Долгие вызовы vision не удерживают разговорный worker. Поле task_types списка опционально в `/api/ai/workers/heartbeat`.

`ai_photo_story_batches`: `id INTEGER PRIMARY KEY`; `chat_id`, `user_id`, `first_message_id` INTEGER NOT NULL; `group_key TEXT NOT NULL` (media_group_id либо single:message_id); `touched_at REAL NOT NULL` (Unix секунды последнего кадра); `status TEXT NOT NULL DEFAULT 'collecting'` (collecting, queued, done, failed, cancelled); `vision_task_id`, `merge_task_id`, `notice_message_id` INTEGER nullable (связи с ai_tasks и Telegram); `created_at TEXT NOT NULL` UTC ISO. Индекс `(status,touched_at)`. До трёх незавершённых подборок на пользователя; запущенная задача не изменяется новыми кадрами.

`ai_photo_story_inputs`: `chat_id`, `message_id`, `batch_id` INTEGER NOT NULL; `file_id TEXT NOT NULL` (ID Telegram, не байты); `caption TEXT NOT NULL DEFAULT ''`. PRIMARY KEY `(chat_id,message_id)`; индекс `(batch_id,message_id)`. Связь batch_id → ai_photo_story_batches.id контролируется приложением, каскадных удалений нет. Таблицы создаются повторяемо. Изображения обрабатываются в памяти и не хранятся на диске. ai_tasks типов photo_story/photo_story_merge сохраняют анализ, batch_id и финальную историю. Это служебные данные, не источник игровой статистики.

- "Кто больше всех писал/флудил/матерился/ставил реакции/получал реакции/кусал за период" - `daily_stats` + `users`.
- "За всё время" по тем же метрикам - `total_stats` + `users`.
- "Топ сообщений по реакциям" - `messages_reactions` + `users`, сортировать по `reactions_count`.
- "Стикеры/стикерпак за день" - `sticker_stats`.
- "Баланс сит" - `users.sits`.
- "Начисления и списания сит, налог и взыскание с дохода" - `sit_ledger`; `sit_stats` — старый неполный журнал.
- "Ликвидность, капитал и текущие ставки банка" - `bank_accounts`; свободные остатки и резервы рассчитываются по договорам и требованиям в `bank_core.bank_metrics`.
- "Вклады и их сроки" - `bank_deposits` + `users`; только активные: `status='active'`.
- "Невыплаченные вклады и кризисные проценты" - `bank_deposit_claims` + `bank_deposits`; текущие требования: `status='open'`.
- "Кредиты, платежи и просрочки" - `bank_loans` + `bank_loan_payments` + `users`.
- "Кредитный рейтинг, дефолтный долг и учитываемый доход" - `bank_credit_profiles`, `bank_daily_income`.
- "История операций банка, налоговые поступления" - `bank_ledger`; детали удержаний в `metadata_json`.
- "Отчёты за банковские дни" - `bank_daily_runs`; историю изменения ставок смотреть в `bank_rate_changes`.
- "Ачивки" - `user_achievements` + `achievements` + `users`.
- "Квесты" - `user_quests` + `quests_catalog` + `users`.
- "Дейлики/мероприятия" - `daily_events`, участники через `daily_participants` + `users`.
- "Гейзеры" - `geyser_events`; веб-поимки по дням - `web_geyser_daily_catches`.
- "Idle-постройки" - `idle_player_buildings` + `idle_building_levels` + `users`.
- "Сосаться/шпехаться" - `sosalsa_stats` + два JOIN к `users`.
- "Укусы и части тела" - счетчики в `daily_stats`/`total_stats`, состояния в `user_body_parts` + `body_parts`.

## Генерация изображений (служебные таблицы)

- `ai_imagegen_batches`: id INTEGER PRIMARY KEY; chat_id, user_id INTEGER NOT NULL; group_key TEXT NOT NULL; first_message_id INTEGER NOT NULL (минимальный Telegram message_id); private INTEGER NOT NULL (0/1); addressed INTEGER NOT NULL DEFAULT 0; touched_at и created_at REAL NOT NULL (Unix-секунды); status TEXT NOT NULL DEFAULT collecting: collecting/confirm/ready/typed/done/cancelled. UNIQUE(chat_id,group_key), индекс idx_imagegen_batches(status,touched_at). confirm — ожидание автора, ready — подтверждены первые 4, typed — передано типизатору.
- `ai_imagegen_inputs`: chat_id, message_id INTEGER NOT NULL, составной PRIMARY KEY; batch_id INTEGER NOT NULL (логическая связь batches.id без FK); file_id TEXT NOT NULL (Telegram); caption TEXT NOT NULL. Индекс idx_imagegen_inputs(batch_id,message_id). Порядок — message_id.
- `ai_imagegen_jobs`: task_id INTEGER PRIMARY KEY (логическая связь ai_tasks.id без FK), state TEXT NOT NULL DEFAULT preparing: preparing/generating/result_ready/delivering/done/failed; prepared_prompt TEXT NULL, image BLOB NULL (временный результат), width/height INTEGER NULL (пиксели); updated_at REAL NOT NULL (Unix-секунды); notice_sent INTEGER NOT NULL DEFAULT 0. Результат удаляется после доставки/ошибки; неизвестный исход generating/delivering не повторяется после рестарта.
- `ai_imagegen_usage`: id INTEGER PRIMARY KEY; day TEXT NOT NULL (дата UTC YYYY-MM-DD, сброс 05:00 Екатеринбурга); task_id INTEGER NOT NULL; amount REAL NOT NULL (Neurons, резерв или фактический расход); state TEXT NOT NULL: reserved/reported/unknown; created_at REAL NOT NULL (Unix-секунды), http_status INTEGER NULL. Индекс idx_imagegen_usage_day(day). Сумма включает неизвестный расход и резервы.
- `ai_imagegen_days`: day TEXT PRIMARY KEY; blocked INTEGER NOT NULL DEFAULT 0 — подтверждённое исчерпание внешней суточной квоты Cloudflare; не блокирует HF. Новая дата не наследует блокировку.
- `ai_tasks.task_type` дополнен imagegen. Существующие prompt/payload_json хранят исходный запрос и упорядоченные ссылки на фото; provider huggingface (Qwen-Image-2.1) или cloudflare (FLUX.2 Klein 4B), model — фактический генератор; response_message_id — доставленная фотография. Журнал ai_attempt_log.usage_json для Cloudflare содержит neurons/accounting/http_status, для HF — quota_before/quota_after и gpu_seconds_account_delta (разница общего расхода аккаунта внутри одного окна; параллельные вызовы других клиентов тоже могут влиять). HF-квота внешняя, не записывается в ai_imagegen_usage/days; новые таблицы не требуются. ai_model_calls — безопасный промпт/размеры/порядок без байтов фото и секретов. Новые таблицы исключены из пользовательского SQL. Создание повторяемо, старые данные не меняются.

`ai_imagegen_batches.notice_message_id INTEGER NULL` — Telegram ID сообщения с кнопками, позволяет продолжить выдачу подтверждения после временного отказа. Колонка добавляется повторяемой миграцией; отсутствие значения означает, что подтверждение ещё не было успешно отправлено.

`cepen_ai_avatars`: chat_id, user_id составной ключ; level и skin — текущий ключ кеша; path — путь локального серверного файла; enabled — 0/1; generated_at — UTC-время последнего результата. `cepen_ai_avatar_usage`: chat_id, user_id, day (местная дата Asia/Yekaterinburg) составной ключ и count — число принятых платных перегенераций за день (не автоматических). Платные перегенерации списывают 5 сит через общий ledger и используют только Cloudflare FLUX; таблицы не видны пользовательскому SQL.

## Истории фотоальбомов дейликов
`daily_photos.captured_at REAL NULL` — Unix-секунды съёмки EXIF. NULL означает отсутствие надёжного тега; порядок отправки используется как запасной, не как время съёмки. Сохраняется до конвертации; миграция повторяема.
`daily_photo_story_state(key TEXT PRIMARY KEY,value REAL NOT NULL)` — activated_at (Unix-секунды первого включения), сохраняется между перезапусками.
`daily_photo_stories`: daily_id INTEGER PRIMARY KEY (daily_events), chat_id INTEGER NOT NULL, due_at REAL NOT NULL (Unix-срок +24ч), status TEXT NOT NULL DEFAULT waiting_photos (waiting_photos/queued/done/failed/cancelled), notify_chat INTEGER DEFAULT 0; batch_id INTEGER (ai_photo_story_batches), photos_json TEXT DEFAULT [], analyses_json TEXT DEFAULT [], next_offset INTEGER DEFAULT 0; story_text TEXT nullable, response_message_id INTEGER nullable; created_at TEXT NOT NULL, finished_at TEXT nullable (UTC ISO), delivery_state TEXT DEFAULT silent (silent/pending/sending/sent/unknown). Индекс status/due_at. JSON хранит снимок ID, порядка и анализа, не изображения. Ссылки проверяются приложением; служебные таблицы запрещены пользовательскому SQL. Одна история на встречу, новые фото её не меняют. Итог сохраняется до Telegram, unknown не пересылается автоматически.

## Инкрементальные счётчики RAG

ai_rag_counters: scope TEXT NOT NULL (all — все сообщения, initial — начальный снимок, chunks — фрагменты), chat_id INTEGER NOT NULL (0 для chunks), reason TEXT NOT NULL (причина исключения или состояние фрагмента), eligible INTEGER NOT NULL, indexed INTEGER NOT NULL, n INTEGER NOT NULL CHECK(n>=0) — количество записей. PRIMARY KEY(scope,chat_id,reason,eligible,indexed). Триггеры rag_count_messages_insert/delete/update на ai_rag_message_status и rag_count_chunks_insert/delete/update на ai_rag_chunks поддерживают гистограмму в общей транзакции. Повтор миграции не пересчитывает счётчики; counters_v1 в ai_rag_state отмечает миграцию, stats_rebuilt_at — UTC сверки. Нулевые корзины не отображаются. Полная сверка раз в неделю ночью.

Индекс idx_rag_events_message на ai_rag_events(chat_id,message_id,id) ускоряет проверку событий за границей снимка. Ключи ai_rag_state: night_open (0/1), night_event_cutoff (максимальный id событий снимка), night_finished_day (местная дата завершения). Незавершённая граница сохраняется между ночами.

Индексы очередей ai_tasks, ai_type_checks, ai_search_plans: idx_<table>_ready(status,retry_at,created_at) и idx_<table>_lease(status,lease_until). Проверка наличия готовой задачи читает SELECT 1 LIMIT 1; уведомления не являются источником состояния.

Очереди ai_tasks/ai_type_checks/ai_search_plans: minute_retry INTEGER NOT NULL DEFAULT 0 — использованные повторы после минутного лимита; refusal_kind TEXT NULL — daily/minute/transport/permanent. transport_attempt сохраняет число отказов и переключений источника, retry_at — UTC ближайшей попытки. ai_provider_state.reason сохраняет структурированную причину ограничения модели.

Индекс idx_rag_events_day на ai_rag_events(chat_id,day,id) ускоряет исключение дней с отложенными событиями. payload_json профильных задач содержит background (автоматические true, ручные false), саммери — background=not command_requested. Автоматические профили имеют priority=0, исторические фотоистории -1, текущие фотоистории 50; ручные профили и саммери RESPONSE_PRIORITY=200.
