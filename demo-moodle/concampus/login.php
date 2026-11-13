<?php
// Where ConCampus sends whoever it signs in (courses/moodle.py, entry): username and time, signed with the token of
// ConCampus's web service, good for a minute. Whoever was signed in before is signed out.
require(__DIR__ . '/../../config.php');
require_once($CFG->dirroot . '/login/lib.php');

$username = required_param('u', PARAM_USERNAME);
$time = required_param('t', PARAM_INT);
$signature = required_param('s', PARAM_ALPHANUM);
$key = $DB->get_field_sql("SELECT t.token FROM {external_tokens} t JOIN {external_services} s ON s.id = t.externalserviceid
    JOIN {user} u ON u.id = t.userid WHERE s.shortname = 'campus' AND u.username = 'campus-sync'");
$user = get_complete_user_data('username', $username);
if (!$key || abs(time() - $time) > 60 || !hash_equals(hash_hmac('sha256', "$username|$time", $key), $signature)
        || !$user || $user->auth !== 'concampus' || $user->suspended) {
    throw new moodle_exception('invalidlogin');
}
if (isloggedin()) {
    require_logout();
}
complete_user_login($user);
redirect(core_login_get_return_url());
