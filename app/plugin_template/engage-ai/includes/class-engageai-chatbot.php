<?php
/**
 * Chatbot module.
 *
 * The visitor-facing half of Site Brain. The brain makes a site readable; this
 * puts a chat bubble on every page that answers from it, in the visitor's own
 * language, citing the pages it used - and captures a lead when someone is
 * ready to talk to a person.
 *
 * Off by default, unlike Site Brain. Turning this on puts a visible widget on
 * every public page of a customer's website and answers strangers in their
 * name; that is a decision an operator should make deliberately, not something
 * that should appear because they installed an update.
 *
 * Replies route through the Engage AI Cloud API, so a customer never needs an
 * AI account of their own. Retrieval runs here, where the index lives; the API
 * owns the answering protocol, so the rules a public assistant works under
 * cannot be rewritten from a WordPress option.
 *
 * The source under chatbot/ is a verbatim copy of the standalone Vision Outreach
 * Chatbot plugin's includes/, assets/ and templates/ - same files, same paths -
 * so re-syncing it is a plain directory copy with no porting step. The only
 * plumbing here is the constants it reads to know it is running as a module.
 */

if (!defined('ABSPATH')) {
    exit;
}

final class EngageAI_Chatbot
{
    /** Local, explicit opt-in. Default off - see the class comment. */
    public const OPTION = 'engageai_chatbot_enabled';

    /** Schema stamp, so a plugin update can migrate the leads table. */
    private const DB_VERSION_OPTION = 'engageai_chatbot_db_version';

    /** Where the vendored source lives, relative to the plugin root. */
    private const SUBDIR = 'chatbot/';

    /**
     * Version of the vendored chatbot source. Tracks the standalone plugin's
     * own header, not ENGAGEAI_VERSION - it is the cache-buster on the widget
     * assets, so it has to change when those files change.
     */
    private const CHATBOT_VERSION = '2.4.0';

    private static bool $loaded = false;

    /** @var string[] Load order matters: later classes call into earlier ones. */
    private const CLASS_FILES = [
        'installer',
        'settings',
        'knowledge',
        'brain-bridge',
        'openai',
        'manus-ai',
        'cloud-ai',
        'mailer',
        'rest',
        'frontend',
        'admin',
    ];

    public static function is_enabled(): bool
    {
        return (bool) get_option(self::OPTION, false);
    }

    public static function boot(): void
    {
        add_action('admin_post_engageai_chatbot_toggle', [self::class, 'handle_toggle']);
        add_action('plugins_loaded', [self::class, 'maybe_start'], 6);
    }

    public static function maybe_start(): void
    {
        if (!self::is_enabled()) {
            return;
        }
        if (self::standalone_active()) {
            add_action('admin_notices', [self::class, 'render_conflict_notice']);
            return;
        }
        if (!self::load()) {
            return;
        }
        self::wire();
    }

    /**
     * True when the standalone Vision Outreach Chatbot plugin is running. Its
     * files declare the same classes, so exactly one of the two may load; the
     * standalone one is included earlier by WordPress, so the module stands down.
     */
    private static function standalone_active(): bool
    {
        if (!self::$loaded && class_exists('VOC_REST', false)) {
            return true;
        }

        $active = (array) get_option('active_plugins', []);
        foreach ($active as $plugin) {
            if (strpos((string) $plugin, 'vom-chatbot/') === 0) {
                return true;
            }
        }

        $network = (array) get_site_option('active_sitewide_plugins', []);
        foreach (array_keys($network) as $plugin) {
            if (strpos((string) $plugin, 'vom-chatbot/') === 0) {
                return true;
            }
        }

        return false;
    }

    /**
     * Defines the constants the vendored source reads, then includes it.
     *
     * VOC_REST_NAMESPACE and VOC_OPTION_KEY keep the standalone plugin's values
     * on purpose: a site moving from the plugin to the module keeps its saved
     * settings and its captured leads, and the widget keeps calling the same
     * endpoint. VOC_PLUGIN_FILE is deliberately not defined - there is no
     * plugin entry to hang action links on.
     */
    private static function load(): bool
    {
        if (self::$loaded) {
            return true;
        }

        $dir = ENGAGEAI_PLUGIN_DIR . self::SUBDIR;
        if (!is_dir($dir . 'includes')) {
            return false;
        }

        if (!defined('VOC_VERSION')) {
            define('VOC_VERSION', self::CHATBOT_VERSION);
            define('VOC_PLUGIN_DIR', $dir);
            define('VOC_PLUGIN_URL', ENGAGEAI_PLUGIN_URL . self::SUBDIR);
            define('VOC_OPTION_KEY', 'voc_settings');
            define('VOC_LEADS_TABLE', 'voc_leads');
            define('VOC_REST_NAMESPACE', 'vision-outreach-chatbot/v1');
            define('VOC_MENU_PARENT', 'engageai-dashboard');
        }

        foreach (self::CLASS_FILES as $file) {
            require_once $dir . 'includes/class-voc-' . $file . '.php';
        }

        self::$loaded = true;
        return true;
    }

    /** Registers the widget, the endpoints and the admin screens. */
    private static function wire(): void
    {
        VOC_Settings::init();
        VOC_REST::init();
        VOC_Frontend::init();

        if (is_admin()) {
            VOC_Admin::init();
        }

        // Plugin updates replace files without firing an activation hook, so
        // the leads table is brought current here instead.
        if (get_option(self::DB_VERSION_OPTION) !== self::CHATBOT_VERSION) {
            VOC_Installer::create_leads_table();
            update_option(self::DB_VERSION_OPTION, self::CHATBOT_VERSION, false);
        }
    }

