<?php
// Once after Moodle's install, again at will: the demo set up as HfM's Moodle is (seen 30.10.2026, docs/moodle.md),
// its theme and logos, its categories and information courses, its dashboard; sign-in required; web services for
// ConCampus with a service of the functions it calls, an account for ConCampus, its token printed.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->dirroot . '/course/lib.php');
require_once($CFG->dirroot . '/my/lib.php');
require_once($CFG->dirroot . '/user/lib.php');
require_once($CFG->dirroot . '/webservice/lib.php');

(new \tool_langimport\controller())->install_languagepacks(['de', 'es']);
foreach ([
    'lang' => 'en', 'timezone' => 'Europe/Berlin', 'country' => 'DE', 'forcelogin' => 1, 'guestloginbutton' => 0,
    'passwordpolicy' => 0,  // ConCampus gives the demo's users its access code, whatever it is
    'theme' => 'boost_union', 'defaulthomepage' => HOMEPAGE_MY, 'frontpage' => FRONTPAGECATEGORYCOMBO, 'frontpageloggedin' => FRONTPAGECATEGORYCOMBO,
    'enablewebservices' => 1, 'webserviceprotocols' => 'rest',
] as $name => $value) {
    set_config($name, $value);
}
foreach (['brandcolor' => '#9E000C', 'navbarcolor' => 'coloreddark',
    'logininstructionsbelow' => '<p>Demo: der Benutzername wie in ConCampus (student, lehre, …), als Passwort der Zugangscode der Demo.</p>',
    'footnote' => '<p>Demo · <a href="https://www.hfm-weimar.de">Hochschule für Musik FRANZ LISZT Weimar</a></p>'] as $name => $value) {
    set_config($name, $value, 'theme_boost_union');
}
$system = context_system::instance();
$fs = get_file_storage();
$hfm = 'https://moodle.hfm-weimar.de';
foreach ([  // HfM's own, from its Moodle
    'logo' => ['Logo_lang.png', "$hfm/pluginfile.php/1/theme_boost_union/logo/0x200/1789445724/Logo_lang.png"],
    'logocompact' => ['Logo HfM weiss.png', "$hfm/pluginfile.php/1/theme_boost_union/logocompact/300x300/1789445724/Logo%20HfM%20wei%C3%9F%20trans%20o%20Rand.png"],
    'favicon' => ['favicon.ico', "$hfm/theme/image.php/boost_union/theme/1789445724/favicon"],
] as $area => [$filename, $url]) {
    $fs->delete_area_files($system->id, 'theme_boost_union', $area);
    $fs->create_file_from_string(['contextid' => $system->id, 'component' => 'theme_boost_union', 'filearea' => $area, 'itemid' => 0,
        'filepath' => '/', 'filename' => $filename], download_file_content($url));
    set_config($area, "/$filename", 'theme_boost_union');
}

// HfM's categories beside the semesters ConCampus makes, and the information courses its dashboard links to, open to guests.
function category($idnumber, $name, $parent = 0) {
    global $DB;
    return $DB->get_field('course_categories', 'id', ['idnumber' => $idnumber])
        ?: core_course_category::create(['name' => $name, 'idnumber' => $idnumber, 'parent' => $parent])->id;
}
function course($idnumber, $name, $category) {
    global $DB;
    if (!$course = $DB->get_record('course', ['idnumber' => $idnumber])) {
        $course = create_course((object) ['fullname' => $name, 'shortname' => $name, 'idnumber' => $idnumber, 'category' => $category]);
        enrol_get_plugin('guest')->add_instance($course, ['status' => ENROL_INSTANCE_ENABLED]);
    }
    return $course->id;
}
$general = category('demo-allgemein', 'Allgemeine Informationen / Fortbildungen');
$first = category('demo-moodle', 'Moodle - Infos & Erste Schritte', $general);
category('demo-ki', 'Künstliche Intelligenz', $general);
category('demo-it', 'IT-Sicherheit', $general);
$institutes = category('demo-institute', 'Informationen (Studienfächer / Institute)');
foreach (['Institut für Gesang / Musiktheater', 'Institut für Gitarre', 'Institut für Musikwissenschaft', 'Tonstudio',
    'IMPK (Lehramt / EMP / Kirchenmusik)'] as $i => $name) {
    category("demo-institut-$i", $name, $institutes);
}
$links = [
    'Veranstaltungen und Links zum Thema Online-Lehre und -Lernen' => course('demo-online', 'Veranstaltungen und Links zum Thema Online-Lehre und -Lernen', $first),
    'Lernvideos zur Nutzung von Moodle' => course('demo-videos', 'Lernvideos zur Nutzung von Moodle', $first),
    'FAQ für Studierende' => course('demo-faq', 'FAQ für Studierende', $first),
];

