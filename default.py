#!/usr/bin/python
# -*- coding: utf-8 -*-
import sys, os, time, re
import xbmc, xbmcgui, xbmcplugin, xbmcaddon
import ssl
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
	xbmc.log("[NNM-ASYNC-TVDB] Модуль tvdb_v4_official успешно импортирован скриптом.", level=xbmc.LOGINFO)
except Exception as imp_err:
	tvdb_v4_official = None
	xbmc.log(f"[NNM-ASYNC-TVDB-ERROR] Не удалось импортировать tvdb_v4_official.py! Ошибка: {str(imp_err)}", level=xbmc.LOGERROR)

# =====================================================================
# ШАГ 1: ГЛОБАЛЬНЫЕ ОБЪЕКТЫ ЯДРА KODI И ОБХОД СЕРТИФИКАТОВ UBUNTU
# =====================================================================
os.environ['PYTHONHTTPSVERIFY'] = '0'
ubuntu_certs_path = '/etc/ssl/certs/ca-certificates.crt'
if os.path.exists(ubuntu_certs_path):
	os.environ['REQUESTS_CA_BUNDLE'] = ubuntu_certs_path
	os.environ['CURL_CA_BUNDLE'] = ubuntu_certs_path

PLUGIN_NAME = 'NNM-Club'
addon = xbmcaddon.Addon(id='plugin.video.nnmclub')
__settings__ = xbmcaddon.Addon(id='plugin.video.nnmclub')

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

#def log_debug(message, level=xbmc.LOGINFO):
#	# Проверяем, включен ли Debug в настройках вашего аддона
#	# (Или в системных настройках Kodi через xbmc.getCondVisibility)
#	if __settings__.getSetting("debug") == "true" or xbmc.getCondVisibility('System.Logging'):
#		# Записываем сообщение в системный лог Kodi с пометкой [NNM-DEBUG]
#		xbmc.log(f"[NNM-DEBUG] {message}", level)

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
xbmcplugin.setContent(int(sys.argv[1]), 'movies')

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

def info_to_utf(inf):
	if sys.version_info.major > 2:
		info = eval(inf.replace("\\x", "\\\\x").replace("\\r", "\\\\r").replace("\\n", "\\\\n").replace("\\\\xa0", " "))
		for key in info.keys():
			try: info[key]=eval("b'"+info[key]+"'").decode()
			except: pass
	else:
		info = eval(inf)
	return info

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def get_params():
	param=[]
	paramstring=sys.argv[2]
	if len(paramstring)>=2:
		params=sys.argv[2]
		cleanedparams=params.replace('?','')
		if (params[len(params)-1]=='/'):
			params=params[0:len(params)-2]
		pairsofparams=cleanedparams.split('&')
		param={}
		for i in range(len(pairsofparams)):
			splitparams={}
			splitparams=pairsofparams[i].split('=')
			if (len(splitparams))==2:
				param[splitparams[0]]=splitparams[1]
	return param

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def inputbox():
	skbd = xbmc.Keyboard()
	skbd.setHeading('Поиск:')
	skbd.doModal()
	if skbd.isConfirmed():
		SearchStr = skbd.getText()
		return SearchStr
	else:
		return ""

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def showMessage(heading, message, times = 50000):
	xbmc.executebuiltin('XBMC.Notification("%s", "%s", %s, "%s")'%(heading, message, times, thumb))

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

# Глобальный объект для удержания сессии API на время работы аддона
_nnm_api_instance = None

