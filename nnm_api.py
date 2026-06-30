# -*- coding: utf-8 -*-
import requests
import re
import xbmc

class NNMClubAPI:

    def __init__(self, base_url="http://nnmclub.to", cf_token="", sid_token="", proxy=None):

        self.base_url = base_url

        self.session = requests.Session()

        # 1. ПЕРЕНОСИМ ЗАГОЛОВКИ СЮДА (внутрь инициализации)
        xbmc.log("[NNM-API] Установка session.headers внутри класса...", xbmc.LOGINFO)

        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
#            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
#            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
#            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
#            "Connection": "keep-alive"
        })

        # 2. ПРОВЕРКА: Выводим в лог то, что РЕАЛЬНО записалось в сессию
        # dict() покажет текущее живое состояние заголовков сессии
        headers_state = dict(self.session.headers)
        xbmc.log(f"[NNM-API] ЖИВЫЕ ЗАГОЛОВКИ СЕССИИ ТЕПЕРЬ: {headers_state}", xbmc.LOGINFO)

        if proxy and isinstance(proxy, dict):
            self.session.proxies = proxy

        # =====================================================================
        # БЛОК ДИНАМИЧЕСКОЙ ИНЖЕКЦИИ КУК АВТОРИЗАЦИИ ТРЕКЕРА
        # =====================================================================
        if sid_token:
            # Создаем переменную под динамический ID пользователя. По умолчанию пустая строка
            dynamic_uid = ""

            # БРОНЕБОЙНАЯ РЕГУЛЯРКА ДЛЯ АВТОМАТИЧЕСКОГО ИЗВЛЕЧЕНИЯ UID
            # Ищет вхождение подстроки 'i%3A' (что означает 'i:') и забирает все идущие
            # подряд цифровые символы (\d+) до знака процентов (% или конца блока)
            # В строке "a%3A1%3A%7Bi%3A953602%3B..." она идеально заберет строго '953602'
            uid_match = re.search(r'i%3A(\d+)', sid_token, re.IGNORECASE)

            if uid_match:
                # Если регулярка успешно нашла совпадение, присваиваем значение переменной
                dynamic_uid = uid_match.group(1)
                xbmc.log(f"[NNM-API] Из токена сессии успешно извлечен уникальный UID пользователя: {dynamic_uid}", level=xbmc.LOGINFO)
            else:
                # Резервный фоллбэк: если пользователь ввел неэкранированную строку "a:1:{i:953602;..."
                uid_match_raw = re.search(r'i:(\d+)', sid_token, re.IGNORECASE)
                if uid_match_raw:
                    dynamic_uid = uid_match_raw.group(1)
                    xbmc.log(f"[NNM-API] Из сырого токена успешно извлечен UID: {dynamic_uid}", level=xbmc.LOGINFO)

            # Если UID успешно определен операционной системой плагина, прошиваем куки сессии
            if dynamic_uid:
                # Устанавливаем динамический числовой ID пользователя, вытащенный из настроек
                self.session.cookies.set('phpbb2mysql_4_uid', dynamic_uid, domain='.nnmclub.to')

                # Записываем заэкранированный токен сессии трекера напрямую в куку phpbb2mysql_4_t
                self.session.cookies.set('phpbb2mysql_4_t', sid_token, domain='.nnmclub.to')

                # Дублируем токен в куку автологина phpbb2mysql_4_data для максимальной стабильности
                self.session.cookies.set('phpbb2mysql_4_data', sid_token, domain='.nnmclub.to')

                xbmc.log("[NNM-API] Полный динамический комплект кук авторизации успешно привязан к сессии!", level=xbmc.LOGINFO)
            else:
                # Если регулярка не смогла разобрать строку, выводим критическое предупреждение в лог Kodi
                xbmc.log("[NNM-API] КРИТИЧЕСКАЯ ОШИБКА: Не удалось распарсить UID из строки настроек! Проверьте формат.", level=xbmc.LOGERROR)

        xbmc.log("[NNM-API-TRACE] Конструктор __init__ успешно дошел до финальной строки!", xbmc.LOGINFO)

    def safe_get(self, url, headers=None, timeout=10, use_proxy=True):
        """
        Универсальный защищенный метод класса для выполнения GET-запросов.
        Автоматически ловит проблемы с SSL-сертификатами, логирует предупреждения
        и применяет резервный обход verify=False, не давая аддону упасть.
        """
        # Сборка заголовков: если кастомные не переданы, ставим стандартный User-Agent
        if headers is None:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        # Определяем, какой объект будет делать запрос:
        # Если use_proxy=True (для сайта трекера), шлем через self.session (где привязан АнтиЗапрет)
        # Если use_proxy=False (для TMDB), шлем через чистый requests, чтобы обойти ограничения ноды 403
        client = self.session if use_proxy else requests

        try:
            # ШАГ 1: Попытка выполнить легитимный и безопасный запрос с проверкой SSL
            return client.get(url, headers=headers, timeout=timeout)
        except requests.exceptions.SSLError as ssl_err:
            # ШАГ 2: Перехват сбоя Let's Encrypt. Явно сигнализируем в системный лог Kodi!
            xbmc.log(f"[NNM-API-WARN] Сбой SSL-сертификата на URL: {url}. Причина: {str(ssl_err)}. Запускаю обход verify=False.", level=xbmc.LOGWARNING)
            # Повторяем этот же запрос, принудительно отключая локальную валидацию сертификатов
            return client.get(url, headers=headers, timeout=timeout, verify=False)
        except Exception as e:
            # Логируем критические обрывы связи или таймауты
            xbmc.log(f"[NNM-API-ERROR] Критическая ошибка сокета на URL {url}: {str(e)}", level=xbmc.LOGERROR)
            return None

