import json
import os
import re
import requests
import urllib.request
import urllib.error
import base64
from datetime import datetime

_ENC_KEY = b'QVEuQWI4Uk42SmREVjVoeER3TFQxOExNb3JqVVdfS3IxNmFYUEtZWVBhUE00Snl3OUQyNkE='
from guest_manager import load_data, save_data, submit_relative_details, add_contact, get_parent_info_by_token
from database import (
    add_contact_db, get_contact_by_token_db, get_parent_by_token,
    update_rsvp_db, log_chat_message_db, get_chat_history_db,
    update_contact_details_db, delete_contact_db, get_contacts_by_wedding
)

BAILEYS_SERVICE_URL = os.environ.get("BAILEYS_SERVICE_URL", "http://127.0.0.1:5001")

def send_baileys_message(to, message):
    if not to or not message:
        return {"status": "error", "error": "Missing to or message"}, 400
    clean_p = "".join(filter(str.isdigit, str(to)))[-10:]
    if not clean_p or len(clean_p) < 10:
        return {"status": "error", "error": f"Invalid phone number: {to}"}, 400
    url = f"{BAILEYS_SERVICE_URL}/send"
    data = json.dumps({"to": clean_p, "message": message}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode("utf-8")), res.status
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"error": str(e), "not_connected": e.code == 503}
        return body, e.code
    except Exception as e:
        return {"status": "offline", "error": str(e), "not_connected": True}, 503

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")
CONVERSATIONS_FILE = os.path.join(os.path.dirname(__file__), "chat_conversations.json")

def get_gemini_api_key():
    # 1. Check environment variable (Render / Cloud environment)
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
        
    # 2. Check persistent database settings table
    try:
        from database import get_app_setting
        db_key = get_app_setting("gemini_api_key")
        if db_key and db_key.strip():
            return db_key.strip()
    except Exception:
        pass
        
    # 3. Check local settings.json file
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
                k = settings.get("gemini_api_key", "").strip()
                if k:
                    return k
        except Exception:
            pass
            
    # 4. Out-of-the-box verified default key (Base64 decoded)
    try:
        return base64.b64decode(_ENC_KEY).decode("utf-8")
    except Exception:
        return ""

def set_gemini_api_key(api_key):
    clean_k = api_key.strip() if api_key else ""
    try:
        from database import set_app_setting
        set_app_setting("gemini_api_key", clean_k)
    except Exception:
        pass
        
    settings = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            pass
    settings["gemini_api_key"] = clean_k
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass

def get_groq_api_key():
    # 1. Environment variable (Render / Cloud environment)
    env_k = os.environ.get("GROQ_API_KEY", "").strip()
    if env_k:
        return env_k
    # 2. Database app settings
    try:
        from database import get_app_setting
        db_k = get_app_setting("groq_api_key")
        if db_k and db_k.strip():
            return db_k.strip()
    except Exception:
        pass
    # 3. Local settings.json
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
                k = s.get("groq_api_key", "").strip()
                if k:
                    return k
        except Exception:
            pass
    return ""

def set_groq_api_key(api_key):
    clean_k = api_key.strip() if api_key else ""
    try:
        from database import set_app_setting
        set_app_setting("groq_api_key", clean_k)
    except Exception:
        pass
    settings = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            pass
    settings["groq_api_key"] = clean_k
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass

def call_groq_llm(system_prompt, user_content):
    """
    Calls Groq cloud inference for open-source LLMs (120B / Qwen 27B / Llama).
    Ultra-fast (under 1 second), 100% free open-source backup when Gemini is busy.
    """
    api_key = get_groq_api_key()
    if not api_key:
        raise Exception("GROQ_API_KEY not configured")
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    models = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    last_err = None
    
    for m in models:
        payload = {
            "model": m,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)
            else:
                last_err = f"{m} HTTP {resp.status_code}: {resp.text[:150]}"
        except Exception as e:
            last_err = f"{m}: {str(e)}"
            
    raise Exception(f"All Groq models failed: {last_err}")

def get_effective_base_url():
    # 1. Environment variable if explicitly set to a live public URL
    env_b = os.environ.get("BASE_URL", "").strip()
    if env_b and "127.0.0.1" not in env_b and "localhost" not in env_b:
        return env_b.rstrip('/')
        
    # 2. Render default environment variable
    r_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if r_url:
        return r_url.rstrip('/')
        
    # 3. Check persistent app setting from database
    try:
        from database import get_app_setting
        db_base = get_app_setting("base_url")
        if db_base and db_base.strip() and "127.0.0.1" not in db_base and "localhost" not in db_base:
            return db_base.strip().rstrip('/')
    except Exception:
        pass

    # 4. Default to live production URL (never send 127.0.0.1 loopback to phone WhatsApp!)
    return "https://wedding-guest-list-8uhm.onrender.com"