// The dashboard everyone gets: the courses, two text blocks with links, the recent courses, timeline and calendar; in the
// drawer private files, badges and upcoming dates.
$default = $DB->get_record('my_pages', ['userid' => null, 'name' => '__default', 'private' => MY_PAGE_PRIVATE]);
foreach ($DB->get_records('block_instances', ['parentcontextid' => $system->id, 'pagetypepattern' => 'my-index', 'subpagepattern' => $default->id]) as $old) {
    blocks_delete_instance($old);
}
$html = fn($text) => base64_encode(serialize((object) ['title' => '', 'text' => $text, 'format' => FORMAT_HTML]));
$list = implode('', array_map(fn($name, $id) => "<p><a href=\"$CFG->wwwroot/course/view.php?id=$id\">$name</a></p>", array_keys($links), $links));
foreach ([
    ['myoverview', 'content', null], ['html', 'content', $html("<p><a href=\"$CFG->wwwroot/course/index.php\">Zu allen Kursen</a></p>")],
    ['html', 'content', $html($list)], ['recentlyaccessedcourses', 'content', null], ['timeline', 'content', null],
    ['calendar_month', 'content', null], ['private_files', 'side-pre', null], ['badges', 'side-pre', null], ['calendar_upcoming', 'side-pre', null],
] as $weight => [$block, $region, $config]) {
    $id = $DB->insert_record('block_instances', (object) ['blockname' => $block, 'parentcontextid' => $system->id, 'showinsubcontexts' => 0,
        'pagetypepattern' => 'my-index', 'subpagepattern' => $default->id, 'defaultregion' => $region, 'defaultweight' => $weight,
        'configdata' => $config ?? '', 'timecreated' => time(), 'timemodified' => time()]);
    context_block::instance($id);
}
my_reset_page_for_all_users(MY_PAGE_PRIVATE, 'my-index');

$functions = ['core_course_get_categories', 'core_course_create_categories', 'core_course_get_courses_by_field',
    'core_course_create_courses', 'core_course_update_courses', 'core_user_get_users_by_field', 'core_user_create_users',
    'enrol_manual_enrol_users', 'enrol_manual_unenrol_users', 'core_group_get_course_groups', 'core_group_create_groups',
    'core_group_get_group_members', 'core_group_add_group_members', 'core_group_delete_group_members'];
$manager = new webservice();
if (!$service = $DB->get_record('external_services', ['shortname' => 'campus'])) {
    $service = $DB->get_record('external_services', ['id' => $manager->add_external_service((object) [
        'name' => 'ConCampus', 'shortname' => 'campus', 'enabled' => 1, 'restrictedusers' => 0, 'downloadfiles' => 0, 'uploadfiles' => 0,
    ])]);
}
foreach ($functions as $function) {
    if (!$manager->service_function_exists($function, $service->id)) {
        $manager->add_external_function_to_service($function, $service->id);
    }
}

if (!$campus = $DB->get_record('user', ['username' => 'campus-sync'])) {
    $campus = $DB->get_record('user', ['id' => user_create_user((object) [
        'username' => 'campus-sync', 'firstname' => 'ConCampus', 'lastname' => 'Abgleich', 'email' => 'campus-sync@localhost',
        'auth' => 'manual', 'password' => random_string(32), 'confirmed' => 1, 'mnethostid' => $CFG->mnet_localhost_id,
    ])]);
}
if (!in_array($campus->id, explode(',', $CFG->siteadmins))) {  // a demo: in production a role with only what it calls
    set_config('siteadmins', $CFG->siteadmins . ',' . $campus->id);
}
$token = $DB->get_field('external_tokens', 'token', ['userid' => $campus->id, 'externalserviceid' => $service->id])
    ?: \core_external\util::generate_token(EXTERNAL_TOKEN_PERMANENT, $service, $campus->id, $system);
theme_reset_all_caches();
purge_all_caches();
echo "MOODLE_TOKEN=$token\n";