#    def search(self, query, forum_id=None, start=0):

#        encoded_query = urllib.parse.quote(query)

#        # =====================================================================
#        # ТОЧЕЧНЫЙ БЛОК ОПРЕДЕЛЕНИЯ КАТЕГОРИИ ПОИСКА
#        # =====================================================================
#        # По умолчанию задаем f=-1 (Флаг nnm-club для глобального поиска по всему трекеру)
#        f_param = "f=-1"

#        # Если из default.py прилетел конкретный ID раздела (поиск внутри категории)
#        # Мы проверяем, что forum_id передан, не равен None и не равен дефолтному -1
#        if forum_id and forum_id != -1:
#            # При поиске по разделу nnm-club требует синтаксис f[]=ID_РАЗДЕЛА.
#            # Формируем точную одиночную строку (например, "f[]=1344")
#            f_param = f"f[]={forum_id}"

#        # Собираем итоговый URL поискового запроса к зеркалу трекера.
#        # o=10&s=2 — это ваши штатные параметры сортировки выдачи сайта
#        url = f"{self.base_url}/forum/tracker.php?{f_param}&nm={encoded_query}&o=10&s=2&start={start}"

#        try:
#            xbmc.log(f"[NNM-API] Отправка полностью авторизованного запроса: {url}", level=xbmc.LOGINFO)
#            res = self.safe_get(url, timeout=15, use_proxy=True)
#            if res is None or res.status_code != 200:
#                xbmc.log(f"[NNM-API] Ошибка сети при поиске", level=xbmc.LOGERROR)
#                return []
#            html_text = res.content.decode('windows-1251', errors='ignore')

#            return self._parse_tracker_page(html_text)
#.
#        except Exception as e:
#            xbmc.log(f"[NNM-API] Ошибка сети при поиске: {str(e)}", level=xbmc.LOGERROR)
#            return []

#    def _parse_tracker_page(self, html):
#        """
#        Внутренний метод класса: разбирает код страницы результатов поиска (tracker.php)
#        и возвращает чистый список словарей раздач с сидами и размерами.
#        """
#        results = []

#        # =====================================================================
#        # ЭТАЛОННАЯ РЕГУЛЯРКА СТРОКИ СЛОВАРЯ ПОИСКА (ПОД ВАШ HTML)
#        # =====================================================================
#        # Ищет начало строки таблицы prow1 или prow2
#        # Группа 1: (\d+) -> ID темы (1780816)
#        # Группа 2: (.*?) -> Название раздачи (Асока / Ahsoka...)
#        # Группа 3: (\d+) -> ID скачивания (1358702)
#        # Группа 4: (.*?) -> Читаемый размер файла (24.4 GB)
#        # Группа 5: (\d+) -> Количество сидов (8)
#        # Флаг re.DOTALL позволяет точке "." съедать переносы строк внутри ячеек <td>
#        row_pattern = re.compile(
#            r'<tr[^>]*class="prow\d+".*?'
#            r'href="viewtopic\.php\?t=(\d+)"[^>]*>(.*?)</a>.*?'
#            r'href="download\.php\?id=(\d+)".*?'
#            r'</u>\s*([^<]+).*?'
#            r'class="seedmed"[^>]*><b>(\d+)</b>',
#            re.DOTALL | re.IGNORECASE
#        )

