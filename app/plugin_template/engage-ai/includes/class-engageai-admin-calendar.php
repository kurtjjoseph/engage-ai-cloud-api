<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * The Calendar: what is going out, when, on which channel — across every
 * campaign at once.
 *
 * The dates already existed. A campaign spreads its pieces over a window and
 * stamps each one with a date, but a campaign could only ever be read one
 * campaign at a time, so "what is going out next week?" had no answer and
 * neither did "we said three Instagram posts a week and there are none".
 *
 * This shows, it does not schedule. Nothing here publishes anything, and no
 * date shown here causes a post to go out - publishing stays an explicit act in
 * the Content Studio, or Postiz where that is connected. Moving a date means
 * editing the campaign that owns the piece, which is why every entry links back
 * to its campaign rather than offering a drag-and-drop that would imply this
 * page owns the plan.
 */
class EngageAI_Admin_Calendar
{
    private static ?EngageAI_Admin_Calendar $instance = null;
    private EngageAI_Api_Client $client;

    /** Weeks shown at once. Four keeps a month in view without paging. */
    private const WEEKS = 4;

    public static function instance(): EngageAI_Admin_Calendar
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
        add_action('admin_post_engageai_save_targets', [$this, 'handle_targets']);
    }

    /** Posts-per-week intent, per channel. Intent only - nothing publishes from it. */
    public function handle_targets(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_save_targets')) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = (int) $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect(['error' => 'not_ready']);
        }

        $targets = [];
        foreach ((array) ($_POST['targets'] ?? []) as $channel => $per_week) {
            $channel = sanitize_key($channel);
            $per_week = (int) $per_week;
            if ($channel !== '' && $per_week > 0) {
                $targets[$channel] = min($per_week, 50);
            }
        }

        $result = $this->client->set_posting_targets($org_id, $targets);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['targets' => 1]);
    }

    private function redirect(array $args): void
    {
        wp_safe_redirect(add_query_arg(array_merge(['page' => 'engageai-calendar'], $args), admin_url('admin.php')));
        exit;
    }

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

        // Start on the Monday of the current week, so rows are whole weeks and
        // "posts per week" means the same thing on screen as in the target.
        $start = $this->week_start((int) current_time('timestamp'));
        $end = $start + (self::WEEKS * 7 - 1) * DAY_IN_SECONDS;

        $calendar = $this->client->get_calendar(
            $org_id,
            gmdate('Y-m-d', $start),
            gmdate('Y-m-d', $end)
        );
        $error = is_wp_error($calendar) ? $calendar->get_error_message() : '';
        $calendar = is_wp_error($calendar) ? [] : (array) $calendar;

        $items = (array) ($calendar['items'] ?? []);
        $undated = (array) ($calendar['undated'] ?? []);
        $by_channel = (array) ($calendar['by_channel'] ?? []);
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Calendar', 'engage-ai'); ?></h1>
            <?php $this->render_notice(); ?>
            <?php if ($error !== ''): ?>
                <div class="notice notice-error"><p><?php echo esc_html($error); ?></p></div>
            <?php endif; ?>

            <p class="description" style="max-width:46em;">
                <?php esc_html_e('Everything your campaigns have planned, in one place. This shows the plan — it does not publish. To move a piece or change what it says, open the campaign it belongs to.', 'engage-ai'); ?>
            </p>

            <?php $this->render_frequency($by_channel); ?>
            <?php $this->render_grid($items, $start); ?>
            <?php $this->render_undated($undated); ?>
        </div>
        <?php
    }

    /** Monday 00:00 of the week containing $timestamp. */
    private function week_start(int $timestamp): int
    {
        $weekday = (int) gmdate('N', $timestamp); // 1 = Monday
        $midnight = strtotime(gmdate('Y-m-d', $timestamp) . ' 00:00:00 UTC');
        return $midnight - ($weekday - 1) * DAY_IN_SECONDS;
    }

    private function render_grid(array $items, int $start): void
    {
        // Bucket by date once, so each cell is a lookup rather than a scan of
        // every item - a busy quarter can carry a few hundred entries.
        $by_date = [];
        foreach ($items as $item) {
            $date = (string) ($item['scheduled_on'] ?? '');
            if ($date !== '') {
                $by_date[$date][] = $item;
            }
        }
        $today = gmdate('Y-m-d', (int) current_time('timestamp'));
        ?>
        <table class="widefat" style="table-layout:fixed;margin-top:1.5rem;">
            <thead>
                <tr>
                    <?php
                    for ($d = 0; $d < 7; $d++) {
                        echo '<th style="text-align:left;">' . esc_html(gmdate('D', $start + $d * DAY_IN_SECONDS)) . '</th>';
                    }
                    ?>
                </tr>
            </thead>
            <tbody>
                <?php for ($week = 0; $week < self::WEEKS; $week++): ?>
                    <tr>
                        <?php for ($d = 0; $d < 7; $d++): ?>
                            <?php
                            $stamp = $start + ($week * 7 + $d) * DAY_IN_SECONDS;
                            $date = gmdate('Y-m-d', $stamp);
                            $entries = $by_date[$date] ?? [];
                            $is_today = $date === $today;
                            ?>
                            <td style="vertical-align:top;height:7em;<?php echo $is_today ? 'background:#f6fafe;' : ''; ?>">
                                <div class="description" style="<?php echo $is_today ? 'font-weight:600;color:#2a78d6;' : ''; ?>">
                                    <?php echo esc_html(gmdate('j M', $stamp)); ?>
                                </div>
                                <?php foreach ($entries as $entry): ?>
                                    <?php $this->render_entry($entry); ?>
                                <?php endforeach; ?>
                            </td>
                        <?php endfor; ?>
                    </tr>
                <?php endfor; ?>
            </tbody>
        </table>
        <p class="description" style="margin-top:.5rem;">
            <?php esc_html_e('Solid = written and ready. Outline = planned, not written yet.', 'engage-ai'); ?>
        </p>
        <?php
    }

    private function render_entry(array $entry): void
    {
        $written = !empty($entry['written']);
        $content_id = (int) ($entry['content_id'] ?? 0);
        $channel = (string) ($entry['channel'] ?? '');
        $headline = (string) ($entry['headline'] ?? $entry['role'] ?? '');

        // A written piece opens where it can be edited and published; one that
        // is still only planned opens the campaign that owns the plan.
        $url = $written && $content_id
            ? add_query_arg(['page' => 'engageai-studio', 'step' => 'draft', 'content_id' => $content_id], admin_url('admin.php'))
            : add_query_arg(['page' => 'engageai-campaigns', 'campaign_id' => (int) ($entry['campaign_id'] ?? 0)], admin_url('admin.php'));

        $style = $written
            ? 'background:#2a78d6;color:#fff;border:1px solid #2a78d6;'
            : 'background:#fff;color:#1d2327;border:1px dashed #a7aaad;';
        ?>
        <a href="<?php echo esc_url($url); ?>"
           title="<?php echo esc_attr(trim($channel . ' · ' . $headline, ' ·')); ?>"
           style="display:block;margin-top:.35rem;padding:.2rem .4rem;border-radius:3px;font-size:.75rem;line-height:1.3;text-decoration:none;<?php echo esc_attr($style); ?>">
            <strong><?php echo esc_html($this->channel_label($channel)); ?></strong>
            <?php if ($headline !== ''): ?>
                <br><?php echo esc_html(wp_trim_words($headline, 5, '…')); ?>
            <?php endif; ?>
        </a>
        <?php
    }

    /**
     * Planned volume against intent. A channel with no target is shown as
     * having none rather than as missing everything - the two are different
     * facts and only one of them is a problem.
     */
    private function render_frequency(array $by_channel): void
    {
        ?>
        <h2 style="margin-top:1.5rem;"><?php esc_html_e('How often you post', 'engage-ai'); ?></h2>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
            <input type="hidden" name="action" value="engageai_save_targets">
            <?php wp_nonce_field('engageai_save_targets'); ?>
            <table class="widefat striped" style="max-width:52em;">
                <thead>
                    <tr>
                        <th><?php esc_html_e('Channel', 'engage-ai'); ?></th>
                        <th style="width:12em;"><?php esc_html_e('Target / week', 'engage-ai'); ?></th>
                        <th style="width:9em;"><?php esc_html_e('Planned', 'engage-ai'); ?></th>
                        <th><?php esc_html_e('Standing', 'engage-ai'); ?></th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($this->channels_for_targets($by_channel) as $channel => $row): ?>
                        <tr>
                            <td><strong><?php echo esc_html($this->channel_label($channel)); ?></strong></td>
                            <td>
                                <input type="number" min="0" max="50" style="width:5em;"
                                       name="targets[<?php echo esc_attr($channel); ?>]"
                                       value="<?php echo esc_attr((string) ($row['target_per_week'] ?? '')); ?>"
                                       placeholder="—">
                            </td>
                            <td><?php echo esc_html((string) ($row['planned'] ?? 0)); ?></td>
                            <td>
                                <?php if ($row['target_per_week'] === null): ?>
                                    <span class="description"><?php esc_html_e('No target set', 'engage-ai'); ?></span>
                                <?php elseif ((int) $row['shortfall'] > 0): ?>
                                    <span style="color:#b32d2e;">
                                        <?php printf(
                                            /* translators: %d: how many posts short */
                                            esc_html(_n('%d short over these four weeks', '%d short over these four weeks', (int) $row['shortfall'], 'engage-ai')),
                                            (int) $row['shortfall']
                                        ); ?>
                                    </span>
                                <?php else: ?>
                                    <span style="color:#0f7b47;"><?php esc_html_e('On track', 'engage-ai'); ?></span>
                                <?php endif; ?>
                            </td>
                        </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
            <p>
                <button type="submit" class="button"><?php esc_html_e('Save targets', 'engage-ai'); ?></button>
                <span class="description" style="margin-left:.5rem;"><?php esc_html_e('Leave blank for no target. Nothing posts automatically from this.', 'engage-ai'); ?></span>
            </p>
        </form>
        <?php
    }

    /**
     * Every channel worth a row: the ones the API reported on, plus the
     * standard set, so a channel with nothing planned and no target can still
     * be given one. Without this the only way to set a target for a quiet
     * channel would be to plan something on it first.
     */
    private function channels_for_targets(array $by_channel): array
    {
        $rows = [];
        foreach (['website', 'facebook', 'instagram', 'linkedin', 'youtube', 'twitter_x', 'google_business'] as $channel) {
            $rows[$channel] = ['planned' => 0, 'target_per_week' => null, 'shortfall' => null];
        }
        foreach ($by_channel as $row) {
            if (!is_array($row) || empty($row['channel'])) {
                continue;
            }
            $rows[(string) $row['channel']] = [
                'planned' => (int) ($row['planned'] ?? 0),
                'target_per_week' => isset($row['target_per_week']) ? $row['target_per_week'] : null,
                'shortfall' => $row['shortfall'] ?? null,
            ];
        }
        return $rows;
    }

    /** Pieces with no usable date. Shown, never hidden - see the class docblock. */
    private function render_undated(array $undated): void
    {
        if (empty($undated)) {
            return;
        }
        ?>
        <h2 style="margin-top:2rem;"><?php esc_html_e('Not on the calendar yet', 'engage-ai'); ?></h2>
        <p class="description">
            <?php esc_html_e('These pieces are planned but have no date, so they are not going out. Open the campaign to give them one.', 'engage-ai'); ?>
        </p>
        <table class="widefat striped" style="max-width:52em;">
            <tbody>
                <?php foreach ($undated as $entry): ?>
                    <?php if (!is_array($entry)) { continue; } ?>
                    <tr>
                        <td><?php echo esc_html($this->channel_label((string) ($entry['channel'] ?? ''))); ?></td>
                        <td><?php echo esc_html((string) ($entry['headline'] ?? $entry['role'] ?? '')); ?></td>
                        <td>
                            <a href="<?php echo esc_url(add_query_arg(
                                ['page' => 'engageai-campaigns', 'campaign_id' => (int) ($entry['campaign_id'] ?? 0)],
                                admin_url('admin.php')
                            )); ?>"><?php echo esc_html((string) ($entry['campaign_name'] ?? __('Open campaign', 'engage-ai'))); ?></a>
                        </td>
                    </tr>
                <?php endforeach; ?>
            </tbody>
        </table>
        <?php
    }

    private function channel_label(string $channel): string
    {
        $labels = [
            'website' => __('Website', 'engage-ai'),
            'google_business' => __('Google Business', 'engage-ai'),
            'youtube' => __('YouTube', 'engage-ai'),
            'facebook' => __('Facebook', 'engage-ai'),
            'instagram' => __('Instagram', 'engage-ai'),
            'linkedin' => __('LinkedIn', 'engage-ai'),
            'twitter_x' => __('X (Twitter)', 'engage-ai'),
            'unassigned' => __('No channel', 'engage-ai'),
        ];
        return $labels[$channel] ?? ($channel !== '' ? ucfirst(str_replace('_', ' ', $channel)) : __('No channel', 'engage-ai'));
    }

    private function render_notice(): void
    {
        if (isset($_GET['targets'])) {
            printf('<div class="notice notice-success is-dismissible"><p>%s</p></div>', esc_html__('Targets saved.', 'engage-ai'));
        } elseif (isset($_GET['error'])) {
            $err = $_GET['error'] === 'not_ready'
                ? __('Connect your account and select an organization on the Settings page first.', 'engage-ai')
                : rawurldecode((string) $_GET['error']);
            printf('<div class="notice notice-error is-dismissible"><p>%s</p></div>', esc_html($err));
        }
    }

    private function render_not_ready(): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Calendar', 'engage-ai'); ?></h1>
            <div class="notice notice-warning"><p>
                <?php esc_html_e('Connect your Engage AI account and select an organization on the Settings page first.', 'engage-ai'); ?>
            </p></div>
        </div>
        <?php
    }
}
