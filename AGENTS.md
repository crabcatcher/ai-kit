# AGENTS.md

Правила работы с набором `ai-kit`. Набор — бэкенд-стартер методологии: канон, реестр, профиль, шаблоны, интервью, генератор и валидатор.

## Границы

- стартер бэкендовый: слои, миграции и владение объектами — форма по умолчанию; фронт и мобильное приложение — отдельные доменные профили, которых пока нет;
- канон самостоятелен; названия технологий, доменные понятия и ссылки на другие проекты в ядро не входят;
- стек и домен живут в профиле и адаптере, а не в шаблонах;
- правило существует строкой реестра `canon/registry.md`, `AGENTS.md` потребителя — его представление.

## Перед правкой

1. прочитать [README.md](./README.md); шаблоны — [docs/templates.md](./docs/templates.md), вопросы и пробы — [docs/interview.md](./docs/interview.md), генерация — [docs/generator.md](./docs/generator.md), проверки — [docs/doctor.md](./docs/doctor.md);
2. свериться с каноном: [canon/canon.md](./canon/canon.md);
3. для новой проверки — [docs/adapter-contract.md](./docs/adapter-contract.md).

## Проверки

```text
python -m unittest discover -s tests -p "test_*.py"
python -m ai_kit.profile --check profiles/example.toml
python -m ai_kit.templates --check
python -m ai_kit.interview --check --adapter tests/fixtures/adapter/adapter.toml
python -m ai_kit.init --profile profiles/example.toml --adapter tests/fixtures/adapter/adapter.toml --dry-run
```

Новый шаблон без манифеста не рендерится; новый слот без схемы профиля не проходит `--check`. Вопрос без `why`, слот вне схемы и проба без доказательства не проходят проверку интервью. Правило адаптера или профиля без метки, исполнителя и владельца не проходит генерацию; для «нет гейта» нужны причина и триггер. `doctor` проверяется корпусом `tests/conformance`: новая проверка без фикстуры считается незавершённой. Запрещённые слова в ядре ловит `tests/test_core_clean.py`.
