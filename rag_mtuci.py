#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RAG система для документов МТУСИ
Поддерживает: PDF (текстовые и сканированные), DOCX, XLSX, TXT
"""

import os
import logging
from pathlib import Path
import re
import warnings
from typing import List, Dict, Optional
import contextlib
import io
import json
import hashlib
# Отключаем вывод tqdm (прогресс-бары ChromaDB)
import os
os.environ['CHROMA_NO_PROGRESS_BAR'] = '1'

# Перехватываем stdout для подавления любых выводов ChromaDB
import sys
import io

# Сохраняем оригинальный stdout
_original_stdout = sys.stdout

def _silent_stdout():
    """Временно подавляет вывод"""
    sys.stdout = io.StringIO()

def _restore_stdout():
    """Восстанавливает вывод"""
    sys.stdout = _original_stdout

# Подавление предупреждений
warnings.filterwarnings("ignore")
os.environ['CHROMA_TELEMETRY_ENABLED'] = 'false'
os.environ['ANONYMIZED_TELEMETRY'] = 'false'
os.environ['DO_NOT_TRACK'] = '1'
os.environ['CHROMA_NO_PROGRESS_BAR'] = '1'

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

# Чтение документов
import PyPDF2
import pdfplumber

# Опциональные импорты
try:
    from pdf2image import convert_from_path
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    import pandas as pd
    XLSX_AVAILABLE = True
except ImportError:
    XLSX_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

logger = logging.getLogger(__name__)
logging.getLogger('chromadb').setLevel(logging.ERROR)


class DocumentReader:
    """Чтение документов разных форматов"""

    @staticmethod
    def read_pdf(file_path: Path) -> str:
        """Читает текст из PDF (текст + OCR для страниц с картинками)"""
        text = ""
        
        try:
            # Открываем PDF через pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    # Пробуем извлечь текст
                    page_text = page.extract_text()
                    
                    # Если текста на странице мало - запускаем OCR для этой страницы
                    if not page_text or len(page_text.strip()) < 50:
                        if OCR_AVAILABLE:
                            # Конвертируем страницу в изображение
                            from PIL import Image
                            import io
                            
                            # Сохраняем страницу как изображение
                            img = page.to_image(resolution=200).original
                            
                            # Распознаём текст
                            ocr_text = pytesseract.image_to_string(img, lang='rus+eng')
                            if ocr_text:
                                text += f"\n--- Страница {page_num + 1} (OCR) ---\n{ocr_text}"
                        else:
                            text += f"\n--- Страница {page_num + 1} (текст не извлечён) ---\n"
                    else:
                        text += page_text + "\n"
                        
        except Exception as e:
            # Запасной вариант через PyPDF2
            try:
                with open(file_path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    for page_num, page in enumerate(reader.pages):
                        page_text = page.extract_text()
                        if page_text and len(page_text.strip()) > 50:
                            text += page_text + "\n"
                        elif OCR_AVAILABLE:
                            # OCR через pdf2image для всей страницы
                            images = convert_from_path(str(file_path), first_page=page_num+1, last_page=page_num+1, dpi=200)
                            for img in images:
                                ocr_text = pytesseract.image_to_string(img, lang='rus+eng')
                                if ocr_text:
                                    text += f"\n--- Страница {page_num + 1} (OCR) ---\n{ocr_text}"
            except Exception as ex:
                logger.error(f"Ошибка чтения PDF {file_path.name}: {ex}")
        
        return text.strip()
    
    @staticmethod
    def read_docx(file_path: Path) -> str:
        """Читает текст из DOCX"""
        if not DOCX_AVAILABLE:
            return ""
        try:
            doc = Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            logger.error(f"Ошибка чтения DOCX {file_path.name}: {e}")
            return ""

    @staticmethod
    def read_xlsx(file_path: Path) -> str:
        """Читает текст из XLSX"""
        if not XLSX_AVAILABLE:
            return ""
        try:
            df = pd.read_excel(file_path)
            return df.to_string()
        except Exception as e:
            logger.error(f"Ошибка чтения XLSX {file_path.name}: {e}")
            return ""

    @staticmethod
    def read_txt(file_path: Path) -> str:
        """Читает текст из TXT"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, 'r', encoding='cp1251') as f:
                    return f.read()
            except Exception:
                return ""

    @staticmethod
    def read_file(file_path: Path) -> str:
        """Определяет формат и читает файл"""
        suffix = file_path.suffix.lower()
        if suffix == '.pdf':
            return DocumentReader.read_pdf(file_path)
        elif suffix == '.docx':
            return DocumentReader.read_docx(file_path)
        elif suffix == '.xlsx':
            return DocumentReader.read_xlsx(file_path)
        elif suffix == '.txt':
            return DocumentReader.read_txt(file_path)
        return ""


