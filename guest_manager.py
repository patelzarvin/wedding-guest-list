import json
import os
import uuid
from datetime import datetime

DATA_FILE = os.path.join(os.path.dirname(__file__), "wedding_data.json")

# Standard 10 categories for Father and Mother sides
CORE_CATEGORIES = [
    {"id": "parents", "en": "Parents (Dada/Dadi or Nana/Nani)", "gu": "માતા-પિતા (દાદા-દાદી / નાના-નાની)", "icon": "👴👵"},
    {"id": "brothers", "en": "Brothers", "gu": "ભાઈઓ", "icon": "👦"},
    {"id": "sisters", "en": "Sisters", "gu": "બહેનો", "icon": "👧"},
    {"id": "fai", "en": "Fai (Paternal Aunt)", "gu": "ફોઈ", "icon": "🧕"},
    {"id": "kaka", "en": "Kaka (Paternal Uncle)", "gu": "કાકા", "icon": "👨"},
    {"id": "mama", "en": "Mama (Maternal Uncle)", "gu": "મામા", "icon": "🧔"},
    {"id": "masi", "en": "Masi (Maternal Aunt)", "gu": "માસી", "icon": "👩"},
    {"id": "friends", "en": "Friends", "gu": "મિત્રો", "icon": "🤝"},
    {"id": "colleagues", "en": "Colleagues", "gu": "સહકર્મીઓ", "icon": "💼"},
    {"id": "neighbours", "en": "Neighbours", "gu": "પડોશીઓ", "icon": "🏡"}
]

SELF_CATEGORIES = [
    {"id": "brothers", "en": "Brothers & Cousins", "gu": "ભાઈઓ / કઝિન્સ", "icon": "👦"},
    {"id": "sisters", "en": "Sisters & Cousins", "gu": "બહેનો / કઝિન્સ", "icon": "👧"},
    {"id": "friends", "en": "Friends & Besties", "gu": "ખાસ મિત્રો", "icon": "🎉"},
    {"id": "colleagues", "en": "Office & Work Colleagues", "gu": "ઓફિસ સહકર્મીઓ", "icon": "💻"}
]

def get_initial_structure():
    return {
        "wedding_info": {
            "groom_name": "",
            "bride_name": "",
            "active_side": "GROOM",
            "groom_family": {
                "father_name": "",
                "father_phone": "",
                "mother_name": "",
                "mother_phone": ""
            },
            "bride_family": {
                "father_name": "",
                "father_phone": "",
                "mother_name": "",
                "mother_phone": ""
            }
        },
        "contacts": []
    }

import secrets

def load_data():
    if not os.path.exists(DATA_FILE):
        init_data = get_initial_structure()
        save_data(init_data)
        return init_data
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure proper schema migrations
            w_info = data.setdefault("wedding_info", {})
            if "groom_family" not in w_info:
                data = get_initial_structure()
                save_data(data)
                return data
            
            # Ensure parent collector tokens exist for father & mother
            modified = False
            for fam_key, pfx in [("groom_family", "gf"), ("bride_family", "bf")]:
                fam = w_info.setdefault(fam_key, {})
                if not fam.get("father_token"):
                    fam["father_token"] = f"{pfx}_fat_{secrets.token_hex(4)}"
                    modified = True
                if not fam.get("mother_token"):
                    fam["mother_token"] = f"{pfx}_mot_{secrets.token_hex(4)}"
                    modified = True
            if modified:
                save_data(data)
            return data
    except Exception:
        init_data = get_initial_structure()
        save_data(init_data)
        return init_data

