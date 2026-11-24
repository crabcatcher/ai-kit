# ai-kit

Бэкенд-стартер методологии: канон, реестр правил, шаблоны документов, профиль, интервью, генератор и валидатор для серверных проектов. Стек и домен в ядро не входят — они живут в адаптере и профиле.

Статус: рабочая версия. План разработки и открытые вопросы ведутся вне репозитория; в поставку входят только контракты, шаблоны и код.

## Установка

```text
python -m pip install -e .
ai-kit --help
```

После установки команды работают из любого каталога. Альтернатива без установки — `PYTHONPATH` на каталог репозитория и `python -m ai_kit.<команда>`.

Обычный `pip install .` не поддерживается: шаблоны, канон и банк вопросов лежат вне пакета. Нужна editable-установка `pip install -e .`; иначе генератор не найдёт ресурсы.

## Запуск агента

Полный промпт: [docs/agent-prompt.md](./docs/agent-prompt.md).

Скопируй в агента, запущенного в каталоге проекта:

```text
Прочитай <путь к ai-kit>/docs/agent-prompt.md и выполни его целиком.
Стартер — тот же путь без docs/agent-prompt.md.
Проект — текущий каталог.
```

Готовый текст с уже подставленным путём:

```text
python -m ai_kit.prompt
```

Команда запускается из каталога проекта, как и остальные команды набора.

## Область применения

Стартер оптимизирован под бэкенд-разработку: API, сервисы, фоновые задания, хранилища. Ядро — канон, маршрут, ревью, интервью, генератор, проверки — не зависит от стека. Форма профиля и шаблонов адаптирована под бэкенд:

| Бэкендовое по умолчанию | Как читать в другом типе проекта |
|---|---|
| слои и направление зависимостей | модули и границы; словарь адаптируется |
| роль команды `migrate` | роль не заполняется, если миграций нет |
| владение объектами и уровни доступа | модель доступа описывается профилем |
| контракт функции «вход, выход, доступ, кто вызывает» | форма контракта зависит от типа проекта |

Документация оптимизирована под бэкенд; для другого типа проекта нужен доменный профиль, иначе шаблоны станут водой. Доменный профиль добавит свои вопросы, правила и фрагменты, а бэкендовые поля уедут в необязательную часть.

## Документы

