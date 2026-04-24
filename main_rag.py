"""
YAHALA Assistant v7.0 — Personalized + Location-Aware
"""

import json
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
    max_output_tokens=1000,
    safety_settings=SAFETY,
)

SYSTEM_PROMPT = """You are 'YAHALA Assistant', the official smart assistant for the YAHALA app and FIFA World Cup 2034 in Saudi Arabia.

CRITICAL LANGUAGE RULE:
- ALWAYS detect the language of the user's message
- ALWAYS respond in EXACTLY the same language the user wrote in
- Arabic → Arabic | English → English | French → French | Any language → same language

Response rules:
- Be friendly, warm, and professional
- Use Markdown for formatting (lists, bold, headers)
- Use ONLY information from the provided context when available
- If user data is provided, personalize the response (use their name, reference their city/location)
- When showing hotels/restaurants/services, prioritize ones in or near the user's city
- Keep responses concise and useful
- For tickets, show real ticket details from the database

You are an expert on FIFA World Cup 2034 Saudi Arabia: stadiums, hotels, restaurants, match schedules, tickets, and fan zones."""

app = FastAPI(title="YAHALA RAG API v7.0")


# ════════════════════════════════════════════
# 1. كشف اللغة + النية
# ════════════════════════════════════════════
DETECT_PROMPT = """You are an intent classifier. Return ONLY a JSON object, no markdown, no explanation.

INTENT DEFINITIONS:
- MyTickets: asks about tickets, bookings, reservations (my tickets, ticket help, تذاكري, مساعدة في التذاكر)
- MatchSchedule: asks about games, matches, fixtures, teams playing, next game, مباراة, جدول المباريات, متى تلعب
- Hotels: asks about hotels, accommodation, where to stay, lodging, فنادق, إقامة, حجز فندق, أين أنام, الفنادق القريبة
- Restaurants: asks about food, restaurants, dining, eat, halal food, مطاعم, أكل, طعام, حلال
- StadiumInfo: asks about stadiums, venues, where is the stadium, ملاعب, مواقع الملاعب, أين الملعب
- FanZone: asks about fan zones, events, activities, entertainment, فان زون, فعاليات, ترفيه
- Emergency: asks about emergency, police, hospital, ambulance, طوارئ, إسعاف, شرطة
- UserProfile: asks about my profile, my info, my data, بياناتي, معلوماتي, ملفي
- General: anything else about World Cup 2034

Message: "{message}"

JSON format: {{"language": "English", "language_code": "en", "intent": "General", "entity": null}}

Rules:
- entity = team name if mentioned (e.g. "Saudi Arabia", "Brazil", "السعودية", "البرازيل"), else null
- language and language_code must match the message language exactly
- Return ONLY the JSON object, nothing else

Few-shot examples:
- "Nearby hotels" -> {{"language": "English", "language_code": "en", "intent": "Hotels", "entity": null}}
- "next game for Saudi team" -> {{"language": "English", "language_code": "en", "intent": "MatchSchedule", "entity": "Saudi Arabia"}}
- "Stadium locations" -> {{"language": "English", "language_code": "en", "intent": "StadiumInfo", "entity": null}}
- "Ticket help" -> {{"language": "English", "language_code": "en", "intent": "MyTickets", "entity": null}}
- "Match schedule" -> {{"language": "English", "language_code": "en", "intent": "MatchSchedule", "entity": null}}
- "Fan zones" -> {{"language": "English", "language_code": "en", "intent": "FanZone", "entity": null}}
- "Emergency services" -> {{"language": "English", "language_code": "en", "intent": "Emergency", "entity": null}}
- "الفنادق القريبة" -> {{"language": "Arabic", "language_code": "ar", "intent": "Hotels", "entity": null}}
- "فنادق" -> {{"language": "Arabic", "language_code": "ar", "intent": "Hotels", "entity": null}}
- "أريد حجز فندق" -> {{"language": "Arabic", "language_code": "ar", "intent": "Hotels", "entity": null}}
- "أين أقرب فندق" -> {{"language": "Arabic", "language_code": "ar", "intent": "Hotels", "entity": null}}
- "متى مباراة السعودية" -> {{"language": "Arabic", "language_code": "ar", "intent": "MatchSchedule", "entity": "Saudi Arabia"}}
- "مواقع الملاعب" -> {{"language": "Arabic", "language_code": "ar", "intent": "StadiumInfo", "entity": null}}
- "تذاكري" -> {{"language": "Arabic", "language_code": "ar", "intent": "MyTickets", "entity": null}}
- "مطاعم قريبة" -> {{"language": "Arabic", "language_code": "ar", "intent": "Restaurants", "entity": null}}
- "فعاليات وترفيه" -> {{"language": "Arabic", "language_code": "ar", "intent": "FanZone", "entity": null}}
- "Où sont les hôtels?" -> {{"language": "French", "language_code": "fr", "intent": "Hotels", "entity": null}}
- "¿Cuándo juega Arabia Saudita?" -> {{"language": "Spanish", "language_code": "es", "intent": "MatchSchedule", "entity": "Saudi Arabia"}}"""


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
        print(f"⚠️ Detect error: {e}")
        return {"language": "English", "language_code": "en", "intent": "General", "entity": None}


