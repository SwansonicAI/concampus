# Studierendenmeldeverfahren

The reports between the Hochschule and the statutory health insurers about each student's insurance (§ 199a SGB V).
Electronic only, both ways, since 1.1.2022; the insurers accept no paper. Inside Campus it is built
(`students/smv.py`, Studierende › Krankenkassen); the files to and from dakota are not, as they wait for the GKV's XML
schemata. From research of October 2026; what is still to be checked is listed at the end.

## The rules

- **§ 199a SGB V**: the duty to report, and the Absendernummer under § 18n SGB IV that each Hochschule applies for at
  the GKV-Spitzenverband. It is stored there in the Hochschuldatei.
- **Gemeinsame Grundsätze** of the GKV-Spitzenverband and the HRK, approved by the Bundesministerium für Gesundheit;
  version 07.2023, with Anlagen for the data and a Verfahrensbeschreibung, at gkv-datenaustausch.de.
- **Schnittstelle 2.0**: the insurers send it since 1.7.2023, Hochschulen since 1.12.2023 at the latest; reports in the
  old version are rejected. Since then a Hochschule sends no gender, and may send the address and an Exmatrikulation
  before the semester's end.

## The reports

| Meldung | From | When | What Campus does with it |
|---|---|---|---|
| M10 | Insurer | The applicant asks their insurer, naming the Hochschule's Absendernummer; status insured, or exempt. Only a statutory insurer sends it: the privately insured go to one with their exemption | Match it to the applicant, set insurer, status and day; Immatrikulation waits for it, as now (`Person.missing()`) |
| M11 | New insurer | The student changes insurer, from a date | Change the insurer; send M20 to the new one once the date has come |
| M12 | Insurer | Contributions unpaid | A Sperre on the Rückmeldung; still unpaid at the semester's change, Exmatrikulation for reason 7 |
| M13 | Insurer | Arrears paid | Lift that Sperre |
| M20 | Hochschule | Immatrikulation: the semester's start and the day of enrolment | Send it when enrolling, and to a new insurer after M11 |
| M30 | Hochschule | Exmatrikulation: the semester's end and the day | Send it when exmatriculating (`students/views.py` already tells the office the insurer will receive it) |

A report sent in error is cancelled (Storno): M20 when an enrolment is taken back, M30 when an Exmatrikulation is. At
OTH Regensburg, M30 after a missing Rückmeldung was often cancelled because the payment came late.

Outside the procedure, by RSH Düsseldorf's example: Gasthörer:innen, Jungstudierende (family insurance), students whose
home Hochschule reports them, Erasmus guests. Over 30 the student insurance ends; Hochschulen differ on whether they
still ask for an M10.

## Sending and receiving

- **Absendernummer**: „H“ and seven digits, one per Hochschule; HS Schmalkalden publishes H0001281. Applicants need it
  to ask for their M10, so it belongs on Meine Bewerbung and in the acceptance letter.
- **Data**: XML after the schemata on gkv-datenaustausch.de, validated against them before sending.
- **Way**: through the insurers' Kommunikationsserver, encrypted, with a certificate from the ITSG Trust Center applied
  for through dakota. It costs a fee and runs out (yearly, for dakota.le); one from another data-exchange program
  cannot be reused.
- **dakota** (ITSG): the program that sends and fetches the reports; dakota.ag is the version with the
  Kommunikationsserver. ITSG offers it as a module for software makers. HIS supplies it to HISinOne Hochschulen within
  their fees.
- **Without dakota**: straight to the Kommunikationsserver over eXTra, the transport standard dakota itself uses, with
  an ITSG certificate.
- **How others run it**: OTH Regensburg runs dakota on a Windows VM that talks to HISinOne over https; a job sends and
  fetches on weekday mornings and mails a log, without passwords, to the administrator. A file with the wrong sequence
  number or an unknown response ID never reaches the database, so errors must be fixed at once or they multiply. dakota
  leaves `KomMalFunc.txt` when the server was not reached; nothing is sent then.

## Using HfM's dakota

The likeliest way: Campus connects to the dakota HfM already runs, instead of one of its own.

- The Absendernummer and the certificate are HfM's, not HIS's: they stay.
- HIS supplies dakota's licence within the HISinOne fees; without HISinOne, HfM may need its own from ITSG.
- HISinOne talks to dakota through HIS's script on the dakota machine (`smv.py` at OTH). Campus needs its own
  counterpart there: it hands dakota Campus's reports and passes on what dakota fetched, over https. Hosted outside
  HfM, Campus must be reachable from that machine through HfM's proxy.
- One system takes the reports at a time: HISinOne stops on a set day and Campus takes over, with the reports still
  open in HISinOne carried across.
- Our part on HfM's machine falls under the processor agreement (`docs/legal.md`).

## In Campus

- **Absendernummer** under Einstellungen › Hochschule; applicants read it where they accept their place, students where
  a missing report holds their Rückmeldung back.