| Документ | Что описывает |
|---|---|
| [canon/canon.md](./canon/canon.md) | 16 аксиом: формулировка, метка исполнения, кто ловит |
| [canon/why.md](./canon/why.md) | почему каждая аксиома такая и что ломается без неё |
| [canon/antipatterns.md](./canon/antipatterns.md) | симптомы нарушения канона |
| [canon/registry.md](./canon/registry.md) | реестр K и правила добавления строк A и P |
| [docs/adapter-contract.md](./docs/adapter-contract.md) | контракт адаптера, модель встроенных и внешних гейтов, контракт заглушки |
| [docs/profile.md](./docs/profile.md) | поля профиля, провенанс фактов, правило версий, ошибки валидатора |
| [docs/templates.md](./docs/templates.md) | синтаксис шаблонов, ссылки и команды, маркеры, удаление пустых разделов |
| [docs/interview.md](./docs/interview.md) | банк вопросов и дедупликация, пробы и доказательства, ответы, неизвестное, отчёт |
| [docs/question-style.md](./docs/question-style.md) | как задавать вопросы человеку: формула, примеры, чек-лист |
| [docs/generator.md](./docs/generator.md) | сборка профиля, рендер, фрагменты адаптера и генератора, правила K/A/P |
| [docs/doctor.md](./docs/doctor.md) | встроенные проверки, внешние гейты, отчёт и конформанс-корпус |
| [docs/agent-prompt.md](./docs/agent-prompt.md) | промпт для агента, запущенного из проекта: инициализация и дозаполнение |
| [interview/agent-brief.md](./interview/agent-brief.md) | брифинг для агента, который поднимает набор: порядок, доказательства, запреты |
| [interview/questions.toml](./interview/questions.toml) | банк 12 универсальных вопросов |
| [ai_kit/interview.py](./ai_kit/interview.py) | банк и дедупликация, пробы, ответы, неизвестное, отчёт, JSON-план |
| [ai_kit/ask.py](./ai_kit/ask.py) | консольный опрос по слотам и подтверждение записи |
| [ai_kit/init.py](./ai_kit/init.py) | генератор: профиль, документы, фрагменты, правила |
| [ai_kit/rules.py](./ai_kit/rules.py) | строки реестра, метки, загрузка канона |
| [ai_kit/baseline.py](./ai_kit/baseline.py) | принятые нарушения правил: формат и проверки |
| [ai_kit/precommit.py](./ai_kit/precommit.py) | скрипт и хук проверки перед коммитом |
| [ai_kit/doctor.py](./ai_kit/doctor.py) | встроенные проверки и запуск гейтов |
| [ai_kit/gates.py](./ai_kit/gates.py) | доступность бинаря, версии, коды возврата |
| [ai_kit/diagnostics.py](./ai_kit/diagnostics.py) | форма диагноза, отчёт, коды выхода |
| [ai_kit/toml_writer.py](./ai_kit/toml_writer.py) | запись TOML стандартной библиотекой |
| [ai_kit/toml_io.py](./ai_kit/toml_io.py) | чтение TOML: BOM, путь в ошибке |
| [ai_kit/cli.py](./ai_kit/cli.py) | единая команда `ai-kit` и диспетчер |
| [ai_kit/args.py](./ai_kit/args.py) | строгая проверка флагов и `--opt=value` |
| [ai_kit/paths.py](./ai_kit/paths.py) | ресурсы и сообщение про editable-установку |
| [tests/test_cli.py](./tests/test_cli.py) | неизвестные флаги и `--help` |
| [tests/test_reliability.py](./tests/test_reliability.py) | атомарная запись, baseline, JSON-ошибки |
| [tests/test_paths.py](./tests/test_paths.py) | отсутствие ресурсов установленного пакета |
| [tests/test_toml_io.py](./tests/test_toml_io.py) | BOM и невалидный UTF-8 |
| [tests/test_canon_sync.py](./tests/test_canon_sync.py) | синхронизация канона и реестра |
| [tests/test_schema_consistency.py](./tests/test_schema_consistency.py) | известные расхождения схемы и валидатора |
| [tests/test_interview.py](./tests/test_interview.py) | тесты вопросов, проб, ответов и отчёта |
| [tests/test_init.py](./tests/test_init.py) | тесты генерации, сохранения фрагментов и профиля |
| [tests/test_doctor.py](./tests/test_doctor.py) | тесты встроенных проверок и гейтов |
| [tests/conformance/](./tests/conformance) | корпус фикстур: дефект и ожидаемый диагноз |
| [templates/](./templates) | 17 шаблонов документов: архитектура, реестр, маршрут, функции, долг, план дозаполнения |
| [documents.toml](./documents.toml) | манифест 17 документов: шаблон, путь, назначение |
| [profiles/example.toml](./profiles/example.toml) | пример профиля на вымышленном стеке |
| [tests/fixtures/adapter/adapter.toml](./tests/fixtures/adapter/adapter.toml) | фикстурный адаптер на вымышленном стеке |
| [tools/forbidden_terms.toml](./tools/forbidden_terms.toml) | список слов для теста чистоты ядра |

## Проверки

```text
python -m unittest discover -s tests -p "test_*.py"
python -m ai_kit.profile --check profiles/example.toml
python -m ai_kit.profile --print-schema
python -m ai_kit.templates --check
python -m ai_kit.templates --render readme --profile profiles/example.toml
python -m ai_kit.interview --check --adapter tests/fixtures/adapter/adapter.toml
python -m ai_kit.interview --plan --profile profiles/example.toml --adapter tests/fixtures/adapter/adapter.toml
python -m ai_kit.init --profile profiles/example.toml --adapter tests/fixtures/adapter/adapter.toml --dry-run
```

`doctor` проверяется корпусом `tests/conformance`; в проекте он запускается из каталога проекта: `python -m ai_kit.doctor --profile profile.toml`.

## Границы

- Правило существует только как строка реестра: номер, формулировка, метка исполнения.
- Технологии и домен — данные проекта, не канон.
- Ядро не ссылается на конкретные проекты и их документацию.
- Стартер оптимизирован под бэкенд; другой тип проекта подключается через доменный профиль, а не размытием текущих шаблонов.
- Существующие нарушения правил — baseline: новый код их не расширяет, затронутый участок приводится к правилу, остальное рефакторится по запросу.