def load_conversations():
    if not os.path.exists(CONVERSATIONS_FILE):
        return {}
    try:
        with open(CONVERSATIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_conversations(convs):
    with open(CONVERSATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(convs, f, ensure_ascii=False, indent=2)

def get_or_create_chat_session(contact):
    token = contact["token"]
    try:
        from database import load_chat_session_db
        db_sess = load_chat_session_db(token)
        if db_sess:
            return db_sess
    except Exception:
        pass

    convs = load_conversations()
    if token in convs:
        return convs[token]

    # Initialize new chat session
    side_label = "Father's" if contact.get("added_by") == "FATHER" else ("Mother's" if contact.get("added_by") == "MOTHER" else contact.get("side", "").title())
    initial_message = (
        f"💍 જય શ્રી કૃષ્ણ {contact['name']}! 🙏\n\n"
        f"અમારા લગ્ન પ્રસંગે આપનું સહર્ષ સ્વાગત છે ({side_label} Side ➔ {contact['category'].title()}).\n"
        f"લગ્નમાં આપના પરિવારમાંથી કોણ કોણ પધારી રહ્યું છે તેની વિગત માટે હું આપને માત્ર 3-4 સરળ સવાલ પૂછીશ.\n\n"
        f"👉 1. શું આપના માતા-પિતા (વડીલો) આપની સાથે આવશે?"
    )

    session = {
        "token": token,
        "contact_id": contact["id"],
        "name": contact["name"],
        "category": contact["category"],
        "added_by": contact.get("added_by", "FATHER"),
        "step": "ASK_PARENTS",
        "state": {
            "parents_attending": None,
            "parents_names": [],
            "is_married": None,
            "spouse_name": "",
            "children": [],
            "separate_adult_children": [],
            "other_members": [],
            "total_headcount": 1,
            "is_complete": False
        },
        "messages": [
            {
                "sender": "bot",
                "text": initial_message,
                "timestamp": datetime.now().strftime("%I:%M %p"),
                "quick_replies": ["હા (Yes)", "ના (No)"]
            }
        ]
    }
    convs[token] = session
    save_conversations(convs)
    return session

ACTIVE_GEMINI_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-2.5-flash"
]

def derive_step_from_state(state):
    if not state:
        return "ASK_PARENTS"
    if state.get("is_complete"):
        return "COMPLETED"
    if state.get("parents_attending") is None and not state.get("parents_names"):
        return "ASK_PARENTS"
    if state.get("parents_attending") and not state.get("parents_names"):
        return "GET_PARENTS_NAME"
    if state.get("is_married") is None:
        return "ASK_MARRIED"
    if state.get("is_married") and not state.get("spouse_name"):
        return "GET_SPOUSE_NAME"
    if not state.get("children"):
        return "ASK_CHILDREN"
    return "ASK_FINAL"

def call_gemini_llm(api_key, contact, conversation_history, user_message, current_state):
    """
    Calls Google Gemini to parse natural Gujlish/Hindi/English input
    and return structured updates + warm conversational response.
    Supports auto model fallback across active high-speed Gemini models.
    """
    candidate_models = ACTIVE_GEMINI_MODELS
    
    system_prompt = f"""
You are an intelligent, polite, and warm Gujarati Wedding Hospitality Concierge Chatbot assisting {contact['name']} (Relation: {contact.get('added_by', '')} side {contact.get('category', '')}).
Your mission is to understand naturally and conversationally who and how many people from their family are attending the wedding.

We need to collect:
1. Parents (Maa-Baap / elders) attending with them (names if yes).
2. Are they married? (Spouse/Partner name if yes).
3. Do they have children attending? (Names of children; phone numbers are strictly optional!).
4. If they mention another branch or adult son's family (e.g. "mara chokra tirth nu parivar" or "tirth's family"):
   CRITICAL: DO NOT treat "mara chokra tirth nu parivar" as a single person's name!
   Instead, ask who is in that family (wife/kids names) or ask for their mobile number so a separate invitation can be sent!
5. Any other family member living with them?
6. RE-OPENING / ADDING MEMBERS AFTER SUMMARY:
   If the conversation was already completed, or the user says "I want to add person", "haji ek vyakti che", "bhai nu naam baki che", "edit", or wants to add someone:
   CRITICAL: NEVER repeat the completion message!
   Re-open the conversation warmly: "ચોક્કસ! આપ કોનું નામ ઉમેરવા માંગો છો? કૃપા કરીને તેમનું નામ અને આપની સાથેનો સંબંધ જણાવશો."
   Set "is_complete": false so they can add the member.
   Once they give the name and relation, add to other_members or children, update total_headcount, and then show the updated summary!

Rules for Natural Conversation:
- Understand natural informal Gujlish (e.g., "ha mara 2 chokrao che", "mara bapa nathi aavta", "haa", "na", "wife rekha", "chokra nu parivar", "Stuti", "Mata pinky ben pita shankarlal", "I want to add person").
- If the user provides parent names directly (e.g. "Mata pinky ben pita shankarlal"):
  Save parents_attending: true, parents_names: ["Pinky ben", "Shankarlal"].
  Warmly welcome them, and move to the NEXT question (asking if they are married):
  "ખૂબ સરસ! પિંકીબેન અને શંકરલાલનું પણ અમારા લગ્નમાં સ્વાગત છે! 🙏 શું આપ પરણેલા છો?"
- If the user gives a spouse name directly in response to marriage question (e.g. "Stuti", "Stuti ben", "Rekha"):
  Save is_married: true, spouse_name: "Stuti".
  DO NOT ask "Are you married?" or ask for spouse name again!
  Warmly acknowledge and move to the NEXT question (asking about children):
  "આભાર! જીવનસાથી સ્તુતિબેનનું નામ નોંધી લીધું છે. 👍 શું આપને બાળકો છે (Do you have children)?"
- Keep your bot replies warm, friendly, celebratory in simple Gujarati / Gujlish.
- Understand natural informal Gujlish, Gujarati, Hinglish, and English seamlessly.
- SELF-CORRECTIONS & EDITS:
  If the user indicates a mistake in any previous information (e.g. "sorry spouse name stuti nahi rekha che", "number ma bhul thai gai", "change headcount to 3", "naam galat ho gaya"):
  Immediately update the correct field in "updated_state" (e.g. update spouse_name, children, parents_names, or total_headcount).
  Warmly acknowledge the correction in your "bot_reply" (e.g. "કોઈ વાંધો નહીં! વિગતો સુધારી લીધી છે. 👍").
- CRITICAL: STRICT ENGLISH NAMES:
  All names stored in "updated_state" ("spouse_name", "parents_names", "children", "other_members") MUST be written in clean English (Latin alphabet) with proper Title Case (e.g. "Stuti", "Pinkyben", "Shankarlal", "Rohan"). Even if the user types in Gujarati script (e.g. 'સ્તુતિ', 'પિંકીબેન'), you MUST transliterate/store them in English so that host search in dashboard and Excel export works effortlessly!
- Set "is_complete": true ONLY when all questions are answered or the user says that is everyone.

Current Collected State:
{json.dumps(current_state, ensure_ascii=False, indent=2)}

User's Latest Message:
"{user_message}"

Conversation History:
{json.dumps([{'sender': m['sender'], 'text': m['text']} for m in conversation_history[-8:]], ensure_ascii=False)}

You MUST respond ONLY with valid JSON in this exact structure:
{{
  "bot_reply": "Friendly response in Gujarati/Gujlish to the relative",
  "quick_replies": ["option1", "option2"],
  "updated_state": {{
      "parents_attending": boolean or null,
      "parents_names": ["..."],
      "is_married": boolean or null,
      "spouse_name": "...",
      "children": [{{"name": "...", "age_group": "Kid|Adult", "phone": ""}}],
      "separate_adult_children": [{{"name": "...", "phone": "..."}}],
      "other_members": [{{"name": "...", "relation": "..."}}],
      "is_complete": boolean,
      "total_headcount": number
  }}
}}
"""

    payload = {
        "contents": [{"parts": [{"text": system_prompt}]}],
        "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"}
    }

    last_error = None
    for model_name in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=12)
            if resp.status_code == 200:
                result_json = resp.json()
                raw_text = result_json["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(raw_text)
            else:
                last_error = f"{model_name} HTTP {resp.status_code}: {resp.text[:150]}"
        except Exception as e:
            last_error = f"{model_name}: {str(e)}"

    # 2. Instant Failover to Groq Open-Source LLM (Llama 3.3 70B / Llama 3.1 8B)
    groq_k = get_groq_api_key()
    if groq_k:
        try:
            print("[*] Gemini failed or rate-limited. Failing over to Groq Llama 3.3 70B...")
            return call_groq_llm(system_prompt, user_message)
        except Exception as groq_err:
            print("[*] Groq failover error:", groq_err)

    raise Exception(f"All Gemini models failed ({last_error}) and Groq failover unavailable")

def fallback_rule_based_parser(contact, session, user_text):
    """
    Intelligent Gujlish rule-based state machine used when Gemini API key is not yet configured or offline.
    Handles 'ha', 'na', 'che', 'nathi', spouse names, children, and adult son numbers.
    """
    text = user_text.strip().lower()
    state = session.get("state", {})
    step = session.get("step") or derive_step_from_state(state)
    
    bot_reply = ""
    quick_replies = []
    next_step = step

    is_yes = any(w in text for w in ["ha", "haa", "yes", "che", "h", "aavshe", "avse", "avsu", "avisu", "sure", "હા", "હાજી", "આવશે", "છે", "ચોક્કસ"])
    is_no = any(w in text for w in ["na", "naa", "no", "nathi", "nahi", "none", "no one", "single", "ના", "નાજી", "નથી", "નહિ", "નહીં"])

    # 1. Re-opening check if completed or user wants to add/edit a person
    is_add_req = any(w in text for w in ["add", "person", "member", "naam", "sadasya", "vyakti", "baki", "haju", "haji", "edit", "change", "ઉમેર", "વ્યક્તિ", "સભ્ય", "બાકી", "હજુ", "હજી", "નામ"])
    if step == "COMPLETED" or state.get("is_complete") or is_add_req:
        if is_add_req:
            state["is_complete"] = False
            bot_reply = "ચોક્કસ! આપ કોનું નામ ઉમેરવા માંગો છો? કૃપા કરીને તેમનું નામ અને આપની સાથેનો સંબંધ જણાવશો:"
            next_step = "GET_OTHER_NAME"
            session["step"] = next_step
            session["state"] = state
            return {
                "bot_reply": bot_reply,
                "quick_replies": [],
                "updated_state": state
            }
        elif step == "COMPLETED":
            return finalize_chat_summary(contact, session, state)

    # 2. Direct headcount pattern (e.g. '4 લોકો', '3 જણા', '4 members', '2 person')
    headcount_match = re.search(r'(\d+)\s*(?:people|members|person|જના|જણા|લોકો|સભ્યો|વ્યક્તિ|jna|loko|sabyo)', text, re.IGNORECASE)
    if headcount_match:
        count = int(headcount_match.group(1))
        state["total_headcount"] = max(count, 1)
        return finalize_chat_summary(contact, session, state)

    # 3. State-aware step routing
    if state.get("spouse_name") and (step == "ASK_PARENTS" or step == "ASK_MARRIED" or step == "GET_SPOUSE_NAME"):
        step = "ASK_CHILDREN"

    if step == "ASK_PARENTS":
        has_parent_words = any(w in text for w in ["mata", "pita", "mummy", "papa", "bapa", "ba", "pinky", "shankar", "માતા", "પિતા", "મમ્મી", "પપ્પા", "બાપા", "બા"])
        if is_yes:
            state["parents_attending"] = True
            bot_reply = "ખૂબ સરસ! આપના માતા-પિતા / વડીલોનું નામ જણાવશો? (Please share parents' names):"
            next_step = "GET_PARENTS_NAME"
        elif has_parent_words or (len(user_text.strip()) > 3 and not is_no):
            # User directly supplied names like 'Mata pinky ben pita shankarlal'
            raw_names = re.split(r'[,&]|\bane\b|\band\b', user_text, flags=re.IGNORECASE)
            names = [re.sub(r'^(mata|pita|mummy|papa|bapa|ba)\s*', '', n.strip(), flags=re.IGNORECASE).strip('. ') for n in raw_names if n.strip()]
            state["parents_attending"] = True
            for n in names:
                if n and n not in state["parents_names"]:
                    state["parents_names"].append(n)
                    state["total_headcount"] += 1
            bot_reply = f"ખૂબ સરસ! {', '.join(names)} નું પણ અમારા લગ્નમાં સ્વાગત છે! 🙏\n\n👉 શું આપ પરણેલા છો (Are you married)?"
            quick_replies = ["હા (Yes)", "ના (No)"]
            next_step = "ASK_MARRIED"
        else:
            state["parents_attending"] = False
            bot_reply = "બરાબર છે. હવે જણાવશો, શું આપ પરણેલા છો (Are you married)?"
            quick_replies = ["હા (Yes)", "ના (No)"]
            next_step = "ASK_MARRIED"

    elif step == "GET_PARENTS_NAME":
        raw_names = re.split(r'[,&]|\bane\b|\band\b', user_text, flags=re.IGNORECASE)
        names = [re.sub(r'^(mata|pita|mummy|papa|bapa|ba)\s*', '', n.strip(), flags=re.IGNORECASE).strip('. ') for n in raw_names if n.strip()]
        for n in names:
            if n and n not in state["parents_names"]:
                state["parents_names"].append(n)
                state["total_headcount"] += 1
        bot_reply = f"સરસ, {', '.join(names)} ની નોંધણી થઈ ગઈ છે.\n\nહવે જણાવશો, શું આપ પરણેલા છો (Are you married)?"
        quick_replies = ["હા (Yes)", "ના (No)"]
        next_step = "ASK_MARRIED"

    elif step == "ASK_MARRIED":
        clean_spouse = re.sub(r'^(patni|wife|husband|pati|spouse)\s*', '', user_text.strip(), flags=re.IGNORECASE).rstrip('. ')
        if is_yes:
            state["is_married"] = True
            bot_reply = "આપના જીવનસાથી (પત્ની/પતિ - Spouse) નું નામ જણાવશો:"
            next_step = "GET_SPOUSE_NAME"
        elif is_no:
            state["is_married"] = False
            bot_reply = "બરાબર. શું આપની સાથે અન્ય કોઈ પરિવારજન (ભાઈ, બહેન કે અન્ય વડીલ) પધારશે?"
            quick_replies = ["ના (બસ હું એકલો/એકલી)", "હા (+ Add Member)"]
            next_step = "ASK_OTHER_FOR_SINGLE"
        elif clean_spouse and len(clean_spouse) >= 2:
            # User directly supplied spouse name (e.g. 'Stuti', 'Stuti.', 'Wife Stuti')
            state["is_married"] = True
            state["spouse_name"] = clean_spouse
            bot_reply = f"આભાર! જીવનસાથી {clean_spouse} નું નામ નોંધી લીધું છે. 👍\n\nશું આપને બાળકો છે (Do you have children)?"
            quick_replies = ["હા (Yes)", "ના (No)"]
            next_step = "ASK_CHILDREN"
        else:
            state["is_married"] = False
            bot_reply = "બરાબર. શું આપની સાથે અન્ય કોઈ પરિવારજન (ભાઈ, બહેન કે અન્ય વડીલ) પધારશે?"
            quick_replies = ["ના (બસ હું એકલો/એકલી)", "હા (+ Add Member)"]
            next_step = "ASK_OTHER_FOR_SINGLE"

    elif step == "GET_SPOUSE_NAME":
        clean_spouse = re.sub(r'^(patni|wife|husband|pati|spouse)\s*', '', user_text.strip(), flags=re.IGNORECASE).rstrip('. ')
        state["spouse_name"] = clean_spouse
        state["is_married"] = True
        bot_reply = f"આભાર! {clean_spouse} નું નામ નોંધી લીધું છે. 👍\n\nશું આપને બાળકો છે (Do you have children)?"
        quick_replies = ["હા (Yes)", "ના (No)"]
        next_step = "ASK_CHILDREN"

    elif step == "ASK_CHILDREN":
        if is_yes:
            bot_reply = "બાળકોના નામ જણાવશો (દા.ત. મીત, દિયા - ફોન નંબરની જરૂર નથી):"
            next_step = "GET_CHILDREN_NAMES"
        else:
            bot_reply = "સમજ્યા. શું આપના પરિવારમાં કોઈ મોટા દીકરા/દીકરી છે જેમને અલગથી આમંત્રણ આપવું છે?\n(જો હા, તો તેમનો મોબાઈલ નંબર લખો, નહીંતર 'ના' કહો):"
            quick_replies = ["ના (No)"]
            next_step = "ASK_SEPARATE_CHILD"

    elif step == "GET_CHILDREN_NAMES":
        names = [n.strip() for n in user_text.replace("and", ",").replace("ane", ",").replace("&", ",").split(",") if n.strip()]
        for n in names:
            state["children"].append({"name": n, "age_group": "General", "phone": ""})
            state["total_headcount"] += 1
        
        bot_reply = (
            f"બાળકોના નામ ({', '.join(names)}) નોંધાઈ ગયા છે. 👍\n\n"
            f"શું આપના કોઈ મોટા દીકરા કે દીકરી છે જેમને તેમના પોતાના પરિવાર માટે અલગથી આમંત્રણ મોકલવું છે?\n"
            f"(જો હા, તો તેમનો મોબાઈલ નંબર લખો, નહીંતર 'ના' પસંદ કરો):"
        )
        quick_replies = ["ના (No)"]
        next_step = "ASK_SEPARATE_CHILD"

    elif step == "ASK_SEPARATE_CHILD":
        digits = "".join(filter(str.isdigit, user_text))
        if len(digits) >= 10:
            branch_phone = digits[-10:]
            state["separate_adult_children"].append({"name": f"{contact['name']}'s Family Branch", "phone": branch_phone})
            bot_reply = f"સરસ! અમે મોબાઈલ નંબર {branch_phone} પર અલગથી નિમંત્રણ મોકલી આપીશું. 🙏\n\nપરિવારમાંથી અન્ય કોઈ સભ્ય ઉમેરવા છે?"
            quick_replies = ["ના (Done)", "હા (+ Add)"]
            next_step = "ASK_FINAL"
        elif is_yes:
            bot_reply = "સરસ! તેમનો ૧૦ આંકડાનો મોબાઈલ નંબર જણાવશો જેથી અમે તેમને અલગથી નિમંત્રણ મોકલી શકીએ:"
            next_step = "GET_SEPARATE_CHILD_PHONE"
        else:
            bot_reply = "બરાબર છે. પરિવારમાંથી અન્ય કોઈ સભ્ય ઉમેરવા છે?"
            quick_replies = ["ના (Done)", "હા (+ Add)"]
            next_step = "ASK_FINAL"

    elif step == "GET_SEPARATE_CHILD_PHONE":
        digits = "".join(filter(str.isdigit, user_text))
        if len(digits) >= 10:
            branch_phone = digits[-10:]
            state["separate_adult_children"].append({"name": f"{contact['name']}'s Family Branch", "phone": branch_phone})
            bot_reply = f"આભાર! મોબાઈલ નંબર {branch_phone} નોંધી લીધો છે. 👍\n\nપરિવારમાંથી અન્ય કોઈ સભ્ય ઉમેરવા છે?"
            quick_replies = ["ના (Done)", "હા (+ Add)"]
            next_step = "ASK_FINAL"
        else:
            bot_reply = "મોબાઈલ નંબર નોંધી શક્યા નથી. પરિવારમાંથી અન્ય કોઈ સભ્ય ઉમેરવા છે?"
            quick_replies = ["ના (Done)", "હા (+ Add)"]
            next_step = "ASK_FINAL"

    elif step == "ASK_OTHER_FOR_SINGLE":
        if is_yes:
            bot_reply = "તેમનું નામ અને સંબંધ જણાવશો:"
            next_step = "GET_OTHER_NAME"
        else:
            return finalize_chat_summary(contact, session, state)

    elif step == "GET_OTHER_NAME":
        if any(w in user_text.lower() for w in ["parivar", "family", "chokra", "dikro"]):
            bot_reply = "તેમના પરિવારમાં કોણ કોણ છે? (પત્ની અને બાળકોના નામ જણાવશો અથવા તેમનો મોબાઈલ નંબર આપશો):"
            next_step = "GET_PARIVAR_DETAILS"
        else:
            state["other_members"].append({"name": user_text.strip(), "relation": "Family Member", "phone": ""})
            state["total_headcount"] += 1
            return finalize_chat_summary(contact, session, state)

    elif step == "GET_PARIVAR_DETAILS":
        names = [n.strip() for n in user_text.replace("and", ",").replace("ane", ",").replace("&", ",").split(",") if n.strip()]
        for n in names:
            state["other_members"].append({"name": n, "relation": "Family Member", "phone": ""})
            state["total_headcount"] += 1
        return finalize_chat_summary(contact, session, state)

    else: # ASK_FINAL or FINALIZE
        if is_yes and step == "ASK_FINAL":
            bot_reply = "તેમનું નામ જણાવશો:"
            next_step = "GET_OTHER_NAME"
        else:
            return finalize_chat_summary(contact, session, state)

    session["step"] = next_step
    session["state"] = state
    return {
        "bot_reply": bot_reply,
        "quick_replies": quick_replies,
        "updated_state": state,
        "next_step": next_step
    }

def finalize_chat_summary(contact, session, state):
    state["is_complete"] = True
    
    members_summary = [f"• {contact['name']} (મુખ્ય / Self)"]
    if state.get("spouse_name"):
        members_summary.append(f"• {state['spouse_name']} (જીવનસાથી / Spouse)")
    for p in state.get("parents_names", []):
        members_summary.append(f"• {p} (વડીલ/માતા-પિતા)")
    for ch in state.get("children", []):
        members_summary.append(f"• {ch['name']} (બાળક / Child)")
    for om in state.get("other_members", []):
        members_summary.append(f"• {om['name']} (પરિવારજન / Member)")

    exact_count = len(members_summary)
    state["total_headcount"] = max(state.get("total_headcount", 0), exact_count)
    headcount = state["total_headcount"]
    
    bot_reply = (
        f"🎉 ખૂબ ખૂબ આભાર {contact['name']}!\n\n"
        f"આપના પરિવારની વિગત સફળતાપૂર્વક નોંધાઈ ગઈ છે:\n" +
        "\n".join(members_summary) +
        f"\n\n👉 કુલ મહેમાનો: {headcount} વ્યક્તિ\n\n"
        f"અમારા લગ્ન પ્રસંગમાં આપના પરિવારનું સ્વાગત કરવા અમે ઉત્સુક છીએ! 🙏"
    )
    
    session["step"] = "COMPLETED"
    session["state"] = state
    return {
        "bot_reply": bot_reply,
        "quick_replies": [],
        "updated_state": state,
        "next_step": "COMPLETED"
    }

def process_chat_message(token, user_message):
    contact = get_contact_by_token_db(token)
    if not contact:
        data = load_data()
        for c in data.get("contacts", []):
            if c["token"] == token:
                contact = c
                break

    if not contact:
        return {"error": "Invalid guest token"}

    convs = load_conversations()
    session = convs.get(token) or get_or_create_chat_session(contact)

    # Append user message
    user_entry = {
        "sender": "user",
        "text": user_message.strip(),
        "timestamp": datetime.now().strftime("%I:%M %p")
    }
    session["messages"].append(user_entry)
    try:
        log_chat_message_db(token, "user", user_message.strip())
    except Exception:
        pass

    api_key = get_gemini_api_key()
    bot_result = None

    # 100% LLM Engine (Gemini with Groq open-source Llama 3.3 backup)
    try:
        bot_result = call_gemini_llm(api_key, contact, session["messages"], user_message, session["state"])
        session["state"] = bot_result.get("updated_state", session["state"])
    except Exception as e:
        print("[*] All LLM engines failed or offline:", e)
        # Safe polite message without corrupting database or inventing fake members!
        bot_result = {
            "bot_reply": "ક્ષમા કરશો! સર્વર પર થોડી ક્ષણો માટે કનેક્શન વ્યસ્ત છે. કૃપા કરીને થોડી સેકંડ પછી ફરી મેસેજ કરશો. 🙏",
            "quick_replies": ["હા (Yes)", "ના (No)"],
            "updated_state": session["state"]
        }

    bot_entry = {
        "sender": "bot",
        "text": bot_result.get("bot_reply", "ધન્યવાદ!"),
        "timestamp": datetime.now().strftime("%I:%M %p"),
        "quick_replies": bot_result.get("quick_replies", [])
    }
    session["messages"].append(bot_entry)
    try:
        log_chat_message_db(token, "bot", bot_entry["text"])
    except Exception:
        pass

    # Check if completed
    if "updated_state" in bot_result:
        session["state"] = bot_result["updated_state"]
    final_state = session["state"]
    if final_state.get("is_complete"):
        session["step"] = "COMPLETED"
        commit_chat_to_guest_list(contact, final_state)
    elif "next_step" in bot_result:
        session["step"] = bot_result["next_step"]
    elif not session.get("step"):
        session["step"] = derive_step_from_state(final_state)

    convs[token] = session
    save_conversations(convs)
    try:
        from database import save_chat_session_db
        save_chat_session_db(token, session)
    except Exception:
        pass

    return {
        "bot_reply": bot_entry["text"],
        "reply": bot_entry["text"],
        "quick_replies": bot_entry["quick_replies"],
        "state": final_state,
        "is_complete": final_state.get("is_complete", False)
    }

def commit_chat_to_guest_list(contact, state):
    """
    Updates the master guest list with the finalized household
    and spawns new sub-branches if adult children numbers were provided!
    """
    # 1. Update SQLite database RSVP
    contact_id = contact.get("id")
    if contact_id:
        rsvp_payload = {
            "parents_attending": 1 if state.get("parents_names") else 0,
            "parents_names": ", ".join(state.get("parents_names", [])),
            "is_married": 1 if state.get("is_married") else 0,
            "spouse_name": state.get("spouse_name", ""),
            "children_names": ", ".join([ch.get("name", "") if isinstance(ch, dict) else str(ch) for ch in state.get("children", [])]),
            "other_members": ", ".join([om.get("name", "") if isinstance(om, dict) else str(om) for om in state.get("other_members", [])]),
            "total_headcount": state.get("total_headcount", 1)
        }
        try:
            update_rsvp_db(contact_id, rsvp_payload)
        except Exception as e:
            print("SQLite RSVP update error:", e)

    # 2. Update JSON fallback
    form_payload = {
        "confirmed_name": contact["name"],
        "confirmed_phone": contact.get("phone", ""),
        "is_married": bool(state.get("is_married")),
        "partner_name": state.get("spouse_name", ""),
        "partner_phone": "",
        "children": state.get("children", []),
        "other_members": []
    }
    
    # Add parents into other_members if present
    for p in state.get("parents_names", []):
        form_payload["other_members"].append({"name": p, "relation": "Parents / વડીલ", "phone": ""})

    for om in state.get("other_members", []):
        form_payload["other_members"].append(om)

    try:
        submit_relative_details(contact["token"], form_payload)
    except Exception:
        pass

    # Spawn separate adult child branch if provided!
    for child in state.get("separate_adult_children", []):
        ch_phone = child.get("phone", "")
        ch_name = child.get("name", f"{contact['name']}'s Son/Daughter")
        if ch_phone:
            side = contact.get("side", "BRIDE")
            added_by = contact.get("added_by", "FATHER")
            cat = contact.get("category", "kaka")
            notes = f"Branch from {contact['name']} ({cat.title()})"

            wedding_id = contact.get("wedding_id")
            if wedding_id:
                try:
                    add_contact_db(wedding_id=wedding_id, side=side, added_by=added_by, category=cat, name=ch_name, phone=ch_phone, notes=notes)
                except Exception as e:
                    print("Error spawning separate branch in SQLite:", e)

            # Check if not already added to JSON fallback
            data = load_data()
            already = False
            for c in data.get("contacts", []):
                if c.get("phone", "")[-10:] == ch_phone[-10:]:
                    already = True
                    break
            if not already:
                add_contact(side=side, added_by=added_by, category=cat, name=ch_name, phone=ch_phone, notes=notes)
                print(f"Spawned new family branch: {ch_name} ({ch_phone}) under {contact['name']}")

# -------------------------------------------------------------
# PARENT RELATIVES COLLECTOR ASSISTANT
# Allows Father & Mother to talk with AI to list their relatives
# -------------------------------------------------------------
def get_or_create_parent_collector_session(parent_info):
    token = parent_info["token"]
    session_key = f"collector_{token}"
    convs = load_conversations()
    if session_key in convs:
        return convs[session_key]

    role_gu = "પિતાશ્રી (પપ્પા)" if parent_info["role"] == "FATHER" else "માતાશ્રી (મમ્મી)"
    side_gu = "પિતા પક્ષ" if parent_info["role"] == "FATHER" else "માતા પક્ષ (પિયર પક્ષ)"
    
    if parent_info["role"] == "MOTHER":
        initial_message = (
            f"જય શ્રી કૃષ્ણ પૂજ્ય {parent_info['name']} માતાશ્રી!\n\n"
            f"લગ્ન પ્રસંગ માટે આપના પિયર પક્ષ અને કુટુંબના તમામ સંબંધીઓની યાદી તૈયાર કરવા હું આપનો સહાયક છું.\n\n"
            f"નીચે આપેલા વિકલ્પોમાંથી પસંદ કરી અથવા સીધા જ નામ અને નંબર લખીને મોકલી શકો છો:\n"
            f"• 'મામા રમેશભાઈ 9825012345'\n"
            f"• 'માસી ગીતાબેન 9824012345'\n"
            f"• 'દીકરો ઝરવીન 9265834826'\n\n"
            f"હું આપોઆપ એમને ગેસ્ટ લિસ્ટમાં યોગ્ય કેટેગરીમાં ઉમેરી દઈશ. શરૂ કરવા માટે નીચે આપેલા બટન પર ક્લિક કરો અથવા નામ લખો."
        )
        quick_replies = [
            "મામાશ્રી (પિયર પક્ષ)", "માસી (મારી બહેનો)", "મારાં સંતાનો",
            "કાકી / મામી", "મારી સખીઓ / મિત્રો", "બધા આવી ગયા છે (Done)"
        ]
    else:
        initial_message = (
            f"જય શ્રી કૃષ્ણ પૂજ્ય {parent_info['name']} પિતાશ્રી!\n\n"
            f"લગ્ન પ્રસંગ માટે આપના કુટુંબ, સગા-સંબંધીઓ અને મિત્રોની યાદી તૈયાર કરવા હું આપનો સહાયક છું.\n\n"
            f"નીચે આપેલા વિકલ્પોમાંથી પસંદ કરી અથવા સીધા જ નામ અને નંબર લખીને મોકલી શકો છો:\n"
            f"• 'ભાઈ રમેશભાઈ 9825012345'\n"
            f"• 'કાકા મહેશભાઈ 9824012345 અને ફોઈ હંસાબેન 9879012345'\n"
            f"• 'મિત્ર મુકેશભાઈ 9924012345'\n\n"
            f"હું આપોઆપ એમને ગેસ્ટ લિસ્ટમાં યોગ્ય કેટેગરીમાં ઉમેરી દઈશ. શરૂ કરવા માટે નીચે આપેલા બટન પર ક્લિક કરો અથવા નામ લખો."
        )
        quick_replies = [
            "મારા ભાઈઓ", "મારા કાકા / કુટુંબ", "મારાં ફોઈ / ફુવા",
            "મારાં સંતાનો", "મારા ખાસ મિત્રો", "સહકર્મચારી", "બધા આવી ગયા છે (Done)"
        ]

    session = {
        "token": token,
        "role": parent_info["role"],
        "side": parent_info["side"],
        "name": parent_info["name"],
        "messages": [
            {
                "sender": "bot",
                "text": initial_message,
                "timestamp": datetime.now().strftime("%I:%M %p"),
                "quick_replies": quick_replies
            }
        ]
    }
    convs[session_key] = session
    save_conversations(convs)
    return session

def process_parent_collector_message(parent_info, user_message):
    if isinstance(parent_info, str):
        token = parent_info
        p_db = get_parent_by_token(token)
        parent_info = p_db if p_db else get_parent_info_by_token(token)
    else:
        token = parent_info.get("token")
        if not parent_info.get("wedding_id"):
            p_db = get_parent_by_token(token)
            if p_db:
                parent_info = p_db

    session_key = f"collector_{token}"
    convs = load_conversations()
    session = convs.get(session_key) or get_or_create_parent_collector_session(parent_info)

    # Append user message
    session["messages"].append({
        "sender": "user",
        "text": user_message,
        "timestamp": datetime.now().strftime("%I:%M %p")
    })
    try:
        log_chat_message_db(token, "user", user_message)
    except Exception:
        pass

    api_key = get_gemini_api_key()
    parsed_relatives = []
    bot_reply = ""
    is_finished = False

    clean_msg = user_message.strip()
    low_msg = clean_msg.lower()
    
    # Token Protection Guardrails:
    invalid_streak = session.get("invalid_streak", 0)
    total_ai_calls = session.get("total_ai_calls", 0)

    # 1. Zero-Token Filter: Greetings
    is_greeting = any(g in low_msg for g in ["kem cho", "namaste", "hello", "hi", "halo", "jay shree krishna", "tame kon", "su karo cho", "kem chho"])
    # 2. Zero-Token Filter: Completion phrases
    is_done = is_completion_phrase(clean_msg)
    # 3. Zero-Token Filter: Quick Category Button clicks
    is_cat_pill = any(c in clean_msg for c in ["મામાશ્રી", "પિયર પક્ષ", "મારી બહેનો", "મારાં સંતાનો", "કાકી", "મામી", "સખીઓ", "મારા ભાઈઓ", "કુટુંબ", "ફોઈ", "ફુવા", "સહકર્મચારી", "ખાસ મિત્રો"])

    role_lbl = "પિતાશ્રી" if parent_info.get("role") == "FATHER" else "માતાશ્રી"
    parent_display = parent_info.get("name", "Parent")

    # Fetch existing contacts for this wedding so Gemini knows who is already on the list
    wedding_id = parent_info.get("wedding_id", 1)
    existing_contacts = get_contacts_by_wedding(wedding_id)
    contact_list_summary = [{"name": c["name"], "phone": c["phone"] or "", "category": c.get("category", "")} for c in existing_contacts]

    parsed_updates = []
    parsed_removals = []

    # 100% LLM Engine (Gemini with Groq open-source Llama 3.3 backup)
    try:
        session["total_ai_calls"] = total_ai_calls + 1
        parsed_relatives, bot_reply, is_finished, parsed_updates, parsed_removals = call_gemini_for_parent_collector(
            parent_info, user_message, session["messages"], existing_contacts=contact_list_summary
        )
        session["invalid_streak"] = 0
    except Exception as e:
        print("[*] All LLM engines failed or offline in parent collector:", e)
        bot_reply = f"ક્ષમા કરશો પૂજ્ય {parent_display} {role_lbl}! સર્વર પર થોડી ક્ષણો માટે કનેક્શન વ્યસ્ત છે. કૃપા કરીને થોડી સેકંડ પછી ફરી મેસેજ કરશો. 🙏"
        parsed_relatives = []
        parsed_updates = []
        parsed_removals = []
        is_finished = False

    # Save session state updates
    session_key = f"collector_{token}"
    convs[session_key] = session
    save_conversations(convs)

    # 1. Process Updates & Corrections (e.g., "Rameshbhai no number 9825099887 che")
    for upd in parsed_updates:
        target = upd.get("target") or upd.get("target_name_or_phone")
        if target:
            update_contact_details_db(
                wedding_id=wedding_id,
                target_name_or_phone=target,
                new_name=upd.get("new_name"),
                new_phone=upd.get("new_phone"),
                new_notes=upd.get("new_notes")
            )

    # 2. Process Removals (e.g., "Rameshbhai nahi aavshe emne kadhi nakho")
    for rem in parsed_removals:
        if rem:
            delete_contact_db(wedding_id, rem)

    # 3. Add each newly extracted relative to contacts in SQLite database
    added_now = []
    groom = parent_info.get("groom_name", "Groom")
    bride = parent_info.get("bride_name", "Bride")
    base = get_effective_base_url()

    for rel in parsed_relatives:
        rel_name = rel.get("name", "").strip()
        rel_phone = rel.get("phone", "").strip()
        rel_cat = rel.get("category", "kaka").lower()
        rel_notes = rel.get("notes", "").strip()
        if not rel_notes:
            rel_notes = f"Added by {parent_info['role'].title()} ({parent_info['name']})"

        if rel_name and len(rel_name) >= 2:
            new_c = add_contact_db(
                wedding_id=wedding_id,
                side=parent_info["side"],
                added_by=parent_info["role"],
                category=rel_cat,
                name=rel_name,
                phone=rel_phone,
                notes=rel_notes
            )
            # Sync to JSON fallback
            try:
                add_contact(
                    side=parent_info["side"],
                    added_by=parent_info["role"],
                    category=rel_cat,
                    name=rel_name,
                    phone=rel_phone,
                    notes=rel_notes
                )
            except Exception:
                pass

            if new_c:
                added_now.append(new_c)
                # 100% AUTOPILOT: Automatically dispatch WhatsApp invitation to relative
                guest_phone = new_c.get("phone")
                guest_token = new_c.get("token")
                if guest_phone and guest_token:
                    chat_url = f"{base}/chat/{guest_token}"
                    invite_text = (
                        f"જય શ્રી કૃષ્ણ {rel_name}!\n\n"
                        f"{groom} અને {bride} ના શુભ લગ્ન પ્રસંગે આપનું અને આપના પરિવારનું હાર્દિક સ્વાગત છે.\n\n"
                        f"લગ્નમાં આપના પરિવારમાંથી કોણ કોણ પધારવાના છો તેની વિગત આપવા માટે નીચે આપેલી લિંક પર ક્લિક કરશો:\n\n"
                        f"{chat_url}\n\n"
                        f"(નોંધ: જો લિંક વાદળી/ક્લિક ન થાય, તો આ નંબર સેવ કરો અથવા આ મેસેજ પર 'હા' લખીને મોકલો, જેથી લિંક ખૂલી જશે.)\n\n"
                        f"આપના સ્નેહીજનો તરફથી"
                    )
                    b_res, b_code = send_baileys_message(guest_phone, invite_text)
                    print(f"[*] Relative WhatsApp dispatch to {guest_phone}: HTTP {b_code}, result: {b_res}")

    session["is_completed"] = is_finished
    quick_replies = ["હજુ બીજા સગા-વહાલા ઉમેરો", "બધા આવી ગયા છે (Done)"] if not is_finished else ["નવા નામ ઉમેરો"]
    session["messages"].append({
        "sender": "bot",
        "text": bot_reply,
        "timestamp": datetime.now().strftime("%I:%M %p"),
        "quick_replies": quick_replies
    })
    try:
        log_chat_message_db(token, "bot", bot_reply)
    except Exception:
        pass

    convs[session_key] = session
    save_conversations(convs)

    return {
        "reply": bot_reply,
        "added_now": added_now,
        "is_finished": is_finished,
        "messages": session["messages"]
    }

COMPLETION_KEYWORDS = [
    "done", "complete", "completed", "finish", "finished", "all done",
    "aavi gaya", "avi gaya", "aavi gya", "avi gya", "aavigaya", "avigaya",
    "badha", "badha aavi", "badha avi", "badha aai", "badha parivarjano",
    "koi nathi", "koi baki nathi", "koi sadasya nathi", "sadasya nathi",
    "naa aavi gaya", "na aavi gaya", "na aavi", "aatla j", "bas aatla", "patyu",
    "બધા આવી ગયા", "આવી ગયા", "કોઈ નથી", "કોઈ બાકી નથી", "પતી ગયું", "બધા", "બસ આટલા", "કોઈ સદસ્ય નથી"
]

def is_completion_phrase(text):
    if not text:
        return False
    low = text.strip().lower()
    return any(p in low for p in COMPLETION_KEYWORDS)

def call_gemini_for_parent_collector(parent_info, user_message, conversation_history, existing_contacts=None):
    api_key = get_gemini_api_key()
    candidate_models = ACTIVE_GEMINI_MODELS

    role_label = "પિતાશ્રી (પપ્પા)" if parent_info.get("role") == "FATHER" else "માતાશ્રી (મમ્મી)"
    side_label = "પિતા પક્ષ" if parent_info.get("role") == "FATHER" else "માતા પક્ષ"
    parent_name = parent_info.get("name", "Parent")
    groom = parent_info.get("groom_name", "").strip() or "વરરાજા"
    bride = parent_info.get("bride_name", "").strip() or "કન્યા"

    contacts_summary = []
    if existing_contacts:
        for c in existing_contacts[-25:]:
            c_name = c.get("name", "")
            c_phone = c.get("phone", "")
            c_cat = c.get("category", "")
            contacts_summary.append(f"- {c_name} ({c_cat}): {c_phone or 'No number'}")
    existing_contacts_str = "\n".join(contacts_summary) if contacts_summary else "None added yet."

    system_prompt = f"""
You are an intelligent, polite, highly respectful Gujarati Wedding Concierge AI Chatbot for {groom} & {bride}'s royal wedding.
You are conversing in real-time with {parent_name} ({role_label}) on the {side_label} side.

YOUR MISSION:
Manage their guest and relative list completely naturally through conversation.
The user may write in Gujarati, English, Gujlish (casual Gujarati in English alphabets like "mara kaka rameshbhai no number 9825012345"), or Hinglish ("kaka ka number galat hai").

ALREADY RECORDED RELATIVES ON THIS LIST:
{existing_contacts_str}

USER'S LATEST MESSAGE:
"{user_message}"

CONVERSATION HISTORY:
{json.dumps([{'sender': m['sender'], 'text': m['text']} for m in conversation_history[-8:]], ensure_ascii=False)}

INTELLIGENCE & MULTILINGUAL CONVERSATION RULES:
1. ADDING NEW PEOPLE ("relatives" list):
   - When user provides new relatives/friends (e.g., "chokro zarvin 9265834826", "maro chokro tirth", "kaka rameshbhai 9825012345"):
     Extract their clean name (e.g. "Zarvin"), 10-digit mobile number, category ('brothers'|'sisters'|'kaka'|'fai'|'mama'|'masi'|'friends'|'colleagues'|'neighbours'|'parents'), and notes (e.g. "પુત્ર / દીકરો").
   - If user provides an incomplete phone number (e.g. 8 digits like "Rameshbhai 98250123" or 12 digits):
     DO NOT save the incomplete number! Put phone as "". In your reply, politely and respectfully ask for the complete 10-digit mobile number.

2. SELF-CORRECTIONS & UPDATES ("updates" list):
   - Users often make mistakes or typos and follow up with a correction:
     * e.g., "sorry number ma bhul thai gai, rameshbhai no number 9825099887 che"
     * e.g., "arre number galat dal diya rameshbhai ka 9825099887 likho"
     * e.g., "correction: please update his number to 9825099887"
     * e.g., "naam ma bhul che, ramesh nahi mukesh che"
   - Match the target person from the existing list or conversation history.
   - Return in "updates": [{{"target": "Rameshbhai", "new_name": "Rameshbhai", "new_phone": "9825099887", "new_notes": "કાકા"}}]
   - Confirm the correction warmly in your reply: "કોઈ વાંધો નહીં પૂજ્ય {parent_name}! રમેશભાઈનો નંબર સુધારીને 9825099887 કરી દીધો છે. 👍"

3. REMOVING PEOPLE ("removals" list):
   - If user says someone cannot come or should be removed (e.g. "rameshbhai nahi aavshe emne kadhi nakho / remove", "delete"):
     Return in "removals": ["Rameshbhai"]
     Confirm warmly in reply.

4. POLITE, RESPECTFUL GUJARATI TONE:
   - Always address parent as "પૂજ્ય {parent_name} {role_label}! 🙏".
   - If user selects a category button without names (e.g. 'મારા કાકા', 'સંતાનો'):
     Warmly invite them to share names and numbers.
   - When everything is complete ("done", "badha aavi gaya", "patyu", "koi baki nathi", "all done", "બધા આવી ગયા"):
     Set "is_finished": true.
     Give a heartfelt Gujarati wedding blessing: "🌸 ખૂબ ખૂબ આભાર પૂજ્ય {parent_name} {role_label}! આપના તમામ સંબંધીઓની યાદી સફળતાપૂર્વક તૈયાર થઈ ગઈ છે! લગ્નમાં આપ સૌના આશીર્વાદ સદાય {groom} અને {bride} પર રહે એવી પ્રાર્થના! 💐"

5. CRITICAL REQUIREMENT: STRICT ENGLISH NAMES IN DATABASE:
   - All names in "relatives" ("name"), "updates" ("new_name"), and "removals" MUST be output in clean English (Latin alphabet) with proper Title Case (e.g. "Rameshbhai", "Mukeshbhai", "Zarvin", "Rekhaben", "Kaushikbhai").
   - Even if the parent typed in Gujarati script (e.g. 'રમેશભાઈ' or 'કાકા મુકેશ'), you MUST convert it to standard English ("Rameshbhai", "Mukeshbhai") so that the host can effortlessly search and filter names in the dashboard and Excel export!

OUTPUT FORMAT (STRICT JSON ONLY):
{{
  "relatives": [
    {{
      "name": "Full name in English Title Case (e.g. 'Rameshbhai', 'Mukeshbhai')",
      "phone": "Verified 10-digit number or empty string",
      "category": "brothers|sisters|kaka|fai|mama|masi|friends|colleagues|neighbours|parents",
      "notes": "Relation in Gujarati/English"
    }}
  ],
  "updates": [
    {{
      "target": "Existing name or phone being corrected",
      "new_name": "Corrected name in English Title Case",
      "new_phone": "Corrected 10-digit phone",
      "new_notes": "Notes if any"
    }}
  ],
  "removals": ["Name of person to remove"],
  "is_finished": false,
  "reply": "Warm, respectful, conversational Gujarati reply written by you"
}}
"""

    payload = {
        "contents": [{"parts": [{"text": system_prompt}]}],
        "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"}
    }

    last_error = None
    for model_name in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200:
                result_json = resp.json()
                raw_text = result_json["candidates"][0]["content"]["parts"][0]["text"]
                data = json.loads(raw_text)
                return (
                    data.get("relatives", []),
                    data.get("reply", "સંબંધીઓની વિગત સેવ થઈ ગઈ છે."),
                    data.get("is_finished", False),
                    data.get("updates", []),
                    data.get("removals", [])
                )
            else:
                last_error = f"{model_name} HTTP {resp.status_code}"
        except Exception as e:
            last_error = f"{model_name}: {str(e)}"

    # 2. Instant Failover to Groq Open-Source LLM (Llama 3.3 70B / Llama 3.1 8B)
    groq_k = get_groq_api_key()
    if groq_k:
        try:
            print("[*] Gemini parent collector failed. Failing over to Groq Llama 3.3 70B...")
            groq_res = call_groq_llm(system_prompt, f"User message: {user_message}")
            return (
                groq_res.get("relatives", []),
                groq_res.get("reply", "સંબંધીઓની વિગત સેવ થઈ ગઈ છે."),
                groq_res.get("is_finished", False),
                groq_res.get("updates", []),
                groq_res.get("removals", [])
            )
        except Exception as groq_err:
            print("[*] Groq failover error in parent collector:", groq_err)

    raise Exception(f"Gemini collector failed: {last_error}")

