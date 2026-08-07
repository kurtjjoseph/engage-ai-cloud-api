<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * The Campaign Creator: a whole run of content, planned as one arc.
 *
 *   1  Brief     the goal, what the campaign is about, and when it runs
 *   2  Plan      the arc, one piece per row - move it, swap it, drop it
 *   3  Write     every piece drafted and quality-checked, in the background
 *   4  Finish    each piece opens in the Content Studio for media and publishing
 *
 * The Content Studio builds ONE piece at a time. This page is the layer above:
 * it decides what a fortnight should say and in what order, then hands each
 * entry back to the studio's own passes to be written. So a campaign piece is
 * an ordinary content item - the library, the media pass, the quality check and
 * every publish path work on it unchanged.
 *
 * Planning and writing are deliberately separate steps. A plan is one call and
 * cheap to throw away; a build is one call per piece and produces real drafts.
 * Everything editable is editable in between.
 *
 * Nothing here publishes anything. A finished campaign is a set of drafts.
 */
class EngageAI_Admin_Campaigns
{
    private static ?EngageAI_Admin_Campaigns $instance = null;
    private EngageAI_Api_Client $client;

    /** Where a proposed plan lives between requests - it isn't saved until accepted. */
    private const PLAN_TRANSIENT = 'engageai_campaign_plan_';

