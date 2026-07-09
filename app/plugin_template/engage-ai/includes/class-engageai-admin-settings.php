<?php

if (!defined('ABSPATH')) {
    exit;
}

class EngageAI_Admin_Settings
{
    private static ?EngageAI_Admin_Settings $instance = null;
    private EngageAI_Api_Client $client;

    public static function instance(): EngageAI_Admin_Settings
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

    /**
     * Every activatable module: "engagement" (the church content generators
     * below) plus one "agent:<niche>" per Claude AI side hustle. Shared with
     * EngageAI_Admin_Agents, which only shows tabs for niches present here
     * and active on the org.
     */
    public static function available_modules(): array
    {
        return [
            'engagement' => __('Church Engagement Content (events, announcements, sermons)', 'engage-ai'),
            'analytics' => __('Analytics (web-search digital footprint scans)', 'engage-ai'),
            'agent:physical_product' => __('Physical Product Business', 'engage-ai'),
            'agent:reselling' => __('Reselling & Thrift Flipping', 'engage-ai'),
            'agent:youtube_channel' => __('YouTube Channel Growth', 'engage-ai'),
            'agent:answer_man' => __('Paid Q&A ("Answer Man")', 'engage-ai'),
            'agent:local_service' => __('Local Service Business', 'engage-ai'),
            'agent:app_builder' => __('Build a Simple App', 'engage-ai'),
            'agent:ugc_creator' => __('UGC Content Creation', 'engage-ai'),
            'agent:coaching' => __('Coaching From Experience', 'engage-ai'),
            'agent:engagement_growth' => __('Engagement Growth (next-best-action from Analytics)', 'engage-ai'),
        ];
    }

    public function register_hooks(): void
    {
        add_action('admin_init', [$this, 'register_settings']);
        add_action('admin_post_engageai_connect', [$this, 'handle_connect']);
        add_action('admin_post_engageai_disconnect', [$this, 'handle_disconnect']);
        add_action('admin_post_engageai_create_organization', [$this, 'handle_create_organization']);
        add_action('admin_post_engageai_select_organization', [$this, 'handle_select_organization']);
        add_action('admin_post_engageai_update_organization', [$this, 'handle_update_organization']);
        add_action('admin_post_engageai_update_targets', [$this, 'handle_update_targets']);
        add_action('admin_post_engageai_update_modules', [$this, 'handle_update_modules']);
    }

    public function register_settings(): void
    {
        register_setting('engageai_settings', 'engageai_api_base_url', [
            'sanitize_callback' => 'esc_url_raw',
            'default' => '',
        ]);
        register_setting('engageai_settings', 'engageai_default_publish_status', [
            'sanitize_callback' => [$this, 'sanitize_publish_status'],
            'default' => 'draft',
        ]);
    }

    public function sanitize_publish_status($value): string
    {
        return in_array($value, ['draft', 'pending', 'publish'], true) ? $value : 'draft';
    }

    public function handle_connect(): void
    {
        $this->verify_request('engageai_connect');

        $email = sanitize_email($_POST['engageai_email'] ?? '');
        $password = (string) ($_POST['engageai_password'] ?? '');

        if ($email === '' || $password === '') {
            $this->redirect_with_notice('error', __('Email and password are required to connect.', 'engage-ai'));
        }

        $result = $this->client->login($email, $password);

        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        $this->redirect_with_notice('success', __('Connected to Engage AI.', 'engage-ai'));
    }

    public function handle_disconnect(): void
    {
        $this->verify_request('engageai_disconnect');
        $this->client->disconnect();
        $this->redirect_with_notice('success', __('Disconnected from Engage AI.', 'engage-ai'));
    }

