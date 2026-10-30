# Moodle

Campus keeps the Hochschule's Moodle up to date (`courses/moodle.py`): each minute the courses changed since
(`manage.py moodle --changed`, compose service `moodlesync`), each night all of them (`manage.py moodle`, with
`prune`), which also enrols whoever has signed in to Moodle since. Off without `MOODLE_URL` and `MOODLE_TOKEN`. Campus
is the record, Moodle follows.

## What goes over

- **Courses**: each Lehrveranstaltung of a published semester not yet over, as a Moodle course: its title, the
  semester's dates, ID number `campus-course-<id>`, by which Campus finds it again should its own link be lost. Its
  summary is laid out as HfM's „Kursinfo“, which teachers there write by hand: who teaches, the weekly date and room,
  the Lehrform, the places, the modules it counts for, the Anmeldeschluss, then the description. A course called off is
  hidden, its people kept. Campus keeps a checksum of what it last sent (`MoodleCourse.state`), so that each minute only
  what changed goes.
- **Categories**: semester (`Wintersemester 2026/27`), Fachgebiet, Lehrform, as HfM's Moodle has them; Campus's carry
  ID numbers `campus-…`, so that it finds them again. They go under `MOODLE_CATEGORY` (0: the top).
- **People**: teachers with the role `MOODLE_TEACHER_ROLE` (3, Moodle's „Trainer:in“), participants with
  `MOODLE_STUDENT_ROLE` (5, „Teilnehmer:in“), by manual enrolment. Campus finds them in Moodle by e-mail address and
  makes no accounts: whoever has not signed in to Moodle yet is enrolled the night after they have (on the demo, see
  below, Campus makes them). Whoever leaves a
  course in Campus is taken out; Campus takes out only whom it put in (`MoodleCourse.people`), so what teachers set up
  themselves, self-enrolment or guests, stays.

## In Moodle

1. Web services and the REST protocol on (Website-Administration › Server › Webservices).
2. A service with these functions, a user for Campus allowed to use it, and a token for that user:
   `core_course_get_categories`, `core_course_create_categories`, `core_course_get_courses_by_field`,
   `core_course_create_courses`, `core_course_update_courses`, `core_user_get_users_by_field`,
   `enrol_manual_enrol_users`, `enrol_manual_unenrol_users`; on the demo also `core_user_create_users`.
3. Manual enrolment on for new courses (Moodle's default).

## Checked

Against Moodle 5.3 with PHP 8.4.27 and PostgreSQL 17, run locally (30.10.2026): three courses of the demo with 336
people, under semester, Fachgebiet and Lehrform; Trainer:in and Teilnehmer:in as they should be; a second run changed
nothing; a participant taken out in Campus was taken out in Moodle, a course called off hidden; with Campus's links
lost, the courses were taken up again, not made twice. Moodle sends a welcome message on enrolment: without mail, as in
that test, the call fails, so a Moodle needs mail or `noemailever`.

## The demo's Moodle

https://moodle.concampus.de, on the demo's server next to Campus (`demo-moodle/`), set up as HfM's Moodle (seen
30.10.2026): Moodle 4.5 LTS with the theme Boost Union, HfM's red (#9E000C) and logos, the dashboard with the same
blocks, its categories „Allgemeine Informationen / Fortbildungen“ and „Informationen (Studienfächer / Institute)“ beside
the semesters, the information courses the dashboard links to, open to guests; English by default, German and Spanish
too. Moodle's PHP image (`moodlehq/moodle-php-apache:8.3`), its cron, its own Postgres, served by Campus's Caddy.
Sign-in required, no mail (`noemailever`: the people are made up, their addresses not); no Shibboleth. There, Campus
makes the accounts too (`core_user_create_users`, on with `DEMO`): the demo's users (`student`, `lehre`, …) sign in with
the demo's access code, the others cannot sign in and fill the participant lists. Campus's account `campus-sync` is a
site administrator; in production a role with only the functions above.

Set up once, on the server:

1. `git clone -b MOODLE_405_STABLE --depth 1 https://github.com/moodle/moodle.git /opt/moodle`; Boost Union's
   `MOODLE_405_STABLE` (github.com/moodle-an-hochschulen/moodle-theme_boost_union) into `/opt/moodle/theme/boost_union`;
   `touch /opt/moodle/config.php` (the mount point of `demo-moodle/config.php`).
2. In `/opt/concampus/demo-moodle`: `.env` with `DB_PASSWORD`; `docker compose up -d`; `docker compose exec --user
   www-data moodle php admin/cli/install_database.php --agree-license --fullname="Moodle der HfM Weimar · Demo" --shortname="Moodle HfM"
   --adminuser=admin --adminpass=… --adminemail=…`; `docker compose exec -T --user www-data moodle php < setup.php`,
   which prints the token.
3. In Campus's `.env`: `MOODLE_URL=https://moodle.concampus.de`, the token as `MOODLE_TOKEN`; `docker compose up -d`.
4. `/etc/cron.d/moodle`: `0 4 * * 1 root /opt/concampus/demo-moodle/update.sh`, Moodle's and PHP's fixes each week.

After a reseed, Moodle follows within minutes. Where Moodle cannot be changed from outside: the Kursinfo stands in
the course summary, not in a section of its own as at HfM, since Moodle's web services cannot write a section.

## Not yet

Lesson groups and ensembles as Moodle groups; grades back from Moodle; HfM's own category names where they differ from
Campus's Fachgebiete and Lehrformen.