- **The insurer** by its Betriebsnummer from its report (`Person.insurer_number`), beside its name.
- **Reports in** (`receive()`): booked to the one person applying or studying here born that day, with that first name
  and surname or birth name. What matches nobody, or several, waits under „Ohne Zuordnung“ with the people it may be
  about, to book by hand or drop; a to-do says so. M10 sets insurer and status and lets the Immatrikulation go ahead;
  „nicht versichert“ does not. M11 changes the insurer, and an M20 to the new one waits for the day it insures. M12
  holds the Rückmeldung back (`Person.missing()`), M13 lets it go; who has not re-registered at the semester's change
  is exmatriculated for reason 7, as before.
- **Reports out** (`tell()`): from the list of status changes (`students.models.Event`), made when someone's first
  study here begins or their last ends (`Study.set_status()`); a change of subject or a second study makes none. M20
  with the semester and the day of enrolment, M30 with the semester and the last day; only for the statutorily insured.
- **Storno** of a report sent; one not sent yet is dropped. One the insurer turned down is made anew once the record is
  right; a to-do says so.
- **Who sees them**: the student office for the whole Hochschule alone (`docs/privacy.md`). They go with the file
  (`students/disposal.py`); reports about nobody here after the period for applications.

## Still to build

1. The files: reports written and read after the GKV's XML schemata (KVdS 2.0), with their sequence numbers, handed to
   dakota and fetched from it on weekday mornings; until then the Hochschule's wait as open, and nothing arrives.
2. dakota: HfM's own (above); otherwise a Windows machine of ours, or ITSG's module built in. For several
   Hochschulen, one Absendernummer and certificate each.
3. A test against the procedure's test system before going live.
4. Current students' insurer, its number and their status taken over from HISinOne, for their M30.

## To check

1. HfM's Absendernummer: not on its website. How HfM runs the procedure today: where dakota runs and who looks after
   it, its licence through HIS or ITSG, the certificate's holder and expiry, how HISinOne exchanges files with it,
   how many reports a semester, who matches those that match no one.
2. The current Grundsätze, Verfahrensbeschreibung and Anlagen: each report's fields, Storno, deadlines. gkv-datenaustausch.de
   did not answer during this research.
3. dakota for a software maker: licence, platform, whether it runs on Linux, one installation for several Hochschulen;
   or eXTra without dakota.
4. Whether HfM asks for an M10 from students over 30, Promovierende and Jungstudierende.
5. How long the reports are kept; Campus keeps them with the file.
6. Whether an insurer that exempted a student also wants M20 and M30; Campus sends them only for the statutorily insured.
7. Which fields identify a person in the insurers' reports: Campus matches by birth date and names, and would use the
   Krankenversichertennummer or Matrikelnummer if the reports carry them.

## Sources

- [§ 199a SGB V](https://www.gesetze-im-internet.de/sgb_5/__199a.html)
- [Gemeinsame Grundsätze, 07.2023](https://www.gkv-datenaustausch.de/media/dokumente/studenten_meldeverfahren/grundsaetze_2/GG_KVdS_MV_07.2023.pdf), [Anlage 1](https://www.gkv-datenaustausch.de/media/dokumente/studenten_meldeverfahren/grundsaetze_anlagen/GG_KVdS_MV_07.2023_Anlage_1_Meldung_KK.pdf), [Verfahrensbeschreibung](https://www.gkv-datenaustausch.de/media/dokumente/studenten_meldeverfahren/verfahrensbeschreibung_2/VB_KVdS-MV.pdf)
- [HIS, Studenten-Meldeverfahren](https://www.his.de/smv)
- [OTH Regensburg, pilot with HISinOne (HIS, 9.11.2021)](https://www.his.de/fileadmin/his/Praesentationen/Online-Veranstaltungen/VA_85_Erfahrungsbericht_OTH.pdf)
- [ITSG, dakota.ag and dakota.le](https://www.itsg.de/produkte/dakota-ag-dakota-le/)
- [geos, eSMV with dakota.ag](https://www.geosmbh.net/dakota-ag/)
- [RSH Düsseldorf, SMV](https://www.rsh-duesseldorf.de/campus/beratung-und-service/studierendenservice/studenten-meldeverfahren-smv)
- [Hochschule Bochum, Krankenkassen-Meldeverfahren](https://www.hochschule-bochum.de/studium/vor-dem-studium/bewerben-einschreiben/neues-krankenkassen-meldeverfahren/)
- [Uni Gießen](https://www.uni-giessen.de/de/studium/bewerbung/einschreibung/kkv), [Frankfurt UAS](https://www.frankfurt-university.de/de/studium/studienorganisation/das-studierenden-meldeverfahren-der-krankenkassen/): M10 over 30
- [HS Schmalkalden, Merkblatt SMV](https://www.hs-schmalkalden.de/fileadmin/portal/Dokumente/Studentische_Angelegenheiten/Studierendenb%C3%BCro/SMV.pdf)
- [softguide, dakota.le](https://www.softguide.de/programm/dakota-le-verschluesselter-datentraegeraustausch/vorschau): the certificate's fee and term