    public function handle_create_organization(): void
    {
        $this->verify_request('engageai_create_organization');

        $name = sanitize_text_field($_POST['engageai_org_name'] ?? '');
        if ($name === '') {
            $this->redirect_with_notice('error', __('Organization name is required.', 'engage-ai'));
        }

        $result = $this->client->create_organization([
            'name' => $name,
            'org_type' => 'church',
            'mission' => sanitize_textarea_field($_POST['engageai_org_mission'] ?? ''),
            'tone' => sanitize_text_field($_POST['engageai_org_tone'] ?? 'warm, clear, inviting, faith-centered'),
            'audience' => sanitize_text_field($_POST['engageai_org_audience'] ?? ''),
            'website_url' => esc_url_raw($_POST['engageai_org_website_url'] ?? ''),
        ]);

        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        if (!empty($result['id'])) {
            $this->client->set_organization_id((int) $result['id']);
        }

        $this->redirect_with_notice('success', __('Organization created and selected.', 'engage-ai'));
    }

    public function handle_select_organization(): void
    {
        $this->verify_request('engageai_select_organization');

        $org_id = (int) ($_POST['engageai_org_id'] ?? 0);
        if ($org_id <= 0) {
            $this->redirect_with_notice('error', __('Choose a valid organization.', 'engage-ai'));
        }

        $this->client->set_organization_id($org_id);
        $this->redirect_with_notice('success', __('Organization selected.', 'engage-ai'));
    }

    public function handle_update_organization(): void
    {
        $this->verify_request('engageai_update_organization');

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect_with_notice('error', __('Select an organization first.', 'engage-ai'));
        }

