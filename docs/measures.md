# Technical and organisational measures

What ConCampus and its operation do to protect personal data (Art. 32 GDPR), as the Hochschule needs it for its records
of processing (Art. 30(1)(g)), its impact assessment (Art. 35) and the processor agreement (Art. 28(3)(c)). Each
measure names where it is, so that it can be checked; what is still missing is marked **Open**. State of 30.10.2026,
for the demo at hfm-weimar.concampus.de; where production will differ, it says so. Who sees what: `docs/privacy.md`;
periods and rules: `docs/rules.md`; the legal requirements: `docs/legal.md`.

## Confidentiality

### Physical access (Zutrittskontrolle)

- The application runs at Hetzner Online GmbH, data centre Nuremberg (ISO/IEC 27001 certified); the database at Neon
  on AWS Frankfurt. Neither ConCampus nor its operator keeps a server of its own; physical access is the providers',
  under their certifications. **Open**: production at an EU-owned provider, with BSI C5 where a cloud is used
  (`docs/legal.md`).

### System access (Zugangskontrolle)

- Server: SSH with keys only, no passwords (`PasswordAuthentication no`, root `prohibit-password`); open ports 22, 80
  and 443 only, the databases not reachable from outside. The deployment key may run nothing but `deploy.sh`
  (`restrict,command=`), the product page's key nothing but its `git pull`.
