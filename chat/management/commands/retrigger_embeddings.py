from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.models import Product
from chat.models import FAQEntry
from core.models import VectorStatus


RETRIGGER_STATUSES = [VectorStatus.SKIPPED, VectorStatus.FAILED]


class Command(BaseCommand):
    help = (
        "Re-enqueue embedding jobs for all Product and FAQEntry records whose "
        "vector_status is SKIPPED (AI Skipped – Free plan) or FAILED. "
        "Optionally filter by shop subdomain or UUID."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--shop",
            type=str,
            default=None,
            help="Limit to a specific shop (subdomain or UUID). Omit to process all shops.",
        )
        parser.add_argument(
            "--status",
            nargs="+",
            choices=["SKIPPED", "FAILED", "PENDING"],
            default=["SKIPPED", "FAILED"],
            help="Which vector_status values to re-enqueue (default: SKIPPED FAILED).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be enqueued without actually dispatching tasks.",
        )

    def handle(self, *args, **options) -> None:
        from chat.tasks.rag import embed_product_specs, embed_faq_entry

        shop_filter = options["shop"]
        target_statuses = options["status"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no tasks will be dispatched.\n"))

        # ── Products ──────────────────────────────────────────────────────────
        prod_qs = Product.objects.filter(
            vector_status__in=target_statuses,
            deleted_at__isnull=True,
        ).select_related("shop")

        if shop_filter:
            prod_qs = prod_qs.filter(shop__subdomain=shop_filter) | prod_qs.filter(shop_id=shop_filter)

        product_rows = list(prod_qs.values("id", "name", "shop__name", "vector_status"))

        # ── FAQ Entries ───────────────────────────────────────────────────────
        faq_qs = FAQEntry.objects.filter(
            vector_status__in=target_statuses,
            deleted_at__isnull=True,
        ).select_related("shop")

        if shop_filter:
            faq_qs = faq_qs.filter(shop__subdomain=shop_filter) | faq_qs.filter(shop_id=shop_filter)

        faq_rows = list(faq_qs.values("id", "question", "shop__name", "vector_status"))

        # ── Summary ───────────────────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING("=== EMBEDDING RETRIGGER ==="))
        self.stdout.write(f"Statuses targeted : {', '.join(target_statuses)}")
        self.stdout.write(f"Shop filter       : {shop_filter or 'ALL'}")
        self.stdout.write(f"Products found    : {len(product_rows)}")
        self.stdout.write(f"FAQ entries found : {len(faq_rows)}\n")

        if not product_rows and not faq_rows:
            self.stdout.write(self.style.SUCCESS("Nothing to re-enqueue. All clean."))
            return

        # ── Dispatch ──────────────────────────────────────────────────────────
        prod_count = 0
        for row in product_rows:
            self.stdout.write(
                f"  {'[DRY]' if dry_run else '[OK] '} Product  | "
                f"{str(row['id'])[:8]}… | {row['shop__name']} | "
                f"{row['name'][:40]} | status={row['vector_status']}"
            )
            if not dry_run:
                embed_product_specs.delay(product_id=str(row["id"]))
                prod_count += 1

        faq_count = 0
        for row in faq_rows:
            self.stdout.write(
                f"  {'[DRY]' if dry_run else '[OK] '} FAQEntry | "
                f"{str(row['id'])[:8]}… | {row['shop__name']} | "
                f"{str(row['question'])[:40]} | status={row['vector_status']}"
            )
            if not dry_run:
                embed_faq_entry.delay(faq_entry_id=str(row["id"]))
                faq_count += 1

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\nDry run complete. Would have enqueued "
                    f"{len(product_rows)} products and {len(faq_rows)} FAQ entries."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nEnqueued {prod_count} product(s) and {faq_count} FAQ entry(ies) "
                    f"for embedding. Watch progress:\n"
                    f"  docker compose -f docker-compose.yml logs -f celery_worker_ai"
                )
            )
