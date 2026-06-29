# -*- coding: utf-8 -*-
import requests
import re
import urllib.parse
import xbmc

class NNMClubAPI:
    def __init__(self, base_url="http://nnmclub.to", cf_token="", sid_token="", proxy=None):
        self.base_url = base_url
        self.session = requests.Session()

        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

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

    def get_tmdb_poster(self, title_text):
        """
        Метод класса: отправляет чистый запрос к API TMDB через 
        альтернативный публичный шлюз без участия Google Translate.
        """
        # Очищаем название от косых черт и скобок (чистое русское имя)
        clean_name = re.sub(r'[/([].*', '', title_text).strip()
        
        # Выуживаем 4 цифры года выпуска фильма
        year_match = re.search(r'(19\d{2}|20\d{2})', title_text)
        year = year_match.group(1) if year_match else ""
        
        if not clean_name:
            return ""

        # ВАШ ИСТИННЫЙ КЛЮЧ TMDB ИЗ СКРЕПЕРА KODI
        my_tmdb_key = "f090bb54758cabf231fb605d3e3e0468"

        # Создаем словарь параметров.
        query_params = {
            'api_key': my_tmdb_key,
            'query': clean_name,
            'language': 'ru-RU'
        }
        
        if year:
            query_params['year'] = year

        string_params = urllib.parse.urlencode(query_params)

        # КАНLabelОНИЧЕСКИЙ URL API TMDB (СТРОГО ПО СТАНДАРТУ, С СЛЭШАМИ)
        tmdb_url = f"https://themoviedb.org?{string_params}"
        tmdb_image_domain = "https://tmdb.org"

        xbmc.log(f"[NNM-API] tmdb_url: {tmdb_url}", level=xbmc.LOGINFO)

        # ИСПОЛЬЗУЕМ АЛЬТЕРНАТИВНОЕ НЕБЛОКLabelРУЕМОЕ ЗЕРКАЛО API ДЛЯ РОССLabelLabel (Фоллбэк):
        # Если оригинальный домен заблокирован, многие аддоны шлют запросы на официальное 
        # рабочее зеркало-прокси TMDB, которое отдает оригинальный JSON без ВПН:
        tmdb_url_mirror = f"https://tmdb.org?{string_params}"

        xbmc.log(f"[NNM-API] tmdb_url_mirror: {tmdb_url_mirror}", level=xbmc.LOGINFO)

        try:
            # Чтобы не зависеть от ТСПУ и ограничений АнтиЗапрета, мы передаем 
            # в запрос чистый, проверенный публичный прокси-сервер (например, CORS-шлюз).
            # Или делаем запрос напрямую к альтернативному незаблокированному зеркалу TMDB.
            # Давайте попробуем сделать запрос к api.themoviedb.org через независимый requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            }
            
            # Попробуем отправить запрос напрямую к зеркалу api.tmdb.org (оно часто доступно без ВПН)
            res = requests.get(tmdb_url_mirror, headers=headers, timeout=5)
            
            # Если зеркало не ответило, пробуем оригинальный адрес
            if res.status_code != 200:
                res = requests.get(tmdb_url, headers=headers, timeout=5)
                
            data = res.json()
            
            # Разбираем ответ
            if data and data.get('results'):
                first_movie = data['results'][0] # Берем первый самый точный фильм из списка
                poster_path = first_movie.get('poster_path')
                
                if poster_path:
                    # Очищаем первый слэш и склеиваем строку картинки
                    clean_path = poster_path.lstrip('/')
                    final_image_url = f"{tmdb_image_domain}/{clean_path}"
                    return final_image_url
                    
        except Exception as e:
            xbmc.log(f"[NNM-TMDB] Ошибка получения JSON-ответа: {str(e)}", level=xbmc.LOGERROR)
            
        return ""

    def search(self, query, forum_id=None, start=0):

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
        url = f"{self.base_url}/forum/tracker.php?{f_param}&nm={encoded_query}&o=10&s=2&start={start}"

        try:
            xbmc.log(f"[NNM-API] Отправка полностью авторизованного запроса: {url}", level=xbmc.LOGINFO)
            res = self.safe_get(url, timeout=15, use_proxy=True)
            if res is None or res.status_code != 200:
                xbmc.log(f"[NNM-API] Ошибка сети при поиске", level=xbmc.LOGERROR)
                return []
            html_text = res.content.decode('windows-1251', errors='ignore')
            return self._parse_tracker_page(html_text)
        except Exception as e:
            xbmc.log(f"[NNM-API] Ошибка сети при поиске: {str(e)}", level=xbmc.LOGERROR)
            return []

    def _parse_tracker_page(self, html):
        """
        Внутренний метод класса: разбирает код страницы результатов поиска (tracker.php)
        и возвращает чистый список словарей раздач с сидами и размерами.
        """
        results = []

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
        xbmc.log(f"[NNM-API] Сквозной парсинг строк prow. Успешно собрано чистых тем: {len(matches)}", level=xbmc.LOGINFO)

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
                    'magnet': dl_id,          # Числовой ID файла скачивания для Topic
                    'size': clean_size,       # Строка размера (например, "24.4 GB")
                    'seeds': seeds.strip()     # Количество сидов
                })
            except:
                continue

        return results



#    def _parse_tracker_page(self, html):

#        results = []

#        self.debug(html)

#        # ПООБЪЕКТНАЯ РЕГУЛЯРКА:
#        # Мы ищем открывающий тег строки таблицы трекера, забираем ID темы,
#        # очищаем её название, съедаем любой HTML-код до ячейки скачивания,
#        # и забираем строго принадлежащий этой строке download ID!
#        # Флаг re.DOTALL позволяет точке "." проходить сквозь переносы строк <td>
#        row_pattern = re.compile(
#            r'viewtopic\.php\?t=(\d+)"[^>]*>(.*?)</a>.*?download\.php\?id=(\d+)".*title="Размер блока.*?">&nbsp;([0-9.]*)&nbsp;([A-Z]*)&nbsp;</.*?',
#            re.DOTALL | re.IGNORECASE
#        )
##seed : .+seed.+\[.+\>(\d+).+&nbsp;
##leech : .+leech.+\[.+\>(\d+).+&nbsp;
##.+title=\"Размер блока.+\">&nbsp;([0-9.]+)&nbsp;([A-Z]+)&nbsp;\<\/.+

#        matches = row_pattern.findall(html)

#        xbmc.log(f"[NNM-API] Сквозной парсинг строк таблицы. Успешно свёрстано тем: {len(matches)}", level=xbmc.LOGINFO)

#        for topic_id, title, dl_id, size_d, size_c in matches:
#            # Очищаем название от внутренних тегов форматирования текста (<b>, <span> и т.д.)
#            clean_title = re.sub(r'<[^>]+>', '', title).strip()

#            # Отсекаем служебные навигационные ссылки phpBB, если они проскочили
#            if not clean_title or any(x in clean_title for x in ["Темы", "Сообщения", "Автор", "Последнее", "След.", "Пред.", "Вернуться", "Профиль"]):
#                continue

#            xbmc.log(f"[NNM-API] Жесткая связка: {clean_title} --> Файл ID: {dl_id}", level=xbmc.LOGINFO)

#            results.append({
#                'title': clean_title,
#                'topic_id': topic_id,
#                'magnet': dl_id  # Записываем строго сопоставленный ID файла скачивания
#            })

#        return results

    def debug(s):
        fl = open(os.path.join( ru(LstDir),"debug.txt"), "w")
        fl.write(s)
        fl.close()

