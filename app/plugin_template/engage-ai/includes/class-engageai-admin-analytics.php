<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Web-search-based digital footprint scans for the selected organization -
 * the "baseline engagement measurement" feature. The first scan for an org
 * is flagged as its baseline (see AnalyticsSnapshot.is_baseline on the API
 * side); every scan after that is meant to be read against that fixed
 * reference point, not just against whatever the previous scan said.
 */
class EngageAI_Admin_Analytics
{
    private static ?EngageAI_Admin_Analytics $instance = null;
    private EngageAI_Api_Client $client;

    public static function instance(): EngageAI_Admin_Analytics
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
        add_action('admin_post_engageai_run_analytics_scan', [$this, 'handle_run_scan']);
        add_action('admin_post_engageai_register_publication', [$this, 'handle_register_publication']);
        add_action('admin_post_engageai_scan_publication', [$this, 'handle_scan_publication']);
    }

    /** Kept in sync with KNOWN_CHANNELS in the API's services/analytics_search.py. */
    private const CHANNELS = [
        'website' => 'Website',
        'google_business' => 'Google Business (reviews)',
        'facebook' => 'Facebook',
        'instagram' => 'Instagram',
        'youtube' => 'YouTube',
        'linkedin' => 'LinkedIn',
        'twitter_x' => 'X / Twitter',
        'news_mentions' => 'News mentions',
    ];

    /** Publications on these channels can't be publicly searched for - kept
     * in sync with PUBLICATION_UNSCANNABLE_CHANNELS in analytics_scoring.py. */
    private const PUBLICATION_UNSCANNABLE = ['email', 'whatsapp'];

    /** Every channel a Publication can be registered against, scannable or not. */
    private const PUBLICATION_CHANNELS = self::CHANNELS + [
        'email' => 'Email',
        'whatsapp' => 'WhatsApp',
    ];

    /** @return array<string, string> shared with Settings (targets form) and Agents (payload rendering) */
    public static function channels(): array
    {
        return self::CHANNELS;
    }

    /**
     * Where to actually go create a profile on a channel that has none yet -
     * shown on "set up this channel from scratch" tickets (Dashboard,
     * Agents) alongside the link back to Settings for recording the result.
     * No entry for website (it's the org's own site, not a signup flow) or
     * news_mentions (press coverage isn't something you sign up for).
     * @return array<string, string>
     */
    public static function signup_urls(): array
    {
        return [
            'google_business' => 'https://business.google.com/create',
            'facebook' => 'https://www.facebook.com/pages/creation/',
            'instagram' => 'https://www.instagram.com/accounts/emailsignup/',
            'youtube' => 'https://www.youtube.com/create_channel',
            'linkedin' => 'https://www.linkedin.com/company/setup/new/',
            'twitter_x' => 'https://x.com/i/flow/signup',
        ];
    }

    public function handle_run_scan(): void
    {
        $this->verify_request('engageai_run_analytics_scan');

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect_with_notice('error', __('Select an organization first.', 'engage-ai'));
        }

        $submitted = array_map('sanitize_key', (array) ($_POST['engageai_channels'] ?? []));
        $channels = array_values(array_intersect($submitted, array_keys(self::CHANNELS)));
        // empty $channels means "full sweep", which already includes website - only
        // block include_pages when channels were explicitly narrowed to exclude it.
        $website_in_scope = empty($channels) || in_array('website', $channels, true);
        $include_pages = !empty($_POST['engageai_include_pages']) && $website_in_scope;

        $result = $this->client->run_analytics_scan($org_id, $channels, $include_pages);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        // The scan now runs in the background (see POST .../analytics/scan) -
        // this redirect happens the instant it's queued, well before it's done.
        $this->redirect_with_notice('success', __('Scan started - this can take a minute or two. Refresh this page to see it once it\'s ready.', 'engage-ai'));
    }

    public function handle_register_publication(): void
    {
        $this->verify_request('engageai_register_publication');

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect_with_notice('error', __('Select an organization first.', 'engage-ai'));
        }

        $channel = sanitize_key($_POST['engageai_pub_channel'] ?? '');
        $url = esc_url_raw($_POST['engageai_pub_url'] ?? '');
        if (!array_key_exists($channel, self::PUBLICATION_CHANNELS) || $url === '') {
            $this->redirect_with_notice('error', __('Channel and URL are required to mark something as published.', 'engage-ai'));
        }

        $result = $this->client->create_publication($org_id, [
            'channel' => $channel,
            'url' => $url,
            'label' => sanitize_text_field($_POST['engageai_pub_label'] ?? '') ?: null,
        ]);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        $this->redirect_with_notice('success', __('Marked as published. Scan it any time to check its performance.', 'engage-ai'));
    }

    public function handle_scan_publication(): void
    {
        $this->verify_request('engageai_scan_publication');

        $org_id = $this->client->get_organization_id();
        $pub_id = (int) ($_POST['engageai_pub_id'] ?? 0);
        if (!$org_id || $pub_id <= 0) {
            $this->redirect_with_notice('error', __('Missing organization or publication.', 'engage-ai'));
        }

        $result = $this->client->scan_publication($org_id, $pub_id);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        $this->redirect_with_notice('success', __('Publication scan complete.', 'engage-ai'));
    }

    public function render_page(): void
    {
        if (!current_user_can('manage_options')) {
            return;
        }

        if (!$this->client->is_connected()) {
            $this->render_not_ready(__('Connect your Engage AI account on the Settings page first.', 'engage-ai'));
            return;
        }

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->render_not_ready(__('Select or create an organization on the Settings page first.', 'engage-ai'));
            return;
        }

        // Per-scan details view (?scan=<id>): every scan attempt has one, showing
        // all data used in that request. Read-only GET nav param, so absint is
        // the only sanitisation needed.
        $scan_id = isset($_GET['scan']) ? absint(wp_unslash($_GET['scan'])) : 0; // phpcs:ignore WordPress.Security.NonceVerification.Recommended
        if ($scan_id) {
            $this->render_scan_details($org_id, $scan_id);
            return;
        }

        $snapshots = $this->client->get_analytics_snapshots($org_id);
        if (is_wp_error($snapshots)) {
            if (strpos($snapshots->get_error_message(), '403') !== false || strpos($snapshots->get_error_message(), 'not enabled') !== false) {
                $this->render_not_ready(sprintf(
                    /* translators: %s: link to the Settings page */
                    esc_html__('The Analytics module is not active for this organization. Turn it on under %s.', 'engage-ai'),
                    '<a href="' . esc_url(admin_url('admin.php?page=engageai-settings')) . '">' . esc_html__('Engage AI > Settings', 'engage-ai') . '</a>'
                ));
                return;
            }
            $this->render_not_ready(esc_html($snapshots->get_error_message()));
            return;
        }

        $latest = $snapshots[0] ?? null;
        $baseline = null;
        foreach ($snapshots as $s) {
            if (!empty($s['is_baseline'])) {
                $baseline = $s;
                break;
            }
        }

        // Only present once a full-sweep scan exists - a channel-scoped scan
        // doesn't have enough data for a whole-org ranking/classification.
        $insights = $this->client->get_analytics_insights($org_id);
        $insights = is_wp_error($insights) ? null : $insights;

        $type_ranking = $this->client->get_engagement_type_ranking($org_id);
        $type_ranking = is_wp_error($type_ranking) ? [] : $type_ranking;

        $publications = $this->client->get_publications($org_id);
        $publications_error = is_wp_error($publications) ? $publications->get_error_message() : null;
        $publications = is_wp_error($publications) ? [] : $publications;
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Analytics', 'engage-ai'); ?></h1>
            <?php $this->render_notice(); ?>

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="margin: 1em 0;">
                <input type="hidden" name="action" value="engageai_run_analytics_scan">
                <?php wp_nonce_field('engageai_run_analytics_scan'); ?>

                <p><strong><?php esc_html_e('Channels to scan (leave all unchecked for the full sweep):', 'engage-ai'); ?></strong></p>
                <p>
                    <?php foreach (self::CHANNELS as $key => $label): ?>
                        <label style="margin-right: 1.25em; display: inline-block;">
                            <input type="checkbox" name="engageai_channels[]" value="<?php echo esc_attr($key); ?>" class="engageai-channel-checkbox">
                            <?php echo esc_html($label); ?>
                        </label>
                    <?php endforeach; ?>
                </p>
                <p>
                    <label>
                        <input type="checkbox" name="engageai_include_pages" value="1">
                        <?php esc_html_e('Include per-page website visibility ranking', 'engage-ai'); ?>
                    </label>
                    <span class="description"> - <?php esc_html_e('discovers individual pages and ranks them by public visibility signals (indexed, ranks for, backlinks, freshness). Not real traffic - web search cannot see actual analytics. Costs more, only applies if Website is in scope.', 'engage-ai'); ?></span>
                </p>

                <?php submit_button(__('Run new scan', 'engage-ai'), 'primary', 'submit', false); ?>
                <p class="description"><?php esc_html_e('Searches the web for this organization\'s public presence and records what it finds. The first scan becomes the baseline every later scan is compared against.', 'engage-ai'); ?></p>
            </form>

            <?php if (!$latest): ?>
                <p><?php esc_html_e('No scans yet - run one above to establish a baseline.', 'engage-ai'); ?></p>
            <?php else: ?>
                <?php if ($insights): ?>
                    <h2><?php esc_html_e('Overview', 'engage-ai'); ?></h2>
                    <p>
                        <?php esc_html_e('Org score:', 'engage-ai'); ?>
                        <?php $this->render_score_badge($insights['org_score'] ?? null); ?>
                        <?php if (($insights['baseline_org_score'] ?? null) !== null && $insights['baseline_org_score'] !== $insights['org_score']): ?>
                            <span class="description">
                                <?php
                                printf(
                                    /* translators: %d: baseline org score */
                                    esc_html__('(baseline was %d)', 'engage-ai'),
                                    (int) $insights['baseline_org_score']
                                );
                                ?>
                            </span>
                        <?php endif; ?>
                    </p>
                    <?php if (!empty($insights['availability'])): ?>
                        <p class="description">
                            <?php
                            printf(
                                /* translators: 1: channels present, 2: total channels, 3: availability percentage, 4: total pieces of content published */
                                esc_html__('%1$d of %2$d channels live (%3$d%% availability) · %4$d pieces of content published', 'engage-ai'),
                                (int) ($insights['availability']['present'] ?? 0),
                                (int) ($insights['availability']['total'] ?? 0),
                                (int) ($insights['availability']['score'] ?? 0),
                                (int) ($insights['content_volume']['total'] ?? 0)
                            );
                            ?>
                        </p>
                    <?php endif; ?>
                    <?php if (!empty($insights['org_score_breakdown'])): ?>
                        <details class="engageai-breakdown">
                            <summary><?php esc_html_e('How the org score was built (breadth: how many channels are live, blended with depth: the average across every channel, 0 for no presence)', 'engage-ai'); ?></summary>
                            <table class="widefat striped">
                                <thead><tr><th><?php esc_html_e('Channel', 'engage-ai'); ?></th><th><?php esc_html_e('Score', 'engage-ai'); ?></th></tr></thead>
                                <tbody>
                                    <?php foreach ($insights['org_score_breakdown'] as $row): ?>
                                        <tr>
                                            <td><?php echo esc_html($this->channel_label($row['channel'] ?? '')); ?></td>
                                            <td><?php echo esc_html((string) ($row['score'] ?? 0)); ?></td>
                                        </tr>
                                    <?php endforeach; ?>
                                </tbody>
                            </table>
                        </details>
                    <?php endif; ?>

                    <h3><?php esc_html_e('Channel ranking', 'engage-ai'); ?></h3>
                    <table class="widefat striped">
                        <thead>
                            <tr>
                                <th><?php esc_html_e('#', 'engage-ai'); ?></th>
                                <th><?php esc_html_e('Channel', 'engage-ai'); ?></th>
                                <th><?php esc_html_e('Score', 'engage-ai'); ?></th>
                                <th><?php esc_html_e('Trend', 'engage-ai'); ?></th>
                                <th><?php esc_html_e('How it was built', 'engage-ai'); ?></th>
                            </tr>
                        </thead>
                        <tbody>
                            <?php foreach ($insights['ranking'] as $r): ?>
                                <tr>
                                    <td><?php echo esc_html((string) ($r['rank'] ?? '')); ?></td>
                                    <td><?php echo esc_html($this->channel_label($r['channel'] ?? '')); ?></td>
                                    <td><?php $this->render_score_badge($r['score'] ?? null); ?></td>
                                    <td><?php $this->render_classification_badge($r['classification'] ?? ''); ?></td>
                                    <td><?php $this->render_breakdown_details($r['score_breakdown'] ?? []); ?></td>
                                </tr>
                            <?php endforeach; ?>
                        </tbody>
                    </table>
                    <p class="description">
                        <?php
                        printf(
                            /* translators: %s: link to the Settings page's targets section */
                            esc_html__('Trend meanings: white_space = no public presence found; new = not enough history yet; growing = score rising since last full scan; saturated = high and roughly flat, diminishing returns from more of the same; healthy = steady and not yet maxed. Set score targets under %s to turn these into next-best-action tickets.', 'engage-ai'),
                            '<a href="' . esc_url(admin_url('admin.php?page=engageai-settings')) . '">' . esc_html__('Engage AI > Settings', 'engage-ai') . '</a>'
                        );
                        ?>
                    </p>

                    <?php if (!empty($type_ranking)): ?>
                        <h3><?php esc_html_e('Engagement type ranking', 'engage-ai'); ?></h3>
                        <p class="description"><?php esc_html_e('Which KIND of content performs best on average, across every channel it was posted to - what to make more of, not where to post it.', 'engage-ai'); ?></p>
                        <table class="widefat striped">
                            <thead>
                                <tr>
                                    <th><?php esc_html_e('Content type', 'engage-ai'); ?></th>
                                    <th><?php esc_html_e('Avg score', 'engage-ai'); ?></th>
                                    <th><?php esc_html_e('Scanned / total publications', 'engage-ai'); ?></th>
                                </tr>
                            </thead>
                            <tbody>
                                <?php foreach ($type_ranking as $t): ?>
                                    <tr>
                                        <td><?php echo esc_html(ucwords(str_replace('_', ' ', (string) ($t['content_type'] ?? '')))); ?></td>
                                        <td><?php $this->render_score_badge((float) ($t['avg_score'] ?? 0)); ?></td>
                                        <td><?php echo esc_html(($t['scanned_publication_count'] ?? 0) . ' / ' . ($t['publication_count'] ?? 0)); ?></td>
                                    </tr>
                                <?php endforeach; ?>
                            </tbody>
                        </table>
                    <?php endif; ?>
                <?php endif; ?>

                <h2>
                    <?php esc_html_e('Latest scan', 'engage-ai'); ?>
                    <?php if (!empty($latest['is_baseline'])): ?>
                        <span class="engageai-risk engageai-risk-low"><?php esc_html_e('BASELINE', 'engage-ai'); ?></span>
                    <?php endif; ?>
                </h2>
                <p class="engageai-rationale"><?php echo esc_html($latest['created_at'] ?? ''); ?></p>
                <?php if (!empty($latest['requested_channels'])): ?>
                    <p class="description">
                        <?php
                        printf(
                            /* translators: %s: comma-separated list of channel labels */
                            esc_html__('Scope: %s only', 'engage-ai'),
                            esc_html(implode(', ', array_map([$this, 'channel_label'], $latest['requested_channels'])))
                        );
                        ?>
                    </p>
                <?php endif; ?>
                <?php if (($latest['status'] ?? null) === 'pending'): ?>
                    <div class="notice notice-info inline"><p><?php esc_html_e('Scan in progress - this runs in the background and can take a minute or two. Refresh this page to see the result.', 'engage-ai'); ?></p></div>
                <?php elseif (($latest['status'] ?? null) === 'failed'): ?>
                    <div class="notice notice-error inline"><p><?php echo esc_html($latest['summary'] ?? __('Scan failed.', 'engage-ai')); ?></p></div>
                <?php else: ?>
                    <?php if (!empty($latest['summary'])): ?>
                        <p><?php echo esc_html($latest['summary']); ?></p>
                    <?php endif; ?>

                    <?php if (!empty($latest['channels'])): ?>
                        <div class="engageai-tickets">
                            <?php foreach ($latest['channels'] as $channel): ?>
                                <div class="engageai-card">
                                    <h3>
                                        <?php echo esc_html($this->channel_label($channel['channel'] ?? '')); ?>
                                        <?php if (isset($channel['score'])): ?>
                                            <?php $this->render_score_badge($channel['score']); ?>
                                        <?php endif; ?>
                                    </h3>
                                    <?php if (!empty($channel['kpis']) && is_array($channel['kpis'])): ?>
                                        <div class="engageai-subfields">
                                            <?php foreach ($channel['kpis'] as $key => $value): ?>
                                                <?php if ($value === null) continue; ?>
                                                <p><strong><?php echo esc_html(ucwords(str_replace('_', ' ', (string) $key))); ?>:</strong> <?php echo esc_html(is_bool($value) ? ($value ? 'yes' : 'no') : (string) $value); ?></p>
                                            <?php endforeach; ?>
                                        </div>
                                    <?php endif; ?>
                                    <?php if (!empty($channel['notes'])): ?>
                                        <p class="engageai-why"><em><?php echo esc_html($channel['notes']); ?></em></p>
                                    <?php endif; ?>
                                    <?php if (!empty($channel['score_breakdown'])): ?>
                                        <?php $this->render_breakdown_details($channel['score_breakdown']); ?>
                                    <?php endif; ?>
                                    <?php if (!empty($channel['pages']) && is_array($channel['pages'])): ?>
                                        <?php $this->render_page_ranking($channel['pages']); ?>
                                    <?php endif; ?>
                                </div>
                            <?php endforeach; ?>
                        </div>
                    <?php else: ?>
                        <p><?php esc_html_e('No channels with findable public data this scan.', 'engage-ai'); ?></p>
                    <?php endif; ?>
                <?php endif; ?>

                <?php if (!empty($latest['sources'])): ?>
                    <p><strong><?php esc_html_e('Sources:', 'engage-ai'); ?></strong></p>
                    <ul>
                        <?php foreach ($latest['sources'] as $url): ?>
                            <li><a href="<?php echo esc_url($url); ?>" target="_blank" rel="noopener noreferrer"><?php echo esc_html($url); ?></a></li>
                        <?php endforeach; ?>
                    </ul>
                <?php endif; ?>

                <?php if ($baseline && $baseline !== $latest): ?>
                    <p class="description">
                        <?php
                        printf(
                            /* translators: %s: baseline scan date */
                            esc_html__('Baseline was recorded %s - compare against that, not just the previous scan.', 'engage-ai'),
                            esc_html($baseline['created_at'] ?? '')
                        );
                        ?>
                    </p>
                <?php endif; ?>
            <?php endif; ?>

            <?php if (count($snapshots) > 1): ?>
                <h2><?php esc_html_e('Scan history', 'engage-ai'); ?></h2>
                <table class="widefat striped">
                    <thead>
                        <tr>
                            <th><?php esc_html_e('When', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Baseline?', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Scope', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Summary', 'engage-ai'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php foreach ($snapshots as $s): ?>
                            <?php $sid = (int) ($s['id'] ?? 0); $scan_link = esc_url(admin_url('admin.php?page=engageai-analytics&scan=' . $sid)); ?>
                            <tr>
                                <td><a href="<?php echo $scan_link; ?>"><?php echo esc_html($s['created_at'] ?? ''); ?></a></td>
                                <td><?php echo !empty($s['is_baseline']) ? esc_html__('Yes', 'engage-ai') : ''; ?></td>
                                <td><?php echo !empty($s['requested_channels']) ? esc_html(implode(', ', array_map([$this, 'channel_label'], $s['requested_channels']))) : esc_html__('All', 'engage-ai'); ?></td>
                                <td>
                                    <?php if (($s['status'] ?? null) === 'pending'): ?>
                                        <em><?php esc_html_e('in progress...', 'engage-ai'); ?></em>
                                    <?php else: ?>
                                        <?php echo esc_html($s['summary'] ?? ''); ?>
                                    <?php endif; ?>
                                    <?php if (!empty($s['needs_review'])): ?> &middot; <span class="engageai-classification engageai-classification-white_space"><?php esc_html_e('needs review', 'engage-ai'); ?></span><?php endif; ?>
                                    &middot; <a href="<?php echo $scan_link; ?>"><?php esc_html_e('details', 'engage-ai'); ?> &rsaquo;</a>
                                </td>
                            </tr>
                        <?php endforeach; ?>
                    </tbody>
                </table>
            <?php endif; ?>

            <hr>
            <h2><?php esc_html_e('Publications', 'engage-ai'); ?></h2>
            <p class="description"><?php esc_html_e('Mark something as published (a generated campaign that went out, or anything posted manually) to track its own performance over time, separate from the channel-wide scans above.', 'engage-ai'); ?></p>

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" class="engageai-inline-form">
                <input type="hidden" name="action" value="engageai_register_publication">
                <?php wp_nonce_field('engageai_register_publication'); ?>
                <select name="engageai_pub_channel" required>
                    <option value=""><?php esc_html_e('Channel...', 'engage-ai'); ?></option>
                    <?php foreach (self::PUBLICATION_CHANNELS as $key => $label): ?>
                        <option value="<?php echo esc_attr($key); ?>"><?php echo esc_html($label); ?></option>
                    <?php endforeach; ?>
                </select>
                <input type="url" name="engageai_pub_url" placeholder="<?php esc_attr_e('URL (or n/a for email/WhatsApp)', 'engage-ai'); ?>" class="regular-text" required>
                <input type="text" name="engageai_pub_label" placeholder="<?php esc_attr_e('label (optional)', 'engage-ai'); ?>">
                <?php submit_button(__('Mark as published', 'engage-ai'), 'secondary', 'submit', false); ?>
            </form>

            <?php if ($publications_error): ?>
                <div class="notice notice-error"><p><?php echo esc_html($publications_error); ?></p></div>
            <?php endif; ?>

            <?php if (empty($publications)): ?>
                <p><?php esc_html_e('Nothing marked as published yet.', 'engage-ai'); ?></p>
            <?php else: ?>
                <table class="widefat striped">
                    <thead>
                        <tr>
                            <th><?php esc_html_e('Channel', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Label / URL', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Score', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Last checked', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Notes', 'engage-ai'); ?></th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php foreach ($publications as $p): ?>
                            <?php $snap = $p['latest_snapshot'] ?? null; ?>
                            <tr>
                                <td><?php echo esc_html($this->channel_label($p['channel'] ?? '')); ?></td>
                                <td>
                                    <?php if (!empty($p['label'])): ?><strong><?php echo esc_html($p['label']); ?></strong><br><?php endif; ?>
                                    <?php if (!empty($p['url']) && $p['url'] !== 'n/a'): ?>
                                        <a href="<?php echo esc_url($p['url']); ?>" target="_blank" rel="noopener noreferrer"><?php echo esc_html($p['url']); ?></a>
                                    <?php else: ?>
                                        <?php echo esc_html($p['url'] ?? ''); ?>
                                    <?php endif; ?>
                                </td>
                                <td>
                                    <?php if ($snap && $snap['score'] !== null): ?>
                                        <?php $this->render_score_badge($snap['score']); ?>
                                        <?php if (!empty($snap['score_breakdown'])): ?>
                                            <?php $this->render_breakdown_details($snap['score_breakdown']); ?>
                                        <?php endif; ?>
                                    <?php elseif (in_array($p['channel'] ?? '', self::PUBLICATION_UNSCANNABLE, true)): ?>
                                        <span class="description"><?php esc_html_e('not publicly scannable', 'engage-ai'); ?></span>
                                    <?php else: ?>
                                        <span class="description"><?php esc_html_e('not scanned yet', 'engage-ai'); ?></span>
                                    <?php endif; ?>
                                </td>
                                <td><?php echo esc_html($snap['scanned_at'] ?? ''); ?></td>
                                <td><?php echo esc_html($snap['notes'] ?? ''); ?></td>
                                <td>
                                    <?php if (!in_array($p['channel'] ?? '', self::PUBLICATION_UNSCANNABLE, true)): ?>
                                        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                                            <input type="hidden" name="action" value="engageai_scan_publication">
                                            <input type="hidden" name="engageai_pub_id" value="<?php echo esc_attr($p['id']); ?>">
                                            <?php wp_nonce_field('engageai_scan_publication'); ?>
                                            <?php submit_button(__('Scan', 'engage-ai'), 'secondary small', 'submit', false); ?>
                                        </form>
                                    <?php endif; ?>
                                </td>
                            </tr>
                        <?php endforeach; ?>
                    </tbody>
                </table>
            <?php endif; ?>
        </div>
        <?php
    }

    private function channel_label(string $channel): string
    {
        return self::PUBLICATION_CHANNELS[$channel] ?? self::CHANNELS[$channel] ?? ucwords(str_replace('_', ' ', $channel));
    }

    /**
     * Per-scan details view: every piece of data used in the request - the org
     * context sent to the model (incl. pinned channel handles), the model/tool,
     * which channels were asked for - plus, per channel, the raw KPIs the model
     * returned, the exact rule-by-rule scoring, the sources it drew on, and any
     * reconciliation flag (a held/stale value or a flagged swing). Reachable for
     * EVERY scan attempt (?scan=<id>), including failed ones.
     */
    private function render_scan_details(int $org_id, int $scan_id): void
    {
        $s = $this->client->get_analytics_snapshot($org_id, $scan_id);
        $back = esc_url(admin_url('admin.php?page=engageai-analytics'));
        echo '<div class="wrap">';
        echo '<p><a href="' . $back . '">&larr; ' . esc_html__('Back to analytics', 'engage-ai') . '</a></p>';
        if (is_wp_error($s)) {
            echo '<div class="notice notice-error"><p>' . esc_html($s->get_error_message()) . '</p></div></div>';
            return;
        }
        $status = (string) ($s['status'] ?? 'complete');
        $rc = is_array($s['request_context'] ?? null) ? $s['request_context'] : [];
        $oc = is_array($rc['org_context'] ?? null) ? $rc['org_context'] : [];
        ?>
        <h1><?php printf(esc_html__('Scan #%d', 'engage-ai'), (int) ($s['id'] ?? $scan_id)); ?></h1>
        <p>
            <strong><?php echo esc_html(ucfirst($status)); ?></strong>
            &middot; <?php echo esc_html($s['created_at'] ?? ''); ?>
            <?php if (isset($s['duration_seconds']) && $s['duration_seconds'] !== null): ?> &middot; <?php echo esc_html((string) $s['duration_seconds']); ?>s<?php endif; ?>
            <?php if (!empty($s['is_baseline'])): ?> &middot; <?php esc_html_e('baseline', 'engage-ai'); ?><?php endif; ?>
            <?php if (!empty($s['needs_review'])): ?> &middot; <span class="engageai-classification engageai-classification-white_space"><?php esc_html_e('needs review', 'engage-ai'); ?></span><?php endif; ?>
            <?php if (isset($s['org_score']) && $s['org_score'] !== null): ?> &middot; <?php esc_html_e('org score', 'engage-ai'); ?> <?php $this->render_score_badge($s['org_score']); ?><?php endif; ?>
        </p>
        <?php if (!empty($s['summary'])): ?><p class="description"><?php echo esc_html($s['summary']); ?></p><?php endif; ?>

        <h2><?php esc_html_e('Data used in the request', 'engage-ai'); ?></h2>
        <h3><?php esc_html_e('Organization context sent to the model', 'engage-ai'); ?></h3>
        <table class="widefat striped"><tbody><?php $this->render_kv_rows($oc); ?></tbody></table>
        <h3><?php esc_html_e('Request parameters', 'engage-ai'); ?></h3>
        <table class="widefat striped"><tbody>
        <?php
        $this->render_kv_rows([
            'model' => $rc['model'] ?? '-',
            'channels requested' => !empty($rc['requested_channels']) ? implode(', ', (array) $rc['requested_channels']) : __('full sweep (all 8)', 'engage-ai'),
            'channels resolved' => !empty($rc['resolved_channels']) ? implode(', ', (array) $rc['resolved_channels']) : '-',
            'tool' => $rc['tool'] ?? '-',
            'mode' => $rc['mode'] ?? '-',
            'include pages' => !empty($rc['include_pages']) ? 'yes' : 'no',
            'stubbed' => !empty($rc['stubbed']) ? ('YES - ' . ($rc['stub_reason'] ?? 'no real research performed')) : 'no (real web research)',
        ]);
        ?>
        </tbody></table>

        <h2><?php esc_html_e('Per-channel data & scoring', 'engage-ai'); ?></h2>
        <?php if (empty($s['channels'])): ?>
            <p class="description"><?php printf(esc_html__('No channel data (scan status: %s).', 'engage-ai'), esc_html($status)); ?></p>
        <?php else: foreach ($s['channels'] as $c): ?>
            <div style="border:1px solid #dcdcde;border-radius:6px;padding:12px 16px;margin:12px 0;background:#fff;">
                <h3 style="margin-top:0;">
                    <?php echo esc_html($this->channel_label($c['channel'] ?? '')); ?>
                    <?php $this->render_score_badge($c['score'] ?? null); ?>
                    <?php if (!empty($c['stale'])): ?> <span class="engageai-classification engageai-classification-white_space"><?php esc_html_e('stale · held', 'engage-ai'); ?></span><?php endif; ?>
                </h3>
                <?php if (!empty($c['review_reason'])): ?><p class="description">&#9873; <?php echo esc_html($c['review_reason']); ?></p><?php endif; ?>
                <?php if (!empty($c['stale']) && !empty($c['last_measured_at'])): ?><p class="description"><?php printf(esc_html__('Last really measured: %s', 'engage-ai'), esc_html($c['last_measured_at'])); ?></p><?php endif; ?>
                <h4><?php esc_html_e('Raw KPIs returned by the model', 'engage-ai'); ?></h4>
                <table class="widefat striped"><tbody><?php $this->render_kv_rows(is_array($c['kpis'] ?? null) ? $c['kpis'] : []); ?></tbody></table>
                <?php $this->render_breakdown_details($c['score_breakdown'] ?? []); ?>
                <?php if (!empty($c['notes'])): ?><p class="description"><?php echo esc_html($c['notes']); ?></p><?php endif; ?>
                <?php if (!empty($c['sources'])): ?>
                    <p class="description"><?php esc_html_e('Sources:', 'engage-ai'); ?>
                        <?php foreach ((array) $c['sources'] as $u): ?><a href="<?php echo esc_url($u); ?>" target="_blank" rel="noopener noreferrer"><?php echo esc_html($u); ?></a> <?php endforeach; ?>
                    </p>
                <?php endif; ?>
            </div>
        <?php endforeach; endif; ?>

        <?php if (!empty($s['sources'])): ?>
            <h2><?php printf(esc_html__('All sources (%d)', 'engage-ai'), count((array) $s['sources'])); ?></h2>
            <p class="description"><?php foreach ((array) $s['sources'] as $u): ?><a href="<?php echo esc_url($u); ?>" target="_blank" rel="noopener noreferrer"><?php echo esc_html($u); ?></a> <?php endforeach; ?></p>
        <?php endif; ?>
        </div>
        <?php
    }

    /**
     * A two-column key/value table body from an associative array - used for the
     * request context and each channel's raw KPIs on the scan-details view.
     * @param array<string, mixed> $data
     */
    private function render_kv_rows(array $data): void
    {
        if (empty($data)) {
            echo '<tr><td colspan="2"><em>' . esc_html__('none', 'engage-ai') . '</em></td></tr>';
            return;
        }
        foreach ($data as $k => $v) {
            if (is_bool($v)) {
                $val = $v ? 'yes' : 'no';
            } elseif (is_scalar($v)) {
                $val = (string) $v;
            } else {
                $val = ($v === null || $v === '') ? '-' : (string) wp_json_encode($v);
            }
            printf('<tr><th scope="row" style="width:200px;">%s</th><td>%s</td></tr>', esc_html((string) $k), esc_html($val));
        }
    }

    private function render_score_badge($score): void
    {
        if ($score === null) {
            echo '<span class="engageai-score-badge">' . esc_html__('n/a', 'engage-ai') . '</span>';
            return;
        }
        $score = (float) $score;
        $tier = $score >= 60 ? 'high' : ($score >= 30 ? 'mid' : 'low');
        $display = ((float) (int) $score === $score) ? (string) (int) $score : (string) round($score, 1);
        printf(
            '<span class="engageai-score-badge engageai-score-%s">%s</span>',
            esc_attr($tier),
            esc_html($display)
        );
    }

    private function render_classification_badge(string $classification): void
    {
        if ($classification === '') {
            return;
        }
        printf(
            '<span class="engageai-classification engageai-classification-%s">%s</span>',
            esc_attr($classification),
            esc_html(str_replace('_', ' ', $classification))
        );
    }

    /**
     * The drill-down every score on this page links back to - exactly the
     * rule/points/basis the API's deterministic scorer used (see
     * analytics_scoring.py), never regenerated or reworded here.
     * @param array<int, array{rule?: string, points?: int, basis?: mixed}> $breakdown
     */
    private function render_breakdown_details(array $breakdown): void
    {
        if (empty($breakdown)) {
            return;
        }
        ?>
        <details class="engageai-breakdown">
            <summary><?php esc_html_e('How this score was built', 'engage-ai'); ?></summary>
            <table class="widefat striped">
                <thead>
                    <tr>
                        <th><?php esc_html_e('Rule', 'engage-ai'); ?></th>
                        <th><?php esc_html_e('Points', 'engage-ai'); ?></th>
                        <th><?php esc_html_e('Based on', 'engage-ai'); ?></th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($breakdown as $row): ?>
                        <tr>
                            <td><?php echo esc_html(ucwords(str_replace('_', ' ', (string) ($row['rule'] ?? '')))); ?></td>
                            <td><?php echo esc_html((string) ($row['points'] ?? 0)); ?></td>
                            <td>
                                <?php
                                $basis = $row['basis'] ?? null;
                                echo esc_html(is_bool($basis) ? ($basis ? 'yes' : 'no') : (is_scalar($basis) ? (string) $basis : (($basis === null) ? '-' : wp_json_encode($basis))));
                                ?>
                            </td>
                        </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </details>
        <?php
    }

    /**
     * @param array<int, array{url?: string, visibility_rank?: int, signals?: array, notes?: string}> $pages
     */
    private function render_page_ranking(array $pages): void
    {
        usort($pages, static fn($a, $b) => ($a['visibility_rank'] ?? 999) <=> ($b['visibility_rank'] ?? 999));
        ?>
        <p><strong><?php esc_html_e('Page visibility ranking:', 'engage-ai'); ?></strong>
            <span class="description"><?php esc_html_e('(public discoverability signals, not real traffic)', 'engage-ai'); ?></span>
        </p>
        <table class="widefat striped">
            <thead>
                <tr>
                    <th><?php esc_html_e('#', 'engage-ai'); ?></th>
                    <th><?php esc_html_e('Page', 'engage-ai'); ?></th>
                    <th><?php esc_html_e('Signals', 'engage-ai'); ?></th>
                    <th><?php esc_html_e('Notes', 'engage-ai'); ?></th>
                </tr>
            </thead>
            <tbody>
                <?php foreach ($pages as $page): ?>
                    <tr>
                        <td><?php echo esc_html((string) ($page['visibility_rank'] ?? '')); ?></td>
                        <td>
                            <?php if (!empty($page['url'])): ?>
                                <a href="<?php echo esc_url($page['url']); ?>" target="_blank" rel="noopener noreferrer"><?php echo esc_html($page['url']); ?></a>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if (!empty($page['signals']) && is_array($page['signals'])): ?>
                                <?php foreach ($page['signals'] as $key => $value): ?>
                                    <?php if (is_scalar($value)): ?>
                                        <div><strong><?php echo esc_html(ucwords(str_replace('_', ' ', (string) $key))); ?>:</strong> <?php echo esc_html((string) $value); ?></div>
                                    <?php elseif (is_array($value)): ?>
                                        <div><strong><?php echo esc_html(ucwords(str_replace('_', ' ', (string) $key))); ?>:</strong> <?php echo esc_html(implode(', ', array_filter($value, 'is_scalar'))); ?></div>
                                    <?php endif; ?>
                                <?php endforeach; ?>
                            <?php endif; ?>
                        </td>
                        <td><?php echo esc_html($page['notes'] ?? ''); ?></td>
                    </tr>
                <?php endforeach; ?>
            </tbody>
        </table>
        <?php
    }

    private function render_not_ready(string $message): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Analytics', 'engage-ai'); ?></h1>
            <div class="notice notice-warning"><p><?php echo wp_kses_post($message); ?></p></div>
        </div>
        <?php
    }

    private function verify_request(string $action): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer($action)) {
            wp_die(esc_html__('Security check failed.', 'engage-ai'));
        }
    }

    private function redirect_with_notice(string $type, string $message): void
    {
        set_transient('engageai_notice_' . get_current_user_id(), ['type' => $type, 'message' => $message], 60);
        wp_safe_redirect(add_query_arg(['page' => 'engageai-analytics'], admin_url('admin.php')));
        exit;
    }

    private function render_notice(): void
    {
        $key = 'engageai_notice_' . get_current_user_id();
        $notice = get_transient($key);
        if (!$notice) {
            return;
        }
        delete_transient($key);
        $class = $notice['type'] === 'error' ? 'notice-error' : 'notice-success';
        printf('<div class="notice %s is-dismissible"><p>%s</p></div>', esc_attr($class), esc_html($notice['message']));
    }
}
