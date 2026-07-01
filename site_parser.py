"""
Парсер сайта приёмной комиссии МТУСИ
Источник: abitur.mtuci.ru
"""

import requests
from bs4 import BeautifulSoup
import logging
import re
from datetime import datetime
import json
import os
import time

logger = logging.getLogger(__name__)

# Актуальные URL с сайта абитуриента
URLS = {
    "main": "https://abitur.mtuci.ru",
    "bachelor": "https://abitur.mtuci.ru/bachelor/",
    "magister": "https://abitur.mtuci.ru/magister/",
    "postgrad": "https://abitur.mtuci.ru/postgrad/",
    "guidelines": "https://abitur.mtuci.ru/guidelines/",
    "news": "https://abitur.mtuci.ru/news/"
}

class MTUCIParser:
    def __init__(self):
        self.cache_file = "data/site_cache.json"
        self.cache = self.load_cache()
        self.last_update = None
        
    def load_cache(self):
        """Загружает кэш из файла"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки кэша: {e}")
        return {
            "news": [],
            "programs": [],
            "documents": [],
            "last_update": None
        }
    
    def save_cache(self):
        """Сохраняет кэш в файл"""
        try:
            os.makedirs("data", exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения кэша: {e}")
    
    def fetch_page(self, url):
        """Загружает страницу с повторными попытками"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        for attempt in range(3):
            try:
                logger.info(f"Загрузка {url} (попытка {attempt+1})")
                response = requests.get(url, headers=headers, timeout=10)
                response.encoding = 'utf-8'
                if response.status_code == 200:
                    return response.text
                else:
                    logger.warning(f"Попытка {attempt+1}: статус {response.status_code}")
            except Exception as e:
                logger.warning(f"Попытка {attempt+1}: {e}")
            time.sleep(1)
        return None
    
    def parse_programs(self):
        """Парсит список программ обучения (бакалавриат, магистратура, аспирантура)"""
        try:
            all_programs = []
            
            # Парсим бакалавриат
            bachelor_html = self.fetch_page(URLS["bachelor"])
            if bachelor_html:
                bachelor_programs = self.parse_bachelor_page(bachelor_html)
                all_programs.extend(bachelor_programs)
                logger.info(f"Найдено {len(bachelor_programs)} программ бакалавриата")
            
            # Парсим магистратуру
            magister_html = self.fetch_page(URLS["magister"])
            if magister_html:
                magister_programs = self.parse_magister_page(magister_html)
                all_programs.extend(magister_programs)
                logger.info(f"Найдено {len(magister_programs)} программ магистратуры")
            
            # Парсим аспирантуру
            postgrad_html = self.fetch_page(URLS["postgrad"])
            if postgrad_html:
                postgrad_programs = self.parse_postgrad_page(postgrad_html)
                all_programs.extend(postgrad_programs)
                logger.info(f"Найдено {len(postgrad_programs)} программ аспирантуры")
            
            if all_programs:
                self.cache["programs"] = all_programs[:30]  # Сохраняем до 30 программ
                logger.info(f"✅ Всего найдено {len(all_programs)} программ")
                return all_programs
            
            return self.get_fallback_programs()
            
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга программ: {e}")
            return self.get_fallback_programs()
    
    def parse_bachelor_page(self, html):
        """Парсит страницу бакалавриата"""
        programs = []
        soup = BeautifulSoup(html, 'lxml')
        
        # Ищем таблицу с программами
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows[1:]:  # Пропускаем заголовок
                cols = row.find_all('td')
                if len(cols) >= 2:
                    program_text = cols[0].get_text(strip=True)
                    # Извлекаем код и название
                    code_match = re.search(r'(\d{2}\.\d{2}\.\d{2})', program_text)
                    code = code_match.group(1) if code_match else ""
                    name = re.sub(r'\d{2}\.\d{2}\.\d{2}\s*[-–]\s*', '', program_text)
                    
                    # Проходной балл
                    passing_score = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                    
                    programs.append({
                        "code": code,
                        "name": name,
                        "level": "Бакалавриат",
                        "passing_score": passing_score,
                        "description": f"Проходной балл 2025: {passing_score}"
                    })
        
        # Если таблиц нет, ищем в списках
        if not programs:
            program_blocks = soup.find_all(['div', 'section'], class_=re.compile(r'program|direction'))
            for block in program_blocks:
                title = block.find(['h3', 'h4'])
                if title:
                    text = title.get_text(strip=True)
                    code_match = re.search(r'(\d{2}\.\d{2}\.\d{2})', text)
                    code = code_match.group(1) if code_match else ""
                    name = re.sub(r'\d{2}\.\d{2}\.\d{2}\s*', '', text)
                    
                    programs.append({
                        "code": code,
                        "name": name,
                        "level": "Бакалавриат",
                        "passing_score": "",
                        "description": ""
                    })
        
        return programs
    
    def parse_magister_page(self, html):
        """Парсит страницу магистратуры"""
        programs = []
        soup = BeautifulSoup(html, 'lxml')
        
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) >= 2:
                    program_text = cols[0].get_text(strip=True)
                    code_match = re.search(r'(\d{2}\.\d{2}\.\d{2})', program_text)
                    code = code_match.group(1) if code_match else ""
                    name = re.sub(r'\d{2}\.\d{2}\.\d{2}\s*[-–]\s*', '', program_text)
                    
                    passing_score = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                    
                    programs.append({
                        "code": code,
                        "name": name,
                        "level": "Магистратура",
                        "passing_score": passing_score,
                        "description": f"Проходной балл: {passing_score}"
                    })
        
        return programs
    
    def parse_postgrad_page(self, html):
        """Парсит страницу аспирантуры"""
        programs = []
        soup = BeautifulSoup(html, 'lxml')
        
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) >= 3:
                    group = cols[0].get_text(strip=True)
                    spec_code = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                    spec_name = cols[2].get_text(strip=True) if len(cols) > 2 else ""
                    
                    if spec_name:
                        programs.append({
                            "code": spec_code,
                            "name": spec_name,
                            "level": "Аспирантура",
                            "group": group,
                            "description": f"Научная группа: {group}"
                        })
        
        return programs
    
    def parse_documents(self):
        """Парсит список необходимых документов"""
        try:
            html = self.fetch_page(URLS["guidelines"])
            if not html:
                return self.get_fallback_documents()
            
            soup = BeautifulSoup(html, 'lxml')
            documents = []
            
            # Ищем разделы с документами
            doc_sections = soup.find_all(['div', 'section'], class_=re.compile(r'doc|document|file'))
            
            for section in doc_sections:
                links = section.find_all('a')
                for link in links:
                    text = link.get_text(strip=True)
                    href = link.get('href', '')
                    if text and len(text) > 5 and any(key in text.lower() for key in ['правила', 'документ', 'список', 'форма', 'заявление', 'паспорт']):
                        documents.append(f"{text} - {URLS['main']}{href}" if href.startswith('/') else text)
            
            # Если не нашли, ищем все ссылки на PDF
            if not documents:
                pdf_links = soup.find_all('a', href=re.compile(r'\.pdf'))
                for link in pdf_links:
                    text = link.get_text(strip=True)
                    if text:
                        documents.append(text)
            
            if documents:
                self.cache["documents"] = documents[:10]
                return documents
            
            return self.get_fallback_documents()
            
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга документов: {e}")
            return self.get_fallback_documents()
    
    def parse_news(self):
        """Парсит новости приёмной комиссии"""
        try:
            html = self.fetch_page(URLS["news"])
            if not html:
                return []
            
            soup = BeautifulSoup(html, 'lxml')
            news = []
            
            # Убираем навигационные элементы
            for nav in soup.find_all(['nav', 'header', 'footer']):
                nav.decompose()
            
            # Ищем все ссылки, которые могут быть новостями
            all_links = soup.find_all('a', href=True)
            
            for link in all_links:
                href = link.get('href', '')
                text = link.get_text(strip=True)
                
                # Проверяем, что это не навигация
                if not text or len(text) < 5:
                    continue
                    
                # Игнорируем служебные ссылки
                if any(skip in href for skip in ['#', 'javascript', 'tel:', 'mailto:']):
                    continue
                    
                # Игнорируем ссылки с маленьким текстом (скорее всего кнопки)
                if len(text) < 8:
                    continue
                
                # Проверяем, что ссылка ведёт на страницу новости
                if '/news/' in href or 'novost' in href.lower() or 'announce' in href.lower():
                    full_url = href if href.startswith('http') else URLS["main"] + href
                    
                    # Ищем дату рядом со ссылкой
                    date = ""
                    parent = link.find_parent(['div', 'li', 'article'])
                    if parent:
                        # Ищем элемент с датой
                        date_elem = parent.find(['time', 'span'], class_=re.compile(r'date|time|day'))
                        if date_elem:
                            date = date_elem.get_text(strip=True)
                        else:
                            # Ищем текст с датой формата ДД.ММ.ГГ
                            date_match = re.search(r'(\d{2}\.\d{2}\.\d{2})', parent.get_text())
                            if date_match:
                                date = date_match.group(1)
                    
                    news.append({
                        "title": text[:100],
                        "date": date,
                        "text": "",
                        "link": full_url
                    })
            
            # Если не нашли через ссылки, ищем все элементы с текстом
            if not news:
                # Ищем все блоки, которые могут содержать новости
                possible_news = soup.find_all(['div', 'article', 'li'], class_=re.compile(r'news|item|post|announce|event'))
                
                for item in possible_news[:15]:
                    # Заголовок
                    title_elem = item.find(['h2', 'h3', 'h4', 'a'])
                    title = title_elem.get_text(strip=True) if title_elem else ""
                    
                    if not title or len(title) < 5:
                        continue
                    
                    # Дата
                    date_elem = item.find(['time', 'span'], class_=re.compile(r'date|time|day'))
                    date = date_elem.get_text(strip=True) if date_elem else ""
                    
                    # Ссылка
                    link_elem = item.find('a')
                    link = link_elem.get('href') if link_elem else ""
                    if link and not link.startswith('http'):
                        link = URLS["main"] + link
                    
                    # Текст (если есть)
                    text_elem = item.find('p')
                    text = text_elem.get_text(strip=True) if text_elem else ""
                    
                    news.append({
                        "title": title[:100],
                        "date": date[:15] if date else "",
                        "text": text[:150] + "..." if text and len(text) > 150 else text,
                        "link": link
                    })
            
            # Фильтруем и убираем дубликаты
            unique_news = []
            seen_titles = set()
            
            for n in news:
                # Убираем слишком короткие заголовки
                if len(n['title']) < 8:
                    continue
                # Убираем навигационные элементы
                if any(skip in n['title'].lower() for skip in ['главная', 'все разделы', 'все новости', 'меню', 'навигация']):
                    continue
                if n['title'] not in seen_titles:
                    seen_titles.add(n['title'])
                    unique_news.append(n)
            
            # Сортируем по дате (если есть)
            unique_news.sort(key=lambda x: x['date'], reverse=True)
            
            self.cache["news"] = unique_news[:10]
            logger.info(f"✅ Найдено {len(unique_news)} новостей")
            
            return unique_news
            
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга новостей: {e}")
            return []
    
    def get_fallback_programs(self):
        """Возвращает запасной список программ на случай неудачи парсинга"""
        fallback = [
            {"code": "09.03.01", "name": "Информатика и вычислительная техника", "level": "Бакалавриат", "description": "Профиль: ТОП-ИИ: Инженерия систем искусственного интеллекта"},
            {"code": "09.03.02", "name": "Информационные системы и технологии", "level": "Бакалавриат", "description": "Профиль: Инженерия DevSecOps"},
            {"code": "09.03.03", "name": "Прикладная информатика", "level": "Бакалавриат", "description": "Профиль: Прикладные информационные системы"},
            {"code": "09.03.04", "name": "Программная инженерия", "level": "Бакалавриат", "description": "Профиль: Разработка и сопровождение ПО"},
            {"code": "10.03.01", "name": "Информационная безопасность", "level": "Бакалавриат", "description": "Профили: Безопасность компьютерных и автоматизированных систем"},
            {"code": "11.03.02", "name": "Инфокоммуникационные технологии и системы связи", "level": "Бакалавриат", "description": "Профили: Беспроводная связь, Сетевая инженерия"},
            {"code": "15.03.04", "name": "Автоматизация технологических процессов", "level": "Бакалавриат", "description": "Профиль: Промышленный интернет вещей и робототехника"},
            {"code": "38.03.05", "name": "Бизнес-информатика", "level": "Бакалавриат", "description": "Профиль: Дата-аналитика и системы больших данных"},
            {"code": "09.04.01", "name": "Информатика и вычислительная техника", "level": "Магистратура", "description": "Профили: Программная защита информации, Технологии ИИ"},
            {"code": "11.04.02", "name": "Инфокоммуникационные технологии", "level": "Магистратура", "description": "Профили: Квантовые коммуникации, Мультисервисные технологии"}
        ]
        self.cache["programs"] = fallback
        logger.info(f"📋 Использую запасной список программ ({len(fallback)} шт.)")
        return fallback
    
    def get_fallback_documents(self):
        """Возвращает запасной список документов"""
        fallback = [
            "Паспорт (копия разворота с фото и пропиской)",
            "СНИЛС (копия)",
            "Аттестат или диплом (оригинал или копия)",
            "6 фотографий 3x4 (матовые)",
            "Заявление о приеме (заполняется при подаче)",
            "Согласие на обработку персональных данных",
            "Документы, подтверждающие индивидуальные достижения (при наличии)",
            "Документы, подтверждающие льготы (при наличии)"
        ]
        self.cache["documents"] = fallback
        return fallback
    
    def update_all(self):
        """Обновляет все данные с сайта"""
        logger.info("🔄 Начинаю обновление данных с сайта МТУСИ...")
        
        programs = self.parse_programs()
        documents = self.parse_documents()
        news = self.parse_news()
        
        self.cache["programs"] = programs
        self.cache["documents"] = documents
        self.cache["news"] = news
        self.cache["last_update"] = datetime.now().isoformat()
        
        self.save_cache()
        
        logger.info(f"✅ Данные обновлены. Программ: {len(programs)}, Документов: {len(documents)}, Новостей: {len(news)}")
        return self.cache
    
    def get_context_for_query(self, query):
        """Возвращает релевантный контекст из сайта для запроса"""
        query_lower = query.lower()
        context_parts = []
        
        # Поиск по программам
        if any(word in query_lower for word in ['программ', 'направл', 'специальност', 'факультет', 'учиться', 'обучение', 'бакалавр', 'магистр']):
            progs = self.cache.get("programs", [])[:7]
            if progs:
                context_parts.append("Актуальные программы обучения в МТУСИ:")
                for p in progs:
                    code = f"({p['code']}) " if p.get('code') else ""
                    level = f"[{p.get('level', '')}] " if p.get('level') else ""
                    context_parts.append(f"• {code}{level}{p['name']}")
                    if p.get('description'):
                        context_parts.append(f"  {p['description']}")
        
        # Поиск по документам
        if any(word in query_lower for word in ['документ', 'паспорт', 'аттестат', 'справк', 'нужно', 'принести', 'собрать']):
            docs = self.cache.get("documents", [])
            if docs:
                context_parts.append("\nНеобходимые документы для поступления:")
                for d in docs[:7]:
                    context_parts.append(f"• {d}")
        
        return "\n".join(context_parts)

# Создаём глобальный экземпляр парсера
parser = MTUCIParser()