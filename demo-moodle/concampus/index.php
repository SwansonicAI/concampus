<?php
// The demo's way in (docs/moodle.md), where Moodle's login page and signing out lead: the demo's access code, given once
// per browser, then its people; the one chosen is signed in. ConCampus checks the code, or the ticket it gave for it,
// and says whom to offer, asked with the web service token, which both sides know.
require(__DIR__ . '/../../config.php');
require_once($CFG->libdir . '/filelib.php');
require_once($CFG->dirroot . '/login/lib.php');  // core_login_get_return_url(), once someone is chosen

// A string cache left without this plugin's English (seen 18.11.2026, every text [[access]]) is renewed, not shown.
if (!get_string_manager()->string_exists('access', 'auth_concampus')) {
    get_string_manager()->reset_caches();
}

$url = new moodle_url('/auth/concampus/index.php');
$PAGE->set_url($url);
$PAGE->set_context(context_system::instance());
$PAGE->set_pagelayout('login');

$code = optional_param('code', null, PARAM_RAW_TRIMMED);
$ticket = $_COOKIE['concampus_demo'] ?? '';
$people = null;
$error = '';
if ($code !== null || $ticket !== '') {
    $token = $DB->get_field_sql("SELECT t.token FROM {external_tokens} t JOIN {external_services} s ON s.id = t.externalserviceid
        JOIN {user} u ON u.id = t.userid WHERE s.shortname = 'campus' AND u.username = 'campus-sync'");
    $curl = new curl();
    $curl->setHeader("Authorization: Bearer $token");
    $answer = json_decode($curl->post(getenv('CAMPUS_URL') . '/demo/moodle/',
        $code !== null ? ['code' => $code, 'address' => getremoteaddr()] : ['ticket' => $ticket]), true);
    $status = $curl->get_info()['http_code'] ?? 0;
    if ($status === 200) {
        $people = $answer['people'];
        if ($code !== null) {  // kept as long as ConCampus keeps it
            setcookie('concampus_demo', $answer['ticket'], ['expires' => time() + 30 * DAYSECS, 'path' => '/', 'secure' => is_https(), 'httponly' => true, 'samesite' => 'Lax']);
        }
    } else if ($code !== null || $status !== 403) {  // a ticket gone stale with a new code: the code is asked again
        $error = get_string([403 => 'wrongcode', 429 => 'toomany'][$status] ?? 'unreachable', 'auth_concampus');
    }
}

$username = optional_param('konto', '', PARAM_USERNAME);
if ($people && $username !== '') {
    require_sesskey();
    $offered = array_merge(...array_map(fn($group) => array_column($group['people'], 'username'), $people));
    $user = in_array($username, $offered, true) ? get_complete_user_data('username', $username) : false;
    if (!$user || $user->auth !== 'concampus' || $user->suspended) {
        throw new moodle_exception('invalidlogin');
    }
    if (isloggedin()) {
        require_logout();
    }
    complete_user_login($user);
    redirect(core_login_get_return_url());
}

$PAGE->set_title(get_string($people === null ? 'access' : 'choose', 'auth_concampus'));
$logo = $OUTPUT->get_logo_url();
echo $OUTPUT->header();
echo $OUTPUT->render_from_template('auth_concampus/index', [
    'logourl' => $logo ? $logo->out(false) : '', 'sitename' => format_string($SITE->fullname), 'error' => $error,
    'url' => $url->out(false), 'sesskey' => sesskey(), 'admitted' => $people !== null,
    'groups' => array_map(fn($group, $i) => $group + ['id' => "konto-$i", 'many' => count($group['people']) > 1, 'first' => $group['people'][0]],
        $people ?? [], array_keys($people ?? [])),
]);
echo $OUTPUT->footer();