def GET_NNM(url):

	global _nnm_api_instance
	# Инициализируем сессию через ваш класс API строго ОДИН раз за запуск аддона
	if _nnm_api_instance is None:
		log_debug("Первичная инициализация глобального инстанса NNMClubAPI...", level=xbmc.LOGINFO)
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
				log_debug(f"ДИНАМИЧЕСКИЙ ПРОКСИ ПОДКЛЮЧЕН К СЕССИИ: {LOCAL_PROXY}", level=xbmc.LOGINFO)

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
		log_debug(f"Куки из GUI подтянуты. CF: {cf_token[:10]}..., SID: {sid_token[:10]}...", level=xbmc.LOGINFO)
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
		log_debug(f"Отправка запроса через сессию API: {url}", level=xbmc.LOGINFO)
		# Если в вашем классе nnm_api.py в __init__ прописано self.session.proxies = proxy,
		# то данный вызов гарантированно пойдет через прокси-сервер!
		response = _nnm_api_instance.safe_get(url, timeout=15)
		if response is None or response.status_code != 200:
			log_debug(f"[NNM-DEBUG] Критическая ошибка сети в GET_NNM: Сервер вернул пустой ответ для {url}", level=xbmc.LOGERROR)
			return ''
		# Декодируем windows-1251 контент сайта
		html_text = response.content.decode('windows-1251', errors='ignore')
		# Проверка на срабатывание защитной заглушки самого сайта
		if "LITTLE GUYS ESTIMATOR" in html_text or "estimator" in html_text.lower():
			log_debug("КРИТИЧЕСКАЯ ОШИБКА: Запрос заблокирован защитой LGE! Обновите cookies.txt.", level=xbmc.LOGERROR)
			showMessage('NNM-Club', 'Защита LGE заблокировала запрос. Обновите куки.', 5000)
			return ''
		return html_text

	except Exception as e:
		log_debug(f"Критическая ошибка сети в GET_NNM: {str(e)}", level=xbmc.LOGERROR)
		showMessage('Ошибка сети', str(e), 4000)
		return ''

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def AddItem(Title = "", mode = "", id='0', url='', inf={}, total=100):

	# =====================================================================
	# СВЕРХПОДРОБНЫЙ БЛОК АСИНХРОННОЙ РАСПАКОВКИ И АРТ-ФИЛЬТРАЦИИ В ADDITEM
	# =====================================================================
	screen_title = Title
	clean_title = Title

	# Проверяем тип прилетевшего объекта на входе в конвейер
	if isinstance(Title, dict):
		screen_title = Title.get('f', '')
		clean_title = Title.get('c', '')
		log_debug(f"[NNM-ADDITEM-DIAG] [ASYNC-STAGE] Успешно распакован dict. Экран: '{screen_title[:35]}...', Чистый: '{clean_title}'", level=xbmc.LOGINFO)
	else:
		log_debug(f"[NNM-ADDITEM-DIAG] [ASYNC-STAGE] Прилетела обычная строка: '{Title}'", level=xbmc.LOGINFO)

	# Инициализируем базовые переменные заглушек до проверки кэша
	cover = icon  # По умолчанию выставляем стандартную иконку плагина (заглушку)
	plot_desc = "[ Идет фоновый поиск постера и описания фильма в TVDB... ]"
	prod_year = ""
	tvdb_rating = 0.0

	# ЖЕСТКАЯ СЕЛЕКТИВНАЯ ПРИВЯЗКА: Работаем строго с топиками фильмов/сериалов
	if mode in ["Topic", "Torrents"] and id != '0':
		log_debug(f"[NNM-ADDITEM-DIAG] [ASYNC-STAGE] Элемент признан раздачей (mode:{mode}, ID:{id}). Проверяем локальный SQLite.", level=xbmc.LOGINFO)

		# 1. МГНОВЕННО ЗАГЛЯДЫВАЕМ В ЛОКАЛЬНУЮ БАЗУ ДАННЫХ SQLITE СACHE.DB
		movie_meta = _db_cache_instance.get_poster(id)

		if movie_meta:
			# КЭШ СРАБОТАЛ: Извлекаем накопленный ранее паспорт фильма из базы за 0 секунд
			cover = movie_meta.get('cover', icon)
			plot_desc = movie_meta.get('plot', '')
			prod_year = movie_meta.get('year', '')
			tvdb_rating = movie_meta.get('rating', 0.0)
			log_debug(f"[NNM-ADDITEM-DIAG] [ASYNC-CACHE-HIT] Найдено в cache.db для ID {id}! Постер: {cover[:40]}... | Рейтинг: {tvdb_rating}", level=xbmc.LOGINFO)
		else:
			# КЭШ ПУСТ: Фильм выводится на экран впервые
			log_debug(f"[NNM-ADDITEM-DIAG] [ASYNC-CACHE-MISS] В cache.db пусто для ID {id}. Отправляем фильм в фоновую очередь.", level=xbmc.LOGINFO)

			# Инициализируем глобальную асинхронную очередь текущей страницы
			global _async_fetch_queue
			if '_async_fetch_queue' not in globals():
				_async_fetch_queue = []

			# Если этой пары (ID, Имя) еще нет в очереди — бережно добавляем её на удержание
			if (id, clean_title) not in _async_fetch_queue:
				_async_fetch_queue.append((id, clean_title))
				log_debug(f"[NNM-ADDITEM-DIAG] [ASYNC-QUEUE-ADD] Фильм '{clean_title}' (ID:{id}) успешно добавлен в очередь. Всего задач: {len(_async_fetch_queue)}", level=xbmc.LOGINFO)
	else:
		# Если это папка раздела, кнопка истории или пагинация
		log_debug(f"[NNM-ADDITEM-DIAG] [ASYNC-STAGE] Пропуск конвейера TMDB (Системный элемент). Mode: {mode}, ID: {id}", level=xbmc.LOGINFO)
		cover = icon
		plot_desc = ""

	fanart = cover

	# Накатываем все собранные переменные (или данные из БД, или заглушки) в инфо-пакет Kodi
	info_labels = {
		'title': inf.get('title', clean_title),
		'plot': plot_desc,
		'year': int(prod_year) if (prod_year and prod_year.isdigit()) else 0,
		'rating': float(tvdb_rating) if tvdb_rating else 0.0,
		'genre': 'NNM-Club Торрент'
	}

	log_debug(f"[NNM-ADDITEM-DIAG] [ASYNC-STAGE] xbmcgui.ListItem Показываем элемент {screen_title} в списке.", level=xbmc.LOGINFO)
	listitem = xbmcgui.ListItem(screen_title)
	try: listitem.setArt({ 'poster': cover, 'fanart' : fanart, 'thumb': cover, 'icon': cover})
	except: pass

	listitem.setInfo(type="Video", infoLabels=info_labels)

	if mode == "Topic":
		purl = f"{sys.argv[0]}?mode=Topic&id={str(id)}&url={quote(url)}&title={quote(screen_title)}&info={quote(repr(inf))}"
		listitem.setProperty('IsPlayable', 'false')
		is_folder = True
	else:
		purl = f"{sys.argv[0]}?mode={mode}&id={str(id)}&info={quote(repr(inf))}"
		if url != "": purl = purl + '&url=' + quote(url)
		is_folder = True

	xbmcplugin.addDirectoryItem(int(sys.argv[1]), purl, listitem, is_folder, total)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def Root():
	AddItem("[B][COLOR lime][ Поиск во всех разделах  ][/COLOR][/B]", "Search")
	if __settings__.getSetting("CategoryON") == 'true':
		AddItem("[B][COLOR lime][ Разделы форума                 ][/COLOR][/B]", "Category", url="1")
	# Проверяем, включена ли галочка сохранения истории в настройках GUI Kodi
	if __settings__.getSetting("HistoryON") == 'true':
		AddItem("[B][COLOR silver]История поиска[/COLOR][/B]", "History")

	xbmcplugin.setPluginCategory(int(sys.argv[1]), PLUGIN_NAME)
	xbmcplugin.endOfDirectory(int(sys.argv[1]))

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def Category(forum_id):
	# Выводим кнопку локального поиска на экране Kodi самым первым пунктом
	# Передаем специальный режим "SearchInCategory" и ID текущего форума
	if forum_id != '1':
		AddItem("[B][COLOR lime][ Поиск в этом разделе ][/COLOR][/B]", "SearchInCategory", url=forum_id)

	if forum_id == '1':
		# Читаем главные папки из нашего нового файла
		for cat in categories.MAIN_CATEGORIES:
			# Если у папки есть прописанные подкатегории в SUB_CATEGORIES, открываем её как подменю
			if cat['id'] in categories.SUB_CATEGORIES:
				AddItem(f"[B][COLOR white]{cat['title']}[/COLOR][/B]", "Category", url=cat['id']) # 1й уровень вложения
			else:
				AddItem(f"[B][COLOR green]{cat['title']}[/COLOR][/B]", "Category", url=cat['id']) # 2й уровень вложения

	elif forum_id in categories.SUB_CATEGORIES:
		# Читаем вложенные папки для выбранного parent_id
		for sub in categories.SUB_CATEGORIES[forum_id]:
			AddItem(f"[COLOR silver]{sub['title']}[/COLOR]", "SubCategory", url=sub['id']) # 3й уровень вложения

	xbmcplugin.setPluginCategory(int(sys.argv[1]), PLUGIN_NAME)
	xbmcplugin.endOfDirectory(int(sys.argv[1]))

	# ... дальше идет ваш стандартный вывод списка фильмов из этой категории, который мы написали ранее

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def List(forum_id, start_index):

	log_debug(f"(List) Листинг содержимого раздела id: {forum_id}, смещение: {start_index}")

	# Получаем данные через ваш оптимизированный метод
	entries = GetEntries('', forum_id=forum_id, start=start_index)

	log_debug(f"(List) вернул подтвержденных тем с MAGNET-ссылками: {len(entries)}")

	# Отрисовываем список на экране Kodi
	ShowEntries(entries, is_search=False, start_index=start_index)

	if len(entries) >= 50:
		next_start = start_index + 50
		AddItem("[B][COLOR gold][ Следующая страница >>> ][/COLOR][/B]", "HList", id='0', url=f"{forum_id}&start={next_start}", total=len(entries) + 1)

	xbmcplugin.endOfDirectory(int(sys.argv[1]))

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def Search(t='', start_index=0, forum_id=-1):
	# Если запрос не передан, запрашиваем у пользователя
	if not t:  # В Python 'if not t' поймает и пустую строку '', и None
		t = inputbox()

	# Защита: Если пользователь нажал Отмену или ничего не ввёл,
	# просто тихо выходим из функции, не мучая трекер
	if not t or t.strip() == '':
		log_debug("(Search) Пользователь отменил ввод или ввёл пустую строку")
		return # Вот тут пустой return — мы просто прерываем работу функции

	# --- Дальнейший код выполнится, ТОЛЬКО если текст железно есть ---
	log_debug(f"(Search) Запуск API поиска: {t}, смещение: {start_index}")

	if __settings__.getSetting("HistoryON") == 'true' and start_index == 0:
		try:
			_db_cache_instance.add_history(t, forum_id)
		except Exception as e:
			log_debug(f"(Search) Ошибка записи в историю SQLite: {str(e)}", level=xbmc.LOGERROR)

	# Приводим forum_id к нужному для API виду
	forum_id = None if forum_id == -1 else forum_id

	# Получаем данные через ваш оптимизированный метод
	entries = GetEntries(t, forum_id=forum_id, start=start_index)

	log_debug(f"(Search) вернул подтвержденных тем с MAGNET-ссылками: {len(entries)}")

	# Отрисовываем список на экране Kodi
	ShowEntries(entries, is_search=True, start_index=start_index)

	if len(entries) >= 50:
		next_start = start_index + 50
		AddItem("[B][COLOR gold][ Следующая страница >>> ][/COLOR][/B]", "HSearch", '0', url = f"{t}&start={next_start}", total=len(entries) + 1)

	xbmcplugin.endOfDirectory(int(sys.argv[1]))

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def GetEntries(query, forum_id=None, start=0):

	encoded_query = urllib.parse.quote(query)

	# =====================================================================
	# ТОЧЕЧНЫЙ БЛОК ОПРЕДЕЛЕНИЯ КАТЕГОРИИ ПОИСКА
	# =====================================================================
	# По умолчанию задаем f=-1 (Флаг nnm-club для глобального поиска по всему трекеру)
	f_param = "f=-1"

	# Если из default.py прилетел конкретный ID раздела (поиск внутри категории)
	# Мы проверяем, что forum_id передан, не равен None и не равен дефолтному -1
	if forum_id and forum_id != -1:
		# При поиске по разделу nnm-club требует синтаксис f[]=ID_РАЗДЕЛА.
		# Формируем точную одиночную строку (например, "f[]=1344")
		f_param = f"f[]={forum_id}"

	# Собираем итоговый URL поискового запроса к зеркалу трекера.
	# o=10&s=2 — это ваши штатные параметры сортировки выдачи сайта
	entries_url = f"{site_url_base}/forum/tracker.php?{f_param}&nm={encoded_query}&o=10&s=2&start={start}"

	results = []

	try:
		log_debug(f"(GetEntries) Отправка полностью авторизованного запроса: {entries_url}", level=xbmc.LOGINFO)

		# Запрос через вашу безопасную GET_NNM
		html = GET_NNM(entries_url)

		if not html:
			log_debug("(GetEntries) Ошибка: GET_NNM вернул пустой HTML", level=xbmc.LOGERROR)
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

		log_debug(f"(GetEntries) Сквозной парсинг строк prow. Успешно собрано чистых тем: {len(matches)}", level=xbmc.LOGINFO)

		for topic_id, title, dl_id, size_text, seeds in matches:
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
					'dnld_id': dl_id,          # Числовой ID файла скачивания для Topic
					'size': clean_size,       # Строка размера (например, "24.4 GB")
					'seeds': seeds.strip()     # Количество сидов
				})
			except:
				continue

		return results

	except Exception as e:
		log_debug(f"(GetEntries) Ошибка сети при поиске: {str(e)}", level=xbmc.LOGERROR)
		return []