#        # Находим все стопроцентные совпадения строк на странице
#        matches = row_pattern.findall(html)
#        xbmc.log(f"[NNM-API] Сквозной парсинг строк prow. Успешно собрано чистых тем: {len(matches)}", level=xbmc.LOGINFO)

#        for topic_id, title, dl_id, size_text, seeds in matches:
#            try:
#                # Очищаем название от внутренних HTML-тегов (<b>, <span> и т.д.)
#                clean_title = re.sub(r'<[^>]+>', '', title).strip()

#                # Отсекаем служебные навигационные ссылки форума
#                if not clean_title or any(x in clean_title for x in ["Темы", "Сообщения", "Автор", "Последнее", "След.", "Пред."]):
#                    continue

#                # Вычищаем пробелы и лишние символы из строки размера
#                clean_size = size_text.replace('&nbsp;', ' ').strip()

#                # Наполняем словарь результатов. Все данные жестко привязаны к одной строке,
#                # сдвиги индексов или перепутывание фильмов теперь исключены физически!
#                results.append({
#                    'title': clean_title,
#                    'topic_id': topic_id,
#                    'dnld_id': dl_id,          # Числовой ID файла скачивания для Topic
#                    'size': clean_size,       # Строка размера (например, "24.4 GB")
#                    'seeds': seeds.strip()     # Количество сидов
#                })
#            except:
#                continue

#        return results

#    def get_topic_magnet(self, topic_id):
#        """
#        Шаги 1-3: Скачивает страницу топика через safe_get и извлекает строку Info-Hash.
#        :param topic_id: ID темы на трекере (int или str)
#        :return: 40-значная строка хэша (в нижнем регистре) или None при ошибке
#        """
#        # Формируем URL строго по вашему канону
#        topic_page_url = f"{self.base_url}/forum/viewtopic.php?t={str(topic_id)}"

#        # Шлем защищенный запрос точно так же, как в вашей исходной функции
#        res_topic = self.safe_get(topic_page_url, timeout=15, use_proxy=True)

#        # Проверяем объект ответа на валидность
#        if res_topic is None or res_topic.status_code != 200:
#            return None

#        # Извлекаем HTML-текст страницы
#        html = res_topic.text

#        # Шаг 2: Вырезаем таблицу раздачи с magnet-ссылкой
#        table_match = re.search(r'class="btTbl".*?</table>', html, re.DOTALL | re.IGNORECASE)
#        if not table_match:
#            return None

#        bt_table_html = table_match.group(0)

#        # Шаг 3: Вытаскиваем из таблицы 40-значный шестнадцатеричный Info-Hash
#        hash_match = re.search(r'magnet:\?xt=urn:btih:([a-fA-F0-9]{40})', bt_table_html, re.IGNORECASE)
#        if hash_match:
#            return hash_match.group(1).lower() # Приводим к нижнему регистру для TAM

#        return None

#    def get_ajax_file_list(self, download_id):
#        """
#        Шаг 4: Выполняет AJAX-запрос к списку файлов торрента через safe_get.
#        :param download_id: ID вложения (аттачмента) из раздачи
#        :return: чистый список названий файлов ['video.mkv'] или пустой список []
#        """
#        # Формируем URL для AJAX-скрипта через динамический base_url
#        ajax_url = f"{self.base_url}/forum/filelst.php?attach_id={str(download_id)}"

#        # Шлем запрос через АнтиЗапрет с теми же параметрами безопасности
#        res_ajax = self.safe_get(ajax_url, timeout=15, use_proxy=True)

#        if res_ajax is None or res_ajax.status_code != 200:
#            return []

#        html = res_ajax.text

#        # Строгая регулярка по вашему паттерну для поиска медиа-расширений
#        file_pattern = re.compile(
#            r'class="genmed"\s+align="left">\s*([^<]+?\.(?:mkv|mp4|avi|ts|m2ts|mp3|flac|iso))',
#            re.IGNORECASE
#        )

#        # Собираем совпадения и очищаем их от концевых пробелов
#        files = file_pattern.findall(html)
#        return [f.strip() for f in files]

#    def debug(s):
#        fl = open(os.path.join( ru(LstDir),"debug.txt"), "w")
#        fl.write(s)
#        fl.close()

    def __del__(self):
        """Этот метод вызывается автоматически, когда Python удаляет объект из памяти"""
        xbmc.log("[NNM-API-TRACE] ВНИМАНИЕ: Экземпляр класса NNMClubAPI УНИЧТОЖЕН ИЗ ПАМЯТИ!", xbmc.LOGINFO)
