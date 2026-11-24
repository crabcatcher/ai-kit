# Архитектура {{ project.name }}

## Назначение

{{ project.purpose }}

## Границы

{{#each project.boundaries}}- {{ this }}
{{/each}}

## Слои

{{#if layout.layers}}
| Слой | Ответственность | Может зависеть от |
|---|---|---|
{{#each layout.layers}}| {{ name }} | {{ responsibility }} | {{ may_depend_on }} |
{{/each}}

Направление зависимостей: только в сторону, разрешённую таблицей. Обратное ребро — нарушение, даже если сборка проходит.
{{/if}}

<!-- adapter:layers:begin -->
<!-- adapter:layers:end -->

## Внешние системы

| Система | Зачем | Кто владеет контрактом |
|---|---|---|
<!-- adapter:systems:begin -->
<!-- adapter:systems:end -->

## Решения

| Решение | Причина | Что отвергли |
|---|---|---|
<!-- adapter:decisions:begin -->
<!-- adapter:decisions:end -->

## Что не разобрано

Список неизвестных; пусто — значит, открытых вопросов нет.

{{#each unknowns}}- {{ field }}: {{ question }}
{{/each}}
{{#if not_applicable}}
Осознанно неприменимо:
{{#each not_applicable}}- {{ field }}: {{ reason }}
{{/each}}
{{/if}}
