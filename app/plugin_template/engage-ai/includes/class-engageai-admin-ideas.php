<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Ideas: the first step of the workflow, and the one that used to have nowhere
 * to live.
 *
 * The Content Studio could always generate ideas, but it held them in a
 * transient keyed by the goal they were generated against - so they expired,
 * belonged to this one site, and vanished the moment the operator changed
 * their mind about the goal. An idea someone liked on Tuesday could not be
 * found again on Thursday.
 *
 * Ideas are kept on the organization now (see the API's /ideas routes), so this
 * page is the standing shortlist of what to make next: generate a batch, keep
 * the ones worth doing, dismiss the rest so they are not proposed back, and
 * send a kept idea into the Studio when it is time to write it.
 *
 * Dismissing is not deleting. A dismissed idea is remembered precisely so the
 * generator's next batch can be recognised as the same suggestion coming round
 * again; deleting is offered separately for the rarer "that should never have
 * been recorded".
 */
class EngageAI_Admin_Ideas
{
    private static ?EngageAI_Admin_Ideas $instance = null;
    private EngageAI_Api_Client $client;

    /** Ideas generated but not yet kept, held only until the page renders them. */
    private const PENDING_TRANSIENT = 'engageai_ideas_pending_';

    public static function instance(): EngageAI_Admin_Ideas
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
        add_action('admin_post_engageai_ideas_generate', [$this, 'handle_generate']);
        add_action('admin_post_engageai_ideas_keep', [$this, 'handle_keep']);
        add_action('admin_post_engageai_ideas_add', [$this, 'handle_add']);
        add_action('admin_post_engageai_ideas_status', [$this, 'handle_status']);
        add_action('admin_post_engageai_ideas_delete', [$this, 'handle_delete']);
    }

    private function guard(string $action): int
    {
        if (!current_user_can('manage_options') || !check_admin_referer($action)) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = (int) $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        return $org_id;
    }

    /**
     * Asks the Studio's own idea generator for a batch. Nothing is stored yet -
     * the batch sits in a transient until the operator says which ones are
     * worth keeping, so a generation run the operator doesn't like leaves no
     * trace in the cache.
     */
    public function handle_generate(): void
    {
        $org_id = $this->guard('engageai_ideas_generate');

        $goal = sanitize_key($_POST['goal'] ?? '');
        $notes = sanitize_textarea_field(wp_unslash($_POST['notes'] ?? ''));
        $count = max(1, min(8, (int) ($_POST['count'] ?? 5)));

        $result = $this->client->studio_ideas($org_id, $goal, $notes, $count);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }

        $ideas = isset($result['ideas']) && is_array($result['ideas']) ? $result['ideas'] : (array) $result;
        set_transient(self::PENDING_TRANSIENT . get_current_user_id(), [
            'goal' => $goal,
            'ideas' => $ideas,
        ], 30 * MINUTE_IN_SECONDS);

        $this->redirect(['generated' => count($ideas)]);
    }

    /** Keeps the ticked ideas from the pending batch. */
    public function handle_keep(): void
    {
        $org_id = $this->guard('engageai_ideas_keep');

        $pending = get_transient(self::PENDING_TRANSIENT . get_current_user_id());
        $batch = is_array($pending) && !empty($pending['ideas']) ? $pending['ideas'] : [];
        $goal = is_array($pending) ? (string) ($pending['goal'] ?? '') : '';
        $chosen = array_map('intval', (array) ($_POST['keep'] ?? []));

        $payload = [];
        foreach ($chosen as $index) {
            if (!isset($batch[$index]) || !is_array($batch[$index])) {
                continue;
            }
            $idea = $batch[$index];
            $payload[] = [
                'title' => sanitize_text_field((string) ($idea['title'] ?? $idea['headline'] ?? '')),
                'angle' => sanitize_textarea_field((string) ($idea['angle'] ?? '')),
                'rationale' => sanitize_textarea_field((string) ($idea['why'] ?? $idea['rationale'] ?? '')),
                'goal' => $goal !== '' ? $goal : null,
                'channel' => sanitize_key((string) ($idea['channel'] ?? '')) ?: null,
                'source' => 'ai',
            ];
        }

        if (empty($payload)) {
            $this->redirect(['error' => rawurlencode(__('Tick at least one idea to keep.', 'engage-ai'))]);
        }

        $result = $this->client->keep_ideas($org_id, $payload);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }

        delete_transient(self::PENDING_TRANSIENT . get_current_user_id());
        // The API skips titles already cached, so "kept 3" and "kept 1 of 3"
        // are different outcomes and the operator should see which happened.
        $this->redirect(['kept' => is_array($result) ? count($result) : 0, 'asked' => count($payload)]);
    }

    /** An idea the operator had themselves, typed straight in. */
    public function handle_add(): void
    {
        $org_id = $this->guard('engageai_ideas_add');

        $title = sanitize_text_field(wp_unslash($_POST['title'] ?? ''));
        if ($title === '') {
            $this->redirect(['error' => rawurlencode(__('An idea needs a title.', 'engage-ai'))]);
        }

        $result = $this->client->keep_ideas($org_id, [[
            'title' => $title,
            'angle' => sanitize_textarea_field(wp_unslash($_POST['angle'] ?? '')),
            'channel' => sanitize_key($_POST['channel'] ?? '') ?: null,
            'source' => 'operator',
        ]]);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }

        $this->redirect(['kept' => is_array($result) ? count($result) : 0, 'asked' => 1]);
    }

    public function handle_status(): void
    {
        $org_id = $this->guard('engageai_ideas_status');
        $idea_id = (int) ($_POST['idea_id'] ?? 0);
        $status = sanitize_key($_POST['status'] ?? '');

        if (!$idea_id || !in_array($status, ['kept', 'drafted', 'dismissed'], true)) {
            $this->redirect(['error' => rawurlencode(__('That is not something an idea can be.', 'engage-ai'))]);
        }

        $result = $this->client->update_idea($org_id, $idea_id, ['status' => $status]);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['moved' => $status]);
    }

    public function handle_delete(): void
    {
        $org_id = $this->guard('engageai_ideas_delete');
        $idea_id = (int) ($_POST['idea_id'] ?? 0);
        if (!$idea_id) {
            $this->redirect(['error' => 'not_ready']);
        }

        $result = $this->client->delete_idea($org_id, $idea_id);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['deleted' => 1]);
    }

    private function redirect(array $args): void
    {
        wp_safe_redirect(add_query_arg(array_merge(['page' => 'engageai-ideas'], $args), admin_url('admin.php')));
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
        $view = sanitize_key($_GET['view'] ?? 'kept');
        if (!in_array($view, ['kept', 'drafted', 'dismissed'], true)) {
            $view = 'kept';
        }

        $ideas = $this->client->get_ideas($org_id, $view);
        $error = is_wp_error($ideas) ? $ideas->get_error_message() : '';
        $ideas = is_wp_error($ideas) ? [] : (array) $ideas;

        $pending = get_transient(self::PENDING_TRANSIENT . get_current_user_id());
        $pending_ideas = is_array($pending) && !empty($pending['ideas']) ? (array) $pending['ideas'] : [];
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Ideas', 'engage-ai'); ?></h1>
            <?php $this->render_notice(); ?>
            <?php if ($error !== ''): ?>
                <div class="notice notice-error"><p><?php echo esc_html($error); ?></p></div>
            <?php endif; ?>

            <p class="description" style="max-width:46em;">
                <?php esc_html_e('What to make next, before it is anything in particular. Keep the ideas worth doing, dismiss the ones that are not — a dismissed idea is remembered, so it is not suggested back to you next week — and send a kept idea to the Content Studio when it is time to write it.', 'engage-ai'); ?>
            </p>

            <?php $this->render_generator(); ?>
            <?php if (!empty($pending_ideas)): ?>
                <?php $this->render_pending($pending_ideas); ?>
            <?php endif; ?>

            <h2 style="margin-top:2rem;"><?php esc_html_e('Your ideas', 'engage-ai'); ?></h2>
            <?php $this->render_view_tabs($view); ?>
            <?php $this->render_list($ideas, $view); ?>
            <?php $this->render_add_form(); ?>
        </div>
        <?php
    }

    private function render_generator(): void
    {
        $goals = $this->goal_choices();
        ?>
        <div class="engageai-card" style="border:1px solid #dcdcde;background:#fff;padding:1rem 1.25rem;margin:1.25rem 0;max-width:46em;">
            <h2 style="margin-top:0;"><?php esc_html_e('Ask for ideas', 'engage-ai'); ?></h2>
            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                <input type="hidden" name="action" value="engageai_ideas_generate">
                <?php wp_nonce_field('engageai_ideas_generate'); ?>
                <p>
                    <label for="engageai-idea-goal"><strong><?php esc_html_e('What should these do for the business?', 'engage-ai'); ?></strong></label><br>
                    <select id="engageai-idea-goal" name="goal" style="min-width:22em;">
                        <?php foreach ($goals as $key => $label): ?>
                            <option value="<?php echo esc_attr($key); ?>"><?php echo esc_html($label); ?></option>
                        <?php endforeach; ?>
                    </select>
                </p>
                <p>
                    <label for="engageai-idea-notes"><strong><?php esc_html_e('Anything specific going on?', 'engage-ai'); ?></strong></label><br>
                    <textarea id="engageai-idea-notes" name="notes" rows="2" class="large-text" placeholder="<?php esc_attr_e('e.g. the autumn open evening, we have three slots left', 'engage-ai'); ?>"></textarea>
                </p>
                <p>
                    <label for="engageai-idea-count"><?php esc_html_e('How many', 'engage-ai'); ?></label>
                    <select id="engageai-idea-count" name="count">
                        <?php foreach ([3, 5, 8] as $n): ?>
                            <option value="<?php echo esc_attr((string) $n); ?>" <?php selected($n, 5); ?>><?php echo esc_html((string) $n); ?></option>
                        <?php endforeach; ?>
                    </select>
                    <button type="submit" class="button button-primary" style="margin-left:.5rem;"><?php esc_html_e('Suggest ideas', 'engage-ai'); ?></button>
                </p>
                <p class="description"><?php esc_html_e('Nothing is saved until you keep it.', 'engage-ai'); ?></p>
            </form>
        </div>
        <?php
    }

    private function render_pending(array $ideas): void
    {
        ?>
        <div class="engageai-card" style="border:1px solid #2a78d6;background:#f6fafe;padding:1rem 1.25rem;margin:1.25rem 0;max-width:46em;">
            <h2 style="margin-top:0;"><?php esc_html_e('Suggested — keep the ones worth doing', 'engage-ai'); ?></h2>
            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                <input type="hidden" name="action" value="engageai_ideas_keep">
                <?php wp_nonce_field('engageai_ideas_keep'); ?>
                <?php foreach ($ideas as $index => $idea): ?>
                    <?php if (!is_array($idea)) { continue; } ?>
                    <p style="margin:.75rem 0;">
                        <label>
                            <input type="checkbox" name="keep[]" value="<?php echo esc_attr((string) $index); ?>">
                            <strong><?php echo esc_html((string) ($idea['title'] ?? $idea['headline'] ?? '')); ?></strong>
                        </label>
                        <?php if (!empty($idea['angle'])): ?>
                            <br><span class="description" style="margin-left:1.75rem;"><?php echo esc_html((string) $idea['angle']); ?></span>
                        <?php endif; ?>
                        <?php if (!empty($idea['why']) || !empty($idea['rationale'])): ?>
                            <br><span class="description" style="margin-left:1.75rem;"><em><?php echo esc_html((string) ($idea['why'] ?? $idea['rationale'])); ?></em></span>
                        <?php endif; ?>
                    </p>
                <?php endforeach; ?>
                <button type="submit" class="button button-primary"><?php esc_html_e('Keep selected', 'engage-ai'); ?></button>
            </form>
        </div>
        <?php
    }

    private function render_view_tabs(string $current): void
    {
        $tabs = [
            'kept' => __('Kept', 'engage-ai'),
            'drafted' => __('Written', 'engage-ai'),
            'dismissed' => __('Dismissed', 'engage-ai'),
        ];
        ?>
        <nav class="nav-tab-wrapper" style="margin-bottom:1rem;">
            <?php foreach ($tabs as $key => $label): ?>
                <a class="nav-tab<?php echo $key === $current ? ' nav-tab-active' : ''; ?>"
                   href="<?php echo esc_url(admin_url('admin.php?page=engageai-ideas&view=' . $key)); ?>"><?php echo esc_html($label); ?></a>
            <?php endforeach; ?>
        </nav>
        <?php
    }

    private function render_list(array $ideas, string $view): void
    {
        if (empty($ideas)) {
            $empty = [
                'kept' => __('Nothing kept yet. Ask for some ideas above, or add one of your own.', 'engage-ai'),
                'drafted' => __('Nothing written from an idea yet. Send a kept idea to the Content Studio and it moves here.', 'engage-ai'),
                'dismissed' => __('Nothing dismissed. Ideas you turn down are kept here so they are not suggested back to you.', 'engage-ai'),
            ];
            echo '<p>' . esc_html($empty[$view] ?? '') . '</p>';
            return;
        }
        ?>
        <table class="widefat striped">
            <thead>
                <tr>
                    <th><?php esc_html_e('Idea', 'engage-ai'); ?></th>
                    <th style="width:12%;"><?php esc_html_e('Goal', 'engage-ai'); ?></th>
                    <th style="width:12%;"><?php esc_html_e('Channel', 'engage-ai'); ?></th>
                    <th style="width:28%;"><?php esc_html_e('Action', 'engage-ai'); ?></th>
                </tr>
            </thead>
            <tbody>
                <?php foreach ($ideas as $idea): ?>
                    <?php
                    if (!is_array($idea)) {
                        continue;
                    }
                    $id = (int) ($idea['id'] ?? 0);
                    $content_id = (int) ($idea['content_item_id'] ?? 0);
                    ?>
                    <tr>
                        <td>
                            <strong><?php echo esc_html((string) ($idea['title'] ?? '')); ?></strong>
                            <?php if (!empty($idea['angle'])): ?>
                                <div class="description"><?php echo esc_html((string) $idea['angle']); ?></div>
                            <?php endif; ?>
                            <?php if (!empty($idea['rationale'])): ?>
                                <div class="description"><em><?php echo esc_html((string) $idea['rationale']); ?></em></div>
                            <?php endif; ?>
                            <?php if (($idea['source'] ?? '') === 'operator'): ?>
                                <span class="description">· <?php esc_html_e('yours', 'engage-ai'); ?></span>
                            <?php endif; ?>
                        </td>
                        <td><span class="description"><?php echo esc_html($this->goal_label((string) ($idea['goal'] ?? ''))); ?></span></td>
                        <td><span class="description"><?php echo esc_html((string) ($idea['channel'] ?? '—')); ?></span></td>
                        <td>
                            <?php if ($view === 'kept'): ?>
                                <a class="button button-primary" href="<?php echo esc_url($this->studio_url($idea)); ?>"><?php esc_html_e('Write it', 'engage-ai'); ?></a>
                                <?php $this->status_button(__('Dismiss', 'engage-ai'), $id, 'dismissed'); ?>
                            <?php elseif ($view === 'dismissed'): ?>
                                <?php $this->status_button(__('Bring back', 'engage-ai'), $id, 'kept'); ?>
                                <?php $this->delete_button($id); ?>
                            <?php else: ?>
                                <?php if ($content_id): ?>
                                    <a class="button" href="<?php echo esc_url(add_query_arg(
                                        ['page' => 'engageai-studio', 'step' => 'draft', 'content_id' => $content_id],
                                        admin_url('admin.php')
                                    )); ?>"><?php esc_html_e('Open the piece', 'engage-ai'); ?></a>
                                <?php else: ?>
                                    <span class="description"><?php esc_html_e('Written', 'engage-ai'); ?></span>
                                <?php endif; ?>
                            <?php endif; ?>
                        </td>
                    </tr>
                <?php endforeach; ?>
            </tbody>
        </table>
        <?php
    }

    /**
     * "Write it" hands the idea to the Studio as a starting point. The Studio
     * owns drafting; this only carries the idea across, so there is one place
     * a piece can be written rather than two.
     */
    private function studio_url(array $idea): string
    {
        return add_query_arg([
            'page' => 'engageai-studio',
            'step' => 'idea',
            'idea_id' => (int) ($idea['id'] ?? 0),
            'idea_title' => rawurlencode((string) ($idea['title'] ?? '')),
        ], admin_url('admin.php'));
    }

    private function status_button(string $label, int $idea_id, string $status): void
    {
        ?>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="display:inline;">
            <input type="hidden" name="action" value="engageai_ideas_status">
            <input type="hidden" name="idea_id" value="<?php echo esc_attr((string) $idea_id); ?>">
            <input type="hidden" name="status" value="<?php echo esc_attr($status); ?>">
            <?php wp_nonce_field('engageai_ideas_status'); ?>
            <button type="submit" class="button"><?php echo esc_html($label); ?></button>
        </form>
        <?php
    }

    private function delete_button(int $idea_id): void
    {
        ?>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="display:inline;"
              onsubmit="return confirm('<?php echo esc_js(__('Delete this idea for good?', 'engage-ai')); ?>');">
            <input type="hidden" name="action" value="engageai_ideas_delete">
            <input type="hidden" name="idea_id" value="<?php echo esc_attr((string) $idea_id); ?>">
            <?php wp_nonce_field('engageai_ideas_delete'); ?>
            <button type="submit" class="button button-link-delete"><?php esc_html_e('Delete', 'engage-ai'); ?></button>
        </form>
        <?php
    }

    private function render_add_form(): void
    {
        ?>
        <h2 style="margin-top:2rem;"><?php esc_html_e('Add your own', 'engage-ai'); ?></h2>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="max-width:46em;">
            <input type="hidden" name="action" value="engageai_ideas_add">
            <?php wp_nonce_field('engageai_ideas_add'); ?>
            <p>
                <input type="text" name="title" class="large-text" placeholder="<?php esc_attr_e('The idea, in a line', 'engage-ai'); ?>" required>
            </p>
            <p>
                <textarea name="angle" rows="2" class="large-text" placeholder="<?php esc_attr_e('The angle — how you would tell it (optional)', 'engage-ai'); ?>"></textarea>
            </p>
            <button type="submit" class="button"><?php esc_html_e('Keep it', 'engage-ai'); ?></button>
        </form>
        <?php
    }

    /**
     * Goals come from the Studio's own catalog so the two pages cannot drift
     * apart. If the API can't be reached the generator still works - it just
     * offers the one safe default rather than an empty select.
     */
    private function goal_choices(): array
    {
        $catalog = $this->client->get_studio_catalog();
        if (is_wp_error($catalog) || empty($catalog['goals']) || !is_array($catalog['goals'])) {
            return ['awareness' => __('Get noticed', 'engage-ai')];
        }
        $out = [];
        foreach ($catalog['goals'] as $goal) {
            if (is_array($goal) && !empty($goal['key'])) {
                $out[(string) $goal['key']] = (string) ($goal['label'] ?? $goal['key']);
            }
        }
        return $out ?: ['awareness' => __('Get noticed', 'engage-ai')];
    }

    private function goal_label(string $key): string
    {
        if ($key === '') {
            return '—';
        }
        $choices = $this->goal_choices();
        return $choices[$key] ?? $key;
    }

    private function render_notice(): void
    {
        if (isset($_GET['generated'])) {
            printf('<div class="notice notice-info is-dismissible"><p>%s</p></div>', esc_html(sprintf(
                /* translators: %d: number of ideas suggested */
                _n('%d idea suggested. Keep the ones worth doing.', '%d ideas suggested. Keep the ones worth doing.', (int) $_GET['generated'], 'engage-ai'),
                (int) $_GET['generated']
            )));
        } elseif (isset($_GET['kept'])) {
            $kept = (int) $_GET['kept'];
            $asked = (int) ($_GET['asked'] ?? $kept);
            $message = $kept === $asked
                ? sprintf(_n('Kept %d idea.', 'Kept %d ideas.', $kept, 'engage-ai'), $kept)
                // The API skips titles already in the cache, so say so rather
                // than reporting a number the operator didn't ask for.
                : sprintf(__('Kept %1$d of %2$d — the rest were already on your list.', 'engage-ai'), $kept, $asked);
            printf('<div class="notice notice-success is-dismissible"><p>%s</p></div>', esc_html($message));
        } elseif (isset($_GET['moved'])) {
            printf('<div class="notice notice-success is-dismissible"><p>%s</p></div>', esc_html(
                $_GET['moved'] === 'dismissed'
                    ? __('Dismissed. It stays on the Dismissed tab so it is not suggested back to you.', 'engage-ai')
                    : __('Moved back to your kept ideas.', 'engage-ai')
            ));
        } elseif (isset($_GET['deleted'])) {
            printf('<div class="notice notice-success is-dismissible"><p>%s</p></div>', esc_html__('Idea deleted.', 'engage-ai'));
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
            <h1><?php esc_html_e('Ideas', 'engage-ai'); ?></h1>
            <div class="notice notice-warning"><p>
                <?php esc_html_e('Connect your Engage AI account and select an organization on the Settings page first.', 'engage-ai'); ?>
            </p></div>
        </div>
        <?php
    }
}
