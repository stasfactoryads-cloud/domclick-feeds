#!/usr/bin/env python3
"""
Аудит XML-фида Домклик по требованиям https://domclick.ru/validation/requirements
и правилам оформления ПСК (CLAUDE.md).

Ошибки (ERROR) останавливают публикацию: фид не пройдет проверку Домклик.
Предупреждения (WARN) показывают заглушки 0 и пустые ID, которые нужно заменить.

Запуск: python3 audit_domklik_feed.py [путь к фиду]
Код выхода: 0 если ошибок нет, 1 если есть ошибки.
"""

import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter

DEFAULT_FEED = "feeds/domklik_feed.xml"
MAX_FILE_BYTES = 10 * 1024 * 1024
MIN_PRICE = 1_500_000
ALLOWED_BUILDING_STATES = {"built", "hand_over", "unfinished", "ready"}
ALLOWED_RENOVATIONS = {"без отделки", "черновая", "предчистовая", "чистовая", "да", "нет"}
PHONE_RE = re.compile(r"(\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}")
EXCLUDED_IMAGES = {
    "https://storage.yandexcloud.net/psk-media/media/p/ea/p/38b72252ea66715a55bd24bc012c59a761ad38a2.jpg",
}

errors = []
warnings = []
placeholders = Counter()


def error(where, msg):
    errors.append(f"{where}: {msg}")


def warn(where, msg):
    warnings.append(f"{where}: {msg}")


def text_of(el):
    return (el.text or "").strip() if el is not None else ""


def check_text_rules(where, text):
    """Общие правила к любому тексту в фиде."""
    if "ё" in text or "Ё" in text:
        error(where, "буква «ё» (нужна «е»)")
    if "—" in text:
        error(where, "длинное тире")
    if "<" in text or ">" in text:
        error(where, "HTML-символы в тексте")
    if PHONE_RE.search(text):
        error(where, "номер телефона в тексте объявления")


def check_placeholder(where, value, what):
    if value in ("", "0"):
        warn(where, f"заглушка вместо {what}")


