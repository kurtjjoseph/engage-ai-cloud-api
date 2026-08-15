<?php
/**
 * Site Brain module.
 *
 * Aggregates this WordPress site into a continuously updated index and serves
 * it to AI agents over the Model Context Protocol, plus plain REST, llms.txt
 * and a .well-known discovery document. Where the rest of the plugin pushes
 * content *out* to channels, this makes the site itself readable *in* - so a
 * chatbot can answer from it and an indexing agent can keep a copy in sync.
 *
 * Deliberately NOT gated on EngageAI_Admin_Settings::cached_modules(). That
 * gate fails open (an unreachable API shows every page), which is right for
 * menu items and wrong here: switching this on publishes public endpoints that
 * expose site content. It is a local, explicit, default-off opt-in instead, so
 * no site can ever start serving its content because an API call timed out.
 *
 * The source under site-brain/ is a verbatim copy of the standalone
 * VOM Site Brain plugin's includes/ and assets/ - same files, same paths - so
 * re-syncing it is a plain directory copy with no porting step. The only
 * plumbing here is the constants it reads to know it is running as a module.
 */

if (!defined('ABSPATH')) {
    exit;
}

final class EngageAI_Site_Brain
{
    /** Local, explicit opt-in. Default off. */
    public const OPTION = 'engageai_site_brain_enabled';

    /** Schema stamp, so a plugin update can migrate the tables. */
    private const DB_VERSION_OPTION = 'engageai_site_brain_db_version';

    /** Set once the one-time setup in provision() has run. */
    private const PROVISIONED_OPTION = 'engageai_site_brain_provisioned';

    /** Where the vendored source lives, relative to the plugin root. */
    private const SUBDIR = 'site-brain/';

    /** Our page inside the Engage AI menu. */
    private const PAGE_SLUG = 'engageai-site-brain';

    /**
     * Version of the vendored Site Brain source. Tracks the standalone
     * plugin's own header, not ENGAGEAI_VERSION - it is reported to agents as
     * the MCP server version, so it has to describe the brain, not its host.
     */
    private const BRAIN_VERSION = '1.0.0';

    private static bool $loaded = false;

    /** @var string[] Load order matters: later classes call into earlier ones. */
    private const CLASS_FILES = [
        'settings',
        'auth',
        'log',
        'index',
        'aggregator',
        'mcp-server',
        'rest',
        'discovery',
        'admin',
    ];

    /**
     * On by default, including on sites that update into this version without
     * ever visiting Settings. get_option()'s default only applies when nothing
     * is stored, so a site that has explicitly switched the brain OFF keeps a
     * stored 0 and stays off - the default never overrides a decision someone
     * actually made.
     *
     * Note what this means on update: a site that has never touched the setting
     * starts serving /llms.txt, /llms-full.txt, /.well-known/mcp.json and the
     * REST endpoints on its own domain. Only published content in the selected
     * post types is ever served (never users, comments, orders or form
     * submissions), and it can be switched off in Settings.
     */
    public static function is_enabled(): bool
    {
        return (bool) get_option(self::OPTION, true);
    }

