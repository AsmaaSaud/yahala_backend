"""
setup_rag.py — يرفع جميع PDF من مجلد docs/ إلى Supabase
شغّليه مرة واحدة فقط
"""
import os
import time
from dotenv import load_dotenv
from supabase import create_client
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from google import genai
from google.genai import types

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
client   = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

EMBEDDING_MODEL = "gemini-embedding-001"
DOCS_DIR        = "docs"   # مجلد الـ PDF


def extract_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    pages  = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text and text.strip():
            pages.append(f"[Page {i+1}]\n{text.strip()}")
    return "\n\n".join(pages)


def split_text(text: str) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", ".", "،", " "]
    )
    return [c for c in splitter.split_text(text) if c.strip()]


def get_embedding(text: str) -> list[float]:
    emb = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
    )
    return emb.embeddings[0].values


def upload_pdf(pdf_path: str):
    source = os.path.basename(pdf_path)

    # تحقق إذا مرفوع مسبقاً
    existing = supabase.table("documents").select("id").eq("source", source).limit(1).execute()
    if existing.data:
        print(f"  ⚠️  {source} مرفوع مسبقاً — تخطي")
        return

    print(f"  📄 رفع: {source}")
    text   = extract_text(pdf_path)
    chunks = split_text(text)
    print(f"      {len(chunks)} chunks")

    batch = []
    for i, chunk in enumerate(chunks):
        embedding = get_embedding(chunk)
        batch.append({
            "content":     chunk,
            "embedding":   embedding,
            "source":      source,
            "chunk_index": i
        })
        if len(batch) >= 10:
            supabase.table("documents").insert(batch).execute()
            print(f"      ✓ {i+1}/{len(chunks)}")
            batch = []
            time.sleep(0.3)

    if batch:
        supabase.table("documents").insert(batch).execute()

    print(f"      ✅ اكتمل {source}")


if __name__ == "__main__":
    print("\n🚀 YAHALA RAG — رفع الوثائق\n")

    if not os.path.exists(DOCS_DIR):
        print(f"❌ مجلد '{DOCS_DIR}' غير موجود!")
        print("   أنشئي مجلد docs/ وضعي فيه ملفات PDF")
        exit(1)

    pdfs = [f for f in os.listdir(DOCS_DIR) if f.endswith(".pdf")]
    if not pdfs:
        print(f"❌ لا توجد ملفات PDF في مجلد '{DOCS_DIR}'")
        exit(1)

    print(f"📂 وجدت {len(pdfs)} ملف PDF:\n")
    for pdf in sorted(pdfs):
        print(f"   • {pdf}")

    print()
    for pdf in sorted(pdfs):
        upload_pdf(os.path.join(DOCS_DIR, pdf))

    print(f"\n🎉 انتهى! تم رفع {len(pdfs)} ملف")
    print("الخطوة التالية: uvicorn main_rag:app --reload --host 0.0.0.0 --port 8000")
    