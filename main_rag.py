"""
YAHALA Assistant v7.2 — Improved Accuracy, Relevance & Speed
التحسينات:
  1. SYSTEM_PROMPT محسّن — يضمن الرد دائماً بلغة المستخدم (عربي/فرنسي/إسباني/صيني...)
  2. DETECT_PROMPT محسّن — يعالج أسئلة PDF بـ intent صحيح (General بدل StadiumInfo)
  3. PDF_INTENT_MAP — يصحح intent تلقائياً بناءً على الكلمات المفتاحية
  4. parallel execution — يشغّل DB + PDF في نفس الوقت لتقليل الوقت
  5. language_rule محسّنة — تُضاف في system_instruction وليس فقط في prompt
"""

import json
import math
import os
import time
import asyncio
import concurrent.futures
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from supabase import create_client, Client
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, GEMINI_API_KEY]):
    raise RuntimeError("❌ تحقق من ملف .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = genai.Client(api_key=GEMINI_API_KEY)

CHAT_MODEL      = "gemini-2.5-flash"
FALLBACK_MODEL  = "gemini-1.5-flash"
EMBEDDING_MODEL = "gemini-embedding-001"

SAFETY = [
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",       threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
]

INTENT_CONFIG = types.GenerateContentConfig(
    temperature=0.0,   # ✅ تغيير: 0.1 → 0.0 لتثبيت نتائج intent detection
    max_output_tokens=300,  # ✅ تغيير: 1000 → 300 (كافي للـ JSON وأسرع)
    safety_settings=SAFETY,
)

# ✅ تحسين 1: SYSTEM_PROMPT — اللغة في أعلى التعليمات وبشكل أقوى
SYSTEM_PROMPT = """You are 'YAHALA Assistant', the official smart assistant for the YAHALA app and FIFA World Cup 2034 in Saudi Arabia.

⚠️ CRITICAL LANGUAGE RULE — TOP PRIORITY:
- Detect the user's language from their message
- ALWAYS respond in EXACTLY that language — no exceptions
- Arabic → Arabic ONLY | English → English ONLY | French → French ONLY
- Spanish → Spanish ONLY | Chinese → Chinese ONLY | Any other → same language
- If you respond in the wrong language, the response is WRONG regardless of content quality

════════════════════════════════════════
DATA PRIORITY — FOLLOW STRICTLY:
════════════════════════════════════════

1. 🗄️ DATABASE (HIGHEST PRIORITY):
   - If the context contains a "🗄️ DATABASE" section → use it EXCLUSIVELY
   - Present real names, ratings, distances exactly as they appear
   - If distance_km exists → ALWAYS show it ("على بُعد X كم" / "X km away")
   - NEVER say "I don't have hotel/restaurant data" if DATABASE section is present

2. 📄 RAG DOCUMENTS (SECOND):
   - Use ONLY when DATABASE has no relevant results for this intent
   - Present clearly as official document information

3. 🧠 GENERAL KNOWLEDGE (LAST RESORT):
   - Use ONLY when both DATABASE and DOCUMENTS are empty
   - Be clear this is general knowledge

════════════════════════════════════════
SPECIAL RULES:
════════════════════════════════════════

TICKETS (MyTickets intent):
- If DATABASE has ticket rows → show them with event name, date, venue, seat info
- If DATABASE shows EMPTY → say CLEARLY:
  Arabic: "لا يوجد لديك أي تذاكر مسجلة حالياً في التطبيق."
  English: "You have no registered tickets in the app yet."
  Do NOT make up tickets or give generic FIFA ticketing info as primary answer.

HOTELS / RESTAURANTS / EVENTS:
- Show top 3 results from DATABASE
- Format: **Name** ⭐ Rating | 📍 City | 🚗 X.X km | 💰 Price range
- If no distance → omit 🚗

GENERAL:
- Be friendly and concise
- Use Markdown formatting
- Max 3-5 items in lists"""

app = FastAPI(title="YAHALA RAG API v7.2")


# ════════════════════════════════════════════
# 0. Haversine
# ════════════════════════════════════════════
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def sort_by_distance(items: list, user_lat: float, user_lon: float, limit: int = 3) -> list:
    enriched = []
    for item in items:
        lat = item.get("latitude") or item.get("lat")
        lon = item.get("longitude") or item.get("lon")
        item = dict(item)
        if lat is not None and lon is not None:
            try:
                item["distance_km"] = round(haversine_km(user_lat, user_lon, float(lat), float(lon)), 1)
            except Exception:
                item["distance_km"] = None
        else:
            item["distance_km"] = None
        enriched.append(item)
    enriched.sort(key=lambda x: x["distance_km"] if x["distance_km"] is not None else 9999)
    return enriched[:limit]


# ════════════════════════════════════════════
# ✅ تحسين 2: DETECT_PROMPT أوضح وأشمل
# ════════════════════════════════════════════
DETECT_PROMPT = """You are an intent classifier. Return ONLY a JSON object, no markdown, no explanation.

INTENT DEFINITIONS (read carefully):
- MyTickets: user asks about THEIR OWN tickets, bookings, reservations
  Examples: my tickets, ticket help, تذاكري, مساعدة في التذاكر
- MatchSchedule: asks about match dates, schedules, fixtures, which teams play
  Examples: match schedule, متى مباراة السعودية, quel match, próximo partido, 下一场比赛
- Hotels: asks about hotels, accommodation, lodging, where to stay
  Examples: nearby hotels, الفنادق القريبة, hôtels, hoteles, 酒店
- Restaurants: asks about restaurants, food, dining, eating, halal food
  Examples: nearby restaurants, مطاعم قريبة, restaurantes, 餐厅
- StadiumInfo: asks about stadium LOCATIONS, names, capacity, directions to stadium
  Examples: stadium locations, مواقع الملاعب, where is King Salman Stadium
  ⚠️ NOT for questions about rules/prohibited items/conduct inside the stadium → use General
- FanZone: asks about fan zones, fan parks, entertainment events, مناطق المشجعين
- Emergency: asks about emergency services, police, ambulance, hospitals
  Examples: emergency services, طوارئ, quelle urgence, emergencia
- UserProfile: asks about own profile, personal info, ملفي, بياناتي
- General: ALL other questions including:
  * Rules/conduct inside stadiums (prohibited items, dress code, behavior)
  * Visa, entry requirements, travel documents
  * Saudi culture, customs, laws
  * Currency, prayer times, weather
  * Ticket PRICES or HOW TO BUY tickets (not "my tickets")
  * Medical facilities AT stadiums
  * Any factual question about Saudi Arabia

Return JSON with keys: language, language_code, intent, entity (or null)

Examples:
- "My tickets" → {{"language": "English", "language_code": "en", "intent": "MyTickets", "entity": null}}
- "Nearby hotels" → {{"language": "English", "language_code": "en", "intent": "Hotels", "entity": null}}
- "تذاكري" → {{"language": "Arabic", "language_code": "ar", "intent": "MyTickets", "entity": null}}
- "الفنادق القريبة" → {{"language": "Arabic", "language_code": "ar", "intent": "Hotels", "entity": null}}
- "مطاعم قريبة" → {{"language": "Arabic", "language_code": "ar", "intent": "Restaurants", "entity": null}}
- "What items are not allowed inside the stadium?" → {{"language": "English", "language_code": "en", "intent": "General", "entity": null}}
- "What are the rules of conduct at the stadium?" → {{"language": "English", "language_code": "en", "intent": "General", "entity": null}}
- "Are there medical facilities at the stadiums?" → {{"language": "English", "language_code": "en", "intent": "General", "entity": null}}
- "How can I buy World Cup tickets?" → {{"language": "English", "language_code": "en", "intent": "General", "entity": null}}
- "Can I get a refund for my ticket?" → {{"language": "English", "language_code": "en", "intent": "General", "entity": null}}
- "ما هي أسعار التذاكر؟" → {{"language": "Arabic", "language_code": "ar", "intent": "General", "entity": null}}
- "Quel est le prochain match?" → {{"language": "French", "language_code": "fr", "intent": "MatchSchedule", "entity": null}}
- "¿Cuándo es el próximo partido?" → {{"language": "Spanish", "language_code": "es", "intent": "MatchSchedule", "entity": null}}
- "附近有什么酒店?" → {{"language": "Chinese", "language_code": "zh", "intent": "Hotels", "entity": null}}
- "فعاليات وترفيه" → {{"language": "Arabic", "language_code": "ar", "intent": "FanZone", "entity": null}}
- "What are the social etiquette rules in Saudi Arabia?" → {{"language": "English", "language_code": "en", "intent": "General", "entity": null}}

Message: {message}"""


# ✅ تحسين 3: خريطة تصحيح intent بناءً على كلمات مفتاحية في السؤال
# هذه تُعالج الحالات التي يُصنّفها Gemini بشكل خاطئ
PDF_INTENT_KEYWORDS = {
    "General": [
        "prohibited", "not allowed", "rules of conduct", "behavior", "behaviour",
        "dress code", "social etiquette", "etiquette rules",
        "medical facilit", "hospital", "medical center",
        "buy ticket", "purchase ticket", "ticket price", "how much ticket",
        "refund", "ticket refund",
        "visa", "entry document", "customs", "currency", "prayer time",
        "heat safety", "traffic fine", "public conduct",
        "ممنوع", "قواعد السلوك", "اللباس", "آداب", "مستشفى",
        "سعر التذكرة", "أسعار التذاكر", "كيف أشتري", "استرداد",
        "تأشيرة", "جمارك", "عملة", "درجة الحرارة",
        "comment acheter", "prix des billets", "règles", "etiquette",
        "cómo comprar", "precio de entradas", "reglas",
    ]
}

def correct_intent(message: str, detected_intent: str) -> str:
    """
    ✅ تحسين: يصحح intent إذا كانت الكلمات المفتاحية تدل على General
    """
    msg_lower = message.lower()
    for target_intent, keywords in PDF_INTENT_KEYWORDS.items():
        if any(kw in msg_lower for kw in keywords):
            return target_intent
    return detected_intent


def analyze_message(message: str) -> dict:
    try:
        prompt = DETECT_PROMPT.replace("{message}", message)
        r = client.models.generate_content(
            model=CHAT_MODEL,
            contents=prompt,
            config=INTENT_CONFIG,
        )
        text = r.text.strip().strip("```json").strip("```").strip()
        result = json.loads(text)

        # ✅ تصحيح intent تلقائياً
        result["intent"] = correct_intent(message, result.get("intent", "General"))
        return result
    except Exception as e:
        print(f"⚠️ Intent error: {e}")
        return {"language": "English", "language_code": "en", "intent": "General", "entity": None}


# ════════════════════════════════════════════
# 2. جلب بيانات المستخدم
# ════════════════════════════════════════════
def fetch_user_profile(user_id: int) -> dict:
    try:
        r = supabase.table("users") \
            .select("user_id,name,city,nationality,gender,id_number,email,latitude,longitude") \
            .eq("user_id", user_id).limit(1).execute()
        return (r.data[0] if r.data else {})
    except Exception as e:
        print(f"⚠️ Profile error: {e}")
        return {}


# ════════════════════════════════════════════
# 3. جلب البيانات من الداتابيس
# ════════════════════════════════════════════
def fetch_db_context(
    intent: str,
    entity: str | None,
    user_id: int,
    user_city: str | None = None,
    user_lat: float | None = None,
    user_lon: float | None = None,
) -> list:
    try:
        if intent == "MyTickets":
            r = supabase.table("tickets") \
                .select("ticket_id,ticket_state,seat_gate,seat_block,seat_row,seat_number,events(event_name,city,venue_name,start_datetime,event_status)") \
                .eq("user_id", user_id) \
                .execute()
            return r.data if r.data else []

        elif intent == "MatchSchedule":
            if entity:
                r = supabase.table("events") \
                    .select("event_id,event_name,city,start_datetime,end_datetime,venue_name,event_status") \
                    .ilike("event_name", f"%{entity}%") \
                    .order("start_datetime").execute()
            else:
                r = supabase.table("events") \
                    .select("event_id,event_name,city,start_datetime,venue_name,event_status") \
                    .order("start_datetime").limit(10).execute()
            return r.data or []

        elif intent == "StadiumInfo":
            r = supabase.table("events").select("venue_name,city").execute()
            seen, unique = set(), []
            for row in (r.data or []):
                if row["venue_name"] not in seen:
                    seen.add(row["venue_name"])
                    unique.append(row)
            return unique

        elif intent == "Hotels":
            cols = "service_name,city,rating,price_range,contact_info,opening_hours,languages_supported,latitude,longitude"
            query = supabase.table("services").select(cols).ilike("service_category", "hotel")
            if user_city:
                query = query.ilike("city", f"%{user_city}%")
            r = query.order("rating", desc=True).limit(20).execute()
            results = r.data or []
            if not results:
                r = supabase.table("services").select(cols).ilike("service_category", "hotel") \
                    .order("rating", desc=True).limit(20).execute()
                results = r.data or []
            if user_lat is not None and user_lon is not None and results:
                return sort_by_distance(results, user_lat, user_lon, limit=3)
            return results[:3]

        elif intent == "Restaurants":
            cols = "service_name,city,rating,price_range,contact_info,opening_hours,halal_certified,latitude,longitude"
            query = supabase.table("services").select(cols).ilike("service_category", "restaurant")
            if user_city:
                query = query.ilike("city", f"%{user_city}%")
            r = query.order("rating", desc=True).limit(20).execute()
            results = r.data or []
            if not results:
                r = supabase.table("services").select(cols).ilike("service_category", "restaurant") \
                    .order("rating", desc=True).limit(20).execute()
                results = r.data or []
            if user_lat is not None and user_lon is not None and results:
                return sort_by_distance(results, user_lat, user_lon, limit=3)
            return results[:3]

        elif intent == "FanZone":
            cols = "service_name,city,opening_hours,contact_info,tags,latitude,longitude"
            query = supabase.table("services").select(cols).ilike("service_category", "event")
            if user_city:
                query = query.ilike("city", f"%{user_city}%")
            r = query.order("rating", desc=True).limit(20).execute()
            results = r.data or []
            if not results:
                r = supabase.table("services").select(cols).ilike("service_category", "event") \
                    .order("rating", desc=True).limit(20).execute()
                results = r.data or []
            if user_lat is not None and user_lon is not None and results:
                return sort_by_distance(results, user_lat, user_lon, limit=3)
            return results[:3]

        elif intent == "Emergency":
            return [{"info": "Emergency: 911 | Police: 999 | Ambulance: 997 | Civil Defense: 998"}]

        elif intent == "UserProfile":
            r = supabase.table("users") \
                .select("name,city,nationality,gender,birthDate") \
                .eq("user_id", user_id).limit(1).execute()
            return [r.data[0]] if r.data else []

    except Exception as e:
        print(f"⚠️ DB error: {e}")
    return []


# ════════════════════════════════════════════
# 4. RAG — بحث في PDF
# ════════════════════════════════════════════
def search_documents(query: str, top_k: int = 4) -> list[dict]:
    try:
        emb = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=query,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
        )
        embedding = emb.embeddings[0].values
        r = supabase.rpc("match_documents", {
            "query_embedding": embedding,
            "match_count": top_k,
            "match_threshold": 0.5
        }).execute()
        return r.data or []
    except Exception as e:
        print(f"⚠️ Vector search: {e}")
        return []