#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def ShowEntries(entries, is_search=False, start_index=0):

	"""Простой и стабильный вывод списка топиков на экран Kodi"""
	if not entries:
		log_debug("(ShowEntries) Список топиков пуст. Нечего отображать.")
		# Можно вывести уведомление Kodi, что ничего не найдено
		return

	# =====================================================================
	# ВОТ СЮДА (СТРОГО МЕЖДУ ЗАПРОСОМ И НАЧАЛОМ ЦИКЛА FOR) ВСТАЕТ СОРТИРОВКА:
	# =====================================================================
	if __settings__.getSetting("sort") == "1":
		try:
			entries = sorted(entries, key=lambda k: int(k.get('seeds', 0)), reverse=True)
			log_debug("(ShowEntries) Массив отсортирован строго по количеству сидов.")
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
			log_debug("(ShowEntries) Выполнена комбинированная сортировка: Разрешение -> Сиды -> Имя.")
		except Exception as e:
			log_debug(f"(ShowEntries) Ошибка комбинированной сортировки: {str(e)}", level=xbmc.LOGERROR)

	displayed_count = 0
	# =====================================================================
	# ТОЧЕЧНЫЙ БЛОК ФОРМАТИРОВАНИЯ СТРОКИ В СТИЛЕ RUTOR
	# =====================================================================
	for item in entries:
		if not filtr(item['title']):
			continue

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
		display_title = f"{resolution_tag}|s:{seeds_str}|{size_text}| {item['title']}"

		# Собираем ваш структурированный словарь
		title_packet = {
			'f': display_title,
			'c': item['title']
		}

		# Печатаем в лог факт отправки элемента в AddItem
		log_debug(f"(ShowEntries) Отправка в AddItem -> TopicID: {item['topic_id']} | Чистый Title: '{item['title']}'", level=xbmc.LOGINFO)

		inf = {
			'title': item['title'],
			'code': item['topic_id'],
			'dnld_id': item['dnld_id']
		}

		AddItem(title_packet, "Topic", id=item['topic_id'], url=item['dnld_id'], inf=inf, total=len(entries))

