<?php
// The demo's sign-in through ConCampus (docs/moodle.md), mounted as auth/concampus.
defined('MOODLE_INTERNAL') || die();

$plugin->component = 'auth_concampus';
$plugin->version = 2026111300;
$plugin->requires = 2024100700;  // Moodle 4.5
