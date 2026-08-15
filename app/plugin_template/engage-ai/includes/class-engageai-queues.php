<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * The in/out queue strip that sits at the top of every workflow page.
 *
 * Each page owns one step of the run, and the two questions that matter on
 * arriving at it are the same every time: what has landed here that I have not
 * dealt with, and what has already left. Before this, both were only knowable
 * by opening the page and reading the whole list - so work stalled silently. A
 * campaign piece scheduled for last Tuesday that nobody wrote looked exactly
 * like a campaign piece nobody had got to yet.
 *
 * Three buckets, not two. "In" and "out" are the queues the operator asked for;
 * "stuck" is the one that makes the promise true. Anything that belongs to a
 * stage but matches none of its expected states lands there and is shown in
 * red, rather than being filtered out of both queues and ceasing to exist. A
 * queue that only ever shows work it understands is precisely how work goes
 * missing.
 *
 * The whole board comes from one API call (GET /pipeline), cached briefly:
 * every page draws this, and an uncached round trip would put the API on the
 * critical path of the entire admin. The cache is deliberately short and is
 * dropped whenever this plugin changes anything, so it cannot show an operator
 * the state from before their own click.
 */
final class EngageAI_Queues
{
    private const CACHE_KEY = 'engageai_pipeline_';
    private const CACHE_TTL = 60; // seconds

    /** Where each stage sends its work next, so "out" can say where it went. */
    private const NEXT_PAGE = [
        'ideas' => ['engageai-studio', 'Content Studio'],
        'campaigns' => ['engageai-studio', 'Content Studio'],
        'studio' => ['engageai-channels', 'Channels'],
        'library' => ['engageai-channels', 'Channels'],
        'calendar' => ['engageai-studio', 'Content Studio'],
        'channels' => ['engageai-analytics', 'Performance'],
        'performance' => ['engageai-dashboard', 'Dashboard'],
    ];

    /**
     * Drops the cached board. Called after anything that changes the work, so
     * an operator never presses a button and sees the previous state staring
     * back at them.
     */
    public static function forget(): void
    {
        delete_transient(self::CACHE_KEY . get_current_user_id());
    }

    /** @return array|null the whole board, or null if it can't be fetched */
    public static function board(EngageAI_Api_Client $client): ?array
    {
        $org_id = (int) $client->get_organization_id();
        if (!$org_id || !$client->is_connected()) {
            return null;
        }

        $key = self::CACHE_KEY . get_current_user_id();
        $cached = get_transient($key);
        if (is_array($cached)) {
            return $cached;
        }

        $board = $client->get_pipeline($org_id);
        if (is_wp_error($board) || !is_array($board)) {
            return null;
        }

        set_transient($key, $board, self::CACHE_TTL);
        return $board;
    }

    /**
     * Renders the strip for one stage. Silent when the board can't be read -
     * a page that still works without its queues should still render, and an
     * API blip is not a reason to show an empty queue that looks like "you
     * have nothing to do".
     *
     * @param string $stage one of the keys in the API's pipeline.stages
     */
    public static function render(string $stage, EngageAI_Api_Client $client): void
    {
        $board = self::board($client);
        if ($board === null || empty($board['stages'][$stage])) {
            return;
        }

        $data = (array) $board['stages'][$stage];
        $in = (array) ($data['in'] ?? []);
        $out = (array) ($data['out'] ?? []);
        $stuck = (array) ($data['stuck'] ?? []);
        [$next_slug, $next_label] = self::NEXT_PAGE[$stage] ?? ['engageai-dashboard', __('Dashboard', 'engage-ai')];
        ?>
        <div class="engageai-queues" style="display:flex;gap:.75rem;flex-wrap:wrap;margin:1rem 0 1.5rem;">
            <?php
            self::card(
                __('In', 'engage-ai'),
                (int) ($in['count'] ?? 0),
                (string) ($in['label'] ?? ''),
                (array) ($in['items'] ?? []),
                '#2a78d6'
            );
            self::card(
                __('Out', 'engage-ai'),
                (int) ($out['count'] ?? 0),
                (string) ($out['label'] ?? ''),
                (array) ($out['items'] ?? []),
                '#0f7b47',
                admin_url('admin.php?page=' . $next_slug),
                /* translators: %s: the page the work moves on to */
                sprintf(__('Goes to %s', 'engage-ai'), $next_label)
            );
            if ((int) ($stuck['count'] ?? 0) > 0) {
                self::card(
                    __('Needs attention', 'engage-ai'),
                    (int) $stuck['count'],
                    (string) ($stuck['label'] ?? ''),
                    (array) ($stuck['items'] ?? []),
                    '#b32d2e'
                );
            }
            ?>
        </div>
        <?php
        self::render_reconciliation($board);
    }

