"""
YAHALA Assistant v9.0 — DB-First, Smart, Personalized
- يجلب بيانات المستخدم أولاً بالـ user_id ثم يجيب على أي سؤال
- DB أولوية قصوى — PDF فقط إذا DB فارغة تماماً
- يخاطب المستخدم باسمه في كل رد
- ترتيب حسب GPS الحقيقي
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

1. LANGUAGE: Always respond in EXACTLY the same language the user wrote in.
   Arabic → Arabic only. English → English only. Mixed → use dominant language.

2. PERSONALIZATION — MANDATORY:
   - You MUST address the user by their first name in EVERY single response.
   - Extract first name from the full name provided.
   - Arabic greeting examples: "أهلاً [Name]،" / "بالتأكيد [Name]،" / "حسناً [Name]،"
   - English greeting examples: "Hi [Name]!" / "Sure [Name]!" / "Of course, [Name]!"
   - NEVER start a response without the user's name.

3. DB-FIRST — STRICT PRIORITY:
   - DATABASE results are ALWAYS the primary source. Present them first.
   - PDF documents are ONLY a fallback when DB returns zero results.
   - NEVER ignore DB results in favor of PDF content.
   - If DB has data, base your answer ENTIRELY on that DB data.

4. TICKETS — CRITICAL:
   - Show ONLY the tickets belonging to the logged-in user (from DB).
   - Include: Event name, Date, Time, Venue, City, Seat (Gate/Block/Row/Number), Status.
   - If ticket status is "valid" → show as ✅ Valid.
   - If ticket status is "expired" → show as ❌ Expired.
   - NEVER show sample or fictional tickets.

5. LOCATION & DISTANCE:
   - When GPS is available: always mention distance in km for nearby results.
   - Show top 5 closest options sorted by distance.
   - When no GPS: filter by user's city, sort by rating.

6. FORMAT:
   - Use Markdown: **bold** for names, bullet points for lists.
   - Be warm, helpful, and concise.
   - For lists (hotels, restaurants, events): use numbered list format.

7. HONESTY:
   - If DB is empty AND PDF has no relevant info → say clearly you don't have that info yet.
   - Never fabricate data."""

# ════════════════════════════════════════════════════════════════
# INTENT CLASSIFIER
# ════════════════════════════════════════════════════════════════
DETECT_PROMPT = """You are an intent classifier. Return ONLY a JSON object, no markdown, no explanation.

INTENTS:
- MyTickets: user asking about their own tickets/bookings (my tickets, تذاكري, تذكرتي, حجوزاتي)
- MatchSchedule: match schedule, upcoming games, fixtures (مباراة, جدول, متى تلعب, المباريات)
- Hotels: hotels, accommodation, where to stay (فنادق, إقامة, أين أنام, فندق قريب)
- Restaurants: food, restaurants, dining, eat (مطاعم, أكل, طعام, مطعم قريب)
- StadiumInfo: stadiums, venues, arena locations (ملاعب, ملعب, أين الملعب)
- FanZone: fan zones, events, entertainment, activities (فان زون, فعاليات, ترفيه)
- Emergency: emergency services, police, hospital (طوارئ, إسعاف, شرطة, مستشفى)
- UserProfile: user's own profile/info (بياناتي, معلوماتي, ملفي, من أنا)
- General: anything about World Cup 2034, Saudi Arabia, FIFA rules, general questions

Message: "{message}"

Return JSON:
{{"language": "English", "language_code": "en", "intent": "General", "entity": null}}

Rules:
- entity = team name if mentioned (e.g. "Saudi Arabia", "Brazil", "السعودية"), else null
- For questions about the user's own tickets/reservations → ALWAYS use "MyTickets"
- Return ONLY the JSON object, absolutely nothing else

Examples:
- "My tickets" → {{"language": "English", "language_code": "en", "intent": "MyTickets", "entity": null}}
- "تذاكري" → {{"language": "Arabic", "language_code": "ar", "intent": "MyTickets", "entity": null}}
- "حجوزاتي" → {{"language": "Arabic", "language_code": "ar", "intent": "MyTickets", "entity": null}}
- "اقرب فندق" → {{"language": "Arabic", "language_code": "ar", "intent": "Hotels", "entity": null}}
- "Nearby restaurants" → {{"language": "English", "language_code": "en", "intent": "Restaurants", "entity": null}}
- "متى مباراة السعودية" → {{"language": "Arabic", "language_code": "ar", "intent": "MatchSchedule", "entity": "Saudi Arabia"}}
- "ما هي أقرب مباراة" → {{"language": "Arabic", "language_code": "ar", "intent": "MatchSchedule", "entity": null}}
- "الملاعب" → {{"language": "Arabic", "language_code": "ar", "intent": "StadiumInfo", "entity": null}}
- "فعاليات وترفيه" → {{"language": "Arabic", "language_code": "ar", "intent": "FanZone", "entity": null}}
- "من أنا" → {{"language": "Arabic", "language_code": "ar", "intent": "UserProfile", "entity": null}}
- "كيف احصل على تذكرة" → {{"language": "Arabic", "language_code": "ar", "intent": "General", "entity": null}}"""