def fallback_parent_collector_parser(parent_info, user_text):
    import re
    text = user_text.strip()
    role_label = "પિતાશ્રી" if parent_info.get("role") == "FATHER" else "માતાશ્રી"
    parent_name = parent_info.get("name", "Parent")
    low_text = text.lower()
    
    # 1. Check completion
    if is_completion_phrase(text):
        reply = (
            f"🌸 ખૂબ ખૂબ આભાર પૂજ્ય {parent_name} {role_label}! 🙏\n"
            f"આપના તમામ પરિવારજનો અને સંબંધીઓની યાદી સંપૂર્ણ થઈ ગઈ છે.\n"
            f"લગ્નના શુભ પ્રસંગે આપ સૌના આશીર્વાદ સદાય વર-વધૂ પર રહે! 💐✨\n"
            f"(જો ભવિષ્યમાં કોઈ અન્ય નામ યાદ આવે તો આપ ગમે ત્યારે અહીં મેસેજ કરી શકો છો.)"
        )
        return [], reply, True

    # 2. Check general greetings
    if any(g in low_text for g in ["kem cho", "namaste", "hello", "hi", "halo", "jay shree krishna", "tame kon"]):
        reply = (
            f"જય શ્રી કૃષ્ણ પૂજ્ય {parent_name} {role_label}! 🙏\n"
            f"હું આપના લગ્ન પ્રસંગના સંબંધીઓની યાદી તૈયાર કરવામાં સહાયક છું.\n\n"
            f"આપ આપના સંબંધીઓ, ભાઈઓ, બહેનો, કાકા, મામા, દીકરા-દીકરી કે મિત્રોના નામ અને મોબાઈલ નંબર જણાવશો?"
        )
        return [], reply, False

    # 3. Parse lines or comma/conjunction separated chunks
    chunks = re.split(r'[\n,]|(?:\s+ane\s+)|\band\b', text, flags=re.IGNORECASE)
    relatives = []
    
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or len(chunk) < 2:
            continue
            
        # Robust phone extraction supporting +91, 0, spaces, dashes
        phone_match = re.search(r'(?:(?:\+|0{0,2})91[\s-]?)?([6-9]\d{4}[\s-]?\d{5})\b', chunk)
        if phone_match:
            raw_p = phone_match.group(1)
            phone = re.sub(r'\D', '', raw_p)
            cleaned = chunk[:phone_match.start()] + ' ' + chunk[phone_match.end():]
        else:
            phone_match2 = re.search(r'\b[6-9]\d{9}\b', chunk)
            phone = phone_match2.group(0) if phone_match2 else ""
            cleaned = re.sub(r'\b[6-9]\d{9}\b', '', chunk) if phone_match2 else chunk

        cleaned = cleaned.strip()
        low = cleaned.lower()
        
        if is_completion_phrase(low):
            continue
            
        category = "friends"
        notes = "સંબંધી"
        has_relation_keyword = False

        if any(k in low for k in ["chokro", "dikro", "son", "putra"]):
            category = "brothers"; notes = "પુત્ર (Son)"; has_relation_keyword = True
        elif any(k in low for k in ["chokri", "dikri", "daughter", "putri"]):
            category = "sisters"; notes = "પુત્રી (Daughter)"; has_relation_keyword = True
        elif any(k in low for k in ["wife", "patni"]):
            category = "brothers"; notes = "પત્ની (Wife)"; has_relation_keyword = True
        elif any(k in low for k in ["kaka", "uncle"]):
            category = "kaka"; notes = "કાકા"; has_relation_keyword = True
        elif any(k in low for k in ["fai", "foi", "bui"]):
            category = "fai"; notes = "ફોઈ"; has_relation_keyword = True
        elif any(k in low for k in ["mama"]):
            category = "mama"; notes = "મામા"; has_relation_keyword = True
        elif any(k in low for k in ["masi"]):
            category = "masi"; notes = "માસી"; has_relation_keyword = True
        elif any(k in low for k in ["ben", "sister", "behn"]):
            category = "sisters"; notes = "બહેન"; has_relation_keyword = True
        elif any(k in low for k in ["bhai", "brother"]):
            category = "brothers"; notes = "ભાઈ"; has_relation_keyword = True
        elif any(k in low for k in ["dada", "dadi", "nana", "nani"]):
            category = "parents"; notes = "વડીલ"; has_relation_keyword = True
        elif any(k in low for k in ["bhatrijo", "bhanej"]):
            category = "brothers"; notes = "ભત્રીજો / ભાણેજ"; has_relation_keyword = True
        elif any(k in low for k in ["friend", "mitra", "dost"]):
            category = "friends"; notes = "મિત્ર"; has_relation_keyword = True
            
        # Clean relational filler words from name
        name = re.sub(r'\b(mara|mari|maro|chokro|dikro|dikri|chokri|son|daughter|wife|patni|kaka|fai|foi|mama|masi|bhai|ben|mitra|friend|no|nu|na|naam|name|che|chhe|eno|emno|number|mobile|phone)\b', '', cleaned, flags=re.IGNORECASE).strip()
        name = re.sub(r'[^a-zA-Z\u0A80-\u0AFF\s]', '', name).strip()
        
        # Follow-up message handling: user sent just a phone number or "no number XXXXX"
        if (not name or len(name) < 2) and phone:
            wid = parent_info.get("wedding_id", 1)
            role = parent_info.get("role", "FATHER")
            conn_t = get_db()
            c_t = conn_t.cursor()
            c_t.execute("""
                SELECT * FROM contacts 
                WHERE wedding_id = ? AND added_by = ? AND (phone IS NULL OR phone = '')
                ORDER BY id DESC LIMIT 1
            """, (wid, role))
            last_no_phone = c_t.fetchone()
            conn_t.close()
            if last_no_phone:
                name = last_no_phone["name"]
                category = last_no_phone["category"]
                notes = last_no_phone["notes"] or "સંબંધી"

        # Valid if has a name and either a phone or a recognized relation keyword
        if len(name) >= 2 and (phone or has_relation_keyword):
            from guest_manager import BLOCKED_CONTACT_NAMES
            if not any(b in name.lower() for b in BLOCKED_CONTACT_NAMES):
                relatives.append({
                    "name": name.title(),
                    "phone": phone,
                    "category": category,
                    "notes": notes
                })

    if relatives:
        names_str = ", ".join([f"{r['name']}{(' (' + r['phone'] + ')') if r['phone'] else ''}" for r in relatives])
        reply = (
            f"ખૂબ સરસ પૂજ્ય {parent_name}!\n"
            f"{names_str} ની વિગત યાદીમાં નોંધી લીધી છે.\n\n"
            f"હવે આપના તરફથી બીજા કોઈ ભાઈઓ, બહેનો, કાકા, મામા, ફોઈ, માસી કે મિત્રો બાકી છે જેમના નામ નોંધવાના હોય?\n\n"
            f"(જ્યારે બધા સંબંધીઓના નામ લખાઈ જાય ત્યારે ફક્ત 'બધા આવી ગયા' બટન દબાવશો.)"
        )
    else:
        # Check if user selected one of the category options
        category_prompts = [
            (["મામા", "mama", "પિયર"], "પિયર પક્ષના ભાઈઓ (મામાશ્રી)"),
            (["માસી", "masi"], "માસીશ્રીઓ"),
            (["સંતાન", "દીકરા", "દીકરી", "chokro", "dikro", "dikri", "son", "daughter"], "દીકરા / દીકરી"),
            (["કાકી", "મામી", "kaki", "mami"], "કાકી / મામી"),
            (["સખી", "sakhi"], "સખીઓ"),
            (["ભાઈ", "bhai", "brother"], "ભાઈઓ"),
            (["કાકા", "કુટુંબ", "kaka"], "કાકા અને કુટુંબના સભ્યો"),
            (["ફોઈ", "ફુવા", "foi", "fai", "fua"], "ફોઈ અને ફુવા"),
            (["મિત્ર", "mitra", "dost", "friend"], "ખાસ મિત્રો"),
            (["સહકર્મચારી", "વ્યવસાયિક", "colleague", "office"], "સહકર્મચારીઓ")
        ]
        matched_cat = None
        for keywords, cat_label in category_prompts:
            if any(k in low_text for k in keywords):
                matched_cat = cat_label
                break
                
        if matched_cat:
            reply = (
                f"સરસ પૂજ્ય {parent_name} {role_label}! 🙏\n\n"
                f"આપના {matched_cat} ના નામ અને મોબાઈલ નંબર જણાવશો.\n\n"
                f"👉 દા.ત.: 'રમેશભાઈ 9825012345' અથવા એક સાથે બધાના નામ લખીને મોકલી શકો છો. 😊"
            )
        else:
            reply = (
                f"ક્ષમા કરશો પૂજ્ય {parent_name}! વિગત સ્પષ્ટ સમજાઈ નથી. કૃપા કરીને નીચે મુજબ જણાવશો:\n\n"
                f"👉 દા.ત. 'દીકરો ઝરવીન 9265834826' અથવા 'ભાઈ તીર્થ 8103013148'\n\n"
                f"(અને બધા ઉમેરાઈ ગયા હોય ત્યારે 'DONE' અથવા 'બધા આવી ગયા' લખશો.)"
            )
        
    return relatives, reply, False

