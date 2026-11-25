#!/usr/bin/env python3
"""Provbänken för The Test Pilot: kör ett främmande repo mot en fast provuppgift i GitHub Actions.

Körs bara i workflowet `.github/workflows/prov.yml`, aldrig på servern. Bara stdlib, Python 3.9+.
Indata kommer som miljövariabler (KORNING, REPO, COMMIT, PLAN), aldrig som text i skalskript.

  python3 prov.py kontrollera   # indata och plan, skriver ett första testpaket
  python3 prov.py hamta         # klonar repot på exakt commit
  python3 prov.py kor           # läser skanningen, kör planen och packar testpaketet

Allt skrivs under ARBETE (standard ./arbete): repo/, prov/, ut/, skanning.json och testpaket/.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

HAR = os.path.dirname(os.path.abspath(__file__))
UPPGIFTER = os.path.join(HAR, "uppgifter")

RE_KORNING = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RE_REPO = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
RE_COMMIT = re.compile(r"^[0-9a-f]{40}$")
RE_KATEGORI = re.compile(r"^[a-z0-9-]{1,40}$")

MAX_PLAN_TECKEN = 20000
MAX_KOMMANDON = 10
MAX_KOMMANDO_TECKEN = 1000
MAX_STEG_S = 600
STANDARD_STEG_S = 300
MAX_UTDATA_TECKEN = 20000
MAX_FIL_BYTE = 5 * 1024 * 1024
MAX_FILER_BYTE = 20 * 1024 * 1024
MAX_REPO_BYTE = 1024 * 1024 * 1024
STOPPA_VID = {"DO_NOT_INSTALL"}   # SkillSpectors rekommendation vid HIGH och CRITICAL (poäng 51 och uppåt)


def arbete():
    return os.path.abspath(os.environ.get("ARBETE") or "arbete")


def nu():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def paket_fil():
    return os.path.join(arbete(), "testpaket", "paket.json")


def las_paket():
    with open(paket_fil(), encoding="utf-8") as f:
        return json.load(f)


def skriv_paket(p):
    os.makedirs(os.path.dirname(paket_fil()), exist_ok=True)
    tmp = paket_fil() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    os.replace(tmp, paket_fil())


def kategorier():
    if not os.path.isdir(UPPGIFTER):
        return []
    return sorted(d for d in os.listdir(UPPGIFTER) if os.path.isdir(os.path.join(UPPGIFTER, d)))


def kontrollera_plan(plan):
    """Planen som kanalmotorn skickar. Ger en lista med fel (tom = godkänd)."""
    fel = []
    if not isinstance(plan, dict):
        return ["planen är inget objekt"]
    kat = plan.get("kategori")
    if kat != "ingen" and (not isinstance(kat, str) or not RE_KATEGORI.match(kat) or kat not in kategorier()):
        fel.append(f"okänd kategori: {kat!r}")
    for nyckel in ("installera", "steg"):
        if not isinstance(plan.get(nyckel, []), list):
            fel.append(f"{nyckel} är ingen lista")
    if fel:
        return fel
    if not plan.get("steg"):
        fel.append("planen har inga steg")
    alla = [("installera", {"kommando": k} if isinstance(k, str) else k) for k in plan.get("installera", [])] + \
           [("steg", s) for s in plan.get("steg", [])]
    if len(alla) > MAX_KOMMANDON:
        fel.append(f"fler än {MAX_KOMMANDON} kommandon")
    for typ, s in alla:
        if not isinstance(s, dict) or not isinstance(s.get("kommando"), str) or not s["kommando"].strip():
            fel.append(f"{typ}: kommandot saknas")
            continue
        if len(s["kommando"]) > MAX_KOMMANDO_TECKEN:
            fel.append(f"{typ}: kommandot är längre än {MAX_KOMMANDO_TECKEN} tecken")
        t = s.get("tidsgrans_s", STANDARD_STEG_S)
        if not isinstance(t, int) or not 1 <= t <= MAX_STEG_S:
            fel.append(f"{typ}: tidsgrans_s ska vara 1–{MAX_STEG_S}")
    spara = plan.get("spara", [])
    if not isinstance(spara, list) or not all(isinstance(x, str) for x in spara):
        fel.append("spara är ingen lista med sökvägar")
    else:
        for x in spara:
            if not sakert_relativt(x):
                fel.append(f"spara: otillåten sökväg {x!r}")
    return fel


def sakert_relativt(sokvag):
    if not sokvag or os.path.isabs(sokvag) or "\x00" in sokvag:
        return False
    delar = sokvag.replace("\\", "/").split("/")
    return ".." not in delar


def indata():
    return {n: os.environ.get(n, "") for n in ("KORNING", "REPO", "COMMIT", "PLAN")}


def kontrollera():
    d = indata()
    fel = []
    if not RE_KORNING.match(d["KORNING"]):
        fel.append("ogiltigt körnings-id")
    if not RE_REPO.match(d["REPO"]) or d["REPO"].endswith(".git"):
        fel.append("ogiltig repoadress (bara https://github.com/<ägare>/<repo>)")
    if not RE_COMMIT.match(d["COMMIT"]):
        fel.append("commit ska vara 40 hex-tecken")
    plan = None
    if len(d["PLAN"]) > MAX_PLAN_TECKEN:
        fel.append("planen är för lång")
    else:
        try:
            plan = json.loads(d["PLAN"])
        except ValueError:
            fel.append("planen är inte JSON")
    if plan is not None:
        fel += kontrollera_plan(plan)
    p = {"version": 1, "korning": d["KORNING"] if RE_KORNING.match(d["KORNING"]) else None,
         "repo": d["REPO"] if RE_REPO.match(d["REPO"]) else None,
         "commit": d["COMMIT"] if RE_COMMIT.match(d["COMMIT"]) else None,
         "plan": plan if not fel else None, "startad": nu(), "utfall": "fel" if fel else "kontrollerat",
         "fel": fel, "steg": [], "skanning": None, "bedomning": None, "filer": []}
    skriv_paket(p)
    return 1 if fel else 0


def hamta():
    p = las_paket()
    if p["utfall"] != "kontrollerat":
        return 1
    mal = os.path.join(arbete(), "repo")
    git = ["git", "-c", "protocol.file.allow=never", "-c", "core.hooksPath=/dev/null"]
    t0 = time.time()
    try:
        subprocess.run(git + ["init", "-q", mal], check=True, timeout=30)
        subprocess.run(git + ["-C", mal, "fetch", "-q", "--depth", "1", p["repo"], p["commit"]],
                       check=True, timeout=300, stdin=subprocess.DEVNULL)
        subprocess.run(git + ["-C", mal, "checkout", "-q", "FETCH_HEAD"], check=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        p["utfall"] = "fel"
        p["fel"].append(f"repot gick inte att hämta på angiven commit: {type(e).__name__}")
        skriv_paket(p)
        return 1
    storlek = katalogstorlek(mal)
    p["hamtat"] = {"sekunder": round(time.time() - t0, 1), "byte": storlek}
    if storlek > MAX_REPO_BYTE:
        p["utfall"] = "fel"
        p["fel"].append("repot är större än 1 GB")
        skriv_paket(p)
        return 1
    p["utfall"] = "hamtat"
    skriv_paket(p)
    return 0


def katalogstorlek(rot):
    s = 0
    for d, _, filer in os.walk(rot):
        for f in filer:
            try:
                s += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return s


def las_skanning():
    """SkillSpectors JSON (risk_assessment.score, severity, recommendation). Saknas den: ok = False."""
    fil = os.path.join(arbete(), "skanning.json")
    try:
        with open(fil, encoding="utf-8") as f:
            r = json.load(f)
        ra = r["risk_assessment"]
        fynd = [i for i in (r.get("issues") or []) if isinstance(i, dict)]
        per_kategori = {}
        for i in fynd:
            nyckel = f"{i.get('category')}/{i.get('severity')}"
            per_kategori[nyckel] = per_kategori.get(nyckel, 0) + 1
        return {"ok": True, "poang": ra.get("score"), "allvar": ra.get("severity"),
                "rekommendation": ra.get("recommendation"), "antal_fynd": len(fynd),
                "per_kategori": dict(sorted(per_kategori.items(), key=lambda x: -x[1])),
                "fynd": [{"id": i.get("id"), "kategori": i.get("category"), "allvar": i.get("severity"),
                          "fil": (i.get("location") or {}).get("file"), "fynd": i.get("finding")}
                         for i in fynd[:20]]}
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {"ok": False, "orsak": f"ingen läsbar rapport ({type(e).__name__})"}


def kort(text):
    if len(text) <= MAX_UTDATA_TECKEN:
        return text
    return f"[… {len(text) - MAX_UTDATA_TECKEN} tecken bortklippta]\n" + text[-MAX_UTDATA_TECKEN:]


def kor_kommando(kommando, cwd, env, tidsgrans):
    t0 = time.time()
    proc = subprocess.Popen(["bash", "-c", kommando], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        ut, fel = proc.communicate(timeout=tidsgrans)
        tidsgrans_nadd = False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        ut, fel = proc.communicate()
        tidsgrans_nadd = True
    return {"slutkod": None if tidsgrans_nadd else proc.returncode, "tidsgrans_nadd": tidsgrans_nadd,
            "sekunder": round(time.time() - t0, 1),
            "stdout": kort(ut.decode("utf-8", "replace")), "stderr": kort(fel.decode("utf-8", "replace"))}


def kor():
    p = las_paket()
    if p["utfall"] != "hamtat":
        return 1
    p["skanning"] = las_skanning()
    if p["skanning"].get("rekommendation") in STOPPA_VID:
        p["utfall"] = "stoppat_av_skanning"
        p["klart"] = nu()
        skriv_paket(p)
        return 0
    plan = p["plan"]
    rot = arbete()
    repo, prov, ut = (os.path.join(rot, n) for n in ("repo", "prov", "ut"))
    os.makedirs(ut, exist_ok=True)
    kat = plan["kategori"]
    if kat != "ingen":
        shutil.copytree(os.path.join(UPPGIFTER, kat), prov, dirs_exist_ok=True)
    else:
        os.makedirs(prov, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ACTIONS_", "GITHUB_TOKEN", "INPUT_"))}
    env.update({"PROV": prov, "UT": ut, "CI": "true"})
    for k in ("PLAN", "KORNING", "REPO", "COMMIT"):
        env.pop(k, None)
    allt_ok = True
    poster = [("installera", {"kommando": k} if isinstance(k, str) else k) for k in plan.get("installera", [])] + \
             [("steg", s) for s in plan["steg"]]
    for typ, s in poster:
        r = kor_kommando(s["kommando"], repo, env, s.get("tidsgrans_s", STANDARD_STEG_S))
        r.update({"typ": typ, "namn": s.get("namn") or typ, "kommando": s["kommando"]})
        p["steg"].append(r)
        skriv_paket(p)
        if r["slutkod"] != 0:
            allt_ok = False
            if typ == "installera":
                break
    p["filer"] = samla_filer(ut, plan.get("spara", []))
    bedom = os.path.join(UPPGIFTER, kat, "bedom.py") if kat != "ingen" else None
    if bedom and os.path.isfile(bedom):
        r = kor_kommando(f"python3 {json.dumps(bedom)} {json.dumps(ut)}", rot, env, 120)
        try:
            p["bedomning"] = json.loads(r["stdout"])
        except ValueError:
            p["bedomning"] = {"fel": "bedom.py gav ingen JSON", "stderr": r["stderr"][-2000:]}
    p["utfall"] = "kort" if allt_ok else "kort_med_fel"
    p["klart"] = nu()
    skriv_paket(p)
    return 0


def samla_filer(ut, spara):
    mal_rot = os.path.join(arbete(), "testpaket", "filer")
    ut_real = os.path.realpath(ut)
    sparade, totalt = [], 0
    for rel in spara:
        if not sakert_relativt(rel):
            continue
        kalla = os.path.realpath(os.path.join(ut, rel))
        post = {"sokvag": rel}
        if not kalla.startswith(ut_real + os.sep) or not os.path.isfile(kalla):
            post["fel"] = "finns inte i UT"
        else:
            storlek = os.path.getsize(kalla)
            if storlek > MAX_FIL_BYTE or totalt + storlek > MAX_FILER_BYTE:
                post["fel"] = "för stor"
            else:
                mal = os.path.join(mal_rot, rel)
                os.makedirs(os.path.dirname(mal), exist_ok=True)
                shutil.copyfile(kalla, mal)
                totalt += storlek
                post["byte"] = storlek
        sparade.append(post)
    return sparade


def main(argv):
    steg = {"kontrollera": kontrollera, "hamta": hamta, "kor": kor}
    if len(argv) != 2 or argv[1] not in steg:
        print(__doc__)
        return 2
    return steg[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
