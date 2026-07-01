import asyncio
import nest_asyncio

# Применяем nest_asyncio сразу после импорта
nest_asyncio.apply()

import os
import logging
from datetime import datetime
from dotenv import load_dotenv
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.contrib.middlewares.logging import LoggingMiddleware
import faq_mtuci
from site_parser import parser
from rag_mtuci import rag

# Импортируем все команды из одного файла
from handlers.quick_commands import register_quick_commands

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://10.19.33.21:33434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen3.5:9b")

if not BOT_TOKEN:
    raise ValueError("Нет BOT_TOKEN в файле .env!")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Отключаем телеметрию ChromaDB, чтобы избежать ошибок
os.environ["ANONYMIZED_TELEMETRY"] = "False"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

context_memory = {}

# Регистрируем все команды из отдельного файла
register_quick_commands(dp)


# ===== БАЗОВЫЕ КОМАНДЫ =====
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    """Приветствие"""
    welcome_text = """
🎓 **Добро пожаловать в бот приёмной комиссии МТУСИ!**

Я помогу вам с вопросами о поступлении. Вся информация актуальна на 2026 год.

⚡ **Быстрые команды:**
/price - стоимость обучения
/dorm - общежитие
/exam - экзамены и баллы
/military - военная кафедра
/transfer - перевод из других вузов
/benefits - льготы

📋 **Другие команды:**
/programs - программы обучения
/documents - список документов
/contacts - контакты
/faq - частые вопросы
/news - новости
/help - все команды

Просто напишите ваш вопрос!
    """
    await message.reply(welcome_text, parse_mode="Markdown")


@dp.message_handler(commands=["help"])
async def cmd_help(message: types.Message):
    """Справка"""
    help_text = """
🆘 **Все команды бота МТУСИ**

💰 **Стоимость и льготы:**
/price - стоимость обучения
/benefits - льготы при поступлении

🏠 **Общежитие:**
/dorm - информация об общежитии

📝 **Экзамены:**
/exam - вступительные испытания

🎖️ **Военная кафедра:**
/military - военный учебный центр

🔄 **Перевод:**
/transfer - перевод из других вузов

📚 **Программы:**
/programs - направления подготовки

📋 **Документы:**
/documents - список документов

📞 **Контакты:**
/contacts - контакты приемной комиссии

❓ **FAQ:**
/faq - частые вопросы

📰 **Новости:**
/news - последние новости
/update - обновить данные с сайта
/site_info - информация с сайта

🔄 **Диалог:**
/reset - очистить историю
/stats - статистика диалога

🏫 **Мероприятия:**
/openday - день открытых дверей
    """
    await message.reply(help_text, parse_mode="Markdown")


@dp.message_handler(commands=["reset"])
async def cmd_reset(message: types.Message):
    """Очистка истории диалога"""
    user_id = message.from_user.id
    if user_id in context_memory:
        context_memory[user_id] = []
        await message.reply("🧹 История диалога очищена!")
    else:
        context_memory[user_id] = []
        await message.reply("📭 История пуста")


@dp.message_handler(commands=["stats"])
async def cmd_stats(message: types.Message):
    """Статистика диалога"""
    user_id = message.from_user.id
    if user_id in context_memory:
        history_len = len(context_memory[user_id])
        await message.reply(f"📊 Сообщений в истории: {history_len}")
    else:
        await message.reply("📊 История пуста")


# ===== АДМИНСКИЕ КОМАНДЫ =====
admin_ids = [5031341213]


@dp.message_handler(commands=["index"])
async def cmd_index(message: types.Message):
    """Индексация документов (только для админа)"""
    if message.from_user.id not in admin_ids:
        await message.reply("⛔ Нет прав")
        return

    await message.reply("📚 Начинаю индексацию...")
    try:
        count = rag.index_all_documents()
        await message.reply(f"✅ Добавлено {count} фрагментов")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ===== ОСНОВНЫЕ ФУНКЦИИ =====
def get_simple_response(text, user_id=None):
    """Простые ответы без модели"""
    text_lower = text.lower().strip()

    # Приветствия
    greetings = ['привет', 'здравствуй', 'добрый день', 'здравствуйте']
    if any(greet in text_lower for greet in greetings):
        return ("👋 Здравствуйте! Чем могу помочь?", True)

    # Благодарности
    thanks = ['спасибо', 'благодарю']
    if any(thank in text_lower for thank in thanks):
        return ("🙏 Пожалуйста! Обращайтесь ещё!", True)

    # Прощания
    farewells = ['пока', 'до свидания']
    if any(farewell in text_lower for farewell in farewells):
        return ("👋 Всего доброго! До свидания!", True)

    # FAQ
    faq_answer, found = faq_mtuci.search_faq(text)
    if found:
        return faq_answer, True

    return None, False


def ask_qwen(prompt):
    """Запрос к обычной модели (не чат) через эндпоинт /api/generate"""
    try:
        # Используем эндпоинт /api/generate для обычной модели
        generate_url = f"{OLLAMA_URL}/api/generate"

        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 300
            }
        }

        logger.info(f"Отправка запроса к {generate_url} с моделью {MODEL_NAME}")
        response = requests.post(generate_url, json=payload, timeout=60)

        if response.status_code == 200:
            result = response.json()
            if "response" in result:
                return result["response"].strip()
            else:
                logger.error(f"Нет поля response в ответе: {result}")
                return None
        else:
            logger.error(f"Ошибка HTTP {response.status_code}: {response.text}")
            return None

    except requests.exceptions.Timeout:
        logger.error("Таймаут при запросе к Ollama")
        return None
    except requests.exceptions.ConnectionError:
        logger.error("Ошибка подключения к Ollama")
        return None
    except Exception as e:
        logger.error(f"Ошибка при запросе к модели: {e}")
        return None