# ════════════════════════════════════════════════════════════════
# HELPER: Haversine Distance
# ════════════════════════════════════════════════════════════════
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


app = FastAPI(title="YAHALA RAG API v9.0")


# ════════════════════════════════════════════════════════════════
# 1. Intent Analysis
# ════════════════════════════════════════════════════════════════
def analyze_message(message: str) -> dict:
    try:
        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=DETECT_PROMPT.format(message=message),
            config=INTENT_CONFIG,
        )
        text = response.text.strip()
        # تنظيف أي markdown
        text = text.replace("```json", "").replace("```", "").strip()
        # أحياناً يضيف النموذج نصاً قبل JSON — نجد أول {
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]
        result = json.loads(text)
        print(f"✅ Intent: {result}")
        return result
    except Exception as e:
        print(f"⚠️ Intent error: {e}")
        return {"language": "Arabic", "language_code": "ar", "intent": "General", "entity": None}


# ════════════════════════════════════════════════════════════════
# 2. Fetch User Profile — always first
# ════════════════════════════════════════════════════════════════
def fetch_user_profile(user_id: int) -> dict:
    """
    يجلب بيانات المستخدم من DB. هذا أول شيء يحدث قبل أي شيء آخر.
    يعيد dict فارغ إذا لم يُوجد المستخدم.
    """
    try:
        r = supabase.table("users") \
            .select("user_id,name,city,nationality,gender,birthDate,email,phone,latitude,longitude") \
            .eq("user_id", user_id) \
            .maybeSingle() \
            .execute()
        data = r.data or {}
        if data:
            print(f"✅ User loaded: {data.get('name','?')} | city={data.get('city','?')} | lat={data.get('latitude')} | lon={data.get('longitude')}")
        else:
            print(f"⚠️ No user found for user_id={user_id}")
        return data
    except Exception as e:
        print(f"❌ User profile fetch error: {e}")
        return {}