def audit_file(path):
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        error(path, f"файл {size} байт больше 10 МБ")

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        error(path, f"некорректный XML: {e}")
        return

    if root.tag != "complexes":
        error(path, f"корневой элемент {root.tag}, нужен complexes")
        return

    complexes = root.findall("complex")
    if not complexes:
        error(path, "нет ни одного <complex>")

    complex_ids = Counter()
    developer_names = set()
    total_flats = 0
    descriptions = Counter()

    for cx in complexes:
        cid = text_of(cx.find("id"))
        cname = text_of(cx.find("name"))
        where = f"ЖК {cid} {cname}"
        complex_ids[cid] += 1
        check_placeholder(where, cid, "ID ЖК")

        address = text_of(cx.find("address"))
        if not address:
            error(where, "не задан address (обязательный)")
        if not re.search(r"\d", address):
            error(where, f"в адресе нет номера дома: {address}")
        if not (address.startswith("г. Санкт-Петербург") or address.startswith("Санкт-Петербург")):
            error(where, f"адрес должен начинаться с города: {address}")

        for tag in ("name", "address"):
            check_text_rules(f"{where} <{tag}>", text_of(cx.find(tag)))

        desc = cx.find("description_main")
        if desc is not None:
            title = text_of(desc.find("title"))
            body = text_of(desc.find("text"))
            if len(title) > 500:
                error(where, "title в description_main больше 500 символов")
            if len(body) > 3000:
                error(where, "text в description_main больше 3000 символов")
            check_text_rules(f"{where} description_main", title + " " + body)

        for profit in cx.findall("profits_main/profit_main"):
            ptitle = text_of(profit.find("title"))
            ptext = text_of(profit.find("text"))
            if len(ptitle) > 500 or len(ptext) > 500:
                error(where, f"profit_main «{ptitle[:30]}» длиннее 500 символов")
            check_text_rules(f"{where} profit_main", ptitle + " " + ptext)
            img = text_of(profit.find("image"))
            if img and img in EXCLUDED_IMAGES:
                error(where, f"запрещенная картинка в profit_main: {img}")

        dev = cx.find("developer")
        if dev is None:
            error(where, "нет блока <developer> (обязательный)")
        else:
            dev_id = text_of(dev.find("id"))
            check_placeholder(where, dev_id, "ID застройщика")
            developer_names.add(text_of(dev.find("name")))

        building_ids = Counter()
        for bld in cx.findall("buildings/building"):
            bid = text_of(bld.find("id"))
            bname = text_of(bld.find("name"))
            bwhere = f"{where} корпус «{bname}» (ID {bid})"
            building_ids[bid] += 1
            check_placeholder(bwhere, bid, "ID корпуса")

            state = text_of(bld.find("building_state"))
            if state and state not in ALLOWED_BUILDING_STATES:
                error(bwhere, f"building_state «{state}» не из допустимых значений")

            flat_ids = Counter()
            apartments = Counter()
            for flat in bld.findall("flats/flat"):
                total_flats += 1
                fid = text_of(flat.find("flat_id"))
                apt = text_of(flat.find("apartment"))
                fwhere = f"{bwhere} квартира flat_id={fid} кв. {apt}"
                flat_ids[fid] += 1
                if not fid:
                    error(fwhere, "нет flat_id")
                if text_of(flat.find("booking")) not in ("0", "1"):
                    error(fwhere, "booking должен быть 0 или 1")
                if not apt:
                    error(fwhere, "нет apartment (номер квартиры)")
                else:
                    apartments[apt] += 1

                if text_of(flat.find("domrf_id")) in ("", "0"):
                    placeholders["domrf_id квартир"] += 1

                room = text_of(flat.find("room"))
                if not room.isdigit() or int(room) > 5:
                    error(fwhere, f"room «{room}» должен быть от 0 до 5")
                if not text_of(flat.find("floor")):
                    error(fwhere, "нет floor")

                price_text = text_of(flat.find("price"))
                try:
                    price = float(price_text)
                    if price < MIN_PRICE:
                        error(fwhere, f"цена {price:.0f} меньше минимальной {MIN_PRICE}")
                except ValueError:
                    error(fwhere, f"цена «{price_text}» не число")

                try:
                    if float(text_of(flat.find("area"))) <= 0:
                        error(fwhere, "площадь должна быть больше 0")
                except ValueError:
                    error(fwhere, "площадь не число")

                plans = [text_of(p) for p in flat.findall("plans/plan")]
                if not plans:
                    error(fwhere, "нет ни одной планировки (plan)")
                for url in plans:
                    if not url.startswith("https://"):
                        error(fwhere, f"ссылка на планировку не https: {url}")
                    if url in EXCLUDED_IMAGES:
                        error(fwhere, f"запрещенная планировка: {url}")

                hous = text_of(flat.find("housing_type"))
                if hous not in ("0", "1"):
                    error(fwhere, f"housing_type «{hous}» должен быть 0 или 1")

                reno = text_of(flat.find("renovation"))
                if reno and reno not in ALLOWED_RENOVATIONS:
                    error(fwhere, f"renovation «{reno}» не из допустимых значений")

                flat_desc = text_of(flat.find("description"))
                descriptions[flat_desc] += 1
                if "ГК «ПСК»" not in flat_desc:
                    error(fwhere, "в описании нет «ГК «ПСК»»")
                if cname and cname.split(" ", 1)[-1] not in flat_desc and f"«{short_name(cname)}»" not in flat_desc:
                    error(fwhere, "в описании нет названия ЖК")
                if len(flat_desc) > 3000:
                    error(fwhere, "описание больше 3000 символов")
                check_text_rules(f"{fwhere} description", flat_desc)

            for fid, n in flat_ids.items():
                if n > 1:
                    error(bwhere, f"flat_id {fid} повторяется {n} раз")
            for apt, n in apartments.items():
                if n > 1:
                    error(bwhere, f"номер квартиры {apt} повторяется {n} раз (запрещено)")

        for bid, n in building_ids.items():
            if bid not in ("", "0") and n > 1:
                error(where, f"ID корпуса {bid} повторяется {n} раз внутри ЖК")

    for cid, n in complex_ids.items():
        if cid not in ("", "0") and n > 1:
            error("feed", f"ID ЖК {cid} повторяется {n} раз")
    if len(developer_names) > 1:
        error("feed", f"несколько застройщиков в фиде: {', '.join(developer_names)}")

    for label, n in placeholders.items():
        warn("feed", f"заглушка вместо {label}: {n}")

    dup_desc = sum(n - 1 for n in descriptions.values() if n > 1)
    if dup_desc:
        warn("feed", f"{dup_desc} одинаковых описаний квартир (УТП и характеристики совпадают)")

    print(f"Файл: {path}, {size} байт")
    print(f"ЖК: {len(complexes)}, квартир: {total_flats}")


def short_name(full):
    """Название ЖК без префикса для проверки описания."""
    m = re.search(r"«([^»]+)»", full)
    return m.group(1) if m else full


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FEED
    if not os.path.exists(path):
        print(f"ОШИБКА: файл {path} не найден", file=sys.stderr)
        sys.exit(1)

    audit_file(path)

    print(f"\nОшибок: {len(errors)}, предупреждений: {len(warnings)}")
    for e in errors:
        print(f"  ERROR {e}")
    for w in warnings:
        print(f"  WARN  {w}")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
