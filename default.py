#!/usr/bin/python
# -*- coding: utf-8 -*-
import sys, os, time, re, ast
import xbmc, xbmcgui, xbmcplugin, xbmcaddon
import ssl
import json
import nnm_api, categories
import threading  # Встроенная библиотека Python для запуска фоновых потоков
import urllib.parse
from urllib.parse import quote, unquote, urlencode # Подключаем стандартные библиотеки Python 3 для работы с URL
import urllib.request as urllib2
import requests
from bs4 import BeautifulSoup
import cache_db  # Импортируем наш новый созданный класс базы данных кэша

try:
	import tvdb_v4_official
	xbmc.log("[NNM-TVDB] Модуль tvdb_v4_official успешно импортирован скриптом.")
except Exception as imp_err:
	tvdb_v4_official = None
	xbmc.log(f"[NNM-TVDB-ERROR] Не удалось импортировать tvdb_v4_official.py! Ошибка: {str(imp_err)}", level=xbmc.LOGERROR)

# =====================================================================
# ШАГ 1: ГЛОБАЛЬНЫЕ ОБЪЕКТЫ ЯДРА KODI И ОБХОД СЕРТИФИКАТОВ UBUNTU
# =====================================================================
os.environ['PYTHONHTTPSVERIFY'] = '0'
ubuntu_certs_path = '/etc/ssl/certs/ca-certificates.crt'
if os.path.exists(ubuntu_certs_path):
	os.environ['REQUESTS_CA_BUNDLE'] = ubuntu_certs_path
	os.environ['CURL_CA_BUNDLE'] = ubuntu_certs_path

# =====================================================================

addon = xbmcaddon.Addon(id='plugin.video.nnmclub')
__settings__ = xbmcaddon.Addon(id='plugin.video.nnmclub')

PLUGIN_NAME = 'NNM-Club'
ADDON_URL = sys.argv[0]         # Наш базовый адрес 'plugin://...'
ADDON_HANDLE = int(sys.argv[1]) # Наш готовый числовой хэндл окна для вывода

# =====================================================================
# ШАГ 2: ВЫНОСИМ ФУНКЦИЮ ЛОГИРОВАНИЯ НАВЕРХ (ЗАЩИТА ОТ NAMEERROR)
# =====================================================================
def log_debug(msg, level=xbmc.LOGINFO):
	"""Универсальная функция логирования, доступная теперь всему файлу сверху вниз"""
	if __settings__.getSetting("debug") == 'true' or level == xbmc.LOGERROR:
		try:
			if not isinstance(msg, str):
				msg = repr(msg)
			xbmc.log(f"[NNM-DEBUG] {msg}", level=level)
		except:
			pass

log_debug(f"ADDON_URL: {ADDON_URL}")
log_debug(f"ADDON_HANDLE: {ADDON_HANDLE}")

# =====================================================================
# ШАГ 3: ИНИЦИАЛИЗАЦИЯ ПЕРЕМЕННЫХ И ИНСТАНСОВ КЛАССОВ (БЕЗ ДУБЛЕЙ)
# =====================================================================
# Инициализируем класс кэша БД строго один раз, передавая объект addon
_db_cache_instance = cache_db.PosterCacheDB(addon)

# Глобальный объект для удержания сессии API на время работы аддона
_nnm_api_instance = None

# Инициализируем пустые глобальные переменные для фонового потока
_async_fetch_queue = []
_is_bg_thread_running = False

# Базовые системные пути к ресурсам графики
icon = os.path.join(addon.getAddonInfo('path'), 'icon.png')
thumb = os.path.join(addon.getAddonInfo('path'), "icon.png")
fanart = os.path.join(addon.getAddonInfo('path'), "fanart.jpg")
# Динамически определяем протокол на основе вашего enum "protokol"
site_protocol_index = int(__settings__.getSetting('protokol'))
chosen_protocol = "http://" if site_protocol_index == 0 else "https://"
# Чистый адрес зеркала трекера с учетом выбранного пользователем протокола
site_url_base = f"{chosen_protocol}{__settings__.getSetting('url').strip()}"

# Передаем контент и хэндл
xbmcplugin.setContent(ADDON_HANDLE, 'movies')

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def get_params():
	# Если параметров нет (самый первый запуск), sys.argv[2] будет пустой строкой
	paramstring = sys.argv[2].lstrip('?')
	if not paramstring:
		return {}

	# ИСПОЛЬЗУЕМ СТАНДАРТНУЮ БИБЛИОТЕКУ PYTHON
	# dict(urllib.parse.parse_qsl(...)) мгновенно собирает идеальный плоский словарь
	# Она сама правильно раскодирует пробелы из "+" обратно в " " и поймет любые спецсимволы!
	try:
		return dict(urllib.parse.parse_qsl(paramstring))
	except:
		return {}

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def inputbox(msg='Поиск:'):
	skbd = xbmc.Keyboard()
	skbd.setHeading(msg)
	skbd.doModal()
	if skbd.isConfirmed():
		SearchStr = skbd.getText()
		return SearchStr
	else:
		return ""

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def showMessage(heading, message, times=3000):
	# Используем официальное API Kodi.
	# Вместо thumb подставляем icon (логотип вашего аддона, который у вас точно объявлен в шапке)
	# xbmcgui.NOTIFICATION_INFO задает красивую иконку (синий кружок с буквой "i")
	try:
		xbmcgui.Dialog().notification(heading, message, xbmcgui.NOTIFICATION_INFO, times)
	except Exception as e:
		log_debug(f"(showMessage) Сбой вывода уведомления: {str(e)}")

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

# Глобальный объект для удержания сессии API на время работы аддона
_nnm_api_instance = None

def GET_NNM(url):

	global _nnm_api_instance
	# Инициализируем сессию через ваш класс API строго ОДИН раз за запуск аддона
	if _nnm_api_instance is None:
#		log_debug("(GET_NNM) Первичная инициализация глобального инстанса NNMClubAPI...")
		# ДИНАМИЧЕСКИЙ СБОР СЛОВАРЯ ПРОКСИ ИЗ НАСТРОЕК GUI KODI
		LOCAL_PROXY = {}
		if __settings__.getSetting("use_proxy") in [True, "true"]:
			proxy_address = __settings__.getSetting('proxy').strip().replace(" ", "")
			if proxy_address:
				# Вычищаем случайные префиксы, если пользователь скопировал их из браузера
				if "://" in proxy_address:
					proxy_address = proxy_address.split("://")[-1]
				# Читаем тип прокси (0=HTTP, 1=HTTPS, 2=SOCKS4, 3=SOCKS5)
				proxy_type_index = int(__settings__.getSetting('proxy_type'))
				proxy_prefixes = ["http://", "https://", "socks4://", "socks5://"]
				chosen_prefix = proxy_prefixes[proxy_type_index]
				# Собираем полный адрес прокси-сервера
				full_proxy_url = f"{chosen_prefix}{proxy_address}"
				# Получаем протокол работы самого аддона с сайтом трекера (0=HTTP, 1=HTTPS)
				site_protocol_index = int(__settings__.getSetting('protokol'))
				site_protocol_key = "http" if site_protocol_index == 0 else "https"
				# ЖЕСТКАЯ СВЯЗКА: Привязываем транспорт строго к целевому ключу трафика сайта.
				# Если выбран HTTPS-прокси (Strict TLS, индекс 1) или SOCKS (индексы 2 и 3),
				# requests безопасно проглотит схему и откроет сокет-туннель.
				LOCAL_PROXY = {
					site_protocol_key: full_proxy_url
				}
#				log_debug(f"(GET_NNM) ДИНАМИЧЕСКИЙ ПРОКСИ ПОДКЛЮЧЕН К СЕССИИ: {LOCAL_PROXY}")

		# =====================================================================
		# БЛОК ИНЖЕКЦИИ ПАСПОРТА СЕССИИ НАПРЯМУЮ ИЗ НАСТРОЕК GUI KODI
		# =====================================================================
		# Читаем токен обхода Cloudflare напрямую из текстового поля настроек аддона
		cf_token = __settings__.getSetting('cookie_cf').strip().replace(" ", "")
		# Читаем токен сессии авторизованного пользователя трекера напрямую из настроек
		# В вашей архитектуре класса NNMClubAPI этот токен передается в параметр sid_token,
		# а внутри класса он автоматически разворачивается в полноценный куки-набор
		sid_token = __settings__.getSetting('cookie_t').strip().replace(" ", "")
		# Логируем факт извлечения токенов в системный лог для контроля отладки
#		log_debug(f"(GET_NNM) Куки из GUI подтянуты. CF: {cf_token[:10]}..., SID: {sid_token[:10]}...")
		# 2. Передаем данные в ВАШ конструктор класса NNMClubAPI
		# В base_url улетает site_url_base (динамический HTTPS/HTTP адрес зеркала)
		# В proxy улетает LOCAL_PROXY (наш новый собранный Strict TLS словарь)
		_nnm_api_instance = nnm_api.NNMClubAPI(base_url=site_url_base, cf_token=cf_token, sid_token=sid_token, proxy=LOCAL_PROXY)

	try:
		# Разбираем входящий URL на части для анализа параметров
		parsed_url = urllib.parse.urlparse(url)
		query_params = urllib.parse.parse_qs(parsed_url.query)
		# Пытаемся вытащить ID темы (t), если он присутствует в запросе
		topic_id = query_params.get('t', [''])[0]
		# Сценарий 1: Запрос списка файлов (AJAX)
		if "filelst.php" in url:
			if topic_id:
				_nnm_api_instance.session.headers['Referer'] = f"{site_url_base}/forum/viewtopic.php?t={topic_id}"
			else:
				# Если вдруг ID темы не передали, берем ID вложения (attach_id) для подстраховки
				attach_id = query_params.get('attach_id', [''])[0]
				_nnm_api_instance.session.headers['Referer'] = f"{site_url_base}/forum/viewtopic.php?p={attach_id}"
		# Сценарий 2: Скачивание самого торрент-файла (вложения)
		elif "download.php" in url:
			# Для download.php трекер обычно требует верификацию, что мы качаем из конкретного топика
			if topic_id:
				_nnm_api_instance.session.headers['Referer'] = f"{site_url_base}/forum/viewtopic.php?t={topic_id}"
			else:
				# На NNM-Club торренты иногда качаются по id вложения (?id=XXXXXX)
				# В таком случае хорошим тоном считается сослаться на главную форума или оставить пустой домен топика
				_nnm_api_instance.session.headers['Referer'] = f"{site_url_base}/forum/index.php"
		# Сценарий 3: Все остальные запросы (главная, разделы, поиск)
		else:
			_nnm_api_instance.session.headers['Referer'] = f"{site_url_base}/forum/index.php"
#		# Динамически подменяем Referer на лету перед каждым GET-запросом
#		if "filelst.php" in url:
#			_nnm_api_instance.session.headers['Referer'] = url.replace("filelst.php", "viewtopic.php")
#		else:
#			_nnm_api_instance.session.headers['Referer'] = f"{site_url_base}/forum/index.php"
#		log_debug(f"(GET_NNM) Отправка запроса через сессию API: {url}")
		# Если в вашем классе nnm_api.py в __init__ прописано self.session.proxies = proxy,
		# то данный вызов гарантированно пойдет через прокси-сервер!
		response = _nnm_api_instance.safe_get(url, timeout=15)
		if response is None or response.status_code != 200:
