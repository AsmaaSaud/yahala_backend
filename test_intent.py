"""
اختبار كشف النية — google.genai
"""
from google import genai
from google.genai import types
from dotenv import load_dotenv
import os, json

load_dotenv()
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

DETECT_PROMPT = """You are an intent classifier. Return ONLY a JSON object, no markdown, no explanation.

INTENT DEFINITIONS:
- MyTickets: asks about tickets, bookings, reservations (my tickets, ticket help, show my tickets)
- MatchSchedule: asks about games, matches, fixtures, teams playing, next game, when is the match
- Hotels: asks about hotels, accommodation, where to stay, lodging, nearby hotels
- Restaurants: asks about food, restaurants, dining, eat, halal food
- StadiumInfo: asks about stadiums, venues, where is the stadium, stadium locations
- FanZone: asks about fan zones, events, activities, entertainment, fan areas
- Emergency: asks about emergency, police, hospital, ambulance, urgent help
- General: anything else about World Cup 2034

Message: "{message}"

JSON format: {{"language": "English", "language_code": "en", "intent": "General", "entity": null}}

Rules:
- entity = team name if mentioned, else null
- Return ONLY the JSON, nothing else

Few-shot examples:
- "Nearby hotels" -> {{"language": "English", "language_code": "en", "intent": "Hotels", "entity": null}}
- "next game for Saudi team" -> {{"language": "English", "language_code": "en", "intent": "MatchSchedule", "entity": "Saudi Arabia"}}
- "Stadium locations" -> {{"language": "English", "language_code": "en", "intent": "StadiumInfo", "entity": null}}
- "Ticket help" -> {{"language": "English", "language_code": "en", "intent": "MyTickets", "entity": null}}
- "Match schedule" -> {{"language": "English", "language_code": "en", "intent": "MatchSchedule", "entity": null}}
- "Fan zones" -> {{"language": "English", "language_code": "en", "intent": "FanZone", "entity": null}}
- "Emergency services" -> {{"language": "English", "language_code": "en", "intent": "Emergency", "entity": null}}
- "الفنادق القريبة" -> {{"language": "Arabic", "language_code": "ar", "intent": "Hotels", "entity": null}}
- "متى مباراة السعودية" -> {{"language": "Arabic", "language_code": "ar", "intent": "MatchSchedule", "entity": "Saudi Arabia"}}
- "مواقع الملاعب" -> {{"language": "Arabic", "language_code": "ar", "intent": "StadiumInfo", "entity": null}}
- "تذاكري" -> {{"language": "Arabic", "language_code": "ar", "intent": "MyTickets", "entity": null}}"""

test_messages = [
    "Nearby hotels",
    "next game for Saudi team",
    "Stadium locations",
    "Ticket help",
    "Match schedule",
    "Fan zones",
    "Emergency services",
    "الفنادق القريبة",
    "متى مباراة السعودية",
    "مواقع الملاعب",
    "تذاكري",
    "مطاعم قريبة",
]

print("=" * 60)
all_pass = True
for msg in test_messages:
    r = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=DETECT_PROMPT.format(message=msg),
        config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=1000)
    )
    text = r.text.strip().replace("```json","").replace("```","").strip()
    try:
        parsed = json.loads(text)
        intent = parsed.get("intent","?")
        lang   = parsed.get("language","?")
        entity = parsed.get("entity","null")
        print(f"✅ '{msg}'\n   → {intent} | {lang} | entity: {entity}\n")
    except:
        print(f"❌ '{msg}' → PARSE ERROR: {text}\n")
        all_pass = False

print("=" * 60)
print("✅ All passed!" if all_pass else "⚠️ Some failed — check above")