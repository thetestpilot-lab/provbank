# provbank

Testkörningen för kanalmotorns steg 2 (The Test Pilot). Repot speglas till **`thetestpilot-lab/provbank`** på GitHub (publikt, Marcus 2026-11-25: "publikt, tänker inte betala något") och körs bara där, i GitHub Actions. Främmande kod körs aldrig på servern (kanalfilen: `test.motor` = `github_actions`). Beställningen står i Utvecklarens `Arbetslista.md` (kanalmotorn fas 1, steg 2) och planen i samma rad 2026-11-25.

Inga secrets i repot, inga rättigheter i jobbet (`permissions: {}`), `persist-credentials: false`, actions låsta på commit. Bara stdlib (Python 3.9+).

SkillSpector (NVIDIA) prövades som filter före körningen och togs bort 2026-11-25 (Marcus: "strunta och ta bort"): det är byggt för skills och MCP-servrar och har inget läge för vanliga repon. `psf/black` fick 100/100 `DO_NOT_INSTALL` med 117 falsklarm (`.git/`-filer, PR-mallen som prompt injection, Blacks flagga "skip the safety"). Skyddet är isoleringen i Actions.

## Flödet
1. Kanalmotorn (servern) startar `prov.yml` med `workflow_dispatch` och fyra indata: `korning` (id), `repo` (`https://github.com/<ägare>/<repo>`), `commit` (40 hex) och `plan` (JSON, se nedan). Körningen heter `prov <korning>`, så att motorn hittar den.
2. Typsnitt för skärmbilder (`fonts-noto-cjk`, `fonts-noto-color-emoji`), annars blir kinesisk text och emoji rutor (minimind 2026-11-25).
3. `prov.py kontrollera`: indata och plan mot reglerna. Fel ger `utfall: fel` och inget mer körs.
4. `prov.py hamta`: `git fetch --depth 1` av exakt commit, utan lokala protokoll och utan krokar, högst 1 GB.
5. `prov.py kor`: kopierar provuppgiften (`uppgifter/<kategori>/`) till `$PROV`, kör `installera` och `steg` i repots mapp med `bash -c`, tidsgräns per steg (högst 600 s, hela jobbet 25 min), stdout och stderr högst 20 000 tecken var. Faller en installation körs inga steg. Sedan `bedom.py` för kategorin om den finns, och filerna i `spara` (ur `$UT`, högst 5 MB var och 20 MB totalt).
6. Testpaketet (`arbete/testpaket/`: `paket.json` och `filer/`) laddas upp som artefakten `testpaket-<korning>`, sparad 7 dagar.

## Planen
```json
{"kategori": "ingen | <mapp i uppgifter/>",
 "installera": ["pip install ."],
 "steg": [{"namn": "sammanfatta", "kommando": "python -m x \"$PROV/text.txt\" > \"$UT/svar.txt\"", "tidsgrans_s": 300}],
 "spara": ["svar.txt"]}
```
Högst 10 kommandon, 1 000 tecken per kommando och 20 000 tecken för hela planen. `spara` är sökvägar under `$UT`, utan `..` och symlänkar ut.

## Testpaketet (`paket.json`)
`version`, `korning`, `repo`, `commit`, `plan`, `startad`, `klart`, `utfall` (`kort`, `kort_med_fel` eller `fel`), `fel`, `hamtat`, `steg` (`typ`, `namn`, `kommando`, `slutkod`, `tidsgrans_nadd`, `sekunder`, `stdout`, `stderr`), `bedomning` (det `bedom.py` skriver) och `filer`. Kanalmotorn läser det (kontrakt).

## Kända gränser
- Den främmande koden kör som samma användare som `prov.py` på GitHubs maskin och kan därför ändra `paket.json` eller `bedom.py` och förfalska sitt eget utfall. Det kan inte nå något av Marcus, men betyget sätts därför alltid efter Marcus tryck, med stegen och utdata synliga.
- GitHubs villkor för Actions förbjuder bland annat kryptobrytning och aktivitet som inte hör till projektet i repot. Att prova andras repon är en gråzon. Därför ligger det i en egen organisation, aldrig under kontot med `marcusos-vault`.
- Publikt repo: körloggarna och testpaketen syns för alla i 7 dagar.

## Provuppgifterna
`uppgifter/` är tom tills Marcus har godkänt provbänken (kanalfilen `provbank`, med Kontent-agenten). Utan den finns bara `kategori: "ingen"`.

## Kontroll
```
python3 -m unittest discover -s tests -v
```
