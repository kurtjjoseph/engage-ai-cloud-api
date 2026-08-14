<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * "Set up a channel" — the wizard that gets a channel from nothing to
 * authorized, one step at a time.
 *
 * The Channels page answers "is it connected?". This page answers the question
 * underneath it: "how do I get there?" — create the Page, make the Instagram
 * account a business account, claim the Google listing, then authorize. Each
 * step that sends you somewhere carries the real link, and the steps that ask
 * for an access token link straight to the page that issues one, because
 * "generate an access token" is otherwise an invitation to get lost in a
 * developer console.
 *
 * The content comes from the API (GET /organizations/{id}/channels/setup-guide)
 * rather than living here, so a provider moving its token page is one deploy to
 * fix rather than a plugin update every customer has to install. This class is
 * the renderer and remembers where you were.
 */
class EngageAI_Admin_Channel_Setup
{
    private const PROGRESS_META = '_engageai_channel_setup_progress';

    private static ?EngageAI_Admin_Channel_Setup $instance = null;
    private EngageAI_Api_Client $client;

    public static function instance(): EngageAI_Admin_Channel_Setup
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
        add_action('admin_post_engageai_setup_reset', [$this, 'handle_reset']);
    }

    public function handle_reset(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_setup_reset')) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        delete_user_meta(get_current_user_id(), self::PROGRESS_META);
        wp_safe_redirect(admin_url('admin.php?page=engageai-channel-setup'));
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
        $guide = $this->client->get_channel_setup_guide($org_id);
        if (is_wp_error($guide)) {
            $this->render_shell(function () use ($guide) {
                printf('<div class="notice notice-error"><p>%s</p></div>', esc_html($guide->get_error_message()));
            });
            return;
        }

        $channels = (array) ($guide['channels'] ?? []);
        $channel_key = sanitize_key($_GET['channel'] ?? '');
        $branch = sanitize_key($_GET['have'] ?? '');       // yes | no
        $step = max(0, (int) ($_GET['step'] ?? 0));

        $current = null;
        foreach ($channels as $entry) {
            if ((string) ($entry['channel'] ?? '') === $channel_key) {
                $current = (array) $entry;
                break;
            }
        }

        $this->render_shell(function () use ($channels, $current, $branch, $step) {
            if ($current === null) {
                $this->render_picker($channels);
                return;
            }
            if ($branch !== 'yes' && $branch !== 'no') {
                $this->render_branch($current);
                return;
            }
            $this->render_steps($current, $branch, $step);
        });
    }

    private function render_shell(callable $body): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Channels', 'engage-ai'); ?></h1>
            <?php
            if (class_exists('EngageAI_Admin_Channels')) {
                EngageAI_Admin_Channels::render_tabs('setup');
            }
            ?>
            <p class="description" style="max-width:46em;">
                <?php esc_html_e('One channel at a time, from “we don’t have one yet” to “Engage AI can post on it”. Nothing here asks you for a password, and you can stop halfway and come back — this page remembers where you were.', 'engage-ai'); ?>
            </p>
            <?php $body(); ?>
        </div>
        <?php
    }

    /** Step 0: which channel are we doing? */
    private function render_picker(array $channels): void
    {
        $progress = $this->progress();
        ?>
        <div style="display:grid;gap:.75rem;max-width:46em;margin-top:1.25rem;">
            <?php foreach ($channels as $channel): ?>
                <?php
                $key = (string) ($channel['channel'] ?? '');
                $done = !empty($channel['connected']);
                $saved = $progress[$key] ?? null;
                ?>
                <a href="<?php echo esc_url($this->url($done ? [] : array_filter([
                    'channel' => $key,
                    'have' => $saved['have'] ?? null,
                    'step' => isset($saved['step']) ? (string) $saved['step'] : null,
                ]))); ?>"
                   style="display:flex;align-items:center;gap:1rem;padding:1rem 1.25rem;background:#fff;border:1px solid #dcdcde;border-radius:6px;text-decoration:none;color:inherit;<?php echo $done ? 'opacity:.7;' : ''; ?>">
                    <span style="flex:1;">
                        <strong style="font-size:1.05em;"><?php echo esc_html((string) ($channel['label'] ?? $key)); ?></strong>
                        <span style="display:block;color:#50575e;font-size:.9em;margin-top:.15rem;">
                            <?php if ($done): ?>
                                <?php printf(
                                    /* translators: %s: the connected account name */
                                    esc_html__('Connected as %s — nothing left to do.', 'engage-ai'),
                                    esc_html((string) ($channel['account_name'] ?? __('this account', 'engage-ai')))
                                ); ?>
                            <?php elseif ($saved): ?>
                                <?php esc_html_e('In progress — pick up where you left off.', 'engage-ai'); ?>
                            <?php elseif (!empty($channel['oauth_available'])): ?>
                                <?php esc_html_e('Set it up, then connect it in one click.', 'engage-ai'); ?>
                            <?php else: ?>
                                <?php esc_html_e('Set it up, then connect it with an access token.', 'engage-ai'); ?>
                            <?php endif; ?>
                        </span>
                    </span>
                    <span style="font-size:.75em;font-weight:600;padding:.2em .7em;border-radius:999px;<?php echo esc_attr($done ? 'background:#e6f4ea;color:#0f7b47;' : 'background:#f0f0f1;color:#50575e;'); ?>">
                        <?php echo esc_html($done ? __('Done', 'engage-ai') : __('Start', 'engage-ai')); ?>
                    </span>
                </a>
            <?php endforeach; ?>
        </div>

        <?php // "See what's connected" lived here; it is the Connected tab now. ?>
        <?php if (!empty($this->progress())): ?>
            <p style="margin-top:1.5rem;"><?php $this->reset_button(); ?></p>
        <?php endif; ?>
        <?php
    }

    /** Step 1: does this channel exist yet? Decides which steps follow. */
    private function render_branch(array $channel): void
    {
        $key = (string) $channel['channel'];
        ?>
        <div style="max-width:40em;margin-top:1.25rem;padding:1.5rem;background:#fff;border:1px solid #dcdcde;border-radius:6px;">
            <p style="font-size:.8em;text-transform:uppercase;letter-spacing:.05em;color:#50575e;margin:0 0 .35rem;"><?php echo esc_html((string) $channel['label']); ?></p>
            <h2 style="margin-top:0;"><?php echo esc_html((string) ($channel['exists_question'] ?? '')); ?></h2>
            <p class="description"><?php esc_html_e('There’s no wrong answer — it only decides which steps you get.', 'engage-ai'); ?></p>
            <p style="margin-top:1.25rem;">
                <a class="button button-primary" href="<?php echo esc_url($this->url(['channel' => $key, 'have' => 'yes', 'step' => '0'])); ?>"><?php esc_html_e('Yes, we have it', 'engage-ai'); ?></a>
                <a class="button" href="<?php echo esc_url($this->url(['channel' => $key, 'have' => 'no', 'step' => '0'])); ?>"><?php esc_html_e('No, not yet', 'engage-ai'); ?></a>
                <a class="button button-link" href="<?php echo esc_url($this->url([])); ?>"><?php esc_html_e('Back', 'engage-ai'); ?></a>
            </p>
        </div>
        <?php
    }

    /** Steps 2..n: the actual walkthrough. */
    private function render_steps(array $channel, string $branch, int $index): void
    {
        $key = (string) $channel['channel'];
        $steps = $this->steps_for($channel, $branch);
        if (empty($steps)) {
            $this->render_finish($channel);
            return;
        }
        if ($index >= count($steps)) {
            $this->render_finish($channel);
            return;
        }

        $this->remember($key, $branch, $index);
        $step = (array) $steps[$index];
        $total = count($steps);
        ?>
        <div style="max-width:42em;margin-top:1.25rem;">
            <div style="display:flex;gap:.3rem;margin-bottom:1rem;">
                <?php for ($i = 0; $i < $total; $i++): ?>
                    <span style="height:4px;flex:1;border-radius:2px;background:<?php echo esc_attr($i <= $index ? '#1d4ed8' : '#dcdcde'); ?>;"></span>
                <?php endfor; ?>
            </div>

            <div style="padding:1.5rem;background:#fff;border:1px solid #dcdcde;border-radius:6px;">
                <p style="font-size:.8em;text-transform:uppercase;letter-spacing:.05em;color:#50575e;margin:0 0 .35rem;">
                    <?php echo esc_html((string) $channel['label']); ?> ·
                    <?php printf(
                        /* translators: 1: current step number, 2: total steps */
                        esc_html__('Step %1$d of %2$d', 'engage-ai'),
                        $index + 1,
                        $total
                    ); ?>
                </p>
                <h2 style="margin-top:0;"><?php echo esc_html((string) ($step['title'] ?? '')); ?></h2>
                <p style="line-height:1.6;"><?php echo esc_html((string) ($step['body'] ?? '')); ?></p>

                <?php if (!empty($step['chips'])): ?>
                    <div style="display:grid;gap:.5rem;margin:1rem 0;">
                        <?php foreach ((array) $step['chips'] as $n => $chip): ?>
                            <?php $id = 'eas-chip-' . $index . '-' . (int) $n; ?>
                            <div style="display:flex;align-items:center;gap:.6rem;padding:.6rem .8rem;background:#f6f7f9;border-radius:6px;">
                                <span style="font-size:.8em;color:#50575e;min-width:6em;"><?php echo esc_html((string) ($chip['label'] ?? '')); ?></span>
                                <code id="<?php echo esc_attr($id); ?>" style="flex:1;background:none;word-break:break-all;"><?php echo esc_html((string) ($chip['value'] ?? '')); ?></code>
                                <button type="button" class="button button-small engageai-copy" data-target="<?php echo esc_attr($id); ?>"><?php esc_html_e('Copy', 'engage-ai'); ?></button>
                            </div>
                        <?php endforeach; ?>
                    </div>
                <?php endif; ?>

                <?php if (!empty($step['link']['url'])): ?>
                    <p style="margin:1rem 0;">
                        <a class="button button-primary" href="<?php echo esc_url((string) $step['link']['url']); ?>" target="_blank" rel="noopener noreferrer">
                            <?php echo esc_html((string) ($step['link']['label'] ?? __('Open', 'engage-ai'))); ?> ↗
                        </a>
                        <span class="description" style="margin-left:.5rem;"><?php esc_html_e('Opens in a new tab — this page keeps your place.', 'engage-ai'); ?></span>
                    </p>
                <?php endif; ?>

                <?php if (!empty($step['note'])): ?>
                    <p class="description" style="border-left:3px solid #dcdcde;padding-left:.8rem;margin:1rem 0 0;"><?php echo esc_html((string) $step['note']); ?></p>
                <?php endif; ?>

                <?php if (!empty($step['wait'])): ?>
                    <p style="margin:1rem 0 0;padding:.7rem .9rem;background:#fcf9e8;border-radius:6px;"><?php echo esc_html((string) $step['wait']); ?></p>
                <?php endif; ?>
            </div>

            <p style="margin-top:1.25rem;">
                <a class="button button-primary" href="<?php echo esc_url($this->url(['channel' => $key, 'have' => $branch, 'step' => (string) ($index + 1)])); ?>">
                    <?php echo esc_html($index + 1 >= $total ? __('Finish', 'engage-ai') : __('Next step', 'engage-ai')); ?>
                </a>
                <?php if ($index > 0): ?>
                    <a class="button" href="<?php echo esc_url($this->url(['channel' => $key, 'have' => $branch, 'step' => (string) ($index - 1)])); ?>"><?php esc_html_e('Back', 'engage-ai'); ?></a>
                <?php endif; ?>
                <a class="button button-link" href="<?php echo esc_url($this->url([])); ?>"><?php esc_html_e('All channels', 'engage-ai'); ?></a>
            </p>
        </div>

        <script>
        document.querySelectorAll('.engageai-copy').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var el = document.getElementById(btn.getAttribute('data-target'));
                if (!el) { return; }
                navigator.clipboard.writeText(el.textContent).then(function () {
                    var prev = btn.textContent;
                    btn.textContent = <?php echo wp_json_encode(__('Copied', 'engage-ai')); ?>;
                    setTimeout(function () { btn.textContent = prev; }, 1500);
                });
            });
        });
        </script>
        <?php
    }

    private function render_finish(array $channel): void
    {
        $key = (string) $channel['channel'];
        $this->clear($key);
        ?>
        <div style="max-width:42em;margin-top:1.25rem;padding:1.5rem;background:#fff;border:1px solid #dcdcde;border-radius:6px;">
            <h2 style="margin-top:0;"><?php printf(
                /* translators: %s: channel name, e.g. Instagram */
                esc_html__('%s is ready to connect', 'engage-ai'),
                esc_html((string) $channel['label'])
            ); ?></h2>
            <p><?php esc_html_e('That’s the setup done. The last thing is to authorize Engage AI on the Channels page — and even then, nothing posts until you publish something.', 'engage-ai'); ?></p>
            <p>
                <a class="button button-primary" href="<?php echo esc_url(admin_url('admin.php?page=engageai-channels#engageai-channel-' . $key)); ?>"><?php esc_html_e('Go to Channels and connect it', 'engage-ai'); ?></a>
                <a class="button" href="<?php echo esc_url($this->url([])); ?>"><?php esc_html_e('Set up another channel', 'engage-ai'); ?></a>
            </p>
        </div>
        <?php
    }

    /* ------------------------------------------------------------ internals */

    /**
     * The steps for one branch, in order: build it (only when it doesn't exist
     * yet), get it into a state that can be authorized, then authorize.
     */
    private function steps_for(array $channel, string $branch): array
    {
        $steps = [];
        if ($branch === 'no') {
            $steps = array_merge($steps, (array) ($channel['create_steps'] ?? []));
        }
        $steps = array_merge($steps, (array) ($channel['prepare_steps'] ?? []));
        return array_merge($steps, (array) ($channel['connect_steps'] ?? []));
    }

    private function url(array $args): string
    {
        return add_query_arg(array_merge(['page' => 'engageai-channel-setup'], $args), admin_url('admin.php'));
    }

    /** @return array<string,array{have:string,step:int}> */
    private function progress(): array
    {
        $saved = get_user_meta(get_current_user_id(), self::PROGRESS_META, true);
        return is_array($saved) ? $saved : [];
    }

    private function remember(string $channel, string $branch, int $step): void
    {
        $progress = $this->progress();
        $progress[$channel] = ['have' => $branch, 'step' => $step];
        update_user_meta(get_current_user_id(), self::PROGRESS_META, $progress);
    }

    private function clear(string $channel): void
    {
        $progress = $this->progress();
        unset($progress[$channel]);
        if (empty($progress)) {
            delete_user_meta(get_current_user_id(), self::PROGRESS_META);
        } else {
            update_user_meta(get_current_user_id(), self::PROGRESS_META, $progress);
        }
    }

    private function reset_button(): void
    {
        ?>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="display:inline;">
            <input type="hidden" name="action" value="engageai_setup_reset">
            <?php wp_nonce_field('engageai_setup_reset'); ?>
            <button type="submit" class="button button-link"><?php esc_html_e('Start over', 'engage-ai'); ?></button>
        </form>
        <?php
    }

    private function render_not_ready(): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Channels', 'engage-ai'); ?></h1>
            <p><?php esc_html_e('Connect this site to Engage AI on the Settings page first — the wizard fills your organisation’s own name and website into the steps.', 'engage-ai'); ?></p>
            <p><a class="button button-primary" href="<?php echo esc_url(admin_url('admin.php?page=engageai-settings')); ?>"><?php esc_html_e('Go to Settings', 'engage-ai'); ?></a></p>
        </div>
        <?php
    }
}
