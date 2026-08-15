<?php
if ( ! defined( 'ABSPATH' ) ) { exit; }

// Handle CSV export.
if ( isset( $_GET['action'] ) && $_GET['action'] === 'export' && current_user_can( 'manage_options' ) && check_admin_referer( 'voc_export_leads' ) ) {
    $leads = VOC_Admin::fetch_leads( 5000 );
    nocache_headers();
    header( 'Content-Type: text/csv; charset=utf-8' );
    header( 'Content-Disposition: attachment; filename="vision-outreach-leads-' . date( 'Y-m-d' ) . '.csv"' );
    $out = fopen( 'php://output', 'w' );
    fputcsv( $out, [ 'ID', 'Name', 'Email', 'Message', 'Page', 'IP', 'Email status', 'Created' ] );
    foreach ( $leads as $r ) {
        fputcsv( $out, [
            $r['id'], $r['name'], $r['email'], $r['message'], $r['page_url'], $r['ip_address'], $r['email_status'], $r['created_at'],
        ] );
    }
    fclose( $out );
    exit;
}

$leads = VOC_Admin::fetch_leads( 200 );
$count = count( $leads );
?>
<div class="wrap">
    <h1>Vision Outreach Chatbot — Leads
        <a href="<?php echo esc_url( wp_nonce_url( admin_url( 'admin.php?page=voc-leads&action=export' ), 'voc_export_leads' ) ); ?>" class="page-title-action">Export CSV</a>
    </h1>
    <p><?php echo (int) $count; ?> lead<?php echo $count === 1 ? '' : 's'; ?> shown (newest first, last 200).</p>

    <table class="widefat striped fixed">
        <thead>
            <tr>
                <th style="width: 60px;">ID</th>
                <th style="width: 160px;">Date</th>
                <th style="width: 160px;">Name</th>
                <th style="width: 220px;">Email</th>
                <th>Message</th>
                <th style="width: 200px;">Page</th>
                <th style="width: 100px;">Email</th>
            </tr>
        </thead>
        <tbody>
        <?php if ( empty( $leads ) ) : ?>
            <tr><td colspan="7"><em>No leads captured yet.</em></td></tr>
        <?php else : foreach ( $leads as $r ) : ?>
            <tr>
                <td>#<?php echo (int) $r['id']; ?></td>
                <td><?php echo esc_html( $r['created_at'] ); ?></td>
                <td><?php echo esc_html( $r['name'] ); ?></td>
                <td><a href="mailto:<?php echo esc_attr( $r['email'] ); ?>"><?php echo esc_html( $r['email'] ); ?></a></td>
                <td><?php echo esc_html( $r['message'] ); ?></td>
                <td><?php echo $r['page_url'] ? '<a href="' . esc_url( $r['page_url'] ) . '" target="_blank">' . esc_html( wp_parse_url( $r['page_url'], PHP_URL_PATH ) ?: $r['page_url'] ) . '</a>' : '—'; ?></td>
                <td><?php echo esc_html( $r['email_status'] ); ?></td>
            </tr>
        <?php endforeach; endif; ?>
        </tbody>
    </table>
</div>
