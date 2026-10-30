<?php
// Once after Moodle's install, again at will: German, sign-in required, web services for Campus with a service of
// the functions it calls, an account for Campus, its token printed. Run as in docs/moodle.md.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->dirroot . '/webservice/lib.php');
require_once($CFG->dirroot . '/user/lib.php');

(new \tool_langimport\controller())->install_languagepacks('de');
foreach ([
    'lang' => 'de', 'timezone' => 'Europe/Berlin', 'country' => 'DE', 'forcelogin' => 1, 'guestloginbutton' => 0,
    'passwordpolicy' => 0,  // Campus gives the demo's users its access code, whatever it is
    'auth_instructions' => 'Demo: der Benutzername wie in Campus (student, lehre, …), als Passwort der Zugangscode der Demo.',
    'enablewebservices' => 1, 'webserviceprotocols' => 'rest',
] as $name => $value) {
    set_config($name, $value);
}

$functions = ['core_course_get_categories', 'core_course_create_categories', 'core_course_get_courses_by_field',
    'core_course_create_courses', 'core_course_update_courses', 'core_user_get_users_by_field', 'core_user_create_users',
    'enrol_manual_enrol_users', 'enrol_manual_unenrol_users'];
$manager = new webservice();
if (!$service = $DB->get_record('external_services', ['shortname' => 'campus'])) {
    $service = $DB->get_record('external_services', ['id' => $manager->add_external_service((object) [
        'name' => 'Campus', 'shortname' => 'campus', 'enabled' => 1, 'restrictedusers' => 0, 'downloadfiles' => 0, 'uploadfiles' => 0,
    ])]);
}
foreach ($functions as $function) {
    if (!$manager->service_function_exists($function, $service->id)) {
        $manager->add_external_function_to_service($function, $service->id);
    }
}

if (!$campus = $DB->get_record('user', ['username' => 'campus-sync'])) {
    $campus = $DB->get_record('user', ['id' => user_create_user((object) [
        'username' => 'campus-sync', 'firstname' => 'Campus', 'lastname' => 'Abgleich', 'email' => 'campus-sync@localhost',
        'auth' => 'manual', 'password' => random_string(32), 'confirmed' => 1, 'mnethostid' => $CFG->mnet_localhost_id,
    ])]);
}
if (!in_array($campus->id, explode(',', $CFG->siteadmins))) {  // a demo: in production a role with only what it calls
    set_config('siteadmins', $CFG->siteadmins . ',' . $campus->id);
}
$token = $DB->get_field('external_tokens', 'token', ['userid' => $campus->id, 'externalserviceid' => $service->id])
    ?: \core_external\util::generate_token(EXTERNAL_TOKEN_PERMANENT, $service, $campus->id, context_system::instance());
purge_all_caches();
echo "MOODLE_TOKEN=$token\n";
