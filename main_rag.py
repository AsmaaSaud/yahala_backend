"""
YAHALA Assistant v8.0 — Smart, Location-Aware, DB-First
- يجلب من DB أولاً بناءً على الإحداثيات الحقيقية (GPS)
- يرتّب النتائج بالمسافة الفعلية (km)
- يعود للـ PDF فقط إذا لم يجد في DB
- يخاطب المستخدم باسمه في كل رد
"""

import json
import math
import os
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
EMBEDDING_MODEL = "gemini-embedding-001"

SAFETY = [
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",       threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
]

INTENT_CONFIG = types.GenerateContentConfig(
    temperature=0.1,
    max_output_tokens=500,
    safety_settings=SAFETY,
)

# ════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — شخصية المساعد
# ════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are 'YAHALA Assistant', the official smart assistant for the YAHALA app and FIFA World Cup 2034 in Saudi Arabia.

CRITICAL RULES — NEVER BREAK THESE:
1. LANGUAGE: Always respond in EXACTLY the same language the user wrote in. Arabic→Arabic, English→English, etc.
2. PERSONALIZATION: If user's name is provided, address them by first name warmly in EVERY response.
   - Arabic: "أهلاً [Name]،" or "بالتأكيد [Name]،"
   - English: "Hi [Name]!" or "Of course, [Name]!"
3. DB-FIRST: Always present database results FIRST. Only use PDF documents as supplementary info.
4. DISTANCE: When GPS coordinates are available and distances are provided, always mention them.
   - Show top 5 closest options, mention distance in km.
5. FORMAT: Use Markdown — bold for names, bullet points for lists, headers for sections.
6. TICKETS: Show real ticket details from database. Include event name, date, seat info, status.
7. HOTELS/RESTAURANTS: Show max 5 options sorted by distance (if GPS available) or rating.
8. Be warm, helpful, and concise. Never say "I don't have information" if DB data is provided."""

# ════════════════════════════════════════════════════════════════
# INTENT CLASSIFIER
# ════════════════════════════════════════════════════════════════
DETECT_PROMPT = """You are an intent classifier. Return ONLY a JSON object, no markdown, no explanation.

INTENTS:
- MyTickets: tickets, bookings, reservations (my tickets, تذاكري, تذكرتي)
- MatchSchedule: matches, games, fixtures, schedule (مباراة, جدول, متى تلعب)
- Hotels: hotels, accommodation, lodging, stay, where to sleep (فنادق, إقامة, أين أنام, الفنادق القريبة)
- Restaurants: food, restaurants, dining, eat, halal (مطاعم, أكل, طعام, مطعم قريب)
- StadiumInfo: stadiums, venues, arena (ملاعب, ملعب, أين الملعب, مكان المباراة)
- FanZone: fan zones, events, entertainment, activities (فان زون, فعاليات, ترفيه, أنشطة)
- Emergency: emergency, police, hospital, ambulance (طوارئ, إسعاف, شرطة, مستشفى)
- UserProfile: my profile, my info, my data, who am I (بياناتي, معلوماتي, ملفي, من أنا)
- General: anything else about World Cup 2034, Saudi Arabia, FIFA

Message: "{message}"

JSON: {{"language": "English", "language_code": "en", "intent": "General", "entity": null}}

Rules:
- entity = team name if mentioned (e.g. "Saudi Arabia", "Brazil", "السعودية"), else null
- Return ONLY JSON, nothing else

