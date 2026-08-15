<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * The Channels page: where the site owner authorizes Engage AI to post on each
 * channel they run — Facebook Page, Instagram, LinkedIn, YouTube, X and Google
 * Business Profile.
 *
 * Connecting a channel is one click ("Connect", approve at the provider, come
 * back). Where this Engage AI deployment has no OAuth app registered for a
 * provider yet, the same row offers the fallback: paste a long-lived access
 * token generated at the provider.
 *
 * Two things this page is careful about:
 *
 *  - It never sees or stores a credential. The token goes straight to the API,
 *    which encrypts it; everything shown here comes back from the API's status
 *    endpoint, which returns no token material at all.
 *  - Connecting is not the same as consenting to autonomous posting. Automatic
 *    posting is a separate, per-channel switch that starts off, and the page
 *    says so.
 */
class EngageAI_Admin_Channels
{
    private static ?EngageAI_Admin_Channels $instance = null;
    private EngageAI_Api_Client $client;

    public static function instance(): EngageAI_Admin_Channels
    {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct()
    {
        $this->client = new EngageAI_Api_Client();
    }

    public function register_hooks(): void
    {
        add_action('admin_post_engageai_channel_connect', [$this, 'handle_connect']);
        add_action('admin_post_engageai_channel_token', [$this, 'handle_token']);
        add_action('admin_post_engageai_channel_verify', [$this, 'handle_verify']);
        add_action('admin_post_engageai_channel_auto_post', [$this, 'handle_auto_post']);
        add_action('admin_post_engageai_channel_disconnect', [$this, 'handle_disconnect']);
    }

    /* --------------------------------------------------------- form handlers */

    /**
     * Asks the API for the provider's consent URL and sends the admin there.
     * The redirect target is a third-party host, so it deliberately uses
     * wp_redirect() with an explicit allow-list rather than wp_safe_redirect(),
     * which only permits this site's own host.
     */
    public function handle_connect(): void
    {
        [$org_id, $channel] = $this->guard('engageai_channel_connect');

        $result = $this->client->start_channel_authorization($org_id, $channel, $this->page_url());
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $url = (string) ($result['authorize_url'] ?? '');
        if ($url === '' || !$this->is_allowed_authorize_url($url)) {
            $this->redirect(['error' => rawurlencode(__('The API returned an authorization link that could not be trusted.', 'engage-ai'))]);
        }
        wp_redirect($url);
        exit;
    }

    public function handle_token(): void
    {
        [$org_id, $channel] = $this->guard('engageai_channel_token');

        // Not sanitize_text_field: an access token is opaque and may legally
        // contain characters that would mangle. It's validated by the provider
        // on the other side, and never rendered back into the page.
        $token = trim((string) wp_unslash($_POST['access_token'] ?? ''));
        if ($token === '') {
            $this->redirect(['error' => rawurlencode(__('Paste the access token first.', 'engage-ai'))]);
        }

        $result = $this->client->connect_channel_token($org_id, $channel, $token);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['connected' => rawurlencode((string) ($result['account_name'] ?? $channel))]);
    }

    public function handle_verify(): void
    {
        [$org_id, $channel] = $this->guard('engageai_channel_verify');

        $result = $this->client->verify_channel($org_id, $channel);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        if (!empty($result['connected'])) {
            $this->redirect(['verified' => rawurlencode((string) ($result['account_name'] ?? $channel))]);
        }
        $this->redirect(['error' => rawurlencode((string) ($result['last_error'] ?? __('That channel is no longer able to post.', 'engage-ai')))]);
    }

