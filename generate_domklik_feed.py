#!/usr/bin/env python3
"""
Генератор единого XML-фида для площадки Домклик (новостройки) по всем проектам застройщика.

Формат: требования Домклик https://domclick.ru/validation/requirements
Корень <complexes> > <complex> (по одному на ЖК) > <buildings> > <building> > <flats> > <flat>.

Данные берутся из API сайта psk-info.ru:
  проекты:  https://psk-info.ru/api/projects/?format=json
  квартиры: https://psk-info.ru/api/flats/?format=json
Планировки JPG берутся из агентских фидов, которые уже лежат на хостинге.

ID Домклик (ID ЖК, ID корпусов, ID застройщика, domrf_id квартир) в API сайта нет.
Они лежат в domclick_ids.json рядом со скриптом. Пустые ID заменяются заглушкой "0".

Запуск: python3 generate_domklik_feed.py
Результат: feeds/domklik_feed.xml
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

# ID проектов в API сайта psk-info.ru. Ключ совпадает с именем агентского фида и с domclick_ids.json
PROJECTS = {
    "optimist": 45,
    "sezony": 43,
    "akvatoriya": 44,
    "assambleya": 41,
    "galernaya-gavan": 42,
    "plus-pulkovsky": 32,
    "respect": 39,
    "industrial-avenir": 40,
    "ladozhsky-avenir": 2,
    "bakunina-33": 30,
    "severnaya-korona": 22,
}

PROJECTS_API = "https://psk-info.ru/api/projects/?format=json"
FLATS_API = "https://psk-info.ru/api/flats/?format=json"
# Агентские фиды с JPG-планировками (plan, floor-plan) по internal-id лота
PLANS_FEED_TEMPLATE = "https://feedhub.realty/feed-evel/feeds/pan/{key}.xml"
IDS_FILE = "domclick_ids.json"
OUTPUT_FILE = "feeds/domklik_feed.xml"

SITE_DOMAIN = "https://psk-info.ru"
PAGE_SIZE = 100
DEVELOPER_NAME = "Петербургская Строительная Компания"
NS_PLANS = "http://webmaster.yandex.ru/schemas/feed/realty/2010-06"
MSK_TZ = timezone(timedelta(hours=3))
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
PLACEHOLDERS = Counter()


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


def load_plan_images(url):
    """Карта internal-id лота -> [(tag, url)] из опубликованного агентского фида."""
    with urllib.request.urlopen(url, timeout=120) as resp:
        root = ET.fromstring(resp.read())
    plans = {}
    for offer in root.iter(f"{{{NS_PLANS}}}offer"):
        images = [
            (img.get("tag"), img.text.strip())
            for img in offer.findall(f"{{{NS_PLANS}}}image")
            if img.get("tag") in ("plan", "floor-plan") and img.text
        ]
        plans[offer.get("internal-id")] = images
    return plans


def load_ids():
    """ID Домклик из domclick_ids.json."""
    with open(os.path.join(OUTPUT_DIR, IDS_FILE), encoding="utf-8") as f:
        return json.load(f)


def value_or_placeholder(value, kind):
    """Пустое значение или 0 считается заглушкой и учитывается в счетчике."""
    if value and str(value) != "0":
        return str(value)
    PLACEHOLDERS[kind] += 1
    return "0"


def clean_text(text):
    """Оформление по правилам ПСК: без буквы «ё», без длинного тире, одинарные пробелы."""
    if not text:
        return ""
    text = text.replace("ё", "е").replace("Ё", "Е").replace("—", "–")
    return re.sub(r"\s+", " ", text).strip()


def short_name(full_name):
    """«Акватория», премиальный дом -> Акватория"""
    match = re.search(r"«([^»]+)»", full_name or "")
    if match:
        return match.group(1)
    return re.sub(r"^(Апарт.отель|ЖК)\s+", "", full_name or "")


def build_project_info(project):
    """Данные проекта из ответа API проектов."""
    metros = sorted(project.get("projectmetro_set") or [], key=lambda m: m.get("order") or 0)
    advantages = sorted(project.get("about_project_advantages") or [], key=lambda a: a.get("order") or 0)
    lat, lng = (project.get("coords") or ",").split(",")[:2]
    return {
        "name": clean_text(short_name(project.get("name"))),
        "title": clean_text(project.get("title")),
        "address": clean_text(project.get("address")),
        "description": clean_text(project.get("tooltip_project_text"))[:3000],
        "latitude": lat.strip(),
        "longitude": lng.strip(),
        "metro": metros[0] if metros else None,
        "completion_year": project.get("completion_year"),
        "completion_quarter": project.get("completion_quarter"),
        "housing_type": 1 if "апарт" in (project.get("name") or "").lower() else 0,
        "advantages": [
            {"title": clean_text(a.get("title"))[:500], "text": clean_text(a.get("text"))[:500], "image": a.get("image")}
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


def get_room(flat):
    """Домклик: 0 студия, 1-5 комнаты. В API 0 комнат означает студию."""
    rooms = flat.get("rooms") or 0
    return min(int(rooms), 5)


def get_renovation(flat):
    finishing = (flat.get("finishing") or "").lower()
    if "без" in finishing:
        return "без отделки"
    if "предчистов" in finishing:
        return "предчистовая"
    if "чистов" in finishing:
        return "чистовая"
    return None


def get_discount_pct(flat, price):
    """Скидка в процентах, если original_price выше текущей цены."""
    original = flat.get("original_price")
    if original and float(original) > price:
        return round((1 - price / float(original)) * 100, 1)
    return None


def build_flat_description(flat, info):
    """Описание квартиры с названием ЖК и «ГК «ПСК»»."""
    room = get_room(flat)
    area = round(flat.get("area") or 0, 2)
    type_label = "Студия" if room == 0 else f"{room}-комнатная квартира"
    head = [f"{type_label}, {area} кв.м"]
    if flat.get("floor") and flat.get("max_floor"):
        head.append(f"этаж {flat['floor']} из {flat['max_floor']}")
    text = ", ".join(head) + f". ЖК «{info['name']}» от ГК «ПСК». "
    line = metro_line(info["metro"])
    if line:
        text += f"Рядом {line}. "
    return text.strip()


def add(parent, tag, text):
    el = ET.SubElement(parent, tag)
    el.text = str(text)
    return el


def add_complex(root, key, flats, info, plan_images, ids):
    """Добавить в корень один <complex> со всеми его корпусами и квартирами."""
    cx_ids = ids["complexes"].get(key, {})
    buildings_ids = cx_ids.get("buildings", {})
    domrf_ids = ids.get("domrf_ids", {})

    cx = ET.SubElement(root, "complex")
    add(cx, "id", value_or_placeholder(cx_ids.get("complex_id"), "ID ЖК"))
    label = "Апарт-отель" if info["housing_type"] else "ЖК"
    add(cx, "name", f"{label} «{info['name']}»")
    add(cx, "latitude", info["latitude"])
    add(cx, "longitude", info["longitude"])
    add(cx, "address", info["address"])

    desc = ET.SubElement(cx, "description_main")
    add(desc, "title", info["title"][:500])
    add(desc, "text", info["description"])

    profits = ET.SubElement(cx, "profits_main")
    for adv in info["advantages"]:
        profit = ET.SubElement(profits, "profit_main")
        add(profit, "title", adv["title"])
        add(profit, "text", adv["text"])
        if adv["image"]:
            add(profit, "image", adv["image"])

    buildings_el = ET.SubElement(cx, "buildings")
    by_building = {}
    for flat in flats:
        by_building.setdefault(str(flat.get("building") or "1"), []).append(flat)

    for number in sorted(by_building, key=lambda n: (len(n), n)):
        bld_flats = by_building[number]
        bld = ET.SubElement(buildings_el, "building")
        add(bld, "id", value_or_placeholder(buildings_ids.get(number), "ID корпуса"))
        add(bld, "name", f"корпус {number}")
        add(bld, "latitude", info["latitude"])
        add(bld, "longitude", info["longitude"])
        add(bld, "address", info["address"])
        add(bld, "floors", max(int(f.get("max_floor") or 0) for f in bld_flats))
        add(bld, "building_state", "unfinished")
        if info["completion_year"]:
            add(bld, "built_year", info["completion_year"])
        if info["completion_quarter"]:
            add(bld, "ready_quarter", info["completion_quarter"])

        flats_el = ET.SubElement(bld, "flats")
        for flat in bld_flats:
            flat_id = str(flat["id"])

            price = float(flat["price"])
            discount = get_discount_pct(flat, price)
            base_price = float(flat["original_price"]) if discount else price

            f_el = ET.SubElement(flats_el, "flat")
            add(f_el, "flat_id", flat_id)
            add(f_el, "apartment", flat.get("number") or "")
            add(f_el, "domrf_id", value_or_placeholder(domrf_ids.get(flat_id), "domrf_id квартиры"))
            add(f_el, "floor", flat.get("floor") or 0)
            add(f_el, "room", get_room(flat))

            plans = [url for tag, url in plan_images.get(flat_id, []) if tag == "plan"]
            if not plans:
                PLACEHOLDERS["планировка квартиры"] += 1
            plans_el = ET.SubElement(f_el, "plans")
            for url in plans:
                add(plans_el, "plan", url)

            renovation = get_renovation(flat)
            if renovation:
                add(f_el, "renovation", renovation)
            add(f_el, "price", int(base_price))
            if discount:
                disc_el = ET.SubElement(f_el, "discounts")
                d = ET.SubElement(disc_el, "discount")
                add(d, "value", discount)
                add(d, "description", "Скидка по акции")
            add(f_el, "area", round(flat.get("area") or 0, 2))
            add(f_el, "housing_type", info["housing_type"])
            add(f_el, "description", build_flat_description(flat, info))

    dev = ET.SubElement(cx, "developer")
    add(dev, "id", value_or_placeholder(ids.get("developer_id"), "ID застройщика"))
    add(dev, "name", DEVELOPER_NAME)
    add(dev, "site", SITE_DOMAIN)


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


def main():
    print("=== Генерация единого фида Домклик по всем проектам ===", file=sys.stderr)

    print("\n1. Загрузка данных из API и агентских фидов...", file=sys.stderr)
    projects = {p["id"]: p for p in fetch_all(PROJECTS_API)}
    all_flats = fetch_all(FLATS_API)
    ids = load_ids()

    root = ET.Element("complexes")
    total = 0
    print("\n2. Сборка ЖК...", file=sys.stderr)
    for key, project_id in PROJECTS.items():
        project = projects.get(project_id)
        if not project:
            print(f"   ВНИМАНИЕ: проект {key} (ID {project_id}) не найден в API, пропускаем", file=sys.stderr)
            continue
        info = build_project_info(project)
        flats = filter_project_flats(all_flats, project_id)
        plan_images = load_plan_images(PLANS_FEED_TEMPLATE.format(key=key))
        if not flats:
            print(f"   {info['name']}: свободных квартир нет, пропускаем", file=sys.stderr)
            continue
        add_complex(root, key, flats, info, plan_images, ids)
        total += len(flats)
        print(f"   {info['name']}: {len(flats)} квартир", file=sys.stderr)

    output_path = os.path.join(OUTPUT_DIR, OUTPUT_FILE)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(prettify_xml(root))
    print(f"\n=== Итого: {len(root)} ЖК, {total} квартир. Сохранено: {OUTPUT_FILE} ===", file=sys.stderr)

    if PLACEHOLDERS:
        print("\nЗАГЛУШКИ 0 (фид не пройдет проверку Домклик, пока не заменено):", file=sys.stderr)
        for kind, count in PLACEHOLDERS.most_common():
            print(f"   - {kind}: {count}", file=sys.stderr)


if __name__ == "__main__":
    main()
