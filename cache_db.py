# -*- coding: utf-8 -*-
import os
import sqlite3
import json
import xbmc
import xbmcaddon
import xbmcvfs
import zlib

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

	def _get_connection(self):
		try:
			return sqlite3.connect(self.db_path, timeout=5)
		except Exception as e:
			xbmc.log(f"[NNM-DB-ERROR] Ошибка подключения БД: {str(e)}", level=xbmc.LOGERROR)
			return None

	def _init_table(self):
		"""Создает таблицы кэша обложек и истории поиска при первом запуске"""
		conn = self._get_connection()
		if not conn:
			return None
		try:
			cursor = conn.cursor()

			# ТАБЛИЦА 1: Кэш метаданных и обложек TVDB
			cursor.execute('''
				CREATE TABLE IF NOT EXISTS content_meta (
					link_id INTEGER PRIMARY KEY,
					tvdb_id TEXT UNIQUE,
					title_ru TEXT,                 -- Кэш русского поискового индекса
					title_en TEXT,                 -- Кэш оригинального поискового индекса
					cover TEXT,
					plot TEXT,
					year TEXT,
					rating REAL,
					tvdb_raw_json TEXT             -- Сюда пишем весь сырой JSON (поиск, постеры, инфо)
				)
			''')

			cursor.execute('''
				INSERT OR IGNORE INTO content_meta (link_id, cover, plot, year, rating)
				VALUES (-1, 'LOCAL_ICON', 'Информация на TheTVDB отсутствует', '0000', 0.0)
			''')

			cursor.execute('''
				CREATE TABLE IF NOT EXISTS topic_linker (
					topic_id TEXT PRIMARY KEY,
					link_id INTEGER,
					FOREIGN KEY(link_id) REFERENCES content_meta(link_id)
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

			# ТАБЛИЦА 3: ВАША НОВАЯ ТАБЛИЦА ИСТОРИИ ПРОСМОТРА
			cursor.execute('''
				CREATE TABLE IF NOT EXISTS view_history (
					topic_id TEXT PRIMARY KEY,
					title TEXT,
					dnld_id TEXT,
					view_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
				)
			''')

			conn.commit()
			conn.close()
			xbmc.log("[NNM-DB] Все таблицы SQLite (Обложки + История) успешно инициализированы.", level=xbmc.LOGINFO)
		except Exception as e:
			xbmc.log(f"[NNM-DB-ERROR] Ошибка инициализации таблиц: {str(e)}", level=xbmc.LOGERROR)

	# --- БЛОК РАБОТЫ С ОБЛОЖКАМИ TVDB ---
	def get_poster(self, topic_id):
		"""Чтение через LEFT JOIN: связываем топик трекера с метаданными по tvdb_id"""
		conn = self._get_connection()
		if not conn:
			return None
		try:
			conn.row_factory = sqlite3.Row
			cursor = conn.cursor()
			# Вытаскиваем данные фильма, соединяя таблицы по числовому ID
			cursor.execute('''
				SELECT tl.topic_id as id, cm.cover, cm.plot, cm.year, cm.rating, cm.link_id, cm.tvdb_raw_json
				FROM topic_linker tl
				LEFT JOIN content_meta cm ON tl.link_id = cm.link_id
				WHERE tl.topic_id = ?
			''', (str(topic_id),))
			row = cursor.fetchone()
			# Формируем результат для отладки
			result = dict(row) if row and row['cover'] else None
			# Печатаем в лог точный тип и содержимое ответа
#			if result is None:
#				# Если вернулся None, проверим: вообще строки нет или просто обложка пустая?
#				if row:
#					xbmc.log(f"[NNM-DEBUG] (get_poster) Для ID {topic_id} строка в БД ЕСТЬ, но cover пуст. Метод возвращает: None", level=xbmc.LOGINFO)
#				else:
#					xbmc.log(f"[NNM-DEBUG] (get_poster) Для ID {topic_id} в БД вообще НЕТ такой записи. Метод возвращает: None", level=xbmc.LOGINFO)
#			else:
#				xbmc.log(f"[NNM-DEBUG] (get_poster) Для ID {topic_id} найдена обложка! Метод возвращает dict: {str(result)}", level=xbmc.LOGINFO)
			return result
		except Exception as e:
			xbmc.log(f"[NNM-DB-ERROR] Ошибка реляционного чтения {topic_id}: {str(e)}", level=xbmc.LOGERROR)
			return None

	def save_poster_relational(self, topic_id, tvdb_id, title_ru, title_en, cover, plot, year, rating, raw_json=''):
		"""Атомарная транзакция записи: карточка фильма + линкер топика"""

		if tvdb_id:
			# Хэшируем строку "movie-753" в уникальное 32-битное целое число.
			# Это гарантирует уникальность и полностью решает проблему дублирования ID!
			server_id = zlib.crc32(str(tvdb_id).encode('utf-8'))
		else:
			server_id = abs(hash(title_ru)) % (10 ** 8)

		conn = self._get_connection()
		if not conn: return
		try:
			cursor = conn.cursor()
			# Шаг А: Записываем или обновляем саму карточку фильма по его tvdb_id
			cursor.execute('''
				INSERT OR REPLACE INTO content_meta (link_id, tvdb_id, title_ru, title_en, cover, plot, year, rating, tvdb_raw_json)
				VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
			''', (int(server_id), str(tvdb_id), str(title_ru), str(title_en), str(cover), str(plot), str(year), float(rating), str(raw_json)))

			# Шаг Б: Привязываем текущий ID топика трекера к этому tvdb_id
			cursor.execute('''
				INSERT OR REPLACE INTO topic_linker (topic_id, link_id)
				VALUES (?, ?)
			''', (str(topic_id), int(server_id)))

			conn.commit()
		except Exception as e:
			xbmc.log(f"[NNM-DB-ERROR] Сбой транзакции линкера для топика {topic_id} через link_id {server_id}: {str(e)}", level=xbmc.LOGERROR)

	def get_poster_by_name(self, title_ru, title_en, year=""):
		"""
		Поиск метаданных в локальном кэше по русскому или английскому названию.
		Позволяет избежать сетевых запросов к TVDB для новых раздач уже известных фильмов.
		"""
#		conn = sqlite3.connect(self.db_path, timeout=5)
		conn = self._get_connection()
		if not conn or not title_ru:
			return None
		try:
			cursor = conn.cursor()
			clean_name = title_ru or title_en
			search_ru = str(title_ru).strip().lower()
			search_en = str(title_en).strip().lower()

			# Строим запрос: ищем совпадение по имени (без учета регистра)
			# Если передан год, добавляем его в фильтрацию для точности (исключаем ремейки)
			if year and str(year).strip():
				cursor.execute('''
					SELECT link_id, tvdb_id, cover, plot, year, rating, title_ru, title_en
					FROM content_meta
					WHERE (LOWER(title_ru) = ? OR LOWER(title_en) = ?) AND year = ?
					LIMIT 1
				''', (search_ru, search_en, str(year).strip()))
			else:
				cursor.execute('''
					SELECT link_id, tvdb_id, cover, plot, year, rating, title_ru, title_en
					FROM content_meta
					WHERE LOWER(title_ru) = ? OR LOWER(title_en) = ?
					LIMIT 1
				''', (search_ru, search_en))

			row = cursor.fetchone()
			if row:
				xbmc.log(f"[NNM-DB-HIT] Фильм '{clean_name}' ({year}) найден в content_meta по имени! tvdb_id: {row['tvdb_id']}", level=xbmc.LOGINFO)
				return dict(row)
			return None
		except Exception as e:
			xbmc.log(f"[NNM-DB-ERROR] Ошибка поиска по имени '{clean_name}': {str(e)}", level=xbmc.LOGERROR)
			return None

	def link_topic_to_tvdb(self, topic_id, link_id):
		"""Быстрая привязка нового топика к уже существующему в базе фильму"""
		conn = self._get_connection()
		if not conn:
			return None
		try:
			cursor = conn.cursor()
			cursor.execute('''
				INSERT OR REPLACE INTO topic_linker (topic_id, link_id)
				VALUES (?, ?)
			''', (str(topic_id), int(link_id)))
			conn.commit()
			xbmc.log(f"[NNM-DB-LINK] Топик {topic_id} успешно привязан к существующему link_id {link_id}", level=xbmc.LOGINFO)
		except Exception as e:
			xbmc.log(f"[NNM-DB-ERROR] Ошибка линковки топика {topic_id}: {str(e)}", level=xbmc.LOGERROR)

	def link_topic_to_null(self, topic_id):
		"""Быстрая привязка нового топика к уже существующему в базе фильму"""
		conn = self._get_connection()
		if not conn: return
		try:
			cursor = conn.cursor()
			# TVDB ничего не нашел? Привязываем топик к системной заглушке
			cursor.execute('''
				INSERT OR REPLACE INTO topic_linker (topic_id, link_id)
				VALUES (?, -1)
			''', (str(topic_id),))
			conn.commit()
			xbmc.log(f"[NNM-DB-LINK] Топик {topic_id} успешно привязан к NULL объекту БД", level=xbmc.LOGINFO)
		except Exception as e:
			xbmc.log(f"[NNM-DB-ERROR] Ошибка линковки топика {topic_id}: {str(e)}", level=xbmc.LOGERROR)

	def relink_topic_group(self, old_link_id, current_topic_id, raw_json_dict):
		"""
		Каскадно перепривязывает ВСЮ ГРУППУ топиков (все релизы одного фильма),
		пострадавших от ошибочного группового сопоставления воркера.

		old_link_id: текущий (ошибочный) link_id, к которому воркер привязал группу топиков
		current_topic_id: ID топика, на котором пользователь вызвал ручное обновление
		raw_json_dict: Python-словарь с выбранным элементом от TVDB v4
		"""
		# --- Шаг 1: Парсинг метаданных из выбранного пользователем JSON ---
		# Забираем tvdb_id. Если вдруг поля 'tvdb_id' нет, страхуемся через 'id' (срезая префикс типа 'series-')
		tvdb_id = raw_json_dict.get('tvdb_id')
		if not tvdb_id and raw_json_dict.get('id'):
			tvdb_id = raw_json_dict['id'].split('-')[-1] # Превратит 'series-441093' в '441093'

		title_ru = raw_json_dict.get('name', 'Без названия')

		# Оригинальное название ищем в translations, если основного поля нет
		title_en = raw_json_dict.get('original_name', '')
		if not title_en and isinstance(raw_json_dict.get('translations'), dict):
			title_en = raw_json_dict['translations'].get('eng', '')

		# Картинку в базу пишем большую (image_url), а если её нет — берем миниатюру
		cover = raw_json_dict.get('image_url') or raw_json_dict.get('thumbnail', '')

		plot = raw_json_dict.get('overview', 'Описание отсутствует.')
		year = raw_json_dict.get('year', '0000')

		# Так как в поисковом ответе 'score' может отсутствовать, защищаем код от падения
		rating = float(raw_json_dict.get('score', 0.0)) if raw_json_dict.get('score') else 0.0

		# Упаковываем этот чистый объект в строку для вашей базы данных
		raw_json_str = json.dumps(raw_json_dict, ensure_ascii=False)

		# --- Шаг 2: Генерация НОВОГО уникального link_id через CRC32 ---
		# Всё так же хэшируем чистый tvdb_id (строку '441093'), как в save_poster_relational
		if tvdb_id:
			new_link_id = zlib.crc32(str(tvdb_id).encode('utf-8'))
		else:
			new_link_id = abs(hash(title_ru)) % (10 ** 8)

		xbmc.log(f"[NNM-DB] Запущена пакетная миграция релизов: старый линк {old_link_id} -> новый линк {new_link_id}", level=xbmc.LOGINFO)

#		# --- Шаг 3: Хирургическая изоляция топика ---
#		# Полностью отключаем массовый SELECT, чтобы случайно не угнать чужие года.
#		# Переносим СТРОГО один текущий топик, на котором пользователь вызвал меню.
#		topics_to_update = [str(current_topic_id)]

#		xbmc.log(f"[NNM-DB] Изолированный перенос топика {current_topic_id} на новый link_id. Остальные раздачи батча подхватит фоновый воркер по году.", level=xbmc.LOGINFO)

		# --- Шаг 3: Находим ВСЕ топики (все качества релиза), привязанные к этому ошибочному линку ---
		# Инициализируем список текущим топиком (на случай, если старая привязка отсутствовала / была -1)
		topics_to_update = [str(current_topic_id)]

#		if old_link_id and int(old_link_id) != -1:
#			try:
#				conn = self._get_connection()
#				if conn:
#					cursor = conn.cursor()
#					# Собираем абсолютно ВСЕ топики (например, все 7 версий «Люси»),
#					# которые воркер ошибочно посадил на эту карточку
#					cursor.execute("SELECT topic_id FROM topic_linker WHERE link_id = ?", (int(old_link_id),))
#					rows = cursor.fetchall()

#					# Корректно извлекаем строки из кортежей SQLite и убираем дубликаты
#					group_topics = [str(r[0]) for r in rows if r and r[0]]
#					if group_topics:
#						topics_to_update = list(set(group_topics + [str(current_topic_id)]))
#					conn.close()
#			except Exception as e:
#				xbmc.log(f"[NNM-DB-ERROR] Сбой сбора группы топиков для линка {old_link_id}: {str(e)}", level=xbmc.LOGERROR)

#		xbmc.log(f"[NNM-DB] Найдено {len(topics_to_update)} связанных релизов. Переносим всю группу на новый link_id.", level=xbmc.LOGINFO)

		# --- ШАГ 4: МГНОВЕННОЕ УНИЧТОЖЕНИЕ СТАРЫХ СВЯЗЕЙ И МУСОРНОЙ КАРТОЧКИ ---
		if old_link_id and int(old_link_id) != -1:
			try:
				conn = self._get_connection()
				if conn:
					cursor = conn.cursor()

					# А. Полностью выжигаем ВСЕ топики-пленники из старой ошибочной группы!
					# Это освобождает и 1994, и 2014 года для нового чистого авто-поиска воркера
					cursor.execute("DELETE FROM topic_linker WHERE link_id = ?", (int(old_link_id),))

					# Б. Намертво удаляем саму старую мусорную карточку из content_meta
					cursor.execute("DELETE FROM content_meta WHERE link_id = ?", (int(old_link_id),))

					conn.commit()
					conn.close()
					xbmc.log(f"[NNM-DB-CASCADING] Старая группа {old_link_id} полностью аннулирована. База очищена.", level=xbmc.LOGINFO)
			except Exception as e:
				xbmc.log(f"[NNM-DB-ERROR] Ошибка тотальной зачистки старого линка {old_link_id}: {str(e)}", level=xbmc.LOGERROR)

		# --- ШАГ 5: СОЗДАНИЕ ОПОРНОЙ ТОЧКИ ДЛЯ ВОРКЕРА ---
		# --- Шаг 4: Массовый перенос всей группы на новую правильную карточку контента ---
		# Вызываем ваш родной метод save_poster_relational для каждого топика из группы.
		# При первом проходе он создаст карточку в content_meta по new_link_id.
		# При остальных проходах — просто мгновенно обновит ссылки в topic_linker.
		for t_id in topics_to_update:
			self.save_poster_relational(
				topic_id=t_id,
				tvdb_id=tvdb_id,
				title_ru=title_ru,
				title_en=title_en,
				cover=cover,
				plot=plot,
				year=year,
				rating=rating,
				raw_json=raw_json_str
			)

		# --- Шаг 5: Полное уничтожение старой ошибочной карточки-сироты ---
		# Удаляем старую запись ТОЛЬКО если мы реально сменили фильм (new_link_id отличается от old_link_id)
		# и если старый ID не был дефолтной заглушкой отсутствия информации (-1).
		if old_link_id and int(old_link_id) != -1 and int(old_link_id) != int(new_link_id):
			try:
				conn = self._get_connection()
				if conn:
					cursor = conn.cursor()

					# Проверяем, не удерживает ли этот старый link_id кто-то ещё.
					# Так как на Шаге 4 мы эвакуировали абсолютно ВСЮ группу, COUNT(*) обязан быть равен 0.
					cursor.execute("SELECT COUNT(*) FROM topic_linker WHERE link_id = ?", (int(old_link_id),))
					count = cursor.fetchone()[0]

					if count == 0:
						cursor.execute("DELETE FROM content_meta WHERE link_id = ?", (int(old_link_id),))
						xbmc.log(f"[NNM-DB] Очистка завершена. Старая ошибочная карточка {old_link_id} удалена из content_meta.", level=xbmc.LOGINFO)
					else:
						xbmc.log(f"[NNM-DB-WARNING] Старый link_id {old_link_id} неожиданно удерживается ещё {count} топиками. Удаление отменено.", level=xbmc.LOGWARNING)

					conn.commit()
					conn.close()
			except Exception as e:
				xbmc.log(f"[NNM-DB-ERROR] Ошибка при удалении осиротевшей карточки {old_link_id}: {str(e)}", level=xbmc.LOGERROR)

		return len(topics_to_update)

	# =====================================================================
	# ВАШ НОВЫЙ ИЗОЛИРОВАННЫЙ БЛОК УПРАВЛЕНИЯ ИСТОРИЕЙ ПОИСКА В SQLITE
	# =====================================================================
	def get_history(self):
		"""
		Извлекает полный список сохраненных поисков.
		Возвращает список кортежей вида: [(текст, id_раздела), ...]
		"""
		conn = self._get_connection()
		if not conn:
			return None
		try:
#			conn = sqlite3.connect(self.db_path, timeout=5)
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

		conn = self._get_connection()
		if not conn:
			return None
		try:
#			conn = sqlite3.connect(self.db_path, timeout=5)
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

	def delete_from_view_history(self, topic_id):
		"""Полностью очищает таблицу истории"""
		try:
			conn = sqlite3.connect(self.db_path, timeout=5)
			cursor = conn.cursor()
			cursor.execute('''
				DELETE FROM view_history WHERE topic_id=?
			''', (topic_id,))
			conn.commit()
			conn.close()
		except Exception as e:
			xbmc.log(f"[NNM-DB-HISTORY-ERROR] Ошибка очистки истории просмотра: {str(e)}", level=xbmc.LOGERROR)

	def save_to_view_history(self, topic_id, download_id, title):
		"""
		Минималистичное сохранение топика в историю просмотров SQLite.
		Использует встроенный CURRENT_TIMESTAMP. При дубликатах обновляет время,
		чтобы последний просмотренндй топик всегда поднимался на самый верх.
		"""
		conn = self._get_connection()
		if not conn:
			return None
		try:
			# 2. Перезаписываем топик. Встроенный CURRENT_TIMESTAMP обновится автоматически!
#			conn = sqlite3.connect(self.db_path, timeout=5)
			cursor = conn.cursor()
			xbmc.log(f"[DB-HISTORY] Сохраняем запись истории: {str(title)}, t:{str(topic_id)}, d:{download_id}", level=xbmc.LOGINFO)
			cursor.execute('''
				INSERT OR REPLACE INTO view_history (topic_id, title, dnld_id, view_time)
				VALUES (?, ?, ?, CURRENT_TIMESTAMP)
			''', (str(topic_id), str(title), str(download_id)))

			# Авто-чистка: держим в базе строго 15 последних
			cursor.execute('''
				DELETE FROM view_history WHERE (topic_id, title) NOT IN (
					SELECT topic_id, title FROM view_history ORDER BY view_time DESC LIMIT 20
				)
			''')

			conn.commit()
			conn.close()
		except Exception as e:
			xbmc.log(f"[DB-HISTORY-ERROR] Ошибка записи истории: {str(e)}", level=xbmc.LOGERROR)

	def get_view_history(self):
		"""
		Извлекает полный список сохраненных поисков.
		Возвращает список кортежей вида: [(текст, id_раздела), ...]
		"""
		conn = self._get_connection()
		if not conn:
			return None
		try:
#			conn = sqlite3.connect(self.db_path, timeout=5)
			cursor = conn.cursor()
			# Забираем текст и привязанный ID форума, сортируя по свежести времени
			cursor.execute("SELECT title, topic_id, dnld_id FROM view_history ORDER BY view_time DESC LIMIT 20")
			rows = cursor.fetchall()
			conn.close()
			return rows # Возвращает [(query, forum_id), (query, forum_id), ...]
		except Exception as e:
			xbmc.log(f"[NNM-DB-VIEW-HISTORY-ERROR] Ошибка чтения истории просмотра: {str(e)}", level=xbmc.LOGERROR)
			return []