    public static function instance(): EngageAI_Admin_Campaigns
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
        add_action('admin_post_engageai_campaign_plan', [$this, 'handle_plan']);
        add_action('admin_post_engageai_campaign_save', [$this, 'handle_save']);
        add_action('admin_post_engageai_campaign_build', [$this, 'handle_build']);
        add_action('admin_post_engageai_campaign_item', [$this, 'handle_item']);
        add_action('admin_post_engageai_campaign_delete', [$this, 'handle_delete']);
        add_action('wp_ajax_engageai_campaign_status', [$this, 'ajax_status']);
    }

    /* ------------------------------------------------------------ handlers */

    public function handle_plan(): void
    {
        $this->guard('engageai_campaign_plan');
        $org_id = (int) $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $goal = sanitize_key($_POST['goal'] ?? 'awareness');
        $theme = sanitize_textarea_field(wp_unslash($_POST['theme'] ?? ''));
        $pieces = max(3, min(12, (int) ($_POST['pieces'] ?? 5)));
        $starts_on = $this->clean_date($_POST['starts_on'] ?? '');
        $ends_on = $this->clean_date($_POST['ends_on'] ?? '');
        $channels = array_map('sanitize_key', (array) ($_POST['channels'] ?? []));

        $plan = $this->client->plan_campaign($org_id, $goal, $theme, $pieces, $starts_on, $ends_on, $channels);
        if (is_wp_error($plan)) {
            $this->redirect(['view' => 'new', 'error' => rawurlencode($plan->get_error_message())]);
        }
        $plan['channels'] = $channels;
        set_transient(self::PLAN_TRANSIENT . get_current_user_id(), $plan, HOUR_IN_SECONDS);
        $this->redirect(['view' => 'plan']);
    }

    /**
     * Accepts the proposed plan, with whatever the operator changed on it -
     * pieces they unticked are dropped, dates and surfaces they changed are
     * used instead of the planner's.
     */
    public function handle_save(): void
    {
        $this->guard('engageai_campaign_save');
        $org_id = (int) $this->client->get_organization_id();
        $plan = get_transient(self::PLAN_TRANSIENT . get_current_user_id());
        if (!$org_id || !is_array($plan) || empty($plan['items'])) {
            $this->redirect(['view' => 'new', 'error' => rawurlencode(__('That plan has expired - plan it again.', 'engage-ai'))]);
        }

        $keep = array_map('intval', (array) ($_POST['keep'] ?? []));
        $dates = (array) ($_POST['scheduled_on'] ?? []);
        $surfaces = (array) ($_POST['surface'] ?? []);
        $items = [];
        foreach ($plan['items'] as $index => $item) {
            if (!in_array($index, $keep, true)) {
                continue;
            }
            $date = $this->clean_date($dates[$index] ?? '');
            if ($date !== '') {
                $item['scheduled_on'] = $date;
            }
            $surface = sanitize_text_field($surfaces[$index] ?? '');
            if ($surface !== '') {
                $item['surface'] = $surface;
            }
            $items[] = $item;
        }
        if (!$items) {
            $this->redirect(['view' => 'plan', 'error' => rawurlencode(__('Keep at least one piece.', 'engage-ai'))]);
        }

        $saved = $this->client->create_campaign($org_id, [
            'name' => sanitize_text_field(wp_unslash($_POST['name'] ?? ($plan['name'] ?? ''))),
            'goal' => sanitize_key($plan['goal'] ?? 'awareness'),
            'theme' => (string) ($plan['theme'] ?? ''),
            'big_idea' => (string) ($plan['big_idea'] ?? ''),
            'audience' => (string) ($plan['audience'] ?? ''),
            'channels' => $plan['channels'] ?: null,
            'starts_on' => (string) ($plan['starts_on'] ?? ''),
            'ends_on' => (string) ($plan['ends_on'] ?? ''),
            'items' => $items,
        ]);
        if (is_wp_error($saved)) {
            $this->redirect(['view' => 'plan', 'error' => rawurlencode($saved->get_error_message())]);
        }
        delete_transient(self::PLAN_TRANSIENT . get_current_user_id());
        $this->redirect(['view' => 'campaign', 'campaign_id' => (int) ($saved['id'] ?? 0), 'saved' => 1]);
    }

    public function handle_build(): void
    {
        $this->guard('engageai_campaign_build');
        $org_id = (int) $this->client->get_organization_id();
        $campaign_id = (int) ($_POST['campaign_id'] ?? 0);
        if (!$org_id || !$campaign_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $result = $this->client->build_campaign($org_id, $campaign_id);
        if (is_wp_error($result)) {
            $this->redirect(['view' => 'campaign', 'campaign_id' => $campaign_id,
                             'error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['view' => 'campaign', 'campaign_id' => $campaign_id, 'building' => 1]);
    }

    /** One piece: move it, swap its surface, drop it, or write it on its own. */
    public function handle_item(): void
    {
        $this->guard('engageai_campaign_item');
        $org_id = (int) $this->client->get_organization_id();
        $campaign_id = (int) ($_POST['campaign_id'] ?? 0);
        $index = (int) ($_POST['index'] ?? -1);
        if (!$org_id || !$campaign_id || $index < 0) {
            $this->redirect(['error' => 'not_ready']);
        }
        $action = sanitize_key($_POST['do'] ?? 'update');
        $back = ['view' => 'campaign', 'campaign_id' => $campaign_id];

        if ($action === 'remove') {
            $result = $this->client->delete_campaign_item($org_id, $campaign_id, $index);
        } elseif ($action === 'write') {
            $result = $this->client->build_campaign_item($org_id, $campaign_id, $index, !empty($_POST['force']));
        } else {
            $fields = [];
            $date = $this->clean_date($_POST['scheduled_on'] ?? '');
            if ($date !== '') {
                $fields['scheduled_on'] = $date;
            }
            $surface = sanitize_text_field($_POST['surface'] ?? '');
            if ($surface !== '') {
                $fields['surface'] = $surface;
            }
            if (isset($_POST['headline'])) {
                $headline = sanitize_text_field(wp_unslash($_POST['headline']));
                if ($headline !== '') {
                    $fields['headline'] = $headline;
                }
            }
            if (!$fields) {
                $this->redirect($back);
            }
            $result = $this->client->update_campaign_item($org_id, $campaign_id, $index, $fields);
        }

        if (is_wp_error($result)) {
            $this->redirect($back + ['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect($back + ['updated' => 1]);
    }

    public function handle_delete(): void
    {
        $this->guard('engageai_campaign_delete');
        $org_id = (int) $this->client->get_organization_id();
        $campaign_id = (int) ($_POST['campaign_id'] ?? 0);
        if (!$org_id || !$campaign_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $result = $this->client->delete_campaign($org_id, $campaign_id);
        if (is_wp_error($result)) {
            $this->redirect(['view' => 'campaign', 'campaign_id' => $campaign_id,
                             'error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['deleted' => 1]);
    }

    /**
     * Build progress. The run is one model call per piece, so the page polls
     * this instead of holding a request open for minutes.
     */
    public function ajax_status(): void
    {
        if (!current_user_can('manage_options')) {
            wp_send_json_error(['message' => __('Not allowed.', 'engage-ai')], 403);
        }
        check_ajax_referer('engageai_campaign_status');
        $org_id = (int) $this->client->get_organization_id();
        $campaign_id = (int) ($_REQUEST['campaign_id'] ?? 0);
        if (!$org_id || !$campaign_id) {
            wp_send_json_error(['message' => __('Not connected.', 'engage-ai')], 400);
        }

        $campaign = $this->client->get_campaign($org_id, $campaign_id);
        if (is_wp_error($campaign)) {
            wp_send_json_success(['status' => 'failed', 'error' => $campaign->get_error_message()]);
        }
        $build = is_array($campaign['build'] ?? null) ? $campaign['build'] : [];
        $counts = is_array($campaign['counts'] ?? null) ? $campaign['counts'] : [];
        wp_send_json_success([
            'status' => (string) ($build['status'] ?? 'none'),
            'built' => (int) ($counts['drafted'] ?? 0),
            'failed' => (int) ($counts['failed'] ?? 0),
            'total' => (int) ($counts['total'] ?? 0),
            'error' => (string) ($build['error'] ?? ''),
        ]);
    }

    /* --------------------------------------------------------------- render */

    public function render_page(): void
    {
        if (!current_user_can('manage_options')) {
            return;
        }
        if (!$this->client->is_connected() || !$this->client->get_organization_id()) {
            $this->render_not_ready();
            return;
        }
        $view = sanitize_key($_GET['view'] ?? 'list');
        if (!in_array($view, ['list', 'new', 'plan', 'campaign'], true)) {
            $view = 'list';
        }
        ?>
        <div class="wrap engageai-studio engageai-campaigns">
            <div class="eas-masthead">
                <h1><?php esc_html_e('Campaigns', 'engage-ai'); ?></h1>
                <p><?php esc_html_e('A run of content that works as a sequence: one idea, argued piece by piece, across your channels and across the weeks. Plan it, change what you want, then have every piece written.', 'engage-ai'); ?></p>
            </div>
            <?php $this->render_notice(); ?>
            <?php
            switch ($view) {
                case 'new':
                    $this->view_new();
                    break;
                case 'plan':
                    $this->view_plan();
                    break;
                case 'campaign':
                    $this->view_campaign((int) ($_GET['campaign_id'] ?? 0));
                    break;
                default:
                    $this->view_list();
            }
            ?>
        </div>
        <?php
    }

    /* ------------------------------------------------------------ 0. list */

    private function view_list(): void
    {
        $org_id = (int) $this->client->get_organization_id();
        $campaigns = $this->client->get_campaigns($org_id);
        if (is_wp_error($campaigns)) {
            printf('<div class="eas-notice eas-notice--bad">%s</div>', esc_html($campaigns->get_error_message()));
            $campaigns = [];
        }
        ?>
        <div class="eas-actions" style="margin-top:0;margin-bottom:24px;">
            <a class="eas-btn" href="<?php echo esc_url($this->url(['view' => 'new'])); ?>"><?php esc_html_e('Plan a campaign', 'engage-ai'); ?></a>
        </div>
        <?php if (empty($campaigns)): ?>
            <div class="eas-panel eas-empty">
                <h2><?php esc_html_e('No campaigns yet', 'engage-ai'); ?></h2>
                <p><?php esc_html_e('One post is a post. Five posts that build on each other are a campaign - and they are what actually moves a number. Start with what you are trying to achieve and the dates it has to happen between.', 'engage-ai'); ?></p>
            </div>
        <?php else: ?>
            <div class="eac-list">
                <?php foreach ($campaigns as $campaign): ?>
                    <?php $counts = $campaign['counts'] ?? []; ?>
                    <a class="eac-card" href="<?php echo esc_url($this->url(['view' => 'campaign', 'campaign_id' => (int) $campaign['id']])); ?>">
                        <div class="eac-card__head">
                            <h3><?php echo esc_html((string) ($campaign['name'] ?? '')); ?></h3>
                            <?php $this->status_badge((string) ($campaign['status'] ?? '')); ?>
                        </div>
                        <?php if (!empty($campaign['big_idea'])): ?>
                            <p class="eac-card__idea"><?php echo esc_html((string) $campaign['big_idea']); ?></p>
                        <?php endif; ?>
                        <p class="eas-meta">
                            <?php
                            printf(
                                /* translators: 1: pieces written, 2: pieces in total, 3: start date, 4: end date */
                                esc_html__('%1$d of %2$d pieces written · %3$s to %4$s', 'engage-ai'),
                                (int) ($counts['drafted'] ?? 0),
                                (int) ($counts['total'] ?? 0),
                                esc_html($this->pretty_date((string) ($campaign['starts_on'] ?? ''))),
                                esc_html($this->pretty_date((string) ($campaign['ends_on'] ?? '')))
                            );
                            ?>
                        </p>
                    </a>
                <?php endforeach; ?>
            </div>
        <?php endif;
    }

    /* ------------------------------------------------------------- 1. brief */

    private function view_new(): void
    {
        $options = $this->client->get_campaign_options();
        $goals = is_wp_error($options) ? [] : ($options['goals'] ?? []);
        $channels = is_wp_error($options) ? [] : ($options['channels'] ?? []);
        $today = current_time('Y-m-d');
        $in_a_fortnight = date('Y-m-d', strtotime($today . ' +14 days'));
        ?>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
            <input type="hidden" name="action" value="engageai_campaign_plan">
            <?php wp_nonce_field('engageai_campaign_plan'); ?>

            <div class="eas-panel">
                <h2><?php esc_html_e('What should this campaign achieve?', 'engage-ai'); ?></h2>
                <p><?php esc_html_e('Every piece in the run is written against this one answer - it decides the arc, the angle and what the quality check holds the copy to.', 'engage-ai'); ?></p>
                <div class="eas-choices">
                    <?php foreach ($goals as $index => $goal): ?>
                        <label class="eas-choice">
                            <input type="radio" name="goal" value="<?php echo esc_attr((string) ($goal['key'] ?? '')); ?>" <?php checked($index, 0); ?>>
                            <span class="eas-choice__title"><?php echo esc_html((string) ($goal['label'] ?? '')); ?></span>
                            <span class="eas-choice__desc"><?php echo esc_html((string) ($goal['guidance'] ?? '')); ?></span>
                        </label>
                    <?php endforeach; ?>
                    <?php if (empty($goals)): ?>
                        <p class="eas-hint"><?php esc_html_e('Could not load the goal list from the API. Check the connection on the Settings page.', 'engage-ai'); ?></p>
                    <?php endif; ?>
                </div>
            </div>

            <div class="eas-panel">
                <h2><?php esc_html_e('What is it about, and when does it run?', 'engage-ai'); ?></h2>
                <p><?php esc_html_e('The more concrete the subject, the less generic the campaign. Name the event, the offer, the deadline - and never anything that is not true.', 'engage-ai'); ?></p>

                <div class="eas-field">
                    <label class="eas-label" for="eac-theme"><?php esc_html_e('What the campaign is about', 'engage-ai'); ?></label>
                    <textarea id="eac-theme" name="theme" rows="3" placeholder="<?php esc_attr_e('e.g. our open evening on 24 September - three slots left, first-timers welcome', 'engage-ai'); ?>"></textarea>
                </div>

                <div class="eas-row">
                    <div class="eas-field">
                        <label class="eas-label" for="eac-start"><?php esc_html_e('Starts', 'engage-ai'); ?></label>
                        <input type="date" id="eac-start" name="starts_on" value="<?php echo esc_attr($today); ?>">
                    </div>
                    <div class="eas-field">
                        <label class="eas-label" for="eac-end"><?php esc_html_e('Ends', 'engage-ai'); ?></label>
                        <input type="date" id="eac-end" name="ends_on" value="<?php echo esc_attr($in_a_fortnight); ?>">
                    </div>
                    <div class="eas-field">
                        <label class="eas-label" for="eac-pieces"><?php esc_html_e('Pieces', 'engage-ai'); ?></label>
                        <input type="number" id="eac-pieces" name="pieces" value="5" min="3" max="12" step="1">
                        <p class="eas-hint"><?php esc_html_e('Five is a full arc: earn attention, teach, prove, ask, close.', 'engage-ai'); ?></p>
                    </div>
                </div>
            </div>

            <div class="eas-panel">
                <h2><?php esc_html_e('Which channels may it use?', 'engage-ai'); ?></h2>
                <p><?php esc_html_e('Leave everything unticked to let the planner choose whatever suits each piece. Tick a few to keep the run where your audience actually is.', 'engage-ai'); ?></p>
                <div class="eac-channels">
                    <?php foreach ($channels as $channel): ?>
                        <label class="eac-channel">
                            <input type="checkbox" name="channels[]" value="<?php echo esc_attr((string) ($channel['key'] ?? '')); ?>">
                            <span><?php echo esc_html((string) ($channel['label'] ?? '')); ?></span>
                        </label>
                    <?php endforeach; ?>
                </div>
            </div>

            <div class="eas-actions">
                <button type="submit" class="eas-btn"><?php esc_html_e('Plan the campaign', 'engage-ai'); ?></button>
                <a class="eas-btn eas-btn--ghost" href="<?php echo esc_url($this->url([])); ?>"><?php esc_html_e('Cancel', 'engage-ai'); ?></a>
                <span class="eas-hint"><?php esc_html_e('Takes about a minute. Nothing is saved until you accept it.', 'engage-ai'); ?></span>
            </div>
        </form>
        <?php
    }

    /* -------------------------------------------------------------- 2. plan */

    private function view_plan(): void
    {
        $plan = get_transient(self::PLAN_TRANSIENT . get_current_user_id());
        if (!is_array($plan) || empty($plan['items'])) {
            printf('<div class="eas-notice eas-notice--bad">%s</div>',
                esc_html__('That plan has expired. Plan it again.', 'engage-ai'));
            printf('<p><a class="eas-btn" href="%s">%s</a></p>',
                esc_url($this->url(['view' => 'new'])), esc_html__('Plan a campaign', 'engage-ai'));
            return;
        }
        $surfaces = $this->surface_choices();
        ?>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
            <input type="hidden" name="action" value="engageai_campaign_save">
            <?php wp_nonce_field('engageai_campaign_save'); ?>

            <div class="eas-panel">
                <div class="eas-field">
                    <label class="eas-label" for="eac-name"><?php esc_html_e('Campaign name', 'engage-ai'); ?></label>
                    <input type="text" id="eac-name" name="name" value="<?php echo esc_attr((string) ($plan['name'] ?? '')); ?>">
                </div>
                <?php if (!empty($plan['big_idea'])): ?>
                    <div class="eac-idea">
                        <span class="eas-label"><?php esc_html_e('The one thing every piece argues', 'engage-ai'); ?></span>
                        <p><?php echo esc_html((string) $plan['big_idea']); ?></p>
                    </div>
                <?php endif; ?>
                <?php if (!empty($plan['audience'])): ?>
                    <p class="eas-meta"><?php echo esc_html((string) $plan['audience']); ?></p>
                <?php endif; ?>
            </div>

            <div class="eas-panel">
                <h2><?php esc_html_e('The run', 'engage-ai'); ?></h2>
                <p><?php esc_html_e('In order, and in the order it matters: attention first, the ask late. Untick anything you do not want, and move a date or swap a channel before it is written.', 'engage-ai'); ?></p>

                <?php foreach ($plan['items'] as $index => $item): ?>
                    <div class="eac-piece">
                        <label class="eac-piece__keep">
                            <input type="checkbox" name="keep[]" value="<?php echo esc_attr((string) $index); ?>" checked>
                            <span class="screen-reader-text"><?php esc_html_e('Keep this piece', 'engage-ai'); ?></span>
                        </label>
                        <div class="eac-piece__body">
                            <div class="eac-piece__head">
                                <span class="eas-badge eac-role"><?php echo esc_html($this->role_label((string) ($item['role'] ?? ''))); ?></span>
                                <h3><?php echo esc_html((string) ($item['headline'] ?? '')); ?></h3>
                            </div>
                            <?php if (!empty($item['angle'])): ?>
                                <p><?php echo esc_html((string) $item['angle']); ?></p>
                            <?php endif; ?>
                            <?php if (!empty($item['why'])): ?>
                                <p class="eas-idea__why"><?php echo esc_html((string) $item['why']); ?></p>
                            <?php endif; ?>
                            <div class="eac-piece__controls">
                                <label>
                                    <span class="eas-label"><?php esc_html_e('Goes out', 'engage-ai'); ?></span>
                                    <input type="date" name="scheduled_on[<?php echo esc_attr((string) $index); ?>]"
                                           value="<?php echo esc_attr((string) ($item['scheduled_on'] ?? '')); ?>">
                                </label>
                                <label>
                                    <span class="eas-label"><?php esc_html_e('Posted as', 'engage-ai'); ?></span>
                                    <?php $this->surface_select('surface[' . $index . ']', (string) ($item['surface'] ?? ''), $surfaces); ?>
                                </label>
                            </div>
                        </div>
                    </div>
                <?php endforeach; ?>
            </div>

            <div class="eas-actions">
                <button type="submit" class="eas-btn"><?php esc_html_e('Accept this plan', 'engage-ai'); ?></button>
                <a class="eas-btn eas-btn--ghost" href="<?php echo esc_url($this->url(['view' => 'new'])); ?>"><?php esc_html_e('Plan a different one', 'engage-ai'); ?></a>
                <span class="eas-hint"><?php esc_html_e('Accepting saves the plan. The copy is written in the next step.', 'engage-ai'); ?></span>
            </div>
        </form>
        <?php
    }

    /* ---------------------------------------------------------- 3. campaign */

    private function view_campaign(int $campaign_id): void
    {
        $org_id = (int) $this->client->get_organization_id();
        $campaign = $campaign_id ? $this->client->get_campaign($org_id, $campaign_id) : null;
        if (!$campaign_id || is_wp_error($campaign)) {
            printf('<div class="eas-notice eas-notice--bad">%s</div>', esc_html(
                is_wp_error($campaign) ? $campaign->get_error_message() : __('Campaign not found.', 'engage-ai')
            ));
            printf('<p><a class="eas-btn eas-btn--ghost" href="%s">%s</a></p>',
                esc_url($this->url([])), esc_html__('Back to campaigns', 'engage-ai'));
            return;
        }

        $items = $campaign['items'] ?? [];
        $counts = $campaign['counts'] ?? [];
        $build = $campaign['build'] ?? [];
        $running = ($build['status'] ?? '') === 'running';
        $pending = max(0, (int) ($counts['total'] ?? 0) - (int) ($counts['drafted'] ?? 0));
        $surfaces = $this->surface_choices();
        ?>
        <div class="eas-panel">
            <div class="eac-card__head">
                <h2><?php echo esc_html((string) ($campaign['name'] ?? '')); ?></h2>
                <?php $this->status_badge((string) ($campaign['status'] ?? '')); ?>
            </div>
            <?php if (!empty($campaign['big_idea'])): ?>
                <div class="eac-idea">
                    <span class="eas-label"><?php esc_html_e('The one thing every piece argues', 'engage-ai'); ?></span>
                    <p><?php echo esc_html((string) $campaign['big_idea']); ?></p>
                </div>
            <?php endif; ?>
            <p class="eas-meta">
                <?php
                printf(
                    /* translators: 1: pieces written, 2: pieces in total, 3: start date, 4: end date */
                    esc_html__('%1$d of %2$d pieces written · %3$s to %4$s', 'engage-ai'),
                    (int) ($counts['drafted'] ?? 0),
                    (int) ($counts['total'] ?? 0),
                    esc_html($this->pretty_date((string) ($campaign['starts_on'] ?? ''))),
                    esc_html($this->pretty_date((string) ($campaign['ends_on'] ?? '')))
                );
                ?>
            </p>

            <div class="eas-actions">
                <?php if ($pending > 0): ?>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <input type="hidden" name="action" value="engageai_campaign_build">
                        <input type="hidden" name="campaign_id" value="<?php echo esc_attr((string) $campaign_id); ?>">
                        <?php wp_nonce_field('engageai_campaign_build'); ?>
                        <button type="submit" class="eas-btn" <?php disabled($running); ?>>
                            <?php
                            printf(
                                /* translators: %d: number of pieces still to write */
                                esc_html(_n('Write the %d piece', 'Write all %d pieces', $pending, 'engage-ai')),
                                (int) $pending
                            );
                            ?>
                        </button>
                    </form>
                <?php endif; ?>
                <a class="eas-btn eas-btn--ghost" href="<?php echo esc_url($this->url([])); ?>"><?php esc_html_e('All campaigns', 'engage-ai'); ?></a>
                <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>"
                      onsubmit="return confirm('<?php echo esc_js(__('Delete this campaign? The pieces it already wrote stay in your Content Library.', 'engage-ai')); ?>');">
                    <input type="hidden" name="action" value="engageai_campaign_delete">
                    <input type="hidden" name="campaign_id" value="<?php echo esc_attr((string) $campaign_id); ?>">
                    <?php wp_nonce_field('engageai_campaign_delete'); ?>
                    <button type="submit" class="eas-btn eas-btn--ghost"><?php esc_html_e('Delete campaign', 'engage-ai'); ?></button>
                </form>
            </div>

            <?php if ($pending > 0 && !$running): ?>
                <p class="eas-hint" style="margin-top:14px;">
                    <?php esc_html_e('Each piece is written and quality-checked one at a time - about a minute each. You can leave this page; it carries on.', 'engage-ai'); ?>
                </p>
            <?php endif; ?>

            <div class="eac-progress" data-campaign="<?php echo esc_attr((string) $campaign_id); ?>"
                 data-running="<?php echo $running ? '1' : '0'; ?>" <?php echo $running ? '' : 'hidden'; ?>>
                <span class="eas-spinner" aria-hidden="true"></span>
                <span class="eac-progress__text">
                    <?php
                    printf(
                        /* translators: 1: pieces written so far, 2: pieces in total */
                        esc_html__('Writing the campaign… %1$d of %2$d done.', 'engage-ai'),
                        (int) ($counts['drafted'] ?? 0),
                        (int) ($counts['total'] ?? 0)
                    );
                    ?>
                </span>
            </div>
            <?php if (!$running && !empty($build['error'])): ?>
                <div class="eas-notice eas-notice--bad" style="margin-top:16px;">
                    <?php echo esc_html((string) $build['error']); ?>
                </div>
            <?php endif; ?>
        </div>

        <div class="eas-panel">
            <h2><?php esc_html_e('The run', 'engage-ai'); ?></h2>
            <p><?php esc_html_e('Each written piece is an ordinary draft: open it in the Content Studio to edit the copy, make the image or video, and publish it.', 'engage-ai'); ?></p>
            <?php foreach ($items as $item): ?>
                <?php $this->render_run_item($campaign_id, $item, $surfaces); ?>
            <?php endforeach; ?>
        </div>
        <?php
        $this->render_poll_script();
    }

    private function render_run_item(int $campaign_id, array $item, array $surfaces): void
    {
        $index = (int) ($item['index'] ?? 0);
        $status = (string) ($item['status'] ?? 'planned');
        $content_id = (int) ($item['content_id'] ?? 0);
        $quality = is_array($item['quality'] ?? null) ? $item['quality'] : [];
        ?>
        <div class="eac-piece eac-piece--<?php echo esc_attr($status); ?>">
            <div class="eac-piece__when">
                <span class="eac-piece__date"><?php echo esc_html($this->pretty_date((string) ($item['scheduled_on'] ?? ''))); ?></span>
                <span class="eas-badge eac-role"><?php echo esc_html($this->role_label((string) ($item['role'] ?? ''))); ?></span>
            </div>
            <div class="eac-piece__body">
                <div class="eac-piece__head">
                    <h3><?php echo esc_html((string) ($item['title'] ?? $item['headline'] ?? '')); ?></h3>
                    <?php $this->piece_badge($status, $quality); ?>
                </div>
                <p class="eas-meta"><?php echo esc_html($this->surface_label((string) ($item['surface'] ?? ''), $surfaces)); ?></p>
                <?php if ($status !== 'drafted' && !empty($item['angle'])): ?>
                    <p><?php echo esc_html((string) $item['angle']); ?></p>
                <?php endif; ?>
                <?php if (!empty($item['error'])): ?>
                    <p class="eac-piece__error"><?php echo esc_html((string) $item['error']); ?></p>
                <?php endif; ?>

                <div class="eac-piece__actions">
                    <?php if ($status === 'drafted' && $content_id): ?>
                        <a class="eas-btn eas-btn--ghost" href="<?php echo esc_url(add_query_arg(
                            ['page' => 'engageai-studio', 'step' => 'draft', 'content_id' => $content_id],
                            admin_url('admin.php')
                        )); ?>"><?php esc_html_e('Open in the Studio', 'engage-ai'); ?></a>
                    <?php else: ?>
                        <?php $this->item_form($campaign_id, $index, 'write', __('Write this piece', 'engage-ai')); ?>
                    <?php endif; ?>
                    <?php $this->item_form($campaign_id, $index, 'remove', __('Remove', 'engage-ai'), true); ?>
                </div>

                <?php if ($status !== 'drafted'): ?>
                    <form class="eac-piece__edit" method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <input type="hidden" name="action" value="engageai_campaign_item">
                        <input type="hidden" name="do" value="update">
                        <input type="hidden" name="campaign_id" value="<?php echo esc_attr((string) $campaign_id); ?>">
                        <input type="hidden" name="index" value="<?php echo esc_attr((string) $index); ?>">
                        <?php wp_nonce_field('engageai_campaign_item'); ?>
                        <label>
                            <span class="eas-label"><?php esc_html_e('Goes out', 'engage-ai'); ?></span>
                            <input type="date" name="scheduled_on" value="<?php echo esc_attr((string) ($item['scheduled_on'] ?? '')); ?>">
                        </label>
                        <label>
                            <span class="eas-label"><?php esc_html_e('Posted as', 'engage-ai'); ?></span>
                            <?php $this->surface_select('surface', (string) ($item['surface'] ?? ''), $surfaces); ?>
                        </label>
                        <button type="submit" class="eas-btn eas-btn--ghost"><?php esc_html_e('Save', 'engage-ai'); ?></button>
                    </form>
                <?php endif; ?>
            </div>
        </div>
        <?php
    }

    private function item_form(int $campaign_id, int $index, string $do, string $label, bool $confirm = false): void
    {
        ?>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>"
            <?php if ($confirm): ?>onsubmit="return confirm('<?php echo esc_js(__('Remove this piece from the campaign?', 'engage-ai')); ?>');"<?php endif; ?>>
            <input type="hidden" name="action" value="engageai_campaign_item">
            <input type="hidden" name="do" value="<?php echo esc_attr($do); ?>">
            <input type="hidden" name="campaign_id" value="<?php echo esc_attr((string) $campaign_id); ?>">
            <input type="hidden" name="index" value="<?php echo esc_attr((string) $index); ?>">
            <?php wp_nonce_field('engageai_campaign_item'); ?>
            <button type="submit" class="eas-btn eas-btn--ghost"><?php echo esc_html($label); ?></button>
        </form>
        <?php
    }

    /**
     * Polls the build while it runs and reloads the page once it finishes -
     * the run is one model call per piece, so this page would otherwise be a
     * manual refresh every minute.
     */
    private function render_poll_script(): void
    {
        $nonce = wp_create_nonce('engageai_campaign_status');
        ?>
        <script>
        (function () {
            var box = document.querySelector('.eac-progress');
            if (!box || box.getAttribute('data-running') !== '1') {
                return;
            }
            var text = box.querySelector('.eac-progress__text');
            var url = <?php echo wp_json_encode(admin_url('admin-ajax.php')); ?>;
            var body = 'action=engageai_campaign_status&_wpnonce=<?php echo esc_js($nonce); ?>&campaign_id=' + box.getAttribute('data-campaign');

            function tick() {
                fetch(url + '?' + body, {credentials: 'same-origin'})
                    .then(function (r) { return r.json(); })
                    .then(function (payload) {
                        var data = (payload && payload.data) || {};
                        if (data.status === 'running') {
                            text.textContent = <?php echo wp_json_encode(__('Writing the campaign…', 'engage-ai')); ?> +
                                ' ' + (data.built || 0) + '/' + (data.total || 0);
                            window.setTimeout(tick, 6000);
                            return;
                        }
                        window.location.reload();
                    })
                    .catch(function () { window.setTimeout(tick, 12000); });
            }
            window.setTimeout(tick, 6000);
        })();
        </script>
        <?php
    }

    /* -------------------------------------------------------------- helpers */

    /** @return array<string,array<string,string>> channel label => [surface id => surface label] */
    private function surface_choices(): array
    {
        $options = $this->client->get_campaign_options();
        if (is_wp_error($options)) {
            return [];
        }
        $out = [];
        foreach ($options['channels'] ?? [] as $channel) {
            $label = (string) ($channel['label'] ?? '');
            foreach ($channel['surfaces'] ?? [] as $surface) {
                $out[$label][(string) ($surface['id'] ?? '')] = (string) ($surface['label'] ?? '');
            }
        }
        return $out;
    }

    private function surface_select(string $name, string $current, array $choices): void
    {
        if (!$choices) {
            printf('<input type="hidden" name="%s" value="%s"><span class="eas-hint">%s</span>',
                esc_attr($name), esc_attr($current), esc_html($current));
            return;
        }
        printf('<select name="%s">', esc_attr($name));
        foreach ($choices as $group => $surfaces) {
            printf('<optgroup label="%s">', esc_attr($group));
            foreach ($surfaces as $id => $label) {
                printf('<option value="%s" %s>%s</option>',
                    esc_attr($id), selected($id, $current, false), esc_html($label));
            }
            echo '</optgroup>';
        }
        echo '</select>';
    }

    private function surface_label(string $surface_id, array $choices): string
    {
        foreach ($choices as $group => $surfaces) {
            if (isset($surfaces[$surface_id])) {
                return $group . ' · ' . $surfaces[$surface_id];
            }
        }
        return $surface_id;
    }

    /** What a piece is FOR in the arc, in the operator's language. */
    private function role_label(string $role): string
    {
        $labels = [
            'hook' => __('Hook', 'engage-ai'),
            'teach' => __('Teach', 'engage-ai'),
            'proof' => __('Proof', 'engage-ai'),
            'offer' => __('The ask', 'engage-ai'),
            'answer' => __('Answer', 'engage-ai'),
            'behind_scenes' => __('Behind the scenes', 'engage-ai'),
            'recap' => __('Close', 'engage-ai'),
        ];
        return $labels[$role] ?? ucwords(str_replace('_', ' ', $role));
    }

    private function status_badge(string $status): void
    {
        $map = [
            'planned' => ['', __('Planned', 'engage-ai')],
            'building' => ['eas-badge--warn', __('Writing…', 'engage-ai')],
            'ready' => ['eas-badge--ok', __('Every piece written', 'engage-ai')],
            'partial' => ['eas-badge--bad', __('Something failed', 'engage-ai')],
        ];
        [$class, $label] = $map[$status] ?? ['', ucfirst($status)];
        printf('<span class="eas-badge %s">%s</span>', esc_attr($class), esc_html($label));
    }

    private function piece_badge(string $status, array $quality): void
    {
        if ($status === 'drafted') {
            $score = (int) ($quality['score'] ?? 0);
            $class = !empty($quality['passed']) ? 'eas-badge--ok' : 'eas-badge--warn';
            printf('<span class="eas-badge %s">%s</span>', esc_attr($class), esc_html(sprintf(
                /* translators: %d: the quality-check score out of 100 */
                __('Written · quality %d', 'engage-ai'), $score
            )));
            return;
        }
        if ($status === 'failed') {
            printf('<span class="eas-badge eas-badge--bad">%s</span>', esc_html__('Not written', 'engage-ai'));
            return;
        }
        if ($status === 'building') {
            printf('<span class="eas-badge eas-badge--warn">%s</span>', esc_html__('Writing…', 'engage-ai'));
            return;
        }
        printf('<span class="eas-badge">%s</span>', esc_html__('Not written yet', 'engage-ai'));
    }

    private function pretty_date(string $iso): string
    {
        $time = strtotime($iso);
        return $time ? date_i18n(get_option('date_format', 'j M Y'), $time) : $iso;
    }

    /** An ISO date, or "" - a bad date is dropped rather than passed on. */
    private function clean_date($value): string
    {
        $value = sanitize_text_field((string) $value);
        return preg_match('/^\d{4}-\d{2}-\d{2}$/', $value) ? $value : '';
    }

    private function render_not_ready(): void
    {
        ?>
        <div class="wrap engageai-studio engageai-campaigns">
            <div class="eas-masthead"><h1><?php esc_html_e('Campaigns', 'engage-ai'); ?></h1></div>
            <div class="eas-notice eas-notice--bad">
                <?php esc_html_e('Connect your Engage AI account and select an organization on the Settings page first.', 'engage-ai'); ?>
            </div>
        </div>
        <?php
    }

    private function render_notice(): void
    {
        if (isset($_GET['saved'])) {
            printf('<div class="eas-notice eas-notice--ok">%s</div>',
                esc_html__('Campaign saved. Nothing is written yet - press the button below when you are ready.', 'engage-ai'));
        } elseif (isset($_GET['building'])) {
            printf('<div class="eas-notice eas-notice--ok">%s</div>',
                esc_html__('Writing the campaign. It carries on if you leave this page.', 'engage-ai'));
        } elseif (isset($_GET['deleted'])) {
            printf('<div class="eas-notice eas-notice--ok">%s</div>',
                esc_html__('Campaign deleted. The pieces it wrote are still in your Content Library.', 'engage-ai'));
        } elseif (isset($_GET['updated'])) {
            printf('<div class="eas-notice eas-notice--ok">%s</div>', esc_html__('Updated.', 'engage-ai'));
        } elseif (isset($_GET['error'])) {
            $error = $_GET['error'] === 'not_ready'
                ? __('Connect your account and select an organization on the Settings page first.', 'engage-ai')
                : rawurldecode((string) $_GET['error']);
            printf('<div class="eas-notice eas-notice--bad">%s</div>', esc_html($error));
        }
    }

    private function url(array $args): string
    {
        return add_query_arg(array_merge(['page' => 'engageai-campaigns'], $args), admin_url('admin.php'));
    }

    private function redirect(array $args): void
    {
        wp_safe_redirect($this->url($args));
        exit;
    }

    private function guard(string $nonce_action): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer($nonce_action)) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
    }
}