#		displayed_count += 1
#	if len(entries) >= 50:
#		next_start = start_index + 50
#		AddItem("[B][COLOR gold][ Следующая страница >>> ][/COLOR][/B]", mode='HSearch' if is_search else 'HList', id='0', url=f"{forum_id}&start={next_start}", total=displayed_count + 1)

	xbmcplugin.setPluginCategory(int(sys.argv[1]), PLUGIN_NAME)
	# =====================================================================
	# АСИНХРОННЫЙ ПУСК ФОНОВОГО ПОТОКА ЗАГРУЗКИ ПОСТЕРОВ И ИНФЫ
	# =====================================================================
	global _async_fetch_queue

	if '_async_fetch_queue' in globals() and _async_fetch_queue:
		# Создаем независимый фоновый поток Python, скармливая ему нашу собранную очередь
		bg_thread = threading.Thread(target=background_fetch_worker, args=(_async_fetch_queue,))
		# Флаг daemon=True гарантирует, что поток корректно закроется, если юзер выйдет из Kodi
		bg_thread.daemon = True
		# Запускаем поток в свободный асинхронный полет!
		bg_thread.start()

		# Очищаем глобальную очередь текущей страницы для следующего захода
		_async_fetch_queue = []

	xbmcplugin.endOfDirectory(int(sys.argv[1]))

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def Topic(topic_id, download_id):

	log_debug(f"(Topic) Запуск парсера топика {topic_id}")

	# 1. СНАЧАЛА ЗАХОДИМ НА СТРАНИЦУ САМОГО ТОПИКА
	topic_page_url = f"{site_url_base}/forum/viewtopic.php?t={topic_id}"

	html = GET_NNM(topic_page_url)

	real_hash = ""

	# ИЩЕМ ТАБЛИЦУ btTbl И ВЫТАСКИВАЕМ ИЗ НЕЁ ЧИСТЫЙ MAGNET-ХЭШ
	table_match = re.search(r'class="btTbl".*?</table>', html, re.DOTALL | re.IGNORECASE)
	if table_match:
		bt_table_html = table_match.group(0)
		log_debug("(Topic) Таблица btTbl успешно изолирована.")

		# Вытаскиваем 40 символов Info-Hash из magnet-ссылки внутри таблицы
		hash_match = re.search(r'magnet:\?xt=urn:btih:([a-fA-F0-9]{40})', bt_table_html, re.IGNORECASE)
		if hash_match:
			real_hash = hash_match.group(1)
			log_debug(f"(Topic) Истинный Info-Hash раздачи найден: {real_hash}")

	# 2. ТЕПЕРЬ ДЕЛАЕМ НАШ ШТАТНЫЙ AJAX ЗАПРОС К СПИСКУ СЕРИЙ
	ajax_filelist_url = f"{site_url_base}/forum/filelst.php?attach_id={download_id}"
	html = GET_NNM(ajax_filelist_url)

	# ТОТАЛЬНАЯ РЕГУЛЯРКА ПОД ОДНОСТРОЧНЫЙ JS-ОТВЕТ (Ваша оригинальная логика)
	file_pattern = r'class="genmed"\s+align="left">\s*([^<]+?\.(?:mkv|mp4|avi|ts|m2ts|mp3|flac|iso))'
	files = re.findall(file_pattern, html, re.IGNORECASE)

	if not files and html:
		file_pattern = r'[^"\'\s>]+?\.(?:mkv|mp4|avi|ts|m2ts|iso)'
		files = re.findall(file_pattern, html, re.IGNORECASE)
		files = [f for f in files if f.lower().endswith(('.mkv', '.mp4', '.avi', '.ts'))]

	# === ВЫВОД ЭЛЕМЕНТОВ СЕРИЙ В KODI ===

	if not files:
		log_debug("(Topic) AJAX: Список файлов пуст.", level=xbmc.LOGINFO)
		display_title = "Нет файлов для воспроизведения."