#			log_debug(f"(GET_NNM) Критическая ошибка сети в GET_NNM: Сервер вернул пустой ответ для {url}", level=xbmc.LOGERROR)
			return ''
		# Декодируем windows-1251 контент сайта
		html_text = response.content.decode('windows-1251', errors='ignore')
		# Проверка на срабатывание защитной заглушки самого сайта
		if "LITTLE GUYS ESTIMATOR" in html_text or "estimator" in html_text.lower():
#			log_debug("(GET_NNM) КРИТИЧЕСКАЯ ОШИБКА: Запрос заблокирован защитой LGE! Обновите cookies.txt.", level=xbmc.LOGERROR)
			showMessage('NNM-Club', 'Защита LGE заблокировала запрос. Обновите куки.', 5000)
			return ''
		return html_text

	except Exception as e:
#		log_debug(f"(GET_NNM) Критическая ошибка сети в GET_NNM: {str(e)}", level=xbmc.LOGERROR)
		showMessage('Ошибка сети', str(e), 4000)
		return ''

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def Root():

	AddItem("Search", {'display_title': "[B][COLOR lime][ Поиск ][/COLOR][/B]"})

	# Вызываем AddItem с техническим режимом, но в AddItem мы перехватим и выставим is_folder=False
	AddItem("ActorSearch", {'display_title': "[B][COLOR cyan][ Поиск по актерам ][/COLOR][/B]"})

	if __settings__.getSetting("cat_inroot") == 'false':
		AddItem("Category", {'display_title': "[B][COLOR lime][ Разделы форума ][/COLOR][/B]", 'forum_id': '1'})

	# Проверяем, включена ли галочка сохранения истории в настройках GUI Kodi
	if __settings__.getSetting("search_hist") == 'true':
		if __settings__.getSetting("cat_inroot") == 'true':
			AddItem("History", {'display_title': "[B][COLOR lime][ История поиска ][/COLOR][/B]"})
		else:
			AddItem("History", {'display_title': "[B][COLOR silver] История поиска [/COLOR][/B]"})

	# Проверяем, включена ли галочка сохранения истории в настройках GUI Kodi
	if __settings__.getSetting("view_hist") == 'true':
		if __settings__.getSetting("cat_inroot") == 'false':
			for item in _db_cache_instance.get_view_history():
				pretty_dict = json.dumps(item, indent=4, ensure_ascii=False)
#				log_debug(f"item is : {pretty_dict}")
				info = {
					'title': item[0],
					'topic_id': item[1],
					'dwnld_id': item[2],
					'view_hist': '1'
				}
				resolution_tag = get_label(item[0])
				AddItem("Topic", {'display_title': f"{resolution_tag} | {item[0]}",'topic_id': item[1],'dwnld_id': item[2],'info': info})
		else:
			AddItem("ViewHistory", {'display_title': "[B][COLOR lime][ История просмотра ][/COLOR][/B]"})

	if __settings__.getSetting("cat_inroot") == 'true':
		for cat in categories.MAIN_CATEGORIES:
			AddItem("Category", {'display_title': f"[B][COLOR white]{cat['title']}[/COLOR][/B]",'forum_id': cat['id']}) # 1й уровень вложения

	xbmcplugin.setPluginCategory(ADDON_HANDLE, PLUGIN_NAME)
	xbmcplugin.endOfDirectory(ADDON_HANDLE)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

#def AddItem(title = "", mode = "", topic_id='0', url='', inf={}):
#def AddItem(mode="", **params):
def AddItem(mode='', item_data={}):

	pretty_dict = json.dumps(item_data, indent=4, ensure_ascii=False)
	log_debug(f"(AddItem) Точка входа mode: {mode} , item_data: {pretty_dict}")

	# --- ШАГ 1: Извлекаем параметры по их явным именам (с дефолтными значениями) ---
	display_title = str(item_data.get('display_title', ''))

#	log_debug(f"(AddItem) Экран: '{display_title[:35]}...'")

	# Инициализируем базовые переменные заглушек до проверки кэша
	cover = icon  # По умолчанию выставляем стандартную иконку плагина (заглушку)
	plot_desc = "..:: Нет описания ::.."
	prod_year = ""
	tvdb_rating = 0.0
	link_id = 0

	listitem = xbmcgui.ListItem(display_title)

	# ЖЕСТКАЯ СЕЛЕКТИВНАЯ ПРИВЯЗКА: Работаем строго с топиками фильмов/сериалов
	if mode == "Topic":

		# Собираем чистый словарь параметров
		url_params = {
			'mode': mode,
		}
		clean_ru = str(item_data.get('title_ru', ''))
		if clean_ru != '':
			url_params['clean_ru'] = clean_ru

		clean_en = str(item_data.get('title_en', ''))
		if clean_en != '':
			url_params['clean_en'] = clean_en

		topic_id = str(item_data.get('topic_id', '0'))
		if topic_id != '0':
			url_params['topic_id'] = topic_id

		forum_id = str(item_data.get('forum_id', '-1'))
		if forum_id != '-1':
			url_params['forum_id'] = forum_id

		dwnld_id = str(item_data.get('dwnld_id', '0'))
		if dwnld_id != '0':
			url_params['dwnld_id'] = dwnld_id

		info = item_data.get('info',{})
		if isinstance(info, str) and info.strip() != "":
			try:
				# Распаковываем текстовое представление словаря обратно в объект dict
				info = eval(info)
			except Exception:
				info = {} # Заглушка на случай критического сбоя парсинга строки

		if not isinstance(info, dict):
			info = {}

		if info != {}:
			url_params['info'] = info


		if topic_id != '0':

#			log_debug(f"(AddItem) Элемент признан раздачей (mode:{mode}, ID:{topic_id}). Проверяем локальный SQLite.")

			# 1. Сначала стандартно проверяем по ID топика через LEFT JOIN
			movie_meta = _db_cache_instance.get_poster(topic_id)

			if not movie_meta:
				# 2. Если по топику пусто, вычищаем имя/год и ищем карточку по ИМЕНИ фильма!
				year_match = re.search(r'(19\d{2}|20\d{2})', str(display_title))
				year_val = year_match.group(1) if year_match else ""

				# Запускаем наш новый метод
				movie_meta = _db_cache_instance.get_poster_by_name(clean_ru, clean_en, year_val)

				if movie_meta:
					# Нашли! Моментально привязываем этот новый топик к старому фильму в SQLite,
					# чтобы при следующем открытии страницы LEFT JOIN сработал мгновенно.
					_db_cache_instance.link_topic_to_tvdb(topic_id, movie_meta['link_id'])

			if movie_meta:
				# КЭШ СРАБОТАЛ (либо по ID, либо по имени)!
				cover = movie_meta.get('cover')
				plot_desc = movie_meta.get('plot', '')
				prod_year = movie_meta.get('year', '')
				tvdb_rating = movie_meta.get('rating', 0.0)
				link_id = movie_meta.get('link_id')
				# ... выводим элемент с красивой обложкой ...
			else:
				# Полный промах кэша — элемент уйдет в пакетную очередь на скачивание
				pass

#			log_debug(f"(AddItem) Найдено в cache.db для ID {topic_id}! Постер: {cover[:40]}... | Рейтинг: {tvdb_rating}")

			fanart = cover

			# Накатываем все собранные переменные (или данные из БД, или заглушки) в инфо-пакет Kodi
			info_labels = {
				'title': info.get('title'),
				'plot': plot_desc,
				'year': int(prod_year) if (prod_year and prod_year.isdigit()) else 0,
				'rating': float(tvdb_rating) if tvdb_rating else 0.0,
				'mediatype': 'movie',
				'genre': 'NNM-Club Торрент'
			}

			pretty_dict = json.dumps(info_labels, indent=4, ensure_ascii=False)
#			log_debug(f"(AddItem) info_labels = : {info_labels}")
			view_hist = info.get('view_hist', 0)

			if link_id != 0:
				context_menu = []
				title = info.get('title')
				purl = f"{ADDON_URL}?mode=TvDB&topic_id={str(topic_id)}&dwnld_id={str(link_id)}&title={str(title)}"
				context_menu.append(('Обновить обложку', f"RunPlugin({purl})"))
				# Флаг True очистит стандартный мусор Kodi и покажет только ваш пункт
				listitem.addContextMenuItems(context_menu) #, replaceItems=True)

				if view_hist != 0:
					purl = f"{ADDON_URL}?mode=DeleteView&topic_id={str(topic_id)}"
					context_menu.append(('Удалить из списка', f"RunPlugin({purl})"))
					# Флаг True очистит стандартный мусор Kodi и покажет только ваш пункт
					listitem.addContextMenuItems(context_menu) #, replaceItems=True)



			listitem.setProperty('IsPlayable', 'false')
			is_folder = True

			try: listitem.setArt({ 'poster': cover, 'fanart' : fanart, 'thumb': cover, 'icon': cover})
			except: pass

			listitem.setInfo(type="Video", infoLabels=info_labels)

		else:
			# Если это папка раздела, кнопка истории или пагинация
#			log_debug(f"(AddItem) Пропуск конвейера TVDB (Системный элемент). Mode: {mode}, ID: {topic_id}")
			cover = icon
			plot_desc = ""


	elif mode in ["Category", "SubCategory", "NextSearch", "NextList"]:
		# Собираем чистый словарь параметров
		url_params = {
			'mode': mode,
		}
		query = str(item_data.get('query', ''))
		if query != '':
			url_params['query'] = query
		forum_id = str(item_data.get('forum_id', '-1'))
		if forum_id != '-1':
			url_params['forum_id'] = forum_id
		search_id = str(item_data.get('search_id', ''))
		if search_id:
			url_params['search_id'] = search_id
		offset = str(item_data.get('offset', '0'))
		if offset:
			url_params['offset'] = offset
		info = item_data.get('info',{})
		if info != {}:
			url_params['info'] = info
		is_folder = True

	elif mode in ["Search", "SearchInCategory"]:
		# Собираем чистый словарь параметров
		url_params = {
			'mode': mode,
		}
		query = str(item_data.get('query', ''))
		if query != '':
			url_params['query'] = query
		forum_id = str(item_data.get('forum_id', '-1'))
		if forum_id != '-1':
			url_params['forum_id'] = forum_id
		info = item_data.get('info',{})
		if info != {}:
			url_params['info'] = info
		is_folder = False

	elif mode == "ActorSearch":
		url_params = {
			'mode': mode
		}
		is_folder = False # КНОПКА ЯВЛЯЕТСЯ ИСПОЛНЯЕМЫМ ФАЙЛОМ! Клик не создаст пустую папку.

	else:
		# Собираем чистый словарь параметров
		url_params = {
			'mode': mode,
		}
		is_folder = True

	# МАГИЯ ОДНОЙ СТРОКОЙ: urlencode сам всё закодирует и склеит амперсандами!
	purl = f"{ADDON_URL}?{urlencode(url_params)}"

	log_debug(f"(AddItem) xbmcgui.ListItem Показываем элемент {display_title} в списке.")
	log_debug(f"(AddItem) Выход из AddItem. mode: {mode} , purl: {purl}")

	xbmcplugin.addDirectoryItem(ADDON_HANDLE, purl, listitem, is_folder)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def Category(forum_id):
	# Выводим кнопку локального поиска на экране Kodi самым первым пунктом
	# Передаем специальный режим "SearchInCategory" и ID текущего форума
	if forum_id != '1':
		if __settings__.getSetting("cat_search") == 'true':
			AddItem("SearchInCategory", {'display_title': "[B][COLOR lime][ Поиск в этом разделе ][/COLOR][/B]",'forum_id': forum_id})

	if forum_id == '1':
		# Читаем главные папки из нашего нового файла
		for cat in categories.MAIN_CATEGORIES:
			# Если у папки есть прописанные подкатегории в SUB_CATEGORIES, открываем её как подменю
			if cat['id'] in categories.SUB_CATEGORIES:
				# 1й уровень вложения
				AddItem("Category", {'display_title': f"[B][COLOR white]{cat['title']}[/COLOR][/B]",'forum_id': cat['id']})
			else:
				# 2й уровень вложения
				AddItem("Category", {'display_title': f"[B][COLOR green]{cat['title']}[/COLOR][/B]",'forum_id': cat['id']})


	elif forum_id in categories.SUB_CATEGORIES:
		# Читаем вложенные папки для выбранного parent_id
		for sub in categories.SUB_CATEGORIES[forum_id]:
			# 3й уровень вложения
			AddItem("SubCategory", {'display_title': f"[COLOR silver]{sub['title']}[/COLOR]",'forum_id': sub['id']})

	xbmcplugin.setPluginCategory(ADDON_HANDLE, PLUGIN_NAME)
	xbmcplugin.endOfDirectory(ADDON_HANDLE)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def List(forum_id, search_id=None, offset=0):

