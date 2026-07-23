<?php
/**
 * Plugin Name: Engage AI
 * Description: Generates and auto-publishes church engagement content (events, weekly announcements, sermon engagement), autonomous check-in agents for the 8 Claude AI side-hustle modules, and web-search-based analytics, via the Engage AI Cloud API.
 * Version: 0.17.0
 * Author: Vision Outreach Media
 * Text Domain: engage-ai
 */

if (!defined('ABSPATH')) {
    exit;
}

define('ENGAGEAI_VERSION', '0.17.0');
define('ENGAGEAI_PLUGIN_DIR', plugin_dir_path(__FILE__));
define('ENGAGEAI_PLUGIN_URL', plugin_dir_url(__FILE__));

require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-api-client.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-post-publisher.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-settings.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-generate.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-agents.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-analytics.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-cycle.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-dashboard.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-assistant.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-content.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-cron.php';

/**
 * Native WordPress "Update Now" support via Plugin Update Checker, pointed
 * at our own API instead of GitHub - no releases/tags to manage, the API
 * serves /plugin/metadata.json straight from its bundled copy of this
 * plugin's source (see engage-ai-cloud-api/app/services/plugin_metadata.py).
 *
 * Guarded on file_exists() because the PUC library isn't committed to this
 * repo (third-party code - see https://github.com/YahnisElsts/plugin-update-checker,
 * drop the release zip's contents into includes/plugin-update-checker/).
 * Until it's present, this is a silent no-op and the rest of the plugin
 * works normally - update checking turns on automatically the moment the
 * library is added, no further code change needed.
 */
$engageai_puc_file = ENGAGEAI_PLUGIN_DIR . 'includes/plugin-update-checker/plugin-update-checker.php';
if (file_exists($engageai_puc_file)) {
    require_once $engageai_puc_file;
    if (class_exists('YahnisElsts\\PluginUpdateChecker\\v5\\PucFactory')) {
        $engageai_api_base = rtrim((string) get_option('engageai_api_base_url', 'https://engage-ai-api.onrender.com'), '/');
        \YahnisElsts\PluginUpdateChecker\v5\PucFactory::buildUpdateChecker(
            $engageai_api_base . '/plugin/metadata.json',
            __FILE__,
            'engage-ai'
        );
    }
}
unset($engageai_puc_file);

final class EngageAI_Plugin
{
    private static ?EngageAI_Plugin $instance = null;

