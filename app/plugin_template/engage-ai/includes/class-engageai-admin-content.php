<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * The Content page: a log of everything Engage AI has generated for this site,
 * plus a one-click "Suggest content" that asks the API to draft a few website
 * posts tailored to this site's type (church / ecommerce / business). Each
 * suggestion can be turned into a WordPress draft to review and publish.
 */
class EngageAI_Admin_Content
{
    private static ?EngageAI_Admin_Content $instance = null;
    private EngageAI_Api_Client $client;
    private EngageAI_Post_Publisher $publisher;

    public static function instance(): EngageAI_Admin_Content
    {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct()
    {
        $this->client = new EngageAI_Api_Client();
        $this->publisher = new EngageAI_Post_Publisher();
    }

    public function register_hooks(): void
    {
        add_action('admin_post_engageai_suggest_content', [$this, 'handle_suggest']);
        add_action('admin_post_engageai_draft_content', [$this, 'handle_draft']);
    }

    public function handle_suggest(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_suggest_content')) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $count = max(1, min(6, (int) ($_POST['count'] ?? 3)));
        $result = $this->client->suggest_content((int) $org_id, $count);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['suggested' => is_array($result) ? count($result) : 0]);
    }

    public function handle_draft(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_draft_content')) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = $this->client->get_organization_id();
        $content_id = (int) ($_POST['content_id'] ?? 0);
        if (!$org_id || !$content_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $items = $this->client->get_content((int) $org_id);
        $item = null;
        if (!is_wp_error($items)) {
            foreach ($items as $candidate) {
                if ((int) ($candidate['id'] ?? 0) === $content_id) {
                    $item = $candidate;
                    break;
                }
            }
        }
        if (!$item) {
            $this->redirect(['error' => rawurlencode(__('Could not find that content item.', 'engage-ai'))]);
        }
        $post_id = $this->publisher->publish(
            $item['output_payload'] ?? [],
            (string) ($item['content_type'] ?? 'website_post'),
            (string) ($item['title'] ?? __('Engage AI post', 'engage-ai')),
            'draft'
        );
        if (is_wp_error($post_id)) {
            $this->redirect(['error' => rawurlencode($post_id->get_error_message())]);
        }
        $this->redirect(['drafted' => (int) $post_id]);
    }

    private function redirect(array $args): void
    {
        wp_safe_redirect(add_query_arg(array_merge(['page' => 'engageai-content'], $args), admin_url('admin.php')));
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
        $site_type = class_exists('EngageAI_Plugin') ? EngageAI_Plugin::detect_site_type() : 'business';
        $items = $this->client->get_content($org_id);
        $items = is_wp_error($items) ? [] : $items;
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Content', 'engage-ai'); ?></h1>
            <?php $this->render_notice(); ?>

            <p class="description">
                <?php
                printf(
                    /* translators: %s: detected site type, e.g. "church" */
                    esc_html__('Suggestions are tailored to your site type: %s. Each draft is saved here and can be turned into a WordPress draft to review before publishing.', 'engage-ai'),
                    '<strong>' . esc_html($site_type) . '</strong>'
                );
                ?>
            </p>

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="margin:16px 0;">
                <input type="hidden" name="action" value="engageai_suggest_content">
                <?php wp_nonce_field('engageai_suggest_content'); ?>
                <label for="engageai-count"><?php esc_html_e('How many ideas', 'engage-ai'); ?></label>
                <select id="engageai-count" name="count">
                    <?php foreach ([2, 3, 4, 5] as $n): ?>
                        <option value="<?php echo esc_attr((string) $n); ?>" <?php selected($n, 3); ?>><?php echo esc_html((string) $n); ?></option>
                    <?php endforeach; ?>
                </select>
                <button type="submit" class="button button-primary"><?php esc_html_e('Suggest content', 'engage-ai'); ?></button>
            </form>

            <h2><?php esc_html_e('Generated content', 'engage-ai'); ?></h2>
            <?php if (empty($items)): ?>
                <p><?php esc_html_e('Nothing yet. Use "Suggest content" above to draft your first posts.', 'engage-ai'); ?></p>
            <?php else: ?>
                <table class="widefat striped">
                    <thead>
                        <tr>
                            <th><?php esc_html_e('Title', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Type', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Why', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Action', 'engage-ai'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php foreach ($items as $item): ?>
                            <?php
                            $angle = $item['output_payload']['angle'] ?? ($item['input_payload']['angle'] ?? '');
                            $is_website_post = !empty($item['output_payload']['website_post']['body_html']);
                            ?>
                            <tr>
                                <td><strong><?php echo esc_html($item['title'] ?? ''); ?></strong></td>
                                <td><?php echo esc_html(str_replace('_', ' ', (string) ($item['content_type'] ?? ''))); ?></td>
                                <td class="description"><?php echo esc_html($angle); ?></td>
                                <td>
                                    <?php if ($is_website_post): ?>
                                        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                                            <input type="hidden" name="action" value="engageai_draft_content">
                                            <input type="hidden" name="content_id" value="<?php echo esc_attr((string) ($item['id'] ?? '')); ?>">
                                            <?php wp_nonce_field('engageai_draft_content'); ?>
                                            <button type="submit" class="button"><?php esc_html_e('Create WordPress draft', 'engage-ai'); ?></button>
                                        </form>
                                    <?php else: ?>
                                        <span class="description">&mdash;</span>
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

    private function render_notice(): void
    {
        if (isset($_GET['suggested'])) {
            printf(
                '<div class="notice notice-success is-dismissible"><p>%s</p></div>',
                esc_html(sprintf(
                    /* translators: %d: number of drafts generated */
                    _n('Generated %d new draft below.', 'Generated %d new drafts below.', (int) $_GET['suggested'], 'engage-ai'),
                    (int) $_GET['suggested']
                ))
            );
        } elseif (isset($_GET['drafted'])) {
            $edit = get_edit_post_link((int) $_GET['drafted'], '');
            printf(
                '<div class="notice notice-success is-dismissible"><p>%s <a href="%s">%s</a></p></div>',
                esc_html__('Created a WordPress draft.', 'engage-ai'),
                esc_url($edit ?: admin_url('edit.php?post_status=draft&post_type=post')),
                esc_html__('Review it →', 'engage-ai')
            );
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
            <h1><?php esc_html_e('Content', 'engage-ai'); ?></h1>
            <div class="notice notice-warning"><p>
                <?php esc_html_e('Connect your Engage AI account and select an organization on the Settings page first.', 'engage-ai'); ?>
            </p></div>
        </div>
        <?php
    }
}
