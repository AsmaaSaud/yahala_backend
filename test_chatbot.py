"""
YAHALA Chatbot — اختبارات شاملة (قاعدة البيانات + وثائق PDF)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests
import time

BASE_URL = "http://localhost:8000"
USER_ID  = "1"

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

results     = {"pass": 0, "fail": 0}
all_details = []


def test(name: str, message: str, source: str,
         expected_intent: str = None, expected_lang: str = None,
         should_contain: list = None):
    """
    source: "DB" أو "PDF" أو "DB+PDF"
    should_contain: قائمة كلمات يجب أن يحتوي عليها الرد
    """
    src_color = CYAN if "PDF" in source else BLUE
    print(f"\n{src_color}{'─'*62}{RESET}")
    print(f"{BOLD}🧪 {name}{RESET}  [{src_color}{source}{RESET}]")
    print(f"   📨 {message}")

    try:
        r = requests.post(
            f"{BASE_URL}/chat",
            params={"user_message": message, "user_id": USER_ID},
            timeout=40
        )
        data     = r.json()
        reply    = data.get("reply", "")
        intent   = data.get("intent", "")
        language = data.get("language", "")

        print(f"   🎯 Intent: {intent}  |  🌍 {language}")
        preview = reply[:220].replace("\n", " ")
        print(f"   💬 {preview}{'...' if len(reply) > 220 else ''}")

        passed = True
        issues = []

        if expected_intent and intent != expected_intent:
            issues.append(f"Intent: توقعنا '{expected_intent}' جاء '{intent}'")
            passed = False

        if expected_lang and language != expected_lang:
            issues.append(f"Language: توقعنا '{expected_lang}' جاء '{language}'")
            passed = False

        if should_contain:
            for kw in should_contain:
                if kw.lower() not in reply.lower():
                    issues.append(f"الرد لا يحتوي على: '{kw}'")
                    passed = False

        if passed:
            print(f"   {GREEN}✅ نجح{RESET}")
            results["pass"] += 1
        else:
            print(f"   {RED}❌ فشل: {' | '.join(issues)}{RESET}")
            results["fail"] += 1

        all_details.append({
            "name": name, "source": source,
            "passed": passed, "intent": intent, "language": language
        })

    except Exception as e:
        print(f"   {RED}❌ خطأ: {e}{RESET}")
        results["fail"] += 1
        all_details.append({"name": name, "source": source, "passed": False})

    time.sleep(1.2)


def section(title: str):
    print(f"\n{YELLOW}{BOLD}{'═'*62}")
    print(f"  {title}")
    print(f"{'═'*62}{RESET}")


def run():
    print(f"\n{BOLD}{'█'*62}")
    print(f"  YAHALA Chatbot — اختبارات شاملة")
    print(f"  قاعدة البيانات + وثائق PDF RAG")
    print(f"{'█'*62}{RESET}")

    # ── اختبار الاتصال ──
    section("0. اختبار الاتصال والصحة")
    try:
        r    = requests.get(f"{BASE_URL}/health", timeout=10).json()
        chks = r.get("checks", {})
        print(f"\n  {GREEN}✅ السيرفر يعمل — v{r.get('version','?')}{RESET}")
        for k, v in chks.items():
            print(f"     {k}: {v}")
        # تحقق من وجود documents
        import requests as req
        docs = req.get(f"{BASE_URL}/health", timeout=10).json()
    except Exception as e:
        print(f"  {RED}❌ السيرفر لا يعمل! {e}{RESET}")
        return

    # ════════════════════════════════════════════
    # أولاً: اختبارات قاعدة البيانات
    # ════════════════════════════════════════════
    section("1. المباريات — من قاعدة البيانات (events)")

    test("جدول المباريات - إنجليزي", "Match schedule",
         source="DB", expected_intent="MatchSchedule", expected_lang="English",
         should_contain=["2034", "Stadium"])

    test("مباريات السعودية - عربي", "متى مباراة السعودية",
         source="DB", expected_intent="MatchSchedule", expected_lang="Arabic",
         should_contain=["2034", "السعودية"])

    test("مباراة فريق - إنجليزي", "next game for Saudi team",
         source="DB", expected_intent="MatchSchedule", expected_lang="English",
         should_contain=["Saudi Arabia", "2034"])

    test("المباريات - فرنسي", "Quel est le prochain match?",
         source="DB", expected_intent="MatchSchedule", expected_lang="French",
         should_contain=["2034"])

    test("المباريات - إسباني", "¿Cuándo es el próximo partido?",
         source="DB", expected_intent="MatchSchedule", expected_lang="Spanish",
         should_contain=["2034"])

    section("2. الفنادق — من قاعدة البيانات (services)")

    test("الفنادق - إنجليزي", "Nearby hotels",
         source="DB", expected_intent="Hotels", expected_lang="English",
         should_contain=["Hotel", "Riyadh"])

    test("الفنادق - عربي", "الفنادق القريبة",
         source="DB", expected_intent="Hotels", expected_lang="Arabic",
         should_contain=["فندق"])

    test("الفنادق - صيني", "附近有什么酒店?",
         source="DB", expected_intent="Hotels", expected_lang="Chinese",
         should_contain=["酒店"])

    test("الفنادق - أردو", "قریبی ہوٹل کہاں ہیں؟",
         source="DB", expected_intent="Hotels", expected_lang="Urdu",
         should_contain=["ہوٹل"])

    section("3. الملاعب — من قاعدة البيانات (events)")

    test("مواقع الملاعب - إنجليزي", "Stadium locations",
         source="DB", expected_intent="StadiumInfo", expected_lang="English",
         should_contain=["Stadium", "Riyadh"])

    test("الملاعب - عربي", "مواقع الملاعب",
         source="DB", expected_intent="StadiumInfo", expected_lang="Arabic",
         should_contain=["ملعب", "الرياض"])

    test("ملعب محدد", "Where is King Salman Stadium?",
         source="DB", expected_intent="StadiumInfo", expected_lang="English",
         should_contain=["Riyadh"])

    test("الملاعب - ألماني", "Wo ist das Stadion?",
         source="DB", expected_intent="StadiumInfo", expected_lang="German",
         should_contain=["Stad"])

    section("4. المطاعم — من قاعدة البيانات (services)")

    test("المطاعم - إنجليزي", "Nearby restaurants",
         source="DB", expected_intent="Restaurants", expected_lang="English",
         should_contain=["Restaurant"])

    test("المطاعم - عربي", "مطاعم قريبة",
         source="DB", expected_intent="Restaurants", expected_lang="Arabic",
         should_contain=["مطعم"])

    test("الطعام الحلال", "Is there halal food available?",
         source="DB", expected_intent="Restaurants", expected_lang="English",
         should_contain=["halal", "Halal"])

    section("5. التذاكر — من قاعدة البيانات (tickets)")

    test("التذاكر - إنجليزي", "Ticket help",
         source="DB", expected_intent="MyTickets", expected_lang="English",
         should_contain=["Ticket", "ticket"])

    test("التذاكر - عربي", "تذاكري",
         source="DB", expected_intent="MyTickets", expected_lang="Arabic",
         should_contain=["تذكر"])

    section("6. الفعاليات — من قاعدة البيانات (services)")

    test("فان زون - إنجليزي", "Fan zones and events",
         source="DB", expected_intent="FanZone", expected_lang="English",
         should_contain=["event", "Event"])

    test("الفعاليات - عربي", "فعاليات وترفيه",
         source="DB", expected_intent="FanZone", expected_lang="Arabic",
         should_contain=["فعالي"])

    section("7. الطوارئ — من قاعدة البيانات")

    test("الطوارئ - إنجليزي", "Emergency services",
         source="DB", expected_intent="Emergency", expected_lang="English",
         should_contain=["911", "999"])

    test("الطوارئ - عربي", "أحتاج مساعدة طارئة",
         source="DB", expected_intent="Emergency", expected_lang="Arabic",
         should_contain=["911", "999"])

    # ════════════════════════════════════════════
    # ثانياً: اختبارات الوثائق PDF
    # ════════════════════════════════════════════
    section("8. دليل المشجعين — fans_guide.pdf")

    test("قواعد الدخول", "What items are not allowed inside the stadium?",
         source="PDF", expected_lang="English",
         should_contain=["backpack", "bottle"])

    test("قواعد السلوك", "What are the rules of conduct at the stadium?",
         source="PDF", expected_lang="English",
         should_contain=["conduct", "behavior", "respect"])

    test("إمكانية الوصول", "Are there facilities for wheelchair users at stadiums?",
         source="PDF", expected_lang="English",
         should_contain=["accessible", "disabilit"])

    section("9. التأشيرات والسفر — visa_travel.pdf")

    test("أنواع التأشيرات", "What types of visas are available for World Cup visitors?",
         source="PDF", expected_lang="English",
         should_contain=["visa", "Visa"])

    test("متطلبات الدخول", "What documents do I need to enter Saudi Arabia?",
         source="PDF", expected_lang="English",
         should_contain=["passport", "insurance"])

    test("الجمارك", "What items are prohibited to bring into Saudi Arabia?",
         source="PDF", expected_lang="English",
         should_contain=["alcohol", "prohibited"])

    test("العملة - عربي", "ما هي العملة في السعودية وكيف أصرف؟",
         source="PDF", expected_lang="Arabic",
         should_contain=["ريال"])

    section("10. القوانين والمخالفات — laws_violations.pdf")

    test("قوانين المرور", "What are the traffic fines in Saudi Arabia?",
         source="PDF", expected_lang="English",
         should_contain=["fine", "SAR"])

    test("قواعد السلوك العام", "What behavior is prohibited in public in Saudi Arabia?",
         source="PDF", expected_lang="English",
         should_contain=["intoxication", "prohibited"])

    test("قواعد اللباس - عربي", "ما هي قواعد اللباس في السعودية؟",
         source="PDF", expected_lang="Arabic",
         should_contain=["لباس", "محتشم"])

    test("المخالفات - فرنسي", "Quelles sont les infractions au code de la route?",
         source="PDF", expected_lang="French",
         should_contain=["SAR", "infraction", "amende"])

    section("11. الصحة والطوارئ — health_emergency.pdf")

    test("أرقام الطوارئ من PDF", "What is the emergency number in Saudi Arabia?",
         source="PDF", expected_lang="English",
         should_contain=["911", "999"])

    test("الخدمات الطبية", "Are there medical facilities at the stadiums?",
         source="PDF", expected_lang="English",
         should_contain=["medical", "first aid"])

    test("نصائح الحر", "How to stay safe from the heat in Saudi Arabia?",
         source="PDF", expected_lang="English",
         should_contain=["water", "heat", "sun"])

    test("المستشفيات - عربي", "ما هي المستشفيات في الرياض؟",
         source="PDF", expected_lang="Arabic",
         should_contain=["مستشفى", "الرياض"])

    section("12. الثقافة والعادات — culture_guide.pdf")

    test("أوقات الصلاة", "What are prayer times and how do they affect daily life?",
         source="PDF", expected_lang="English",
         should_contain=["prayer", "Prayer"])

    test("آداب التعامل", "What are the social etiquette rules in Saudi Arabia?",
         source="PDF", expected_lang="English",
         should_contain=["right hand", "respect"])

    test("الطعام الحلال من PDF", "Is all food in Saudi Arabia halal?",
         source="PDF", expected_lang="English",
         should_contain=["halal", "Islamic"])

    test("العبارات العربية - إنجليزي", "How do I say thank you in Arabic?",
         source="PDF", expected_lang="English",
         should_contain=["Shukran", "shukran"])

    section("13. دليل التذاكر — tickets_guide.pdf")

    test("شراء التذاكر", "How can I buy official World Cup tickets?",
         source="PDF", expected_lang="English",
         should_contain=["fifa.com", "FIFA"])

    test("سياسة الاسترداد", "Can I get a refund for my World Cup ticket?",
         source="PDF", expected_lang="English",
         should_contain=["refund", "cancel"])

    test("فئات التذاكر - عربي", "ما هي أسعار تذاكر كأس العالم؟",
         source="PDF", expected_lang="Arabic",
         should_contain=["80", "1,000"])

    # ════════════════════════════════════════════
    # ثالثاً: اختبارات متعددة المصادر DB+PDF
    # ════════════════════════════════════════════
    section("14. اختبارات تجمع DB + PDF")

    test("ملعب + قواعد الدخول", "What are the rules to enter King Salman Stadium?",
         source="DB+PDF", expected_lang="English",
         should_contain=["Stadium", "ticket"])

    test("فندق + ثقافة", "What hotels are available and what is the dress code?",
         source="DB+PDF", expected_lang="English",
         should_contain=["Hotel", "dress"])

    test("مباراة + تأشيرة - عربي", "متى تلعب السعودية وكيف أحصل على تأشيرة؟",
         source="DB+PDF", expected_lang="Arabic",
         should_contain=["السعودية", "تأشيرة"])

    test("طوارئ + مستشفى", "I have an emergency, what number should I call and which hospital is nearby?",
         source="DB+PDF", expected_lang="English",
         should_contain=["911", "hospital"])

    # ════════════════════════════════════════════
    # رابعاً: اختبارات اللغات النادرة
    # ════════════════════════════════════════════
    section("15. اختبارات لغات متعددة")

    test("برتغالي", "Quando é o próximo jogo?",
         source="DB", expected_lang="Portuguese",
         should_contain=["2034"])

    test("ياباني", "スタジアムはどこですか？",
         source="DB", expected_lang="Japanese",
         should_contain=["スタジアム", "リヤド"])

    test("كوري", "호텔이 어디 있나요?",
         source="DB", expected_lang="Korean",
         should_contain=["호텔", "Hotel"])

    test("هندي", "स्टेडियम कहाँ है?",
         source="DB", expected_lang="Hindi",
         should_contain=["स्टेडियम", "रियाद"])

    # ════════════════════════════════════════════
    # النتائج النهائية
    # ════════════════════════════════════════════
    total   = results["pass"] + results["fail"]
    percent = round(results["pass"] / total * 100) if total > 0 else 0

    # تفصيل حسب المصدر
    db_tests  = [d for d in all_details if d["source"] == "DB"]
    pdf_tests = [d for d in all_details if d["source"] == "PDF"]
    mix_tests = [d for d in all_details if d["source"] == "DB+PDF"]

    db_pass  = sum(1 for d in db_tests  if d["passed"])
    pdf_pass = sum(1 for d in pdf_tests if d["passed"])
    mix_pass = sum(1 for d in mix_tests if d["passed"])

    print(f"\n{BOLD}{'█'*62}{RESET}")
    print(f"{BOLD}   📊 النتائج النهائية{RESET}")
    print(f"{'█'*62}")
    print(f"\n   {BLUE}🗄️  قاعدة البيانات:{RESET}  {GREEN}{db_pass}/{len(db_tests)}{RESET} نجح")
    print(f"   {CYAN}📄 وثائق PDF:      {RESET}  {GREEN}{pdf_pass}/{len(pdf_tests)}{RESET} نجح")
    print(f"   🔀 DB + PDF:        {GREEN}{mix_pass}/{len(mix_tests)}{RESET} نجح")
    print(f"\n   {BOLD}المجموع: {GREEN}{results['pass']}{RESET}/{total} — {percent}%{RESET}")

    if percent == 100:
        print(f"\n   {GREEN}{BOLD}🏆 مثالي! الشات بوت جاهز للعرض أمام المشرفة 🎉{RESET}")
    elif percent >= 90:
        print(f"\n   {GREEN}{BOLD}✅ ممتاز! دقة عالية جداً{RESET}")
    elif percent >= 75:
        print(f"\n   {YELLOW}{BOLD}👍 جيد — يحتاج تحسينات بسيطة{RESET}")
    else:
        print(f"\n   {RED}{BOLD}⚠️  يحتاج مراجعة{RESET}")

    # الفاشلة
    failed = [d for d in all_details if not d["passed"]]
    if failed:
        print(f"\n   {RED}الاختبارات الفاشلة:{RESET}")
        for f in failed:
            print(f"   ❌ [{f['source']}] {f['name']}")
    print(f"\n{'█'*62}\n")


if __name__ == "__main__":
    run()