def build_prompt(user_id, user_message):
    """Формирование промпта для обычной модели"""
    history = context_memory.get(user_id, [])[-4:]  # Берем последние 4 сообщения для контекста

    # Формируем историю диалога в виде текста
    history_text = ""
    if history:
        history_text = "Предыдущий диалог:\n" + "\n".join(history) + "\n\n"

    # Получаем контекст из разных источников
    faq_answer, found_in_faq = faq_mtuci.search_faq(user_message)
    faq_context = f"Информация из FAQ: {faq_answer}\n" if found_in_faq else ""

    site_context = parser.get_context_for_query(user_message)
    site_context = f"Информация с сайта: {site_context}\n" if site_context else ""

    rag_context = rag.get_context_for_prompt(user_message)
    rag_context = f"Документы: {rag_context}\n" if rag_context else ""

    # Формируем промпт для обычной модели
    prompt = f"""{history_text}Ты - официальный бот приёмной комиссии МТУСИ (Московский технический университет связи и информатики). Отвечай на вопросы о поступлении.

Доступная информация:
{faq_context}{site_context}{rag_context}

ВАЖНЫЕ ПРАВИЛА:
1. Отвечай кратко и по делу (2-3 предложения максимум)
2. Если информации нет в предоставленных источниках, честно скажи об этом и предложи обратиться в приёмную комиссию
3. Не придумывай цифры и факты
4. Будь вежливым и официальным

Вопрос пользователя: {user_message}

Ответ:"""

    logger.info(f"Сформирован промпт длиной {len(prompt)} символов")
    return prompt


# ===== ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ =====
@dp.message_handler()
async def handle_message(message: types.Message):
    """Обработка всех сообщений"""
    user_id = message.from_user.id
    user_text = message.text

    if not user_text or user_text.startswith('/'):
        return

    # Проверяем простые ответы
    simple_response, found = get_simple_response(user_text, user_id)
    if found:
        await message.reply(simple_response)
        return

    # Инициализация истории
    if user_id not in context_memory:
        context_memory[user_id] = []

    # Добавляем сообщение пользователя в историю
    context_memory[user_id].append(f"Пользователь: {user_text}")

    await bot.send_chat_action(message.chat.id, "typing")

    # Пробуем получить ответ от модели
    prompt = build_prompt(user_id, user_text)
    model_answer = ask_qwen(prompt)

    if model_answer:
        answer = model_answer
        logger.info(f"Получен ответ от модели длиной {len(answer)} символов")
    else:
        # Запасные ответы на случай ошибки модели
        logger.warning("Модель не ответила, используем запасные ответы")
        text_lower = user_text.lower()

        if "стоимост" in text_lower or "цена" in text_lower or "сколько стоит" in text_lower:
            answer = "💰 Стоимость обучения в МТУСИ: бакалавриат 280-300 тыс. руб/год, магистратура 295-310 тыс. руб/год. Точную стоимость по вашему направлению можно уточнить в приёмной комиссии."
        elif "документ" in text_lower or "что нужно" in text_lower:
            answer = "📋 Для поступления нужны: паспорт, аттестат/диплом, СНИЛС, фото 3x4 (2 шт), заявление. Подробнее: /documents"
        elif "общежити" in text_lower or "dorm" in text_lower:
            answer = "🏠 Общежитие предоставляется иногородним студентам. Подробнее: /dorm"
        elif "экзамен" in text_lower or "егэ" in text_lower or "балл" in text_lower:
            answer = "📝 Минимальные баллы ЕГЭ: русский язык - 40, математика - 39, физика/информатика - 44. Подробнее: /exam"
        elif "военн" in text_lower or "военная кафедра" in text_lower or "military" in text_lower:
            answer = "🎖️ В МТУСИ есть военный учебный центр. Подробнее: /military"
        else:
            answer = "❓ Извините, я не могу найти ответ на ваш вопрос. Пожалуйста, уточните или используйте /help для списка команд."

    # Сохраняем ответ в историю
    context_memory[user_id].append(f"Бот: {answer}")

    # Ограничиваем историю последними 20 сообщениями
    if len(context_memory[user_id]) > 20:
        context_memory[user_id] = context_memory[user_id][-20:]

    await message.reply(answer)


# ===== ЗАПУСК =====
async def on_startup(dp):
    logger.info("🔄 Загрузка данных с сайта...")
    try:
        parser.update_all()
        logger.info("✅ Данные загружены")

        # Проверяем доступность Ollama при запуске
        try:
            response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get('models', [])
                model_names = [m['name'] for m in models]
                logger.info(f"✅ Ollama доступна. Доступные модели: {model_names}")

                if MODEL_NAME not in model_names and not any(MODEL_NAME in m for m in model_names):
                    logger.warning(f"⚠️ Модель {MODEL_NAME} не найдена в списке доступных")
            else:
                logger.warning("⚠️ Ollama не отвечает")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось проверить Ollama: {e}")

    except Exception as e:
        logger.error(f"⚠️ Ошибка загрузки: {e}")


def main():
    """Основная функция запуска"""
    logger.info("🚀 Запуск бота МТУСИ...")

    # Создаем новый event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Запускаем бота
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True, loop=loop)


if __name__ == "__main__":
    main()