"""infer_language: the spoken language from the Notion name and the script, or "auto" when they disagree or say nothing."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import prepare  # noqa: E402

TE = "ఇవన్నీ అస్సలు చెప్పకండి, please! మీకు English మాట్లాడటం రాదని ఎదుటివాళ్ళు అనుకుంటారు, పైగా ప్రతి interviewer కూడా ఇలాంటి lines కనీసం వెయ్యి సార్లయినా వినే ఉంటారు."
KN = "ಈ ಕಡೆ ಬನ್ನಿ, regular ಆಗಿ ಮಾತಾಡಿ. ನಾನು ಇಂಗ್ಲಿಷ್ ಮಾತನಾಡುವಾಗ ತುಂಬಾ ತಪ್ಪುಗಳು ಆಗುತ್ತವೆ ಅಂತ ಹೇಳಿದರು."


class InferLanguage(unittest.TestCase):
    def test_name_and_script_agree(self):
        self.assertEqual(prepare.infer_language("W47 - Telugu - AI Only - Micro Narrative", TE), "te")
        self.assertEqual(prepare.infer_language("W46 - Kannada - AI Only - Skit", KN), "kn")

    def test_script_alone(self):
        self.assertEqual(prepare.infer_language("", TE), "te")

    def test_name_alone(self):
        self.assertEqual(prepare.infer_language("W45 - Malayalam - Robot", "English only script"), "ml")

    def test_disagreement_stays_auto(self):
        self.assertEqual(prepare.infer_language("W47 - Telugu - AI Only", KN), "auto")

    def test_two_languages_in_name_stays_auto(self):
        self.assertEqual(prepare.infer_language("Telugu and Kannada mix", ""), "auto")

    def test_nothing_known(self):
        self.assertEqual(prepare.infer_language("Plain English video", "Hello there"), "auto")

    def test_hindi_marathi_share_a_script(self):
        hi = "यह एक परीक्षा है और हमें बहुत सारे शब्द चाहिए ताकि स्क्रिप्ट काफी लंबी हो जाए और पहचान सही हो।"
        self.assertEqual(prepare.infer_language("W40 - Marathi - Skit", hi), "mr")


if __name__ == "__main__":
    unittest.main()