#		if real_hash:
#			download_url = f"magnet:?xt=urn:btih:{real_hash}"
#		else:
#			download_url = f"{site_url_base}/forum/download.php?id={download_id}"
#		purl = f"{sys.argv[0]}?mode=Play&url={quote(download_url)}&id=0&title={quote(display_title)}&info={quote(repr({'title': display_title}))}"
		listitem = xbmcgui.ListItem(display_title)
		listitem.setInfo(type="Video", infoLabels={'title': display_title})
		listitem.setProperty('IsPlayable', 'false')
		xbmcplugin.addDirectoryItem(int(sys.argv[1]), purl, listitem, False, 1)
	else:
		log_debug(f"(Topic) AJAX: Найдено файлов для вывода серий: {len(files)}", level=xbmc.LOGINFO)
		for index, filename in enumerate(files):
			clean_filename = filename.strip()
			if not clean_filename:
				continue

			display_title = f"{clean_filename}"
			if real_hash:
				download_url = f"magnet:?xt=urn:btih:{real_hash}"
			else:
				download_url = f"{site_url_base}/forum/download.php?id={download_id}"
			purl = f"{sys.argv[0]}?mode=Play&url={quote(download_url)}&id={str(index)}&title={quote(clean_filename)}&info={quote(repr({'title': clean_filename}))}"
			listitem = xbmcgui.ListItem(clean_filename)
			listitem.setInfo(type="Video", infoLabels={'title': clean_filename})
			listitem.setProperty('IsPlayable', 'true')
			xbmcplugin.addDirectoryItem(int(sys.argv[1]), purl, listitem, False, len(files))

	# Закрываем каталог страницы серий
	xbmcplugin.setPluginCategory(int(sys.argv[1]), PLUGIN_NAME)
	xbmcplugin.endOfDirectory(int(sys.argv[1]))

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
	xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, listitem)