#	log_debug(f"(List) Листинг содержимого раздела id: {forum_id}, смещение: {offset}")

	# Получаем данные через ваш оптимизированный метод
	entries = GetEntries('', forum_id=forum_id, search_id=search_id, offset=offset)

#	log_debug(f"(List) вернул подтвержденных тем с MAGNET-ссылками: {len(entries)}")

	offset = entries.get('offset')
	search_id = entries.get('search_id')

	# Отрисовываем список на экране Kodi
	#ShowEntries(entries, is_search=False)
	ShowEntries(entries.get('topics_list', []), is_search=False)

#	if len(entries) >= 40:
#		offset = offset + 50

	if offset != '0':
		# Собираем чистый словарь параметров
		item_data = {
			'display_title': "[B][COLOR gold][ Следующая страница >>> ][/COLOR][/B]",
			'topic_id': '0',
		}
		forum_id = str(item_data.get('forum_id', '-1'))
		if forum_id != '-1':
			item_data['forum_id'] = forum_id
		if search_id:
			item_data['search_id'] = search_id
		item_data['offset'] = offset

		AddItem("NextList", item_data)

	xbmcplugin.setContent(ADDON_HANDLE, 'movies') # Объявляем, что в этой папке лежат Фильмы
	xbmcplugin.endOfDirectory(ADDON_HANDLE)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def Search(query='', forum_id=-1, search_id=None, offset=0):
	# --- Дальнейший код выполнится, ТОЛЬКО если текст железно есть ---
#	log_debug(f"(Search) Запуск поиска: {t}, смещение: {offset}")

	if __settings__.getSetting("search_hist") == 'true' and offset == 0:
		try:
			_db_cache_instance.add_history(query, forum_id)
		except Exception as e:
			log_debug(f"(Search) Ошибка записи в историю SQLite: {str(e)}", level=xbmc.LOGERROR)

	# Приводим forum_id к нужному для API виду
	forum_id = None if forum_id == -1 else forum_id

#	log_debug(f"(Search) Запуск GetEntries: {t}, раздел: {forum_id} смещение: {offset}")

	# Получаем данные через ваш оптимизированный метод
	entries = GetEntries(query, forum_id=forum_id, search_id=search_id, offset=offset)

#	log_debug(f"(Search) вернул подтвержденных тем с MAGNET-ссылками: {len(entries)}")

	offset = entries.get('offset')
	search_id = entries.get('search_id')

	# Отрисовываем список на экране Kodi
	ShowEntries(entries.get('topics_list', []), is_search=True)

#	if len(entries) >= 40:
#		next_start = offset + 50

	if offset != '0':

		item_data = {
			'display_title': "[B][COLOR gold][ Следующая страница >>> ][/COLOR][/B]",
			'topic_id': '0',
		}
		if query != '':
			item_data['query'] = query
		forum_id = str(item_data.get('forum_id', '-1'))
		if forum_id != '-1':
			item_data['forum_id'] = forum_id
		if search_id:
			item_data['search_id'] = search_id
		item_data['offset'] = offset

		AddItem("NextSearch", item_data)

	xbmcplugin.setContent(ADDON_HANDLE, 'movies') # Объявляем, что в этой папке лежат Фильмы
	xbmcplugin.endOfDirectory(ADDON_HANDLE)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def GetEntries(query, forum_id=None, search_id=None, offset=0):

	# =====================================================================
	# ЖЕЛЕЗНАЯ ОЧИСТКА FORUM_ID ОТ ЛЮБЫХ СПИСКОВ И МУСОРА KODI
	# =====================================================================
	if forum_id is not None:
		# Если это реальный Python-список, берем первый элемент
		if isinstance(forum_id, list) and len(forum_id) > 0:
			forum_id = forum_id[0]

		# Переводим в строку и вычищаем физические символы квадратных скобок и кавычек,
		# если они прилетели внутри строки из sys.argv
		forum_id_str = str(forum_id).replace('[', '').replace(']', '').replace("'", "").replace('"', '').strip()
	else:
		forum_id_str = "-1"

	encoded_query = urllib.parse.quote(query)
	f_param = "f=-1"

	# Теперь проверяем чистую, гарантированную строку (например, "224")
	if forum_id_str and forum_id_str != "-1":

		# Бьем напрямую по словарю из categories.py
		if hasattr(categories, 'SUB_CATEGORIES') and forum_id_str in categories.SUB_CATEGORIES:
			# Вытаскиваем все дочерние ID
			sub_ids = [str(sub['id']).strip() for sub in categories.SUB_CATEGORIES[forum_id_str]]

			# Собираем гирлянду: f[]=225&f[]=226&f[]=227...
			f_param = "&".join([f"f[]={sid}" for sid in sub_ids])
#			log_debug(f"(GetEntries) Раскрыт 1-й уровень ({forum_id_str}). Подкатегорий: {len(sub_ids)}")
		else:
			# Обычный 2-й уровень (чистая строка без мусора)
			f_param = f"f[]={forum_id_str}"

	sd_param = ""
	if __settings__.getSetting("hide_zero") == 'true':
		sd_param = "&sd=1"




	# Итоговый URL торрент-поисковика
	if search_id:
		entries_url = f"{site_url_base}/forum/tracker.php?{f_param}&nm={encoded_query}&o=10&s=2{sd_param}&search_id={search_id}&start={offset}"
	else:
		entries_url = f"{site_url_base}/forum/tracker.php?{f_param}&nm={encoded_query}&o=10&s=2{sd_param}&start={offset}"

	results = []

	try:
#		log_debug(f"(GetEntries) Отправка полностью авторизованного запроса: {entries_url}")

		# Запрос через вашу безопасную GET_NNM
		html = GET_NNM(entries_url)

		if not html:
#			log_debug("(GetEntries) Ошибка: GET_NNM вернул пустой HTML", level=xbmc.LOGERROR)
			return []

		# =====================================================================
		# ЭТАЛОННАЯ РЕГУЛЯРКА СТРОКИ СЛОВАРЯ ПОИСКА (ПОД ВАШ HTML)
		# =====================================================================
		# Ищет начало строки таблицы prow1 или prow2
		# Группа 1: (\d+) -> ID темы (1780816)
		# Группа 2: (.*?) -> Название раздачи (Асока / Ahsoka...)
		# Группа 3: (\d+) -> ID скачивания (1358702)
		# Группа 4: (.*?) -> Читаемый размер файла (24.4 GB)
		# Группа 5: (\d+) -> Количество сидов (8)
		# Флаг re.DOTALL позволяет точке "." съедать переносы строк внутри ячеек <td>
		row_pattern = re.compile(
			r'<tr[^>]*class="prow\d+".*?'
			r'href="viewtopic\.php\?t=(\d+)"[^>]*>(.*?)</a>.*?'
			r'href="download\.php\?id=(\d+)".*?'
			r'</u>\s*([^<]+).*?'
			r'class="seedmed"[^>]*><b>(\d+)</b>',
			re.DOTALL | re.IGNORECASE
		)

		# Находим все стопроцентные совпадения строк на странице
		matches = row_pattern.findall(html)

#		log_debug(f"(GetEntries) Сквозной парсинг строк prow. Успешно собрано чистых тем: {len(matches)}")

		# Ищем ссылку "След.", забирая и search_id, и start
		next_page_match = re.search(r'href="[^"]*?search_id=(\d+)[^"]*?start=(\d+)[^"]*?">След\.', html)

		if next_page_match:
			search_id = next_page_match.group(1) # Заберет '205904612'
			offset    = next_page_match.group(2) # Заберет '50'
			log_debug(f"[NNM-PAGINATION] Из HTML трекера успешно извлечен следующий offset: {offset} search_id : {search_id}")
		else:
			search_id = None
			offset = '0'
			log_debug("[NNM-PAGINATION] Следующая страница не найдена (пользователь на последней странице).")

		for topic_id, title, dwnld_id, size_text, seeds in matches:
			try:
				# Очищаем название от внутренних HTML-тегов (<b>, <span> и т.д.)
				clean_title = re.sub(r'<[^>]+>', '', title).strip()

				# Отсекаем служебные навигационные ссылки форума
				if not clean_title or any(x in clean_title for x in ["Темы", "Сообщения", "Автор", "Последнее", "След.", "Пред."]):
					continue

				# Вычищаем пробелы и лишние символы из строки размера
				clean_size = size_text.replace('&nbsp;', ' ').strip()

				# Наполняем словарь результатов. Все данные жестко привязаны к одной строке,
				# сдвиги индексов или перепутывание фильмов теперь исключены физически!
				results.append({
					'title': clean_title,
					'topic_id': topic_id,
					'dwnld_id': dwnld_id,          # Числовой ID файла скачивания для Topic
					'size': clean_size,       # Строка размера (например, "24.4 GB")
					'seeds': seeds.strip()     # Количество сидов
				})
			except:
				continue

		return {'offset': offset, 'search_id': search_id, 'topics_list': results }

	except Exception as e:
		log_debug(f"(GetEntries) Ошибка сети при поиске: {str(e)}", level=xbmc.LOGERROR)
		return []



#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def ShowEntries(entries, is_search=False):

	"""Простой и стабильный вывод списка топиков на экран Kodi"""
	if not entries:
#		log_debug("(ShowEntries) Список топиков пуст. Нечего отображать.")
		# Можно вывести уведомление Kodi, что ничего не найдено
		return

	# =====================================================================
	# ВОТ СЮДА (СТРОГО МЕЖДУ ЗАПРОСОМ И НАЧАЛОМ ЦИКЛА FOR) ВСТАЕТ СОРТИРОВКА:
	# =====================================================================
	if __settings__.getSetting("sort") == "1":
		try:
			entries = sorted(entries, key=lambda k: int(k.get('seeds', 0)), reverse=True)