# ════════════════════════════════════════════
# ✅ تحسين 4: تشغيل DB + PDF بالتوازي
# ════════════════════════════════════════════
def fetch_db_and_pdf_parallel(
    intent: str,
    entity: str | None,
    user_id: int,
    user_city: str | None,
    user_lat: float | None,
    user_lon: float | None,
    user_message: str,
) -> tuple[list, list]:
    """
    يشغّل fetch_db_context و search_documents في نفس الوقت بدلاً من تسلسلي.
    يوفر ~2-3 ثانية في المتوسط.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        db_future  = executor.submit(
            fetch_db_context, intent, entity, user_id, user_city, user_lat, user_lon
        )
        pdf_future = executor.submit(search_documents, user_message)
        db_results  = db_future.result()
        pdf_results = pdf_future.result()
    return db_results, pdf_results


# ════════════════════════════════════════════
# 5. بناء الـ Prompt
# ════════════════════════════════════════════
def build_prompt(
    message: str,
    language: str,
    language_code: str,
    pdf_results: list,
    db_results: list,
    user_profile: dict,
    intent: str = "General",
) -> str:
    # ✅ تحسين 5: قاعدة اللغة تتضمن جميع اللغات المدعومة وأكثر إلحاحاً
    lang_rule = (
        f"🚨 MANDATORY LANGUAGE: Respond in {language} ONLY. "
        f"Language code: {language_code}. "
        f"Do NOT switch to English or any other language."
    )
    sections = []

    if user_profile:
        lat = user_profile.get("latitude")
        lon = user_profile.get("longitude")
        loc_str = f"\n  GPS: ({round(float(lat),4)}, {round(float(lon),4)})" if lat and lon else ""
        sections.append(
            f"👤 USER:\n"
            f"  Name: {user_profile.get('name','')}\n"
            f"  City: {user_profile.get('city','')}"
            f"{loc_str}"
        )

    if intent == "MyTickets":
        if db_results:
            sections.append(
                "🗄️ DATABASE — USER TICKETS:\n" +
                json.dumps(db_results, ensure_ascii=False, indent=2)
            )
        else:
            sections.append("🗄️ DATABASE — USER TICKETS: [] (EMPTY — user has NO tickets)")
    elif db_results:
        sections.append(
            "🗄️ DATABASE RESULTS (USE THESE — real data):\n" +
            json.dumps(db_results, ensure_ascii=False, indent=2)
        )

    if pdf_results:
        sections.append(
            "📄 OFFICIAL DOCUMENTS (use only if database has nothing relevant):\n" +
            "\n\n".join(f"[{r['source']}]\n{r['content']}" for r in pdf_results)
        )

    ctx = ("\n\n" + "─" * 40 + "\n\n").join(sections)

    extra = ""
    if intent == "MyTickets" and not db_results:
        extra = f"\nCRITICAL: User has NO tickets in DB. State this clearly IN {language}. Do not give general FIFA ticketing info as main answer."
    elif db_results and any(
        isinstance(r, dict) and r.get("distance_km") is not None for r in db_results
    ):
        extra = "\nSHOW DISTANCE: Each result has distance_km → always display it per item."

    return (
        f"{lang_rule}\n\n"
        f"{'═'*40}\n"
        f"CONTEXT:\n{ctx}\n"
        f"{'═'*40}\n"
        f"{extra}\n\n"
        f"User ({language}): {message}\n\nAnswer in {language}:"
    )


# ════════════════════════════════════════════
# 6. Endpoint: الترحيب
# ════════════════════════════════════════════
@app.get("/user/greeting")
def get_user_greeting(user_id: int = Query(...)):
    try:
        profile = fetch_user_profile(user_id)
        return {"name": profile.get("name", ""), "city": profile.get("city", "")}
    except Exception as e:
        raise HTTPException(500, str(e))


# ════════════════════════════════════════════
# 7. Endpoints الرئيسية
# ════════════════════════════════════════════
@app.get("/")
def root():
    return {"status": "✅ YAHALA RAG API v7.2"}


@app.post("/chat")
def chat(
    user_message: str = Query(...),
    user_id: str = Query(...),
    user_lat: float | None = Query(default=None),
    user_lon: float | None = Query(default=None),
):
    if not user_message.strip():
        raise HTTPException(400, "Message is empty")
    try:
        uid = int(user_id)
        user_profile = fetch_user_profile(uid)
        user_city = user_profile.get("city")
        lat = user_lat if user_lat is not None else user_profile.get("latitude")
        lon = user_lon if user_lon is not None else user_profile.get("longitude")

        analysis = analyze_message(user_message)
        intent   = analysis.get("intent", "General")
        entity   = analysis.get("entity")
        language = analysis.get("language", "English")
        lang_code= analysis.get("language_code", "en")

        # ✅ تشغيل DB + PDF بالتوازي
        db, pdf = fetch_db_and_pdf_parallel(intent, entity, uid, user_city, lat, lon, user_message)
        print(f"📊 intent={intent} DB={len(db)} PDF={len(pdf)} lat={lat} lon={lon}")

        prompt = build_prompt(user_message, language, lang_code, pdf, db, user_profile, intent)

        # ✅ تحسين: language_code في system_instruction أيضاً
        system = SYSTEM_PROMPT + f"\n\nCurrent user language: {language} ({lang_code}). Respond ONLY in {language}."

        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.7,
                max_output_tokens=800,
                safety_settings=SAFETY,
            )
        )
        try:
            reply = response.text
        except Exception:
            reply = "Sorry, could not generate a response. Please try again."

        return {"reply": reply, "language": language, "intent": intent}

    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(500, str(e))


@app.post("/chat/stream")
def chat_stream(
    user_message: str = Query(...),
    user_id: str = Query(...),
    user_lat: float | None = Query(default=None),
    user_lon: float | None = Query(default=None),
):
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(400, "user_id must be a number")

    user_profile = fetch_user_profile(uid)
    user_city    = user_profile.get("city")
    lat = user_lat if user_lat is not None else user_profile.get("latitude")
    lon = user_lon if user_lon is not None else user_profile.get("longitude")

    analysis  = analyze_message(user_message)
    intent    = analysis.get("intent", "General")
    entity    = analysis.get("entity")
    language  = analysis.get("language", "English")
    lang_code = analysis.get("language_code", "en")

    # ✅ تشغيل DB + PDF بالتوازي
    db, pdf = fetch_db_and_pdf_parallel(intent, entity, uid, user_city, lat, lon, user_message)
    print(f"📊 intent={intent} DB={len(db)} PDF={len(pdf)} lat={lat} lon={lon} user={user_profile.get('name','')}")

    prompt = build_prompt(user_message, language, lang_code, pdf, db, user_profile, intent)

    # ✅ system instruction مع اللغة
    system = SYSTEM_PROMPT + f"\n\nCurrent user language: {language} ({lang_code}). Respond ONLY in {language}."

    def stream():
        for attempt in range(3):
            model_to_use = CHAT_MODEL if attempt < 2 else FALLBACK_MODEL
            try:
                for chunk in client.models.generate_content_stream(
                    model=model_to_use,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=0.7,
                        max_output_tokens=800,
                        safety_settings=SAFETY,
                    )
                ):
                    try:
                        if chunk.text:
                            yield chunk.text
                    except Exception:
                        continue
                break
            except Exception as e:
                err = str(e)
                if "503" in err or "UNAVAILABLE" in err:
                    if attempt < 2:
                        time.sleep(1.5)
                        continue
                    else:
                        yield "\n\n⚠️ الخادم مشغول حالياً، يرجى المحاولة مرة أخرى."
                else:
                    yield f"\n\n❌ Error: {e}"
                break

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


@app.get("/health")
def health():
    checks = {}
    try:
        r = supabase.table("events").select("count", count="exact").execute()
        checks["events"]   = f"✅ {r.count} records"
    except:
        checks["events"]   = "❌"
    try:
        r = supabase.table("services").select("count", count="exact").execute()
        checks["services"] = f"✅ {r.count} records"
    except:
        checks["services"] = "❌"
    try:
        client.models.generate_content(
            model=CHAT_MODEL,
            contents="ping",
            config=types.GenerateContentConfig(max_output_tokens=5)
        )
        checks["gemini"] = "✅ google.genai"
    except Exception as e:
        checks["gemini"] = f"❌ {e}"
    return {"status": "running", "version": "7.2", "checks": checks}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)