#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def background_fetch_worker(items_to_fetch):
	"""
	Внутренний фоновый рабочий поколения 3.0 (Миграция на TheTVDB v4):
	По очереди ищет метаданные и обложки через официальный движок TVDB,
	наполняя SQLite базу cache.db со стопроцентной отладкой в лог-файл!
	"""
	log_debug(f"[NNM-ASYNC-TVDB] Фоновый поток запущен. Элементов в очереди на обработку: {len(items_to_fetch)}", level=xbmc.LOGINFO)

	if tvdb_v4_official is None:
		log_debug("[NNM-ASYNC-TVDB-ERROR] Критическая остановка: библиотека tvdb_v4_official.py отсутствует на диске!", level=xbmc.LOGERROR)
		return

	# Считываем ваш новый ключ TVDB из настроек GUI дополнения
	tvdb_api_key = __settings__.getSetting('tvdb_key').strip().replace(" ", "")
	if not tvdb_api_key:
		log_debug("[NNM-ASYNC-TVDB-ERROR] Поле ключа в настройках GUI пустое! Заполните его. Выход из потока.", level=xbmc.LOGERROR)
		return

	log_debug(f"[NNM-ASYNC-TVDB] Попытка авторизации ключа {tvdb_api_key[:10]}... на серверах ://thetvdb.com", level=xbmc.LOGINFO)

	try:
		# ИНИЦИАЛИЗИРУЕМ ОФИЦИАЛЬНЫЙ КЛLabelЕНТ КЛАССА ИЗ GIT МАНLabelФЕСТА
		# Скрипт сам выполнит подстановку login_url и вызов urllib.request!
		tvdb_client = tvdb_v4_official.TVDB(apikey=tvdb_api_key)
		log_debug("[NNM-ASYNC-TVDB] УСПЕШНАЯ АВТОРИЗАЦИЯ! JWT-токен TVDB v4 получен и сохранен в сессии.", level=xbmc.LOGINFO)
	except Exception as auth_err:
		log_debug(f"[NNM-ASYNC-TVDB-ERROR] Сервер TVDB v4 отклонил ваш API-ключ! Причина: {str(auth_err)}", level=xbmc.LOGERROR)
		return

	# Запускаем асинхронный цикл обработки очереди страниц
	for topic_id, title_text in items_to_fetch:
		log_debug(f"[NNM-ASYNC-TVDB] ---> СТАРТ ОБРАБОТКИ ЭЛЕМЕНТА очереди -> ID топика: {topic_id} | Исходная строка: '{title_text}'", level=xbmc.LOGINFO)

		try:
			# Очищаем название от скобок (оставляем только чистое имя "Черный Плащ")
			clean_name = re.sub(r'[/([].*', '', title_text).strip()
			year_match = re.search(r'(19\d{2}|20\d{2})', title_text)
			year_val = year_match.group(1) if year_match else ""

			log_debug(f"[NNM-ASYNC-TVDB] Фильтр скобок завершен. Имя для поиска: '{clean_name}', Год: '{year_val}'", level=xbmc.LOGINFO)

			if not clean_name:
				log_debug(f"[NNM-ASYNC-TVDB-WARN] Чистое имя для ID {topic_id} пустая строка! Пропуск.", level=xbmc.LOGWARNING)
				continue

			# Вызываем состояние вашей новой галочки разблокировки из GUI (pic_proxy)
			# Привязка к прокси-серверам не требуется, так как TVDB использует urllib.request напрямую!
			log_debug(f"[NNM-ASYNC-TVDB] Отправка официального поискового API v4 вызова для '{clean_name}'...", level=xbmc.LOGINFO)

			# ВЫЗЫВАЕМ ОФИЦИАЛЬНЫЙ МЕТОД ПОИСКА ИЗ КЛАССА TVDB C ДЕКСПРLabelПТОРОМ ЯЗЫКА
			# Библиотека сама соберет url.construct("search") со всеми параметрами!
			search_results = tvdb_client.search(query=clean_name, language='rus')

			if search_results:
				total_hits = len(search_results)
				log_debug(f"[NNM-ASYNC-TVDB] Ответ от API v4 получен! Найдено совпадений в базе TVDB: {total_hits}", level=xbmc.LOGINFO)

				# Извлекаем самый первый (наиболее точный) объект из возвращенного списка
				first_match = search_results[0]

				# Разбираем ключи строго по спецификации официальной библиотеки v4!
				poster_url = first_match.get('image_url') or first_match.get('image', '')
				plot_text = first_match.get('overview', '')
				movie_year = first_match.get('year', year_val)

				# Читаем рейтинг популярности score
				tvdb_score = first_match.get('score', 0.0)
				# Приводим к стандартной 10-бальной шкале для медиатеки Kodi
				rating_val = float(tvdb_score) / 10.0 if tvdb_score > 10 else float(tvdb_score)

				if poster_url:
					# Если ссылка на постер относительная, достраиваем её (у TVDB картинки лежат на сервере артворков)
					if poster_url.startswith('/'):
						poster_url = f"https://thetvdb.com{poster_url}"

					log_debug(f"[NNM-ASYNC-TVDB-SUCCESS] Метаданные TVDB успешно получены! Постер: {poster_url} | Год: {movie_year} | Рейтинг: {rating_val}", level=xbmc.LOGINFO)

					# НАВСЕГДА СОХРАНЯЕМ ПАСПОРТ ФИЛЬМА В НАШУ СУПЕР-БАЗУ SQLITE CACHE.DB
					# Наша функция AddItem заберет эти данные из SQLite при следующем рефреше экрана!
					_db_cache_instance.save_poster(topic_id, poster_url, plot_text, movie_year, rating_val)
				else:
					log_debug(f"[NNM-ASYNC-TVDB-WARN] Фильм найден в TVDB v4, но поле image_url пустое.", level=xbmc.LOGWARNING)
			else:
				log_debug(f"[NNM-ASYNC-TVDB-WARN] Индексы TVDB вернули пустой массив результатов для запроса '{clean_name}'", level=xbmc.LOGWARNING)

			# Обязательная микро-пауза 150 миллисекунд, чтобы сервер не выдал Rate Limit
			xbmc.sleep(150)

		except Exception as item_err:
			log_debug(f"[NNM-ASYNC-TVDB-ERROR] Сбой фоновой обработки элемента ID {topic_id}: {str(item_err)}", level=xbmc.LOGERROR)
			continue

	log_debug("[NNM-ASYNC-TVDB] Фоновый поток TVDB v4 успешно завершил обработку всей очереди текущей страницы.", level=xbmc.LOGINFO)


#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def get_info(inf):
	# Возвращаем чистый словарь без ключа 'id', чтобы Kodi не ругался
	info = {
		'title': inf.get('title', ''),
		'genre': 'NNM-Club Торрент'
	}
	try:
		if 'type' in inf: info['type'] = inf['type']
	except:
		pass
	return info

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

