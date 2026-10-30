import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "recap"))

import config
import meta


class VideoDescriptionTests(unittest.TestCase):
    def test_description_includes_dynamic_show_episode_and_date(self):
        show = config.SHOWS["rimjhim"]
        description = meta.make_description(
            show,
            "217",
            "20 May 2026",
            "https://youtu.be/source-video",
            "आज की कहानी में बड़ा मोड़ आता है।",
            [],
            ["आगे नया खुलासा हो सकता है।"],
            ["परिवार का नया रहस्य"],
            [],
            source_ep="215",
            source_date="18 May 2026",
        )

        self.assertIn("Rimjhim Choti Umar Bada Safar | 18 May 2026 | Episode 215 Recap | Dangal TV", description)
        self.assertIn("Episode 217 Story + Prediction | 20 May 2026", description)
        self.assertIn("रिमझिम छोटी उम्र बड़ा सफर", description)
        self.assertIn("Rimjhim Choti Umar Bada Safar 19 May 2026 episode 216", description)
        self.assertIn("Rimjhim Choti Umar Bada Safar 20 May 2026 episode 217", description)
        search_terms = description.split("खोज-शब्द / Search terms:\n", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("18 May 2026", search_terms)
        self.assertNotIn("episode 215", search_terms)
        self.assertIn("Rimjhim Choti Umar Bada Safar natak", search_terms)
        self.assertIn("Rimjhim Choti Umar Bada Safar today episode 19 May", search_terms)
        self.assertIn("Rimjhim Choti Umar Bada Safar ka 217 bhag", search_terms)
        self.assertIn("रिमझिम छोटी उम्र बड़ा सफर", search_terms)
        self.assertLessEqual(len(description), 4900)
        self.assertIn("https://youtu.be/source-video", description)
        self.assertIn("यह एक फ़ैन-निर्मित रिकैप है।", description)
        self.assertIn("https://youtu.be/source-video", description)
        self.assertIn("आज की कहानी में बड़ा मोड़ आता है।", description)
        self.assertIn("आगे नया खुलासा हो सकता है।", description)
        self.assertIn("यह एक फ़ैन-निर्मित रिकैप है।", description)

    def test_every_show_gets_its_own_search_terms_and_hashtags(self):
        for key, show in config.SHOWS.items():
            with self.subTest(show=key):
                description = meta.make_description(
                    show, "12", "20 May 2026", "https://youtu.be/source",
                    "Hook", [], ["Prediction"], [], [],
                    source_ep="10", source_date="18 May 2026",
                )

                self.assertIn(show["en"], description)
                self.assertIn(show["hi"], description)
                self.assertIn(meta.hashtag(show["match"]), description)
                self.assertIn(f"{show['en']} 19 May 2026 episode 11", description)
                self.assertIn(f"{show['en']} 20 May 2026 episode 12", description)
                self.assertIn(f"{show['en']} natak", description)
                self.assertLessEqual(len(description), 4900)
                self.assertIn("https://youtu.be/source", description)

    def test_search_terms_exclude_source_day_and_unfiltered_keywords(self):
        show = config.SHOWS["atisundar"]
        terms = meta.description_search_terms(
            show, "1195", "31 Oct 2026", "1193", "29 Oct 2026",
        )

        self.assertEqual(terms[1][0], "Mann Atisundar 30 Oct 2026 episode 1194")
        self.assertEqual(terms[2][0], "Mann Atisundar 31 Oct 2026 episode 1195")
        self.assertFalse(any("29 Oct 2026" in term or "1193" in term for group in terms.values() for term in group))

    def test_search_dates_fall_back_to_target_date_without_source_date(self):
        terms = meta.description_search_terms(
            config.SHOWS["atisundar"], "1195", "31 Oct 2026", "1193", "",
        )

        self.assertIn("Mann Atisundar 30 Oct 2026 episode 1194", terms[1])
        self.assertIn("Mann Atisundar 31 Oct 2026 episode 1195", terms[2])


if __name__ == "__main__":
    unittest.main()
