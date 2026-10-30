<?php  // The demo's Moodle (docs/moodle.md); the database password from .env.

unset($CFG);
global $CFG;
$CFG = new stdClass();

$CFG->dbtype    = 'pgsql';
$CFG->dblibrary = 'native';
$CFG->dbhost    = 'db';
$CFG->dbname    = 'moodle';
$CFG->dbuser    = 'moodle';
$CFG->dbpass    = getenv('DB_PASSWORD');
$CFG->prefix    = 'mdl_';

$CFG->wwwroot   = 'https://moodle.concampus.de';
$CFG->dataroot  = '/var/www/moodledata';
$CFG->admin     = 'admin';
$CFG->directorypermissions = 02777;

$CFG->sslproxy = true;  // Caddy speaks HTTPS and passes on plain HTTP
$CFG->getremoteaddrconf = 1;  // the visitor's address from Caddy's X-Forwarded-For
$CFG->noemailever = true;  // the demo's people are made up, their addresses not: no mail goes out
$CFG->disableupdateautodeploy = true;  // code changes only through update.sh

require_once(__DIR__ . '/lib/setup.php');
