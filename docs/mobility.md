# Stays abroad and Erasmus Without Paper

ConCampus keeps the Hochschule's Erasmus+ partners, the agreements with them, and its students' stays abroad with their
Learning Agreements (`mobility/`, Internationales). Its data follows Erasmus Without Paper (EWP), the network through
which Hochschulen exchange agreements and Learning Agreements since 2021, so that a connection to it carries them over;
the connection itself is not built.

## In ConCampus

- **Partnerhochschulen and Abkommen** (Internationales › Partnerhochschulen; Einstellungen): each partner with its
  Erasmus code and EWP id; each agreement with its Studienjahre, ISCED-F field, EQF levels, places and months each way,
  teaching stays, the recommended language, and the day each side signed. The list shows how many places of the year
  are taken.
- **Auslandsaufenthalte**: the International Office notes a student's stay at a partner (Studium or Praktikum, from
  and to); the agreement of that Studienjahr comes with it.
- **Learning Agreement**: the student enters what they take there (Table A, with ECTS) and the modules here it counts
  for (Table B), and signs; the International Office approves for the Hochschule and notes the partner's approval. A
  line changed after signing asks for every signature anew; once all three had signed, it counts as a change. To-dos:
  the student's to fill in and sign, the office's to approve.
- **Anerkennung**: back home, whoever recognises for the study opens the stay and goes from each module of Table B to
  the Anerkennung, filled in with the module, the partner, its country, the semester and the ECTS; the to-do says
  which stays still have modules open.

Rights: `manage_mobility` (International Office, for the whole Hochschule); the student their own stay; `recognise`
reads the stays of the studies it may recognise in.

## Onto EWP

| ConCampus | EWP |
|---|---|
| `Partner` | HEI (`hei-id`: the SCHAC code), with its Erasmus code |
| `Agreement` | IIA (v7): one `student-studies-mobility-spec` each way (`mobilities-per-year`, `total-months-per-year` from places × months, `subject-area` with the ISCED-F code, `eqf-level`, `recommended-language-skill`), `staff-teacher-mobility-spec` for teaching stays, the academic years, each side's signature |
| `Mobility` | outgoing mobility (OMobility): sending and receiving HEI, planned dates, the student |
| `Component` A / B | Online Learning Agreement: `components-studied` / `components-recognized`, with credits |
| signatures | `student-signature`, `sending-hei-signature`, `receiving-hei-signature` |
| a change after approval | a `changes-proposal`, approved anew |

## Connecting

Three ways; which one is HfM's to say.

1. **The EWP Dashboard** of the European University Foundation, free for Hochschulen without their own connection:
   agreements and Learning Agreements are kept there, ConCampus keeps its own and the International Office carries them
   over by hand, or by the Dashboard's interface where it has one (to check).
2. **ConCampus as an EWP node**: a manifest in the EWP registry for each Hochschule, signed requests both ways (HTTP
   signatures with registered keys), and the APIs: Institutions, Organizational Units, IIAs with approval and change
   notifications, Outgoing Mobilities, OMobility Learning Agreements; tested on EWP's development network before the
   live one.
3. **The mobility software HfM uses now**, if it has one already connected to EWP: ConCampus then leaves agreements and
   Learning Agreements there and takes over only what recognition needs.

## Not yet

Students coming here (nominations from partners, their admission and enrolment, their Learning Agreements to approve
as receiving Hochschule); staff mobility beyond the number of places; applying for a place and the selection; the
Erasmus grant; the partner's Transcript of Records; the stays in the official exam statistics (EF135 to EF146, whose
code lists for the kind of stay and programme are to check).

## To check with HfM's International Office

1. Which software it uses now for Erasmus, and whether that is connected to EWP.
2. Who signs Learning Agreements for the Hochschule: the International Office, or a coordinator for each subject.
3. Whether students apply for Erasmus places through ConCampus.

## Sources

- [EWP Dashboard (European University Foundation)](https://uni-foundation.eu/2026/02/12/the-ewp-dashboard-a-look-at-an-ewp-unsung-hero/)
- [European Commission, digital nominations guide, March 2026](https://ec.europa.eu/assets/eac/erasmus-plus/student-card/user-guide-digital-nominations-mar26_en.pdf)
- [EWP registry](https://registry.erasmuswithoutpaper.eu/manifestsOverview), [IIA API](https://deepwiki.com/erasmus-without-paper/ewp-specs-api-iias)
- [HfM Weimar, partner institutions](https://www.hfm-weimar.de/en/university/international/exchange-programmes/partner-institutions): the demo's partners
