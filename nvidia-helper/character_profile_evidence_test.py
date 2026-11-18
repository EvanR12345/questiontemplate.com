"""Exercise quote ownership through the native chapter analyzer and saved profile."""
import tempfile
import threading
import unittest

from director_provider import validate_schema
from studio_data import new_project, sentences
from studio_service import StudioService


class PlanningService(StudioService):
    def worker(self):
        pass


class EmptyQueue:
    def snapshot(self):
        return {"counts": {}, "current": None}


def person(name, quote):
    return {"id": name, "name": name, "type": "main", "description": name,
            "evidence": quote, "aliases": []}


class CharacterProfileEvidenceTest(unittest.TestCase):
    def run_chapter(self, source, cast):
        with tempfile.TemporaryDirectory() as folder:
            service = PlanningService(folder, None, lambda: None, threading.RLock(),
                                      lambda: None, lambda: None, EmptyQueue())
            service.providers["existing"].healthCheck = lambda: {
                "installed": True, "models": ["sd15"], "workflow": ["text-to-image"]}
            project = new_project("Exact casting evidence")
            chapter = project["chapters"][0]
            chapter["sourceText"] = source
            chapter["audio"] = {
                "duration": len(sentences(source)) * 5,
                "sentences": [{"index": i, "text": s, "start": i * 5, "end": (i + 1) * 5}
                              for i, s in enumerate(sentences(source))],
            }
            project = service.store.save(project)
            calls = {}

            def response(role, context, schema, gate, vision=False):
                if role.startswith("Casting supervisor"):
                    result = {"people": cast}
                elif role.startswith("Character bible"):
                    evidence = context["sourceEvidence"]
                    calls[context["name"]] = evidence
                    identity = {key: "" for key in schema["properties"]["permanentIdentity"]["properties"]}
                    appearance = {key: "" for key in schema["properties"]["defaultAppearance"]["properties"]}
                    identity["face"] = "white beard" if "white beard" in evidence else ""
                    identity["naturalHair"] = "gray hair" if "gray hair" in evidence else ""
                    result = {"permanentIdentity": identity, "defaultAppearance": appearance,
                              "gender": "", "approximateAge": ""}
                elif role.startswith("Story analyst"):
                    result = {"summary": "The characters are introduced.", "people": [], "locations": [],
                              "beats": [], "changes": [], "environmentChanges": [],
                              "objects": [], "goals": [], "unresolved": []}
                elif role.startswith("Chapter director"):
                    result = {"scenes": [{"startSentence": 0, "endSentence": len(context["sentences"]) - 1,
                                          "purpose": "Introduce characters", "location": "Station", "mood": "calm",
                                          "characters": [p["id"] for p in context["people"]], "shotCount": 1,
                                          "pacingReason": "One introduction"}]}
                elif role.startswith("Scene director"):
                    result = {"shots": [{"sceneIndex": 0, "startSentence": 0,
                                         "endSentence": len(context["sentences"]) - 1,
                                         "characters": [p["id"] for p in context["people"]],
                                         "action": "The characters meet.", "expression": "calm", "pose": "standing",
                                         "lighting": "daylight", "motion": "slow zoom in", "transition": "cut"}]}
                elif role.startswith("Cinematographer"):
                    result = {"cameras": [{"shotIndex": 0, "shot": "medium wide",
                                           "angle": "eye level", "composition": "Two figures in frame"}]}
                elif role.startswith("Workflow planner"):
                    result = {"provider": "existing", "model": "sd15", "workflow": "text-to-image",
                              "referenceStrategy": "Character descriptions", "reason": "Installed workflow"}
                elif role.startswith("Continuity supervisor"):
                    result = {"issues": [], "intentionalChanges": [], "objectChanges": []}
                elif role.startswith("Image prompt engineer"):
                    result = {"prompts": [{"shotIndex": 0, "prompt": "The characters meet in daylight."}]}
                else:
                    self.fail(f"Unexpected director role: {role[:90]}")
                validate_schema(result, schema)
                return result

            service.director.call = response
            try:
                service.analyze(project, chapter["id"], {})
                saved = service.store.load(project["id"])["chapters"][0]
                self.assertEqual(saved["status"], "READY_FOR_IMAGES")
                self.assertEqual(saved["scenes"][0]["startSentence"], 0)
                self.assertEqual(saved["scenes"][0]["endSentence"], len(sentences(source)) - 1)
                return calls, {p["name"]: p for p in saved["people"]}
            finally:
                service.close()

    def test_genuine_casting_quote_keeps_short_beard_and_preserves_long_sentence(self):
        # The local Qwen casting reply used these exact two excerpts.
        mira = "Mira Chen, a young woman with black curls and brown eyes, wears a yellow raincoat."
        otis = "Otis Bell, an elderly man with a white beard."
        source = f"{mira} At the railway station, she gives a brass compass to {otis}"
        cast = [person("Mira Chen", mira), person("Otis Bell", otis)]
        calls, profiles = self.run_chapter(source, cast)
        self.assertEqual(calls["Mira Chen"], sentences(source)[0])
        self.assertEqual(calls["Otis Bell"], "Otis Bell, an elderly man with a white beard.")
        self.assertEqual(profiles["Otis Bell"]["permanentIdentity"]["face"], "white beard")
        self.assertEqual(profiles["Mira Chen"]["permanentIdentity"]["face"], "")

    def test_short_name_anchor_and_original_case(self):
        calls, profiles = self.run_chapter("Jo has gray hair. Bo wears a red scarf.",
                                           [person("Jo", "jo has gray hair."), person("Bo", "Bo")])
        self.assertEqual(calls["Jo"], "Jo has gray hair.")
        self.assertEqual(profiles["Jo"]["permanentIdentity"]["naturalHair"], "gray hair")
        self.assertNotIn("Bo", calls)
        self.assertEqual(profiles["Bo"]["permanentIdentity"]["naturalHair"], "")

    def test_forward_only_the_short_subject_clause_in_a_multiple_subject_sentence(self):
        source = "Mira Chen has gray hair and sees Otis Bell, an elderly man with a white beard."
        calls, profiles = self.run_chapter(source, [
            person("Mira Chen", "Mira Chen has gray hair"), person("Otis Bell", "Otis Bell, an elderly man with a white beard.")
        ])
        self.assertEqual(calls["Mira Chen"], "Mira Chen has gray hair")
        self.assertEqual(calls["Otis Bell"], "Otis Bell, an elderly man with a white beard.")
        self.assertEqual(profiles["Mira Chen"]["permanentIdentity"]["naturalHair"], "gray hair")
        self.assertEqual(profiles["Otis Bell"]["permanentIdentity"]["face"], "white beard")
        self.assertEqual(profiles["Mira Chen"]["permanentIdentity"]["face"], "")
        self.assertEqual(profiles["Otis Bell"]["permanentIdentity"]["naturalHair"], "")

    def test_ambiguous_repeated_hallucinated_or_other_subject_quotes_do_not_enrich(self):
        fixtures = [
            ("Jo has gray hair. Jo has gray hair.", [person("Jo", "Jo has gray hair.")]),
            ("Jo has gray hair, Jo has gray hair.", [person("Jo", "Jo has gray hair")]),
            ("Jo has gray hair.", [person("Jo", "Jo has green eyes.")]),
            ("Jo has gray hair.", [person("Jo", "gray hair")]),
            ("Banjo has gray hair.", [person("Jo", "Jo has gray hair.")]),
            ("Jo has a gray hairstyle.", [person("Jo", "Jo has a gray hair")]),
            ("Jordan has gray hair.", [person("Jo", "Jordan has gray hair.")]),
            ("Jo has gray hair and Bo has white hair.", [person("Jo", "Jo has gray hair and Bo has white hair."), person("Bo", "")]),
            ("Jo and Bo have gray hair.", [person("Jo", "Jo and Bo have gray hair."), person("Bo", "")]),
            ("Jo has Bo's gray wig.", [person("Jo", "Jo has Bo's gray wig."), person("Bo", "")]),
            ("Jo has her friend's gray hair ribbon.", [person("Jo", "Jo has her friend's gray hair ribbon.")]),
            ("Jo has a gray hat. Bo has gray hair.", [person("Jo", "Bo has gray hair."), person("Bo", "")]),
        ]
        for source, cast in fixtures:
            with self.subTest(source=source):
                calls, profiles = self.run_chapter(source, cast)
                self.assertEqual(calls.get("Jo", ""), "")
                if "Jo" in profiles:
                    self.assertEqual(profiles["Jo"]["permanentIdentity"]["naturalHair"], "")

    def test_valid_long_fragment_still_passes_the_matched_sentence(self):
        quote = "Jo, a quiet neighbor with a white beard and gold spectacles"
        sentence = f"At noon, {quote}, looked out of the blue door."
        calls, profiles = self.run_chapter(sentence, [person("Jo", quote)])
        self.assertEqual(calls["Jo"], sentence)
        self.assertEqual(profiles["Jo"]["permanentIdentity"]["face"], "white beard")

    def test_near_limit_exact_excerpt_can_end_inside_a_word(self):
        source = ("Jo, an elderly neighbor with a white beard, waited beside the ticket turnstile "
                  "while employees quietly reset the departures board before the early trains arrived, "
                  "and checked the printed notices near platform nine.")
        quote = source[:180]
        self.assertTrue(source[179].isalnum() and source[180].isalnum())
        calls, profiles = self.run_chapter(source, [person("Jo", quote)])
        self.assertEqual(calls["Jo"], source)
        self.assertEqual(profiles["Jo"]["permanentIdentity"]["face"], "white beard")

    def test_near_limit_edited_suffix_and_ambiguous_verbatim_excerpt_do_not_enrich(self):
        source = ("Jo, an elderly neighbor with a white beard, waited beside the ticket turnstile "
                  "while employees quietly reset the departures board before the early trains arrived, "
                  "and checked the printed notices near platform nine.")
        examples = [
            (source, source[:177] + "xyz"),
            (source + " " + source, source[:180]),
        ]
        for text, quote in examples:
            with self.subTest(expectedQuoteLength=len(quote), sourceLength=len(text)):
                calls, profiles = self.run_chapter(text, [person("Jo", quote)])
                self.assertEqual(calls.get("Jo", ""), "")
                self.assertEqual(profiles["Jo"]["permanentIdentity"]["face"], "")

    def test_long_prefix_with_invented_suffix_is_not_verbatim(self):
        first = "Jo, a quiet neighbor with a gray jacket, was standing by the painted blue door"
        calls, profiles = self.run_chapter(first + ".",
                                           [person("Jo", first + ", and had a white beard.")])
        self.assertEqual(calls.get("Jo", ""), "")
        self.assertEqual(profiles["Jo"]["permanentIdentity"]["face"], "")


if __name__ == "__main__":
    unittest.main()
