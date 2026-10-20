#!/usr/bin/env python3
"""
Генератор XML-фидов для площадки Домклик (новостройки).

Проекты: АКВАТОРИЯ (ID 44), АССАМБЛЕЯ (ID 41).
Данные о проектах (адрес, метро, класс, сдача, преимущества) и планировки
берутся из API сайта psk-info.ru:
  проекты:  https://psk-info.ru/api/projects/?format=json
  квартиры: https://psk-info.ru/api/flats/?format=json

Структура XML повторяет realty-feed, который уже используется в фидах ПСК.
Официальная спецификация фида Домклик в открытом доступе не найдена,
поля нужно сверить с требованиями в кабинете партнера Домклик.

Запуск: python3 generate_domklik_feed.py
"""

import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from xml.dom import minidom

# === Конфигурация проектов ===
PROJECTS = {
    44: {"output_file": "akvatoriya_domklik_feed.xml"},
    41: {"output_file": "assambleya_domklik_feed.xml"},
}

PROJECTS_API = "https://psk-info.ru/api/projects/?format=json"
FLATS_API = "https://psk-info.ru/api/flats/?format=json"
SITE_DOMAIN = "https://psk-info.ru"
PAGE_SIZE = 100
AGENT_ORG = "Группа компаний ПСК"
NS = "http://webmaster.yandex.ru/schemas/feed/realty/2010-06"
MSK_TZ = timezone(timedelta(hours=3))
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def fetch_json(url):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all(base_url):
    """Загрузить все записи списочного API с пагинацией limit/offset."""
    items = []
    offset = 0
    total = None

    while True:
        data = fetch_json(f"{base_url}&limit={PAGE_SIZE}&offset={offset}")
        if total is None:
            total = data.get("count", 0)

        results = data.get("results", [])
        if not results:
            break

        items.extend(results)
        offset += PAGE_SIZE
        if offset >= total:
            break

    return items


def clean_text(text):
    """Оформление по правилам ПСК: без буквы «ё», без длинного тире, одинарные пробелы."""
    if not text:
        return ""
    text = text.replace("ё", "е").replace("Ё", "Е").replace("—", "–")
    return re.sub(r"\s+", " ", text).strip()


def short_name(full_name):
    """«Акватория», премиальный дом -> Акватория"""
    match = re.search(r"«([^»]+)»", full_name or "")
    return match.group(1) if match else (full_name or "")


def build_project_info(project):
    """Собрать данные проекта из ответа API проектов."""
    metros = sorted(project.get("projectmetro_set") or [], key=lambda m: m.get("order") or 0)
    advantages = sorted(project.get("about_project_advantages") or [], key=lambda a: a.get("order") or 0)
    return {
        "name": clean_text(short_name(project.get("name"))),
        "slug": project.get("slug", ""),
        "address": clean_text(project.get("address")),
        "project_class": project.get("project_class") or "",
        "metro": metros[0] if metros else None,
        "completion_year": project.get("completion_year"),
        "completion_quarter": project.get("completion_quarter"),
        "advantages": [
            {"title": clean_text(a.get("title")), "text": clean_text(a.get("text"))}
            for a in advantages
            if a.get("title") and a.get("text")
        ],
    }


def metro_line(metro):
    """Ближайшее метро и время до него."""
    if not metro:
        return ""
    if metro.get("walk_time"):
        return f"метро {metro['metro_name']}, {metro['walk_time']} мин пешком"
    if metro.get("car_time"):
        return f"метро {metro['metro_name']}, {metro['car_time']} мин на машине"
    return f"метро {metro['metro_name']}"


def filter_project_flats(all_flats, project_id):
    """Свободные квартиры проекта с ценой."""
    filtered = []
    for flat in all_flats:
        if flat.get("project") != project_id:
            continue
        if flat.get("status") != "Свободно":
            continue
        price = flat.get("price")
        if not price or float(price) <= 0:
            continue
        filtered.append(flat)
    return filtered


