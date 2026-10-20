# Фиды Домклик ГК «ПСК»

Единый XML-фид для площадки Домклик по всем проектам застройщика.

- Фид: [feeds/domklik_feed.xml](feeds/domklik_feed.xml)
- Ссылка для площадки: https://raw.githubusercontent.com/stasfactoryads-cloud/domclick-feeds/main/feeds/domklik_feed.xml
- Требования: https://domclick.ru/validation/requirements

## Файлы

- `generate_domklik_feed.py`: сборка фида из API psk-info.ru и агентских фидов с планировками.
- `audit_domklik_feed.py`: аудит фида по требованиям Домклик и правилам ПСК.
- `domclick_ids.json`: ID ЖК, корпусов, застройщика. Пустые значения заменяются заглушкой `0`.
- `.github/workflows/update-domklik-feed.yml`: ежедневная сборка в 11:00 МСК.

## Запуск

```bash
python3 generate_domklik_feed.py
python3 audit_domklik_feed.py feeds/domklik_feed.xml
```

Аудит возвращает код 1, если есть ошибки. Заглушки `0` выводятся как предупреждения.

## Автоматика

Workflow запускается каждый день в 08:00 UTC (11:00 МСК). Сначала собирается фид, затем проходит аудит. Если аудит нашел ошибки, фид в репозитории не меняется. Если фид изменился, он коммитится в `main`.
