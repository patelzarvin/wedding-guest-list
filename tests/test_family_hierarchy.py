import unittest
from database import clean_all_data, add_contact_db, update_guest_family_db, get_contacts_by_wedding, save_or_update_wedding
from bot_engine import process_chat_message, save_conversations

class TestFamilyHierarchyAndChat(unittest.TestCase):
    def setUp(self):
        clean_all_data()
        save_conversations({})
        self.wedding_id = save_or_update_wedding(1, {
            "groom_name": "Kunj",
            "bride_name": "Kruti"
        })

    def test_update_family_and_headcount(self):
        c = add_contact_db(self.wedding_id, "GROOM", "FATHER", "friends", "Jyot Patel", "7984285496")
        self.assertIsNotNone(c)
        cid = c["id"]

        # Initially 1 headcount
        contacts = get_contacts_by_wedding(self.wedding_id)
        jyot = [x for x in contacts if x["id"] == cid][0]
        self.assertEqual(jyot["total_headcount"], 1)

        # Update Jyot with Shankarlal (parent), Stuti (spouse), Agnya (other) -> 1 (self) + 1 + 1 + 1 = 4
        new_hc = update_guest_family_db(cid, {
            "parents_names": "Shankarlal",
            "spouse_name": "Stuti",
            "children_names": "",
            "other_members": "Agnya",
            "total_headcount": 4
        })
        self.assertEqual(new_hc, 4)

        # Re-fetch contacts
        contacts = get_contacts_by_wedding(self.wedding_id)
        jyot_updated = [x for x in contacts if x["id"] == cid][0]
        self.assertEqual(jyot_updated["total_headcount"], 4)
        self.assertEqual(jyot_updated["parents_names"], "Shankarlal")
        self.assertEqual(jyot_updated["spouse_name"], "Stuti")
        self.assertEqual(jyot_updated["other_members"], "Agnya")

    @unittest.mock.patch('bot_engine.get_gemini_api_key', return_value='')
    def test_bot_chat_flow_with_punctuation(self, mock_gemini):
        c = add_contact_db(self.wedding_id, "GROOM", "FATHER", "friends", "Test Guest", "9999999999")
        tok = c["token"]

        # Step 1: Parents question -> "હા"
        r1 = process_chat_message(tok, "હા")
        self.assertIn("માતા-પિતા", r1["reply"])

        # Step 2: Parent name -> "Shankarlal"
        r2 = process_chat_message(tok, "Shankarlal")
        self.assertTrue(any(w in r2["reply"].lower() for w in ["married", "પરણેલા", "પરણિત", "લગ્ન", "જીવનસાથી"]))

        # Step 3: Married? -> "હા (Yes)"
        r3 = process_chat_message(tok, "હા (Yes)")
        self.assertIn("જીવનસાથી", r3["reply"])

        # Step 4: Spouse name with period -> "Stuti."
        r4 = process_chat_message(tok, "Stuti.")
        self.assertIn("બાળકો", r4["reply"])
        self.assertIn("Stuti", r4["reply"])

        # Step 5: Children? -> "ના (No)"
        r5 = process_chat_message(tok, "ના (No)")
        self.assertIn("મોટા દીકરા", r5["reply"])

        # Step 6: Separate adult child branch? -> "ના (No)"
        r6 = process_chat_message(tok, "ના (No)")
        self.assertIn("પરિવારમાંથી અન્ય કોઈ સભ્ય", r6["reply"])

        # Step 7: Other relatives? -> "હા (Yes)"
        r7 = process_chat_message(tok, "હા (Yes)")
        self.assertIn("તેમનું નામ", r7["reply"])

        # Step 8: Member name -> "Agnya"
        r8 = process_chat_message(tok, "Agnya")
        self.assertIn("નોંધાઈ ગઈ", r8["reply"])
        self.assertIn("Stuti", r8["reply"])
        self.assertIn("Shankarlal", r8["reply"])
        self.assertIn("Agnya", r8["reply"])
        self.assertIn("4", r8["reply"])

if __name__ == "__main__":
    unittest.main()