def get_rooms_info(flat):
    """В API количество комнат 0 означает студию."""
    rooms = flat.get("rooms") or 0
    if rooms == 0:
        return 1, True
    return rooms, False


def get_finishing(flat):
    finishing = (flat.get("finishing") or "").lower()
    if "без" in finishing:
        return "без отделки"
    if "предчистов" in finishing:
        return "предчистовая отделка"
    if "чистов" in finishing:
        return "чистовая отделка"
    if "под ключ" in finishing:
        return "под ключ"
    return None


def get_old_price(flat, price):
    original = flat.get("original_price")
    if original and float(original) > price:
        return float(original)
    return None


def build_description(flat, info, utp_index):
    """Уникальное описание объявления: характеристики квартиры и одно УТП проекта."""
    rooms, is_studio = get_rooms_info(flat)
    area = round(flat.get("area") or 0, 2)
    floor = flat.get("floor")
    max_floor = flat.get("max_floor")
    building = flat.get("building")

    type_label = "Студия" if is_studio else f"{rooms}-комнатная квартира"
    head = [f"{type_label}, {area} кв.м"]
    if floor and max_floor:
        head.append(f"этаж {floor} из {max_floor}")
    if building:
        head.append(f"корпус {building}")
    text = ", ".join(head) + f". ЖК «{info['name']}» от ГК «ПСК». "

    if info["advantages"]:
        utp = info["advantages"][utp_index % len(info["advantages"])]
        text += f"{utp['title']}: {utp['text'].rstrip('.')}. "

    line = metro_line(info["metro"])
    if line:
        text += f"Рядом {line}. "

    if info["completion_quarter"] and info["completion_year"]:
        text += f"Сдача {info['completion_quarter']} кв. {info['completion_year']} г. "

    price = float(flat.get("price") or 0)
    old_price = get_old_price(flat, price)
    if old_price:
        discount_pct = round((1 - price / old_price) * 100)
        if discount_pct > 0:
            text += f"Скидка {discount_pct}% по акции. "

    return text.strip()


def add_text(parent, tag, text):
    el = ET.SubElement(parent, tag)
    el.text = text
    return el


def build_feed(flats, info):
    """Собрать XML-фид для одного проекта."""
    now = datetime.now(MSK_TZ)
    creation_date = now.strftime("%Y-%m-%dT%H:%M:%S+03:00")

    ET.register_namespace("", NS)
    root = ET.Element(f"{{{NS}}}realty-feed")
    add_text(root, "generation-date", creation_date)

    agent_url = f"{SITE_DOMAIN}/projects/{info['slug']}"

    for index, flat in enumerate(flats):
        flat_id = flat.get("id")
        if not flat_id:
            continue

        rooms, is_studio = get_rooms_info(flat)
        price = float(flat.get("price") or 0)
        old_price = get_old_price(flat, price)
        area = round(flat.get("area") or 0, 2)
        floor = flat.get("floor")
        max_floor = flat.get("max_floor")
        building = flat.get("building") or ""

        offer = ET.SubElement(root, "offer", attrib={"internal-id": str(flat_id)})

        add_text(offer, "type", "продажа")
        add_text(offer, "property-type", "жилая")
        add_text(offer, "category", "квартира")
        add_text(offer, "creation-date", creation_date)
        add_text(offer, "url", f"{SITE_DOMAIN}/flats/{flat_id}")

        loc = ET.SubElement(offer, "location")
        add_text(loc, "country", "Россия")
        add_text(loc, "region", "Санкт-Петербург")
        add_text(loc, "locality-name", "Санкт-Петербург")
        add_text(loc, "address", info["address"])
        if info["metro"]:
            metro = ET.SubElement(loc, "metro")
            add_text(metro, "name", info["metro"]["metro_name"])

        price_el = ET.SubElement(offer, "price")
        add_text(price_el, "value", str(int(price)))
        add_text(price_el, "currency", "RUR")
        if old_price:
            add_text(offer, "old-price", str(int(old_price)))

        area_el = ET.SubElement(offer, "area")
        add_text(area_el, "value", str(area))
        add_text(area_el, "unit", "кв.м")

        add_text(offer, "rooms", str(rooms))
        add_text(offer, "rooms-offered", str(rooms))
        if is_studio:
            add_text(offer, "studio", "да")

        if floor:
            add_text(offer, "floor", str(floor))
        if max_floor:
            add_text(offer, "floors-total", str(max_floor))

        bld_label = f"ЖК «{info['name']}»"
        if building:
            bld_label += f", корпус {building}"
        add_text(offer, "building-name", bld_label)

        if info["completion_year"]:
            add_text(offer, "built-year", str(info["completion_year"]))
        if info["completion_quarter"]:
            add_text(offer, "ready-quarter", str(info["completion_quarter"]))
        add_text(offer, "new-flat", "да")

        finishing = get_finishing(flat)
        if finishing:
            add_text(offer, "renovation", finishing)

        # Планировки из API: квартира и поэтажный план
        for key in ("plan", "floor_plan"):
            url = flat.get(key)
            if url:
                add_text(offer, "image", url)

        add_text(offer, "description", build_description(flat, info, index))

        agent = ET.SubElement(offer, "sales-agent")
        add_text(agent, "organization", AGENT_ORG)
        add_text(agent, "category", "застройщик")
        add_text(agent, "url", agent_url)

    return root