# ════════════════════════════════════════════
# 2. جلب بيانات المستخدم (الاسم + المدينة)
# ════════════════════════════════════════════
def fetch_user_profile(user_id: int) -> dict:
    """يجلب اسم المستخدم ومدينته وجنسيته من جدول users"""
    try:
        r = supabase.table("users") \
            .select("user_id,name,city,nationality,gender,id_number,email") \
            .eq("user_id", user_id) \
            .maybeSingle() \
            .execute()
        data = r.data or {}
        print(f"✅ fetch_user_profile({user_id}) → {data.get('name','NOT FOUND')}")
        return data
    except Exception as e:
        print(f"⚠️ User profile error for id={user_id}: {e}")
        return {}


# ════════════════════════════════════════════
# 3. جلب البيانات من Supabase (مع فلترة المدينة)
# ════════════════════════════════════════════
def fetch_db_context(intent: str, entity: str | None, user_id: int, user_city: str | None = None) -> list:
    try:
        if intent == "MyTickets":
            # جلب التذاكر مع تفاصيل الحدث
            r = supabase.table("tickets") \
                .select("ticket_id,ticket_state,seat_gate,seat_block,seat_row,seat_number,events(event_name,city,venue_name,start_datetime,event_status)") \
                .eq("user_id", user_id) \
                .execute()
            return r.data or []

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
            # فلترة بمدينة المستخدم أولاً، وإلا جلب الأفضل تقييماً
            query = supabase.table("services") \
                .select("service_name,description,location,city,rating,price_range,contact_info,opening_hours,languages_supported") \
                .eq("service_category", "Hotel")
            if user_city:
                query = query.ilike("city", f"%{user_city}%")
            r = query.order("rating", desc=True).limit(8).execute()
            results = r.data or []
            # إذا لم تجد في مدينة المستخدم، جلب الكل
            if not results:
                r = supabase.table("services") \
                    .select("service_name,description,location,city,rating,price_range,contact_info,opening_hours,languages_supported") \
                    .eq("service_category", "Hotel") \
                    .order("rating", desc=True).limit(8).execute()
                results = r.data or []
            return results

        elif intent == "Restaurants":
            query = supabase.table("services") \
                .select("service_name,description,location,city,rating,price_range,contact_info,opening_hours,halal_certified") \
                .eq("service_category", "Restaurant")
            if user_city:
                query = query.ilike("city", f"%{user_city}%")
            r = query.order("rating", desc=True).limit(8).execute()
            results = r.data or []
            if not results:
                r = supabase.table("services") \
                    .select("service_name,description,location,city,rating,price_range,contact_info,opening_hours,halal_certified") \
                    .eq("service_category", "Restaurant") \
                    .order("rating", desc=True).limit(8).execute()
                results = r.data or []
            return results

        elif intent == "FanZone":
            query = supabase.table("services") \
                .select("service_name,description,location,city,opening_hours,contact_info,tags") \
                .eq("service_category", "Event")
            if user_city:
                query = query.ilike("city", f"%{user_city}%")
            r = query.order("rating", desc=True).limit(8).execute()
            results = r.data or []
            if not results:
                r = supabase.table("services") \
                    .select("service_name,description,location,city,opening_hours,contact_info,tags") \
                    .eq("service_category", "Event") \
                    .order("rating", desc=True).limit(8).execute()
                results = r.data or []
            return results

        elif intent == "Emergency":
            return [{"info": "Emergency: 911 | Police: 999 | Ambulance: 997 | Civil Defense: 998"}]

        elif intent == "UserProfile":
            r = supabase.table("users") \
                .select("name,city,nationality,gender,birthDate") \
                .eq("user_id", user_id).maybeSingle().execute()
            return [r.data] if r.data else []

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
# 5. بناء الـ Prompt
# ════════════════════════════════════════════
def build_prompt(message: str, language: str, language_code: str,
                 pdf_results: list, db_results: list,
                 user_profile: dict) -> str:

    lang_rule = f"MANDATORY: User wrote in {language}. Respond in {language} ONLY."
    parts = []

    # معلومات المستخدم
    if user_profile:
        user_ctx = f"👤 User Info: Name={user_profile.get('name','')}, City={user_profile.get('city','')}, Nationality={user_profile.get('nationality','')}"
        parts.append(user_ctx)

    if pdf_results:
        parts.append("📄 Official Documents:\n" + "\n\n".join(
            f"[{r['source']}]\n{r['content']}" for r in pdf_results))

    if db_results:
        parts.append("🗄️ Database:\n" + json.dumps(db_results, ensure_ascii=False, indent=2))

    if parts:
        ctx = "\n\n---\n\n".join(parts)
        return (
            f"{lang_rule}\n\n"
            f"Context:\n{ctx}\n\n"
            f"---\nUser: {message}\n\n"
            f"Answer in {language} using the context above. "
            f"If user city is available and results show nearby options, mention they are near the user's location."
        )
    else:
        return (
            f"{lang_rule}\n\n"
            f"User: {message}\n\n"
            f"Answer in {language} about FIFA World Cup 2034 Saudi Arabia."
        )