def US(text):
	Search(text)

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def filtr(title):
	f=True
	Tresh = ["Repack"," PC ","XBOX","RePack","FB2","TXT","DOC"," MP3"," JPG"," PNG"," SCR"]
	for i in Tresh:
		if i in title: f=False

	if __settings__.getSetting("Hide Scr") == 'true':
		Scr = ["CAMRip", ") TS", ") TC", ") ТС", "CamRip", " DVDScr"]
		for i in Scr:
			if i in title: f=False

	if __settings__.getSetting("EnabledFiltr") == 'true':
		Flt = __settings__.getSetting("Filtr").split(',')
		for i in Flt:
			if i.strip() in title: f=False

	return f

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def SetViewMode():
	n = int(__settings__.getSetting("ListView"))
	if n>0:
		xbmc.executebuiltin("Container.SetViewMode(0)")
		for i in range(1,n):
			xbmc.executebuiltin("Container.NextViewMode")

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

def History():
	# =====================================================================
	# ОБНОВЛЕННЫЙ БЛОК ОТРИСОВКИ ОДОБРЕННОГО ЭКРАНА ИСТОРИИ ИЗ SQLITE
	# =====================================================================
	# 1. Самым первым пунктом на экране всегда выводим кнопку ПОЛНОЙ очистки
	AddItem("[COLOR red][ Очистить всю историю поиска ][/COLOR]", "ClearHistory", id='0', url='')

	# 2. Вытаскиваем список сохраненных поисков из SQLite через наш глобальный класс
	# Метод возвращает нам список кортежей вида: [(текст_запроса, id_раздела), ...]
	saved_queries = _db_cache_instance.get_history()

	if saved_queries:
		for q_word, f_id in saved_queries:
			# Если у этого поискового запроса в базе зафиксирован ID раздела
			if f_id and str(f_id).strip() != "":
				display_label = f"{q_word}"
				# Склеиваем параметры через амперсанд. Ваш роутер 'HSearch'
				# сам расколет эту строку по условию "if '&start=' in url:"!
				purl_data = f"{q_word}&start=0&forum_id={f_id}"
			else:
				# Если поиск был глобальным (по всему трекеру)
				display_label = f"{q_word}"
				purl_data = f"{q_word}&start=0"

			# Отрисовываем пункт истории на экране Kodi.
			# В аргумент url передаем собранную строку параметров purl_data
			AddItem(display_label, 'HSearch', '0', purl_data)

	xbmcplugin.setPluginCategory(int(sys.argv[1]), PLUGIN_NAME)
	xbmcplugin.endOfDirectory(int(sys.argv[1]))

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
#		log_debug(f"[NNM-HISTORY] Слово '{t}' успешно удалено из истории.")

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
#		log_debug("[NNM-HISTORY] Поле истории в результирующем settings.xml успешно очищено.")
#	except Exception as e:
#		log_debug(f"[NNM-HISTORY] Ошибка очистки XML-поля настроек: {str(e)}", level=xbmc.LOGERROR)

#	# Принудительно обновляем текущий экран Kodi, чтобы история мгновенно исчезла из меню
#	xbmc.executebuiltin("Container.Refresh")

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████
# === ОФИЦИАЛЬНЫЙ СТАРТ ПЛАГИНА (САМЫЙ КОНЕЦ ФАЙЛА) ===

params = get_params()

mode  = ''
url   = ''
title = ''
id    = '0'
info  = {}

try:    mode  = unquote(params["mode"])
except: pass
try:    url   = unquote(params["url"])
except: pass
try:    title = unquote(params["title"])
except: pass
try:    id    = unquote(params["id"])
except: pass
try:    info  = eval(unquote(params["info"]))
except: pass

#█████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████

# Строгий последовательный вызов режимов, исключающий наложение потоков
if mode == '':
	# Самый первый вход в плагин
	log_debug(f"START ADDON: NO Mode YET")
	Root()

elif mode == 'SubCategory':
	# Вход во вложенную категорию (в переменной url лежит ID родительской папки, например 724)
	log_debug(f"Mode is : {mode}")
	List(forum_id=url, start_index=0)

elif mode == 'Category':
#    try: start_val = int(id)
#    except: start_val = 0
#    Category(url, start_val)
	log_debug(f"Mode is : {mode}")
	Category(url)

elif mode == 'Search':
	log_debug(f"Mode is : {mode}")
	Search()

elif mode == 'SearchInCategory':
	# Запрашиваем текст у пользователя
	log_debug(f"Mode is : {mode}")
	t = inputbox()
	if t != '':
		# Вызываем обычную функцию поиска, но передаем ей ID нашего форума в виде списка!
		# Наш nnm_api.py автоматически подставит f[]=ID в URL запроса
		Search(t, start_index=0, forum_id=[url])

elif mode == 'HSearch':
	log_debug(f"Mode is : {mode} | Данные из истории: {url}")

	# Инициализируем дефолтные значения параметров
	search_term = url
	start_val = 0
	target_forum_id = -1

	# Разбираем строку параметров, прилетевшую из нашей новой функции History()
	if '&start=' in url:
		# Разбиваем по первому маркеру смещения страницы
		parts = url.split('&start=')
		search_term = parts[0]

		# Если внутри второй части прилетел еще и ID раздела
		if '&forum_id=' in parts[1]:
			sub_parts = parts[1].split('&forum_id=')
			start_val = int(sub_parts[0])
			target_forum_id = sub_parts[1]
		else:
			start_val = int(parts[1])

	# Передаем вытащенные чистые параметры в вашу оригинальную функцию Search!
	Search(search_term, start_index=start_val, forum_id=target_forum_id)

