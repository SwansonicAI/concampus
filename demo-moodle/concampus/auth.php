<?php
// Signed in only by choosing one of the demo's people (index.php), never by password: Moodle's login page sends on there,
// signing out goes back there to choose another. With ?noredirect=1 Moodle's own form stays, for the admin.
defined('MOODLE_INTERNAL') || die();
require_once($CFG->libdir . '/authlib.php');

class auth_plugin_concampus extends auth_plugin_base {
    public function __construct() {
        $this->authtype = 'concampus';
        $this->config = get_config('auth_concampus');
    }

    public function user_login($username, $password) {
        return false;
    }

    public function is_internal() {
        return false;
    }

    public function can_change_password() {
        return false;
    }

    public function prevent_local_passwords() {
        return true;
    }

    public function loginpage_hook() {
        if ($_SERVER['REQUEST_METHOD'] === 'GET' && !optional_param('noredirect', 0, PARAM_BOOL)) {
            redirect(new moodle_url('/auth/concampus/index.php'));
        }
    }

    public function logoutpage_hook() {
        global $redirect;
        $redirect = (new moodle_url('/auth/concampus/index.php'))->out(false);
    }
}