def prettify_xml(root):
    """Форматирование XML с отступами."""
    rough = ET.tostring(root, encoding="unicode", xml_declaration=False)
    declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    parsed = minidom.parseString(rough)
    pretty = parsed.toprettyxml(indent="  ", encoding=None)
    lines = pretty.split("\n")
    if lines[0].startswith("<?xml"):
        lines = lines[1:]
    return declaration + "\n".join(lines)


def generate_project_feed(all_flats, projects_by_id, project_id, config):
    """Сгенерировать фид для одного проекта."""
    project = projects_by_id.get(project_id)
    if not project:
        print(f"  ВНИМАНИЕ: проект ID={project_id} не найден в API, пропускаем", file=sys.stderr)
        return 0

    info = build_project_info(project)
    print(f"\n--- ЖК {info['name']} (ID={project_id}) ---", file=sys.stderr)

    flats = filter_project_flats(all_flats, project_id)
    print(f"  Свободных квартир с ценой: {len(flats)}", file=sys.stderr)
    if not flats:
        print("  ВНИМАНИЕ: нет квартир, фид не создан", file=sys.stderr)
        return 0

    feed = build_feed(flats, info)
    output_path = os.path.join(OUTPUT_DIR, config["output_file"])
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(prettify_xml(feed))

    print(f"  Сохранено: {config['output_file']} ({len(flats)} квартир)", file=sys.stderr)

    rooms_count = Counter()
    for flat in flats:
        rooms, is_studio = get_rooms_info(flat)
        rooms_count["Студии" if is_studio else f"{rooms}-комн."] += 1
    for label, count in sorted(rooms_count.items()):
        print(f"    {label}: {count}", file=sys.stderr)

    return len(flats)


def main():
    print("=== Генерация фидов ГК ПСК для Домклик ===", file=sys.stderr)

    print("\n1. Загрузка проектов и квартир из API...", file=sys.stderr)
    projects_by_id = {p["id"]: p for p in fetch_all(PROJECTS_API)}
    all_flats = fetch_all(FLATS_API)
    print(f"   Проектов: {len(projects_by_id)}, квартир: {len(all_flats)}", file=sys.stderr)

    print("\n2. Генерация фидов по проектам...", file=sys.stderr)
    total_flats = 0
    total_feeds = 0
    for project_id, config in PROJECTS.items():
        count = generate_project_feed(all_flats, projects_by_id, project_id, config)
        if count > 0:
            total_flats += count
            total_feeds += 1

    print(f"\n=== Итого: {total_feeds} фидов, {total_flats} квартир ===", file=sys.stderr)
    if total_feeds == 0:
        print("ОШИБКА: ни один фид не сгенерирован", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