elif mode == 'HList':
	log_debug(f"Mode is : {mode}")
	if '&start=' in url:
		f_id, start_val = url.split('&start=')
		List(forum_id=f_id, start_index=int(start_val))
	else:
		List(forum_id=url, start_index=0)

elif mode == 'Topic':
	# Вызывается при клике на фильм в листинге функции List.
	# В id передается ID темы (topic_id), в url — ID скачивания (download_id)
	Topic(id, url)

elif mode == 'Play':
	# =====================================================================
	# ФИНАЛЬНЫЙ КОНВЕЙЕР ВОСПРОИЗВЕДЕНИЯ ПО СХЕМЕ ЧИСТОГО MAGNET-СТРИМИНГА
	# =====================================================================
	log_debug(f"[NNM-PLAY] Запуск плеера. Серия: {title}, Индекс: {id}")

	# Переменная url уже содержит чистый Info-Hash раздачи в формате "magnet:?xt=urn:btih:..."
	# Мы убираем из кода любые склейки с download.php, полностью освобождая плеер от сайта трекера!
	magnet_link = url
	file_index = id  # Порядковый номер файла серии (0, 1, 2...)

	try: pu = f"plugin://{sys.argv[0]}?mode=Root"
	except: pu = ''

	try: actual_title = info.get('title', title)
	except: actual_title = title if title else 'Video File'

	# Заэкранируем параметры по стандарту плагина ТАМ
	quoted_url = quote(magnet_link)
	quoted_info = quote(repr({'title': actual_title}))
	quoted_pu = quote(pu)

	# Собираем пусковой URL для ТАМа по канону чистого Magnet-вещания.
	# proxy и cookie больше НЕ НУЖНЫ, TorrServer заберет раздачу из DHT сети напрямую!
	tam_purl = f"plugin://plugin.video.tam/?mode=play&ind={file_index}&url={quoted_url}&info={quoted_info}&purl={quoted_pu}"
	log_debug(f"[NNM-PLAY] Итоговый чистый Magnet-URL передан в ТАМ: {tam_purl}")

	# Передаем команду разрешения ссылки встроенному VideoPlayer Kodi строго по дескриптору handle
	listitem = xbmcgui.ListItem(actual_title)
	listitem.setPath(tam_purl)
	# Каноническое разрешение ссылки Kodi строго по handle
	xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, listitem)

elif mode == 'History':
	log_debug(f"Mode is : {mode}")
	History()

elif mode == 'OpenHistory':
	# Логируем факт перехода пользователя в меню истории
	log_debug("[NNM-ROUTER] Открытие выделенного подраздела истории поиска.")
	
	# Вызываем нашу новую функцию отрисовки экрана истории
	ShowHistory()

elif mode == 'RemoveSingleHistory':
	# В переменной url у нас прилетит заэкранированное слово, которое нужно стереть
	log_debug(f"[NNM-ROUTER] Запущена команда точечного удаления слова из XML: {url}")
	
	# Вызываем нашу новую функцию удаления элемента
	remove_from_history(url)

elif mode == 'ClearHistory':
	# Логируем факт вызова команды очистки
	log_debug("[NNM-ROUTER] Запущена команда полной очистки XML-истории поиска.")
	# Вызываем нашу новую точечную функцию сброса настройки в "[]"
	del_history()

elif mode == 'Open':
	# На лету читаем значение куки авторизации из файла
	log_debug(f"Mode is : {mode}")

	addon_path = addon.getAddonInfo('path')
	log_debug(f"Mode is : {mode} addon_path: {addon_path}")

	cookie_file_path = os.path.join(addon_path, 'resources', 'cookies.txt')
	log_debug(f"Mode is : {mode} cookie_file_path: {cookie_file_path}")

	user_auth_cookie = ""

	if os.path.exists(cookie_file_path):
		try:
			with open(cookie_file_path, 'r', encoding='utf-8') as f:
				for line in f:
					if 'phpbb2mysql_4_t' in line:
						user_auth_cookie = line.strip()
		except: pass

	# url содержит ID файла скачивания торрента
	download_url = f"http://{siteUrl}/forum/download.php?id={url}"
	log_debug(f"Mode is : {mode} download_url: {download_url}")

	try: pu = 'plugin://plugin.video.nnmclub/?mode=HSearch&url=' + quote(title)
	except: pu = ''

	log_debug(f"Mode is : {mode} pu: {pu}")

	# Склеиваем вызов ТАМ со всеми куками обхода Cloudflare и авторизации зеркала
	tam_purl = f"plugin://plugin.video.tam/?mode=open&url={quote(download_url)}&info={quote(repr(info))}&purl={quote(pu)}&cookie={quote(user_auth_cookie)}"

	log_debug(f"Mode is : {mode} tam_purl: {tam_purl}")

	# ПРИНУДИТЕЛЬНЫЙ СТАРТ ПЛЕЕРА:
	# Даем Kodi жесткую команду немедленно запустить воспроизведение этой ссылки через ТАМ!
	# Это на 100% уберет ошибку "skipping unplayable item"
	import xbmc
	xbmc.executebuiltin(f'PlayMedia({tam_purl})')

# Обязательно убедитесь, что в самом конце файла НЕТ никаких лишних строк вроде c.close()
#c.close()

