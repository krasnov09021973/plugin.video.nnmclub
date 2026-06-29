# -*- coding: utf-8 -*-
import os
import sqlite3
import xbmc
import xbmcaddon
import xbmcvfs

class PosterCacheDB:
    def __init__(self, addon_instance=None):
        self.addon = addon_instance if addon_instance else xbmcaddon.Addon()
        self.db_path = self._get_db_path()
        self._init_table()

    def _get_db_path(self):
        profile_path = xbmcvfs.translatePath(self.addon.getAddonInfo('profile'))
        if not os.path.exists(profile_path):
            try: os.makedirs(profile_path)
            except: pass
        return os.path.join(profile_path, 'cache.db')

    def _init_table(self):
        """Создает таблицы кэша обложек и истории поиска при первом запуске"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            # ТАБЛИЦА 1: Кэш метаданных и обложек TVDB
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS poster_cache (
                    topic_id TEXT PRIMARY KEY,
                    poster_url TEXT NOT NULL,
                    plot_text TEXT,
                    movie_year TEXT,
                    rating_val REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # ТАБЛИЦА 2: ВАША НОВАЯ ТАБЛИЦА ИСТОРИИ ПОИСКА (БЕЗ МУСОРА В XML)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS search_history (
                    query_text TEXT,
                    forum_id TEXT DEFAULT '',
                    search_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (query_text, forum_id)
                )
            ''')

            conn.commit()
            conn.close()
            xbmc.log("[NNM-DB] Все таблицы SQLite (Обложки + История) успешно инициализированы.", level=xbmc.LOGINFO)
        except Exception as e:
            xbmc.log(f"[NNM-DB-ERROR] Ошибка инициализации таблиц: {str(e)}", level=xbmc.LOGERROR)

    # --- БЛОК РАБОТЫ С ОБЛОЖКАМИ TVDB ---
    def get_poster(self, topic_id):
        if not topic_id: return None
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()
            cursor.execute('SELECT poster_url, plot_text, movie_year, rating_val FROM poster_cache WHERE topic_id = ?', (str(topic_id),))
            row = cursor.fetchone()
            conn.close()
            if row:
                return {'cover': row[0], 'plot': row[1], 'year': row[2], 'rating': row[3]}
        except Exception as e:
            xbmc.log(f"[NNM-DB-ERROR] Ошибка чтения обложки {topic_id}: {str(e)}", level=xbmc.LOGERROR)
        return None

    def save_poster(self, topic_id, poster_url, plot="", year="", rating=0.0):
        if not topic_id or not poster_url: return
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()
            try: final_rating = float(rating) if rating else 0.0
            except: final_rating = 0.0
            cursor.execute('''
                INSERT OR REPLACE INTO poster_cache (topic_id, poster_url, plot_text, movie_year, rating_val)
                VALUES (?, ?, ?, ?, ?)
            ''', (str(topic_id), str(poster_url), str(plot), str(year), final_rating))
            conn.commit()
            conn.close()
        except Exception as e:
            xbmc.log(f"[NNM-DB-ERROR] Ошибка записи обложки {topic_id}: {str(e)}", level=xbmc.LOGERROR)

    # =====================================================================
    # ВАШ НОВЫЙ ИЗОЛИРОВАННЫЙ БЛОК УПРАВЛЕНИЯ ИСТОРИЕЙ ПОИСКА В SQLITE
    # =====================================================================
    def get_history(self):
        """
        Извлекает полный список сохраненных поисков.
        Возвращает список кортежей вида: [(текст, id_раздела), ...]
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()
            # Забираем текст и привязанный ID форума, сортируя по свежести времени
            cursor.execute('SELECT query_text, forum_id FROM search_history ORDER BY search_time DESC')
            rows = cursor.fetchall()
            conn.close()
            return rows # Возвращает [(query, forum_id), (query, forum_id), ...]
        except Exception as e:
            xbmc.log(f"[NNM-DB-HISTORY-ERROR] Ошибка чтения истории: {str(e)}", level=xbmc.LOGERROR)
            return []

    def add_history(self, query_text, forum_id=""):
        """Добавляет запрос в базу, привязывая его к конкретному разделу трекера"""
        if not query_text or query_text.strip() == "": return
        query_text = query_text.strip()
        # Преобразуем None или числовой ID в строковый формат для базы данных
        forum_str = str(forum_id) if (forum_id and forum_id != -1) else ""

        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()

            # Пишем или обновляем штамп времени для связки Слово + Раздел
            cursor.execute('''
                INSERT OR REPLACE INTO search_history (query_text, forum_id, search_time)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (query_text, forum_str))

            # Авто-чистка: держим в базе строго 15 последних уникальных поисков
            cursor.execute('''
                DELETE FROM search_history WHERE (query_text, forum_id) NOT IN (
                    SELECT query_text, forum_id FROM search_history ORDER BY search_time DESC LIMIT 15
                )
            ''')

            conn.commit()
            conn.close()
        except Exception as e:
            xbmc.log(f"[NNM-DB-HISTORY-ERROR] Ошибка записи истории: {str(e)}", level=xbmc.LOGERROR)

    def clear_history(self):
        """Полностью очищает таблицу истории"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM search_history')
            conn.commit()
            conn.close()
        except Exception as e:
            xbmc.log(f"[NNM-DB-HISTORY-ERROR] Ошибка очистки истории: {str(e)}", level=xbmc.LOGERROR)