    public static function instance(): EngageAI_Plugin
    {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct()
    {
        add_action('admin_menu', [$this, 'register_admin_menu']);
        add_action('admin_enqueue_scripts', [$this, 'enqueue_admin_assets']);
        // First-run check-in: report this site's home URL to the API once, so
        // the console can link to the live site and any duplicate org record
        // gets merged. Cheap no-op after the first success (option guard).
        add_action('admin_init', [$this, 'maybe_hello_site']);
        add_action(EngageAI_Cron::HOOK, [EngageAI_Cron::class, 'run']);
        // Activation hooks don't fire on a self-update (Plugin Update
        // Checker replaces the files without deactivating/reactivating), so
        // an existing install would otherwise never get the cron scheduled
        // once it updates to the version that introduced it. schedule() is
        // idempotent (wp_next_scheduled guard), so hooking it here too is cheap.
        add_action('init', [EngageAI_Cron::class, 'schedule']);

        EngageAI_Admin_Settings::instance()->register_hooks();
        EngageAI_Admin_Generate::instance()->register_hooks();
        EngageAI_Admin_Agents::instance()->register_hooks();
        EngageAI_Admin_Analytics::instance()->register_hooks();
        EngageAI_Admin_Cycle::instance()->register_hooks();
        EngageAI_Admin_Dashboard::instance()->register_hooks();
        EngageAI_Admin_Assistant::instance()->register_hooks();
        EngageAI_Admin_Content::instance()->register_hooks();
    }

    public function register_admin_menu(): void
    {
        add_menu_page(
            __('Engage AI', 'engage-ai'),
            __('Engage AI', 'engage-ai'),
            'manage_options',
            'engageai-dashboard',
            [EngageAI_Admin_Dashboard::instance(), 'render_page'],
            'dashicons-megaphone',
            58
        );

        add_submenu_page(
            'engageai-dashboard',
            __('Dashboard', 'engage-ai'),
            __('Dashboard', 'engage-ai'),
            'manage_options',
            'engageai-dashboard',
            [EngageAI_Admin_Dashboard::instance(), 'render_page']
        );

        add_submenu_page(
            'engageai-dashboard',
            __('Generate Content', 'engage-ai'),
            __('Generate Content', 'engage-ai'),
            'manage_options',
            'engageai-generate',
            [EngageAI_Admin_Generate::instance(), 'render_page']
        );

        add_submenu_page(
            'engageai-dashboard',
            __('Content', 'engage-ai'),
            __('Content', 'engage-ai'),
            'manage_options',
            'engageai-content',
            [EngageAI_Admin_Content::instance(), 'render_page']
        );

        add_submenu_page(
            'engageai-dashboard',
            __('Agents', 'engage-ai'),
            __('Agents', 'engage-ai'),
            'manage_options',
            'engageai-agents',
            [EngageAI_Admin_Agents::instance(), 'render_page']
        );

        add_submenu_page(
            'engageai-dashboard',
            __('Analytics', 'engage-ai'),
            __('Analytics', 'engage-ai'),
            'manage_options',
            'engageai-analytics',
            [EngageAI_Admin_Analytics::instance(), 'render_page']
        );

        add_submenu_page(
            'engageai-dashboard',
            __('Engagement Cycle', 'engage-ai'),
            __('Engagement Cycle', 'engage-ai'),
            'manage_options',
            'engageai-cycle',
            [EngageAI_Admin_Cycle::instance(), 'render_page']
        );

        add_submenu_page(
            'engageai-dashboard',
            __('AI Assistant', 'engage-ai'),
            __('AI Assistant', 'engage-ai'),
            'manage_options',
            'engageai-assistant',
            [EngageAI_Admin_Assistant::instance(), 'render_page']
        );

        add_submenu_page(
            'engageai-dashboard',
            __('Engage AI Settings', 'engage-ai'),
            __('Settings', 'engage-ai'),
            'manage_options',
            'engageai-settings',
            [EngageAI_Admin_Settings::instance(), 'render_page']
        );
    }

    public function enqueue_admin_assets(string $hook): void
    {
        if (strpos($hook, 'engageai-') === false) {
            return;
        }

        wp_enqueue_style(
            'engageai-admin',
            ENGAGEAI_PLUGIN_URL . 'assets/admin.css',
            [],
            ENGAGEAI_VERSION
        );
    }

    /**
     * Reports this site's home_url to the API exactly once, the first time an
     * admin loads any wp-admin page while the plugin is connected. Lets the
     * console show a live link to the site and lets the API fold this org into
     * an existing record for the same site if the operator had already created
     * one (see POST /organizations/{id}/site-hello). Guarded by an option so
     * it's a no-op on every later page load; runs on update too, since an
     * already-installed site won't have the flag set yet.
     */
    public function maybe_hello_site(): void
    {
        if (get_option('engageai_site_synced')) {
            return;
        }

        $client = new EngageAI_Api_Client();
        $org_id = $client->get_organization_id();
        if (!$client->is_connected() || !$org_id) {
            return; // not connected yet - try again on a later page load
        }

        $result = $client->hello_site((int) $org_id, home_url('/'), admin_url());
        if (is_wp_error($result)) {
            return; // leave the flag unset so it retries next time
        }

        // The API may have merged this org into a pre-existing one for the same
        // site; if so, repoint this install at the surviving org id.
        if (!empty($result['organization_id']) && (int) $result['organization_id'] !== (int) $org_id) {
            $org_id = (int) $result['organization_id'];
            $client->set_organization_id($org_id);
        }
        update_option('engageai_site_synced', 1, false);

        // Send the site's ground-truth content counts right away, so the very
        // next scan scores the website channel from real data.
        self::report_site_facts($client, $org_id);
    }

    /**
     * Tells the API this site is live and how much content it has actually
     * published (real WordPress post/page counts), so the analytics scan scores
     * the website channel from ground truth instead of a web-search guess that
     * a small or new site fails. Called on first run and on each cron tick.
     */
    public static function report_site_facts(EngageAI_Api_Client $client, int $org_id): void
    {
        if ($org_id <= 0) {
            return;
        }
        $posts = (int) (wp_count_posts('post')->publish ?? 0);
        $pages = (int) (wp_count_posts('page')->publish ?? 0);
        $client->report_site($org_id, $posts, $pages, self::detect_site_type());
    }

    /**
     * Best-guess of what kind of WordPress site this is, so content
     * suggestions can be tailored to it: an active WooCommerce install is an
     * "ecommerce" site; a church-type org is "church"; everything else defaults
     * to "business". Deliberately simple and safe - a wrong guess just yields
     * generically-useful posts, and the operator can refine later.
     */
    /** Website types the operator can choose from (Settings) or the plugin can detect. */
    public const SITE_TYPES = ['church', 'business', 'ecommerce'];

    public static function detect_site_type(): string
    {
        // An operator-set type (Settings > Organization details) always wins.
        $override = (string) get_option('engageai_site_type', '');
        if (in_array($override, self::SITE_TYPES, true)) {
            return $override;
        }
        if (class_exists('WooCommerce') || function_exists('WC')) {
            return 'ecommerce';
        }
        $client = new EngageAI_Api_Client();
        $orgs = $client->get_organizations();
        $org_id = $client->get_organization_id();
        if (!is_wp_error($orgs) && $org_id) {
            foreach ($orgs as $o) {
                if ((int) ($o['id'] ?? 0) === (int) $org_id && ($o['org_type'] ?? '') === 'church') {
                    return 'church';
                }
            }
        }
        return 'business';
    }

    /**
     * Runs once, on activation. If this zip was downloaded from the
     * onboarding page (POST /onboarding on the API), includes/preconfigured.php
     * exists and already has this org's base URL/token/org ID baked in -
     * connect automatically instead of making the admin fill in Settings.
     * A plain "Download ZIP from GitHub" install has no such file, so this
     * is a no-op there.
     */
    public static function activate(): void
    {
        EngageAI_Cron::schedule();

        $config_file = ENGAGEAI_PLUGIN_DIR . 'includes/preconfigured.php';
        if (!file_exists($config_file)) {
            return;
        }

        $client = new EngageAI_Api_Client();
        if ($client->is_connected() && $client->get_organization_id()) {
            return; // already configured (e.g. re-activation) - don't clobber it
        }

        $config = include $config_file;
        if (!is_array($config) || empty($config['token']) || empty($config['api_base_url'])) {
            return;
        }

        $client->set_base_url($config['api_base_url']);
        $client->store_token($config['token']);
        if (!empty($config['organization_id'])) {
            $client->set_organization_id((int) $config['organization_id']);
        }
    }
}

register_activation_hook(__FILE__, ['EngageAI_Plugin', 'activate']);
register_deactivation_hook(__FILE__, ['EngageAI_Cron', 'unschedule']);

EngageAI_Plugin::instance();