    private static function card(string $heading, int $count, string $label, array $items, string $colour, string $link = '', string $link_label = ''): void
    {
        ?>
        <div style="flex:1 1 14em;min-width:14em;border:1px solid #dcdcde;border-top:3px solid <?php echo esc_attr($colour); ?>;background:#fff;padding:.75rem .9rem;">
            <div style="display:flex;align-items:baseline;justify-content:space-between;">
                <strong style="text-transform:uppercase;font-size:.7rem;letter-spacing:.04em;color:#50575e;"><?php echo esc_html($heading); ?></strong>
                <span style="font-size:1.6rem;font-weight:600;line-height:1;color:<?php echo esc_attr($colour); ?>;"><?php echo esc_html((string) $count); ?></span>
            </div>
            <?php if ($label !== ''): ?>
                <div class="description" style="margin-top:.15rem;"><?php echo esc_html($label); ?></div>
            <?php endif; ?>

            <?php if (!empty($items)): ?>
                <ul style="margin:.5rem 0 0;font-size:.8rem;">
                    <?php foreach (array_slice($items, 0, 3) as $item): ?>
                        <?php if (!is_array($item)) { continue; } ?>
                        <li style="margin:.15rem 0;list-style:disc;margin-left:1.1em;">
                            <?php echo esc_html(self::item_title($item)); ?>
                        </li>
                    <?php endforeach; ?>
                    <?php if ($count > 3): ?>
                        <li style="margin:.15rem 0;list-style:none;" class="description">
                            <?php printf(
                                /* translators: %d: how many more items there are */
                                esc_html__('and %d more', 'engage-ai'),
                                $count - 3
                            ); ?>
                        </li>
                    <?php endif; ?>
                </ul>
            <?php elseif ($count === 0): ?>
                <div class="description" style="margin-top:.4rem;"><?php esc_html_e('Nothing waiting.', 'engage-ai'); ?></div>
            <?php endif; ?>

            <?php if ($link !== '' && $count > 0): ?>
                <p style="margin:.6rem 0 0;">
                    <a href="<?php echo esc_url($link); ?>" style="font-size:.8rem;"><?php echo esc_html($link_label); ?> &rarr;</a>
                </p>
            <?php endif; ?>
        </div>
        <?php
    }

    private static function item_title(array $item): string
    {
        $title = trim((string) ($item['title'] ?? ''));
        if ($title === '') {
            $title = __('(untitled)', 'engage-ai');
        }
        $channel = (string) ($item['channel'] ?? '');
        return $channel !== '' ? $title . ' · ' . $channel : $title;
    }

    /**
     * The honesty check. The API counts every piece of content into exactly one
     * position and reports both that total and the raw row count; if they ever
     * disagree, something has been lost and this says so rather than showing a
     * plausible-looking subset. Silent in the normal case, which is always.
     */
    private static function render_reconciliation(array $board): void
    {
        $rec = (array) ($board['reconciliation'] ?? []);
        if ($rec === []) {
            return;
        }
        $total = (int) ($rec['content_total'] ?? 0);
        $accounted = (int) ($rec['accounted_for'] ?? 0);
        if ($total === $accounted) {
            return;
        }
        printf(
            '<div class="notice notice-error"><p>%s</p></div>',
            esc_html(sprintf(
                /* translators: 1: pieces accounted for, 2: pieces that exist */
                __('Engage AI is showing %1$d of %2$d pieces of content — some are in a state the queues do not recognise. Nothing has been deleted; please report this.', 'engage-ai'),
                $accounted,
                $total
            ))
        );
    }
}