#			log_debug("(ShowEntries) Массив отсортирован строго по количеству сидов.")
		except Exception as e:
			log_debug(f"(ShowEntries) Ошибка одиночной сортировки: {str(e)}", level=xbmc.LOGERROR)

	elif __settings__.getSetting("sort") == "2":
		def get_resolution_rank(title_text):
			label = get_label(title_text)
			if '2160' in label or '4K' in label:   return 4
			if '1080' in label:                    return 3
			if '720' in label:                     return 2
			if '??? ' not in label:                return 1
			return 0

		try:
			entries = sorted(
				entries,
				key=lambda k: (-get_resolution_rank(k['title']), -int(k.get('seeds', 0)), k['title'].lower())
			)
#			log_debug("(ShowEntries) Выполнена комбинированная сортировка: Разрешение -> Сиды -> Имя.")
		except Exception as e:
			log_debug(f"(ShowEntries) Ошибка комбинированной сортировки: {str(e)}", level=xbmc.LOGERROR)

	displayed_count = 0
	# Инициализируем наш группировщик строго на первом уровне функции
	grouped_fetch_dict = {}

	# =====================================================================
	# ТОЧЕЧНЫЙ БЛОК ФОРМАТИРОВАНИЯ СТРОКИ В СТИЛЕ RUTOR
	# =====================================================================
	for item in entries:

		pretty_dict = json.dumps(item, indent=4, ensure_ascii=False)
		log_debug(f"(ShowEntries) На входе в ParseTitle : {pretty_dict}")

		if not topic_filter(item['title']):
			continue

		#pretty_dict = json.dumps(item, indent=4, ensure_ascii=False)
		#log_debug(f"(ShowEntries) На входе в ParseTitle : {pretty_dict}")

		title_packet = ParseTitle(item)

#		pretty_dict = json.dumps(title_packet, indent=4, ensure_ascii=False)
#		log_debug(f"(ShowEntries) На выходе из ParseTitle : {pretty_dict}")

		# Печатаем в лог факт отправки элемента в AddItem
#		log_debug(f"(ShowEntries) Отправка в AddItem -> TopicID: {item['topic_id']} | Чистый Title: '{item['title']}'")

		info = {
			'title': item['title'],
			'topic_id': item['topic_id'],
			'dwnld_id': item['dwnld_id']
		}

#		log_debug(f"(ShowEntries) Отправка в AddItem -> info: {info}")

		item_data = {
			'display_title': title_packet['disp'],
			'topic_id': item['topic_id'],
			'dwnld_id': item['dwnld_id'],
			'info': repr(info)
		}

#		log_debug(f"(ShowEntries) Отправка в AddItem -> item_data: {item_data}")

		AddItem("Topic", item_data)

		# 2. Вытаскиваем данные для анализа
		topic_id = str(item.get('topic_id')).strip()
		title_text = title_packet.get('rus')

		# Заглядываем в вашу локальную базу cache.db по ID топика
		# Если в базе уже есть эта раздача, мы её пропускаем
		if _db_cache_instance.get_poster(topic_id):
#			log_debug(f"(ShowEntries hit) TopicID: {item['topic_id']} найден в cache.db")
			continue

		if _db_cache_instance.get_poster_by_name(title_ru=title_packet['rus'],title_en=title_packet['eng'],year=title_packet['year']):
#			log_debug(f"(ShowEntries miss/hit) Title: {title_packet['rus']} найден в cache.db")
			continue

		# Группируем топики по этому фильму
		if title_text not in grouped_fetch_dict:
#			log_debug(f"(ShowEntries) title_text: {title_text} в grouped_fetch_dict")
			grouped_fetch_dict[title_text] = {
				'title_ru': title_packet['rus'],
				'title_en': title_packet['eng'],
				'year_val': title_packet['year'],
				'topic_ids': [topic_id] # Создаем список ID и кладем туда первый
			}
		else:
			# Если фильм уже встречался (например, Робокоп HQ после LQ),
			# просто дописываем текущий topic_id в его список!
			if topic_id not in grouped_fetch_dict[title_text]['topic_ids']:
				grouped_fetch_dict[title_text]['topic_ids'].append(topic_id)

	xbmcplugin.setPluginCategory(ADDON_HANDLE, PLUGIN_NAME)
	# =====================================================================
	# АСИНХРОННЫЙ ПУСК ФОНОВОГО ПОТОКА ЗАГРУЗКИ ПОСТЕРОВ И ИНФЫ
	# =====================================================================
	global _async_fetch_queue

#	if '_async_fetch_queue' in globals() and _async_fetch_queue:

	if grouped_fetch_dict:
		# Переводим наш сгруппированный словарь в список для фонового воркера
		# Получится компактный массив уникальных задач
		_async_fetch_queue = list(grouped_fetch_dict.values())
#		log_debug(f"[NNM-ASYNC-OPTIMIZE] Из пачки топиков сформировано ВСЕГО {len(_async_fetch_queue)} уникальных фильмов для поиска.")

		# Создаем независимый фоновый поток Python, скармливая ему нашу собранную очередь
		bg_thread = threading.Thread(target=Fetch_TvDB, args=(_async_fetch_queue,))
		# Флаг daemon=True гарантирует, что поток корректно закроется, если юзер выйдет из Kodi
		bg_thread.daemon = True
		# Запускаем поток в свободный асинхронный полет!
		bg_thread.start()

		# Очищаем глобальную очередь текущей страницы для следующего захода
		_async_fetch_queue = []

#	xbmcplugin.endOfDirectory(ADDON_HANDLE)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def ParseTitle(item):

	if not isinstance(item, dict):
#		log_debug(f"(ParseTitle) Прилетела обычная строка")
		return

	# 1. Извлекаем цветной тег разрешения (например, [COLOR...][ 1080p ][/COLOR])
	resolution_tag = get_label(item['title'])

	# 2. Забираем размер и сиды из нашего обновленного класса АПИ
	size_text = item.get('size', '0 MB')
	seeds_count = item.get('seeds', '0')

	# Подкрашиваем сиды в зеленый или серый цвет
	if int(seeds_count) > 0:
		seeds_str = f"[COLOR lime]{seeds_count}[/COLOR]"
	else:
		seeds_str = f"[COLOR silver]{seeds_count}[/COLOR]"

	# Очищаем оригинальное название от двойных пробелов для аккуратности
	clean_title = item['title'].replace("  ", " ").strip()

	# 3. СБОРКА СТРОКИ СТРОГО ПО ВАШЕМУ ШАБЛОНУ RUTOR:
	# [ Качество ] Сиды | Размер | {Наименование торрента}
	display_title = f"{resolution_tag}| {seeds_str} | {size_text} | {item['title']}"

	# 1. Извлекаем год (стандартный поиск четырех цифр)
	year_match = re.search(r'(19\d{2}|20\d{2})', item['title'])
	year_val = year_match.group(1) if year_match else ""

	# 2. Вырезаем кусок с названиями до первой круглой скобки года
	names_chunk = re.sub(r'[(].*', '', item['title']).strip()

	title_ru = ""
	title_en = ""

	has_cyrillic = re.compile(r'[а-яА-ЯёЁ]')
	has_latin = re.compile(r'[a-zA-Z]')

	# 3. Валидируем языки по вашему строгому алгоритму с проверкой алфавита
	if '/' in names_chunk:
		parts = names_chunk.split('/')
		for part in parts:
			clean_part = re.sub(r'[\[].*', '', part).strip()
			if not clean_part:
				continue

			if has_cyrillic.search(clean_part) and not title_ru:
				title_ru = clean_part.lower()
			elif has_latin.search(clean_part) and not title_en:
				title_en = clean_part.lower()

		if not title_ru and parts:
			title_ru = re.sub(r'[\[].*', '', parts[0]).strip().lower()
		if not title_en:
			title_en = title_ru
	else:
		clean_single = re.sub(r'[\[].*', '', names_chunk).strip().lower()
		if has_cyrillic.search(clean_single):
			title_ru = clean_single
			title_en = clean_single
		elif has_latin.search(clean_single):
			title_en = clean_single
			title_ru = clean_single
		else:
			title_ru = clean_single
			title_en = clean_single

	# Возвращаем красивый структурированный пакет для ShowEntries
	return {
		'disp': display_title,
		'eng': title_en,
		'rus': title_ru,
		'orig': item['title'],
		'year': year_val
	}


#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def Topic(topic_id, download_id, info={}):

	pretty_dict = json.dumps(info, indent=4, ensure_ascii=False)
	log_debug(f"(Topic) Точка входа mode: {mode} , topic_id: {topic_id} , download_id : {download_id} info : {pretty_dict}")

#	log_debug(f"(Topic) Запуск парсера топика {topic_id} c dnld_id: {download_id}")

	# 1. СНАЧАЛА ЗАХОДИМ НА СТРАНИЦУ САМОГО ТОПИКА
	topic_page_url = f"{site_url_base}/forum/viewtopic.php?t={topic_id}"

	html = GET_NNM(topic_page_url)

	topic_title = "Неизвестный торрент"
	try:
		# Ищем заголовок темы nnm-club
		title_pattern = re.compile(r'<a[^>]*class="maintitle"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
		title_match = title_pattern.search(html)

		if title_match:
			raw_title = title_match.group(1).strip()
			# Очищаем от HTML-тегов, если они там есть, и отрезаем мусор на конце
			clean_title = re.sub(r'<[^>]+>', '', raw_title).strip()
			# Сразу очищаем от квадратных скобок и качества для истории (например, "Интерстеллар")
			topic_title = re.sub(r'[/([].*', '', clean_title).strip()
#			log_debug(f"(Topic) Из HTML успешно вытянуто чистое имя топика: '{topic_title}'")
	except Exception as title_err:
		log_debug(f"(Topic-WARN) Не удалось распарсить заголовок темы: {str(title_err)}")

	real_hash = ""
	# ИЩЕМ ТАБЛИЦУ btTbl И ВЫТАСКИВАЕМ ИЗ НЕЁ ЧИСТЫЙ MAGNET-ХЭШ
	table_match = re.search(r'class="btTbl".*?</table>', html, re.DOTALL | re.IGNORECASE)
	if table_match:
		bt_table_html = table_match.group(0)
#		log_debug("(Topic) Таблица btTbl успешно изолирована.")

		# Вытаскиваем 40 символов Info-Hash из magnet-ссылки внутри таблицы
		hash_match = re.search(r'magnet:\?xt=urn:btih:([a-fA-F0-9]{40})', bt_table_html, re.IGNORECASE)
		if hash_match:
			real_hash = hash_match.group(1)
#			log_debug(f"(Topic) Истинный Info-Hash раздачи найден: {real_hash}")

	# 2. ТЕПЕРЬ ДЕЛАЕМ НАШ ШТАТНЫЙ AJAX ЗАПРОС К СПИСКУ СЕРИЙ
	ajax_filelist_url = f"{site_url_base}/forum/filelst.php?attach_id={download_id}"

	html = GET_NNM(ajax_filelist_url)

	# ТОТАЛЬНАЯ РЕГУЛЯРКА ПОД ОДНОСТРОЧНЫЙ JS-ОТВЕТ (Ваша оригинальная логика)
#	file_pattern = r'class="genmed"\s+align="left">\s*([^<]+?\.(?:mkv|mp4|avi|ts|m2ts|mp3|flac|iso))'
	file_pattern = r'class="genmed"\s+align="left">\s*([^<]+?\.(?:mkv|mp4|avi|ts))'
	files = re.findall(file_pattern, html, re.IGNORECASE)

	if not files and html:
		file_pattern = r'[^"\'\s>]+?\.(?:mkv|mp4|avi|ts)'
		files = re.findall(file_pattern, html, re.IGNORECASE)
		files = [f for f in files if f.lower().endswith(('.mkv', '.mp4', '.avi', '.ts'))]

	# === ВЫВОД ЭЛЕМЕНТОВ СЕРИЙ В KODI ===

	if not files:
#		log_debug("(Topic) AJAX: Список файлов пуст.")
#		log_debug("(Topic) AJAX: Список файлов пуст. Выводим уведомление.")

		# Красивое всплывающее окошко Kodi на 3 секунды
		xbmcgui.Dialog().notification('Внимание', 'Нет доступных файлов для воспроизведения', xbmcgui.NOTIFICATION_WARNING, 3000)

		# Закрываем директорию Kodi, чтобы крутилка загрузки исчезла
		xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)
		return # Мгновенный выход, ошибка UnboundLocalError теперь физически невозможна

	else:
#		log_debug(f"(Topic) AJAX: Найдено файлов для вывода серий: {len(files)}")
		for file_id, filename in enumerate(files):
			clean_filename = filename.strip()
			if not clean_filename:
				continue

			display_title = f"{clean_filename}"

			if real_hash:
				download_url = f"magnet:?xt=urn:btih:{real_hash}"
			else:
				download_url = f"{site_url_base}/forum/download.php?id={download_id}"

			# 2. Собираем весь "сброд" переменных в один аккуратный плоский словарь
			play_params = {
				'mode': 'Play',
				'url': download_url,
				'title': clean_filename,
				'info': repr({'title': clean_filename}), # Вложенный инфо-пакет для самого плеера
				'file_id': str(file_id),
				'topic_id': topic_id,
				'dwnld_id': download_id,
				'topic_title': info.get('title', 'Без названия') if isinstance(info, dict) else 'Без названия'
			}

#			purl = f"{ADDON_URL}?mode=Play&url={quote(download_url)}&index={str(file_id)}&title={quote(clean_filename)}&info={quote(repr({'title': clean_filename}))}&topic_id={topic_id}&topic_title={quote(info['title'])}&dwnld_id={quote(download_id)}"

			purl = f"{ADDON_URL}?{urlencode(play_params)}"

			listitem = xbmcgui.ListItem(clean_filename)

			listitem.setInfo(type="Video", infoLabels={'title': clean_filename})
			listitem.setProperty('IsPlayable', 'true')

			xbmcplugin.addDirectoryItem(ADDON_HANDLE, purl, listitem, False, len(files))

	# Закрываем каталог страницы серий
	xbmcplugin.setPluginCategory(ADDON_HANDLE, PLUGIN_NAME)
	xbmcplugin.endOfDirectory(ADDON_HANDLE)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def Play(download_url, file_title, file_info):
	# На лету читаем значение единственной куки авторизации трекера из файла cookies.txt
	addon_path = addon.getAddonInfo('path')
	cookie_file_path = os.path.join(addon_path, 'resources', 'cookies.txt')
	user_auth_cookie = ""

	if os.path.exists(cookie_file_path):
		try:
			with open(cookie_file_path, 'r', encoding='utf-8') as f:
				for line in f:
					if 'phpbb2mysql_4_t' in line:
						user_auth_cookie = line.strip()
		except: pass

	try: pu = 'plugin://plugin.video.nnmclub/?mode=Root'
	except: pu = ''

	# Склеиваем вызов ТАМ со всеми куками обхода Cloudflare и авторизации зеркала
	tam_purl = f"plugin://plugin.video.tam/?mode=open&url={quote(download_url)}&info={quote(repr(file_info))}&purl={quote(pu)}&cookie={quote(user_auth_cookie)}"

	# Создаем ListItem с чистым именем файла (file_title)
	listitem = xbmcgui.ListItem(file_title)
	listitem.setPath(tam_purl)

	# Каноническое разрешение ссылки Kodi строго по handle (индекс 1)
	xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, listitem)

def SearchByActor():
	"""
	Запрашивает имя актера, выводит всплывающее графическое окно
	со списком его фильмов и обложками, а клик сразу запускает поиск по трекеру.
	"""

	if tvdb_v4_official is None:
#		log_debug("[NNM-MAN-TVDB-ERROR] Критическая остановка: библиотека tvdb_v4_official.py отсутствует на диске!", level=xbmc.LOGERROR)
		return

	tvdb_api_key = __settings__.getSetting('tvdb_key').strip().replace(" ", "")
	if not tvdb_api_key:
		log_debug("[NNM-ACTOR-TVDB-ERROR] Поле ключа в настройках GUI пустое! Заполните его. Выход из потока.", level=xbmc.LOGERROR)
		return

	log_debug(f"(SearchByActor) Попытка авторизации ключа {tvdb_api_key[:10]}... на серверах ://thetvdb.com")
	try:
		tvdb_client = tvdb_v4_official.TVDB(apikey=tvdb_api_key)
		log_debug("(SearchByActor) УСПЕШНАЯ АВТОРИЗАЦИЯ! JWT-токен TVDB v4 получен и сохранен в сессии.")
	except Exception as auth_err:
		log_debug(f"[NNM-ACTOR-TVDB-ERROR] Сервер TVDB v4 отклонил ваш API-ключ! Причина: {str(auth_err)}", level=xbmc.LOGERROR)
		return

	# 1. Вызываем клавиатуру для ввода имени актера
	dialog = xbmcgui.Dialog()
	actor_input = dialog.input("Поиск по актерам (TheTVDB)", defaultt="")

	if not actor_input or actor_input.strip() == "":
		xbmc.executebuiltin("Action(Back)")
#		return # Юзер нажал отмену, просто выходим

	actor_name = actor_input.strip()
	log_debug(f"(SearchByActor) Запущен онлайн-поиск актера на TVDB: '{actor_name}'", level=xbmc.LOGINFO)

	try:
		# 2. Ищем самого актера на сервере TVDB v4
		search_results = tvdb_client.search(query=actor_name, type="person")
		if not search_results:
			log_debug(f"(SearchByActor) Актёр '{actor_name}' не найден.")
			dialog.ok("NNM-Трэкер", f"Актёр '{actor_name}' не найден.")
			return

		pretty_dict = json.dumps(search_results, indent=4, ensure_ascii=False)
		log_debug(f"(SearchByActor) Ищем самого актера на сервере TVDB v4 : {pretty_dict}")


		# Ваша фирменная страховка структуры ответа SDK
		if isinstance(search_results, dict) and 'data' in search_results:
			raw_people = search_results['data']
		elif isinstance(search_results, list):
			raw_people = search_results
		else:
			raw_people = []

		if not raw_people:
			log_debug(f"(SearchByActor) Актёр '{actor_name}' не найден.")
			dialog.ok("NNM-Трэкер", f"Актёр '{actor_name}' не найден.")
			return

		# Теперь мы со 100% уверенностью берём ПЕРВЫЙ элемент из массива людей!
		chosen_person = raw_people[0]

		# Вытаскиваем ID и хирургически срезаем текстовый префикс 'person-', если он есть
		if isinstance(chosen_person, dict):
			raw_id = str(chosen_person.get('id', '')) or str(chosen_person.get('tvdb_id', ''))
		else:
			raw_id = str(getattr(chosen_person, 'id', '')) or str(getattr(chosen_person, 'tvdb_id', ''))

		# Наш спасительный сплит: превратит 'person-255093' или '255093' в чистые цифры '255093'
		actor_id = raw_id.split('-')[-1]

		log_debug(f"(SearchByActor) Успешно найден и очищен ID актера: {actor_id}", level=xbmc.LOGINFO)

		# 3. Запрашиваем расширенную карточку актера, передавая СТРОГО чистые цифры!
		person_details = tvdb_client.get_person_extended(int(actor_id))

		if not person_details:
			log_debug("(SearchByActor) Не удалось загрузить фильмографию этого актера.")
			dialog.ok("NNM-Трэкер", "Не удалось загрузить фильмографию этого актера.")
			return

		# === Шаг 4: ИЗВЛЕЧЕНИЕ ФИЛЬМОГРАФИИ ЧЕРЕЗ РОЛИ (CHARACTERS) ===
		data_block = person_details
		if isinstance(person_details, dict) and 'data' in person_details:
			data_block = person_details['data']

		# В API v4 extended-информация о человеке хранит фильмы внутри его ролей ('characters')
		raw_chars = []
		if isinstance(data_block, dict):
			raw_chars = data_block.get('characters', [])
		else:
			raw_chars = getattr(data_block, 'characters', [])

		log_debug(f"[NNM-ACTOR-DEBUG] Найдено {len(raw_chars)} ролей/персонажей актера в базе.", level=xbmc.LOGINFO)

		list_items = []
		clean_titles_mapping = []

		for char in raw_chars:
			# Каждая роль содержит объект фильма/сериала, к которому она принадлежит
			# В API v4 этот вложенный объект обычно называется 'movie' или 'series'
			# Вытаскиваем тип медиа

			p_type = str(char.get('peopleType', '')).strip().lower()
			role_name = str(char.get('name', '')).strip().lower()

			# 1. Отсекаем шоу, документалки и передачи, где актер — просто гость или ведущий
			# Оставляем строго "actor" (актер) или "cameo" (камео в художественном фильме)
			if p_type not in ['actor', 'cameo', 'voice']:
				continue

			# 2. Отсекаем промо-ролики, интервью и фильмы о фильмах,
			# где актер играет сам себя ("himself" / "herself")
			if 'himself' in role_name or 'herself' in role_name:
				continue

			media_object = None
			if isinstance(char, dict):
				media_object = char.get('movie') or char.get('series')
			else:
				media_object = getattr(char, 'movie', None) or getattr(char, 'series', None)

			media_type = str(media_object.get('type', 'movie')).lower()
			# ЖЕСТКИЙ ФИЛЬТР: оставляем ТОЛЬКО фильмы и сериалы!
			# Если TVDB пометил запись как "sub-series", "documentary" или пустую — до свидания!
			if media_type not in ['movie', 'series']:
				continue

			pretty_dict = json.dumps(char, indent=4, ensure_ascii=False)
			log_debug(f"(SearchByActor) char: {pretty_dict}")

			# Если фильм внутри роли нашелся — вытаскиваем его данные!
			if media_object:
				if isinstance(media_object, dict):
					title_ru = media_object.get('name') or media_object.get('title') or 'Без названия'
					year_val = media_object.get('year') or '0000'
					cover_url = media_object.get('image') or media_object.get('image_url') or media_object.get('thumbnail', '')
				else:
					title_ru = getattr(media_object, 'name', '') or getattr(media_object, 'title', 'Без названия')
					year_val = getattr(media_object, 'year', '0000')
					cover_url = getattr(media_object, 'image', '') or getattr(media_object, 'image_url', '')

				# Исключаем дубликаты фильмов (ведь актер мог сыграть двух персонажей в одном фильме)
				if title_ru in clean_titles_mapping:
					continue

				display_text = f"{title_ru} ({year_val})"

				item = xbmcgui.ListItem(label=display_text)
				if cover_url:
					item.setArt({'thumb': cover_url, 'icon': cover_url, 'poster': cover_url})

				list_items.append(item)
				clean_titles_mapping.append(title_ru)

		log_debug(f"[NNM-ACTOR-DEBUG] Итого укомплектовано {len(list_items)} уникальных фильмов для вывода на экран.", level=xbmc.LOGINFO)

		if not list_items:
			dialog.ok("NNM-Трэкер", "У этого актера нет зарегистрированных фильмов.")
			return

		# === Шаг 5: ВЫВОД ГРАФИЧЕСКОГО ОКНА ===
		selected_index = dialog.select(f"Фильмы: {actor_name}", list_items, useDetails=True)

		if selected_index >= 0:
			chosen_movie_title = clean_titles_mapping[selected_index]
			log_debug(f"[NNM-ACTOR-REVERSE] Выбран фильм '{chosen_movie_title}'. Запускаем поиск раздач...", level=xbmc.LOGINFO)

			from urllib.parse import quote_plus

			search_purl = f"{ADDON_URL}?mode=NextSearch&query={quote_plus(chosen_movie_title)}&forum_id=-1"
			xbmc.executebuiltin(f"Container.Update({search_purl})")


	except Exception as e:
		xbmc.log(f"[NNM-ACTOR-ERROR] Ошибка расширенного поиска по актерам: {str(e)}", level=xbmc.LOGERROR)
		dialog.ok("NNM-Трэкер", "Ошибка при обработке данных актера.")

