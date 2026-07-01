#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Веб-версия бота приёмной комиссии МТУСИ
Работает через браузер, не требует Telegram
"""

import os
import sys
import logging
import requests
import warnings
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import uuid
import time
import asyncio
import threading

# Подавление предупреждений
os.environ['CHROMA_TELEMETRY_ENABLED'] = 'false'
os.environ['ANONYMIZED_TELEMETRY'] = 'false'
os.environ['DO_NOT_TRACK'] = '1'
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message="Failed to send telemetry")
warnings.filterwarnings("ignore", message="Failed to initialize NumPy")

# Импортируем существующие модули
import faq_mtuci
from site_parser import parser
from rag_mtuci import rag
from handlers.quick_commands import get_quick_command_response, is_quick_command

os.environ['CHROMA_NO_PROGRESS_BAR'] = '1'

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger(__name__)

# Flask приложение
app = Flask(__name__)
app.secret_key = os.urandom(24)
CORS(app)

# Хранилище истории диалогов
chat_histories = {}

# Конфигурация Ollama из переменных окружения
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen3.5:9b")

# Флаг готовности RAG
rag_ready = False


# ===== ФУНКЦИЯ ДЛЯ ПРОВЕРКИ И ПРОГРЕВА МОДЕЛИ =====

def check_and_warmup_model() -> dict:
    """Проверяет доступность Ollama и прогревает модель"""
    result = {
        'ollama_available': False,
        'model_available': False,
        'model_responding': False,
        'message': '',
        'warmed': False
    }
    
    logger.info("=" * 50)
    logger.info("🔥 ПРОВЕРКА И ПРОГРЕВ МОДЕЛИ")
    logger.info("=" * 50)
    
    # 1. Проверяем доступность Ollama
    try:
        logger.info(f"🔍 Проверка Ollama: {OLLAMA_URL}")
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if response.status_code == 200:
            result['ollama_available'] = True
            models = response.json().get('models', [])
            model_names = [m['name'] for m in models]
            logger.info(f"✅ Ollama доступна. Доступные модели: {model_names}")
            
            if MODEL_NAME in model_names or any(MODEL_NAME in m for m in model_names):
                result['model_available'] = True
                logger.info(f"✅ Модель {MODEL_NAME} найдена")
            else:
                result['message'] = f"⚠️ Модель {MODEL_NAME} не найдена. Запусти: ollama pull {MODEL_NAME}"
                logger.warning(result['message'])
                return result
        else:
            result['message'] = f"⚠️ Ollama не отвечает: статус {response.status_code}"
            logger.warning(result['message'])
            return result
            
    except requests.exceptions.ConnectionError:
        result['message'] = f"❌ Не удалось подключиться к Ollama по адресу {OLLAMA_URL}\nУбедись, что Ollama запущен: ollama serve"
        logger.error(result['message'])
        return result
    except Exception as e:
        result['message'] = f"❌ Ошибка проверки Ollama: {e}"
        logger.error(result['message'])
        return result
    
    # 3. Прогреваем модель с минимальным запросом
    logger.info(f"🔥 Прогрев модели {MODEL_NAME}...")
    try:
        start_time = time.time()
        
        # Короткий промпт для быстрого прогрева
        payload = {
            "model": MODEL_NAME,
            "prompt": "Привет",
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 2,
                "num_ctx": 256  # Уменьшаем контекст для скорости
            }
        }
        
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=30
        )
        
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            result_data = response.json()
            if "response" in result_data:
                result['model_responding'] = True
                result['warmed'] = True
                result['message'] = f"✅ Модель прогрета! Время ответа: {elapsed:.2f} сек"
                logger.info(f"✅ Модель ответила: '{result_data['response'].strip()[:50]}'")
                logger.info(result['message'])
            else:
                result['message'] = "⚠️ Модель ответила, но в неожиданном формате"
                logger.warning(result['message'])
        else:
            result['message'] = f"⚠️ Ошибка прогрева: статус {response.status_code}"
            logger.warning(result['message'])
            
    except requests.exceptions.Timeout:
        result['message'] = "⏱️ Таймаут прогрева модели. Модель может загружаться в память..."
        logger.warning(result['message'])
    except Exception as e:
        result['message'] = f"❌ Ошибка прогрева: {e}"
        logger.error(result['message'])
    
    logger.info("=" * 50)
    return result

def initialize_rag():
    """Инициализирует RAG (индексирует только при изменении документов)"""
    global rag_ready
    
    try:
        count = rag.ensure_indexed()
        print(f"✅ RAG готов: {count} чанков")
        rag_ready = True
    except Exception as e:
        print(f"❌ Ошибка RAG: {e}")
        rag_ready = False

def run_ollama_with_timeout(prompt: str, context: str = "", timeout_seconds: int = 20) -> str:
    """Запускает запрос к Ollama с таймаутом"""
    try:
        generate_url = f"{OLLAMA_URL}/api/generate"
        
        full_prompt = f"""Ты — помощник приёмной комиссии МТУСИ.  