Examples:
- "Nearby hotels" -> {{"language": "English", "language_code": "en", "intent": "Hotels", "entity": null}}
- "My tickets" -> {{"language": "English", "language_code": "en", "intent": "MyTickets", "entity": null}}
- "Stadium locations" -> {{"language": "English", "language_code": "en", "intent": "StadiumInfo", "entity": null}}
- "Match schedule" -> {{"language": "English", "language_code": "en", "intent": "MatchSchedule", "entity": null}}
- "next game Saudi Arabia" -> {{"language": "English", "language_code": "en", "intent": "MatchSchedule", "entity": "Saudi Arabia"}}
- "Nearby restaurants" -> {{"language": "English", "language_code": "en", "intent": "Restaurants", "entity": null}}
- "الفنادق القريبة" -> {{"language": "Arabic", "language_code": "ar", "intent": "Hotels", "entity": null}}
- "تذاكري" -> {{"language": "Arabic", "language_code": "ar", "intent": "MyTickets", "entity": null}}
- "مطاعم قريبة" -> {{"language": "Arabic", "language_code": "ar", "intent": "Restaurants", "entity": null}}
- "مواقع الملاعب" -> {{"language": "Arabic", "language_code": "ar", "intent": "StadiumInfo", "entity": null}}
- "متى مباراة السعودية" -> {{"language": "Arabic", "language_code": "ar", "intent": "MatchSchedule", "entity": "Saudi Arabia"}}
- "فعاليات وترفيه" -> {{"language": "Arabic", "language_code": "ar", "intent": "FanZone", "entity": null}}
- "Où sont les hôtels?" -> {{"language": "French", "language_code": "fr", "intent": "Hotels", "entity": null}}"""


# ════════════════════════════════════════════════════════════════
# HELPER: حساب المسافة بين نقطتين (Haversine)
# ════════════════════════════════════════════════════════════════
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """يحسب المسافة بالكيلومترات بين إحداثيتين"""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


app = FastAPI(title="YAHALA RAG API v8.0")


# ════════════════════════════════════════════════════════════════
# 1. تحليل النية واللغة
# ════════════════════════════════════════════════════════════════
def analyze_message(message: str) -> dict:
    try:
        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=DETECT_PROMPT.format(message=message),
            config=INTENT_CONFIG,
        )
        text = response.text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        print(f"✅ Intent: {result}")
        return result
    except Exception as e:
        print(f"⚠️ Intent error: {e}")
        return {"language": "English", "language_code": "en", "intent": "General", "entity": None}


# ════════════════════════════════════════════════════════════════
# 2. جلب بيانات المستخدم
# ════════════════════════════════════════════════════════════════
def fetch_user_profile(user_id: int) -> dict:
    try:
        r = supabase.table("users") \
            .select("user_id,name,city,nationality,gender,latitude,longitude") \
            .eq("user_id", user_id) \
            .maybeSingle() \
            .execute()
        data = r.data or {}
        print(f"✅ User: {data.get('name','?')} | city={data.get('city','?')} | lat={data.get('latitude')} | lon={data.get('longitude')}")
        return data
    except Exception as e:
        print(f"⚠️ User profile error: {e}")
        return {}


# ════════════════════════════════════════════════════════════════
# 3. جلب البيانات من DB مع دعم GPS الحقيقي
# ════════════════════════════════════════════════════════════════
def fetch_db_context(
    intent: str,
    entity: str | None,
    user_id: int,
    user_city: str | None = None,
    user_lat: float | None = None,
    user_lon: float | None = None,
) -> list:
    """
    يجلب البيانات من Supabase حسب النية.
    - إذا توفرت GPS → يرتّب بالمسافة الفعلية ويعيد أقرب 5
    - إذا لم تتوفر GPS → يفلتر بالمدينة أو يعيد الأعلى تقييماً
    """
    has_gps = (user_lat is not None and user_lon is not None)

    try:
        # ────────────────────────────────────────────
        if intent == "MyTickets":
            r = supabase.table("tickets") \
                .select("ticket_id,ticket_state,seat_gate,seat_block,seat_row,seat_number,events(event_name,city,venue_name,start_datetime,end_datetime,event_status)") \
                .eq("user_id", user_id) \
                .execute()
            return r.data or []

        # ────────────────────────────────────────────
        elif intent == "MatchSchedule":
            if entity:
                r = supabase.table("events") \
                    .select("event_id,event_name,city,start_datetime,end_datetime,venue_name,event_status") \
                    .ilike("event_name", f"%{entity}%") \
                    .order("start_datetime").limit(10).execute()
            else:
                r = supabase.table("events") \
                    .select("event_id,event_name,city,start_datetime,end_datetime,venue_name,event_status") \
                    .order("start_datetime").limit(10).execute()
            return r.data or []

        # ────────────────────────────────────────────
        elif intent == "StadiumInfo":
            r = supabase.table("events") \
                .select("venue_name,city,latitude,longitude") \
                .execute()
            seen, unique = set(), []
            for row in (r.data or []):
                if row.get("venue_name") not in seen:
                    seen.add(row["venue_name"])
                    # أضف المسافة إذا GPS متوفر
                    if has_gps and row.get("latitude") and row.get("longitude"):
                        row["distance_km"] = round(haversine_km(
                            user_lat, user_lon,
                            float(row["latitude"]), float(row["longitude"])
                        ), 1)
                    unique.append(row)
            if has_gps:
                unique.sort(key=lambda x: x.get("distance_km", 9999))
            return unique[:5]

        # ────────────────────────────────────────────
        elif intent == "Hotels":
            return _fetch_services_sorted(
                category="Hotel",
                select_cols="service_id,service_name,description,location,city,latitude,longitude,rating,price_range,contact_info,opening_hours",
                user_city=user_city,
                user_lat=user_lat,
                user_lon=user_lon,
                limit=5,
            )

        # ────────────────────────────────────────────
        elif intent == "Restaurants":
            return _fetch_services_sorted(
                category="Restaurant",
                select_cols="service_id,service_name,description,location,city,latitude,longitude,rating,price_range,contact_info,opening_hours,halal_certified",
                user_city=user_city,
                user_lat=user_lat,
                user_lon=user_lon,
                limit=5,
            )

        # ────────────────────────────────────────────
        elif intent == "FanZone":
            return _fetch_services_sorted(
                category="Event",
                select_cols="service_id,service_name,description,location,city,latitude,longitude,opening_hours,contact_info",
                user_city=user_city,
                user_lat=user_lat,
                user_lon=user_lon,
                limit=5,
            )

        # ────────────────────────────────────────────
        elif intent == "Emergency":
            return [{
                "info": "Emergency Numbers in Saudi Arabia",
                "emergency": "911",
                "police": "999",
                "ambulance": "997",
                "civil_defense": "998",
                "tourist_police": "920000814"
            }]

        # ────────────────────────────────────────────
        elif intent == "UserProfile":
            r = supabase.table("users") \
                .select("name,city,nationality,gender,birthDate,email,phone") \
                .eq("user_id", user_id).maybeSingle().execute()
            return [r.data] if r.data else []

    except Exception as e:
        print(f"⚠️ DB error (intent={intent}): {e}")
    return []


def _fetch_services_sorted(
    category: str,
    select_cols: str,
    user_city: str | None,
    user_lat: float | None,
    user_lon: float | None,
    limit: int = 5,
) -> list:
    """
    يجلب الخدمات ويرتّبها:
    1. إذا GPS متوفر → يجلب الكل ويحسب المسافة ويعيد أقرب 5
    2. إذا مدينة فقط → يفلتر بالمدينة
    3. إذا لا شيء → يعيد الأعلى تقييماً
    """
    has_gps = (user_lat is not None and user_lon is not None)

    try:
        if has_gps:
            # جلب كل السجلات مع إحداثياتها لحساب المسافة
            r = supabase.table("services") \
                .select(select_cols) \
                .eq("service_category", category) \
                .execute()
            results = r.data or []

            # احسب المسافة لكل سجل
            for row in results:
                lat = row.get("latitude")
                lon = row.get("longitude")
                if lat and lon:
                    try:
                        row["distance_km"] = round(haversine_km(
                            user_lat, user_lon,
                            float(lat), float(lon)
                        ), 1)
                    except:
                        row["distance_km"] = 9999
                else:
                    row["distance_km"] = 9999

            # رتّب بالمسافة وعيد أقرب 5
            results.sort(key=lambda x: x.get("distance_km", 9999))
            return results[:limit]

        elif user_city:
            # بدون GPS — فلتر بالمدينة
            r = supabase.table("services") \
                .select(select_cols) \
                .eq("service_category", category) \
                .ilike("city", f"%{user_city}%") \
                .order("rating", desc=True).limit(limit).execute()
            results = r.data or []
            if results:
                return results

        # fallback: الأعلى تقييماً بغض النظر عن المدينة
        r = supabase.table("services") \
            .select(select_cols) \
            .eq("service_category", category) \
            .order("rating", desc=True).limit(limit).execute()
        return r.data or []

    except Exception as e:
        print(f"⚠️ _fetch_services_sorted error: {e}")
        return []


# ════════════════════════════════════════════════════════════════
# 4. RAG — بحث في PDF (يُستخدم كـ fallback فقط)
# ════════════════════════════════════════════════════════════════
def search_documents(query: str, top_k: int = 3) -> list[dict]:
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
            "match_threshold": 0.55
        }).execute()
        return r.data or []
    except Exception as e:
        print(f"⚠️ Vector search: {e}")
        return []


# ════════════════════════════════════════════════════════════════
# 5. بناء الـ Prompt الذكي
# ════════════════════════════════════════════════════════════════
def build_prompt(
    message: str,
    language: str,
    db_results: list,
    pdf_results: list,
    user_profile: dict,
    has_gps: bool,
    intent: str,
) -> str:

    lang_rule = f"MANDATORY: Respond in {language} ONLY."
    parts = []

    # ── معلومات المستخدم ──
    if user_profile:
        name = user_profile.get('name', '')
        city = user_profile.get('city', '')
        nationality = user_profile.get('nationality', '')
        user_info = f"👤 User: Name={name}"
        if city: user_info += f", City={city}"
        if nationality: user_info += f", Nationality={nationality}"
        if has_gps: user_info += " | GPS Location: ✅ Available (distances calculated)"
        else: user_info += " | GPS Location: ❌ Not available (using city)"
        parts.append(user_info)

    # ── بيانات DB (الأولوية) ──
    if db_results:
        db_label = "🗄️ DATABASE RESULTS (PRIMARY SOURCE — use these first):"
        parts.append(db_label + "\n" + json.dumps(db_results, ensure_ascii=False, indent=2))

    # ── PDF كـ fallback ──
    if pdf_results and not db_results:
        parts.append("📄 Official Documents (fallback — DB had no results):\n" +
                     "\n\n".join(f"[{r.get('source','doc')}]\n{r['content']}" for r in pdf_results))
    elif pdf_results:
        # إضافة PDF كمعلومات تكميلية فقط إذا كانت DB موجودة
        parts.append("📄 Additional Context:\n" +
                     "\n\n".join(f"[{r.get('source','doc')}]\n{r['content']}" for r in pdf_results[:1]))

    ctx = "\n\n---\n\n".join(parts)

    # ── تعليمات خاصة بكل intent ──
    intent_instructions = {
        "Hotels": (
            "Show hotels as a numbered list. For each: **Name** | ⭐ rating | 📍 city | "
            "distance_km if available | price_range. Show top 5 only."
        ),
        "Restaurants": (
            "Show restaurants as a numbered list. For each: **Name** | ⭐ rating | 📍 city | "
            "distance_km if available | halal status. Show top 5 only."
        ),
        "MyTickets": (
            "Show each ticket with: Event name, Date/Time, Venue, City, Seat (Gate/Block/Row/Number), Status. "
            "If no tickets found in DB, say so clearly."
        ),
        "StadiumInfo": (
            "Show stadiums with their city and distance if available. "
            "Mention which stadiums host which type of events."
        ),
        "MatchSchedule": (
            "Show matches sorted by date. Include: Match name, Date, Time, Venue, City, Status."
        ),
    }
    extra = intent_instructions.get(intent, "")

    return (
        f"{lang_rule}\n\n"
        f"Context:\n{ctx}\n\n"
        f"---\nUser message: {message}\n\n"
        f"{extra}\n\n"
        f"Important: Address the user by their first name. "
        f"{'Mention distances in km for nearby results.' if has_gps else 'GPS not available, use city-based filtering.'} "
        f"Use DB results as the primary source. Fall back to documents only if DB is empty."
    )


# ════════════════════════════════════════════════════════════════
# 6. Endpoints
# ════════════════════════════════════════════════════════════════
@app.get("/")
def root():
    return {"status": "✅ YAHALA RAG API v8.0", "features": ["GPS-sorting", "DB-first", "Personalized"]}


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

    def stream():
        try:
            # 1. بيانات المستخدم
            user_profile = fetch_user_profile(uid)
            user_city = user_profile.get("city")

            # 2. استخدم GPS من Flutter إذا أُرسل، وإلا استخدم ما في DB
            lat = user_lat
            lon = user_lon
            if lat is None and user_profile.get("latitude"):
                try:
                    lat = float(user_profile["latitude"])
                    lon = float(user_profile["longitude"])
                    print(f"📍 Using GPS from DB: {lat}, {lon}")
                except:
                    pass
            elif lat is not None:
                print(f"📍 Using GPS from Flutter: {lat}, {lon}")

            has_gps = (lat is not None and lon is not None)

            # 3. تحليل النية
            analysis = analyze_message(user_message)
            intent    = analysis.get("intent", "General")
            entity    = analysis.get("entity")
            language  = analysis.get("language", "English")

            # 4. جلب DB أولاً
            db = fetch_db_context(intent, entity, uid, user_city, lat, lon)
            print(f"📊 DB results: {len(db)} | intent={intent} | GPS={has_gps} | city={user_city}")

            # 5. PDF كـ fallback (فقط للـ General أو إذا DB فارغة)
            pdf = []
            if intent == "General" or not db:
                pdf = search_documents(user_message)
                print(f"📄 PDF results: {len(pdf)}")

            # 6. بناء الـ prompt
            prompt = build_prompt(
                message=user_message,
                language=language,
                db_results=db,
                pdf_results=pdf,
                user_profile=user_profile,
                has_gps=has_gps,
                intent=intent,
            )

            # 7. توليد الرد
            for chunk in client.models.generate_content_stream(
                model=CHAT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.7,
                    max_output_tokens=1000,
                    safety_settings=SAFETY,
                )
            ):
                try:
                    if chunk.text:
                        yield chunk.text
                except Exception:
                    continue

        except Exception as e:
            print(f"❌ Stream error: {e}")
            yield f"\n\n❌ Error: {e}"

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


@app.post("/chat")
def chat(
    user_message: str = Query(...),
    user_id: str = Query(...),
    user_lat: float | None = Query(default=None),
    user_lon: float | None = Query(default=None),
):
    """Non-streaming version"""
    if not user_message.strip():
        raise HTTPException(400, "Message is empty")
    try:
        uid = int(user_id)
        user_profile = fetch_user_profile(uid)
        user_city = user_profile.get("city")

        lat = user_lat or (float(user_profile["latitude"]) if user_profile.get("latitude") else None)
        lon = user_lon or (float(user_profile["longitude"]) if user_profile.get("longitude") else None)
        has_gps = lat is not None and lon is not None

        analysis = analyze_message(user_message)
        intent   = analysis.get("intent", "General")
        entity   = analysis.get("entity")
        language = analysis.get("language", "English")

        db  = fetch_db_context(intent, entity, uid, user_city, lat, lon)
        pdf = search_documents(user_message) if (intent == "General" or not db) else []

        prompt = build_prompt(user_message, language, db, pdf, user_profile, has_gps, intent)
        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=1000,
                safety_settings=SAFETY,
            )
        )
        return {"reply": response.text, "language": language, "intent": intent, "db_count": len(db)}

    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(500, str(e))


@app.get("/user/greeting")
def get_user_greeting(user_id: int = Query(...)):
    profile = fetch_user_profile(user_id)
    return {"name": profile.get("name", ""), "city": profile.get("city", "")}


@app.get("/health")
def health():
    checks = {}
    try:
        r = supabase.table("events").select("count", count="exact").execute()
        checks["events"] = f"✅ {r.count} records"
    except Exception as e:
        checks["events"] = f"❌ {e}"
    try:
        r = supabase.table("services").select("count", count="exact").execute()
        checks["services"] = f"✅ {r.count} records"
    except Exception as e:
        checks["services"] = f"❌ {e}"
    try:
        r = supabase.table("tickets").select("count", count="exact").execute()
        checks["tickets"] = f"✅ {r.count} records"
    except Exception as e:
        checks["tickets"] = f"❌ {e}"
    try:
        client.models.generate_content(
            model=CHAT_MODEL,
            contents="ping",
            config=types.GenerateContentConfig(max_output_tokens=5)
        )
        checks["gemini"] = "✅ google.genai v8"
    except Exception as e:
        checks["gemini"] = f"❌ {e}"
    return {"status": "running", "version": "8.0", "checks": checks}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)