def manual_Fetch_TvDB(topic_id, link_id, current_title=""):
	"""
	Отображает диалоги Kodi, запрашивает ввод, ищет на TVDB v4
	и выводит результаты со значками обложек для визуального выбора.
	"""
	dialog = xbmcgui.Dialog()

	# 1. Показываем красивое окно ввода с уже предзаполненным названием топика
	search_query = dialog.input("Редактирование поискового запроса TVDB", defaultt=current_title)
	if not search_query:
		xbmc.executebuiltin("Action(Back)")
#		return  # Пользователь отменил ввод или нажал "Назад"

	# 2. Получаем текущий link_id из базы перед поиском (чтобы знать, какую группу мы переселяем)
	old_link_id = link_id

	if tvdb_v4_official is None:
#		log_debug("[NNM-MAN-TVDB-ERROR] Критическая остановка: библиотека tvdb_v4_official.py отсутствует на диске!", level=xbmc.LOGERROR)
		return

	tvdb_api_key = __settings__.getSetting('tvdb_key').strip().replace(" ", "")
	if not tvdb_api_key:
#		log_debug("[NNM-MAN-TVDB-ERROR] Поле ключа в настройках GUI пустое! Заполните его. Выход из потока.", level=xbmc.LOGERROR)
		return

#	log_debug(f"[NNM-MAN-TVDB] Попытка авторизации ключа {tvdb_api_key[:10]}... на серверах ://thetvdb.com")
	try:
		tvdb_client = tvdb_v4_official.TVDB(apikey=tvdb_api_key)
#		log_debug("[NNM-MAN-TVDB] УСПЕШНАЯ АВТОРИЗАЦИЯ! JWT-токен TVDB v4 получен и сохранен в сессии.")
	except Exception as auth_err:
		log_debug(f"[NNM-MAN-TVDB-ERROR] Сервер TVDB v4 отклонил ваш API-ключ! Причина: {str(auth_err)}", level=xbmc.LOGERROR)
		return

	# Показываем нативный индикатор загрузки Kodi, пока идет сетевой запрос
	p_dialog = xbmcgui.DialogProgress()
	p_dialog.create("NNM-Club", "Поиск совпадений на серверах TVDB v4...")

	try:
		# Запрашиваем данные у TVDB (ищем всё подряд: и фильмы, и сериалы)
		search_results = tvdb_client.search(query=search_query)
		p_dialog.close()

		pretty_dict = json.dumps(search_results, indent=4, ensure_ascii=False)

#		log_debug(f"[NNM-MAN-TVDB] search_results is : {pretty_dict}")

		# Если ничего не нашли
		if not search_results:
			dialog.ok("NNM-Club", "По запросу ничего не найдено.\nПопробуйте сократить название.")
			return

		# Страховка на случай изменения структуры библиотекой
		if isinstance(search_results, dict) and 'data' in search_results:
			raw_items = search_results['data']
		elif isinstance(search_results, list):
			raw_items = search_results
		else:
			raw_items = []

		if not raw_items:
			dialog.ok("NNM-Club", "Не удалось распознать формат ответа от TVDB.")
			return

		# 3. СТРОИМ ГРАФИЧЕСКИЙ СПИСОК С КАРТИНКАМИ И ПОДПИСЯМИ
		list_items = []

		for item in raw_items:
			title_ru = item.get('name', 'Без названия')
			item_year = item.get('year', '????')
			item_type = item.get('type', 'контент').upper()

			# Извлекаем оригинальное имя из translations (как в вашем JSON)
			title_en = ""
			if isinstance(item.get('translations'), dict):
				title_en = item['translations'].get('eng', '')
			if not title_en:
				title_en = item.get('original_name', '')

			# Формируем красивую подпись (Заголовок и подзаголовок для Kodi)
			# label — это жирный верхний текст, label2 — серый текст справа/снизу
			label_text = f"{title_ru} ({item_year}) [{item_type}]"
			label2_text = title_en if title_en and title_en != title_ru else ""

			# Достаем ссылки на картинки из вашего формата JSON
			# Идеально брать thumbnail для быстрой загрузки сетки в окне
			thumb_url = item.get('thumbnail') or item.get('image_url') or ''

			# Создаем визуальный ListItem
			li = xbmcgui.ListItem(label=label_text, label2=label2_text)

			# Накатываем арты. Скин Kodi сам подхватит их и отрендерит миниатюры!
			if thumb_url:
				li.setArt({
					'thumb': thumb_url,
					'icon': thumb_url,
					'poster': thumb_url
				})

			list_items.append(li)

		# 4. ВЫВОД ОТДЕЛЬНОГО ОКНА ВЫБОРА С МИНИАТЮРАМИ
		# Kodi откроет графическое окно со списком/сеткой и подгрузит все постеры
		selected_index = dialog.select("Выберите правильный вариант:", list_items, -1,-1,True)
#		selected_index = dialog.grid("Выберите правильную обложку:", list_items) # KODI 19.4 нет grid у dialog
		if selected_index < 0:
			return  # Пользователь закрыл окно выбора

		# Получаем именно тот словарь фильма, на который кликнул пользователь
		chosen_item = raw_items[selected_index]

		# 5. Отправляем выбранный JSON в наш каскадный обработчик базы данных
		updated_count = _db_cache_instance.relink_topic_group(
			old_link_id=old_link_id,
			current_topic_id=topic_id,
			raw_json_dict=chosen_item
		)

		# 6. Финальный аккорд: уведомление и обновление экрана
		msg = f"Успешно обновлено релизов: {updated_count}" if updated_count > 1 else "Обложка успешно обновлена!"
		dialog.notification("NNM-Club", msg, xbmcgui.NOTIFICATION_INFO, 3000)

		# Принудительно заставляем Kodi перерисовать текущий экран, чтобы новые обложки применились
		xbmc.executebuiltin("Container.Refresh")

	except Exception as e:
		if 'p_dialog' in locals():
			p_dialog.close()
		dialog.ok("Ошибка сопоставления", f"Не удалось обновить данные: {str(e)}")
		xbmc.log(f"[NNM-MAN-TVDB-ERROR] Сбой ручного сопоставления для топика {topic_id}: {str(e)}", level=xbmc.LOGERROR)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def Fetch_TvDB(items_to_fetch):

	log_debug(f"[NNM-ASYNC-TVDB] Фоновый поток запущен. Уникальных фильмов в пакете: {len(items_to_fetch)}")

	if tvdb_v4_official is None:
#		log_debug("[NNM-ASYNC-TVDB-ERROR] Критическая остановка: библиотека tvdb_v4_official.py отсутствует на диске!", level=xbmc.LOGERROR)
		return

	tvdb_api_key = __settings__.getSetting('tvdb_key').strip().replace(" ", "")
	if not tvdb_api_key:
#		log_debug("[NNM-ASYNC-TVDB-ERROR] Поле ключа в настройках GUI пустое! Заполните его. Выход из потока.", level=xbmc.LOGERROR)
		return

#	log_debug(f"[NNM-ASYNC-TVDB] Попытка авторизации ключа {tvdb_api_key[:10]}... на серверах ://thetvdb.com")
	try:
		tvdb_client = tvdb_v4_official.TVDB(apikey=tvdb_api_key)
#		log_debug("[NNM-ASYNC-TVDB] УСПЕШНАЯ АВТОРИЗАЦИЯ! JWT-токен TVDB v4 получен и сохранен в сессии.")
	except Exception as auth_err:
		log_debug(f"[NNM-ASYNC-TVDB-ERROR] Сервер TVDB v4 отклонил ваш API-ключ! Причина: {str(auth_err)}", level=xbmc.LOGERROR)
		return

	# Обрабатываем каждый уникальный фильм из пакета, собранного в ShowEntries
	for movie_task in items_to_fetch:
		title_ru = movie_task.get('title_ru').strip()
		title_en = movie_task.get('title_en').strip()
		year_val = movie_task.get('year_val').strip()
		associated_ids = movie_task.get('topic_ids', [])

		log_debug(f"[NNM-ASYNC-TVDB] ---> СТАРТ ОБРАБОТКИ ФИЛЬМА: '{title_ru}' ({year_val}) | Связанных топиков: {len(associated_ids)}")
		pretty_dict = json.dumps(movie_task, indent=4, ensure_ascii=False)
		log_debug(f"(Fetch_TvDB) В обработке пакет : {pretty_dict}")

		try:
			# Очищаем спецсимволы и кавычки, которые ломают поисковый движок TheTVDB
			search_query = title_ru.replace('«', '').replace('»', '').replace('"', '').replace("'", "")
			search_query = search_query.replace(':', ' ').replace('-', ' ')
			search_query = re.sub(r'\s+', ' ', search_query).strip()

			# --- Шаг 1: Сетевой поиск А (Попытка на русском языке) ---
			log_debug(f"[NNM-ASYNC-TVDB] Попытка А (RUS): '{search_query}', язык: rus")
			search_results = tvdb_client.search(query=search_query, language='rus')

			has_target_year = False
			if search_results:
				for match in search_results:
					if str(match.get('year', '')).strip() == str(year_val):
						has_target_year = True
						break

			# --- Шаг 2: Сетевой поиск Б (Если на русском не совпал год, шлем в ENG контур) ---
			if not has_target_year and title_en:
				log_debug(f"[NNM-ASYNC-TVDB-WARN] Год '{year_val}' не найден в русском индексе. Включаем международный ENG-контур...")
				search_query = title_en.replace('«', '').replace('»', '').replace('"', '').replace("'", "")
				search_query = search_query.replace(':', ' ').replace('-', ' ')
				search_query = re.sub(r'\s+', ' ', search_query).strip()

				log_debug(f"[NNM-ASYNC-TVDB] Попытка Б (ENG): '{search_query}', язык: eng")
				eng_results = tvdb_client.search(query=search_query, language='eng')
				if eng_results:
					search_results = eng_results

			# --- Шаг 3: Жесткая фильтрация прилетевших результатов ---
			if search_results:
				first_match = None
				# Сначала ищем идеальное совпадение по году (защита от ремейков)
				for match in search_results:
					match_year = str(match.get('year', '')).strip()
					if year_val and match_year == str(year_val):
						first_match = match
						log_debug(f"[NNM-ASYNC-TVDB] Железное совпадение по году найдено: {match.get('name')} ({match_year})")
						break

				# Если по году не зацепились, ищем хотя бы просто фильм (movie), отсекая левые сериалы
				if not first_match:
					for match in search_results:
						if str(match.get('type', '')).lower() == 'movie':
							first_match = match
							break

				# Если фильтры пролетели мимо, берем первый элемент поисковой выдачи сервера
				if not first_match:
					first_match = search_results[0] if isinstance(search_results, list) else search_results

				# --- Шаг 4: Извлечение паспорта фильма строго по спецификации TVDB v4 ---
				server_id = first_match.get('id') or first_match.get('tvdb_id')

				# Если сервер по какой-то мистической причине не отдал ID, генерируем временный числовой хэш
				if not server_id:
					server_id = abs(hash(title_ru)) % (10 ** 8)

				# Забираем имена для локального кэша индексов (то, что просил метод поиска по имени)
				name_ru = first_match.get('name', title_ru)
				name_en = first_match.get('originalName', '') or first_match.get('englishName', '')

				poster_url = first_match.get('image_url') or first_match.get('image', '')
				plot_text = first_match.get('overview', '')
				movie_year = first_match.get('year', year_val)

				# Обрабатываем рейтинг
				tvdb_score = first_match.get('score', 0.0)
				rating_val = float(tvdb_score) / 10.0 if tvdb_score > 10 else float(tvdb_score)

				if poster_url:
					if poster_url.startswith('/'):
						poster_url = f"https://thetvdb.com{poster_url}"