Ответь на вопрос пользователя, используя контекст.

Контекст:
{context[:1000]}

Вопрос: {prompt}

Ответ:"""
        
        payload = {
            "model": MODEL_NAME,
            "prompt": full_prompt,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 200,
                "num_ctx": 1024,
                "top_k": 40,
                "top_p": 0.9,
                "repeat_penalty": 1.0,
                "seed": 42,
                "mirostat": 0
            }
        }
        
        logger.info(f"🔄 Запрос к Ollama (модель: {MODEL_NAME})...")
        response = requests.post(generate_url, json=payload, timeout=timeout_seconds)
        
        if response.status_code == 200:
            result = response.json()
            answer = result.get("response", "").strip()
            if answer:
                return answer
            else:
                logger.warning("⚠️ Модель вернула пустой ответ")
                return None
        else:
            logger.error(f"❌ Ошибка Ollama: {response.status_code}")
            return None
            
    except requests.exceptions.Timeout:
        logger.warning(f"⏱️ Таймаут Ollama ({timeout_seconds} сек)")
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return None


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====

def get_simple_response(text: str) -> str:
    """Простые ответы без модели"""
    text_lower = text.lower().strip()

    greetings = ['привет', 'здравствуй', 'добрый день', 'здравствуйте', 'хай', 'hello']
    if any(greet in text_lower for greet in greetings):
        return "👋 Здравствуйте! Чем могу помочь? Я бот приёмной комиссии МТУСИ."

    thanks = ['спасибо', 'благодарю', 'спс']
    if any(thank in text_lower for thank in thanks):
        return "🙏 Пожалуйста! Обращайтесь ещё!"

    farewells = ['пока', 'до свидания', 'досвидания', 'всего доброго']
    if any(farewell in text_lower for farewell in farewells):
        return "👋 Всего доброго! Если будут вопросы - обращайтесь!"

    return None


def get_context_for_query(query: str) -> str:
    """Собирает контекст ТОЛЬКО из RAG документов"""
    if rag_ready:
        try:
            rag_context = rag.get_context_for_prompt(query, max_chars=1000)
            if rag_context:
                return rag_context
        except Exception as e:
            logger.error(f"Ошибка RAG: {e}")
    return ""


def get_fallback_answer(query: str) -> str:
    """Быстрые запасные ответы на основе ключевых слов"""
    text_lower = query.lower()
    
    if "стоимост" in text_lower or "цена" in text_lower:
        return "💰 Стоимость обучения: бакалавриат 280-300 тыс. руб/год, магистратура 295-310 тыс. руб/год."
    
    if "документ" in text_lower or "что нужно" in text_lower:
        return "📋 Для поступления нужны: паспорт, аттестат/диплом, СНИЛС, фото 3x4, заявление."
    
    if "общежити" in text_lower or "dorm" in text_lower:
        return "🏠 Общежитие предоставляется иногородним студентам. Адреса: ул. Авиамоторная 8а, ул. Народного Ополчения 32."
    
    if "экзамен" in text_lower or "егэ" in text_lower:
        return "📝 Минимальные баллы ЕГЭ: русский - 40, математика - 39, информатика/физика - 44."
    
    if "военн" in text_lower:
        return "🎖️ В МТУСИ есть военный учебный центр. Подробнее: /military"
    
    if "поступить" in text_lower:
        return "🎓 Для поступления: выберите направление, сдайте ЕГЭ, подайте документы до 25 июля."
    
    if "магистратур" in text_lower:
        return "🎓 Для поступления в магистратуру: диплом бакалавра, междисциплинарный экзамен, подача документов до 25 июля."
    
    return None


def format_answer(text: str) -> str:
    """Форматирует ответ для HTML"""
    if not text:
        return "❓ Не удалось получить ответ."
    
    # Обработка жирного текста
    text = text.replace('**', '<strong>', 1)
    if '**' in text:
        parts = text.split('**')
        for i in range(1, len(parts), 2):
            parts[i] = f'<strong>{parts[i]}</strong>'
        text = ''.join(parts)
    
    # Замена переносов строк
    text = text.replace('\n', '<br>')
    
    return text


def process_query(user_id: str, user_message: str) -> dict:
    """Основная функция обработки запроса"""

    # 1. ПРИВЕТСТВИЯ, БЛАГОДАРНОСТИ, ПРОЩАНИЯ
    simple_response = get_simple_response(user_message)
    if simple_response:
        return {
            'answer': simple_response,
            'source': 'simple',
            'context': ''
        }

    # 2. БЫСТРЫЕ КОМАНДЫ
    if is_quick_command(user_message):
        quick_response = get_quick_command_response(user_message)
        if quick_response:
            return {
                'answer': quick_response,
                'source': 'quick_command',
                'context': ''
            }

    # 3. СНАЧАЛА RAG + МОДЕЛЬ (для конкретных вопросов)
    context = get_context_for_query(user_message)  # Только RAG, без парсера!
        # Перед вызовом модели
    logger.info(f"📄 Контекст для модели: {len(context)} символов")
    if not context:
        logger.warning("⚠️ КОНТЕКСТ ПУСТОЙ! RAG не нашёл документов.")
    ollama_answer = run_ollama_with_timeout(
        user_message,
        context,
        timeout_seconds=60
    )

    if ollama_answer:
        return {
            'answer': ollama_answer,
            'source': 'ollama',
            'context': context
        }

    # 4. ЕСЛИ МОДЕЛЬ НЕ ОТВЕТИЛА - ПОКАЗЫВАЕМ КОНТЕКСТ RAG
    if context:
        return {
            'answer': f"📚 **Найдено в документах:**\n\n{context[:1000]}",
            'source': 'rag_only',
            'context': context
        }

    # 5. ПОТОМ ПАРСИНГ САЙТА (только если RAG ничего не нашёл)
    site_context = parser.get_context_for_query(user_message)
    if site_context:
        return {
            'answer': f"🌐 **Информация с сайта МТУСИ:**\n\n{site_context}",
            'source': 'site_parser',
            'context': ''
        }

    # 6. FAQ
    faq_answer, found = faq_mtuci.search_faq(user_message)
    if found:
        return {
            'answer': faq_answer,
            'source': 'faq',
            'context': ''
        }

    # 7. НИЧЕГО НЕ НАЙДЕНО
    return {
        'answer': "❓ Информация не найдена. Пожалуйста, уточните вопрос.",
        'source': 'not_found',
        'context': ''
    }

# ===== ВЕБ-МАРШРУТЫ =====

@app.route('/')
def index():
    """Главная страница"""
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
        chat_histories[session['user_id']] = []
    
    return render_template('chat.html', 
                         user_id=session['user_id'],
                         welcome_message="🎓 Добро пожаловать в помощник приёмной комиссии МТУСИ!",
                         current_year=datetime.now().year)


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """API для отправки сообщений"""
    data = request.json
    user_id = data.get('user_id', session.get('user_id', 'anonymous'))
    message = data.get('message', '').strip()
    
    if not message:
        return jsonify({'error': 'Пустое сообщение'}), 400
    
    if user_id not in chat_histories:
        chat_histories[user_id] = []
    
    chat_histories[user_id].append({
        'role': 'user',
        'content': message,
        'time': datetime.now().strftime('%H:%M')
    })
    
    # Обрабатываем запрос
    start_time = time.time()
    result = process_query(user_id, message)
    elapsed = time.time() - start_time
    
    logger.info(f"⏱️ Обработка запроса заняла {elapsed:.2f} сек (источник: {result['source']})")
    
    formatted_answer = format_answer(result['answer'])
    
    if result['context']:
        context_html = f"<details><summary>📚 Показать источники</summary><div class='context'>{format_answer(result['context'][:800])}</div></details>"
        formatted_answer += "<br><br>" + context_html
    
    chat_histories[user_id].append({
        'role': 'assistant',
        'content': formatted_answer,
        'time': datetime.now().strftime('%H:%M'),
        'source': result['source']
    })
    
    if len(chat_histories[user_id]) > 50:
        chat_histories[user_id] = chat_histories[user_id][-50:]
    
    return jsonify({
        'answer': formatted_answer,
        'history': chat_histories[user_id][-10:],
        'user_id': user_id,
        'processing_time': elapsed
    })


@app.route('/api/history', methods=['GET'])
def get_history():
    """Получение истории"""
    user_id = request.args.get('user_id', session.get('user_id', 'anonymous'))
    
    if user_id in chat_histories:
        return jsonify({'history': chat_histories[user_id]})
    
    return jsonify({'history': []})


@app.route('/api/clear', methods=['POST'])
def clear_history():
    """Очистка истории"""
    user_id = request.json.get('user_id', session.get('user_id', 'anonymous'))
    
    if user_id in chat_histories:
        chat_histories[user_id] = []
    
    return jsonify({'success': True})


@app.route('/api/quick_commands', methods=['GET'])
def get_quick_commands():
    """Список быстрых команд"""
    commands = [
        {'cmd': '/price', 'desc': '💰 Стоимость обучения'},
        {'cmd': '/dorm', 'desc': '🏠 Общежитие'},
        {'cmd': '/exam', 'desc': '📝 Экзамены и баллы'},
        {'cmd': '/military', 'desc': '🎖️ Военная кафедра'},
        {'cmd': '/transfer', 'desc': '🔄 Перевод из других вузов'},
        {'cmd': '/benefits', 'desc': '🎯 Льготы'},
        {'cmd': '/programs', 'desc': '🎓 Программы обучения'},
        {'cmd': '/documents', 'desc': '📋 Документы'},
        {'cmd': '/contacts', 'desc': '📞 Контакты'},
        {'cmd': '/openday', 'desc': '🏫 День открытых дверей'},
        {'cmd': '/faq', 'desc': '❓ Частые вопросы'},
        {'cmd': '/reset', 'desc': '🔄 Очистить историю'}
    ]
    return jsonify(commands)


@app.route('/api/status', methods=['GET'])
def get_status():
    """Статус бота"""
    return jsonify({
        'rag_ready': rag_ready,
        'ollama_url': OLLAMA_URL,
        'model_name': MODEL_NAME
    })


# ===== ЗАПУСК =====
if __name__ == '__main__':
    logger.info("🚀 Запуск веб-версии бота МТУСИ...")
    logger.info(f"📁 Путь к документам RAG: {rag.docs_path}")
    logger.info(f"🔗 Ollama: {OLLAMA_URL}")
    logger.info(f"🤖 Модель: {MODEL_NAME}")
    
    # 1. Прогреваем модель
    warmup_result = check_and_warmup_model()
    
    if not warmup_result['ollama_available']:
        logger.error("❌ Ollama недоступен! Бот будет работать только с запасными ответами.")
    elif not warmup_result['model_available']:
        logger.error(f"❌ Модель {MODEL_NAME} не найдена! Запусти: ollama pull {MODEL_NAME}")
    elif warmup_result['model_responding']:
        logger.info("✅ Модель готова к работе!")
    
    # 2. Инициализируем RAG и индексируем документы (в фоновом потоке)
    logger.info("🔄 Запуск инициализации RAG (в фоне)...")
    rag_thread = threading.Thread(target=initialize_rag, daemon=True)
    rag_thread.start()
    
    # 3. Загружаем данные сайта (в фоне)
    logger.info("🔄 Загрузка данных сайта (в фоне)...")
    def load_site_data():
        try:
            parser.update_all()
            logger.info("✅ Данные сайта загружены")
        except Exception as e:
            logger.error(f"⚠️ Ошибка загрузки данных сайта: {e}")
    
    site_thread = threading.Thread(target=load_site_data, daemon=True)
    site_thread.start()
    
    # 4. Запускаем сервер
    logger.info("🌐 Запуск веб-сервера...")
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)