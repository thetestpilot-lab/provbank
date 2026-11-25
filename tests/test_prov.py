import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prov  # noqa: E402

COMMIT = "a" * 40


class Bas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rot = self.tmp.name
        self.upp = os.path.join(self.rot, "uppgifter")
        os.makedirs(os.path.join(self.upp, "exempel"))
        with open(os.path.join(self.upp, "exempel", "indata.txt"), "w") as f:
            f.write("hej\n")
        with open(os.path.join(self.upp, "exempel", "bedom.py"), "w") as f:
            f.write("import json,os,sys\nut=sys.argv[1]\n"
                    "ok=open(os.path.join(ut,'svar.txt')).read().strip()=='HEJ'\n"
                    "print(json.dumps({'ratt': ok}))\n")
        self.arb = os.path.join(self.rot, "arbete")
        self.p = [mock.patch.object(prov, "UPPGIFTER", self.upp),
                  mock.patch.dict(os.environ, {"ARBETE": self.arb}, clear=False)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        self.tmp.cleanup()

    def env(self, **kw):
        d = {"KORNING": "ai-nyheter-1", "REPO": "https://github.com/x/y", "COMMIT": COMMIT,
             "PLAN": json.dumps({"kategori": "exempel", "steg": [{"kommando": "true"}]})}
        d.update(kw)
        return mock.patch.dict(os.environ, d)

    def lasrepo(self):
        """Låtsas att hamta har gått: ett repo med en fil och paketet i läget hamtat."""
        os.makedirs(os.path.join(self.arb, "repo"))
        p = prov.las_paket()
        p["utfall"] = "hamtat"
        prov.skriv_paket(p)


class Kontrollera(Bas):
    def test_godkand(self):
        with self.env():
            self.assertEqual(prov.kontrollera(), 0)
        p = prov.las_paket()
        self.assertEqual(p["utfall"], "kontrollerat")
        self.assertEqual(p["fel"], [])

    def test_fel_indata(self):
        fall = [{"KORNING": "Stora; rm -rf"}, {"REPO": "https://evil.com/x/y"}, {"REPO": "file:///etc"},
                {"REPO": "https://github.com/x/y.git"}, {"REPO": "https://github.com/x/y/../z"},
                {"COMMIT": "main"}, {"PLAN": "inte json"}, {"PLAN": "x" * 20001}]
        for f in fall:
            with self.subTest(f=f), self.env(**f):
                self.assertEqual(prov.kontrollera(), 1)
                self.assertEqual(prov.las_paket()["utfall"], "fel")

    def test_plan(self):
        ok = {"kategori": "exempel", "installera": ["pip install ."], "steg": [{"kommando": "x", "tidsgrans_s": 60}],
              "spara": ["svar.txt", "bilder/a.png"]}
        self.assertEqual(prov.kontrollera_plan(ok), [])
        self.assertEqual(prov.kontrollera_plan({"kategori": "ingen", "steg": [{"kommando": "x"}]}), [])
        dåliga = [{"kategori": "okand", "steg": [{"kommando": "x"}]},
                  {"kategori": "exempel", "steg": []},
                  {"kategori": "exempel", "steg": [{"kommando": "x", "tidsgrans_s": 601}]},
                  {"kategori": "exempel", "steg": [{"kommando": "x" * 1001}]},
                  {"kategori": "exempel", "steg": [{"kommando": "x"}] * 11},
                  {"kategori": "exempel", "steg": [{"kommando": "x"}], "spara": ["../hemligt"]},
                  {"kategori": "exempel", "steg": [{"kommando": "x"}], "spara": ["/etc/passwd"]},
                  {"kategori": "../uppgifter", "steg": [{"kommando": "x"}]}, []]
        for d in dåliga:
            with self.subTest(d=d):
                self.assertNotEqual(prov.kontrollera_plan(d), [])


class Kor(Bas):
    def starta(self, plan):
        with self.env(PLAN=json.dumps(plan)):
            self.assertEqual(prov.kontrollera(), 0)
        self.lasrepo()

    def test_hela_provet(self):
        self.starta({"kategori": "exempel", "installera": ["echo installerar"],
                     "steg": [{"namn": "svara", "kommando": "tr a-z A-Z < \"$PROV/indata.txt\" > \"$UT/svar.txt\""}],
                     "spara": ["svar.txt", "saknas.txt"]})
        with open(os.path.join(self.arb, "skanning.json"), "w") as f:
            json.dump({"risk_assessment": {"score": 10, "severity": "LOW", "recommendation": "SAFE"},
                       "issues": [{"id": "R1", "category": "exec", "severity": "LOW", "finding": "m", "location": {"file": "a"}}]}, f)
        self.assertEqual(prov.kor(), 0)
        p = prov.las_paket()
        self.assertEqual(p["utfall"], "kort")
        self.assertEqual([s["typ"] for s in p["steg"]], ["installera", "steg"])
        self.assertEqual(p["bedomning"], {"ratt": True})
        self.assertEqual(p["skanning"]["rekommendation"], "SAFE")
        self.assertEqual(p["skanning"]["antal_fynd"], 1)
        self.assertEqual(p["skanning"]["per_kategori"], {"exec/LOW": 1})
        self.assertEqual(p["skanning"]["fynd"][0]["fil"], "a")
        self.assertEqual(p["filer"][0]["byte"], 4)
        self.assertIn("fel", p["filer"][1])
        self.assertTrue(os.path.isfile(os.path.join(self.arb, "testpaket", "filer", "svar.txt")))

    def test_stoppas_av_skanning(self):
        self.starta({"kategori": "ingen", "steg": [{"kommando": "touch \"$UT/kordes\""}]})
        with open(os.path.join(self.arb, "skanning.json"), "w") as f:
            json.dump({"risk_assessment": {"score": 90, "severity": "CRITICAL", "recommendation": "DO_NOT_INSTALL"}}, f)
        self.assertEqual(prov.kor(), 0)
        self.assertEqual(prov.las_paket()["utfall"], "stoppat_av_skanning")
        self.assertFalse(os.path.exists(os.path.join(self.arb, "ut", "kordes")))

    def test_utan_skanning_kors_med_notering(self):
        self.starta({"kategori": "ingen", "steg": [{"kommando": "true"}]})
        self.assertEqual(prov.kor(), 0)
        p = prov.las_paket()
        self.assertEqual(p["utfall"], "kort")
        self.assertFalse(p["skanning"]["ok"])

    def test_tidsgrans_och_fel(self):
        self.starta({"kategori": "ingen", "steg": [{"kommando": "sleep 30 & sleep 30", "tidsgrans_s": 1},
                                                   {"kommando": "exit 3"}]})
        self.assertEqual(prov.kor(), 0)
        p = prov.las_paket()
        self.assertEqual(p["utfall"], "kort_med_fel")
        self.assertTrue(p["steg"][0]["tidsgrans_nadd"])
        self.assertLess(p["steg"][0]["sekunder"], 10)
        self.assertEqual(p["steg"][1]["slutkod"], 3)

    def test_installation_som_faller_stoppar(self):
        self.starta({"kategori": "ingen", "installera": ["exit 1"], "steg": [{"kommando": "touch \"$UT/x\""}]})
        prov.kor()
        p = prov.las_paket()
        self.assertEqual(len(p["steg"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.arb, "ut", "x")))

    def test_indata_syns_inte_for_koden(self):
        self.starta({"kategori": "ingen", "steg": [{"kommando": "env > \"$UT/env.txt\""}], "spara": ["env.txt"]})
        with mock.patch.dict(os.environ, {"ACTIONS_RUNTIME_TOKEN": "hemligt", "PLAN": "{}"}):
            prov.kor()
        with open(os.path.join(self.arb, "ut", "env.txt")) as f:
            env = f.read()
        self.assertNotIn("hemligt", env)
        self.assertNotIn("PLAN=", env)

    def test_lang_utdata_klipps(self):
        self.starta({"kategori": "ingen", "steg": [{"kommando": "head -c 50000 /dev/zero | tr '\\0' x"}]})
        prov.kor()
        s = prov.las_paket()["steg"][0]["stdout"]
        self.assertLess(len(s), prov.MAX_UTDATA_TECKEN + 100)
        self.assertTrue(s.startswith("[…"))

    def test_symlank_ut_ur_ut_sparas_inte(self):
        self.starta({"kategori": "ingen", "steg": [{"kommando": "ln -s /etc/hosts \"$UT/l\""}], "spara": ["l"]})
        prov.kor()
        self.assertIn("fel", prov.las_paket()["filer"][0])


class Hamta(Bas):
    def test_git_utan_lokala_protokoll_och_krokar(self):
        with self.env():
            prov.kontrollera()
        anrop = []
        with mock.patch.object(prov.subprocess, "run", side_effect=lambda a, **k: anrop.append(a)):
            self.assertEqual(prov.hamta(), 0)
        for a in anrop:
            self.assertIn("protocol.file.allow=never", a)
            self.assertIn("core.hooksPath=/dev/null", a)
        self.assertIn(COMMIT, anrop[1])
        self.assertEqual(prov.las_paket()["utfall"], "hamtat")

    def test_fel_vid_hamtning(self):
        with self.env():
            prov.kontrollera()

        def fall(a, **k):
            raise subprocess.CalledProcessError(128, a)
        with mock.patch.object(prov.subprocess, "run", side_effect=fall):
            self.assertEqual(prov.hamta(), 1)
        self.assertEqual(prov.las_paket()["utfall"], "fel")


if __name__ == "__main__":
    unittest.main()
