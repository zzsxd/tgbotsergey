from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def subscription_keyboard(urls: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура с кнопками-ссылками на каналы (по 2 в ряд)."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for idx, url in enumerate(urls, start=1):
        row.append(InlineKeyboardButton(text=f"Подписаться {idx}", url=url))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


