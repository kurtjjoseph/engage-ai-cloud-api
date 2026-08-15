<?php
/**
 * Plugin Name: Engage AI
 * Description: Generates and auto-publishes church engagement content (events, weekly announcements, sermon engagement), autonomous check-in agents for the 8 Claude AI side-hustle modules, and web-search-based analytics, via the Engage AI Cloud API.
 * Version: 0.30.0
 * Author: Vision Outreach Media
 * Text Domain: engage-ai
 */

if (!defined('ABSPATH')) {
    exit;
}

define('ENGAGEAI_VERSION', '0.30.0');
define('ENGAGEAI_PLUGIN_DIR', plugin_dir_path(__FILE__));
define('ENGAGEAI_PLUGIN_URL', plugin_dir_url(__FILE__));

require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-api-client.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-post-publisher.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-settings.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-agents.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-analytics.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-cycle.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-dashboard.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-assistant.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-content.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-ideas.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-calendar.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-studio.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-campaigns.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-channels.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-channel-setup.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-cron.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-site-brain.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-chatbot.php';

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
        EngageAI_Admin_Agents::instance()->register_hooks();
        EngageAI_Admin_Analytics::instance()->register_hooks();
        EngageAI_Admin_Cycle::instance()->register_hooks();
        EngageAI_Admin_Dashboard::instance()->register_hooks();
        EngageAI_Admin_Assistant::instance()->register_hooks();
        EngageAI_Admin_Content::instance()->register_hooks();
        EngageAI_Admin_Ideas::instance()->register_hooks();
        EngageAI_Admin_Calendar::instance()->register_hooks();
        EngageAI_Admin_Studio::instance()->register_hooks();
        EngageAI_Admin_Campaigns::instance()->register_hooks();
        EngageAI_Admin_Channels::instance()->register_hooks();
        EngageAI_Admin_Channel_Setup::instance()->register_hooks();

        // Serves this site to AI agents. On by default; stands down entirely if
        // it has been switched off in Settings, or if the standalone VOM Site
        // Brain plugin is active and already serving this site.
        EngageAI_Site_Brain::boot();

        // Answers visitors from what the brain holds. Off by default - it puts
        // a visible widget on every public page - and stands down if the
        // standalone Vision Outreach Chatbot plugin is active. Booted after the
        // brain so the retrieval it depends on is already loaded.
        EngageAI_Chatbot::boot();
    }

    /**
     * True when a module is on for this org - or when we have never managed to
     * read the org's module list, in which case everything shows. Failing open
     * matters: a fresh install, a disconnected site or an API outage must not
     * be able to make pages disappear out from under the operator.
     */
    private function module_active(string $module): bool
    {
        $modules = EngageAI_Admin_Settings::cached_modules();
        return $modules === null || in_array($module, $modules, true);
    }

    /** True when any side-hustle agent niche is on (they share the one page). */
    private function any_agent_active(): bool
    {
        $modules = EngageAI_Admin_Settings::cached_modules();
        if ($modules === null) {
            return true;
        }
        foreach ($modules as $module) {
            if (strpos((string) $module, 'agent:') === 0) {
                return true;
            }
        }
        return false;
    }

    public function register_admin_menu(): void
    {
        // Dashboard, Channels and Settings are always present - they are how you
        // see what is happening, connect somewhere to post, and turn the rest on.
        // Every other page is gated on the module that owns it, so an org that
        // only bought Analytics doesn't wade through eight content pages.
        $engagement = $this->module_active('engagement');

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

        // Ordered as the work actually runs, which is the order the product
        // already names in its own Engagement Cycle: analyse -> plan -> make ->
        // distribute. Before this the menu read Studio, Campaigns, Library,
        // which is backwards - a campaign's pieces open INTO the Studio, so the
        // planning step sat after the step it feeds.
        //
        // Dashboard stays first because it is also the top-level slug: whatever
        // sits here is what clicking "Engage AI" opens, so it has to be the
        // dashboard. Settings stays last by WordPress convention.
        if ($this->module_active('analytics')) {
            add_submenu_page(
                'engageai-dashboard',
                __('Analytics', 'engage-ai'),
                __('Analytics', 'engage-ai'),
                'manage_options',
                'engageai-analytics',
                [EngageAI_Admin_Analytics::instance(), 'render_page']
            );
        }

        if ($engagement) {
            add_submenu_page(
                'engageai-dashboard',
                __('Ideas', 'engage-ai'),
                __('Ideas', 'engage-ai'),
                'manage_options',
                'engageai-ideas',
                [EngageAI_Admin_Ideas::instance(), 'render_page']
            );

            add_submenu_page(
                'engageai-dashboard',
                __('Campaigns', 'engage-ai'),
                __('Campaigns', 'engage-ai'),
                'manage_options',
                'engageai-campaigns',
                [EngageAI_Admin_Campaigns::instance(), 'render_page']
            );

            add_submenu_page(
                'engageai-dashboard',
                __('Content Studio', 'engage-ai'),
                __('Content Studio', 'engage-ai'),
                'manage_options',
                'engageai-studio',
                [EngageAI_Admin_Studio::instance(), 'render_page']
            );

            add_submenu_page(
                'engageai-dashboard',
                __('Content Library', 'engage-ai'),
                __('Content Library', 'engage-ai'),
                'manage_options',
                'engageai-content',
                [EngageAI_Admin_Content::instance(), 'render_page']
            );

            add_submenu_page(
                'engageai-dashboard',
                __('Calendar', 'engage-ai'),
                __('Calendar', 'engage-ai'),
                'manage_options',
                'engageai-calendar',
                [EngageAI_Admin_Calendar::instance(), 'render_page']
            );
        }

        add_submenu_page(
            'engageai-dashboard',
            __('Channels', 'engage-ai'),
            __('Channels', 'engage-ai'),
            'manage_options',
            'engageai-channels',
            [EngageAI_Admin_Channels::instance(), 'render_page']
        );

        // The setup wizard is the same job as Channels - it walks you to a token,
        // then hands you back to Channels to actually connect it. It is a tab on
        // that page now rather than a second menu item.
        //
        // Registered with an EMPTY parent slug, which is how WordPress keeps a
        // page routable while leaving it out of the sidebar: add_submenu_page()
        // still fills in $_registered_pages and records $_parent_pages[slug] =
        // false, which is what admin.php's user_can_access_admin_page() needs to
        // let the request through.
        //
        // Do NOT "register under the real parent, then remove_submenu_page()" -
        // that looks equivalent and is not. remove_submenu_page() takes the entry
        // back out of $submenu, and the capability check reads $submenu, so every
        // link answers "Sorry, you are not allowed to access this page." That
        // shipped in 0.27.0; 0.27.2 fixed it. scripts/smoke_plugin.mjs asserts it.
        add_submenu_page(
            '',
            __('Set up a channel', 'engage-ai'),
            __('Set up a channel', 'engage-ai'),
            'manage_options',
            'engageai-channel-setup',
            [EngageAI_Admin_Channel_Setup::instance(), 'render_page']
        );

        if ($this->module_active('engagement_cycle')) {
            add_submenu_page(
                'engageai-dashboard',
                __('Engagement Cycle', 'engage-ai'),
                __('Engagement Cycle', 'engage-ai'),
                'manage_options',
                'engageai-cycle',
                [EngageAI_Admin_Cycle::instance(), 'render_page']
            );
        }

        if ($this->any_agent_active()) {
            add_submenu_page(
                'engageai-dashboard',
                __('Agents', 'engage-ai'),
                __('Agents', 'engage-ai'),
                'manage_options',
                'engageai-agents',
                [EngageAI_Admin_Agents::instance(), 'render_page']
            );
        }

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

        // The Content Studio has its own design system (see assets/studio.css)
        // and is deliberately not styled like the rest of wp-admin, so it's
        // only loaded on the pages built on it. Campaigns is one of them - it
        // is the same workflow one level up - and adds its own layer on top.
        $is_studio = strpos($hook, 'engageai-studio') !== false;
        $is_campaigns = strpos($hook, 'engageai-campaigns') !== false;
        if ($is_studio || $is_campaigns) {
            wp_enqueue_style(
                'engageai-studio',
                ENGAGEAI_PLUGIN_URL . 'assets/studio.css',
                ['engageai-admin'],
                ENGAGEAI_VERSION
            );
        }
        if ($is_campaigns) {
            wp_enqueue_style(
                'engageai-campaigns',
                ENGAGEAI_PLUGIN_URL . 'assets/campaigns.css',
                ['engageai-studio'],
                ENGAGEAI_VERSION
            );
        }
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
// The Site Brain module keeps its own schedule; deactivating the plugin has to
// stop that too, or a disabled site keeps re-crawling itself in the background.
register_deactivation_hook(__FILE__, ['EngageAI_Site_Brain', 'stop']);

EngageAI_Plugin::instance();