# ════════════════════════════════════════════════════════════════
# 3. Fetch DB Context — DB First always
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
    - MyTickets: يستخدم user_id بشكل صريح لضمان تذاكر المستخدم الصحيح
    - Hotels/Restaurants/FanZone: يرتّب بالمسافة إذا GPS متوفر
    - MatchSchedule: يرتّب بالتاريخ
    """
    has_gps = (user_lat is not None and user_lon is not None)

    try:
        # ── تذاكر المستخدم ──────────────────────────────────────
        if intent == "MyTickets":
            print(f"🎫 Fetching tickets for user_id={user_id}")
            r = supabase.table("tickets") \
                .select(
                    "ticket_id,"
                    "ticket_state,"
                    "seat_gate,"
                    "seat_block,"
                    "seat_row,"
                    "seat_number,"
                    "events(event_name,city,venue_name,start_datetime,end_datetime,event_status)"
                ) \
                .eq("user_id", user_id) \
                .execute()
            tickets = r.data or []
            print(f"✅ Found {len(tickets)} tickets for user_id={user_id}")
            return tickets

        # ── جدول المباريات ──────────────────────────────────────
        elif intent == "MatchSchedule":
            if entity:
                r = supabase.table("events") \
                    .select("event_id,event_name,city,start_datetime,end_datetime,venue_name,event_status,latitude,longitude") \
                    .ilike("event_name", f"%{entity}%") \
                    .order("start_datetime").limit(10).execute()
            else:
                r = supabase.table("events") \
                    .select("event_id,event_name,city,start_datetime,end_datetime,venue_name,event_status,latitude,longitude") \
                    .order("start_datetime").limit(10).execute()
            results = r.data or []
            # أضف المسافة إذا GPS متوفر
            if has_gps:
                for row in results:
                    if row.get("latitude") and row.get("longitude"):
                        try:
                            row["distance_km"] = round(haversine_km(
                                user_lat, user_lon,
                                float(row["latitude"]), float(row["longitude"])
                            ), 1)
                        except:
                            pass
            return results

        # ── معلومات الملاعب ─────────────────────────────────────
        elif intent == "StadiumInfo":
            r = supabase.table("events") \
                .select("venue_name,city,latitude,longitude") \
                .execute()
            seen, unique = set(), []
            for row in (r.data or []):
                vname = row.get("venue_name")
                if vname and vname not in seen:
                    seen.add(vname)
                    if has_gps and row.get("latitude") and row.get("longitude"):
                        try:
                            row["distance_km"] = round(haversine_km(
                                user_lat, user_lon,
                                float(row["latitude"]), float(row["longitude"])
                            ), 1)
                        except:
                            pass
                    unique.append(row)
            if has_gps:
                unique.sort(key=lambda x: x.get("distance_km", 9999))
            return unique[:8]

        # ── الفنادق ─────────────────────────────────────────────
        elif intent == "Hotels":
            return _fetch_services_sorted(
                category="Hotel",
                select_cols="service_id,service_name,description,location,city,latitude,longitude,rating,price_range,contact_info,opening_hours",
                user_city=user_city,
                user_lat=user_lat,
                user_lon=user_lon,
                limit=5,
            )

        # ── المطاعم ─────────────────────────────────────────────
        elif intent == "Restaurants":
            return _fetch_services_sorted(
                category="Restaurant",
                select_cols="service_id,service_name,description,location,city,latitude,longitude,rating,price_range,contact_info,opening_hours,halal_certified",
                user_city=user_city,
                user_lat=user_lat,
                user_lon=user_lon,
                limit=5,
            )

        # ── الفعاليات والترفيه ──────────────────────────────────
        elif intent == "FanZone":
            return _fetch_services_sorted(
                category="Event",
                select_cols="service_id,service_name,description,location,city,latitude,longitude,opening_hours,contact_info",
                user_city=user_city,
                user_lat=user_lat,
                user_lon=user_lon,
                limit=5,
            )

        # ── الطوارئ ─────────────────────────────────────────────
        elif intent == "Emergency":
            return [{
                "info": "Emergency Numbers in Saudi Arabia",
                "emergency": "911",
                "police": "999",
                "ambulance": "997",
                "civil_defense": "998",
                "tourist_police": "920000814"
            }]

        # ── ملف المستخدم ────────────────────────────────────────
        elif intent == "UserProfile":
            r = supabase.table("users") \
                .select("name,city,nationality,gender,birthDate,email,phone") \
                .eq("user_id", user_id).maybeSingle().execute()
            return [r.data] if r.data else []

    except Exception as e:
        print(f"❌ DB error (intent={intent}): {e}")

    return []


# ════════════════════════════════════════════════════════════════
# Helper: Fetch & Sort Services
# ════════════════════════════════════════════════════════════════
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
    1. GPS متوفر → جميع السجلات، احسب المسافة، أقرب 5
    2. مدينة فقط → فلتر بالمدينة، الأعلى تقييماً
    3. لا شيء → الأعلى تقييماً عالمياً
    """
    has_gps = (user_lat is not None and user_lon is not None)

    try:
        if has_gps:
            r = supabase.table("services") \
                .select(select_cols) \
                .eq("service_category", category) \
                .execute()
            results = r.data or []

            for row in results:
                lat = row.get("latitude")
                lon = row.get("longitude")
                if lat and lon:
                    try:
                        row["distance_km"] = round(haversine_km(
                            user_lat, user_lon, float(lat), float(lon)
                        ), 1)
                    except:
                        row["distance_km"] = 9999
                else:
                    row["distance_km"] = 9999

            results.sort(key=lambda x: x.get("distance_km", 9999))
            return results[:limit]

        elif user_city:
            r = supabase.table("services") \
                .select(select_cols) \
                .eq("service_category", category) \
                .ilike("city", f"%{user_city}%") \
                .order("rating", desc=True).limit(limit).execute()
            results = r.data or []
            if results:
                return results

        # fallback: أعلى تقييم
        r = supabase.table("services") \
            .select(select_cols) \
            .eq("service_category", category) \
            .order("rating", desc=True).limit(limit).execute()
        return r.data or []

    except Exception as e:
        print(f"❌ _fetch_services_sorted error ({category}): {e}")
        return []


# ════════════════════════════════════════════════════════════════
# 4. RAG — PDF Search (Fallback ONLY)
# ════════════════════════════════════════════════════════════════
def search_documents(query: str, top_k: int = 3) -> list[dict]:
    """يُستخدم فقط إذا DB فارغة أو intent=General"""
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
        print(f"⚠️ Vector search error: {e}")
        return []