        $result = $this->client->update_organization($org_id, [
            'website_url' => esc_url_raw($_POST['engageai_org_website_url'] ?? ''),
            'mission' => sanitize_textarea_field($_POST['engageai_org_mission'] ?? ''),
            'audience' => sanitize_text_field($_POST['engageai_org_audience'] ?? ''),
        ]);

        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        $this->redirect_with_notice('success', __('Organization details updated.', 'engage-ai'));
    }

    public function handle_update_targets(): void
    {
        $this->verify_request('engageai_update_targets');

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect_with_notice('error', __('Select an organization first.', 'engage-ai'));
        }

        $target_org_score = $_POST['engageai_target_org_score'] ?? '';
        $target_channel_scores = [];
        foreach (array_keys(EngageAI_Admin_Analytics::channels()) as $channel) {
            $field = 'engageai_target_' . $channel;
            if (isset($_POST[$field]) && $_POST[$field] !== '') {
                $target_channel_scores[$channel] = max(0, min(100, (int) $_POST[$field]));
            }
        }

        $result = $this->client->update_organization($org_id, [
            'target_org_score' => $target_org_score !== '' ? max(0, min(100, (int) $target_org_score)) : null,
            'target_channel_scores' => !empty($target_channel_scores) ? $target_channel_scores : null,
        ]);

        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        $this->redirect_with_notice('success', __('Targets updated - the Engagement Growth agent will use these on its next cycle.', 'engage-ai'));
    }

    public function handle_update_modules(): void
    {
        $this->verify_request('engageai_update_modules');

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect_with_notice('error', __('Select an organization first.', 'engage-ai'));
        }

        $submitted = array_map('sanitize_text_field', (array) ($_POST['engageai_modules'] ?? []));
        $valid = array_keys(self::available_modules());
        $enabled = array_values(array_intersect($submitted, $valid));

        $result = $this->client->update_modules($org_id, $enabled);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        $this->redirect_with_notice('success', __('Modules updated.', 'engage-ai'));
    }

    public function render_page(): void
    {
        if (!current_user_can('manage_options')) {
            return;
        }

        $connected = $this->client->is_connected();
        $expiry = $this->client->token_expiry();
        $organizations = [];
        $org_error = null;

        if ($connected) {
            $result = $this->client->get_organizations();
            if (is_wp_error($result)) {
                $org_error = $result->get_error_message();
            } else {
                $organizations = $result;
            }
        }

        $selected_org_id = $this->client->get_organization_id();
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Engage AI Settings', 'engage-ai'); ?></h1>
            <?php $this->render_notice(); ?>

            <h2><?php esc_html_e('1. API connection', 'engage-ai'); ?></h2>
            <form method="post" action="options.php">
                <?php settings_fields('engageai_settings'); ?>
                <table class="form-table">
                    <tr>
                        <th><label for="engageai_api_base_url"><?php esc_html_e('Engage AI API URL', 'engage-ai'); ?></label></th>
                        <td>
                            <input type="url" id="engageai_api_base_url" name="engageai_api_base_url"
                                   value="<?php echo esc_attr($this->client->get_base_url()); ?>"
                                   class="regular-text" placeholder="https://api.yourdomain.com" required>
                            <p class="description"><?php esc_html_e('Base URL of your deployed Engage AI Cloud API, no trailing slash.', 'engage-ai'); ?></p>
                        </td>
                    </tr>
                    <tr>
                        <th><label for="engageai_default_publish_status"><?php esc_html_e('Default publish status', 'engage-ai'); ?></label></th>
                        <td>
                            <select id="engageai_default_publish_status" name="engageai_default_publish_status">
                                <?php $current_status = get_option('engageai_default_publish_status', 'draft'); ?>
                                <option value="draft" <?php selected($current_status, 'draft'); ?>><?php esc_html_e('Draft (review before publishing)', 'engage-ai'); ?></option>
                                <option value="pending" <?php selected($current_status, 'pending'); ?>><?php esc_html_e('Pending review', 'engage-ai'); ?></option>
                                <option value="publish" <?php selected($current_status, 'publish'); ?>><?php esc_html_e('Publish immediately', 'engage-ai'); ?></option>
                            </select>
                            <p class="description"><?php esc_html_e('Draft is recommended so a person reviews AI-generated content before it goes live.', 'engage-ai'); ?></p>
                        </td>
                    </tr>
                </table>
                <?php submit_button(__('Save API settings', 'engage-ai')); ?>
            </form>

            <hr>

            <h2><?php esc_html_e('2. Connect your account', 'engage-ai'); ?></h2>
            <?php if ($connected): ?>
                <p>
                    <strong><?php esc_html_e('Status:', 'engage-ai'); ?></strong>
                    <?php esc_html_e('Connected.', 'engage-ai'); ?>
                    <?php if ($expiry): ?>
                        <?php
                        printf(
                            /* translators: %s: human-readable date/time */
                            esc_html__('Session expires %s.', 'engage-ai'),
                            esc_html(date_i18n(get_option('date_format') . ' ' . get_option('time_format'), $expiry))
                        );
                        ?>
                    <?php endif; ?>
                </p>
                <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                    <input type="hidden" name="action" value="engageai_disconnect">
                    <?php wp_nonce_field('engageai_disconnect'); ?>
                    <?php submit_button(__('Disconnect', 'engage-ai'), 'secondary'); ?>
                </form>
            <?php else: ?>
                <p><?php esc_html_e('Enter the email/password you registered with on your Engage AI account.', 'engage-ai'); ?></p>
                <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                    <input type="hidden" name="action" value="engageai_connect">
                    <?php wp_nonce_field('engageai_connect'); ?>
                    <table class="form-table">
                        <tr>
                            <th><label for="engageai_email"><?php esc_html_e('Email', 'engage-ai'); ?></label></th>
                            <td><input type="email" id="engageai_email" name="engageai_email" class="regular-text" required></td>
                        </tr>
                        <tr>
                            <th><label for="engageai_password"><?php esc_html_e('Password', 'engage-ai'); ?></label></th>
                            <td><input type="password" id="engageai_password" name="engageai_password" class="regular-text" required></td>
                        </tr>
                    </table>
                    <p class="description"><?php esc_html_e('Your password is used once to connect and is not stored — only the resulting session token is saved.', 'engage-ai'); ?></p>
                    <?php submit_button(__('Connect', 'engage-ai')); ?>
                </form>
            <?php endif; ?>

            <?php if ($connected): ?>
                <hr>
                <h2><?php esc_html_e('3. Organization', 'engage-ai'); ?></h2>
                <?php if ($org_error): ?>
                    <div class="notice notice-error"><p><?php echo esc_html($org_error); ?></p></div>
                <?php endif; ?>

                <?php if (!empty($organizations)): ?>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <input type="hidden" name="action" value="engageai_select_organization">
                        <?php wp_nonce_field('engageai_select_organization'); ?>
                        <table class="form-table">
                            <tr>
                                <th><label for="engageai_org_id"><?php esc_html_e('Active organization', 'engage-ai'); ?></label></th>
                                <td>
                                    <select id="engageai_org_id" name="engageai_org_id">
                                        <?php foreach ($organizations as $org): ?>
                                            <option value="<?php echo esc_attr($org['id']); ?>" <?php selected($selected_org_id, $org['id']); ?>>
                                                <?php echo esc_html($org['name']); ?>
                                            </option>
                                        <?php endforeach; ?>
                                    </select>
                                </td>
                            </tr>
                        </table>
                        <?php submit_button(__('Use this organization', 'engage-ai')); ?>
                    </form>
                <?php else: ?>
                    <p><?php esc_html_e('No organization yet — create one below.', 'engage-ai'); ?></p>
                <?php endif; ?>

                <h3><?php esc_html_e('Create a new organization', 'engage-ai'); ?></h3>
                <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                    <input type="hidden" name="action" value="engageai_create_organization">
                    <?php wp_nonce_field('engageai_create_organization'); ?>
                    <table class="form-table">
                        <tr>
                            <th><label for="engageai_org_name"><?php esc_html_e('Name', 'engage-ai'); ?></label></th>
                            <td><input type="text" id="engageai_org_name" name="engageai_org_name" class="regular-text" required></td>
                        </tr>
                        <tr>
                            <th><label for="engageai_org_mission"><?php esc_html_e('Mission', 'engage-ai'); ?></label></th>
                            <td><textarea id="engageai_org_mission" name="engageai_org_mission" class="large-text" rows="2"></textarea></td>
                        </tr>
                        <tr>
                            <th><label for="engageai_org_tone"><?php esc_html_e('Tone', 'engage-ai'); ?></label></th>
                            <td><input type="text" id="engageai_org_tone" name="engageai_org_tone" class="regular-text" value="warm, clear, inviting, faith-centered"></td>
                        </tr>
                        <tr>
                            <th><label for="engageai_org_audience"><?php esc_html_e('Audience', 'engage-ai'); ?></label></th>
                            <td><input type="text" id="engageai_org_audience" name="engageai_org_audience" class="regular-text"></td>
                        </tr>
                        <tr>
                            <th><label for="engageai_org_website_url"><?php esc_html_e('Website URL', 'engage-ai'); ?></label></th>
                            <td>
                                <input type="url" id="engageai_org_website_url" name="engageai_org_website_url" class="regular-text" placeholder="https://example.org">
                                <p class="description"><?php esc_html_e('Optional, but sharpens the Analytics module\'s web search a lot for common organization names.', 'engage-ai'); ?></p>
                            </td>
                        </tr>
                    </table>
                    <?php submit_button(__('Create organization', 'engage-ai')); ?>
                </form>

                <?php if ($selected_org_id): ?>
                    <?php
                    $active_org = null;
                    foreach ($organizations as $o) {
                        if ((int) $o['id'] === (int) $selected_org_id) {
                            $active_org = $o;
                            break;
                        }
                    }
                    $enabled_modules = $active_org['enabled_modules'] ?? [];
                    ?>
                    <hr>
                    <h2><?php esc_html_e('4. Organization details', 'engage-ai'); ?></h2>
                    <p class="description"><?php esc_html_e('Website URL sharpens the Analytics module\'s web search a lot for common organization names.', 'engage-ai'); ?></p>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <input type="hidden" name="action" value="engageai_update_organization">
                        <?php wp_nonce_field('engageai_update_organization'); ?>
                        <table class="form-table">
                            <tr>
                                <th><label for="engageai_org_website_url"><?php esc_html_e('Website URL', 'engage-ai'); ?></label></th>
                                <td><input type="url" id="engageai_org_website_url" name="engageai_org_website_url" class="regular-text" value="<?php echo esc_attr($active_org['website_url'] ?? ''); ?>" placeholder="https://example.org"></td>
                            </tr>
                            <tr>
                                <th><label for="engageai_org_mission"><?php esc_html_e('Mission', 'engage-ai'); ?></label></th>
                                <td><textarea id="engageai_org_mission" name="engageai_org_mission" class="large-text" rows="2"><?php echo esc_textarea($active_org['mission'] ?? ''); ?></textarea></td>
                            </tr>
                            <tr>
                                <th><label for="engageai_org_audience"><?php esc_html_e('Audience', 'engage-ai'); ?></label></th>
                                <td><input type="text" id="engageai_org_audience" name="engageai_org_audience" class="regular-text" value="<?php echo esc_attr($active_org['audience'] ?? ''); ?>"></td>
                            </tr>
                        </table>
                        <?php submit_button(__('Save organization details', 'engage-ai')); ?>
                    </form>

                    <hr>
                    <h2><?php esc_html_e('5. Engagement targets', 'engage-ai'); ?></h2>
                    <p class="description"><?php esc_html_e('What the Engagement Growth agent works toward - leave a field blank for "no target set" (that channel just gets reported on, not chased). See it turn these into concrete next actions on the Analytics page.', 'engage-ai'); ?></p>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <input type="hidden" name="action" value="engageai_update_targets">
                        <?php wp_nonce_field('engageai_update_targets'); ?>
                        <table class="form-table">
                            <tr>
                                <th><label for="engageai_target_org_score"><?php esc_html_e('Overall org score target', 'engage-ai'); ?></label></th>
                                <td><input type="number" min="0" max="100" id="engageai_target_org_score" name="engageai_target_org_score" value="<?php echo esc_attr($active_org['target_org_score'] ?? ''); ?>" class="small-text"> / 100</td>
                            </tr>
                            <?php $target_channel_scores = $active_org['target_channel_scores'] ?? []; ?>
                            <?php foreach (EngageAI_Admin_Analytics::channels() as $key => $label): ?>
                                <tr>
                                    <th><label for="engageai_target_<?php echo esc_attr($key); ?>"><?php echo esc_html($label); ?></label></th>
                                    <td><input type="number" min="0" max="100" id="engageai_target_<?php echo esc_attr($key); ?>" name="engageai_target_<?php echo esc_attr($key); ?>" value="<?php echo esc_attr($target_channel_scores[$key] ?? ''); ?>" class="small-text"> / 100</td>
                                </tr>
                            <?php endforeach; ?>
                        </table>
                        <?php submit_button(__('Save targets', 'engage-ai')); ?>
                    </form>

                    <hr>
                    <h2><?php esc_html_e('6. Modules', 'engage-ai'); ?></h2>
                    <p class="description"><?php esc_html_e('Turn on only what this organization needs. "Church Engagement Content" is the original event/announcement/sermon generator; each Claude AI side hustle below runs its own autonomous check-in cycle once activated.', 'engage-ai'); ?></p>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <input type="hidden" name="action" value="engageai_update_modules">
                        <?php wp_nonce_field('engageai_update_modules'); ?>
                        <table class="form-table">
                            <?php foreach (self::available_modules() as $key => $label): ?>
                                <tr>
                                    <th scope="row"><?php echo esc_html($label); ?></th>
                                    <td>
                                        <label>
                                            <input type="checkbox" name="engageai_modules[]" value="<?php echo esc_attr($key); ?>"
                                                <?php checked(in_array($key, $enabled_modules, true)); ?>>
                                            <?php esc_html_e('Active', 'engage-ai'); ?>
                                        </label>
                                    </td>
                                </tr>
                            <?php endforeach; ?>
                        </table>
                        <?php submit_button(__('Save modules', 'engage-ai')); ?>
                    </form>
                    <p class="description">
                        <?php
                        printf(
                            /* translators: %s: link to the Agents admin page */
                            esc_html__('Once a side-hustle module is active, review and approve its work under %s.', 'engage-ai'),
                            '<a href="' . esc_url(admin_url('admin.php?page=engageai-agents')) . '">' . esc_html__('Engage AI > Agents', 'engage-ai') . '</a>'
                        );
                        ?>
                    </p>
                <?php endif; ?>
            <?php endif; ?>
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
        wp_safe_redirect(add_query_arg(['page' => 'engageai-settings'], admin_url('admin.php')));
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
