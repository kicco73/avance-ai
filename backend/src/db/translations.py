from __future__ import annotations

from datetime import datetime

from .models import Translation, database


class TranslationMixin:

    def get_translation(self, key: str, src_lang: str, src_text: str, dst_lang: str) -> str | None:
        row = Translation.get_or_none(
            (Translation.key == key) & (Translation.src_lang == src_lang)
            & (Translation.src_text == src_text) & (Translation.dst_lang == dst_lang)
        )
        return row.dst_text if row is not None else None

    def save_translation(self, key: str, src_lang: str, src_text: str, dst_lang: str, dst_text: str) -> None:
        with database.atomic():
            written = Translation.update(dst_text=dst_text, timestamp=datetime.utcnow()).where(
                (Translation.key == key) & (Translation.src_lang == src_lang)
                & (Translation.src_text == src_text) & (Translation.dst_lang == dst_lang)
            ).execute()
            if not written:
                Translation.create(
                    key=key, src_lang=src_lang, src_text=src_text, dst_lang=dst_lang, dst_text=dst_text,
                )

    def count_translations(self) -> int:
        return Translation.select().count()

    def clear_translations(self) -> int:
        count = self.count_translations()
        Translation.delete().execute()
        return count