# ════════════════════════════════════════════════════════════════
# 5. Build Smart Prompt — DB data is explicit & prominent
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

    parts = []

    # ── معلومات المستخدم (مطلوبة دائماً) ──
    user_name = user_profile.get("name", "")
    first_name = user_name.split()[0] if user_name else ""
    city = user_profile.get("city", "")
    nationality = user_profile.get("nationality", "")
    user_id = user_profile.get("user_id", "")

    user_section = f"""=== LOGGED-IN USER ===
user_id: {user_id}
Full Name: {user_name}
First Name (use this to address): {first_name}
City: {city}
Nationality: {nationality}
GPS Available: {"Yes" if has_gps else "No"}
Language: {language}
====================="""
    parts.append(user_section)

    # ── بيانات DB ──
    if db_results:
        db_section = f"""=== DATABASE RESULTS (PRIMARY — USE THESE FIRST) ===
Intent: {intent}
Results count: {len(db_results)}
Data:
{json.dumps(db_results, ensure_ascii=False, indent=2)}
======================================================="""
        parts.append(db_section)
    else:
        parts.append("=== DATABASE: No results found for this query ===")

    # ── PDF كـ fallback فقط ──
    if pdf_results and not db_results:
        pdf_section = "=== FALLBACK: Official Documents (DB was empty) ===\n"
        pdf_section += "\n\n".join(
            f"[Source: {r.get('source', 'document')}]\n{r.get('content', '')}"
            for r in pdf_results
        )
        pdf_section += "\n==================================================="
        parts.append(pdf_section)
    elif pdf_results and db_results:
        # إذا كان DB موجوداً، PDF تكميلي فقط للـ General
        if intent == "General":
            supp = "=== Supplementary Context (Official Docs) ===\n"
            supp += "\n".join(r.get("content", "")[:300] for r in pdf_results[:2])
            parts.append(supp)

    # ── السؤال الأصلي ──
    parts.append(f"=== USER QUESTION ===\n{message}\n====================")

    # ── تعليمات خاصة بالـ intent ──
    intent_instructions = {
        "MyTickets": f"""
TICKET INSTRUCTIONS:
- Show ONLY tickets from the DATABASE RESULTS above — these belong to user_id={user_id}.
- Do NOT mention any other tickets.
- Format each ticket:
  🎟️ **[Event Name]**
  📅 Date: [start_datetime]
  🏟️ Venue: [venue_name], [city]
  💺 Seat: Gate [seat_gate] | Block [seat_block] | Row [seat_row] | Seat [seat_number]
  Status: {"✅ Valid" if True else "❌ Expired"} (use actual ticket_state from DB)
- If no tickets in DB → say clearly "{first_name}، لا يوجد لديك تذاكر حالياً" or "You don't have any tickets yet, {first_name}."
""",
        "Hotels": f"""
HOTEL INSTRUCTIONS:
- Show top 5 from DATABASE RESULTS.
- Format: **[Name]** | ⭐ [rating] | 📍 [city] {"| 🚗 [distance_km] km away" if has_gps else ""} | 💰 [price_range]
- Sort by distance_km if GPS available, else by rating.
""",
        "Restaurants": f"""
RESTAURANT INSTRUCTIONS:
- Show top 5 from DATABASE RESULTS.
- Format: **[Name]** | ⭐ [rating] | 📍 [city] {"| 🚗 [distance_km] km away" if has_gps else ""} | 🍽️ [halal status]
- Sort by distance_km if GPS available, else by rating.
""",
        "MatchSchedule": """
MATCH SCHEDULE INSTRUCTIONS:
- Show matches sorted by start_datetime (earliest first).
- Format each match:
  ⚽ **[event_name]**
  📅 [start_datetime]
  🏟️ [venue_name], [city]
  Status: [event_status]
""",
        "FanZone": """
FAN ZONE INSTRUCTIONS:
- Show nearby events/fan zones from DATABASE RESULTS.
- Include name, location, opening hours, contact info.
""",
        "StadiumInfo": """
STADIUM INSTRUCTIONS:
- Show stadium names and cities from DATABASE RESULTS.
- Include distance if GPS available.
""",
    }

    instruction = intent_instructions.get(intent, "Answer based on DATABASE RESULTS first. Only use documents if DB is empty.")
    parts.append(f"=== INSTRUCTIONS ===\n{instruction}")
    parts.append(f"""
FINAL REMINDERS:
1. Respond in {language} ONLY.
2. Start your response by addressing the user as "{first_name}" — MANDATORY.
3. DB data = truth. PDF = fallback only.
4. {"Mention distances in km." if has_gps else "GPS not available, use city info."}
""")

    return "\n\n".join(parts)


