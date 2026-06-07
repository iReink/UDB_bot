# Карта схемы `stats.db`

Источник: продовая SQLite-база `/root/UDB_bot/stats.db` на VPS, схема прочитана 2026-06-06.

Назначение файла: компактное описание схемы для text2sql. Описания намеренно короткие, но смысловые.

## Общие правила

- `user_id` - Telegram ID пользователя.
- `chat_id` - Telegram ID чата; групповые чаты обычно имеют отрицательный `chat_id`.
- Даты в полях `date`, `date_taken`, `date_completed`, `catch_date`, `grow_date`, `subscription_till` обычно хранятся как текст `YYYY-MM-DD`.
- Время в поле `time` обычно `HH:MM`, в `scheduled_time` - `HH:MM`, в `sit_stats.time` - `HH:MM:SS`.
- Поля-флаги обычно `INTEGER`: `0` = нет/выключено, `1` = да/включено.
- Валюта бота называется "ситы"; баланс лежит в `users.sits`, движения сит - в `sit_stats`.
- Для имен пользователей в статистике обычно JOIN: `... JOIN users u ON u.user_id = t.user_id AND u.chat_id = t.chat_id`.
- Для обычной статистики активности используйте `daily_stats` за период или `total_stats` за всё время.
- Для текстов сообщений и реакций используйте `messages_reactions`; это самая большая таблица.

## Таблицы

### `ai_tasks`

Очередь AI-задач для локального worker. Для обычных статистических запросов пользователей обычно не использовать.

Ключ: `id`.

| Поле | Тип | Описание |
|---|---:|---|
| `id` | INTEGER | ID AI-задачи. |
| `task_type` | TEXT | Тип задачи: `text_to_sql` для запросов к БД, `profile_update` для AI-профиля или `chat_summary` для короткого саммари чата. |
| `status` | TEXT | Статус: `pending`, `processing`, `done`, `failed`. |
| `priority` | INTEGER | Приоритет выбора задачи; большее значение важнее. |
| `model` | TEXT | Модель, которую worker должен вызвать в Ollama. |
| `prompt` | TEXT | Полный prompt, который worker передает в LLM. |
| `payload_json` | TEXT | JSON с исходными параметрами задачи. |
| `result_text` | TEXT | Итоговый текст результата; для `text_to_sql` хранится SQL, для `profile_update` - JSON профиля, для `chat_summary` - короткое саммари. |
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
| `value` | INTEGER | Значение настройки, обычно флаг `0/1`. |

Известные `name`: `daily_reminders`, `enable_geyser`, `forbid_mujlo`, `group_masturbation`.

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

- "Кто больше всех писал/флудил/матерился/ставил реакции/получал реакции/кусал за период" - `daily_stats` + `users`.
- "За всё время" по тем же метрикам - `total_stats` + `users`.
- "Топ сообщений по реакциям" - `messages_reactions` + `users`, сортировать по `reactions_count`.
- "Стикеры/стикерпак за день" - `sticker_stats`.
- "Баланс сит" - `users.sits`.
- "Начисления сит" - `sit_stats`.
- "Ачивки" - `user_achievements` + `achievements` + `users`.
- "Квесты" - `user_quests` + `quests_catalog` + `users`.
- "Дейлики/мероприятия" - `daily_events`, участники через `daily_participants` + `users`.
- "Гейзеры" - `geyser_events`; веб-поимки по дням - `web_geyser_daily_catches`.
- "Idle-постройки" - `idle_player_buildings` + `idle_building_levels` + `users`.
- "Сосаться/шпехаться" - `sosalsa_stats` + два JOIN к `users`.
- "Укусы и части тела" - счетчики в `daily_stats`/`total_stats`, состояния в `user_body_parts` + `body_parts`.
