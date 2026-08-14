<?php
/**
 * Plain REST surface for crawlers and indexing agents that do not speak MCP.
 * Same data, same auth, no JSON-RPC envelope.
 *
 * @package VOM_Site_Brain
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class VOM_Brain_REST {

	/**
	 * Register hooks.
	 */
	public static function hooks() {
		add_action( 'rest_api_init', array( __CLASS__, 'register_routes' ) );
	}

	/**
	 * Register every read endpoint.
	 */
	public static function register_routes() {
		$routes = array(
			'manifest'  => 'manifest',
			'overview'  => 'overview',
			'search'    => 'search',
			'documents' => 'documents',
			'document'  => 'document',
			'changes'   => 'changes',
			'sitemap'   => 'sitemap',
			'faqs'      => 'faqs',
		);

		foreach ( $routes as $path => $method ) {
			register_rest_route(
				VOM_BRAIN_NS,
				'/' . $path,
				array(
					'methods'             => 'GET',
					'callback'            => array( __CLASS__, $method ),
					'permission_callback' => '__return_true',
				)
			);
		}
	}

	/**
	 * Gate a request, returning the auth context or an error response.
	 *
	 * @param WP_REST_Request $request Request.
	 * @param string          $label   Log label.
	 * @return array|WP_REST_Response
	 */
	private static function gate( $request, $label ) {
		$auth = VOM_Brain_Auth::authenticate( $request );
		if ( ! $auth['ok'] ) {
			VOM_Brain_Log::record(
				array(
					'method' => 'rest/' . $label,
					'status' => 'denied',
				)
			);
			return new WP_REST_Response(
				array(
					'error' => $auth['reason'],
					'hint'  => 'Send Authorization: Bearer <token>, or ask the site owner to enable anonymous read access.',
				),
				401
			);
		}
		VOM_Brain_Log::record(
			array(
				'method'   => 'rest/' . $label,
				'tool'     => 'rest/' . $label,
				'query'    => (string) $request->get_param( 'q' ),
				'scope'    => $auth['scope'],
				'token_id' => $auth['token_id'],
				'status'   => 'ok',
			)
		);
		return $auth;
	}

	/**
	 * Capability document — what this site publishes and how to consume it.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response
	 */
	public static function manifest( $request ) {
		unset( $request );
		$overview = VOM_Brain_Aggregator::overview();
		$tools    = array();
		foreach ( VOM_Brain_MCP_Server::tools( 'read' ) as $tool ) {
			$tools[] = array(
				'name'        => $tool['name'],
				'description' => $tool['description'],
			);
		}

		return new WP_REST_Response(
			array(
				'name'        => isset( $overview['business']['name'] ) ? $overview['business']['name'] : $overview['site']['name'],
				'site'        => $overview['site'],
				'generator'   => 'VOM Site Brain ' . VOM_BRAIN_VERSION,
				'generated_at'=> $overview['generated_at'],
				'mcp'         => array(
					'transport'       => 'streamable-http',
					'url'             => rest_url( VOM_BRAIN_NS . '/mcp' ),
					'protocolVersion' => VOM_Brain_MCP_Server::LATEST_PROTOCOL,
					'authentication'  => VOM_Brain_Settings::get( 'public_read' ) ? 'none required (bearer optional)' : 'bearer token required',
					'tools'           => $tools,
				),
				'rest'        => $overview['endpoints'],
				'statistics'  => $overview['what_i_know'],
				'usage_policy'=> 'Public content only. Attribute answers to the source URL returned with each passage.',
			),
			200
		);
	}

	/**
	 * The aggregated site profile.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response
	 */
	public static function overview( $request ) {
		$auth = self::gate( $request, 'overview' );
		if ( $auth instanceof WP_REST_Response ) {
			return $auth;
		}
		return new WP_REST_Response( VOM_Brain_Aggregator::overview(), 200 );
	}

	/**
	 * Passage search. ?q=&limit=&type=
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response
	 */
	public static function search( $request ) {
		$auth = self::gate( $request, 'search' );
		if ( $auth instanceof WP_REST_Response ) {
			return $auth;
		}

		$query = (string) $request->get_param( 'q' );
		if ( '' === trim( $query ) ) {
			return new WP_REST_Response( array( 'error' => 'Pass a query as ?q=' ), 400 );
		}

		$types = $request->get_param( 'type' );
		$hits  = VOM_Brain_Index::search(
			$query,
			array(
				'limit'      => (int) $request->get_param( 'limit' ) ? (int) $request->get_param( 'limit' ) : 8,
				'post_types' => $types ? array_map( 'sanitize_key', explode( ',', (string) $types ) ) : array(),
				'scope'      => $auth['scope'],
			)
		);

		return new WP_REST_Response(
			array(
				'query'   => $query,
				'count'   => count( $hits ),
				'results' => $hits,
			),
			200
		);
	}

	/**
	 * Paginated document dump, optionally incremental via ?since=.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response
	 */
	public static function documents( $request ) {
		$auth = self::gate( $request, 'documents' );
		if ( $auth instanceof WP_REST_Response ) {
			return $auth;
		}

		$listing = VOM_Brain_Index::list_documents(
			array(
				'post_type' => (string) $request->get_param( 'type' ),
				'taxonomy'  => (string) $request->get_param( 'taxonomy' ),
				'term'      => (string) $request->get_param( 'term' ),
				'search'    => (string) $request->get_param( 'search' ),
				'since'     => (string) $request->get_param( 'since' ),
				'page'      => (int) $request->get_param( 'page' ) ? (int) $request->get_param( 'page' ) : 1,
				'per_page'  => (int) $request->get_param( 'per_page' ) ? (int) $request->get_param( 'per_page' ) : 20,
				'scope'     => $auth['scope'],
			)
		);

		// Optionally inline the body text, for one-shot ingestion.
		if ( $request->get_param( 'full' ) ) {
			foreach ( $listing['items'] as $i => $item ) {
				$doc = VOM_Brain_Index::get_document(
					array(
						'id'    => $item['id'],
						'scope' => $auth['scope'],
					)
				);
				if ( $doc ) {
					$listing['items'][ $i ]['text'] = $doc['text'];
				}
			}
		}

		return new WP_REST_Response( $listing, 200 );
	}

	/**
	 * One document, by ?id= / ?slug= / ?url=.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response
	 */
	public static function document( $request ) {
		$auth = self::gate( $request, 'document' );
		if ( $auth instanceof WP_REST_Response ) {
			return $auth;
		}

		$doc = VOM_Brain_Index::get_document(
			array(
				'id'    => (int) $request->get_param( 'id' ),
				'slug'  => (string) $request->get_param( 'slug' ),
				'url'   => (string) $request->get_param( 'url' ),
				'scope' => $auth['scope'],
			)
		);

		if ( ! $doc ) {
			return new WP_REST_Response( array( 'error' => 'Not found in the index.' ), 404 );
		}
		return new WP_REST_Response( $doc, 200 );
	}

	/**
	 * Incremental change feed.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response
	 */
	public static function changes( $request ) {
		$auth = self::gate( $request, 'changes' );
		if ( $auth instanceof WP_REST_Response ) {
			return $auth;
		}

		$since = (string) $request->get_param( 'since' );
		if ( '' === $since ) {
			$since = gmdate( 'c', time() - 7 * DAY_IN_SECONDS );
		}
		$limit = (int) $request->get_param( 'limit' ) ? (int) $request->get_param( 'limit' ) : 100;

		$listing = VOM_Brain_Index::list_documents(
			array(
				'since'    => $since,
				'per_page' => $limit,
				'scope'    => $auth['scope'],
			)
		);

		return new WP_REST_Response(
			array(
				'since'   => $since,
				'now'     => gmdate( 'c' ),
				'updated' => $listing['items'],
				'removed' => VOM_Brain_Index::removed_since( $since ),
				'total'   => $listing['total'],
			),
			200
		);
	}

	/**
	 * URL inventory.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response
	 */
	public static function sitemap( $request ) {
		$auth = self::gate( $request, 'sitemap' );
		if ( $auth instanceof WP_REST_Response ) {
			return $auth;
		}

		return new WP_REST_Response(
			VOM_Brain_Aggregator::sitemap(
				array(
					'post_type' => (string) $request->get_param( 'type' ),
					'limit'     => (int) $request->get_param( 'limit' ) ? (int) $request->get_param( 'limit' ) : 500,
					'offset'    => (int) $request->get_param( 'offset' ),
					'scope'     => $auth['scope'],
				)
			),
			200
		);
	}

	/**
	 * Curated FAQs.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response
	 */
	public static function faqs( $request ) {
		$auth = self::gate( $request, 'faqs' );
		if ( $auth instanceof WP_REST_Response ) {
			return $auth;
		}
		$faqs = VOM_Brain_Aggregator::overview()['faqs'];
		return new WP_REST_Response(
			array(
				'count' => count( $faqs ),
				'faqs'  => $faqs,
			),
			200
		);
	}
}