#					log_debug(f"[NNM-ASYNC-TVDB-SUCCESS] Метаданные TVDB получены! Фильм '{name_ru}' привязан к tvdb_id: {server_id}")

					# --- Шаг 5: НАПОЛНЕНИЕ НАШЕЙ НОВОЙ РЕЛЯЦИОННОЙ БД SQLite ---
					# Вызываем метод, который атомарно заполнит content_meta и свяжет все топики в линкере
#					log_debug(f"[NNM-ASYNC-TVDB] Сохраняем карточку фильма и связываем её с {len(associated_ids)} топиками...")
					for target_id in associated_ids:
						try:
							_db_cache_instance.save_poster_relational(
								topic_id=target_id,
								tvdb_id=server_id,
								title_ru=name_ru,
								title_en=name_en,
								cover=poster_url,
								plot=plot_text,
								year=movie_year,
								rating=rating_val
							)
						except Exception as db_err:
							log_debug(f"[NNM-ASYNC-TVDB-ERROR] Ошибка транзакции для топика {target_id}: {str(db_err)}", level=xbmc.LOGERROR)
				else:
					log_debug(f"[NNM-ASYNC-TVDB-WARN] Фильм найден, но поле image_url пустое.", level=xbmc.LOGWARNING)
			else:
#				log_debug(f"[NNM-ASYNC-TVDB-WARN] Индексы TVDB вернули пустой массив результатов для запроса topic_id:{target_id} : '{title_ru}'", level=xbmc.LOGWARNING)
				for target_id in associated_ids:
					_db_cache_instance.link_topic_to_null(target_id)
			xbmc.sleep(150) # Микропауза, чтобы не спамить сервер TVDB
		except Exception as item_err:
			log_debug(f"[NNM-ASYNC-TVDB-ERROR] Сбой фоновой обработки фильма '{title_ru}': {str(item_err)}", level=xbmc.LOGERROR)
			continue

	# Когда вся пачка обработана и таблицы SQLite заполнены — разом перерисовываем экран Kodi
#	log_debug("[NNM-ASYNC-TVDB] Пакетное наполнение БД завершено. Вызов Container.Refresh!")
	xbmc.executebuiltin('Container.Refresh')

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def get_info(info):
	# Возвращаем чистый словарь без ключа 'id', чтобы Kodi не ругался
	info = {
		'title': info.get('title', ''),
		'genre': 'NNM-Club Торрент'
	}
	try:
		if 'type' in info: info['type'] = info['type']
	except:
		pass
	return info

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def lower(s):
	if sys.version_info.major > 2: return s.lower()
	try:s=s.decode('utf-8')
	except: pass
	try:s=s.decode('windows-1251')
	except: pass
	s=s.lower().encode('utf-8')
	return s

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def get_title(i):
	r=i['title']
	e=i['originaltitle']
	if __settings__.getSetting("Title Mode") == '0':
		if r == e: title = r
		else: title = r+' / '+e
	if __settings__.getSetting("Title Mode") == '1':
		title = r
	if __settings__.getSetting("Title Mode") == '3':
		title = r +" [COLOR 33FFFFFF]("+str(i['year'])+")[/COLOR]"
	if __settings__.getSetting("Title Mode") == '2':
		try: rat = i['seeds']
		except: rat = '--'
		rat = mids(rat, 6)
		title = '['+rat+'] '+r
	return title

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def update_info(id):
	rem_inf_db(id)
	xbmc.sleep(300)
	xbmc.executebuiltin('Container.Refresh')

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def get_label(text):
	text=lower(text)#.lower()
	#print text
	if 'трейлер'  in text: return FC('[ Трейл.]',    'FF999999')
	if ' кпк'     in text: return FC('[   КПК  ]',   'FFF8888F')
	if 'telesyn'  in text: return FC('[    TS    ]', 'FFFF2222')
	if 'telecin'  in text: return FC('[    TS    ]', 'FFFF2222')
	if 'camrip'   in text: return FC('[    TS    ]', 'FFFF2222')
	if ' ts'      in text: return FC('[    TS    ]', 'FFFF2222')
	if 'dvdscr'   in text: return FC('[    Scr   ]', 'FFFF2222')
	if ' 3d'      in text: return FC('[    3D    ]', 'FC45FF45')
	if '720'      in text: return FC('[  720p  ]',   'FBFFFF55')
	if '1080'     in text: return FC('[ 1080p ]',    'FAFF9535')
	if '2160'     in text: return FC('[ 2160p ]',    'FAF990FF')
	if 'blu-ray'  in text: return FC('[  BRay  ]',   'FF5555FF')
	if 'bdremux'  in text: return FC('[    BD    ]', 'FF5555FF')
	if ' 4k'      in text: return FC('[    4K    ]', 'FF5555FF')
	if 'bdrip'    in text: return FC('[ BDRip ]',    'FE98FF98')
	if 'drip'     in text: return FC('[ BDRip ]',    'FE98FF98')
	if 'hdrip'    in text: return FC('[ HDRip ]',    'FE98FF98')
	if 'webrip'   in text: return FC('[  WEB   ]',   'FEFF88FF')
	if 'WEB'      in text: return FC('[  WEB   ]',   'FEFF88FF')
	if 'web-dl'   in text: return FC('[  WEB   ]',   'FEFF88FF')
	if 'hdtv'     in text: return FC('[ HDTV ]',     'FEFFFF88')
	if 'tvrip'    in text: return FC('[    TV    ]', 'FEFFFF88')
	if 'satrip'   in text: return FC('[    TV    ]', 'FEFFFF88')
	if 'dvb '     in text: return FC('[    TV    ]', 'FEFFFF88')
	if 'dvdrip'   in text: return FC('[DVDRip]',     'FE88FFFF')
	if 'dvd5'     in text: return FC('[  DVD   ]',   'FE88FFFF')
	if 'xdvd'     in text: return FC('[  DVD   ]',   'FE88FFFF')
	if 'dvd-5'    in text: return FC('[  DVD   ]',   'FE88FFFF')
	if 'dvd-9'    in text: return FC('[  DVD   ]',   'FE88FFFF')
	if 'dvd9'     in text: return FC('[  DVD   ]',   'FE88FFFF')
	return FC('[   ????  ]', 'FFFFFFFF')

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def FC(s, color="FFFFFF00"):
	s="[COLOR "+color+"]"+s+"[/COLOR]"
	return s

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def debug(s):
	fl = open(os.path.join( ru(LstDir),"test.txt"), "w")
	fl.write(s)
	fl.close()

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def topic_filter(title):

	"""
	[РЕГУЛЯРНЫЙ ЭТАЛОН]
	Фильтрует мусор одной строкой с помощью регулярных выражений.
	Флаг re.IGNORECASE автоматически отключает чувствительность к регистру (lowcase).
	"""
	# 1. Собираем базовый список мусора через разделитель "|" (ИЛИ)
	# \b означает границу слова (чтобы " pc " ловилось как отдельное слово, а не часть других слов)
	trash_pattern = r"repack|\bpc\b|xbox|fb2|txt|doc|\bmp3\b|jpg|png|scr|pdf|\bchm\b|\bflac\b|lossless|portable|windows|office|android|mac os|игры|софт|\bmac\b|\brip\b"

	# Проверяем базовый мусор
	if re.search(trash_pattern, title, re.IGNORECASE):
		return False

	# 2. Проверяем экранки (CAMRip / TS), если включено в настройках
	if __settings__.getSetting("hide_scr") == 'true':
		# Экранируем круглую скобку  как \), так как в регулярках скобки — это спецсимволы!
		scr_pattern = r"camrip|\) ts|\) tc|\) тс|dvdscr"
		if re.search(scr_pattern, title, re.IGNORECASE):
			return False

	# 3. Проверяем пользовательский фильтр из настроек
	if __settings__.getSetting("use_filter") == 'true':
		user_filter = __settings__.getSetting("filter").split(',')
		# Безопасно экранируем пользовательские слова на случай, если юзер ввёл спецсимволы (точки, скобки)
		user_markers = [re.escape(i.strip()) for i in user_filter if i.strip()]
		if user_markers:
			user_pattern = "|".join(user_markers)
			if re.search(user_pattern, title, re.IGNORECASE):
				return False

	return True

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def SetViewMode():

	n = int(__settings__.getSetting("view_type"))
	if n>0:
		xbmc.executebuiltin("Container.SetViewMode(0)")
		for i in range(1,n):
			xbmc.executebuiltin("Container.NextViewMode")

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def ViewHistory():

	for item in _db_cache_instance.get_view_history():

		info = {
			'title': item[0],
			'topic_id': item[1],
			'dwnld_id': item[2]
		}
		resolution_tag = get_label(item[0])
		AddItem("Topic", {'display_title': f"{resolution_tag} | {item[0]}",'topic_id': item[1],'dwnld_id': item[2],'info': info})

	xbmcplugin.setPluginCategory(ADDON_HANDLE, PLUGIN_NAME)
	xbmcplugin.endOfDirectory(ADDON_HANDLE)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def History():
	# =====================================================================
	# ОБНОВЛЕННЫЙ БЛОК ОТРИСОВКИ ОДОБРЕННОГО ЭКРАНА ИСТОРИИ ИЗ SQLITE
	# =====================================================================
	# 1. Самым первым пунктом на экране всегда выводим кнопку ПОЛНОЙ очистки

	AddItem("ClearHistory", {'display_title': "[COLOR red][ Очистить историю поиска ][/COLOR]",'topic_id': '0'})

	# 2. Вытаскиваем список сохраненных поисков из SQLite через наш глобальный класс
	# Метод возвращает нам список кортежей вида: [(текст_запроса, id_раздела), ...]
	saved_queries = _db_cache_instance.get_history()

	if saved_queries:
		for query, forum_id in saved_queries:
			# Если у этого поискового запроса в базе зафиксирован ID раздела
			if forum_id and str(forum_id).strip() != "":
				item_data = {
					'display_title': query,
					'query': query,
					'forum_id': forum_id
				}
			else:
				# Если поиск был глобальным (по всему трекеру)
				item_data = {
					'display_title': query,
					'query': query,
				}

			# Отрисовываем пункт истории на экране Kodi.
			# В аргумент url передаем собранную строку параметров purl
			AddItem('NextSearch', item_data)

	xbmcplugin.setPluginCategory(ADDON_HANDLE, PLUGIN_NAME)
	xbmcplugin.endOfDirectory(ADDON_HANDLE)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def ClearHistory():
	# 1. Железно подключаем глобальный инстанс базы из шапки default.py
	global _db_cache_instance