    /**
     * Registers the settings handler always, and starts the module itself on
     * plugins_loaded - late enough that every other plugin has been included,
     * so the standalone-plugin check below can actually see one.
     */
    public static function boot(): void
    {
        add_action('admin_post_engageai_site_brain_toggle', [self::class, 'handle_toggle']);
        add_action('plugins_loaded', [self::class, 'maybe_start'], 5);
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
     * True when the standalone VOM Site Brain plugin is running. Its files
     * declare the same classes, so exactly one of the two may load; the
     * standalone one is included earlier by WordPress, so the module is the
     * one that stands down.
     */
    private static function standalone_active(): bool
    {
        if (!self::$loaded && class_exists('VOM_Brain_Index', false)) {
            return true;
        }

        $active = (array) get_option('active_plugins', []);
        foreach ($active as $plugin) {
            if (strpos((string) $plugin, 'vom-site-brain/') === 0) {
                return true;
            }
        }

        $network = (array) get_site_option('active_sitewide_plugins', []);
        foreach (array_keys($network) as $plugin) {
            if (strpos((string) $plugin, 'vom-site-brain/') === 0) {
                return true;
            }
        }

        return false;
    }

    /**
     * Defines the constants the vendored source reads, then includes it.
     *
     * VOM_BRAIN_NS is deliberately left at the standalone plugin's value: the
     * two builds are the same code and should answer on the same path, so one
     * set of documentation stays true for both.
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

        if (!defined('VOM_BRAIN_VERSION')) {
            define('VOM_BRAIN_VERSION', self::BRAIN_VERSION);
            define('VOM_BRAIN_DIR', $dir);
            define('VOM_BRAIN_URL', ENGAGEAI_PLUGIN_URL . self::SUBDIR);
            define('VOM_BRAIN_NS', 'vom-mcp/v1');
            define('VOM_BRAIN_MENU_PARENT', 'engageai-dashboard');
            define('VOM_BRAIN_PAGE_SLUG', self::PAGE_SLUG);
        }

        foreach (self::CLASS_FILES as $file) {
            require_once $dir . 'includes/class-vom-brain-' . $file . '.php';
        }

        self::$loaded = true;
        return true;
    }

    /** Registers the indexer, the transports and the admin screen. */
    private static function wire(): void
    {
        VOM_Brain_Index::hooks();
        VOM_Brain_MCP_Server::hooks();
        VOM_Brain_REST::hooks();
        VOM_Brain_Discovery::hooks();

        if (is_admin()) {
            VOM_Brain_Admin::hooks();
        }

        // Plugin updates replace files without firing an activation hook, so
        // the schema is brought current here instead.
        if (get_option(self::DB_VERSION_OPTION) !== self::BRAIN_VERSION) {
            VOM_Brain_Index::install();
            update_option(self::DB_VERSION_OPTION, self::BRAIN_VERSION, false);
        }

        // Now that the brain is on by default, most sites arrive here having
        // never pressed the Settings toggle - so the setup that toggle used to
        // do has to happen on this path too, or the endpoints would answer from
        // an unseeded, never-crawled index.
        self::provision();
    }

    /**
     * The one-time setup a live brain needs: default settings, the daily
     * refresh, and a first crawl. Idempotent and cheap to re-enter - the flag
     * makes it a single option read on every later request, and each step is
     * separately guarded anyway.
     *
     * Deliberately does not call run_batch() here the way the Settings toggle
     * does: this runs on a normal front-end request, and crawling the site
     * inline would put the whole first build on whichever visitor happens to
     * arrive first. queue_full_build() leaves it to the scheduled batch.
     */
    private static function provision(): void
    {
        if (get_option(self::PROVISIONED_OPTION)) {
            return;
        }

        VOM_Brain_Settings::seed();

        // seed() fills the contact email from the WordPress admin_email. That
        // was reasonable when the brain only ever started because an operator
        // pressed the button - they were looking at the settings screen and
        // could see it. Starting by default, it would publish an administrator's
        // personal address at /llms.txt, on every site that updates, with no
        // authentication and nobody having asked for it. The site name and
        // tagline are already public on the site itself; the email is not, so
        // it stays blank until someone types it in deliberately.
        if (VOM_Brain_Settings::get('biz_email') === get_option('admin_email')) {
            VOM_Brain_Settings::update(['biz_email' => '']);
        }

        if (!wp_next_scheduled('vom_brain_daily')) {
            wp_schedule_event(time() + 300, 'daily', 'vom_brain_daily');
        }

        VOM_Brain_Index::queue_full_build();

        update_option(self::PROVISIONED_OPTION, self::BRAIN_VERSION, false);
    }

    /**
     * Settings toggle. Turning it on builds the schema and starts the first
     * crawl immediately, so the operator sees a document count rather than an
     * empty page and no explanation.
     */
    public static function handle_toggle(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_site_brain_toggle')) {
            wp_die(esc_html__('Security check failed.', 'engage-ai'));
        }

        $enable = !empty($_POST['engageai_site_brain_enabled']);

        if ($enable && self::standalone_active()) {
            self::redirect_with_notice(
                'error',
                __('Deactivate the standalone VOM Site Brain plugin first - only one brain can run per site.', 'engage-ai')
            );
        }

        update_option(self::OPTION, $enable ? 1 : 0, false);

        if (!$enable) {
            self::stop();
            self::redirect_with_notice(
                'success',
                __('Site Brain is off. The endpoints no longer answer; the index is kept, so turning it back on is instant.', 'engage-ai')
            );
        }

        if (!self::load()) {
            self::redirect_with_notice('error', __('The Site Brain files are missing from this plugin install.', 'engage-ai'));
        }

        VOM_Brain_Index::install();
        update_option(self::DB_VERSION_OPTION, self::BRAIN_VERSION, false);
        VOM_Brain_Settings::seed();

        if (!wp_next_scheduled('vom_brain_daily')) {
            wp_schedule_event(time() + 300, 'daily', 'vom_brain_daily');
        }

        VOM_Brain_Index::queue_full_build();
        VOM_Brain_Index::run_batch();

        // Everything provision() would do has just been done, and done more
        // eagerly - stamp it so the front-end path doesn't repeat the work.
        update_option(self::PROVISIONED_OPTION, self::BRAIN_VERSION, false);

        self::redirect_with_notice(
            'success',
            __('Site Brain is on and indexing this site. It continues in the background.', 'engage-ai')
        );
    }

