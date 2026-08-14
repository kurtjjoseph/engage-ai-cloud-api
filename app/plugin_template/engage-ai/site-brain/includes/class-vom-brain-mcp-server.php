<?php
/**
 * The MCP server: JSON-RPC 2.0 over Streamable HTTP, with tools, resources
 * and prompts backed by the aggregated site index.
 *
 * @package VOM_Site_Brain
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class VOM_Brain_MCP_Server {

	const LATEST_PROTOCOL = '2025-06-18';

	/**
	 * Set when the current request was a JSON-RPC notification, which the spec
	 * answers with 202 and no body at all.
	 *
	 * @var bool
	 */
	private static $empty_body = false;

	/**
	 * Protocol revisions this server can speak.
	 *
	 * @return string[]
	 */
	public static function supported_protocols() {
		return array( '2025-06-18', '2025-03-26', '2024-11-05' );
	}

	/**
	 * Register the transport route and CORS handling.
	 */
	public static function hooks() {
		add_action( 'rest_api_init', array( __CLASS__, 'register_routes' ) );
		add_filter( 'rest_pre_serve_request', array( __CLASS__, 'send_cors_headers' ), 10, 4 );
	}

	/**
	 * Register /wp-json/vom-mcp/v1/mcp.
	 */
	public static function register_routes() {
		register_rest_route(
			VOM_BRAIN_NS,
			'/mcp',
			array(
				array(
					'methods'             => 'POST',
					'callback'            => array( __CLASS__, 'handle' ),
					'permission_callback' => '__return_true',
				),
				array(
					'methods'             => 'GET',
					'callback'            => array( __CLASS__, 'handle_get' ),
					'permission_callback' => '__return_true',
				),
			)
		);
	}

	/**
	 * Permissive CORS for our namespace only. Authentication is bearer-token
	 * based, never cookie based, so a wildcard origin leaks nothing.
	 *
	 * @param bool             $served  Whether the request was already served.
	 * @param WP_HTTP_Response $result  Response object.
	 * @param WP_REST_Request  $request Request object.
	 * @param WP_REST_Server   $server  Server instance.
	 * @return bool
	 */
	public static function send_cors_headers( $served, $result, $request, $server ) {
		unset( $result, $server );
		if ( ! $request instanceof WP_REST_Request || 0 !== strpos( ltrim( $request->get_route(), '/' ), VOM_BRAIN_NS ) ) {
			return $served;
		}

		header( 'Access-Control-Allow-Origin: *' );
		header( 'Access-Control-Allow-Methods: GET, POST, OPTIONS' );
		header( 'Access-Control-Allow-Headers: Authorization, Content-Type, X-API-Key, Mcp-Protocol-Version, Mcp-Session-Id, Accept' );
		header( 'Access-Control-Expose-Headers: Mcp-Protocol-Version' );
		header( 'Mcp-Protocol-Version: ' . self::LATEST_PROTOCOL );

		// A notification gets 202 with no body — claim the request as served so
		// WordPress does not print a JSON "null".
		if ( self::$empty_body ) {
			return true;
		}

		return $served;
	}

	/**
	 * GET on the MCP endpoint. This server is stateless — no SSE stream to open.
	 *
	 * @return WP_REST_Response
	 */
	public static function handle_get() {
		return new WP_REST_Response(
			array(
				'error' => 'This MCP server is stateless. POST JSON-RPC requests to this URL; there is no server-initiated SSE stream.',
				'spec'  => 'Streamable HTTP, protocol ' . self::LATEST_PROTOCOL,
			),
			405
		);
	}

	/**
	 * Main JSON-RPC entry point.
	 *
	 * @param WP_REST_Request $request Incoming request.
	 * @return WP_REST_Response
	 */
	public static function handle( $request ) {
		$started = microtime( true );
		$body    = $request->get_json_params();

		if ( null === $body ) {
			return self::rpc_error( null, -32700, 'Parse error: the request body is not valid JSON.', 400 );
		}
		if ( ! is_array( $body ) ) {
			return self::rpc_error( null, -32600, 'Invalid request.', 400 );
		}

		// A JSON-RPC batch (removed in protocol 2025-06-18, still tolerated here).
		if ( isset( $body[0] ) && is_array( $body[0] ) ) {
			$out = array();
			foreach ( $body as $single ) {
				$response = self::dispatch( $single, $request, $started );
				if ( null !== $response ) {
					$out[] = $response;
				}
			}
			if ( ! $out ) {
				self::$empty_body = true;
				return new WP_REST_Response( null, 202 );
			}
			return new WP_REST_Response( $out, 200 );
		}

		$response = self::dispatch( $body, $request, $started );
		if ( null === $response ) {
			self::$empty_body = true;
			return new WP_REST_Response( null, 202 );
		}
		if ( isset( $response['__http'] ) ) {
			$status = (int) $response['__http'];
			unset( $response['__http'] );
			return new WP_REST_Response( $response, $status );
		}
		return new WP_REST_Response( $response, 200 );
	}

	/**
	 * Route one JSON-RPC message.
	 *
	 * @param array           $msg     Decoded message.
	 * @param WP_REST_Request $request Original request, for auth.
	 * @param float           $started Start time for logging.
	 * @return array|null Response array, or null for notifications.
	 */
	private static function dispatch( $msg, $request, $started ) {
		$id     = isset( $msg['id'] ) ? $msg['id'] : null;
		$method = isset( $msg['method'] ) ? (string) $msg['method'] : '';
		$params = isset( $msg['params'] ) && is_array( $msg['params'] ) ? $msg['params'] : array();

		if ( '' === $method ) {
			return self::rpc_body( $id, null, array( 'code' => -32600, 'message' => 'Invalid request: no method.' ) );
		}

		// Notifications never get a response body.
		if ( 0 === strpos( $method, 'notifications/' ) ) {
			return null;
		}

		// `initialize` and `ping` are reachable without credentials so clients
		// can discover why they were refused.
		if ( 'initialize' === $method ) {
			return self::rpc_body( $id, self::initialize_result( $params ) );
		}
		if ( 'ping' === $method ) {
			return self::rpc_body( $id, new stdClass() );
		}

		$auth = VOM_Brain_Auth::authenticate( $request );
		if ( ! $auth['ok'] ) {
			VOM_Brain_Log::record(
				array(
					'method' => $method,
					'status' => 'denied',
					'scope'  => 'none',
					'ms'     => (int) round( ( microtime( true ) - $started ) * 1000 ),
				)
			);
			$body            = self::rpc_body( $id, null, array( 'code' => -32001, 'message' => $auth['reason'] ) );
			$body['__http']  = 401;
			return $body;
		}

		switch ( $method ) {
			case 'tools/list':
				$result = array( 'tools' => array_values( self::tools( $auth['scope'] ) ) );
				break;

			case 'tools/call':
				$name = isset( $params['name'] ) ? (string) $params['name'] : '';
				$args = isset( $params['arguments'] ) && is_array( $params['arguments'] ) ? $params['arguments'] : array();
				$result = self::call_tool( $name, $args, $auth );
				VOM_Brain_Log::record(
					array(
						'method' => $method,
						'tool'   => $name,
						'query'  => isset( $args['query'] ) ? (string) $args['query'] : '',
						'scope'  => $auth['scope'],
						'token_id' => $auth['token_id'],
						'status' => empty( $result['isError'] ) ? 'ok' : 'tool_error',
						'ms'     => (int) round( ( microtime( true ) - $started ) * 1000 ),
					)
				);
				return self::rpc_body( $id, $result );

			case 'resources/list':
				$result = array( 'resources' => self::resources() );
				break;

			case 'resources/templates/list':
				$result = array(
					'resourceTemplates' => array(
						array(
							'uriTemplate' => 'site://page/{slug}',
							'name'        => 'Page by slug',
							'description' => 'The full text of any indexed page or post, addressed by its slug.',
							'mimeType'    => 'text/markdown',
						),
					),
				);
				break;

			case 'resources/read':
				$uri    = isset( $params['uri'] ) ? (string) $params['uri'] : '';
				$result = self::read_resource( $uri, $auth );
				if ( is_wp_error( $result ) ) {
					return self::rpc_body( $id, null, array( 'code' => -32602, 'message' => $result->get_error_message() ) );
				}
				break;

			case 'prompts/list':
				$result = array( 'prompts' => self::prompts() );
				break;

			case 'prompts/get':
				$name   = isset( $params['name'] ) ? (string) $params['name'] : '';
				$result = self::get_prompt( $name );
				if ( is_wp_error( $result ) ) {
					return self::rpc_body( $id, null, array( 'code' => -32602, 'message' => $result->get_error_message() ) );
				}
				break;

			case 'completion/complete':
				$result = array( 'completion' => array( 'values' => array(), 'hasMore' => false ) );
				break;

			case 'logging/setLevel':
				$result = new stdClass();
				break;

			default:
				return self::rpc_body( $id, null, array( 'code' => -32601, 'message' => 'Unknown method: ' . $method ) );
		}

		VOM_Brain_Log::record(
			array(
				'method' => $method,
				'scope'  => $auth['scope'],
				'token_id' => $auth['token_id'],
				'status' => 'ok',
				'ms'     => (int) round( ( microtime( true ) - $started ) * 1000 ),
			)
		);

		return self::rpc_body( $id, $result );
	}

	/**
	 * The initialize handshake result.
	 *
	 * @param array $params Client params.
	 * @return array
	 */
	private static function initialize_result( $params ) {
		$requested = isset( $params['protocolVersion'] ) ? (string) $params['protocolVersion'] : '';
		$version   = in_array( $requested, self::supported_protocols(), true ) ? $requested : self::LATEST_PROTOCOL;

		$overview = VOM_Brain_Aggregator::overview();
		$name     = isset( $overview['business']['name'] ) ? $overview['business']['name'] : $overview['site']['name'];

		return array(
			'protocolVersion' => $version,
			'capabilities'    => array(
				'tools'     => array( 'listChanged' => false ),
				'resources' => array( 'subscribe' => false, 'listChanged' => false ),
				'prompts'   => array( 'listChanged' => false ),
				'logging'   => new stdClass(),
			),
			'serverInfo'      => array(
				'name'    => 'site-brain:' . wp_parse_url( home_url(), PHP_URL_HOST ),
				'title'   => $name . ' — Site Brain',
				'version' => VOM_BRAIN_VERSION,
			),
			'instructions'    => self::instructions( $overview ),
		);
	}

	/**
	 * The server instructions block: what this brain is and how to use it well.
	 *
	 * @param array $overview Cached aggregate.
	 * @return string
	 */
	private static function instructions( $overview ) {
		$biz   = isset( $overview['business'] ) ? $overview['business'] : array();
		$name  = isset( $biz['name'] ) ? $biz['name'] : $overview['site']['name'];
		$lines = array();

		$lines[] = sprintf(
			'This server is the live knowledge base of %s (%s). It holds %d indexed documents and %d retrievable passages, last updated %s.',
			$name,
			$overview['site']['url'],
			(int) $overview['what_i_know']['documents'],
			(int) $overview['what_i_know']['retrievable_passages'],
			$overview['what_i_know']['last_content_update'] ? $overview['what_i_know']['last_content_update'] : 'unknown'
		);
		$lines[] = '';
		$lines[] = 'Start with site_overview to learn what the site is about, then use search_site for anything factual and get_page when you need a full document. Always cite the URL you used.';

		if ( ! empty( $overview['answer_guidelines']['rules'] ) ) {
			$lines[] = '';
			foreach ( $overview['answer_guidelines']['rules'] as $rule ) {
				$lines[] = '- ' . $rule;
			}
		}

		return implode( "\n", $lines );
	}

	/* --------------------------------------------------------------------- */
	/* Tools                                                                   */
	/* --------------------------------------------------------------------- */

	/**
	 * The tool catalogue.
	 *
	 * @param string $scope Caller scope.
	 * @return array
	 */
	public static function tools( $scope = 'read' ) {
		$types = VOM_Brain_Settings::indexed_post_types( 'full' === $scope );

		$tools = array();

		$tools['site_overview'] = array(
			'name'        => 'site_overview',
			'title'       => 'Site overview',
			'description' => 'Everything the website knows about itself in one call: identity, business facts, contact details, opening hours, key pages, navigation, topic map, FAQs and content statistics. Call this first.',
			'inputSchema' => array(
				'type'       => 'object',
				'properties' => new stdClass(),
			),
		);

		$tools['search_site'] = array(
			'name'        => 'search_site',
			'title'       => 'Search the site',
			'description' => 'Retrieve the passages of this website most relevant to a question. This is the primary grounding call for answering anything factual. Returns passage text with the page title and URL to cite.',
			'inputSchema' => array(
				'type'       => 'object',
				'properties' => array(
					'query'      => array(
						'type'        => 'string',
						'description' => 'The question or keywords to look for.',
					),
					'limit'      => array(
						'type'        => 'integer',
						'description' => 'How many passages to return (1-50).',
						'default'     => 8,
						'minimum'     => 1,
						'maximum'     => 50,
					),
					'post_types' => array(
						'type'        => 'array',
						'description' => 'Restrict to these content types. Available: ' . implode( ', ', $types ) . '.',
						'items'       => array( 'type' => 'string' ),
					),
				),
				'required'   => array( 'query' ),
			),
		);

		$tools['get_page'] = array(
			'name'        => 'get_page',
			'title'       => 'Get a page',
			'description' => 'The full text of one page or post, addressed by URL, slug or numeric id. Use after search_site when a passage is not enough.',
			'inputSchema' => array(
				'type'       => 'object',
				'properties' => array(
					'url'    => array(
						'type'        => 'string',
						'description' => 'Full URL of the page.',
					),
					'slug'   => array(
						'type'        => 'string',
						'description' => 'Slug of the page.',
					),
					'id'     => array(
						'type'        => 'integer',
						'description' => 'WordPress post id.',
					),
					'format' => array(
						'type'        => 'string',
						'description' => 'markdown (default) or text.',
						'enum'        => array( 'markdown', 'text' ),
						'default'     => 'markdown',
					),
				),
			),
		);

		$tools['list_content'] = array(
			'name'        => 'list_content',
			'title'       => 'Browse content',
			'description' => 'Browse the index by content type, taxonomy term or title keyword, newest first. Use to enumerate what exists rather than to answer a question.',
			'inputSchema' => array(
				'type'       => 'object',
				'properties' => array(
					'post_type' => array(
						'type'        => 'string',
						'description' => 'One of: ' . implode( ', ', $types ) . '.',
					),
					'taxonomy'  => array(
						'type'        => 'string',
						'description' => 'Taxonomy slug to filter on, e.g. category.',
					),
					'term'      => array(
						'type'        => 'string',
						'description' => 'Term name within that taxonomy.',
					),
					'search'    => array(
						'type'        => 'string',
						'description' => 'Match against titles and summaries.',
					),
					'page'      => array(
						'type'    => 'integer',
						'default' => 1,
					),
					'per_page'  => array(
						'type'    => 'integer',
						'default' => 20,
						'maximum' => 200,
					),
				),
			),
		);

		$tools['contact_info'] = array(
			'name'        => 'contact_info',
			'title'       => 'Contact and hours',
			'description' => 'The verified contact channels, address, opening hours, booking link and service area, exactly as the site owner entered them. Use this instead of guessing from page text.',
			'inputSchema' => array(
				'type'       => 'object',
				'properties' => new stdClass(),
			),
		);

		$tools['get_faqs'] = array(
			'name'        => 'get_faqs',
			'title'       => 'Frequently asked questions',
			'description' => 'The site owner\'s curated question and answer pairs, optionally filtered by keyword.',
			'inputSchema' => array(
				'type'       => 'object',
				'properties' => array(
					'query' => array(
						'type'        => 'string',
						'description' => 'Optional keyword filter.',
					),
				),
			),
		);

		$tools['recent_changes'] = array(
			'name'        => 'recent_changes',
			'title'       => 'Recent changes',
			'description' => 'Documents added, updated or removed since a timestamp, with content hashes. Built for indexing agents that keep their own copy in sync without recrawling the whole site.',
			'inputSchema' => array(
				'type'       => 'object',
				'properties' => array(
					'since' => array(
						'type'        => 'string',
						'description' => 'ISO 8601 timestamp. Omit for the last 7 days.',
					),
					'limit' => array(
						'type'    => 'integer',
						'default' => 50,
						'maximum' => 200,
					),
				),
			),
		);

		$tools['site_map'] = array(
			'name'        => 'site_map',
			'title'       => 'Site map',
			'description' => 'Every indexed URL with its title, type, last-modified date, word count and content hash. Use to plan a crawl or to verify coverage.',
			'inputSchema' => array(
				'type'       => 'object',
				'properties' => array(
					'post_type' => array( 'type' => 'string' ),
					'limit'     => array(
						'type'    => 'integer',
						'default' => 200,
						'maximum' => 1000,
					),
					'offset'    => array(
						'type'    => 'integer',
						'default' => 0,
					),
				),
			),
		);

		if ( VOM_Brain_Settings::get( 'include_woo' ) && function_exists( 'wc_get_product' ) && in_array( 'product', $types, true ) ) {
			$tools['list_products'] = array(
				'name'        => 'list_products',
				'title'       => 'List products',
				'description' => 'The WooCommerce catalogue with live prices, SKUs and stock status, filterable by keyword or category.',
				'inputSchema' => array(
					'type'       => 'object',
					'properties' => array(
						'query'    => array(
							'type'        => 'string',
							'description' => 'Keyword to match in product titles and descriptions.',
						),
						'category' => array(
							'type'        => 'string',
							'description' => 'Product category name.',
						),
						'in_stock' => array(
							'type'        => 'boolean',
							'description' => 'Only return products currently in stock.',
						),
						'limit'    => array(
							'type'    => 'integer',
							'default' => 20,
							'maximum' => 100,
						),
					),
				),
			);
		}

		/**
		 * Filter the advertised MCP tool set.
		 *
		 * @param array  $tools Tool definitions keyed by name.
		 * @param string $scope Caller scope.
		 */
		return apply_filters( 'vom_brain_tools', $tools, $scope );
	}

	/**
	 * Execute one tool.
	 *
	 * @param string $name Tool name.
	 * @param array  $args Arguments.
	 * @param array  $auth Auth context.
	 * @return array MCP tool result.
	 */
	public static function call_tool( $name, $args, $auth ) {
		$catalogue = self::tools( $auth['scope'] );
		if ( ! isset( $catalogue[ $name ] ) ) {
			return self::tool_error( 'Unknown tool: ' . $name . '. Call tools/list for the current catalogue.' );
		}

		$scope = $auth['scope'];

		switch ( $name ) {
			case 'site_overview':
				$data = VOM_Brain_Aggregator::overview();
				return self::tool_result( self::render_overview( $data ), $data );

			case 'search_site':
				$query = isset( $args['query'] ) ? (string) $args['query'] : '';
				if ( '' === trim( $query ) ) {
					return self::tool_error( 'search_site needs a non-empty "query".' );
				}
				$hits = VOM_Brain_Index::search(
					$query,
					array(
						'limit'      => isset( $args['limit'] ) ? (int) $args['limit'] : 8,
						'post_types' => isset( $args['post_types'] ) ? (array) $args['post_types'] : array(),
						'scope'      => $scope,
					)
				);
				$data = array(
					'query'   => $query,
					'count'   => count( $hits ),
					'results' => $hits,
				);
				return self::tool_result( self::render_search( $data ), $data );

			case 'get_page':
				$doc = VOM_Brain_Index::get_document(
					array(
						'id'    => isset( $args['id'] ) ? (int) $args['id'] : 0,
						'slug'  => isset( $args['slug'] ) ? (string) $args['slug'] : '',
						'url'   => isset( $args['url'] ) ? (string) $args['url'] : '',
						'scope' => $scope,
					)
				);
				if ( ! $doc ) {
					return self::tool_error( 'No indexed page matched that url, slug or id. Try search_site or list_content first.' );
				}
				$format = isset( $args['format'] ) && 'text' === $args['format'] ? 'text' : 'markdown';
				$text   = '# ' . $doc['title'] . "\n" . $doc['url'] . "\n\n" . ( 'text' === $format ? $doc['text'] : $doc['markdown'] );
				return self::tool_result( self::truncate( $text ), $doc );

			case 'list_content':
				$data = VOM_Brain_Index::list_documents(
					array(
						'post_type' => isset( $args['post_type'] ) ? (string) $args['post_type'] : '',
						'taxonomy'  => isset( $args['taxonomy'] ) ? (string) $args['taxonomy'] : '',
						'term'      => isset( $args['term'] ) ? (string) $args['term'] : '',
						'search'    => isset( $args['search'] ) ? (string) $args['search'] : '',
						'page'      => isset( $args['page'] ) ? (int) $args['page'] : 1,
						'per_page'  => isset( $args['per_page'] ) ? (int) $args['per_page'] : 20,
						'scope'     => $scope,
					)
				);
				return self::tool_result( self::render_listing( $data ), $data );

			case 'contact_info':
				$biz  = VOM_Brain_Settings::business();
				$data = array(
					'business'   => $biz,
					'escalation' => VOM_Brain_Settings::get( 'escalation', '' ),
					'source'     => 'Entered directly by the site owner.',
				);
				return self::tool_result( self::render_kv( $biz ), $data );

			case 'get_faqs':
				$faqs   = VOM_Brain_Aggregator::overview()['faqs'];
				$filter = isset( $args['query'] ) ? trim( (string) $args['query'] ) : '';
				if ( '' !== $filter ) {
					$needle = mb_strtolower( $filter );
					$faqs   = array_values(
						array_filter(
							$faqs,
							function ( $faq ) use ( $needle ) {
								return false !== mb_strpos( mb_strtolower( $faq['question'] . ' ' . $faq['answer'] ), $needle );
							}
						)
					);
				}
				$data = array(
					'count' => count( $faqs ),
					'faqs'  => $faqs,
				);
				return self::tool_result( self::render_faqs( $faqs ), $data );

			case 'recent_changes':
				$since = isset( $args['since'] ) && $args['since'] ? (string) $args['since'] : gmdate( 'c', time() - 7 * DAY_IN_SECONDS );
				$limit = isset( $args['limit'] ) ? (int) $args['limit'] : 50;
				$list  = VOM_Brain_Index::list_documents(
					array(
						'since'    => $since,
						'per_page' => $limit,
						'scope'    => $scope,
					)
				);
				$data = array(
					'since'   => $since,
					'updated' => $list['items'],
					'removed' => VOM_Brain_Index::removed_since( $since ),
					'total'   => $list['total'],
				);
				return self::tool_result( self::render_changes( $data ), $data );

			case 'site_map':
				$data = VOM_Brain_Aggregator::sitemap(
					array(
						'post_type' => isset( $args['post_type'] ) ? (string) $args['post_type'] : '',
						'limit'     => isset( $args['limit'] ) ? (int) $args['limit'] : 200,
						'offset'    => isset( $args['offset'] ) ? (int) $args['offset'] : 0,
						'scope'     => $scope,
					)
				);
				$lines = array();
				foreach ( $data['urls'] as $u ) {
					$lines[] = $u['url'] . ' — ' . $u['title'] . ' (' . $u['type'] . ', modified ' . $u['lastmod'] . ')';
				}
				return self::tool_result( self::truncate( implode( "\n", $lines ) ), $data );

			case 'list_products':
				$data = self::products( $args, $scope );
				return self::tool_result( self::render_products( $data ), $data );
		}

		/**
		 * Handle a tool added through the vom_brain_tools filter.
		 *
		 * @param array|null $result Tool result, or null when unhandled.
		 * @param string     $name   Tool name.
		 * @param array      $args   Arguments.
		 * @param array      $auth   Auth context.
		 */
		$custom = apply_filters( 'vom_brain_call_tool', null, $name, $args, $auth );
		if ( is_array( $custom ) ) {
			return $custom;
		}

		return self::tool_error( 'Tool "' . $name . '" is advertised but has no handler.' );
	}

	/**
	 * WooCommerce catalogue query.
	 *
	 * @param array  $args  Tool arguments.
	 * @param string $scope Caller scope.
	 * @return array
	 */
	private static function products( $args, $scope ) {
		$listing = VOM_Brain_Index::list_documents(
			array(
				'post_type' => 'product',
				'search'    => isset( $args['query'] ) ? (string) $args['query'] : '',
				'taxonomy'  => isset( $args['category'] ) && $args['category'] ? 'product_cat' : '',
				'term'      => isset( $args['category'] ) ? (string) $args['category'] : '',
				'per_page'  => isset( $args['limit'] ) ? (int) $args['limit'] : 20,
				'scope'     => $scope,
			)
		);

		$in_stock_only = ! empty( $args['in_stock'] );
		$items         = array();

		foreach ( $listing['items'] as $item ) {
			$meta = isset( $item['meta'] ) ? $item['meta'] : array();
			if ( $in_stock_only && ( ! isset( $meta['stock_status'] ) || 'instock' !== $meta['stock_status'] ) ) {
				continue;
			}
			$items[] = array(
				'name'         => $item['title'],
				'url'          => $item['url'],
				'summary'      => $item['summary'],
				'sku'          => isset( $meta['sku'] ) ? $meta['sku'] : '',
				'price'        => isset( $meta['price'] ) ? $meta['price'] : '',
				'sale_price'   => isset( $meta['sale_price'] ) ? $meta['sale_price'] : '',
				'currency'     => isset( $meta['currency'] ) ? $meta['currency'] : '',
				'stock_status' => isset( $meta['stock_status'] ) ? $meta['stock_status'] : '',
				'categories'   => isset( $item['taxonomies']['product_cat'] ) ? $item['taxonomies']['product_cat'] : array(),
			);
		}

		return array(
			'count'    => count( $items ),
			'total'    => $listing['total'],
			'products' => $items,
		);
	}

	/* --------------------------------------------------------------------- */
	/* Resources                                                               */
	/* --------------------------------------------------------------------- */

	/**
	 * Static resource list.
	 *
	 * @return array
	 */
	public static function resources() {
		return array(
			array(
				'uri'         => 'site://overview',
				'name'        => 'Site overview',
				'description' => 'The complete aggregated profile of this website as JSON.',
				'mimeType'    => 'application/json',
			),
			array(
				'uri'         => 'site://facts',
				'name'        => 'Business facts',
				'description' => 'Owner-verified contact details, hours and service area.',
				'mimeType'    => 'application/json',
			),
			array(
				'uri'         => 'site://llms.txt',
				'name'        => 'llms.txt',
				'description' => 'The link-first briefing this site publishes for language models.',
				'mimeType'    => 'text/markdown',
			),
			array(
				'uri'         => 'site://sitemap',
				'name'        => 'Site map',
				'description' => 'Every indexed URL with last-modified stamps and content hashes.',
				'mimeType'    => 'application/json',
			),
			array(
				'uri'         => 'site://guidelines',
				'name'        => 'Answering guidelines',
				'description' => 'How the site owner wants an assistant to speak on their behalf.',
				'mimeType'    => 'application/json',
			),
		);
	}

	/**
	 * Read one resource.
	 *
	 * @param string $uri  Resource URI.
	 * @param array  $auth Auth context.
	 * @return array|WP_Error
	 */
	public static function read_resource( $uri, $auth ) {
		$json = function ( $uri, $data ) {
			return array(
				'contents' => array(
					array(
						'uri'      => $uri,
						'mimeType' => 'application/json',
						'text'     => wp_json_encode( $data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE ),
					),
				),
			);
		};

		switch ( $uri ) {
			case 'site://overview':
				return $json( $uri, VOM_Brain_Aggregator::overview() );

			case 'site://facts':
				return $json( $uri, VOM_Brain_Settings::business() );

			case 'site://guidelines':
				return $json( $uri, VOM_Brain_Aggregator::guidelines() );

			case 'site://sitemap':
				return $json( $uri, VOM_Brain_Aggregator::sitemap( array( 'limit' => 1000, 'scope' => $auth['scope'] ) ) );

			case 'site://llms.txt':
				return array(
					'contents' => array(
						array(
							'uri'      => $uri,
							'mimeType' => 'text/markdown',
							'text'     => VOM_Brain_Aggregator::llms_txt(),
						),
					),
				);
		}

		if ( 0 === strpos( $uri, 'site://page/' ) ) {
			$slug = substr( $uri, strlen( 'site://page/' ) );
			$doc  = VOM_Brain_Index::get_document(
				array(
					'slug'  => $slug,
					'scope' => $auth['scope'],
				)
			);
			if ( ! $doc ) {
				return new WP_Error( 'not_found', 'No indexed page with slug "' . $slug . '".' );
			}
			return array(
				'contents' => array(
					array(
						'uri'      => $uri,
						'mimeType' => 'text/markdown',
						'text'     => '# ' . $doc['title'] . "\n" . $doc['url'] . "\n\n" . $doc['markdown'],
					),
				),
			);
		}

		return new WP_Error( 'unknown_resource', 'Unknown resource URI: ' . $uri );
	}

	/* --------------------------------------------------------------------- */
	/* Prompts                                                                 */
	/* --------------------------------------------------------------------- */

	/**
	 * Prompt catalogue.
	 *
	 * @return array
	 */
	public static function prompts() {
		return array(
			array(
				'name'        => 'site_assistant',
				'title'       => 'Answer as this site\'s assistant',
				'description' => 'Loads the site persona, rules and key facts so the model can answer visitor questions grounded in this website.',
				'arguments'   => array(
					array(
						'name'        => 'question',
						'description' => 'The visitor question to answer.',
						'required'    => false,
					),
				),
			),
			array(
				'name'        => 'summarize_site',
				'title'       => 'Summarize this website',
				'description' => 'Produces a briefing on what this organisation does, who it serves and what it offers.',
				'arguments'   => array(),
			),
		);
	}

	/**
	 * Build one prompt.
	 *
	 * @param string $name Prompt name.
	 * @return array|WP_Error
	 */
	public static function get_prompt( $name ) {
		$overview = VOM_Brain_Aggregator::overview();
		$rules    = $overview['answer_guidelines'];

		if ( 'site_assistant' === $name ) {
			$text = $rules['persona'] . "\n\n" . implode( "\n", array_map(
				function ( $r ) {
					return '- ' . $r;
				},
				$rules['rules']
			) ) . "\n\nVerified facts:\n" . self::render_kv( $overview['business'] );

			return array(
				'description' => 'Site assistant persona and grounding rules.',
				'messages'    => array(
					array(
						'role'    => 'user',
						'content' => array(
							'type' => 'text',
							'text' => $text,
						),
					),
				),
			);
		}

		if ( 'summarize_site' === $name ) {
			return array(
				'description' => 'Briefing request for this website.',
				'messages'    => array(
					array(
						'role'    => 'user',
						'content' => array(
							'type' => 'text',
							'text' => "Call site_overview, then list_content, then read the three most important pages with get_page. Write a briefing covering: what this organisation does, who it serves, what it sells or offers, how to contact it, and anything a first-time visitor should know.",
						),
					),
				),
			);
		}

		return new WP_Error( 'unknown_prompt', 'Unknown prompt: ' . $name );
	}

	/* --------------------------------------------------------------------- */
	/* Rendering helpers                                                       */
	/* --------------------------------------------------------------------- */

	/**
	 * Human-readable rendering of the overview.
	 *
	 * @param array $o Overview.
	 * @return string
	 */
	private static function render_overview( $o ) {
		$lines = array();
		$biz   = isset( $o['business'] ) ? $o['business'] : array();

		$lines[] = '# ' . ( isset( $biz['name'] ) ? $biz['name'] : $o['site']['name'] );
		if ( ! empty( $biz['tagline'] ) ) {
			$lines[] = $biz['tagline'];
		}
		$lines[] = $o['site']['url'];
		$lines[] = '';

		if ( ! empty( $biz['description'] ) ) {
			$lines[] = $biz['description'];
			$lines[] = '';
		}

		$lines[] = '## Verified facts';
		$lines[] = self::render_kv( $biz );
		$lines[] = '';

		$lines[] = '## What this brain holds';
		$lines[] = sprintf(
			'%d documents, %d passages, %s words. Last content update: %s.',
			(int) $o['what_i_know']['documents'],
			(int) $o['what_i_know']['retrievable_passages'],
			number_format_i18n( (int) $o['what_i_know']['total_words'] ),
			$o['what_i_know']['last_content_update'] ? $o['what_i_know']['last_content_update'] : 'unknown'
		);
		foreach ( (array) $o['what_i_know']['by_type'] as $type => $n ) {
			$lines[] = '- ' . $type . ': ' . (int) $n;
		}
		$lines[] = '';

		if ( ! empty( $o['key_pages'] ) ) {
			$lines[] = '## Key pages';
			foreach ( $o['key_pages'] as $page ) {
				$lines[] = '- ' . $page['title'] . ' — ' . $page['url'];
			}
			$lines[] = '';
		}

		if ( ! empty( $o['topics'] ) ) {
			$lines[] = '## Topics';
			foreach ( $o['topics'] as $tax => $terms ) {
				$names = array();
				foreach ( array_slice( $terms, 0, 15 ) as $term ) {
					$names[] = $term['name'] . ' (' . $term['count'] . ')';
				}
				$lines[] = '- ' . $tax . ': ' . implode( ', ', $names );
			}
			$lines[] = '';
		}

		if ( ! empty( $o['commerce'] ) ) {
			$lines[] = '## Shop';
			$lines[] = self::render_kv( $o['commerce'] );
			$lines[] = '';
		}

		return self::truncate( implode( "\n", $lines ) );
	}

	/**
	 * Human-readable rendering of search hits, with citations.
	 *
	 * @param array $data Search payload.
	 * @return string
	 */
	private static function render_search( $data ) {
		if ( ! $data['results'] ) {
			return 'No passages matched "' . $data['query'] . '". Try broader keywords, or call list_content to see what exists.';
		}
		$lines = array( 'Found ' . $data['count'] . ' passages for "' . $data['query'] . '".', '' );
		$n     = 1;
		foreach ( $data['results'] as $hit ) {
			$head    = $hit['title'] . ( $hit['heading'] ? ' › ' . $hit['heading'] : '' );
			$lines[] = '[' . $n . '] ' . $head;
			$lines[] = $hit['url'];
			$lines[] = $hit['passage'];
			$lines[] = '';
			$n++;
		}
		return self::truncate( implode( "\n", $lines ) );
	}

	/**
	 * Human-readable rendering of a listing.
	 *
	 * @param array $data Listing payload.
	 * @return string
	 */
	private static function render_listing( $data ) {
		if ( ! $data['items'] ) {
			return 'Nothing matched. ' . $data['total'] . ' documents exist in total.';
		}
		$lines = array( sprintf( 'Showing %d of %d (page %d of %d).', count( $data['items'] ), $data['total'], $data['page'], $data['pages'] ), '' );
		foreach ( $data['items'] as $item ) {
			$lines[] = '- ' . $item['title'] . ' [' . $item['type'] . '] — ' . $item['url'];
			if ( $item['summary'] ) {
				$lines[] = '  ' . $item['summary'];
			}
		}
		return self::truncate( implode( "\n", $lines ) );
	}

	/**
	 * Human-readable rendering of FAQs.
	 *
	 * @param array $faqs FAQ pairs.
	 * @return string
	 */
	private static function render_faqs( $faqs ) {
		if ( ! $faqs ) {
			return 'This site has no curated FAQs. Use search_site instead.';
		}
		$lines = array();
		foreach ( $faqs as $faq ) {
			$lines[] = 'Q: ' . $faq['question'];
			$lines[] = 'A: ' . $faq['answer'];
			if ( ! empty( $faq['url'] ) ) {
				$lines[] = '   ' . $faq['url'];
			}
			$lines[] = '';
		}
		return self::truncate( implode( "\n", $lines ) );
	}

	/**
	 * Human-readable rendering of the change feed.
	 *
	 * @param array $data Change payload.
	 * @return string
	 */
	private static function render_changes( $data ) {
		$lines = array( 'Changes since ' . $data['since'] . ':', '' );
		if ( $data['updated'] ) {
			$lines[] = 'Added or updated (' . count( $data['updated'] ) . '):';
			foreach ( $data['updated'] as $item ) {
				$lines[] = '- ' . $item['modified'] . ' ' . $item['url'] . ' (hash ' . substr( $item['hash'], 0, 8 ) . ')';
			}
			$lines[] = '';
		}
		if ( $data['removed'] ) {
			$lines[] = 'Removed (' . count( $data['removed'] ) . '):';
			foreach ( $data['removed'] as $item ) {
				$lines[] = '- ' . $item['removed_at'] . ' ' . $item['url'];
			}
		}
		if ( ! $data['updated'] && ! $data['removed'] ) {
			$lines[] = 'Nothing changed.';
		}
		return self::truncate( implode( "\n", $lines ) );
	}

	/**
	 * Human-readable rendering of products.
	 *
	 * @param array $data Product payload.
	 * @return string
	 */
	private static function render_products( $data ) {
		if ( ! $data['products'] ) {
			return 'No products matched.';
		}
		$lines = array( $data['count'] . ' of ' . $data['total'] . ' products.', '' );
		foreach ( $data['products'] as $p ) {
			$price   = $p['price'] ? trim( $p['currency'] . ' ' . $p['price'] ) : 'price not published';
			$lines[] = '- ' . $p['name'] . ' — ' . $price . ' — ' . ( $p['stock_status'] ? $p['stock_status'] : 'stock unknown' );
			$lines[] = '  ' . $p['url'];
		}
		return self::truncate( implode( "\n", $lines ) );
	}

	/**
	 * Flatten a shallow associative array into readable lines.
	 *
	 * @param array $data Data.
	 * @return string
	 */
	private static function render_kv( $data ) {
		$lines = array();
		foreach ( (array) $data as $key => $value ) {
			if ( is_array( $value ) ) {
				$flat = array();
				foreach ( $value as $k => $v ) {
					$flat[] = is_int( $k ) ? (string) $v : $k . ' ' . $v;
				}
				$value = implode( '; ', $flat );
			}
			if ( '' === $value || null === $value ) {
				continue;
			}
			$lines[] = ucwords( str_replace( '_', ' ', $key ) ) . ': ' . $value;
		}
		return implode( "\n", $lines );
	}

	/**
	 * Keep tool text within the configured response budget.
	 *
	 * @param string $text Text.
	 * @return string
	 */
	private static function truncate( $text ) {
		$max = (int) VOM_Brain_Settings::get( 'max_response_chars', 12000 );
		if ( strlen( $text ) <= $max ) {
			return $text;
		}
		return substr( $text, 0, $max ) . "\n\n… truncated at " . $max . ' characters. Narrow the request for more detail.';
	}

	/* --------------------------------------------------------------------- */
	/* JSON-RPC envelopes                                                      */
	/* --------------------------------------------------------------------- */

	/**
	 * Successful tool result envelope.
	 *
	 * @param string $text       Readable rendering.
	 * @param mixed  $structured Structured payload.
	 * @return array
	 */
	private static function tool_result( $text, $structured = null ) {
		$out = array(
			'content' => array(
				array(
					'type' => 'text',
					'text' => (string) $text,
				),
			),
			'isError' => false,
		);
		if ( null !== $structured ) {
			$out['structuredContent'] = $structured;
		}
		return $out;
	}

	/**
	 * Tool-level error envelope. The call succeeded, the tool did not.
	 *
	 * @param string $message Message for the model.
	 * @return array
	 */
	private static function tool_error( $message ) {
		return array(
			'content' => array(
				array(
					'type' => 'text',
					'text' => $message,
				),
			),
			'isError' => true,
		);
	}

	/**
	 * Build a JSON-RPC response body.
	 *
	 * @param mixed      $id     Request id.
	 * @param mixed      $result Result payload.
	 * @param array|null $error  Error payload.
	 * @return array
	 */
	private static function rpc_body( $id, $result = null, $error = null ) {
		$body = array(
			'jsonrpc' => '2.0',
			'id'      => $id,
		);
		if ( $error ) {
			$body['error'] = $error;
		} else {
			$body['result'] = null === $result ? new stdClass() : $result;
		}
		return $body;
	}

	/**
	 * Transport-level error response.
	 *
	 * @param mixed  $id      Request id.
	 * @param int    $code    JSON-RPC error code.
	 * @param string $message Message.
	 * @param int    $status  HTTP status.
	 * @return WP_REST_Response
	 */
	private static function rpc_error( $id, $code, $message, $status = 400 ) {
		return new WP_REST_Response( self::rpc_body( $id, null, array( 'code' => $code, 'message' => $message ) ), $status );
	}
}