#	log_debug("(ClearHistory) Запуск полной очистки истории поиска в SQLite...")

	try:
		# 2. Вызываем ваш готовый метод очистки таблицы
		_db_cache_instance.clear_history()

		# 3. Показываем пользователю красивое уведомление об успехе
		xbmcgui.Dialog().notification('История', 'История поиска успешно очищена', xbmcgui.NOTIFICATION_INFO, 3000)

		# 4. Плавно обновляем экран, чтобы пустая история сразу отобразилась в Kodi
		xbmc.executebuiltin('Container.Refresh')

	except Exception as e:
		log_debug(f"(ClearHistory) Критическая ошибка при очистке БД: {str(e)}", level=xbmc.LOGERROR)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

#def add_history(t):
#	try:L=eval(__settings__.getSetting("History"))
#	except: L=[]
#	if t not in L:
#		NL=[]
#		NL.append(t)
#		NL.extend(L[:15])
#		__settings__.setSetting("History", repr(NL))

##█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

#def remove_from_history(t):
#	# =====================================================================
#	# БЛОК ТОЧЕЧНОГО УДАЛЕНИЯ ОДНОГО СЛОВА ИЗ XML-НАСТРОЕК
#	# =====================================================================
#	try:
#		# Читаем текущую строку истории и превращаем её в живой список Python
#		history_list = eval(__settings__.getSetting("History"))
#	except:
#		history_list = []

#	# Проверяем, если удаляемое слово действительно присутствует в списке
#	if t in history_list:
#		# Удаляем строго один этот элемент из массива
#		history_list.remove(t)
#		# Записываем обновленный урезанный массив обратно в результирующий settings.xml
#		__settings__.setSetting("History", repr(history_list))
##		log_debug(f"[NNM-HISTORY] Слово '{t}' успешно удалено из истории.")

#	# Мгновенно обновляем текущий экран Kodi, чтобы строка сразу пропала из интерфейса
#	xbmc.executebuiltin("Container.Refresh")

##█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

#def del_history():
#	# =====================================================================
#	# ТОЧЕЧНЫЙ БЛОК ОЧИСТКИ ИСТОРИИ В РЕЗУЛЬТИРУЮЩЕМ XML НАСТРОЕК KODI
#	# =====================================================================
#	try:
#		# Перезаписываем XML-поле "History" пустым массивом, упакованным в repr() -> "[]"
#		__settings__.setSetting("History", repr([]))
##		log_debug("[NNM-HISTORY] Поле истории в результирующем settings.xml успешно очищено.")
#	except Exception as e:
##		log_debug(f"[NNM-HISTORY] Ошибка очистки XML-поля настроек: {str(e)}", level=xbmc.LOGERROR)

#	# Принудительно обновляем текущий экран Kodi, чтобы история мгновенно исчезла из меню
#	xbmc.executebuiltin("Container.Refresh")

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████
# === ОФИЦИАЛЬНЫЙ СТАРТ ПЛАГИНА (САМЫЙ КОНЕЦ ФАЙЛА) ===

params = get_params()

mode     = ''
url      = ''
title    = ''
query    = ''
forum_id = '-1'
topic_id = '0'
dwnld_id = '0'
file_id  = '0'
search_id= ''
offset   = '0'
info     = {}

try: mode = unquote(params["mode"])
except: pass
try: url = unquote(params["url"])
except: pass
try: title = unquote(params["title"])
except: pass
try: query = unquote(params["query"])
except: pass
try: forum_id = unquote(params["forum_id"])
except: pass
try: topic_id = unquote(params["topic_id"])
except: pass
try: dwnld_id = unquote(params["dwnld_id"])
except: pass
try: file_id = unquote(params["file_id"])
except: pass
try: search_id = unquote(params["search_id"])
except: pass
try: offset = unquote(params["offset"])
except: pass
try: info = eval(unquote(params["info"]))
except: pass


log_debug(f"Mode is : {mode}")
#pretty_dict = json.dumps(params, indent=4, ensure_ascii=False)
#log_debug(f"ADDON Startup params is : {pretty_dict}")

# Строгий последовательный вызов режимов, исключающий наложение потоков
if mode == '':
	# Самый первый вход в плагин
#	log_debug(f"START ADDON: NO Mode YET")
	Root()

elif mode == 'Category':
	# Вход в категорию
	Category(forum_id)

elif mode == 'SubCategory':
#	log_debug(f"Mode is : {mode}")
	# Вход во вложенную категорию (в переменной url лежит ID родительской папки, например 724)
	List(forum_id, int(offset))

elif mode in ['Search', 'SearchInCategory']:
#	log_debug(f"Mode is : {mode}")

	# Запрашиваем текст у пользователя
	if not query:  # В Python 'if not t' поймает и пустую строку '', и None
		query = inputbox()

	if not query or query.strip() == '':
#		log_debug("(Search) Пользователь отменил ввод или ввёл пустую строку")
		xbmc.executebuiltin("Action(Back)")
#		return # Вот тут пустой return — мы просто прерываем работу функции

	xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)

	if query != '':
		url_params = {
			'mode': 'NextSearch',
		}

		url_params['query'] = query

		if forum_id != '0':
			url_params['forum_id'] = forum_id

		if search_id:
			url_params['search_id'] = search_id

		if offset != '0':
			url_params['offset'] = offset

		#pretty_dict = json.dumps(url_params, indent=4, ensure_ascii=False)
		#log_debug(f"(Startup Search) url_params is : {pretty_dict}")

		search_purl = f"{ADDON_URL}?{urlencode(url_params)}"
		log_debug(f"(Startup Search) search_purl: {search_purl}")

		# Принудительно запускаем аддон заново уже по новому адресу
		xbmc.executebuiltin(f"Container.Update({search_purl})")

elif mode == 'NextSearch':

	log_debug(f"(Startup NextSearch) Точка входа. Поиск. Текст: '{query}', Смещение: {offset}, forum_id: {forum_id}")
	# Передаем вытащенные чистые параметры в вашу оригинальную функцию Search!

	Search(query=query, forum_id=forum_id, search_id=search_id, offset=int(offset))

elif mode == 'NextList':

#	log_debug(f"Mode is : {mode}")

	if offset:
		List(forum_id=forum_id, search_id=search_id, offset=int(offset))
	else:
		List(forum_id=forum_id, search_id=search_id, offset=0)

elif mode == 'Topic':
	# Вызывается при клике на фильм в листинге функции List.
	# В id передается ID темы (topic_id), в url — ID скачивания (download_id)
	Topic(topic_id, dwnld_id, info)

elif mode == 'Play':
	# =====================================================================
	# ФИНАЛЬНЫЙ КОНВЕЙЕР ВОСПРОИЗВЕДЕНИЯ ПО СХЕМЕ ЧИСТОГО MAGNET-СТРИМИНГА
	# =====================================================================
#	log_debug(f"[NNM-PLAY] Запуск плеера. Серия: {title}, Индекс: {file_id}, URL={url}")
#	log_debug(f"[NNM-PLAY] Содержимое info: {repr(info)}")

	# Переменная url уже содержит чистый Info-Hash раздачи в формате "magnet:?xt=urn:btih:..."
	# Мы убираем из кода любые склейки с download.php, полностью освобождая плеер от сайта трекера!
	magnet_link = url
#	file_index = file_id  # Порядковый номер файла серии (0, 1, 2...)

	try: pu = f"plugin://{ADDON_URL}?mode=Root"
	except: pu = ''

	try: actual_title = info.get('title', title)
	except: actual_title = title if title else 'Video File'

	# Заэкранируем параметры по стандарту плагина ТАМ
	quoted_url = quote(magnet_link)
	quoted_info = quote(repr({'title': actual_title}))
	quoted_pu = quote(pu)

	if __settings__.getSetting("view_hist") == 'true':
		try:
			_db_cache_instance.save_to_view_history(topic_id, dwnld_id, unquote(params.get('topic_title')))
		except Exception as e:
			log_debug(f"(Play) Ошибка записи в историю SQLite: {str(e)}", level=xbmc.LOGERROR)

	# Собираем пусковой URL для ТАМа по канону чистого Magnet-вещания.
	# proxy и cookie больше НЕ НУЖНЫ, TorrServer заберет раздачу из DHT сети напрямую!
	tam_purl = f"plugin://plugin.video.tam/?mode=play&ind={file_id}&url={quoted_url}&info={quoted_info}&purl={quoted_pu}"
#	log_debug(f"[NNM-PLAY] Итоговый чистый Magnet-URL передан в ТАМ: {tam_purl}")

	# Передаем команду разрешения ссылки встроенному VideoPlayer Kodi строго по дескриптору handle
	listitem = xbmcgui.ListItem(actual_title)
	listitem.setPath(tam_purl)
	# Каноническое разрешение ссылки Kodi строго по handle
	xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, listitem)

elif mode == 'History':
#	log_debug(f"Mode is : {mode}")
	History()

elif mode == 'ViewHistory':
#	log_debug(f"Mode is : {mode}")
	ViewHistory()

elif mode == 'TvDB':
#	log_debug(f"Mode is : {mode}")
	manual_Fetch_TvDB(topic_id, dwnld_id, title)

elif mode == 'DeleteView':
#	log_debug(f"Mode is : {mode}")
	_db_cache_instance.delete_from_view_history(topic_id)
	xbmc.executebuiltin("Container.Refresh")
elif mode == 'ActorSearch':
	# Просто вызываем нашу монолитную функцию!
	# Она сама покажет клавиатуру, сама выкатит диалог с обложками и сама обновит контейнер.
	SearchByActor()

#elif mode == 'OpenHistory':
#	# Логируем факт перехода пользователя в меню истории
##	log_debug("[NNM-ROUTER] Открытие выделенного подраздела истории поиска.")
#
#	# Вызываем нашу новую функцию отрисовки экрана истории
#	ShowHistory()

#elif mode == 'RemoveSingleHistory':
#	# В переменной url у нас прилетит заэкранированное слово, которое нужно стереть
##	log_debug(f"[NNM-ROUTER] Запущена команда точечного удаления слова из XML: {url}")
#
#	# Вызываем нашу новую функцию удаления элемента
#	remove_from_history(url)

#elif mode == 'ClearHistory':
#	# Логируем факт вызова команды очистки
##	log_debug("[NNM-ROUTER] Запущена команда полной очистки истории поиска.")
#	# Вызываем нашу новую точечную функцию сброса настройки в "[]"
#	ClearHistory()

# Обязательно убедитесь, что в самом конце файла НЕТ никаких лишних строк вроде c.close()
#c.close()