def get_parent_info_by_token(token):
    data = load_data()
    w = data.get("wedding_info", {})
    mappings = [
        ("GROOM", "groom_family", "FATHER", "father_token", "father_name", "father_phone"),
        ("GROOM", "groom_family", "MOTHER", "mother_token", "mother_name", "mother_phone"),
        ("BRIDE", "bride_family", "FATHER", "father_token", "father_name", "father_phone"),
        ("BRIDE", "bride_family", "MOTHER", "mother_token", "mother_name", "mother_phone")
    ]
    for side, fam_key, role, token_key, name_key, phone_key in mappings:
        fam = w.get(fam_key, {})
        if fam.get(token_key) == token:
            return {
                "side": side,
                "role": role,
                "name": fam.get(name_key, ""),
                "phone": fam.get(phone_key, ""),
                "token": token
            }
    return None

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def update_wedding_profiles(groom_name=None, bride_name=None,
                            groom_father=None, groom_father_phone=None, groom_mother=None, groom_mother_phone=None,
                            bride_father=None, bride_father_phone=None, bride_mother=None, bride_mother_phone=None):
    data = load_data()
    w = data.setdefault("wedding_info", {})
    if groom_name is not None:
        w["groom_name"] = str(groom_name).strip()
    if bride_name is not None:
        w["bride_name"] = str(bride_name).strip()
    
    gf = w.setdefault("groom_family", {})
    if groom_father is not None:
        gf["father_name"] = str(groom_father).strip()
    if groom_father_phone is not None:
        gf["father_phone"] = str(groom_father_phone).strip()
    if groom_mother is not None:
        gf["mother_name"] = str(groom_mother).strip()
    if groom_mother_phone is not None:
        gf["mother_phone"] = str(groom_mother_phone).strip()
    
    bf = w.setdefault("bride_family", {})
    if bride_father is not None:
        bf["father_name"] = str(bride_father).strip()
    if bride_father_phone is not None:
        bf["father_phone"] = str(bride_father_phone).strip()
    if bride_mother is not None:
        bf["mother_name"] = str(bride_mother).strip()
    if bride_mother_phone is not None:
        bf["mother_phone"] = str(bride_mother_phone).strip()
    
    save_data(data)
    return w

def clear_wedding_data(target_side=None):
    """
    Clears contacts and optionally resets family profiles.
    - If target_side == 'GROOM': clears groom contacts and groom family profiles.
    - If target_side == 'BRIDE': clears bride contacts and bride family profiles.
    - If target_side is None: clears all contacts and all family profiles.
    """
    data = load_data()
    if target_side == "GROOM":
        data["contacts"] = [c for c in data.get("contacts", []) if c.get("side") != "GROOM"]
        if "wedding_info" in data and "groom_family" in data["wedding_info"]:
            data["wedding_info"]["groom_family"] = {
                "father_name": "", "father_phone": "",
                "mother_name": "", "mother_phone": ""
            }
    elif target_side == "BRIDE":
        data["contacts"] = [c for c in data.get("contacts", []) if c.get("side") != "BRIDE"]
        if "wedding_info" in data and "bride_family" in data["wedding_info"]:
            data["wedding_info"]["bride_family"] = {
                "father_name": "", "father_phone": "",
                "mother_name": "", "mother_phone": ""
            }
    else:
        data = get_initial_structure()
    save_data(data)
    return True

def check_duplicate(phone, name, exclude_id=None, target_side=None):
    data = load_data()
    clean_phone = "".join(filter(str.isdigit, str(phone))) if phone else ""
    duplicates = []
    for c in data.get("contacts", []):
        if exclude_id and c["id"] == exclude_id:
            continue
        # Strict side isolation if target_side is specified
        if target_side and c.get("side") != target_side.upper():
            continue
        c_phone = "".join(filter(str.isdigit, str(c.get("phone", ""))))
        # Match on last 10 digits
        if clean_phone and len(clean_phone) >= 10 and len(c_phone) >= 10 and clean_phone[-10:] == c_phone[-10:]:
            duplicates.append({"match_type": "Same Phone Number", "contact": c})
        elif name and len(name.strip()) >= 3 and name.strip().lower() == c.get("name", "").strip().lower():
            duplicates.append({"match_type": "Same Name", "contact": c})
    return duplicates

BLOCKED_CONTACT_NAMES = {
    "aavi gaya", "avi gaya", "aavi gya", "avi gya", "badha aavi gaya", "badha avi gaya",
    "badha", "badha aavi", "badha avi", "koi nathi", "koi sadasya nathi", "koi baki nathi",
    "sadasya nathi", "naa aavi gaya", "na aavi gaya", "all done", "complete", "done",
    "aatla j", "bas aatla", "patyu", "dofa", "loda", "fadav", "nahi", "nathi",
    "બધા આવી ગયા", "આવી ગયા", "કોઈ નથી", "કોઈ બાકી નથી", "પતી ગયા", "બધા", "શુભ લગ્ન"
}