    /** Stops all background work. Used on disable and on plugin deactivation. */
    public static function stop(): void
    {
        wp_clear_scheduled_hook('vom_brain_daily');
        wp_clear_scheduled_hook('vom_brain_build_batch');
    }

    public static function render_conflict_notice(): void
    {
        if (!current_user_can('activate_plugins')) {
            return;
        }
        echo '<div class="notice notice-warning"><p>';
        esc_html_e('Engage AI\'s Site Brain module is standing down: the standalone VOM Site Brain plugin is active and already serving this site. Deactivate it to use the module instead.', 'engage-ai');
        echo '</p></div>';
    }

    /**
     * The Settings page section. Rendered outside the "connected" branches -
     * the brain is served by this site itself and needs no API account, so it
     * has to be reachable on a site that has not connected one.
     */
    public static function render_settings_section(): void
    {
        $enabled = self::is_enabled();
        $running = $enabled && self::$loaded;
        ?>
        <hr>
        <h2><?php esc_html_e('8. Site Brain (AI agent access)', 'engage-ai'); ?></h2>
        <p class="description">
            <?php esc_html_e('Publishes this website as a live knowledge base that AI agents can read: a Model Context Protocol server, an llms.txt briefing and a discovery document. Point a chatbot at it and it answers from your actual pages instead of guessing. On by default — only published content in the post types you pick is ever served, and you can switch it off here.', 'engage-ai'); ?>
        </p>

        <?php if ($running) : ?>
            <?php $stats = VOM_Brain_Index::stats(); ?>
            <table class="form-table">
                <tr>
                    <th scope="row"><?php esc_html_e('Indexed', 'engage-ai'); ?></th>
                    <td>
                        <?php
                        printf(
                            /* translators: 1: document count, 2: passage count */
                            esc_html__('%1$s documents, %2$s retrievable passages.', 'engage-ai'),
                            esc_html(number_format_i18n((int) $stats['documents'])),
                            esc_html(number_format_i18n((int) $stats['passages']))
                        );
                        ?>
                    </td>
                </tr>
                <tr>
                    <th scope="row"><?php esc_html_e('MCP endpoint', 'engage-ai'); ?></th>
                    <td><code><?php echo esc_html(rest_url(VOM_BRAIN_NS . '/mcp')); ?></code></td>
                </tr>
                <tr>
                    <th scope="row"><?php esc_html_e('Manage', 'engage-ai'); ?></th>
                    <td>
                        <a href="<?php echo esc_url(admin_url('admin.php?page=' . self::PAGE_SLUG)); ?>">
                            <?php esc_html_e('Engage AI > Site Brain', 'engage-ai'); ?>
                        </a>
                        <span class="description">
                            <?php esc_html_e('- choose what to share, enter your business facts, issue tokens, see which agents are calling.', 'engage-ai'); ?>
                        </span>
                    </td>
                </tr>
            </table>
        <?php endif; ?>

        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
            <input type="hidden" name="action" value="engageai_site_brain_toggle">
            <?php wp_nonce_field('engageai_site_brain_toggle'); ?>
            <table class="form-table">
                <tr>
                    <th scope="row"><?php esc_html_e('Site Brain', 'engage-ai'); ?></th>
                    <td>
                        <label>
                            <input type="checkbox" name="engageai_site_brain_enabled" value="1" <?php checked($enabled); ?>>
                            <?php esc_html_e('Active', 'engage-ai'); ?>
                        </label>
                        <p class="description">
                            <?php esc_html_e('Only published content in the types you pick is ever served - no users, comments, orders or form submissions. You choose those, and whether anonymous agents may read at all, on the Site Brain page.', 'engage-ai'); ?>
                        </p>
                    </td>
                </tr>
            </table>
            <?php submit_button($enabled ? __('Save Site Brain', 'engage-ai') : __('Turn on Site Brain', 'engage-ai')); ?>
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