    public function handle_auto_post(): void
    {
        [$org_id, $channel] = $this->guard('engageai_channel_auto_post');

        $auto = !empty($_POST['auto_post']);
        $result = $this->client->set_channel_auto_post($org_id, $channel, $auto);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['auto' => $auto ? 'on' : 'off', 'channel' => $channel]);
    }

    public function handle_disconnect(): void
    {
        [$org_id, $channel] = $this->guard('engageai_channel_disconnect');

        $result = $this->client->disconnect_channel($org_id, $channel);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['disconnected' => $channel]);
    }

    /**
     * Capability + nonce + "is this plugin even connected" check, shared by
     * every handler above.
     * @return array{0:int,1:string} [organization id, channel key]
     */
    private function guard(string $action): array
    {
        if (!current_user_can('manage_options') || !check_admin_referer($action)) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = (int) $this->client->get_organization_id();
        $channel = sanitize_key($_POST['channel'] ?? '');
        if (!$org_id || $channel === '') {
            $this->redirect(['error' => rawurlencode(__('Connect this site to Engage AI first.', 'engage-ai'))]);
        }
        return [$org_id, $channel];
    }

    /**
     * The consent URL comes from this site's own Engage AI API, but it is a
     * link to a third party, so its host is checked against the providers this
     * plugin actually knows before the browser is sent there.
     */
    private function is_allowed_authorize_url(string $url): bool
    {
        $host = wp_parse_url($url, PHP_URL_HOST);
        $scheme = wp_parse_url($url, PHP_URL_SCHEME);
        if ($scheme !== 'https' || !is_string($host)) {
            return false;
        }
        $allowed = [
            'www.facebook.com',
            'facebook.com',
            'www.linkedin.com',
            'accounts.google.com',
            'x.com',
            'twitter.com',
        ];
        return in_array(strtolower($host), $allowed, true);
    }

    private function page_url(): string
    {
        return admin_url('admin.php?page=engageai-channels');
    }

    private function redirect(array $args): void
    {
        EngageAI_Queues::forget();
        wp_safe_redirect(add_query_arg(array_merge(['page' => 'engageai-channels'], $args), admin_url('admin.php')));
        exit;
    }

    /* ----------------------------------------------------------------- page */

    public function render_page(): void
    {
        if (!current_user_can('manage_options')) {
            return;
        }
        if (!$this->client->is_connected() || !$this->client->get_organization_id()) {
            $this->render_not_ready();
            return;
        }

        $org_id = (int) $this->client->get_organization_id();
        $response = $this->client->get_channel_connections($org_id);
        $error = is_wp_error($response) ? $response->get_error_message() : '';
        $channels = is_wp_error($response) ? [] : (array) ($response['channels'] ?? []);
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Channels', 'engage-ai'); ?></h1>
            <?php self::render_tabs('connected'); ?>
            <?php $this->render_notice(); ?>
            <?php EngageAI_Queues::render('channels', $this->client); ?>
            <?php if ($error !== ''): ?>
                <div class="notice notice-error"><p><?php echo esc_html($error); ?></p></div>
            <?php endif; ?>

            <p class="description" style="max-width:46em;">
                <?php esc_html_e('Connect the accounts you want Engage AI to post to. You sign in at the channel itself — Engage AI never sees your password, and the access it gets can be withdrawn here or at the provider at any time.', 'engage-ai'); ?>
            </p>
            <p class="description" style="max-width:46em;">
                <strong><?php esc_html_e('Connecting does not start posting.', 'engage-ai'); ?></strong>
                <?php esc_html_e('Content still goes out only when you publish it from the Content Studio, unless you switch automatic posting on for a channel below.', 'engage-ai'); ?>
            </p>
            <?php foreach ($channels as $channel): ?>
                <?php $this->render_channel_card((array) $channel); ?>
            <?php endforeach; ?>

            <?php if (empty($channels) && $error === ''): ?>
                <p><?php esc_html_e('No connectable channels were returned by the API.', 'engage-ai'); ?></p>
            <?php endif; ?>

            <h2><?php esc_html_e('What about the website?', 'engage-ai'); ?></h2>
            <p class="description" style="max-width:46em;">
                <?php esc_html_e('Your website needs no separate authorization — this plugin already publishes to it, as a draft you approve.', 'engage-ai'); ?>
            </p>
        </div>
        <?php
    }

    /**
     * The one nav shared by this page and the setup wizard. They are two halves
     * of a single job - the wizard walks you to a token, this page is where you
     * hand it over - so they read as two tabs of one screen rather than two
     * unrelated menu items you have to know to alternate between.
     *
     * @param string $current 'connected' or 'setup'
     */
    public static function render_tabs(string $current): void
    {
        $tabs = [
            'connected' => ['engageai-channels', __('Connected', 'engage-ai')],
            'setup' => ['engageai-channel-setup', __('Set one up', 'engage-ai')],
        ];
        ?>
        <nav class="nav-tab-wrapper" style="margin-bottom:1.25rem;">
            <?php foreach ($tabs as $key => [$page, $label]): ?>
                <a
                    class="nav-tab<?php echo $key === $current ? ' nav-tab-active' : ''; ?>"
                    href="<?php echo esc_url(admin_url('admin.php?page=' . $page)); ?>"
                ><?php echo esc_html($label); ?></a>
            <?php endforeach; ?>
        </nav>
        <?php
    }

    private function render_channel_card(array $channel): void
    {
        $key = (string) ($channel['channel'] ?? '');
        $label = (string) ($channel['label'] ?? $key);
        $connected = !empty($channel['connected']);
        $status = (string) ($channel['status'] ?? 'not_connected');
        ?>
        <div class="engageai-card" id="engageai-channel-<?php echo esc_attr($key); ?>" style="margin:1rem 0;padding:1rem 1.25rem;background:#fff;border:1px solid #dcdcde;border-radius:6px;max-width:46em;">
            <h2 style="margin-top:0;display:flex;align-items:center;gap:.5rem;">
                <?php echo esc_html($label); ?>
                <span style="font-size:.72em;font-weight:600;padding:.15em .6em;border-radius:999px;<?php echo esc_attr($connected ? 'background:#e6f4ea;color:#0f7b47;' : 'background:#f0f0f1;color:#50575e;'); ?>">
                    <?php echo esc_html($this->status_label($status, $connected)); ?>
                </span>
            </h2>

            <?php if ($connected): ?>
                <p style="margin:.25rem 0 1rem;">
                    <?php
                    printf(
                        /* translators: %s: the account name Engage AI posts as, e.g. a Page name or @handle */
                        esc_html__('Posting as %s.', 'engage-ai'),
                        '<strong>' . esc_html((string) ($channel['account_name'] ?? __('this account', 'engage-ai'))) . '</strong>'
                    );
                    ?>
                    <?php if (!empty($channel['account_url'])): ?>
                        <a href="<?php echo esc_url((string) $channel['account_url']); ?>" target="_blank" rel="noopener noreferrer"><?php esc_html_e('View →', 'engage-ai'); ?></a>
                    <?php endif; ?>
                    <?php if (!empty($channel['expires_at'])): ?>
                        <br><span class="description"><?php
                            printf(
                                /* translators: %s: date the channel's access expires */
                                esc_html__('Access expires %s — Engage AI renews it automatically where the channel allows that.', 'engage-ai'),
                                esc_html($this->format_date((string) $channel['expires_at']))
                            );
                        ?></span>
                    <?php endif; ?>
                    <?php if (empty($channel['supports_media'])): ?>
                        <br><span class="description"><?php esc_html_e('This channel posts text only — images and video are not attached.', 'engage-ai'); ?></span>
                    <?php endif; ?>
                </p>
            <?php elseif (!empty($channel['last_error'])): ?>
                <p class="notice notice-warning" style="padding:.5rem .75rem;margin:.25rem 0 1rem;"><?php echo esc_html((string) $channel['last_error']); ?></p>
            <?php endif; ?>

            <div style="display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;">
                <?php if (!empty($channel['oauth_available'])): ?>
                    <?php $this->render_button(
                        $connected ? __('Reconnect', 'engage-ai') : __('Connect', 'engage-ai'),
                        'engageai_channel_connect',
                        $key,
                        $connected ? 'button' : 'button button-primary'
                    ); ?>
                <?php endif; ?>

                <?php if ($connected): ?>
                    <?php $this->render_button(__('Check it still works', 'engage-ai'), 'engageai_channel_verify', $key); ?>
                    <?php $this->render_button(__('Disconnect', 'engage-ai'), 'engageai_channel_disconnect', $key); ?>
                <?php endif; ?>
            </div>

            <?php if ($connected): ?>
                <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="margin-top:1rem;">
                    <input type="hidden" name="action" value="engageai_channel_auto_post">
                    <input type="hidden" name="channel" value="<?php echo esc_attr($key); ?>">
                    <?php wp_nonce_field('engageai_channel_auto_post'); ?>
                    <label>
                        <input type="checkbox" name="auto_post" value="1" <?php checked(!empty($channel['auto_post'])); ?>>
                        <?php esc_html_e('Let Engage AI post here on its own, without asking each time', 'engage-ai'); ?>
                    </label>
                    <button type="submit" class="button" style="margin-left:.5rem;"><?php esc_html_e('Save', 'engage-ai'); ?></button>
                    <p class="description" style="margin-top:.35rem;">
                        <?php esc_html_e('Off (recommended): Engage AI drafts, you publish. On: the engagement cycle may publish here unattended.', 'engage-ai'); ?>
                    </p>
                </form>
            <?php endif; ?>

            <?php if (!$connected || empty($channel['oauth_available'])): ?>
                <details style="margin-top:1rem;">
                    <summary style="cursor:pointer;">
                        <?php echo esc_html(
                            empty($channel['oauth_available'])
                                ? __('Connect with an access token', 'engage-ai')
                                : __('Connect with an access token instead', 'engage-ai')
                        ); ?>
                    </summary>
                    <?php if (empty($channel['oauth_available'])): ?>
                        <p class="description" style="margin:.5rem 0;">
                            <?php esc_html_e('One-click sign-in is not available for this channel on your Engage AI plan yet, so it connects with a token you generate at the provider.', 'engage-ai'); ?>
                        </p>
                    <?php endif; ?>
                    <p class="description" style="margin:.5rem 0;"><?php echo esc_html((string) ($channel['manual_token_hint'] ?? '')); ?></p>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" autocomplete="off">
                        <input type="hidden" name="action" value="engageai_channel_token">
                        <input type="hidden" name="channel" value="<?php echo esc_attr($key); ?>">
                        <?php wp_nonce_field('engageai_channel_token'); ?>
                        <input type="password" name="access_token" class="large-text" autocomplete="off"
                               placeholder="<?php esc_attr_e('Paste the access token', 'engage-ai'); ?>">
                        <button type="submit" class="button" style="margin-top:.5rem;"><?php esc_html_e('Connect with this token', 'engage-ai'); ?></button>
                    </form>
                </details>
            <?php endif; ?>
        </div>
        <?php
    }

    private function render_button(string $label, string $action, string $channel, string $class = 'button'): void
    {
        ?>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="display:inline;">
            <input type="hidden" name="action" value="<?php echo esc_attr($action); ?>">
            <input type="hidden" name="channel" value="<?php echo esc_attr($channel); ?>">
            <?php wp_nonce_field($action); ?>
            <button type="submit" class="<?php echo esc_attr($class); ?>"><?php echo esc_html($label); ?></button>
        </form>
        <?php
    }

    private function status_label(string $status, bool $connected): string
    {
        if ($connected) {
            return __('Connected', 'engage-ai');
        }
        switch ($status) {
            case 'expired':
                return __('Access expired', 'engage-ai');
            case 'error':
                return __('Needs attention', 'engage-ai');
            case 'revoked':
                return __('Disconnected', 'engage-ai');
            default:
                return __('Not connected', 'engage-ai');
        }
    }

    private function format_date(string $iso): string
    {
        $timestamp = strtotime($iso);
        if (!$timestamp) {
            return $iso;
        }
        return date_i18n(get_option('date_format'), $timestamp);
    }

    private function render_notice(): void
    {
        if (isset($_GET['connected'])) {
            printf(
                '<div class="notice notice-success is-dismissible"><p>%s</p></div>',
                esc_html(sprintf(
                    /* translators: %s: the connected account name */
                    __('Connected. Engage AI can now post as %s.', 'engage-ai'),
                    sanitize_text_field(rawurldecode((string) $_GET['connected']))
                ))
            );
        } elseif (isset($_GET['verified'])) {
            printf(
                '<div class="notice notice-success is-dismissible"><p>%s</p></div>',
                esc_html(sprintf(
                    /* translators: %s: the connected account name */
                    __('Checked — %s is still able to post.', 'engage-ai'),
                    sanitize_text_field(rawurldecode((string) $_GET['verified']))
                ))
            );
        } elseif (isset($_GET['disconnected'])) {
            printf(
                '<div class="notice notice-success is-dismissible"><p>%s</p></div>',
                esc_html__('Disconnected. Engage AI can no longer post on that channel.', 'engage-ai')
            );
        } elseif (isset($_GET['auto'])) {
            printf(
                '<div class="notice notice-success is-dismissible"><p>%s</p></div>',
                esc_html(
                    $_GET['auto'] === 'on'
                        ? __('Automatic posting is on for that channel.', 'engage-ai')
                        : __('Automatic posting is off — you publish each piece yourself.', 'engage-ai')
                )
            );
        } elseif (isset($_GET['error'])) {
            printf(
                '<div class="notice notice-error is-dismissible"><p>%s</p></div>',
                esc_html(sanitize_text_field(rawurldecode((string) $_GET['error'])))
            );
        }
    }

    private function render_not_ready(): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Channels', 'engage-ai'); ?></h1>
            <p><?php esc_html_e('Connect this site to Engage AI on the Settings page first, then come back to authorize your channels.', 'engage-ai'); ?></p>
            <p><a class="button button-primary" href="<?php echo esc_url(admin_url('admin.php?page=engageai-settings')); ?>"><?php esc_html_e('Go to Settings', 'engage-ai'); ?></a></p>
        </div>
        <?php
    }
}