def add_contact(side, added_by, category, name, phone, notes=""):
    data = load_data()
    clean_name = name.strip()
    clean_phone = "".join(filter(str.isdigit, str(phone))) if phone else ""
    
    # 1. Reject invalid or blocked names
    if not clean_name or len(clean_name) < 2:
        return None
    name_low = clean_name.lower()
    if any(blocked in name_low for blocked in BLOCKED_CONTACT_NAMES):
        return None
        
    # 2. Check for duplicate on the same side
    side_upper = side.upper()
    for existing in data.get("contacts", []):
        if existing.get("side") == side_upper:
            # Check phone match
            ex_phone = "".join(filter(str.isdigit, str(existing.get("phone", ""))))
            if clean_phone and len(clean_phone) >= 10 and len(ex_phone) >= 10:
                if clean_phone[-10:] == ex_phone[-10:]:
                    return existing # Duplicate found, return existing without re-adding
            # Check exact name match within same category
            if existing.get("category", "").lower() == category.lower() and existing.get("name", "").strip().lower() == clean_name.lower():
                return existing

    token = str(uuid.uuid4())[:8]
    contact_id = "c_" + str(uuid.uuid4())[:8]
    
    new_contact = {
        "id": contact_id,
        "token": token,
        "side": side_upper,       # "GROOM" or "BRIDE"
        "added_by": added_by.upper(), # "FATHER", "MOTHER", "SELF"
        "category": category.lower(), # "parents", "kaka", "mama", etc.
        "name": clean_name,
        "phone": clean_phone[-10:] if len(clean_phone) >= 10 else phone.strip(),
        "notes": notes.strip(),
        "status": "WAITING",
        "created_at": datetime.now().isoformat(),
        "household": None
    }
    
    data["contacts"].append(new_contact)
    save_data(data)
    return new_contact

def get_contact_by_token(token):
    data = load_data()
    for c in data.get("contacts", []):
        if c["token"] == token:
            return c
    return None

def submit_relative_details(token, form_payload):
    data = load_data()
    for c in data.get("contacts", []):
        if c["token"] == token:
            is_married = form_payload.get("is_married", False)
            partner_name = form_payload.get("partner_name", "").strip() if is_married else ""
            partner_phone = form_payload.get("partner_phone", "").strip() if is_married else ""
            
            children = form_payload.get("children", [])
            other_members = form_payload.get("other_members", [])
            
            members = []
            
            head_name = form_payload.get("confirmed_name", c["name"]).strip()
            head_phone = form_payload.get("confirmed_phone", c["phone"]).strip()
            c["name"] = head_name
            c["phone"] = head_phone
            
            members.append({
                "name": head_name,
                "relation_to_head": "Self (Head)",
                "phone": head_phone,
                "is_child": False
            })
            
            if is_married and partner_name:
                members.append({
                    "name": partner_name,
                    "relation_to_head": "Spouse / Partner",
                    "phone": partner_phone,
                    "is_child": False
                })
                
            for child in children:
                ch_name = child.get("name", "").strip()
                if ch_name:
                    members.append({
                        "name": ch_name,
                        "relation_to_head": "Child (" + child.get("age_group", "General") + ")",
                        "phone": child.get("phone", "").strip(),
                        "is_child": True
                    })
                    
            for om in other_members:
                om_name = om.get("name", "").strip()
                if om_name:
                    members.append({
                        "name": om_name,
                        "relation_to_head": om.get("relation", "Family Member").strip() or "Family Member",
                        "phone": om.get("phone", "").strip(),
                        "is_child": False
                    })
            
            total_count = len(members)
            household_name = f"{head_name} & Parivar (કુટુંબ)"
            
            c["status"] = "COMPLETED"
            c["submitted_at"] = datetime.now().isoformat()
            c["household"] = {
                "household_name": household_name,
                "total_coming": total_count,
                "adults_count": sum(1 for m in members if not m.get("is_child")),
                "children_count": sum(1 for m in members if m.get("is_child")),
                "members": members
            }
            
            save_data(data)
            return c
    return None

def get_stats(target_side=None):
    data = load_data()
    all_contacts = data.get("contacts", [])
    
    if target_side:
        contacts = [c for c in all_contacts if c.get("side") == target_side]
    else:
        contacts = all_contacts
        
    total_contacts = len(contacts)
    completed_contacts = sum(1 for c in contacts if c.get("status") == "COMPLETED")
    waiting_contacts = total_contacts - completed_contacts
    
    total_cards_needed = total_contacts
    total_guests_coming = 0
    total_adults = 0
    total_children = 0
    
    for c in contacts:
        if c.get("status") == "COMPLETED" and c.get("household"):
            total_guests_coming += c["household"].get("total_coming", 1)
            total_adults += c["household"].get("adults_count", 1)
            total_children += c["household"].get("children_count", 0)
        else:
            total_guests_coming += 1
            total_adults += 1

    return {
        "total_contacts": total_contacts,
        "completed": completed_contacts,
        "waiting": waiting_contacts,
        "total_cards": total_cards_needed,
        "total_guests": total_guests_coming,
        "total_adults": total_adults,
        "total_children": total_children
    }