# ════════════════════════════════════════════════════════════════
# 6. Core Logic — shared between stream and non-stream
# ════════════════════════════════════════════════════════════════
def process_request(
    user_message: str,
    uid: int,
    user_lat: float | None,
    user_lon: float | None,
) -> tuple[str, str, str, list, list, dict, bool]:
    """
    Returns: (prompt, language, intent, db_results, pdf_results, user_profile, has_gps)
    """
    # 1. جلب بيانات المستخدم أولاً (دائماً)
    user_profile = fetch_user_profile(uid)
    user_city = user_profile.get("city")

    # 2. GPS — من Flutter أو من DB
    lat = user_lat
    lon = user_lon
    if lat is None and user_profile.get("latitude"):
        try:
            lat = float(user_profile["latitude"])
            lon = float(user_profile["longitude"])
            print(f"📍 GPS from DB: {lat}, {lon}")
        except:
            pass
    elif lat is not None:
        print(f"📍 GPS from client: {lat}, {lon}")

    has_gps = (lat is not None and lon is not None)

    # 3. تحليل النية
    analysis = analyze_message(user_message)
    intent   = analysis.get("intent", "General")
    entity   = analysis.get("entity")
    language = analysis.get("language", "Arabic")

    # 4. جلب DB أولاً
    db = fetch_db_context(intent, entity, uid, user_city, lat, lon)
    print(f"📊 DB results: {len(db)} | intent={intent} | GPS={has_gps}")

    # 5. PDF — فقط إذا General أو DB فارغة
    pdf = []
    if intent == "General" or not db:
        pdf = search_documents(user_message)
        print(f"📄 PDF results: {len(pdf)}")

    # 6. بناء prompt
    prompt = build_prompt(
        message=user_message,
        language=language,
        db_results=db,
        pdf_results=pdf,
        user_profile=user_profile,
        has_gps=has_gps,
        intent=intent,
    )

    return prompt, language, intent, db, pdf, user_profile, has_gps


# ════════════════════════════════════════════════════════════════
# 7. Endpoints
# ════════════════════════════════════════════════════════════════
@app.get("/")
def root():
    return {
        "status": "✅ YAHALA RAG API v9.0",
        "features": ["DB-First", "GPS-sorting", "Personalized", "User-ID-aware"]
    }


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
        raise HTTPException(400, "user_id must be a valid integer")

    def stream():
        try:
            prompt, language, intent, db, pdf, user_profile, has_gps = process_request(
                user_message, uid, user_lat, user_lon
            )

            for chunk in client.models.generate_content_stream(
                model=CHAT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.6,
                    max_output_tokens=1200,
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
            yield f"\n\n❌ حدث خطأ: {e}"

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
    except ValueError:
        raise HTTPException(400, "user_id must be a valid integer")

    try:
        prompt, language, intent, db, pdf, user_profile, has_gps = process_request(
            user_message, uid, user_lat, user_lon
        )

        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.6,
                max_output_tokens=1200,
                safety_settings=SAFETY,
            )
        )

        return {
            "reply": response.text,
            "language": language,
            "intent": intent,
            "db_count": len(db),
            "pdf_count": len(pdf),
            "user": user_profile.get("name", ""),
            "gps": has_gps,
        }

    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(500, str(e))


@app.get("/user/greeting")
def get_user_greeting(user_id: int = Query(...)):
    """يُستخدم من Flutter لجلب اسم المستخدم عند فتح الشات"""
    profile = fetch_user_profile(user_id)
    name = profile.get("name", "")
    first_name = name.split()[0] if name else ""
    return {
        "name": name,
        "first_name": first_name,
        "city": profile.get("city", ""),
        "nationality": profile.get("nationality", ""),
    }


@app.get("/health")
def health():
    checks = {}
    tables = ["events", "services", "tickets", "users"]
    for table in tables:
        try:
            r = supabase.table(table).select("count", count="exact").execute()
            checks[table] = f"✅ {r.count} records"
        except Exception as e:
            checks[table] = f"❌ {e}"
    try:
        client.models.generate_content(
            model=CHAT_MODEL,
            contents="ping",
            config=types.GenerateContentConfig(max_output_tokens=5)
        )
        checks["gemini"] = f"✅ {CHAT_MODEL}"
    except Exception as e:
        checks["gemini"] = f"❌ {e}"
    return {
        "status": "running",
        "version": "9.0",
        "checks": checks
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)