# ════════════════════════════════════════════
# 6. Endpoint: معلومات الترحيب (الاسم)
# ════════════════════════════════════════════
@app.get("/user/greeting")
def get_user_greeting(user_id: int = Query(...)):
    """يرجع اسم المستخدم للرسالة الترحيبية"""
    try:
        profile = fetch_user_profile(user_id)
        return {
            "name": profile.get("name", ""),
            "city": profile.get("city", ""),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ════════════════════════════════════════════
# 7. Endpoints الرئيسية
# ════════════════════════════════════════════
@app.get("/")
def root():
    return {"status": "✅ YAHALA RAG API v7.0", "sdk": "google.genai"}


@app.post("/chat")
def chat(user_message: str = Query(...), user_id: str = Query(...)):
    if not user_message.strip():
        raise HTTPException(400, "Message is empty")
    try:
        uid = int(user_id)
        user_profile = fetch_user_profile(uid)
        user_city = user_profile.get("city")

        analysis = analyze_message(user_message)
        intent   = analysis.get("intent", "General")
        entity   = analysis.get("entity")
        language = analysis.get("language", "English")
        lang_code= analysis.get("language_code", "en")

        pdf = search_documents(user_message)
        db  = fetch_db_context(intent, entity, uid, user_city)
        print(f"📊 DB:{len(db)} PDF:{len(pdf)} City:{user_city}")

        prompt   = build_prompt(user_message, language, lang_code, pdf, db, user_profile)
        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
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
def chat_stream(user_message: str = Query(...), user_id: str = Query(...)):
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(400, "user_id must be a number")

    def stream():
        try:
            user_profile = fetch_user_profile(uid)
            user_city    = user_profile.get("city")

            analysis  = analyze_message(user_message)
            intent    = analysis.get("intent", "General")
            entity    = analysis.get("entity")
            language  = analysis.get("language", "English")
            lang_code = analysis.get("language_code", "en")

            pdf = search_documents(user_message)
            db  = fetch_db_context(intent, entity, uid, user_city)
            print(f"📊 DB:{len(db)} PDF:{len(pdf)} City:{user_city} User:{user_profile.get('name','')}")

            prompt = build_prompt(user_message, language, lang_code, pdf, db, user_profile)

            for chunk in client.models.generate_content_stream(
                model=CHAT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
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

        except Exception as e:
            yield f"\n\n❌ Error: {e}"

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
    return {"status": "running", "version": "7.0", "checks": checks}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)