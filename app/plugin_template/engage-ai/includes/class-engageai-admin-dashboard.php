<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * The plugin's landing page: current engagement scores (from Analytics) and
 * next-best-step tickets aggregated across every active agent module, so the
 * admin sees "where do we stand" and "what should I do next" without
 * visiting Analytics and Agents separately. Everything actionable here links
 * through to the page that actually owns that action.
 */
class EngageAI_Admin_Dashboard
{
    private static ?EngageAI_Admin_Dashboard $instance = null;
    private EngageAI_Api_Client $client;

    public static function instance(): EngageAI_Admin_Dashboard
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
        // No form handlers of its own - every action here links through to
        // the page that owns it (Analytics, Agents).
    }

    private function current_org(): ?array
    {
        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            return null;
        }
        $orgs = $this->client->get_organizations();
        if (is_wp_error($orgs)) {
            return null;
        }
        foreach ($orgs as $o) {
            if ((int) $o['id'] === (int) $org_id) {
                return $o;
            }
        }
        return null;
    }

    /** @return string[] niche keys (without "agent:" prefix) active on an org */
    private function active_niches(array $org): array
    {
        $enabled = $org['enabled_modules'] ?? [];
        $niches = [];
        foreach ($enabled as $module) {
            if (str_starts_with($module, 'agent:')) {
                $niches[] = substr($module, strlen('agent:'));
            }
        }
        return $niches;
    }

    private function niche_label(string $niche): string
    {
        $modules = EngageAI_Admin_Settings::available_modules();
        return $modules['agent:' . $niche] ?? $niche;
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

        $org = $this->current_org();
        if (!$org) {
            $this->render_not_ready(__('Select or create an organization on the Settings page first.', 'engage-ai'));
            return;
        }

        $org_id = (int) $org['id'];
        $enabled = $org['enabled_modules'] ?? [];

        $insights = null;
        if (in_array('analytics', $enabled, true)) {
            $result = $this->client->get_analytics_insights($org_id);
            $insights = is_wp_error($result) ? null : $result;
        }

        $niches = $this->active_niches($org);
        $proposed = [];
        foreach ($niches as $niche) {
            $tickets = $this->client->get_tickets($org_id, $niche, 'proposed');
            if (is_wp_error($tickets)) {
                continue;
            }
            foreach ($tickets as $t) {
                $t['niche'] = $niche;
                $proposed[] = $t;
            }
        }

        // Split proposed tickets into two kinds: an agent asking you something
        // (a "message" - it can't move forward without an answer) vs. an
        // agent proposing something for you to approve/reject/redirect (a
        // "next best step"). See render_payload() on the Agents page for the
        // same distinction.
        $messages = [];
        $next_steps = [];
        foreach ($proposed as $t) {
            if (!empty($t['payload']['question'])) {
                $messages[] = $t;
            } else {
                $next_steps[] = $t;
            }
        }
        $rank_by_risk = static function ($a, $b) {
            $rank = ['high' => 0, 'low' => 1];
            return ($rank[$a['risk'] ?? 'low'] ?? 1) <=> ($rank[$b['risk'] ?? 'low'] ?? 1);
        };
        usort($messages, $rank_by_risk);
        usort($next_steps, $rank_by_risk);
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php echo esc_html(sprintf(/* translators: %s: organization name */ __('Engage AI: %s', 'engage-ai'), $org['name'] ?? '')); ?></h1>

            <h2><?php esc_html_e('Current scores', 'engage-ai'); ?></h2>
            <?php if (!in_array('analytics', $enabled, true)): ?>
                <p class="description">
                    <?php
                    printf(
                        /* translators: %s: link to the Settings page */
                        esc_html__('The Analytics module is not active for this organization. Turn it on under %s to see engagement scores here.', 'engage-ai'),
                        '<a href="' . esc_url(admin_url('admin.php?page=engageai-settings')) . '">' . esc_html__('Settings', 'engage-ai') . '</a>'
                    );
                    ?>
                </p>
            <?php elseif (!$insights): ?>
                <p>
                    <?php
                    printf(
                        /* translators: %s: link to the Analytics page */
                        esc_html__('No scans yet. Run one on the %s page to see scores here.', 'engage-ai'),
                        '<a href="' . esc_url(admin_url('admin.php?page=engageai-analytics')) . '">' . esc_html__('Analytics', 'engage-ai') . '</a>'
                    );
                    ?>
                </p>
            <?php else: ?>
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
                <?php $this->render_radar_chart($insights); ?>
                <table class="widefat striped">
                    <thead>
                        <tr>
                            <th><?php esc_html_e('#', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Channel', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Score', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Trend', 'engage-ai'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php foreach ($insights['ranking'] ?? [] as $r): ?>
                            <tr>
                                <td><?php echo esc_html((string) ($r['rank'] ?? '')); ?></td>
                                <td><?php echo esc_html($this->channel_label($r['channel'] ?? '')); ?></td>
                                <td><?php $this->render_score_badge($r['score'] ?? null); ?></td>
                                <td><?php $this->render_classification_badge($r['classification'] ?? ''); ?></td>
                            </tr>
                        <?php endforeach; ?>
                    </tbody>
                </table>
                <p class="description">
                    <a href="<?php echo esc_url(admin_url('admin.php?page=engageai-analytics')); ?>"><?php esc_html_e('Full analytics, breakdowns and scan history →', 'engage-ai'); ?></a>
                </p>
            <?php endif; ?>

            <?php if (!empty($niches)): ?>
                <h2>
                    <?php
                    printf(
                        /* translators: %d: number of agent messages (clarifying questions) waiting on the admin */
                        esc_html__('Messages (%d)', 'engage-ai'),
                        count($messages)
                    );
                    ?>
                </h2>
                <?php if (empty($messages)): ?>
                    <p><?php esc_html_e('No questions from your agents right now.', 'engage-ai'); ?></p>
                <?php else: ?>
                    <div class="engageai-tickets">
                        <?php foreach ($messages as $t): ?>
                            <?php $this->render_message_card($t); ?>
                        <?php endforeach; ?>
                    </div>
                <?php endif; ?>
            <?php endif; ?>

            <h2>
                <?php
                printf(
                    /* translators: %d: number of next-best-step tickets waiting on the admin */
                    esc_html__('Next best steps (%d)', 'engage-ai'),
                    count($next_steps)
                );
                ?>
            </h2>
            <?php if (empty($niches)): ?>
                <p class="description">
                    <?php
                    printf(
                        /* translators: 1: "Engagement Growth" module name, 2: link to the Settings page */
                        esc_html__('No agent modules are active for this organization. Turn on %1$s under %2$s to get next-best-action suggestions here.', 'engage-ai'),
                        '<em>' . esc_html__('Engagement Growth', 'engage-ai') . '</em>',
                        '<a href="' . esc_url(admin_url('admin.php?page=engageai-settings')) . '">' . esc_html__('Settings', 'engage-ai') . '</a>'
                    );
                    ?>
                </p>
            <?php elseif (empty($next_steps)): ?>
                <p><?php esc_html_e('Nothing waiting on you right now.', 'engage-ai'); ?></p>
            <?php else: ?>
                <div class="engageai-tickets">
                    <?php foreach ($next_steps as $t): ?>
                        <?php $this->render_step_card($t); ?>
                    <?php endforeach; ?>
                </div>
            <?php endif; ?>
        </div>
        <?php
    }

    private function render_message_card(array $t): void
    {
        $niche = $t['niche'] ?? '';
        $agents_url = add_query_arg(['page' => 'engageai-agents', 'niche' => $niche], admin_url('admin.php'));
        ?>
        <div class="engageai-card">
            <h3><?php echo esc_html($t['title'] ?? ''); ?></h3>
            <p class="description"><?php echo esc_html($this->niche_label($niche)); ?></p>
            <p class="engageai-question">
                <strong><?php esc_html_e('Question:', 'engage-ai'); ?></strong>
                <?php echo esc_html($t['payload']['question']); ?>
            </p>
            <p><a href="<?php echo esc_url($agents_url); ?>" class="button button-primary"><?php esc_html_e('Answer on the Agents page', 'engage-ai'); ?></a></p>
        </div>
        <?php
    }

    private function render_step_card(array $t): void
    {
        $risk = $t['risk'] ?? 'low';
        $niche = $t['niche'] ?? '';
        $agents_url = add_query_arg(['page' => 'engageai-agents', 'niche' => $niche], admin_url('admin.php'));
        ?>
        <div class="engageai-card">
            <h3>
                <?php echo esc_html($t['title'] ?? ''); ?>
                <span class="engageai-risk engageai-risk-<?php echo esc_attr($risk); ?>">
                    <?php echo esc_html(strtoupper($risk)); ?>
                </span>
            </h3>
            <p class="description"><?php echo esc_html($this->niche_label($niche)); ?></p>
            <?php if (!empty($t['rationale'])): ?>
                <p class="engageai-rationale"><em><?php echo esc_html($t['rationale']); ?></em></p>
            <?php endif; ?>
            <?php $this->render_channel_links($t['payload'] ?? []); ?>
            <p><a href="<?php echo esc_url($agents_url); ?>" class="button button-primary"><?php esc_html_e('Review & decide', 'engage-ai'); ?></a></p>
        </div>
        <?php
    }

    /**
     * For engagement_growth tickets (payload.channel present): a link back
     * to where the org's profile URL/handle for that channel gets recorded
     * once it exists, plus - for a "set this channel up from scratch" ticket
     * - a live link to that platform's own signup/creation flow, so the
     * ticket is something to act on immediately, not just read.
     */
    private function render_channel_links(array $payload): void
    {
        $channel = $payload['channel'] ?? '';
        if ($channel === '') {
            return;
        }

        $settings_url = admin_url('admin.php?page=engageai-settings') . '#engageai-channel-' . $channel;
        $signup_urls = EngageAI_Admin_Analytics::signup_urls();
        ?>
        <p class="engageai-channel-links">
            <a href="<?php echo esc_url($settings_url); ?>"><?php esc_html_e('Update channel details', 'engage-ai'); ?></a>
            <?php if (($payload['action_type'] ?? '') === 'channel_setup_guidance' && !empty($signup_urls[$channel])): ?>
                &nbsp;|&nbsp;
                <a href="<?php echo esc_url($signup_urls[$channel]); ?>" target="_blank" rel="noopener noreferrer">
                    <?php
                    printf(
                        /* translators: %s: channel name, e.g. "Instagram" */
                        esc_html__('Sign up for %s ↗', 'engage-ai'),
                        esc_html($this->channel_label($channel))
                    );
                    ?>
                </a>
            <?php endif; ?>
        </p>
        <?php
    }

    private function channel_label(string $channel): string
    {
        $channels = EngageAI_Admin_Analytics::channels();
        return $channels[$channel] ?? ucwords(str_replace('_', ' ', $channel));
    }

    /** Shortened labels for the radar chart only - the full names (e.g.
     * "Google Business (reviews)") run too long for a fixed-radius axis
     * label. The table below keeps the full name. */
    private const RADAR_SHORT_LABELS = [
        'google_business' => 'Google Business',
        'twitter_x' => 'X/Twitter',
        'news_mentions' => 'News',
    ];

    private const RADAR_COLOR = '#2a78d6';
    private const RADAR_GRID_COLOR = '#dcdcde';
    private const RADAR_LABEL_COLOR = '#50575e';

    /**
     * A single-series radar/spider chart of the current per-channel scores,
     * one axis per channel in a fixed order (not rank order, which would
     * rotate the shape unpredictably between scans). Exact numbers stay in
     * the table right below it - this is the "shape at a glance" view, not
     * the precise one. No JS: axis labels are static text, and each vertex
     * carries a native SVG <title> for a hover tooltip.
     */
    private function render_radar_chart(array $insights): void
    {
        $channels = EngageAI_Admin_Analytics::channels();
        $keys = array_keys($channels);
        $count = count($keys);
        if ($count < 3) {
            return;
        }

        $scores = [];
        foreach ($insights['ranking'] ?? [] as $r) {
            if (!empty($r['channel'])) {
                $scores[$r['channel']] = (float) ($r['score'] ?? 0);
            }
        }

        $cx = 230;
        $cy = 230;
        $max_radius = 125;
        $label_radius = 160;

        $axis_points = [];
        $data_points = [];
        $labels = [];
        foreach ($keys as $i => $key) {
            $rad = deg2rad(-90 + (360 / $count) * $i);
            $cos = cos($rad);
            $sin = sin($rad);

            $axis_points[] = $this->polar_point($cx, $cy, $max_radius, $cos, $sin);

            $score = max(0, min(100, $scores[$key] ?? 0));
            $data_points[] = $this->polar_point($cx, $cy, $max_radius * ($score / 100), $cos, $sin);

            $labels[] = [
                'x' => round($cx + $label_radius * $cos, 1),
                'y' => round($cy + $label_radius * $sin, 1),
                'anchor' => $cos > 0.15 ? 'start' : ($cos < -0.15 ? 'end' : 'middle'),
                'dy' => $sin < -0.5 ? -2 : ($sin > 0.5 ? 12 : 4),
                'text' => self::RADAR_SHORT_LABELS[$key] ?? $channels[$key],
            ];
        }

        $rings = [];
        foreach ([0.25, 0.5, 0.75, 1.0] as $fraction) {
            $ring_points = [];
            foreach ($keys as $i => $key) {
                $rad = deg2rad(-90 + (360 / $count) * $i);
                $p = $this->polar_point($cx, $cy, $max_radius * $fraction, cos($rad), sin($rad));
                $ring_points[] = $p[0] . ',' . $p[1];
            }
            $rings[] = implode(' ', $ring_points);
        }

        $data_points_str = implode(' ', array_map(static fn($p) => $p[0] . ',' . $p[1], $data_points));
        ?>
        <div class="engageai-radar">
            <svg viewBox="0 0 460 460" role="img" aria-label="<?php esc_attr_e('Radar chart of current per-channel engagement scores', 'engage-ai'); ?>">
                <?php foreach ($rings as $ring): ?>
                    <polygon points="<?php echo esc_attr($ring); ?>" fill="none" stroke="<?php echo esc_attr(self::RADAR_GRID_COLOR); ?>" stroke-width="1"></polygon>
                <?php endforeach; ?>
                <?php foreach ($axis_points as $p): ?>
                    <line x1="<?php echo esc_attr((string) $cx); ?>" y1="<?php echo esc_attr((string) $cy); ?>" x2="<?php echo esc_attr((string) $p[0]); ?>" y2="<?php echo esc_attr((string) $p[1]); ?>" stroke="<?php echo esc_attr(self::RADAR_GRID_COLOR); ?>" stroke-width="1"></line>
                <?php endforeach; ?>
                <polygon points="<?php echo esc_attr($data_points_str); ?>" fill="<?php echo esc_attr(self::RADAR_COLOR); ?>" fill-opacity="0.18" stroke="<?php echo esc_attr(self::RADAR_COLOR); ?>" stroke-width="2"></polygon>
                <?php foreach ($data_points as $i => $p): ?>
                    <?php $key = $keys[$i]; ?>
                    <circle cx="<?php echo esc_attr((string) $p[0]); ?>" cy="<?php echo esc_attr((string) $p[1]); ?>" r="4" fill="<?php echo esc_attr(self::RADAR_COLOR); ?>" stroke="#fff" stroke-width="1.5">
                        <title><?php echo esc_html($channels[$key] . ': ' . (string) ($scores[$key] ?? 0)); ?></title>
                    </circle>
                <?php endforeach; ?>
                <?php foreach ($labels as $l): ?>
                    <text x="<?php echo esc_attr((string) $l['x']); ?>" y="<?php echo esc_attr((string) $l['y']); ?>" dy="<?php echo esc_attr((string) $l['dy']); ?>" text-anchor="<?php echo esc_attr($l['anchor']); ?>" font-size="11" fill="<?php echo esc_attr(self::RADAR_LABEL_COLOR); ?>"><?php echo esc_html($l['text']); ?></text>
                <?php endforeach; ?>
            </svg>
        </div>
        <?php
    }

    /** @return array{0: float, 1: float} */
    private function polar_point(float $cx, float $cy, float $radius, float $cos, float $sin): array
    {
        return [round($cx + $radius * $cos, 1), round($cy + $radius * $sin, 1)];
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

    private function render_not_ready(string $message): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Engage AI', 'engage-ai'); ?></h1>
            <div class="notice notice-warning"><p><?php echo wp_kses_post($message); ?></p></div>
        </div>
        <?php
    }
}