    /**
     * Settings toggle. Turning it on creates the leads table and seeds the
     * defaults immediately, so the settings screen has something to show.
     */
    public static function handle_toggle(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_chatbot_toggle')) {
            wp_die(esc_html__('Security check failed.', 'engage-ai'));
        }

        $enable = !empty($_POST['engageai_chatbot_enabled']);

        if ($enable && self::standalone_active()) {
            self::redirect_with_notice(
                'error',
                __('Deactivate the standalone Vision Outreach Chatbot plugin first - only one chatbot can run per site.', 'engage-ai')
            );
        }

        update_option(self::OPTION, $enable ? 1 : 0, false);

        if (!$enable) {
            self::redirect_with_notice(
                'success',
                __('Chatbot is off. The widget no longer appears; your settings and captured leads are kept.', 'engage-ai')
            );
        }

        if (!self::load()) {
            self::redirect_with_notice('error', __('The Chatbot files are missing from this plugin install.', 'engage-ai'));
        }

        VOC_Installer::create_leads_table();
        VOC_Installer::seed_default_settings();
        update_option(self::DB_VERSION_OPTION, self::CHATBOT_VERSION, false);

        self::redirect_with_notice(
            'success',
            __('Chatbot is on. The chat bubble now appears on every public page.', 'engage-ai')
        );
    }

    public static function render_conflict_notice(): void
    {
        if (!current_user_can('activate_plugins')) {
            return;
        }
        echo '<div class="notice notice-warning"><p>';
        esc_html_e('Engage AI\'s Chatbot module is standing down: the standalone Vision Outreach Chatbot plugin is active and already running on this site. Deactivate it to use the module instead.', 'engage-ai');
        echo '</p></div>';
    }

    /** The Settings page section. */
    public static function render_settings_section(): void
    {
        $enabled = self::is_enabled();
        $running = $enabled && self::$loaded;
        $brain_on = class_exists('EngageAI_Site_Brain') && EngageAI_Site_Brain::is_enabled();
        ?>
        <hr>
        <h2><?php esc_html_e('9. Chatbot (answers visitors on your website)', 'engage-ai'); ?></h2>
        <p class="description">
            <?php esc_html_e('Puts a chat bubble on every public page. It answers from your Site Brain — your actual pages, your verified business facts, your FAQs — in the visitor\'s own language, and captures a name and email when someone wants to talk to a person. Replies run through your Engage AI account, so there is no separate AI key to set up. Off by default.', 'engage-ai'); ?>
        </p>

        <?php if ($enabled && !$brain_on) : ?>
            <div class="notice notice-warning inline"><p>
                <?php esc_html_e('Site Brain is switched off, so the chatbot has nothing to answer from. It will tell visitors it cannot look things up and offer to pass them on. Turn Site Brain on above.', 'engage-ai'); ?>
            </p></div>
        <?php endif; ?>

        <?php if ($running) : ?>
            <table class="form-table">
                <tr>
                    <th scope="row"><?php esc_html_e('Manage', 'engage-ai'); ?></th>
                    <td>
                        <a href="<?php echo esc_url(admin_url('admin.php?page=voc-settings')); ?>">
                            <?php esc_html_e('Engage AI > Chatbot Settings', 'engage-ai'); ?>
                        </a>
                        <span class="description">
                            <?php esc_html_e('- greeting, colours, call-to-action, where lead emails go.', 'engage-ai'); ?>
                        </span>
                    </td>
                </tr>
                <tr>
                    <th scope="row"><?php esc_html_e('Leads', 'engage-ai'); ?></th>
                    <td>
                        <a href="<?php echo esc_url(admin_url('admin.php?page=voc-leads')); ?>">
                            <?php esc_html_e('Engage AI > Chatbot', 'engage-ai'); ?>
                        </a>
                        <span class="description"><?php esc_html_e('- everyone who left their details, with CSV export.', 'engage-ai'); ?></span>
                    </td>
                </tr>
            </table>
        <?php endif; ?>

        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
            <input type="hidden" name="action" value="engageai_chatbot_toggle">
            <?php wp_nonce_field('engageai_chatbot_toggle'); ?>
            <table class="form-table">
                <tr>
                    <th scope="row"><?php esc_html_e('Chatbot', 'engage-ai'); ?></th>
                    <td>
                        <label>
                            <input type="checkbox" name="engageai_chatbot_enabled" value="1" <?php checked($enabled); ?>>
                            <?php esc_html_e('Active', 'engage-ai'); ?>
                        </label>
                        <p class="description">
                            <?php esc_html_e('The widget appears on every public page while this is on. Conversations are answered from your own website content; visitors are told that AI is involved.', 'engage-ai'); ?>
                        </p>
                    </td>
                </tr>
            </table>
            <?php submit_button($enabled ? __('Save Chatbot', 'engage-ai') : __('Turn on Chatbot', 'engage-ai')); ?>
        </form>
        <?php
    }

    /** Matches EngageAI_Admin_Settings' notice/redirect convention. */
    private static function redirect_with_notice(string $type, string $message): void
    {
        set_transient('engageai_notice_' . get_current_user_id(), ['type' => $type, 'message' => $message], 60);
        wp_safe_redirect(add_query_arg(['page' => 'engageai-settings'], admin_url('admin.php')));
        exit;
    }
}
