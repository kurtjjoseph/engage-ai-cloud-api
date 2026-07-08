<?php
/**
 * Plugin Name: Engage AI
 * Description: Generates and auto-publishes church engagement content (events, weekly announcements, sermon engagement), plus autonomous check-in agents for the 8 Claude AI side-hustle modules, via the Engage AI Cloud API.
 * Version: 0.2.0
 * Author: Vision Outreach Media
 * Text Domain: engage-ai
 */

if (!defined('ABSPATH')) {
    exit;
}

define('ENGAGEAI_VERSION', '0.2.0');
define('ENGAGEAI_PLUGIN_DIR', plugin_dir_path(__FILE__));
define('ENGAGEAI_PLUGIN_URL', plugin_dir_url(__FILE__));

require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-api-client.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-post-publisher.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-settings.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-generate.php';
require_once ENGAGEAI_PLUGIN_DIR . 'includes/class-engageai-admin-agents.php';

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

        EngageAI_Admin_Settings::instance()->register_hooks();
        EngageAI_Admin_Generate::instance()->register_hooks();
        EngageAI_Admin_Agents::instance()->register_hooks();
    }

    public function register_admin_menu(): void
    {
        add_menu_page(
            __('Engage AI', 'engage-ai'),
            __('Engage AI', 'engage-ai'),
            'manage_options',
            'engageai-generate',
            [EngageAI_Admin_Generate::instance(), 'render_page'],
            'dashicons-megaphone',
            58
        );

        add_submenu_page(
            'engageai-generate',
            __('Generate Content', 'engage-ai'),
            __('Generate Content', 'engage-ai'),
            'manage_options',
            'engageai-generate',
            [EngageAI_Admin_Generate::instance(), 'render_page']
        );

        add_submenu_page(
            'engageai-generate',
            __('Agents', 'engage-ai'),
            __('Agents', 'engage-ai'),
            'manage_options',
            'engageai-agents',
            [EngageAI_Admin_Agents::instance(), 'render_page']
        );

        add_submenu_page(
            'engageai-generate',
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
     * Runs once, on activation. If this zip was downloaded from the
     * onboarding page (POST /onboarding on the API), includes/preconfigured.php
     * exists and already has this org's base URL/token/org ID baked in -
     * connect automatically instead of making the admin fill in Settings.
     * A plain "Download ZIP from GitHub" install has no such file, so this
     * is a no-op there.
     */
    public static function activate(): void
    {
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

EngageAI_Plugin::instance();
