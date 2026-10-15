import json
import os
import re
import requests
import urllib.request
import urllib.error
from datetime import datetime
from guest_manager import load_data, save_data, submit_relative_details, add_contact, get_parent_info_by_token
from database import (
    add_contact_db, get_contact_by_token_db, get_parent_by_token,
    update_rsvp_db, log_chat_message_db, get_chat_history_db
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

def call_gemini_llm(api_key, contact, conversation_history, user_message, current_state):
    """
    Calls Google Gemini Flash to parse natural Gujlish/Hindi/English input
    and return structured updates + warm conversational response.
    Supports auto model fallback (gemini-2.5-flash -> gemini-2.0-flash).
    """
    candidate_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    
    system_prompt = f"""
You are an intelligent, polite, and warm Gujarati Wedding Hospitality Concierge Chatbot assisting {contact['name']} (Relation: {contact.get('added_by', '')} side {contact.get('category', '')}).
Your mission is to accurately understand who and how many people from their family are attending the wedding.

We need to collect:
1. Parents (Maa-Baap / elders) attending with them (names if yes).
2. Are they married? (Spouse/Partner name if yes).
3. Do they have children attending? (Names of children; phone numbers are strictly optional!).
4. If they mention another branch or adult son's family (e.g. "mara chokra tirth nu parivar" or "tirth's family"):
   CRITICAL: DO NOT treat "mara chokra tirth nu parivar" as a single person's name!
   Instead, ask who is in that family (wife/kids names) or ask for their mobile number so a separate invitation can be sent!
5. Any other family member living with them?

Rules for Natural Conversation:
- Understand natural informal Gujlish (e.g., "ha mara 2 chokrao che", "mara bapa nathi aavta", "haa", "na", "wife rekha", "chokra nu parivar").
- If the user says "haa" to separate invite but hasn't given the number yet, ask for their mobile number politely!
- Keep your bot replies warm, friendly, celebratory in simple Gujarati / Gujlish.
- Set "is_complete": true ONLY when all questions are answered and individual names are collected.

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
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200:
                result_json = resp.json()
                raw_text = result_json["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(raw_text)
            else:
                last_error = f"{model_name} HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            last_error = str(e)
            
    raise Exception(f"All Gemini models failed. Last error: {last_error}")

def fallback_rule_based_parser(contact, session, user_text):
    """
    Intelligent Gujlish rule-based state machine used when Gemini API key is not yet configured.
    Handles 'ha', 'na', 'che', 'nathi', spouse names, children, and adult son numbers.
    """
    text = user_text.strip().lower()
    step = session.get("step", "ASK_PARENTS")
    state = session.get("state", {})
    
    bot_reply = ""
    quick_replies = []
    next_step = step

    is_yes = any(w in text for w in ["ha", "haa", "yes", "che", "h", "aavshe", "avse", "avsu", "avisu", "sure"])
    is_no = any(w in text for w in ["na", "naa", "no", "nathi", "nahi", "none", "no one", "single"])

    # Direct headcount pattern (e.g. '4 લોકો', '3 જણા', '4 members', '2 person')
    headcount_match = re.search(r'(\d+)\s*(?:people|members|person|જના|જણા|લોકો|સભ્યો|વ્યક્તિ|jna|loko|sabyo)', text, re.IGNORECASE)
    if headcount_match:
        count = int(headcount_match.group(1))
        state["total_headcount"] = max(count, 1)
        return finalize_chat_summary(contact, session, state)

    if step == "ASK_PARENTS":
        if is_yes:
            state["parents_attending"] = True
            bot_reply = "ખૂબ સરસ! આપના માતા-પિતા / વડીલોનું નામ જણાવશો? (Please share parents' names):"
            next_step = "GET_PARENTS_NAME"
        else:
            state["parents_attending"] = False
            bot_reply = "બરાબર છે. હવે જણાવશો, શું આપ પરણેલા છો (Are you married)?"
            quick_replies = ["હા (Yes)", "ના (No)"]
            next_step = "ASK_MARRIED"

    elif step == "GET_PARENTS_NAME":
        names = [n.strip() for n in user_text.replace("and", ",").replace("ane", ",").replace("&", ",").split(",") if n.strip()]
        for n in names:
            state["parents_names"].append(n)
            state["total_headcount"] += 1
        bot_reply = f"સરસ, {', '.join(names)} ની નોંધણી થઈ ગઈ છે.\n\nહવે જણાવશો, શું આપ પરણેલા છો (Are you married)?"
        quick_replies = ["હા (Yes)", "ના (No)"]
        next_step = "ASK_MARRIED"

    elif step == "ASK_MARRIED":
        if is_yes:
            state["is_married"] = True
            state["total_headcount"] += 1
            bot_reply = "આપના જીવનસાથી (પત્ની/પતિ - Spouse) નું નામ જણાવશો:"
            next_step = "GET_SPOUSE_NAME"
        else:
            state["is_married"] = False
            bot_reply = "બરાબર. શું આપની સાથે અન્ય કોઈ પરિવારજન (ભાઈ, બહેન કે અન્ય વડીલ) પધારશે?"
            quick_replies = ["ના (બસ હું એકલો/એકલી)", "હા (+ Add Member)"]
            next_step = "ASK_OTHER_FOR_SINGLE"

    elif step == "GET_SPOUSE_NAME":
        state["spouse_name"] = user_text.strip()
        bot_reply = f"આભાર! {user_text.strip()} નું નામ નોંધી લીધું છે. 👍\n\nશું આપને બાળકો છે (Do you have children)?"
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
        "updated_state": state
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

    if not state.get("total_headcount") or state["total_headcount"] <= 1:
        state["total_headcount"] = len(members_summary)
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
        "updated_state": state
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

    # Check for direct one-shot headcount confirmation first (e.g. '4 લોકો', '3 જણા')
    headcount_match = re.search(r'(\d+)\s*(?:people|members|person|જના|જણા|લોકો|સભ્યો|વ્યક્તિ|jna|loko|sabyo)', user_message, re.IGNORECASE)
    if headcount_match:
        count = int(headcount_match.group(1))
        state = session.get("state", {})
        state["total_headcount"] = max(count, 1)
        bot_result = finalize_chat_summary(contact, session, state)
    elif api_key:
        try:
            bot_result = call_gemini_llm(api_key, contact, session["messages"], user_message, session["state"])
            session["state"] = bot_result.get("updated_state", session["state"])
        except Exception as e:
            print("Gemini API call failed, falling back to rule parser:", e)
            bot_result = fallback_rule_based_parser(contact, session, user_message)
    else:
        bot_result = fallback_rule_based_parser(contact, session, user_message)

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
        # Auto-commit to guest list
        commit_chat_to_guest_list(contact, final_state)

    convs[token] = session
    save_conversations(convs)

    return {
        "bot_reply": bot_entry["text"],
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
            # Check if not already added
            data = load_data()
            already = False
            for c in data.get("contacts", []):
                if c.get("phone", "")[-10:] == ch_phone[-10:]:
                    already = True
                    break
            if not already:
                side = contact.get("side", "BRIDE")
                added_by = contact.get("added_by", "FATHER")
                cat = contact.get("category", "kaka")
                notes = f"Branch from {contact['name']} ({cat.title()})"
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
    side_gu = "Father's Side (પિતા પક્ષ)" if parent_info["role"] == "FATHER" else "Mother's Side (માતા પક્ષ)"
    
    initial_message = (
        f"💍 જય શ્રી કૃષ્ણ {parent_info['name']} {role_gu}! 🙏\n\n"
        f"લગ્ન પ્રસંગ માટે આપના {side_gu} ના તમામ સંબંધીઓની યાદી તૈયાર કરવા માટે હું આપનો સહાયક છું.\n\n"
        f"આપ આપના સંબંધીઓ (આપના ભાઈઓ, બહેનો, કાકા, ફોઈ, મામા, માસી, મિત્રો વગેરે) ના નામ અને મોબાઈલ નંબર અહીં જણાવો.\n\n"
        f"દાખલા તરીકે:\n"
        f"• 'મારા ભાઈ રમેશભાઈ 9825012345'\n"
        f"• 'કાકા મહેશભાઈ 9824012345 અને ફોઈ હંસાબેન 9879012345'\n"
        f"• 'મિત્ર મુકેશભાઈ 9924012345'\n\n"
        f"હું આપોઆપ એમને ગેસ્ટ લિસ્ટમાં યોગ્ય કેટેગરીમાં ઉમેરી દઈશ! શરૂ કરવા માટે જણાવશો, આપના ભાઈઓ કે કાકા કોણ કોણ છે?"
    )

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
                "quick_replies": ["મારા ભાઈઓનાં નામ ઉમેરો", "મારા કાકા / ફોઈ નાં નામ", "મારા ખાસ મિત્રોનાં નામ"]
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

    if api_key:
        try:
            parsed_relatives, bot_reply, is_finished = call_gemini_for_parent_collector(parent_info, user_message, session["messages"])
        except Exception as e:
            print("Gemini parent collector fallback:", e)
            parsed_relatives, bot_reply, is_finished = fallback_parent_collector_parser(parent_info, user_message)
    else:
        parsed_relatives, bot_reply, is_finished = fallback_parent_collector_parser(parent_info, user_message)

    # Add each extracted relative to contacts in SQLite database
    added_now = []
    wedding_id = parent_info.get("wedding_id", 1)
    groom = parent_info.get("groom_name", "Groom")
    bride = parent_info.get("bride_name", "Bride")
    base = os.environ.get("BASE_URL", "http://127.0.0.1:5000")

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
                        f"💍 જય શ્રી કૃષ્ણ {rel_name}! 🙏\n\n"
                        f"{groom} અને {bride} ના શુભ લગ્ન પ્રસંગે આપનું અને આપના પરિવારનું હાર્દિક સ્વાગત છે.\n\n"
                        f"આપના પરિવારમાંથી કેટલા સભ્યો પધારવાના છો તેની વિગત આપવા માટે નીચે આપેલી લિંક પર ક્લિક કરી માત્ર ૧ મિનિટમાં જણાવશો:\n"
                        f"👉 {chat_url}\n\n"
                        f"આપના સ્નેહીજનો તરફથી 🙏"
                    )
                    send_baileys_message(guest_phone, invite_text)

    session["is_completed"] = is_finished
    quick_replies = ["હજુ બીજા સગા-વહાલા ઉમેરો", "બધા આવી ગયા છે ✅"] if not is_finished else ["નવા નામ ઉમેરો"]
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

def call_gemini_for_parent_collector(parent_info, user_message, conversation_history):
    api_key = get_gemini_api_key()
    candidate_models = ["gemini-2.5-flash", "gemini-1.5-flash"]

    role_label = "પિતાશ્રી (પપ્પા)" if parent_info.get("role") == "FATHER" else "માતાશ્રી (મમ્મી)"
    side_label = "પિતા પક્ષ" if parent_info.get("role") == "FATHER" else "માતા પક્ષ"
    parent_name = parent_info.get("name", "Parent")
    groom = parent_info.get("groom_name", "").strip() or "વરરાજા"
    bride = parent_info.get("bride_name", "").strip() or "કન્યા"

    # Pre-check completion
    if is_completion_phrase(user_message):
        reply = (
            f"🌸 ખૂબ ખૂબ આભાર પૂજ્ય {parent_name} {role_label}! 🙏\n\n"
            f"આપના તમામ પરિવારજનો અને સંબંધીઓની યાદી સંપૂર્ણ થઈ ગઈ છે. લગ્નના શુભ પ્રસંગે આપ સૌના આશીર્વાદ સદાય {groom} અને {bride} પર રહે એવી પ્રાર્થના! 💐✨\n\n"
            f"(જો ભવિષ્યમાં કોઈ અન્ય નામ યાદ આવે તો આપ ગમે ત્યારે અહીં મેસેજ કરી શકો છો.)"
        )
        return [], reply, True

    system_prompt = f"""
You are an intelligent, polite, and warm Gujarati Wedding Concierge AI Chatbot for {groom} & {bride}'s royal wedding.
You are talking in a real-time conversational chat on WhatsApp with {parent_name} ({role_label}) on the {side_label} side.

YOUR PRIMARY GOAL:
Collect and organize their invited relatives, friends, and family members comfortably in natural conversation. The user may write in Gujarati, English, or Gujlish (casual Gujarati in English letters, e.g. 'maro chokro zarvin 9265834826').

User's Latest Message:
"{user_message}"

Conversation History:
{json.dumps([{'sender': m['sender'], 'text': m['text']} for m in conversation_history[-8:]], ensure_ascii=False)}

INTELLIGENCE & CHATBOT GUIDELINES:
1. EXTRACT ANY FAMILY MEMBER OR RELATIVE:
   - Son / Daughter / Children: 'chokro', 'dikro', 'son', 'putra', 'chokri', 'dikri', 'daughter', 'putri' ➔ Extract name & phone, category: 'brothers' (or 'sisters' for daughter), notes: 'પુત્ર (Son)' or 'પુત્રી (Daughter)'
   - Immediate & Extended Family: 'wife', 'patni', 'bhai', 'brother', 'ben', 'sister', 'kaka', 'kaki', 'fai', 'foi', 'fua', 'mama', 'mami', 'masi', 'masa', 'bhatrijo', 'bhanej', 'jamai', 'vahu', 'dada', 'dadi', 'nana', 'nani', 'mitra', 'friend', 'dost', 'padosi', 'colleague'
   - If the user writes casually like "chokro zarvin 9265834826", "maro chokro zarvin 9265834826", "kaushik kaka 9825...", "zarvin 9265834826":
     Accurately identify the person's real name (e.g. "Zarvin"), their 10-digit phone number if provided ("9265834826"), and their relationship.

2. TALK LIKE A REAL, RESPECTFUL GUJARATI WEDDING CHATBOT:
   - Always address them with high respect: "પૂજ્ય {parent_name} {role_label}! 🙏"
   - If relatives were added:
     Acknowledge enthusiastically: "વાહ પૂજ્ય {parent_name}! આપના દીકરા Zarvin (9265834826) નું નામ યાદીમાં નોંધી લીધું છે! 😊"
     Then ask warmly: "હવે આપના તરફથી બીજા કોઈ ભાઈઓ, બહેનો, કાકા, મામા, ફોઈ, માસી કે મિત્રો બાકી છે જેમના નામ નોંધવાના હોય? (બધા આવી જાય ત્યારે ફક્ત 'બધા આવી ગયા' અથવા 'DONE' લખશો.)"
   - If user asks a question or says greetings ("kem cho", "namaste", "hu bijal ben chu", "aa shu che"):
     DO NOT display an error template! Answer conversationally: "જય શ્રી કૃષ્ણ પૂજ્ય {parent_name} {role_label}! 🙏 હું {groom} અને {bride} ના લગ્ન પ્રસંગ માટે આપના સંબંધીઓની યાદી તૈયાર કરવામાં આપનો સહાયક છું. આપ આપના પરિવારજનો, સંબંધીઓ કે મિત્રોના નામ અને નંબર જણાવશો?"
   - If user says finished ("done", "badha aavi gaya", "patyu", "koi baki nathi"):
     Set "is_finished": true, "relatives": []
     Give a heartfelt Gujarati wedding blessing: "🌸 ખૂબ ખૂબ આભાર પૂજ્ય {parent_name} {role_label}! આપના તમામ સંબંધીઓની યાદી સફળ થઈ ગઈ છે! લગ્નમાં આપ સૌનું સ્વાગત છે! 💐"

3. OUTPUT FORMAT (STRICT JSON ONLY):
{{
  "relatives": [
    {{
      "name": "Full name (e.g. Zarvin)",
      "phone": "10-digit number if found, else empty string",
      "category": "brothers|sisters|kaka|fai|mama|masi|friends|colleagues|neighbours|parents",
      "notes": "Relation in Gujarati/English (e.g. પુત્ર / દીકરો)"
    }}
  ],
  "is_finished": false,
  "reply": "Warm, conversational, respectful Gujarati reply"
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
                return data.get("relatives", []), data.get("reply", "સંબંધીઓની વિગત સેવ થઈ ગઈ છે."), data.get("is_finished", False)
            else:
                last_error = f"{model_name} HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)
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
            
        phone_match = re.search(r'\b[6-9]\d{9}\b', chunk)
        phone = phone_match.group(0) if phone_match else ""
        cleaned = re.sub(r'\b[6-9]\d{9}\b', '', chunk).strip()
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
        names_str = ", ".join([f"{r['name']} ({r['notes']}{(' - ' + r['phone']) if r['phone'] else ''})" for r in relatives])
        reply = (
            f"✅ ખૂબ સરસ પૂજ્ય {parent_name}! 🙏\n"
            f"{names_str} ને યાદીમાં ઉમેરી દીધા છે. 😊\n\n"
            f"હવે આપના તરફથી બીજા કોઈ ભાઈઓ, બહેનો, કાકા, મામા, ફોઈ, માસી કે મિત્રો બાકી છે જેમના નામ નોંધવાના હોય?\n\n"
            f"(જ્યારે બધા સંબંધીઓના નામ લખાઈ જાય ત્યારે ફક્ત 'DONE' અથવા 'બધા આવી ગયા' લખીને મોકલશો.)"
        )
    else:
        reply = (
            f"ક્ષમા કરશો પૂજ્ય {parent_name}! વિગત સ્પષ્ટ સમજાઈ નથી. કૃપા કરીને નીચે મુજબ જણાવશો:\n\n"
            f"👉 દા.ત. 'દીકરો ઝરવીન 9265834826' અથવા 'ભાઈ તીર્થ 8103013148'\n\n"
            f"(અને બધા ઉમેરાઈ ગયા હોય ત્યારે 'DONE' અથવા 'બધા આવી ગયા' લખશો.)"
        )
        
    return relatives, reply, False