- Secrets (database, mail, SAML keys, the demo's access code) are in the server's `.env`, readable by the deploy user
  only (`chmod 600`), never in the repository.
- Sign-in: through the Hochschule's login (SAML, `core/sso.py`), whose second factor then counts; applicants, who have
  no Hochschule account, with a password. Passwords are stored hashed (Django's PBKDF2) and checked against length,
  common passwords and the person's own data (`AUTH_PASSWORD_VALIDATORS`). Five failures for one name from one address
  hold it back there for a quarter of an hour (`core/backends.py` `held()`).
- Sessions end after two hours without a request and when the browser closes (`SESSION_COOKIE_AGE`,
  `SESSION_EXPIRE_AT_BROWSER_CLOSE`; a request renews the time at most every five minutes, `core.middleware.renewal`);
  cookies only over HTTPS (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`).
- Every page needs a sign-in (`LoginRequiredMiddleware`), but for the login, the check of a Bescheinigung's code, a
  person's calendar feed by its secret signed link, and the public calendar of concerts. The demo also asks a shared access code first (`core/middleware.py` `gate`).
- Accounts of people who leave are deactivated, which ends sign-in at once (Einstellungen › Konten).

### Data access (Zugriffskontrolle)

- Rights are capabilities held by offices, never by people directly (`core/access.py`); an office may be limited to
  an Institut or Studiengänge (`Person.within()`). Everything else follows from what someone does: their own file, their
  courses, the auditions they judge. Who may see which data is set out tier by tier in `docs/privacy.md`.
- Four eyes for rights: joining an office and new rights for an office take effect only once a second person who keeps
  accounts confirms them (`Membership`, `Grant`, Einstellungen › Freigaben).
- Uploaded files are not served from a public folder: each goes out through a view that checks who may open it
  (`admissions/views.py` `document()`); the jury sees only documents marked for it. Health data is opened only to the
  person and the offices whose decision depends on it.
- The Django admin is for superusers only and not used in daily work; the demo's system account cannot sign in.

### Separation (Trennungsgebot)

- Each Hochschule runs its own instance with its own database, no data shared between them (`docs/roadmap.md`, „For
  other Hochschulen“); so far there is HfM's demo only.
- Within a Hochschule, purposes are kept apart by capability: the statistics office sees figures, not names
  (`view_statistics`); the jury sees applications, not files; teachers see their courses, not grades elsewhere.
- Development and tests run on generated data only (`manage.py seed`); real data never leaves production.

### Pseudonymisation and encryption

- In transit: HTTPS only, with HSTS for a year (`SECURE_HSTS_SECONDS`); the database connection with TLS
  (`sslmode=require`); mail, once configured, with STARTTLS or TLS (`EMAIL_URL`); Moodle's web services over HTTPS.
- At rest: the database encrypted by its provider (Neon, AES-256; to confirm in its terms). **Open**: uploaded files lie
  on the server's disk unencrypted.
- People made anonymous keep only what the figures count (`Person.anonymise`, `Person.COUNTED`); the statistics count
  them without a name.

## Integrity

### Input (Eingabekontrolle)

- Every change to a record that matters is kept in its Verlauf: who, when, each field before and after; on deletion
  all of it, so that it can be restored (`core.models.Change`, `core/history.py`). The Verlauf is kept per kind of
  record as long as its period allows (`Tracked.keep`).
- Grades: a deviating grade needs a reason; released results change only through the Prüfungsausschuss, the change
  recorded; Bescheide go out only after the office has seen a preview (`docs/rules.md`).
- Forms check what is typed in as the database would, and keep what was refused in the session, never in the address
  (`core/forms.py`). Every form is protected against cross-site requests (CSRF); pages are not framed by others
  (`X-Frame-Options`), content types are not guessed (`nosniff`).
- Files: by type and size per kind of document (`DocumentType.file_types`, `max_mb`, 20 MB by default). A logo, the only
  file shown in the page, is served sandboxed (`Content-Security-Policy: sandbox`). **Open**: no scan for malware; no
  Content-Security-Policy for the pages themselves.

### Transfer (Weitergabekontrolle)

- Every file with personal data that leaves (student lists, Notenlisten, the official statistics, the complete copy)
  is logged with who, when, what and how many rows (`core.models.Export`, Einstellungen › Exporte), kept two years.
- Bescheinigungen carry a check code that anyone handed one can verify; the XHochschule files are checked against the
  standard's schemas before they go out (`students/xhochschule.py`).
- Interfaces send only what their purpose needs: to Moodle the course, its teachers and participants by e-mail
  address (`docs/moodle.md`); to the health insurers the reports the procedure prescribes (`docs/smv.md`); to the
  statistics office the official fields.
- Mail goes out only after the transaction it belongs to has been saved (`Message.mail`); the Postfach holds every
  message either way.

## Availability and resilience (Verfügbarkeitskontrolle)

- The application restarts by itself after a failure or reboot (`restart: unless-stopped`); the server's operating
  system installs security updates daily (`unattended-upgrades`).
- Database: the provider's point-in-time restore (Neon's restore window). **Open**: backup of uploaded files and of the
  demo's Moodle, kept apart from the server (`docs/legal.md`, „To do“).
- The code is in a Git repository with its whole history; a deployment builds the same containers from it
  (`deploy.sh`). Escrow of code and access for the Hochschule: `docs/escrow.md`.
- A complete copy of all data and files for a change of provider (`manage.py transfer --everything`,
  `docs/transfer.md`).

## Data protection by design and by default

- Each role sees by default what its task needs (`docs/privacy.md`); nobody lists all students; birth dates appear in
  searches only for the offices that need them.
- Periods: applications that came to nothing, former students' files and their Grunddaten, former staff, bookings,
  sign-ins, the export log; deleted nightly (`manage.py prune`, `docs/rules.md` „Data kept“).
- Health data only with consent, recorded with its wording and withdrawable (`core.models.Consent`).
- Restriction of processing (Art. 18) holds back every deletion of the person (`Person.restriction`).
- The person downloads a copy of everything kept about them (Art. 15, `core/dossier.py`).
- No tracking, no third-party content in the pages, only the cookies the sign-in needs.

## Testing and review (Art. 32(1)(d))

- Every change runs the automated tests before it is deployed (GitHub Actions, `.github/workflows/deploy.yml`): 147
  tests, among them the rights of every role on every page, the deletion periods, the restriction, the complete copy
  without passwords, and that no Hochschule's name is in the code.
- Accessibility checked automatically against WCAG 2.1 AA on 48 pages (`docs/legal.md`).
- **Open**: a security concept after BSI IT-Grundschutz (Profil für Hochschulen) and a penetration test; a manual
  accessibility test by a third party.

## Organisational

- Who operates ConCampus has the server's root key and the repository; nobody else. **Open**: a written role and
  deputy for operation, and a plan for incidents, with the 72-hour notice to the supervisory authority that falls to
  the Hochschule (Art. 33) and how the processor tells it at once.
- Processors used: Hetzner (hosting), Neon and through it AWS (database), GitHub (code and deployment, no personal
  data), the mail provider in `EMAIL_URL`. **Open**: the processor agreement with the Hochschule (Art. 28), listing them.
- The Hochschule's own measures (training, instructions, its Datenschutzbeauftragte's approval) are the Hochschule's;
  this document is its input, not its record.
