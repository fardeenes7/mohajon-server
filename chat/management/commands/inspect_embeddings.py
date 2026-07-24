from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count
from catalog.models import Product
from chat.models import FAQEntry
from core.models import VectorStatus
from mohajon.celery import app as celery_app


class Command(BaseCommand):
    help = "Inspect pending, active, scheduled, and failed embedding jobs and database vector statuses."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--shop",
            type=str,
            help="Filter DB vector status summary by shop subdomain or UUID.",
        )

    def handle(self, *args, **options) -> None:
        shop_filter = options.get("shop")

        self.stdout.write(self.style.MIGRATE_HEADING("=== 1. DATABASE VECTOR STATUS SUMMARY ==="))
        
        prod_qs = Product.objects.filter(deleted_at__isnull=True)
        faq_qs = FAQEntry.objects.filter(deleted_at__isnull=True)

        if shop_filter:
            prod_qs = prod_qs.filter(shop_id=shop_filter) | prod_qs.filter(shop__subdomain=shop_filter)
            faq_qs = faq_qs.filter(shop_id=shop_filter) | faq_qs.filter(shop__subdomain=shop_filter)

        prod_counts = prod_qs.values("vector_status").annotate(count=Count("id"))
        faq_counts = faq_qs.values("vector_status").annotate(count=Count("id"))

        self.stdout.write(self.style.SUCCESS("Products:"))
        for row in prod_counts:
            self.stdout.write(f"  - {row['vector_status']}: {row['count']}")
        if not prod_counts:
            self.stdout.write("  - None")

        self.stdout.write(self.style.SUCCESS("\nFAQ Entries:"))
        for row in faq_counts:
            self.stdout.write(f"  - {row['vector_status']}: {row['count']}")
        if not faq_counts:
            self.stdout.write("  - None")

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== 2. CELERY EMBEDDING QUEUE INSPECTION ==="))
        inspector = celery_app.control.inspect()
        
        # Active tasks
        active = inspector.active() or {}
        self.stdout.write(self.style.WARNING(f"\nActive Tasks (Currently executing across workers):"))
        active_count = 0
        for worker, tasks in active.items():
            for t in tasks:
                if "embed" in t.get("name", ""):
                    active_count += 1
                    self.stdout.write(f"  • Worker: {worker} | Task: {t['name']} | Args: {t.get('kwargs')}")
        if active_count == 0:
            self.stdout.write("  - No active embedding tasks running right now.")

        # Scheduled (Backoff / Rate limited countdown) tasks
        scheduled = inspector.scheduled() or {}
        self.stdout.write(self.style.WARNING(f"\nScheduled Tasks (Waiting on 429 Rate Limit backoff timer):"))
        sched_count = 0
        for worker, tasks in scheduled.items():
            for t in tasks:
                if "embed" in t.get("name", ""):
                    sched_count += 1
                    eta = t.get("eta")
                    self.stdout.write(f"  • Worker: {worker} | Task: {t.get('request', {}).get('name')} | ETA: {eta} | Kwargs: {t.get('request', {}).get('kwargs')}")
        if sched_count == 0:
            self.stdout.write("  - No tasks currently paused in rate-limit backoff delay.")

        # Reserved / Queued tasks
        reserved = inspector.reserved() or {}
        self.stdout.write(self.style.WARNING(f"\nReserved Tasks (Queued in Redis waiting for worker):"))
        res_count = 0
        for worker, tasks in reserved.items():
            for t in tasks:
                if "embed" in t.get("name", ""):
                    res_count += 1
                    self.stdout.write(f"  • Worker: {worker} | Task: {t['name']} | Kwargs: {t.get('kwargs')}")
        if res_count == 0:
            self.stdout.write("  - No tasks queued in Redis buffer.")
