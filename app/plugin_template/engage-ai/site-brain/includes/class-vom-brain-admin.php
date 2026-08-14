<?php
/**
 * The admin screen: status, connection details, tokens, content selection,
 * business facts and agent activity.
 *
 * @package VOM_Site_Brain
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class VOM_Brain_Admin {

	const CAP  = 'manage_options';
	const SLUG = 'vom-site-brain';

	/**
	 * Register hooks.
	 */
	public static function hooks() {
		add_action( 'admin_menu', array( __CLASS__, 'menu' ) );
		add_action( 'admin_post_vom_brain_save', array( __CLASS__, 'handle_save' ) );
		add_action( 'admin_post_vom_brain_token', array( __CLASS__, 'handle_token' ) );
		add_action( 'admin_post_vom_brain_rebuild', array( __CLASS__, 'handle_rebuild' ) );
		add_action( 'admin_post_vom_brain_clear_log', array( __CLASS__, 'handle_clear_log' ) );
		add_action( 'admin_enqueue_scripts', array( __CLASS__, 'assets' ) );
	}

	/**
	 * The admin page slug.
	 *
	 * Overridable with VOM_BRAIN_PAGE_SLUG so this same code can run inside a
	 * host plugin as a module without colliding with its page namespace.
	 *
	 * @return string
	 */
	public static function page_slug() {
		return defined( 'VOM_BRAIN_PAGE_SLUG' ) && VOM_BRAIN_PAGE_SLUG ? VOM_BRAIN_PAGE_SLUG : self::SLUG;
	}

	/**
	 * Menu entry: a top-level item when standalone, a submenu of the host
	 * plugin when VOM_BRAIN_MENU_PARENT names one.
	 */
	public static function menu() {
		$parent = defined( 'VOM_BRAIN_MENU_PARENT' ) ? VOM_BRAIN_MENU_PARENT : '';

		if ( $parent ) {
			add_submenu_page(
				$parent,
				__( 'Site Brain', 'vom-site-brain' ),
				__( 'Site Brain', 'vom-site-brain' ),
				self::CAP,
				self::page_slug(),
				array( __CLASS__, 'render' )
			);
			return;
		}

		add_menu_page(
			__( 'Site Brain', 'vom-site-brain' ),
			__( 'Site Brain', 'vom-site-brain' ),
			self::CAP,
			self::page_slug(),
			array( __CLASS__, 'render' ),
			'dashicons-database-view',
			58
		);
	}

	/**
	 * Load the stylesheet on our screen only.
	 *
	 * @param string $hook Current admin page hook.
	 */
	public static function assets( $hook ) {
		if ( false === strpos( (string) $hook, self::page_slug() ) ) {
			return;
		}
		wp_enqueue_style( 'vom-brain-admin', VOM_BRAIN_URL . 'assets/admin.css', array(), VOM_BRAIN_VERSION );
	}

	/**
	 * Current tab.
	 *
	 * @return string
	 */
	private static function tab() {
		$tab = isset( $_GET['tab'] ) ? sanitize_key( wp_unslash( $_GET['tab'] ) ) : 'status'; // phpcs:ignore WordPress.Security.NonceVerification.Recommended
		return in_array( $tab, array( 'status', 'connect', 'content', 'facts', 'activity' ), true ) ? $tab : 'status';
	}

	/**
	 * URL of a tab.
	 *
	 * @param string $tab Tab slug.
	 * @return string
	 */
	private static function tab_url( $tab ) {
		return admin_url( 'admin.php?page=' . self::page_slug() . '&tab=' . $tab );
	}

	/* --------------------------------------------------------------------- */
	/* Handlers                                                                */
	/* --------------------------------------------------------------------- */

	/**
	 * Save settings.
	 */
	public static function handle_save() {
		if ( ! current_user_can( self::CAP ) ) {
			wp_die( esc_html__( 'You are not allowed to do that.', 'vom-site-brain' ) );
		}
		check_admin_referer( 'vom_brain_save' );

		$tab = isset( $_POST['tab'] ) ? sanitize_key( wp_unslash( $_POST['tab'] ) ) : 'status';
		VOM_Brain_Settings::update( VOM_Brain_Settings::sanitize( $_POST, $tab ) ); // phpcs:ignore WordPress.Security.NonceVerification.Missing
		VOM_Brain_Aggregator::rebuild();

		wp_safe_redirect( add_query_arg( 'saved', '1', self::tab_url( $tab ) ) );
		exit;
	}

	/**
	 * Create or revoke a token.
	 */
	public static function handle_token() {
		if ( ! current_user_can( self::CAP ) ) {
			wp_die( esc_html__( 'You are not allowed to do that.', 'vom-site-brain' ) );
		}
		check_admin_referer( 'vom_brain_token' );

		$args = array();

		if ( ! empty( $_POST['revoke'] ) ) {
			VOM_Brain_Auth::revoke( sanitize_text_field( wp_unslash( $_POST['revoke'] ) ) );
			$args['revoked'] = '1';
		} else {
			$label  = isset( $_POST['label'] ) ? sanitize_text_field( wp_unslash( $_POST['label'] ) ) : '';
			$scope  = isset( $_POST['scope'] ) ? sanitize_key( wp_unslash( $_POST['scope'] ) ) : 'read';
			$token  = VOM_Brain_Auth::create_token( $label, $scope );
			set_transient( 'vom_brain_new_token_' . get_current_user_id(), $token['token'], 300 );
			$args['created'] = '1';
		}

		wp_safe_redirect( add_query_arg( $args, self::tab_url( 'connect' ) ) );
		exit;
	}

	/**
	 * Queue a full rebuild and run the first batch immediately.
	 */
	public static function handle_rebuild() {
		if ( ! current_user_can( self::CAP ) ) {
			wp_die( esc_html__( 'You are not allowed to do that.', 'vom-site-brain' ) );
		}
		check_admin_referer( 'vom_brain_rebuild' );

		if ( ! empty( $_POST['wipe'] ) ) {
			VOM_Brain_Index::truncate();
		}
		VOM_Brain_Index::queue_full_build();
		VOM_Brain_Index::run_batch();

		wp_safe_redirect( add_query_arg( 'rebuilding', '1', self::tab_url( 'status' ) ) );
		exit;
	}

	/**
	 * Empty the activity log.
	 */
	public static function handle_clear_log() {
		if ( ! current_user_can( self::CAP ) ) {
			wp_die( esc_html__( 'You are not allowed to do that.', 'vom-site-brain' ) );
		}
		check_admin_referer( 'vom_brain_clear_log' );
		VOM_Brain_Log::clear();
		wp_safe_redirect( self::tab_url( 'activity' ) );
		exit;
	}

	/* --------------------------------------------------------------------- */
	/* Rendering                                                               */
	/* --------------------------------------------------------------------- */

	/**
	 * Page router.
	 */
	public static function render() {
		if ( ! current_user_can( self::CAP ) ) {
			return;
		}
		$tab = self::tab();

		echo '<div class="wrap vom-brain">';
		echo '<h1>' . esc_html__( 'Site Brain', 'vom-site-brain' ) . ' <span class="vom-brain-version">v' . esc_html( VOM_BRAIN_VERSION ) . '</span></h1>';
		echo '<p class="vom-brain-lede">' . esc_html__( 'This website, aggregated and served to AI agents over the Model Context Protocol.', 'vom-site-brain' ) . '</p>';

		if ( isset( $_GET['saved'] ) ) { // phpcs:ignore WordPress.Security.NonceVerification.Recommended
			echo '<div class="notice notice-success is-dismissible"><p>' . esc_html__( 'Settings saved.', 'vom-site-brain' ) . '</p></div>';
		}
		if ( isset( $_GET['rebuilding'] ) ) { // phpcs:ignore WordPress.Security.NonceVerification.Recommended
			echo '<div class="notice notice-success is-dismissible"><p>' . esc_html__( 'Rebuild started. It continues in the background — reload this page to watch progress.', 'vom-site-brain' ) . '</p></div>';
		}
		if ( isset( $_GET['revoked'] ) ) { // phpcs:ignore WordPress.Security.NonceVerification.Recommended
			echo '<div class="notice notice-success is-dismissible"><p>' . esc_html__( 'Token revoked.', 'vom-site-brain' ) . '</p></div>';
		}

		echo '<h2 class="nav-tab-wrapper">';
		$tabs = array(
			'status'   => __( 'Status', 'vom-site-brain' ),
			'connect'  => __( 'Connect an agent', 'vom-site-brain' ),
			'content'  => __( 'What to share', 'vom-site-brain' ),
			'facts'    => __( 'Business facts', 'vom-site-brain' ),
			'activity' => __( 'Agent activity', 'vom-site-brain' ),
		);
		foreach ( $tabs as $slug => $label ) {
			printf(
				'<a href="%s" class="nav-tab%s">%s</a>',
				esc_url( self::tab_url( $slug ) ),
				$slug === $tab ? ' nav-tab-active' : '',
				esc_html( $label )
			);
		}
		echo '</h2>';

		switch ( $tab ) {
			case 'connect':
				self::tab_connect();
				break;
			case 'content':
				self::tab_content();
				break;
			case 'facts':
				self::tab_facts();
				break;
			case 'activity':
				self::tab_activity();
				break;
			default:
				self::tab_status();
		}

		echo '</div>';
	}

	/**
	 * Status tab.
	 */
	private static function tab_status() {
		$stats = VOM_Brain_Index::stats();
		$state = VOM_Brain_Index::state();
		$queue = isset( $state['queued'] ) ? (int) $state['queued'] : 0;

		echo '<div class="vom-cards">';
		self::card( __( 'Documents', 'vom-site-brain' ), number_format_i18n( $stats['documents'] ) );
		self::card( __( 'Retrievable passages', 'vom-site-brain' ), number_format_i18n( $stats['passages'] ) );
		self::card( __( 'Words indexed', 'vom-site-brain' ), number_format_i18n( $stats['words'] ) );
		self::card( __( 'Queue', 'vom-site-brain' ), $queue ? number_format_i18n( $queue ) . ' ' . __( 'waiting', 'vom-site-brain' ) : __( 'idle', 'vom-site-brain' ) );
		echo '</div>';

		echo '<table class="widefat striped vom-table"><tbody>';
		self::row( __( 'Serving', 'vom-site-brain' ), VOM_Brain_Settings::get( 'enabled' ) ? __( 'On', 'vom-site-brain' ) : __( 'Off', 'vom-site-brain' ) );
		self::row( __( 'Anonymous read access', 'vom-site-brain' ), VOM_Brain_Settings::get( 'public_read' ) ? __( 'Allowed', 'vom-site-brain' ) : __( 'Token required', 'vom-site-brain' ) );
		self::row( __( 'Full-text search index', 'vom-site-brain' ), $stats['fulltext'] ? __( 'Active (MySQL FULLTEXT)', 'vom-site-brain' ) : __( 'Not available — using LIKE fallback', 'vom-site-brain' ) );
		self::row( __( 'Newest content', 'vom-site-brain' ), $stats['last_content'] ? $stats['last_content'] : '—' );
		self::row( __( 'Last full build', 'vom-site-brain' ), isset( $state['last_built'] ) ? $state['last_built'] : '—' );
		echo '</tbody></table>';

		if ( $stats['by_type'] ) {
			echo '<h3>' . esc_html__( 'Indexed by type', 'vom-site-brain' ) . '</h3><ul class="vom-list">';
			foreach ( $stats['by_type'] as $type => $count ) {
				echo '<li><code>' . esc_html( $type ) . '</code> — ' . esc_html( number_format_i18n( $count ) ) . '</li>';
			}
			echo '</ul>';
		}

		echo '<form method="post" action="' . esc_url( admin_url( 'admin-post.php' ) ) . '" class="vom-form">';
		wp_nonce_field( 'vom_brain_rebuild' );
		echo '<input type="hidden" name="action" value="vom_brain_rebuild" />';
		echo '<p><label><input type="checkbox" name="wipe" value="1" /> ' . esc_html__( 'Empty the index first (slower, fixes a corrupted index)', 'vom-site-brain' ) . '</label></p>';
		submit_button( __( 'Rebuild the brain now', 'vom-site-brain' ), 'primary', 'submit', false );
		echo '</form>';

		echo '<p class="description">' . esc_html__( 'The brain also refreshes itself: every published change is reindexed immediately, and a full crawl runs daily.', 'vom-site-brain' ) . '</p>';
	}

	/**
	 * Connection tab.
	 */
	private static function tab_connect() {
		$mcp   = rest_url( VOM_BRAIN_NS . '/mcp' );
		$token = get_transient( 'vom_brain_new_token_' . get_current_user_id() );
		if ( $token ) {
			delete_transient( 'vom_brain_new_token_' . get_current_user_id() );
			echo '<div class="notice notice-warning"><p><strong>' . esc_html__( 'Copy this token now — it is never shown again:', 'vom-site-brain' ) . '</strong></p>';
			echo '<p><code class="vom-token">' . esc_html( $token ) . '</code></p></div>';
		}

		echo '<h3>' . esc_html__( 'Endpoint', 'vom-site-brain' ) . '</h3>';
		echo '<p>' . esc_html__( 'Streamable HTTP, protocol', 'vom-site-brain' ) . ' <code>' . esc_html( VOM_Brain_MCP_Server::LATEST_PROTOCOL ) . '</code></p>';
		echo '<p><code class="vom-endpoint">' . esc_html( $mcp ) . '</code></p>';

		echo '<h3>' . esc_html__( 'Claude Code / Claude Desktop', 'vom-site-brain' ) . '</h3>';
		$config = array(
			'mcpServers' => array(
				'site-brain' => array(
					'type' => 'http',
					'url'  => $mcp,
				),
			),
		);
		echo '<pre class="vom-pre">' . esc_html( wp_json_encode( $config, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES ) ) . '</pre>';
		echo '<p class="description">' . esc_html__( 'With a token, add: "headers": { "Authorization": "Bearer <token>" }', 'vom-site-brain' ) . '</p>';

		echo '<h3>' . esc_html__( 'Try it from a terminal', 'vom-site-brain' ) . '</h3>';
		$curl = "curl -s -X POST '" . $mcp . "' \\\n  -H 'Content-Type: application/json' \\\n  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'";
		echo '<pre class="vom-pre">' . esc_html( $curl ) . '</pre>';

		echo '<h3>' . esc_html__( 'Other surfaces', 'vom-site-brain' ) . '</h3>';
		echo '<ul class="vom-list">';
		foreach ( VOM_Brain_Aggregator::endpoints() as $label => $url ) {
			echo '<li><strong>' . esc_html( str_replace( '_', ' ', $label ) ) . '</strong> — <a href="' . esc_url( $url ) . '" target="_blank" rel="noreferrer noopener">' . esc_html( $url ) . '</a></li>';
		}
		echo '</ul>';

		echo '<h3>' . esc_html__( 'Access tokens', 'vom-site-brain' ) . '</h3>';
		echo '<p class="description">' . esc_html__( 'Tokens are optional when anonymous read access is on. A "full" token additionally exposes the content types you marked as private.', 'vom-site-brain' ) . '</p>';

		$tokens = VOM_Brain_Auth::tokens();
		echo '<table class="widefat striped"><thead><tr>';
		echo '<th>' . esc_html__( 'Label', 'vom-site-brain' ) . '</th>';
		echo '<th>' . esc_html__( 'Scope', 'vom-site-brain' ) . '</th>';
		echo '<th>' . esc_html__( 'Created', 'vom-site-brain' ) . '</th>';
		echo '<th>' . esc_html__( 'Last used', 'vom-site-brain' ) . '</th>';
		echo '<th>' . esc_html__( 'Calls', 'vom-site-brain' ) . '</th>';
		echo '<th></th></tr></thead><tbody>';

		if ( ! $tokens ) {
			echo '<tr><td colspan="6">' . esc_html__( 'No tokens issued.', 'vom-site-brain' ) . '</td></tr>';
		}
		foreach ( $tokens as $id => $t ) {
			echo '<tr>';
			echo '<td>' . esc_html( $t['label'] ) . '</td>';
			echo '<td><code>' . esc_html( $t['scope'] ) . '</code></td>';
			echo '<td>' . esc_html( $t['created'] ) . '</td>';
			echo '<td>' . esc_html( $t['last_used'] ? $t['last_used'] : '—' ) . '</td>';
			echo '<td>' . esc_html( isset( $t['calls'] ) ? (int) $t['calls'] : 0 ) . '</td>';
			echo '<td><form method="post" action="' . esc_url( admin_url( 'admin-post.php' ) ) . '">';
			wp_nonce_field( 'vom_brain_token' );
			echo '<input type="hidden" name="action" value="vom_brain_token" />';
			echo '<input type="hidden" name="revoke" value="' . esc_attr( $id ) . '" />';
			echo '<button class="button button-small" type="submit">' . esc_html__( 'Revoke', 'vom-site-brain' ) . '</button>';
			echo '</form></td></tr>';
		}
		echo '</tbody></table>';

		echo '<form method="post" action="' . esc_url( admin_url( 'admin-post.php' ) ) . '" class="vom-form">';
		wp_nonce_field( 'vom_brain_token' );
		echo '<input type="hidden" name="action" value="vom_brain_token" />';
		echo '<p><input type="text" name="label" class="regular-text" placeholder="' . esc_attr__( 'What is this token for?', 'vom-site-brain' ) . '" /> ';
		echo '<select name="scope"><option value="read">' . esc_html__( 'read — public content', 'vom-site-brain' ) . '</option>';
		echo '<option value="full">' . esc_html__( 'full — includes private content types', 'vom-site-brain' ) . '</option></select></p>';
		submit_button( __( 'Issue a token', 'vom-site-brain' ), 'secondary', 'submit', false );
		echo '</form>';
	}

	/**
	 * Content selection tab.
	 */
	private static function tab_content() {
		$s = VOM_Brain_Settings::all();

		self::form_open( 'content' );

		echo '<table class="form-table" role="presentation"><tbody>';

		self::field(
			__( 'Serving', 'vom-site-brain' ),
			'<label><input type="checkbox" name="enabled" value="1" ' . checked( $s['enabled'], true, false ) . ' /> ' . esc_html__( 'Serve the brain to agents', 'vom-site-brain' ) . '</label><br />' .
			'<label><input type="checkbox" name="public_read" value="1" ' . checked( $s['public_read'], true, false ) . ' /> ' . esc_html__( 'Allow anonymous read access (recommended — this is how indexing agents find you)', 'vom-site-brain' ) . '</label><br />' .
			'<label><input type="checkbox" name="log_enabled" value="1" ' . checked( $s['log_enabled'], true, false ) . ' /> ' . esc_html__( 'Log agent activity', 'vom-site-brain' ) . '</label>'
		);

		$public_types = '';
		foreach ( VOM_Brain_Settings::selectable_post_types() as $slug => $label ) {
			$public_types .= '<label class="vom-check"><input type="checkbox" name="post_types[]" value="' . esc_attr( $slug ) . '" ' . checked( in_array( $slug, (array) $s['post_types'], true ), true, false ) . ' /> ' . esc_html( $label ) . ' <code>' . esc_html( $slug ) . '</code></label>';
		}
		self::field( __( 'Public content types', 'vom-site-brain' ), $public_types );

		$private_types = '';
		foreach ( VOM_Brain_Settings::selectable_post_types() as $slug => $label ) {
			$private_types .= '<label class="vom-check"><input type="checkbox" name="private_post_types[]" value="' . esc_attr( $slug ) . '" ' . checked( in_array( $slug, (array) $s['private_post_types'], true ), true, false ) . ' /> ' . esc_html( $label ) . ' <code>' . esc_html( $slug ) . '</code></label>';
		}
		self::field(
			__( 'Private content types', 'vom-site-brain' ),
			$private_types . '<p class="description">' . esc_html__( 'Indexed, but only returned to agents presenting a full-scope token. Use for internal handbooks or staff-only material.', 'vom-site-brain' ) . '</p>'
		);

		$taxes = '';
		foreach ( VOM_Brain_Settings::selectable_taxonomies() as $slug => $label ) {
			$taxes .= '<label class="vom-check"><input type="checkbox" name="taxonomies[]" value="' . esc_attr( $slug ) . '" ' . checked( in_array( $slug, (array) $s['taxonomies'], true ), true, false ) . ' /> ' . esc_html( $label ) . '</label>';
		}
		self::field( __( 'Taxonomies', 'vom-site-brain' ), $taxes );

		self::field(
			__( 'Never index these post IDs', 'vom-site-brain' ),
			'<input type="text" name="exclude_ids" class="large-text" value="' . esc_attr( implode( ', ', (array) $s['exclude_ids'] ) ) . '" placeholder="12, 45, 108" />'
		);

		self::field(
			__( 'Extra meta keys to expose', 'vom-site-brain' ),
			'<input type="text" name="meta_keys" class="large-text" value="' . esc_attr( implode( ', ', (array) $s['meta_keys'] ) ) . '" placeholder="event_date, price_from" />' .
			'<p class="description">' . esc_html__( 'Custom fields returned verbatim with each document. Only add keys that are safe to publish.', 'vom-site-brain' ) . '</p>'
		);

		self::field(
			__( 'Extraction', 'vom-site-brain' ),
			'<label><input type="checkbox" name="render_shortcodes" value="1" ' . checked( $s['render_shortcodes'], true, false ) . ' /> ' . esc_html__( 'Run shortcodes when indexing (slower; only if page builders hide your text behind them)', 'vom-site-brain' ) . '</label><br />' .
			'<label><input type="checkbox" name="include_woo" value="1" ' . checked( $s['include_woo'], true, false ) . ' /> ' . esc_html__( 'Include WooCommerce prices and stock status', 'vom-site-brain' ) . '</label>'
		);

		self::field(
			__( 'Tuning', 'vom-site-brain' ),
			'<label>' . esc_html__( 'Words per passage', 'vom-site-brain' ) . ' <input type="number" name="chunk_words" value="' . esc_attr( $s['chunk_words'] ) . '" min="60" max="1200" /></label> ' .
			'<label>' . esc_html__( 'Overlap', 'vom-site-brain' ) . ' <input type="number" name="chunk_overlap" value="' . esc_attr( $s['chunk_overlap'] ) . '" min="0" max="400" /></label><br />' .
			'<label>' . esc_html__( 'Documents per background batch', 'vom-site-brain' ) . ' <input type="number" name="batch_size" value="' . esc_attr( $s['batch_size'] ) . '" min="5" max="200" /></label><br />' .
			'<label>' . esc_html__( 'Max characters per answer', 'vom-site-brain' ) . ' <input type="number" name="max_response_chars" value="' . esc_attr( $s['max_response_chars'] ) . '" min="1000" max="200000" /></label><br />' .
			'<label>' . esc_html__( 'Anonymous calls per hour, per IP (0 = unlimited)', 'vom-site-brain' ) . ' <input type="number" name="rate_limit" value="' . esc_attr( $s['rate_limit'] ) . '" min="0" /></label>'
		);

		echo '</tbody></table>';
		submit_button( __( 'Save and re-aggregate', 'vom-site-brain' ) );
		echo '</form>';
		echo '<p class="description">' . esc_html__( 'Changing the content types or tuning values does not reindex on its own — run a rebuild from the Status tab afterwards.', 'vom-site-brain' ) . '</p>';
	}

	/**
	 * Business facts tab.
	 */
	private static function tab_facts() {
		$s = VOM_Brain_Settings::all();

		echo '<p class="description">' . esc_html__( 'Everything here is served verbatim to agents through the contact_info tool and the site overview. This is what stops a chatbot inventing your opening hours.', 'vom-site-brain' ) . '</p>';

		self::form_open( 'facts' );
		echo '<table class="form-table" role="presentation"><tbody>';

		self::text_field( __( 'Business name', 'vom-site-brain' ), 'biz_name', $s['biz_name'] );
		self::text_field( __( 'Tagline', 'vom-site-brain' ), 'biz_tagline', $s['biz_tagline'] );
		self::area_field( __( 'What you do', 'vom-site-brain' ), 'biz_description', $s['biz_description'], 3, __( 'Two or three sentences. This is the first thing an agent reads.', 'vom-site-brain' ) );
		self::text_field( __( 'Industry', 'vom-site-brain' ), 'biz_industry', $s['biz_industry'] );
		self::text_field( __( 'Email', 'vom-site-brain' ), 'biz_email', $s['biz_email'] );
		self::text_field( __( 'Phone', 'vom-site-brain' ), 'biz_phone', $s['biz_phone'] );
		self::text_field( __( 'WhatsApp', 'vom-site-brain' ), 'biz_whatsapp', $s['biz_whatsapp'] );
		self::area_field( __( 'Address', 'vom-site-brain' ), 'biz_address', $s['biz_address'], 3 );
		self::text_field( __( 'Service area', 'vom-site-brain' ), 'biz_service_area', $s['biz_service_area'] );
		self::area_field( __( 'Opening hours', 'vom-site-brain' ), 'biz_hours', $s['biz_hours'], 7, __( 'One per line, as "Monday: 09:00–17:00".', 'vom-site-brain' ) );
		self::text_field( __( 'Booking or quote URL', 'vom-site-brain' ), 'biz_booking_url', $s['biz_booking_url'] );
		self::text_field( __( 'Pricing note', 'vom-site-brain' ), 'biz_pricing_note', $s['biz_pricing_note'] );
		self::text_field( __( 'Languages spoken', 'vom-site-brain' ), 'biz_languages', $s['biz_languages'] );
		self::area_field( __( 'Social profiles', 'vom-site-brain' ), 'biz_socials', $s['biz_socials'], 4, __( 'One per line, as "LinkedIn: https://…".', 'vom-site-brain' ) );

		self::area_field(
			__( 'How an assistant should speak for you', 'vom-site-brain' ),
			'answer_guidelines',
			$s['answer_guidelines'],
			4,
			__( 'Tone, what to emphasise, what never to promise. Delivered to every connected chatbot.', 'vom-site-brain' )
		);
		self::area_field(
			__( 'When it cannot answer', 'vom-site-brain' ),
			'escalation',
			$s['escalation'],
			3,
			__( 'For example: "Offer the contact form at /contact and never quote a price."', 'vom-site-brain' )
		);
		self::area_field(
			__( 'Frequently asked questions', 'vom-site-brain' ),
			'faqs_raw',
			$s['faqs_raw'],
			12,
			__( 'Q: on one line, A: on the next, three dashes between pairs.', 'vom-site-brain' )
		);

		echo '</tbody></table>';
		submit_button( __( 'Save facts', 'vom-site-brain' ) );
		echo '</form>';
	}

	/**
	 * Activity tab.
	 */
	private static function tab_activity() {
		$counts = VOM_Brain_Log::counts();
		if ( $counts ) {
			echo '<h3>' . esc_html__( 'Most requested', 'vom-site-brain' ) . '</h3><ul class="vom-list">';
			foreach ( array_slice( $counts, 0, 12, true ) as $tool => $n ) {
				echo '<li><code>' . esc_html( $tool ) . '</code> — ' . esc_html( number_format_i18n( $n ) ) . '</li>';
			}
			echo '</ul>';
		}

		$rows = VOM_Brain_Log::recent( 100 );
		echo '<h3>' . esc_html__( 'Recent calls', 'vom-site-brain' ) . '</h3>';

		if ( ! $rows ) {
			echo '<p>' . esc_html__( 'Nothing yet. Once an agent connects, every call shows up here.', 'vom-site-brain' ) . '</p>';
		} else {
			echo '<table class="widefat striped"><thead><tr>';
			echo '<th>' . esc_html__( 'When (UTC)', 'vom-site-brain' ) . '</th>';
			echo '<th>' . esc_html__( 'Call', 'vom-site-brain' ) . '</th>';
			echo '<th>' . esc_html__( 'Query', 'vom-site-brain' ) . '</th>';
			echo '<th>' . esc_html__( 'Client', 'vom-site-brain' ) . '</th>';
			echo '<th>' . esc_html__( 'Scope', 'vom-site-brain' ) . '</th>';
			echo '<th>' . esc_html__( 'Status', 'vom-site-brain' ) . '</th>';
			echo '<th>ms</th></tr></thead><tbody>';
			foreach ( $rows as $row ) {
				echo '<tr>';
				echo '<td>' . esc_html( $row['t'] ) . '</td>';
				echo '<td><code>' . esc_html( $row['tool'] ? $row['tool'] : $row['method'] ) . '</code></td>';
				echo '<td>' . esc_html( $row['query'] ) . '</td>';
				echo '<td>' . esc_html( $row['client'] ) . '</td>';
				echo '<td>' . esc_html( $row['scope'] ) . '</td>';
				echo '<td>' . esc_html( $row['status'] ) . '</td>';
				echo '<td>' . esc_html( $row['ms'] ) . '</td>';
				echo '</tr>';
			}
			echo '</tbody></table>';
		}

		echo '<form method="post" action="' . esc_url( admin_url( 'admin-post.php' ) ) . '" class="vom-form">';
		wp_nonce_field( 'vom_brain_clear_log' );
		echo '<input type="hidden" name="action" value="vom_brain_clear_log" />';
		submit_button( __( 'Clear the log', 'vom-site-brain' ), 'secondary', 'submit', false );
		echo '</form>';
		echo '<p class="description">' . esc_html__( 'Only a truncated hash of the caller IP is stored, never the address itself.', 'vom-site-brain' ) . '</p>';
	}

	/* --------------------------------------------------------------------- */
	/* Small view helpers                                                      */
	/* --------------------------------------------------------------------- */

	/**
	 * Open a settings form.
	 *
	 * @param string $tab Tab slug to return to.
	 */
	private static function form_open( $tab ) {
		echo '<form method="post" action="' . esc_url( admin_url( 'admin-post.php' ) ) . '">';
		wp_nonce_field( 'vom_brain_save' );
		echo '<input type="hidden" name="action" value="vom_brain_save" />';
		echo '<input type="hidden" name="tab" value="' . esc_attr( $tab ) . '" />';
	}

	/**
	 * One form-table row. $html is pre-escaped by the caller.
	 *
	 * @param string $label Label.
	 * @param string $html  Field markup.
	 */
	private static function field( $label, $html ) {
		echo '<tr><th scope="row">' . esc_html( $label ) . '</th><td>' . $html . '</td></tr>'; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped
	}

	/**
	 * Text input row.
	 *
	 * @param string $label Label.
	 * @param string $name  Field name.
	 * @param string $value Current value.
	 */
	private static function text_field( $label, $name, $value ) {
		self::field( $label, '<input type="text" class="regular-text" name="' . esc_attr( $name ) . '" value="' . esc_attr( $value ) . '" />' );
	}

	/**
	 * Textarea row.
	 *
	 * @param string $label Label.
	 * @param string $name  Field name.
	 * @param string $value Current value.
	 * @param int    $rows  Rows.
	 * @param string $help  Optional description.
	 */
	private static function area_field( $label, $name, $value, $rows = 4, $help = '' ) {
		$html = '<textarea class="large-text code" rows="' . (int) $rows . '" name="' . esc_attr( $name ) . '">' . esc_textarea( $value ) . '</textarea>';
		if ( $help ) {
			$html .= '<p class="description">' . esc_html( $help ) . '</p>';
		}
		self::field( $label, $html );
	}

	/**
	 * A stat card.
	 *
	 * @param string $label Label.
	 * @param string $value Value.
	 */
	private static function card( $label, $value ) {
		echo '<div class="vom-card"><span class="vom-card-value">' . esc_html( $value ) . '</span><span class="vom-card-label">' . esc_html( $label ) . '</span></div>';
	}

	/**
	 * A two-column table row.
	 *
	 * @param string $label Label.
	 * @param string $value Value, already escaped or plain.
	 */
	private static function row( $label, $value ) {
		echo '<tr><td class="vom-key">' . esc_html( $label ) . '</td><td>' . esc_html( $value ) . '</td></tr>';
	}
}
