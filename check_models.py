import google.generativeai as genai
import os

# --- ضعي مفتاح الـ API الخاص بكِ هنا ---
GEMINI_API_KEY = "AIzaSyDQlJDdnYFfGiObdfZg4VKBNqzYEQ7Ff_0" 
genai.configure(api_key=GEMINI_API_KEY)

print("--- قائمة النماذج المتاحة لحسابك ---")
for m in genai.list_models():
  # سنبحث عن النماذج التي تدعم طريقة 'generateContent' التي نستخدمها
  if 'generateContent' in m.supported_generation_methods:
    print(m.name)
print("------------------------------------")