class CustomEmbeddingFunction:
    """Кастомная функция эмбеддингов для ChromaDB"""
    
    def __init__(self):
        self.model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
    
    def __call__(self, input):
        if isinstance(input, str):
            texts = [input]
        else:
            texts = input
        return self.model.encode(texts).tolist()


class MTUCIRAG:
    def __init__(self):
        self.persist_directory = "rag_db"
        self.docs_path = Path("RAG/documents")
        os.makedirs(self.persist_directory, exist_ok=True)
        
        # Файл для хранения хеша документов
        self.manifest_file = Path(self.persist_directory) / "documents_manifest.json"
        
        # Подключаемся к ChromaDB
        self.client = chromadb.PersistentClient(
            path=self.persist_directory,
            settings=Settings(anonymized_telemetry=False)
        )
        
        # Получаем существующую коллекцию или создаём новую
        self.collection = self.client.get_or_create_collection(
            name="mtuci_docs",
            embedding_function=CustomEmbeddingFunction(),
            metadata={"hnsw:space": "cosine"}
        )
        
        logger.info("✅ RAG система инициализирована")


    def _get_documents_hash(self) -> str:
        """Вычисляет хеш всех документов для отслеживания изменений"""
        if not self.docs_path.exists():
            return ""
        
        file_hashes = []
        
        for category_folder in sorted(self.docs_path.iterdir()):
            if not category_folder.is_dir() or category_folder.name.startswith('.'):
                continue
            
            for file_path in sorted(category_folder.glob("*.*")):
                if file_path.suffix.lower() not in ['.txt', '.pdf', '.docx', '.xlsx']:
                    continue
                
                # Используем имя файла + размер + время модификации
                stat = file_path.stat()
                file_info = f"{category_folder.name}/{file_path.name}|{stat.st_size}|{stat.st_mtime}"
                file_hashes.append(file_info)
        
        # Вычисляем общий хеш
        combined = "|".join(file_hashes)
        return hashlib.md5(combined.encode()).hexdigest()
    
    def _load_manifest(self) -> dict:
        """Загружает сохранённый манифест"""
        if self.manifest_file.exists():
            try:
                with open(self.manifest_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {'hash': '', 'total_chunks': 0}
    
    def _save_manifest(self, docs_hash: str, total_chunks: int):
        """Сохраняет манифест"""
        with open(self.manifest_file, 'w') as f:
            json.dump({'hash': docs_hash, 'total_chunks': total_chunks}, f)
    
    def needs_indexing(self) -> bool:
        """Проверяет, нужно ли переиндексировать документы"""
        current_hash = self._get_documents_hash()
        manifest = self._load_manifest()
        
        if not current_hash:
            return True
        
        return current_hash != manifest.get('hash', '')
    
    def ensure_indexed(self) -> int:
        """Индексирует документы только если они изменились ИЛИ коллекция пуста"""
        
        # Проверяем реальное состояние коллекции
        try:
            actual_count = self.collection.count()
        except:
            actual_count = 0
        
        # Проверяем, нужно ли индексировать
        needs_indexing = self.needs_indexing() or actual_count == 0
        
        if not needs_indexing:
            # Всё в порядке
            return actual_count
        
        # Нужна индексация
        print("📚 Обнаружены изменения в документах или коллекция пуста. Выполняю индексацию...")
        total_chunks = self.index_all_documents()
        
        # Сохраняем манифест
        current_hash = self._get_documents_hash()
        self._save_manifest(current_hash, total_chunks)
        
        return total_chunks

    def _split_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """Разбивает текст на чанки"""
        if not text or len(text) < 100:
            return [text] if text else []
        
        # Разбиваем по предложениям
        sentences = re.split(r'(?<=[.!?])\s+', text.replace('\n', ' '))
        
        chunks = []
        current_chunk = ""
        
        for sent in sentences:
            if len(current_chunk) + len(sent) < chunk_size:
                current_chunk += sent + " "
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sent + " "
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks

    def index_all_documents(self) -> int:
        """Индексирует все документы в ChromaDB (полностью без вывода)"""
        if not self.docs_path.exists():
            return 0
        
        total_chunks = 0
        
        for category_folder in self.docs_path.iterdir():
            if not category_folder.is_dir() or category_folder.name.startswith('.'):
                continue
            
            category = category_folder.name
            
            for file_path in category_folder.glob("*.*"):
                if file_path.suffix.lower() not in ['.txt', '.pdf', '.docx', '.xlsx']:
                    continue
                
                text = DocumentReader.read_file(file_path)
                if not text or len(text) < 100:
                    continue
                
                chunks = self._split_text(text)
                
                for i, chunk in enumerate(chunks):
                    if len(chunk) < 50:
                        continue
                    
                    doc_id = f"{category}_{file_path.stem}_{i}"
                    
                    # Полностью подавляем вывод ChromaDB
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.collection.add(
                            ids=[doc_id],
                            documents=[chunk],
                            metadatas=[{
                                "category": category,
                                "source": file_path.name
                            }]
                        )
                    total_chunks += 1
        
        return total_chunks

    def search(self, query: str, n_results: int = 5, category: str = None) -> Optional[Dict]:
        """Поиск документов с возможной фильтрацией по категории"""
        try:
            where_filter = {"category": category} if category else None
            
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_filter,
                include=["documents", "metadatas", "distances"]
            )
            return results
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")
            return None

    def get_context_for_prompt(self, query: str, max_chars: int = 1000) -> str:
        """Возвращает отформатированный контекст для промпта"""
        results = self.search(query, n_results=5)
        
        if not results or not results['documents']:
            return ""
        
        context_parts = []
        total_len = 0
        used_sources = set()
        
        for i, doc_text in enumerate(results['documents'][0]):
            metadata = results['metadatas'][0][i]
            category = metadata.get('category', '?')
            source = metadata.get('source', '?')
            
            source_key = f"{category}/{source}"
            if source_key in used_sources:
                continue
            used_sources.add(source_key)
            
            header = f"[{category}/{source}]"
            remaining = max_chars - total_len - len(header) - 50
            
            if remaining <= 0:
                break
            
            if len(doc_text) > remaining:
                doc_text = doc_text[:remaining] + "..."
            
            context_parts.append(f"{header}\n{doc_text}")
            total_len += len(doc_text) + len(header) + 50
        
        if not context_parts:
            return ""
        
        return "📚 ИЗ ДОКУМЕНТОВ МТУСИ:\n\n" + "\n\n---\n\n".join(context_parts)

    def get_stats(self) -> Dict:
        """Возвращает статистику RAG системы"""
        try:
            results = self.collection.get(limit=10000)
            categories = set()
            if results and results['metadatas']:
                for meta in results['metadatas']:
                    if cat := meta.get('category'):
                        categories.add(cat)
            return {
                'total_chunks': len(results['ids']) if results else 0,
                'categories': sorted(list(categories))
            }
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return {'total_chunks': 0, 'categories': []}


# Создаём глобальный экземпляр
rag = MTUCIRAG()